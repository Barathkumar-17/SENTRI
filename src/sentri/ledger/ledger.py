"""The bug ledger: runs, checks, bugs, the bug state machine, reuse and ordering rules.

TRUSTED ZONE: no LLM imports. Status 'intended' can only be set through a HumanDecision.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from sentri.ledger.db import connect
from sentri.verdict.engine import CheckVerdict, OracleHit


def now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True)
class HumanDecision:
    """Only the CLI `bug mark` command and the dashboard POST endpoint construct this."""

    bug_id: str
    status: Literal["intended", "open"]
    note: str
    by: str

    def __post_init__(self) -> None:
        if self.status == "intended" and not self.note.strip():
            raise ValueError("a note is required to mark a bug intended")


@dataclass
class LedgerEvent:
    kind: Literal["bug_opened", "bug_fixed", "regression", "bug_needs_review", "bug_flaky"]
    bug_id: str
    flow_id: str
    attack_id: str
    commit_sha: str
    detail: str = ""


@dataclass
class CheckRecord:
    run_id: int
    flow_id: str
    attack_id: str
    verdict: str
    oracle_hits: str = ""
    attempts: int = 0
    payload: dict[str, Any] = field(default_factory=dict)
    payload_source: str = "none"
    reason: str | None = None
    error_msg: str | None = None
    screenshot_path: str | None = None
    evidence_path: str | None = None
    duplicate_method: str | None = None
    is_probe: bool = False
    reused_from_check_id: int | None = None
    duration_s: float | None = None
    origin: str | None = None  # baseline | probe | ai | ai_suspicion | code_probe | code_fallback (default by kind)


class Ledger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn: sqlite3.Connection = connect(path)

    def close(self) -> None:
        self.conn.close()

    # ---- runs ---------------------------------------------------------------------------------------
    def create_run(self, commit_sha: str, branch: str, app_fingerprint: str, contract_hash: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (commit_sha, branch, app_fingerprint, contract_hash, started_at, status)"
            " VALUES (?, ?, ?, ?, ?, 'running')",
            (commit_sha, branch, app_fingerprint, contract_hash, now()),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        status: str,
        readiness: int | None,
        duration_s: float,
        llm_cost_usd: float | None,  # None = unknown (a model without a known price was used)
        llm_mode: str,
        breakdown: dict[str, Any] | None,
        usage: dict[str, Any] | None = None,
        coverage: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, status=?, readiness=?, duration_s=?, llm_cost_usd=?, llm_mode=?,"
            " breakdown=?, usage=?, coverage=? WHERE id=?",
            (
                now(),
                status,
                readiness,
                duration_s,
                llm_cost_usd,
                llm_mode,
                json.dumps(breakdown),
                json.dumps(usage, default=str) if usage is not None else None,
                json.dumps(coverage, default=str) if coverage is not None else None,
                run_id,
            ),
        )
        self.conn.commit()

    def run(self, run_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return row

    def latest_run(self, branch: str | None = None, finished_only: bool = True) -> sqlite3.Row | None:
        sql = "SELECT * FROM runs WHERE 1=1"
        args: list[Any] = []
        if finished_only:
            sql += " AND status='finished'"
        if branch:
            sql += " AND branch=?"
            args.append(branch)
        row: sqlite3.Row | None = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return row

    def runs(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def trend(self, branch: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
        sql = "SELECT id AS run_id, commit_sha, readiness, started_at FROM runs WHERE status='finished'"
        args: list[Any] = []
        if branch:
            sql += " AND branch=?"
            args.append(branch)
        rows = self.conn.execute(sql + " ORDER BY id DESC LIMIT ?", [*args, limit]).fetchall()
        return list(reversed(rows))

    # ---- checks -------------------------------------------------------------------------------------
    def insert_check(self, rec: CheckRecord) -> int:
        cur = self.conn.execute(
            "INSERT INTO checks (run_id, flow_id, attack_id, verdict, score_verdict, oracle_hits, attempts,"
            " payload, payload_source, reason, error_msg, screenshot_path, evidence_path, duplicate_method,"
            " is_probe, reused_from_check_id, duration_s, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec.run_id,
                rec.flow_id,
                rec.attack_id,
                rec.verdict,
                rec.verdict,
                rec.oracle_hits,
                rec.attempts,
                json.dumps(rec.payload),
                rec.payload_source,
                rec.reason,
                rec.error_msg,
                rec.screenshot_path,
                rec.evidence_path,
                rec.duplicate_method,
                int(rec.is_probe),
                rec.reused_from_check_id,
                rec.duration_s,
                rec.origin or ("probe" if rec.is_probe else "baseline"),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def set_score_verdict(self, check_id: int, score_verdict: str) -> None:
        self.conn.execute("UPDATE checks SET score_verdict=? WHERE id=?", (score_verdict, check_id))
        self.conn.commit()

    def check(self, check_id: int) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute("SELECT * FROM checks WHERE id=?", (check_id,)).fetchone()
        return row

    def checks_for_run(self, run_id: int, include_probes: bool = True) -> list[sqlite3.Row]:
        sql = "SELECT * FROM checks WHERE run_id=?"
        if not include_probes:
            sql += " AND is_probe=0"
        return self.conn.execute(sql + " ORDER BY id", (run_id,)).fetchall()

    def cell_history(self, flow_id: str, attack_id: str, limit: int = 5) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT c.id, c.payload, c.verdict, c.oracle_hits, c.payload_source,"
            " (SELECT GROUP_CONCAT(bug_id) FROM bug_hits h WHERE h.check_id=c.id) AS bug_ids"
            " FROM checks c WHERE flow_id=? AND attack_id=? AND reused_from_check_id IS NULL"
            " AND verdict NOT IN ('na','error') ORDER BY id DESC LIMIT ?",
            (flow_id, attack_id, limit),
        ).fetchall()
        return [
            {
                "payload": json.loads(r["payload"] or "{}"),
                "verdict": r["verdict"],
                "oracle_hits": r["oracle_hits"],
                "bug_ids": (r["bug_ids"] or "").split(",") if r["bug_ids"] else [],
            }
            for r in rows
        ]

    def cell_run_count(self, flow_id: str, attack_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM checks WHERE flow_id=? AND attack_id=? AND reused_from_check_id IS NULL"
            " AND is_probe=0 AND verdict NOT IN ('na','error')",
            (flow_id, attack_id),
        ).fetchone()
        return int(row[0])

    # ---- reuse and ordering -------------------------------------------------------------------------
    def find_reusable(
        self, flow_id: str, attack_id: str, app_fingerprint: str, contract_hash: str
    ) -> sqlite3.Row | None:
        last = self.conn.execute(
            "SELECT c.*, r.app_fingerprint, r.contract_hash FROM checks c JOIN runs r ON r.id=c.run_id"
            " WHERE c.flow_id=? AND c.attack_id=? AND c.is_probe=0 AND c.verdict NOT IN ('na','error')"
            " AND r.status='finished' ORDER BY c.id DESC LIMIT 1",
            (flow_id, attack_id),
        ).fetchone()
        if last is None or last["verdict"] != "pass":
            return None
        if last["app_fingerprint"] != app_fingerprint or last["contract_hash"] != contract_hash:
            return None
        if self.cell_has_problem_bugs(flow_id, attack_id):
            return None
        row: sqlite3.Row = last
        return row

    def cell_has_problem_bugs(self, flow_id: str, attack_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM bugs WHERE flow_id=? AND attack_id=? AND (status IN ('open','flaky') OR needs_review=1)"
            " LIMIT 1",
            (flow_id, attack_id),
        ).fetchone()
        return row is not None

    def recently_regressed(self, flow_id: str, attack_id: str, runs: int = 5) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM bug_history h JOIN bugs b ON b.id=h.bug_id WHERE b.flow_id=? AND b.attack_id=?"
            " AND h.from_status='fixed' AND h.to_status IN ('open','flaky')"
            " AND h.run_id IN (SELECT id FROM runs ORDER BY id DESC LIMIT ?) LIMIT 1",
            (flow_id, attack_id, runs),
        ).fetchone()
        return row is not None

    # ---- bugs ---------------------------------------------------------------------------------------
    def bugs(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.conn.execute("SELECT * FROM bugs WHERE status=? ORDER BY id", (status,)).fetchall()
        return self.conn.execute("SELECT * FROM bugs ORDER BY id").fetchall()

    def bug(self, bug_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute("SELECT * FROM bugs WHERE id=?", (bug_id,)).fetchone()
        return row

    def bug_history(self, bug_id: str) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM bug_history WHERE bug_id=? ORDER BY id", (bug_id,)).fetchall()

    def bugs_for_check(self, check_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT b.*, h.k FROM bug_hits h JOIN bugs b ON b.id=h.bug_id WHERE h.check_id=? ORDER BY b.id",
            (check_id,),
        ).fetchall()

    def _next_bug_id(self) -> str:
        row = self.conn.execute("SELECT id FROM bugs ORDER BY CAST(SUBSTR(id, 5) AS INTEGER) DESC LIMIT 1").fetchone()
        n = int(row[0][4:]) + 1 if row else 1
        return f"BUG-{n:03d}"

    def _history(
        self, bug_id: str, from_s: str | None, to_s: str, commit: str, run_id: int | None, actor: str, note: str = ""
    ) -> None:
        self.conn.execute(
            "INSERT INTO bug_history (bug_id, ts, from_status, to_status, commit_sha, run_id, actor, note)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (bug_id, now(), from_s, to_s, commit, run_id, actor, note),
        )

    def record_check_result(
        self, run_id: int, check_id: int, flow_id: str, attack_id: str, commit: str, result: CheckVerdict
    ) -> tuple[list[LedgerEvent], str]:
        """Apply the bug state machine for one check that actually ran. Returns (events, score_verdict)."""
        events: list[LedgerEvent] = []
        attempts = result.attempts
        hit_sigs = {h.signature for h in result.hits}
        all_intended = bool(result.hits)

        for hit in result.hits:
            new_status = "open" if hit.k >= attempts else "flaky"
            bug = self.conn.execute("SELECT * FROM bugs WHERE signature=?", (hit.signature,)).fetchone()
            if bug is None:
                bug_id = self._create_bug(run_id, flow_id, attack_id, commit, hit, new_status, events)
                all_intended = False
            else:
                bug_id = bug["id"]
                old = bug["status"]
                if old == "intended":
                    self.conn.execute(
                        "UPDATE bugs SET last_seen_commit=?, sample_detail=? WHERE id=?",
                        (commit, hit.detail, bug_id),
                    )
                else:
                    all_intended = False
                    regressed = old == "fixed"
                    self.conn.execute(
                        "UPDATE bugs SET status=?, last_seen_commit=?, sample_detail=?,"
                        " regressions=regressions+?, fixed_commit=CASE WHEN ? THEN NULL ELSE fixed_commit END"
                        " WHERE id=?",
                        (new_status, commit, hit.detail, int(regressed), int(regressed), bug_id),
                    )
                    if old != new_status:
                        self._history(bug_id, old, new_status, commit, run_id, "system")
                    if regressed:
                        events.append(LedgerEvent("regression", bug_id, flow_id, attack_id, commit, hit.detail))
            self.conn.execute(
                "INSERT OR REPLACE INTO bug_hits (bug_id, check_id, k) VALUES (?,?,?)", (bug_id, check_id, hit.k)
            )

        # Bugs in this cell that did not reproduce are fixed (intended ones stay as they are).
        for bug in self.conn.execute(
            "SELECT * FROM bugs WHERE flow_id=? AND attack_id=? AND status IN ('open','flaky')", (flow_id, attack_id)
        ).fetchall():
            if bug["signature"] not in hit_sigs:
                self.conn.execute("UPDATE bugs SET status='fixed', fixed_commit=? WHERE id=?", (commit, bug["id"]))
                self._history(bug["id"], bug["status"], "fixed", commit, run_id, "system")
                events.append(LedgerEvent("bug_fixed", bug["id"], flow_id, attack_id, commit))
        self.conn.commit()

        score_verdict = "pass" if (result.verdict in ("fail", "flaky") and all_intended) else result.verdict
        if score_verdict != result.verdict:
            self.set_score_verdict(check_id, score_verdict)
        return events, score_verdict

    def _create_bug(
        self,
        run_id: int,
        flow_id: str,
        attack_id: str,
        commit: str,
        hit: OracleHit,
        status: str,
        events: list[LedgerEvent],
    ) -> str:
        related = self.conn.execute(
            "SELECT id FROM bugs WHERE flow_id=? AND attack_id=? AND oracle_id=? AND status='intended'"
            " ORDER BY id DESC LIMIT 1",
            (flow_id, attack_id, hit.oracle_id),
        ).fetchone()
        bug_id = self._next_bug_id()
        self.conn.execute(
            "INSERT INTO bugs (id, flow_id, attack_id, oracle_id, signature, status, first_commit, last_seen_commit,"
            " related_bug_id, needs_review, sample_detail) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                bug_id,
                flow_id,
                attack_id,
                hit.oracle_id,
                hit.signature,
                status,
                commit,
                commit,
                related["id"] if related else None,
                1 if related else 0,
                hit.detail,
            ),
        )
        self._history(bug_id, None, status, commit, run_id, "system")
        events.append(
            LedgerEvent(
                "bug_opened" if status == "open" else "bug_flaky", bug_id, flow_id, attack_id, commit, hit.detail
            )
        )
        if related:
            events.append(
                LedgerEvent(
                    "bug_needs_review",
                    bug_id,
                    flow_id,
                    attack_id,
                    commit,
                    f"behaviour changed from intended {related['id']}",
                )
            )
        return bug_id

    def mark(self, decision: HumanDecision, commit: str = "") -> None:
        if not isinstance(decision, HumanDecision):
            raise TypeError("bug status changes by humans require a HumanDecision")
        bug = self.bug(decision.bug_id)
        if bug is None:
            raise KeyError(decision.bug_id)
        note = f"{decision.note} (by {decision.by})" if decision.by else decision.note
        self.conn.execute(
            "UPDATE bugs SET status=?, human_note=?, needs_review=0 WHERE id=?",
            (decision.status, note, decision.bug_id),
        )
        self._history(decision.bug_id, bug["status"], decision.status, commit, None, "human", note)
        self.conn.commit()

    def regressions_in_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT DISTINCT b.* FROM bug_history h JOIN bugs b ON b.id=h.bug_id WHERE h.run_id=?"
            " AND h.from_status='fixed' AND h.to_status IN ('open','flaky')",
            (run_id,),
        ).fetchall()

    def bugs_opened_in_run(self, run_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT DISTINCT b.* FROM bug_history h JOIN bugs b ON b.id=h.bug_id WHERE h.run_id=?"
            " AND h.from_status IS NULL",
            (run_id,),
        ).fetchall()

    # ---- agentic mode -------------------------------------------------------------------------------
    def save_app_map(self, run_id: int, data: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO app_maps (run_id, created_at, data) VALUES (?,?,?)", (run_id, now(), json.dumps(data))
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def latest_app_map(self, run_id: int | None = None) -> dict[str, Any] | None:
        sql, args = "SELECT data FROM app_maps", []
        if run_id is not None:
            sql, args = sql + " WHERE run_id=?", [run_id]
        row = self.conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
        return json.loads(row["data"]) if row else None

    def save_strategy(self, run_id: int, source: str, data: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO strategies (run_id, created_at, source, data) VALUES (?,?,?,?)",
            (run_id, now(), source, json.dumps(data)),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def strategy(self, run_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT source, data FROM strategies WHERE run_id=? ORDER BY id DESC LIMIT 1", (run_id,)
        ).fetchone()
        return {"source": row["source"], **json.loads(row["data"])} if row else None

    def add_step(self, run_id: int, step: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO exploration_steps (run_id, step, parent_step, hypothesis_id, identity, thought, action,"
            " action_text, ok, error, url_after, signals, ts, origin) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                step["step"],
                step.get("parent_step"),
                step.get("hypothesis_id"),
                step.get("identity"),
                step.get("thought"),
                json.dumps(step.get("action")),
                step.get("action_text"),
                int(bool(step.get("ok"))),
                step.get("error"),
                step.get("url_after"),
                json.dumps(step.get("signals", [])),
                now(),
                step.get("origin"),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def steps_for_run(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM exploration_steps WHERE run_id=? ORDER BY step", (run_id,))
        out = []
        for r in rows.fetchall():
            d = dict(r)
            d["action"] = json.loads(d["action"] or "null")
            d["signals"] = json.loads(d["signals"] or "[]")
            out.append(d)
        return out

    def add_finding(self, run_id: int, finding: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO findings (run_id, hypothesis_id, title, oracle_ids, repro, from_step, to_step, verdict,"
            " attempts, check_id, created_at, origin, replay_state, evidence, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                finding.get("hypothesis_id"),
                finding.get("title"),
                ",".join(finding.get("oracle_ids", [])),
                json.dumps(finding.get("repro", [])),
                finding.get("from_step"),
                finding.get("to_step"),
                finding["verdict"],
                finding.get("attempts", 0),
                finding.get("check_id"),
                now(),
                finding.get("origin"),
                finding.get("replay_state"),
                json.dumps(finding.get("evidence") or []),
                json.dumps(finding.get("notes") or []),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    @staticmethod
    def _finding(r: sqlite3.Row) -> dict[str, Any]:
        d = dict(r)
        for key in ("repro", "evidence", "notes"):
            d[key] = json.loads(d.get(key) or "[]")
        return d

    def findings_for_run(self, run_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM findings WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [self._finding(r) for r in rows]

    def findings(self, verdict: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """Findings across runs, newest first, with their run's commit and branch and their check key."""
        sql = (
            "SELECT f.*, r.commit_sha, r.branch, r.started_at AS run_started_at, c.attack_id AS key"
            " FROM findings f JOIN runs r ON r.id=f.run_id LEFT JOIN checks c ON c.id=f.check_id"
        )
        args: list[Any] = []
        if verdict:
            sql += " WHERE f.verdict=?"
            args.append(verdict)
        rows = self.conn.execute(sql + " ORDER BY f.id DESC LIMIT ?", [*args, limit]).fetchall()
        return [self._finding(r) for r in rows]

    def finding(self, finding_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT f.*, r.commit_sha, r.branch, c.attack_id AS key FROM findings f JOIN runs r ON r.id=f.run_id"
            " LEFT JOIN checks c ON c.id=f.check_id WHERE f.id=?",
            (finding_id,),
        ).fetchone()
        return self._finding(row) if row else None

    def confirmed_repros(self) -> list[dict[str, Any]]:
        """Latest confirmed repro per agentic cell, for regression re-checks in later runs."""
        rows = self.conn.execute(
            "SELECT f.*, c.flow_id, c.attack_id FROM findings f JOIN checks c ON c.id=f.check_id"
            " WHERE f.verdict IN ('fail','flaky') ORDER BY f.id DESC"
        ).fetchall()
        seen: set[str] = set()
        out = []
        for r in rows:
            if r["attack_id"] in seen:
                continue
            seen.add(r["attack_id"])
            out.append(self._finding(r))
        return out

    # ---- events -------------------------------------------------------------------------------------
    def add_event(self, run_id: int | None, kind: str, message: str, reason: str | None, data: dict[str, Any]) -> int:
        cur = self.conn.execute(
            "INSERT INTO events (run_id, ts, kind, message, reason, data) VALUES (?,?,?,?,?,?)",
            (run_id, now(), kind, message, reason, json.dumps(data, default=str)),
        )
        self.conn.commit()
        return int(cur.lastrowid or 0)

    def events_after(self, after_id: int, run_id: int | None = None, limit: int = 500) -> list[sqlite3.Row]:
        sql = "SELECT * FROM events WHERE id>?"
        args: list[Any] = [after_id]
        if run_id is not None:
            sql += " AND run_id=?"
            args.append(run_id)
        return self.conn.execute(sql + " ORDER BY id LIMIT ?", [*args, limit]).fetchall()
