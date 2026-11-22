"""The agentic phase of a run: recon -> strategy -> regression re-checks -> exploration -> confirmation."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentri.agent.explorer import ExploreBudget, Explorer
from sentri.agent.identities import make_login
from sentri.agent.recon import Recon
from sentri.agent.replay import ReplayEnv, agentic_oracle_config, confirm, record
from sentri.agent.session import AgentSession
from sentri.agent.strategy import plan_strategy
from sentri.contract.models import Contract
from sentri.contract.placeholders import DataGenerator, seed_for
from sentri.feed import EventBus
from sentri.ledger.ledger import Ledger
from sentri.llm import BudgetedProvider
from sentri.runner.browser import BrowserManager
from sentri.sandbox.base import Sandbox


@dataclass
class AgenticResult:
    pages: int = 0
    endpoints: int = 0
    hypotheses: int = 0
    steps: int = 0
    candidates: int = 0
    confirmed: int = 0
    regressions: int = 0
    rechecked: int = 0
    unconfirmed: int = 0
    inconclusive: int = 0
    exploration_stop: str = ""
    fallbacks: int = 0
    invalid: int = 0
    stalls: int = 0
    stats: dict[str, Any] = field(default_factory=dict)  # explorer coverage (states, actions, origins)
    notes: list[str] = field(default_factory=list)


async def run_agentic(
    *,
    contract: Contract,
    sandbox: Sandbox,
    browser: BrowserManager,
    provider: BudgetedProvider,
    ledger: Ledger,
    bus: EventBus,
    run_id: int,
    commit: str,
    budget: ExploreBudget,
    evidence_dir: Path,
    on_page: Any = None,
) -> AgenticResult:
    out = AgenticResult()
    tag = f"r{run_id}"
    tmp = Path(tempfile.mkdtemp(prefix="sentri-agent-"))
    login = make_login(contract, tmp, tag)

    if login is None:
        note = (
            "no auth in the contract (auth.login_flow or auth.api): only the logged-out identity exists, so "
            "cross-user checks (user b reading user a's data, by id or in pages) and login-boundary tests do not run"
        )
        out.notes.append(note)
        bus.emit("warning", note)

    # 1. Recon
    bus.emit("phase", "recon: exploring the app before attacking it")
    await sandbox.reset()
    session = AgentSession(browser, sandbox, contract.app.url, login, tmp)
    try:
        await session.start()
        app_map = await Recon(session, DataGenerator(seed_for(tag, "recon"), contract.fixtures, tmp, tag)).run()
    finally:
        await session.close()
    ledger.save_app_map(run_id, app_map.model_dump())
    out.pages, out.endpoints = len(app_map.pages), len(app_map.endpoints)
    protected = sorted(app_map.protected_paths())
    bus.emit(
        "recon",
        f"recon: {out.pages} pages, {out.endpoints} endpoints, {len(protected)} login-only pages",
        pages=out.pages,
        endpoints=out.endpoints,
        protected=protected,
    )

    # 2. Understand
    planned = await plan_strategy(app_map, provider)  # black box: no README, only what recon saw
    ledger.save_strategy(run_id, planned.source, planned.model_dump())
    out.hypotheses = len(planned.strategy.hypotheses)
    if planned.error:
        out.notes.append(f"strategy: {planned.error}")
    bus.emit(
        "strategy",
        f"strategy: {planned.strategy.app_kind or 'app'}; {out.hypotheses} hypotheses"
        + (f" ({len(planned.dropped)} dropped: not grounded in the app map)" if planned.dropped else ""),
        hypotheses=[
            h.model_dump(include={"id", "title", "category", "targets", "priority"})
            for h in planned.strategy.hypotheses
        ],
        dropped=planned.dropped,
    )

    env = ReplayEnv(contract, sandbox, browser, app_map, tag, evidence_dir=evidence_dir)

    run_hits: dict[str, Any] = {}  # key -> everything confirmed for it in this run (see replay.record)

    # 3. Re-check findings confirmed in earlier runs (regressions and fixes)
    for stored in ledger.confirmed_repros():
        verdict, key, notes = await confirm(env, stored["repro"], key=stored["attack_id"])
        candidate = {
            "title": stored["title"],
            "repro": stored["repro"],
            "hypothesis_id": stored["hypothesis_id"],
            "recheck": True,
        }
        _check, events = record(ledger, run_id, commit, candidate, verdict, key, run_hits)
        out.rechecked += 1
        if verdict.verdict == "error":
            out.notes.append(f"re-check '{stored['title']}' could not be replayed: {'; '.join(notes[:2])}")
        out.regressions += sum(1 for e in events if e.kind == "regression")
        bus.emit(
            "recheck", f"re-check '{stored['title']}': {verdict.verdict.upper()}", key=key, verdict=verdict.verdict
        )

    # 4. Explore
    if not planned.strategy.hypotheses:
        out.notes.append("no grounded hypotheses; exploration skipped")
        return out
    bus.emit("phase", f"exploration: up to {budget.max_steps} steps, {budget.max_seconds / 60:g} minutes")
    await sandbox.reset()
    session = AgentSession(browser, sandbox, contract.app.url, login, tmp)
    session.protected = set(protected)
    session.proven_protected = app_map.proven_protected_paths()
    session.anon_shells = tuple(app_map.anon_shells)
    session.login_path = app_map.login_path

    def record_step(step: dict[str, Any]) -> None:
        ledger.add_step(run_id, step)
        if on_page is not None:
            on_page(session.page)

    try:
        await session.start()
        explored = await Explorer(
            session,
            provider,
            planned.strategy,
            app_map,
            budget,
            record_step,
            emit=bus.emit,
            oracle_cfg=agentic_oracle_config(contract),
        ).run()
    finally:
        await session.close()
        if on_page is not None:
            on_page(None)
    out.steps, out.candidates = explored.steps, len(explored.candidates)
    out.exploration_stop = explored.stopped
    out.fallbacks, out.invalid, out.stalls = explored.fallbacks, explored.invalid, explored.stalls
    out.stats = dict(explored.stats)
    bus.emit(
        "phase",
        f"exploration finished after {explored.steps} steps ({explored.stopped}); "
        f"{out.candidates} candidates to confirm"
        + (
            f" [{explored.fallbacks} fallback, {explored.invalid} invalid, {explored.stalls} stalls]"
            if explored.fallbacks or explored.invalid or explored.stalls
            else ""
        ),
    )

    # 5. Confirm each candidate by replaying it 3x from a clean state
    seen: set[str] = set()
    for cand in sorted(explored.candidates, key=lambda c: len(c.repro)):
        data = cand.to_dict()
        signal_keys = sorted(f"{s['oracle_id']}|{s.get('route')}" for s in cand.signals)
        fingerprint = json.dumps(signal_keys) if signal_keys else json.dumps(data["repro"], sort_keys=True)
        if fingerprint in seen:
            continue  # the same break, already confirmed through a shorter path
        seen.add(fingerprint)
        verdict, key, notes = await confirm(env, data["repro"])
        _check, events = record(ledger, run_id, commit, data, verdict, key, run_hits)
        out.regressions += sum(1 for e in events if e.kind == "regression")
        if verdict.verdict in ("fail", "flaky"):
            out.confirmed += 1
        elif verdict.verdict == "error":
            out.inconclusive += 1
        else:
            out.unconfirmed += 1
        details = "; ".join(f"{h.detail} [{h.k}/{verdict.attempts}]" for h in verdict.hits) or (
            "could not be replayed (inconclusive)" if verdict.verdict == "error" else "did not reproduce"
        )
        bus.emit(
            "finding",
            f"{cand.title}: {verdict.verdict.upper()} - {details}",
            key=key,
            verdict=verdict.verdict,
            from_step=cand.from_step,
            to_step=cand.to_step,
            notes=notes[:3],
        )
    return out
