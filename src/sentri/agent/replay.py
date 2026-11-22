"""Confirm candidates by replaying their exact steps 3x from a clean state. Plain code decides.

Each attempt resets the app, starts fresh identities, measures health and list counts before, replays the
steps (re-matching referenced requests by method and path, since ids differ in a fresh session), measures
again, and runs all eleven oracles. The verdict engine turns 3 attempts into fail / flaky / pass. Confirmed
findings enter the bug ledger like any other check, so regressions and fixes are tracked across runs.
"""

from __future__ import annotations

import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError

from sentri.agent.actions import ACTION_ADAPTER, ActionRefused, Burst, Replay, SendRequest
from sentri.agent.identities import make_login
from sentri.agent.recon import AppMap
from sentri.agent.session import AgentSession
from sentri.agent.tokens import tokens_in
from sentri.contract.models import Contract
from sentri.evidence.agent_models import AgentEvidence, CountProbe, HealthProbe
from sentri.evidence.records import count_records
from sentri.ledger.ledger import CheckRecord, Ledger, LedgerEvent
from sentri.redact import redact_obj
from sentri.runner.browser import BrowserManager
from sentri.sandbox.base import Sandbox
from sentri.verdict.agent_oracles import AGENT_ORACLE_IDS, evaluate_agent
from sentri.verdict.engine import CheckVerdict, decide_replays
from sentri.verdict.oracles import OracleConfig, OracleResult
from sentri.verdict.signature import normalize_path

_NONCE = re.compile(r"\b[0-9a-f]{16,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
PRIMARY_ORDER = (*AGENT_ORACLE_IDS, "http_500", "exception", "duplicates", "timeout", "console_error")


def agentic_oracle_config(contract: Contract) -> OracleConfig:
    """The same contract settings the baseline suite applies (ignored console noise, extra log patterns)."""
    return OracleConfig(
        flow_id="agentic",
        attack_id="explore",
        ignore_console=tuple(contract.app.ignore_console),
        extra_log_patterns=tuple(contract.app.log_patterns_extra),
        app_origin=contract.app.url,
    )


@dataclass
class ReplayEnv:
    contract: Contract
    sandbox: Sandbox
    browser: BrowserManager
    app_map: AppMap
    run_tag: str
    attempts: int = 3
    evidence_dir: Path | None = None

    @property
    def protected(self) -> set[str]:
        return self.app_map.protected_paths()

    def list_selectors(self, path: str) -> list[str]:
        page = self.app_map.page(path)
        return list(page.list_selectors) if page is not None else []

    def is_list_page(self, path: str) -> bool:
        """A page or parameter-free GET endpoint that lists records (where duplicates would show)."""
        page = self.app_map.page(path)
        if page is not None and page.list_selectors:
            return True
        return any(e.method == "GET" and e.path == path and ":id" not in e.path for e in self.app_map.endpoints)

    def health_pages(self, repro: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """A few pages that should keep working: list pages and the pages the steps touched."""
        paths: list[str] = []
        for p in self.app_map.pages:
            if p.list_selectors and p.path not in paths:
                paths.append(p.path)
        for e in self.app_map.endpoints:  # API apps: parameter-free GET endpoints are the "list pages"
            if e.method == "GET" and ":id" not in e.path and not e.query_params and e.path not in paths:
                paths.append(e.path)
        for step in repro:
            path = step["action"].get("path")
            if isinstance(path, str) and path.startswith("/") and "?" not in path and path not in paths:
                paths.append(path)
        login = self.app_map.has_login_flow
        out = []
        protected = self.app_map.protected_paths()
        for path in paths[:6]:
            out.append(("a" if (normalize_path(path) in protected and login) else "anon", path))
        return out


async def _fetch(session: AgentSession, who: str, path: str) -> tuple[int | None, str, str | None]:
    """Unrecorded GET as `who` (measurement, not part of the evidence): status, body, content type."""
    try:
        ctx = await session._context(who)
        headers = session.identity_headers.get(who, {})
        resp = await ctx.request.get(
            session.base_url + path, headers=headers, max_redirects=0, timeout=session.timeout_ms
        )
        return resp.status, await resp.text(), resp.headers.get("content-type")
    except (PlaywrightError, ActionRefused):
        return None, "", None


async def _fetch_text(session: AgentSession, who: str, path: str) -> tuple[int | None, str]:
    status, text, _ctype = await _fetch(session, who, path)
    return status, text


_CANARY = re.compile(r"sentri-canary-(a|b)-[0-9a-f]{6}")
_XSS = re.compile(r"sentri-xss-[0-9a-f]{6}")


def _with_current_canaries(session: AgentSession, raw: dict[str, Any]) -> dict[str, Any]:
    """Canaries are random per session; swap recorded ones for this attempt's, keeping the identity."""
    text = json.dumps(raw)
    text = _CANARY.sub(lambda m: session.canaries[m.group(1)], text)
    text = _XSS.sub(session.xss_marker[3:-1], text)  # the marker's id part, keeping whatever quoting surrounds it
    data: dict[str, Any] = json.loads(text)
    return data


def _resolve(session: AgentSession, step: dict[str, Any]) -> Any:
    action = ACTION_ADAPTER.validate_python(_with_current_canaries(session, step["action"]))
    match = step.get("match")
    if match and isinstance(action, (Replay, SendRequest)):
        found = next(
            (
                r
                for r in reversed(session.requests)
                if r.method == match["method"] and normalize_path(r.url) == match["path"]
            ),
            None,
        )
        if found is None:
            raise ActionRefused(f"no request {match['method']} {match['path']} to replay in this attempt")
        update = {"request_id": found.id} if isinstance(action, Replay) else {"from_request": found.id}
        action = action.model_copy(update=update)
    return action


@dataclass
class Attempt:
    evidence: AgentEvidence
    notes: list[str]
    completed: bool  # every step ran as it did during exploration; otherwise the attempt proves nothing


async def replay_once(env: ReplayEnv, repro: list[dict[str, Any]], attempt: int) -> Attempt:
    """Replay the whole session prefix from a clean state. A step that ran cleanly during exploration but cannot
    run now (selector gone, request not observed, refused) fails the attempt: later steps would run against a
    different state, so skipping it and carrying on could neither confirm nor clear anything."""
    await env.sandbox.reset()
    tmp = Path(tempfile.mkdtemp(prefix="sentri-replay-"))
    tag = f"{env.run_tag}-rp{attempt}"
    session = AgentSession(env.browser, env.sandbox, env.contract.app.url, make_login(env.contract, tmp, tag), tmp)
    session.protected = env.protected
    session.proven_protected = env.app_map.proven_protected_paths()
    session.anon_shells = tuple(env.app_map.anon_shells)
    session.login_path = env.app_map.login_path
    notes: list[str] = []
    try:
        await session.start()
        health = env.health_pages(repro)
        before: dict[tuple[str, str], tuple[int | None, str]] = {}
        mark = session.mark()
        judging = False
        counts: list[CountProbe] = []
        completed = True
        for index, step in enumerate(repro, start=1):
            if not judging and not step.get("setup"):
                # Setup (earlier lines of attack) only rebuilds the state; earlier breaks belong to their own
                # findings. Evidence and the health baseline start with the candidate's own steps.
                judging = True
                before = {key: await _fetch_text(session, *key) for key in health}
                mark = session.mark()
            try:
                action = _resolve(session, step)
            except ActionRefused as exc:
                notes.append(f"attempt {attempt} failed at step {index}/{len(repro)}: {exc}")
                completed = False
                break
            probe: tuple[str, list[str], int] | None = None
            list_pages = [path for _who, path in health if env.is_list_page(path)]
            if isinstance(action, (Replay, Burst)):
                src_body = None
                if isinstance(action, Replay):
                    src = next((r for r in session.requests if r.id == action.request_id), None)
                    src_body = src.body if src else None
                else:
                    writes = [r for r in session.requests if r.method in ("POST", "PUT", "PATCH")]
                    src_body = writes[-1].body if writes else None
                tokens = tokens_in(src_body)
                one_time = bool(_NONCE.search(src_body or ""))
                expected = 1 if isinstance(action, Burst) else (0 if one_time else -1)
                what = "a burst" if isinstance(action, Burst) else "a replay"
                if expected < 0:
                    notes.append(
                        f"replay_duplicate not applicable at step {index}: the replayed request has no one-time token"
                    )
                elif not tokens:
                    notes.append(
                        f"replay_duplicate inconclusive at step {index}: {what} carries no Sentri value to count"
                    )
                elif not list_pages:
                    notes.append(f"replay_duplicate inconclusive at step {index}: no list page to count records on")
                    counts += [CountProbe(t, "", 0, 0, expected, inconclusive=True) for t in tokens]
                else:
                    probe = (session.current, tokens, expected)
            pages_before: dict[str, tuple[str, str | None]] = {}
            if probe is not None:
                for path in list_pages:
                    _st, text, ctype = await _fetch(session, probe[0], path)
                    pages_before[path] = (text, ctype)
            session.side_probes = probe is None  # a CSRF re-send inside a counted step would add a record
            outcome = await session.execute(action)
            session.side_probes = True
            if not outcome.ok and step.get("expect_ok", True):
                notes.append(f"attempt {attempt} failed at step {index}/{len(repro)}: {outcome.error}")
                completed = False
                break
            if probe is not None:
                counts += await _count_probes(env, session, probe, pages_before, index, notes)
        if not judging:  # every step was setup (should not happen): judge from the end, i.e. nothing
            before = {key: await _fetch_text(session, *key) for key in health}
            mark = session.mark()
        after = {key: await _fetch_text(session, *key) for key in health}
        probes = tuple(HealthProbe(path, who, before[(who, path)][0], after[(who, path)][0]) for (who, path) in health)
        return Attempt(session.agent_evidence_since(mark, tuple(counts), probes), notes, completed)
    finally:
        await session.close()


async def _count_probes(
    env: ReplayEnv,
    session: AgentSession,
    probe: tuple[str, list[str], int],
    pages_before: dict[str, tuple[str, str | None]],
    index: int,
    notes: list[str],
) -> list[CountProbe]:
    """Distinct records holding each Sentri value on each list page, before and after the replayed step."""
    who, tokens, expected = probe
    out: list[CountProbe] = []
    for path, (text_before, ctype_before) in pages_before.items():
        _st, text_after, ctype_after = await _fetch(session, who, path)
        selectors = env.list_selectors(path)
        for token in tokens:
            before = count_records(text_before, token, ctype_before, selectors)
            after = count_records(text_after, token, ctype_after, selectors)
            unknown = before is None or after is None
            out.append(CountProbe(token, path, before or 0, after or 0, expected, inconclusive=unknown))
    if not any(not p.inconclusive and p.after > 0 for p in out):
        notes.append(f"replay_duplicate inconclusive at step {index}: no list page shows {', '.join(tokens)[:120]}")
        out = [CountProbe(p.token, p.page, p.before, p.after, p.expected_increase, inconclusive=True) for p in out]
    return out


def primary_key(results: list[list[OracleResult]]) -> str:
    fired = [r for attempt in results for r in attempt if r.fired]
    for oracle_id in PRIMARY_ORDER:
        hit = next((r for r in fired if r.oracle_id == oracle_id), None)
        if hit is not None:
            where = hit.route or (hit.error_key or "")[:60]
            return f"{oracle_id} {where}".strip()
    return "explore"


async def confirm(
    env: ReplayEnv, repro: list[dict[str, Any]], key: str | None = None
) -> tuple[CheckVerdict, str, list[str]]:
    cfg = agentic_oracle_config(env.contract)
    results: list[list[OracleResult] | None] = []
    notes: list[str] = []
    for attempt in range(1, env.attempts + 1):
        run = await replay_once(env, repro, attempt)
        ev = run.evidence
        notes += run.notes
        results.append(evaluate_agent(ev, cfg) if run.completed else None)
        if env.evidence_dir is not None:
            path = (
                env.evidence_dir
                / f"agentic-{env.run_tag}-{abs(hash(json.dumps(repro, default=str))) % 10**8}-a{attempt}.json"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "responses": [r.__dict__ for r in ev.responses],
                "probes": [p.__dict__ for p in ev.count_probes],
                "health": [h.__dict__ for h in ev.health_probes],
                "completed": run.completed,
                "notes": run.notes,
                "oracles": [r.__dict__ for r in results[-1] or []],
                "logs": [line.text for line in ev.base.logs][-80:],
            }
            path.write_text(json.dumps(redact_obj(data), indent=1, default=str), encoding="utf-8")
    final_key = key or primary_key([r for r in results if r is not None])
    return decide_replays("agentic", final_key, results), final_key, notes


def record(
    ledger: Ledger,
    run_id: int,
    commit: str,
    candidate: dict[str, Any],
    verdict: CheckVerdict,
    key: str,
) -> tuple[int | None, list[LedgerEvent]]:
    """Store the outcome. Confirmed findings become checks and bugs (outside the comparable base score)."""
    events: list[LedgerEvent] = []
    check_id = None
    status: str = verdict.verdict if verdict.verdict in ("fail", "flaky") else "unconfirmed"
    if status in ("fail", "flaky"):
        check_id = ledger.insert_check(
            CheckRecord(
                run_id,
                "agentic",
                key,
                verdict.verdict,
                oracle_hits=verdict.oracle_hits,
                attempts=verdict.attempts,
                payload={"repro": candidate["repro"]},
                payload_source="llm",
                reason=candidate.get("title"),
                is_probe=True,
            )
        )
        events, _ = ledger.record_check_result(run_id, check_id, "agentic", key, commit, verdict)
    elif candidate.get("recheck") and verdict.verdict == "pass":
        # A stored finding that no longer reproduces: record a passing check so its bugs become fixed. An
        # inconclusive re-check (steps could not be replayed) leaves its bugs exactly as they were.
        check_id = ledger.insert_check(
            CheckRecord(
                run_id,
                "agentic",
                key,
                "pass",
                attempts=verdict.attempts,
                payload={"repro": candidate["repro"]},
                payload_source="none",
                reason=candidate.get("title"),
                is_probe=True,
            )
        )
        events, _ = ledger.record_check_result(run_id, check_id, "agentic", key, commit, verdict)
        status = "pass"
    ledger.add_finding(
        run_id,
        {
            "hypothesis_id": candidate.get("hypothesis_id"),
            "title": candidate.get("title"),
            "oracle_ids": sorted({h.oracle_id for h in verdict.hits}),
            "repro": candidate["repro"],
            "from_step": candidate.get("from_step"),
            "to_step": candidate.get("to_step"),
            "verdict": status,
            "attempts": verdict.attempts,
            "check_id": check_id,
        },
    )
    return check_id, events
