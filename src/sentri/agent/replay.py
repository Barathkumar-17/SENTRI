"""Confirm candidates by replaying their exact steps 3x from a clean state. Plain code decides.

Each attempt resets the app, starts fresh identities, measures health and list counts before, replays the
steps (re-matching referenced requests by method and path, since ids differ in a fresh session), measures
again, and runs all nine oracles. The verdict engine turns 3 attempts into fail / flaky / pass. Confirmed
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
from sentri.agent.explorer import tokens_in
from sentri.agent.identities import make_login
from sentri.agent.recon import AppMap
from sentri.agent.session import AgentSession
from sentri.contract.models import Contract
from sentri.evidence.agent_models import AgentEvidence, CountProbe, HealthProbe
from sentri.ledger.ledger import CheckRecord, Ledger, LedgerEvent
from sentri.redact import redact_obj
from sentri.runner.browser import BrowserManager
from sentri.sandbox.base import Sandbox
from sentri.verdict.agent_oracles import AGENT_ORACLE_IDS, evaluate_agent
from sentri.verdict.engine import CheckVerdict, decide
from sentri.verdict.oracles import OracleConfig, OracleResult
from sentri.verdict.signature import normalize_path

_NONCE = re.compile(r"\b[0-9a-f]{16,}\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
PRIMARY_ORDER = (*AGENT_ORACLE_IDS, "http_500", "exception", "duplicates", "timeout", "console_error")


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
        return {normalize_path(p.path) for p in self.app_map.pages if p.requires_login}

    def health_pages(self, repro: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """A few pages that should keep working: list pages and the pages the steps touched."""
        paths: list[str] = []
        for p in self.app_map.pages:
            if p.list_selectors and p.path not in paths:
                paths.append(p.path)
        for step in repro:
            path = step["action"].get("path")
            if isinstance(path, str) and path.startswith("/") and "?" not in path and path not in paths:
                paths.append(path)
        login = self.app_map.has_login_flow
        out = []
        for path in paths[:5]:
            entry = self.app_map.page(path)
            out.append(("a" if (entry is not None and entry.requires_login and login) else "anon", path))
        return out


async def _fetch_text(session: AgentSession, who: str, path: str) -> tuple[int | None, str]:
    """Unrecorded GET as `who` (measurement, not part of the evidence)."""
    try:
        ctx = await session._context(who)
        resp = await ctx.request.get(session.base_url + path, max_redirects=0, timeout=session.timeout_ms)
        return resp.status, await resp.text()
    except (PlaywrightError, ActionRefused):
        return None, ""


_CANARY = re.compile(r"sentri-canary-(a|b)-[0-9a-f]{6}")


def _with_current_canaries(session: AgentSession, raw: dict[str, Any]) -> dict[str, Any]:
    """Canaries are random per session; swap recorded ones for this attempt's, keeping the identity."""
    text = json.dumps(raw)
    text = _CANARY.sub(lambda m: session.canaries[m.group(1)], text)
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


async def replay_once(env: ReplayEnv, repro: list[dict[str, Any]], attempt: int) -> tuple[AgentEvidence, list[str]]:
    await env.sandbox.reset()
    tmp = Path(tempfile.mkdtemp(prefix="sentri-replay-"))
    tag = f"{env.run_tag}-rp{attempt}"
    session = AgentSession(env.browser, env.sandbox, env.contract.app.url, make_login(env.contract, tmp, tag), tmp)
    session.protected = env.protected
    notes: list[str] = []
    try:
        await session.start()
        health = env.health_pages(repro)
        before = {key: await _fetch_text(session, *key) for key in health}
        mark = session.mark()
        counts: list[CountProbe] = []
        for step in repro:
            try:
                action = _resolve(session, step)
            except ActionRefused as exc:
                notes.append(str(exc))
                continue
            probe: tuple[str, list[str], int] | None = None
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
                if tokens and expected >= 0:
                    probe = (session.current, tokens, expected)
            pages_before: dict[str, str] = {}
            if probe is not None:
                for _who, path in health:
                    pages_before[path] = (await _fetch_text(session, probe[0], path))[1]
            await session.execute(action)
            if probe is not None:
                who, tokens, expected = probe
                for path, text_before in pages_before.items():
                    text_after = (await _fetch_text(session, who, path))[1]
                    for token in tokens:
                        counts.append(
                            CountProbe(token, path, text_before.count(token), text_after.count(token), expected)
                        )
        after = {key: await _fetch_text(session, *key) for key in health}
        probes = tuple(HealthProbe(path, who, before[(who, path)][0], after[(who, path)][0]) for (who, path) in health)
        return session.agent_evidence_since(mark, tuple(counts), probes), notes
    finally:
        await session.close()


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
    cfg = OracleConfig(flow_id="agentic", attack_id="explore")
    results: list[list[OracleResult]] = []
    notes: list[str] = []
    for attempt in range(1, env.attempts + 1):
        ev, attempt_notes = await replay_once(env, repro, attempt)
        results.append(evaluate_agent(ev, cfg))
        notes += attempt_notes
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
                "oracles": [r.__dict__ for r in results[-1]],
                "logs": [line.text for line in ev.base.logs][-80:],
            }
            path.write_text(json.dumps(redact_obj(data), indent=1, default=str), encoding="utf-8")
    final_key = key or primary_key(results)
    return decide("agentic", final_key, results), final_key, notes


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
    elif candidate.get("recheck"):
        # A stored finding that no longer reproduces: record a passing check so its bugs become fixed.
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
