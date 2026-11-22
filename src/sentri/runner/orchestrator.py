"""One `sentri run`: the lifecycle in docs/reference/architecture.md section 4."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Page

from sentri.attacker import Attacker, PayloadChoice, Prober
from sentri.attacks import ATTACKS
from sentri.config import Settings
from sentri.contract.loader import contract_hash
from sentri.contract.models import Contract, Flow
from sentri.feed import EventBus
from sentri.ledger import CheckRecord, Ledger
from sentri.llm import BudgetedProvider
from sentri.runner.browser import BrowserManager
from sentri.runner.check_runner import CheckOutcome, CheckRunner
from sentri.runner.fingerprint import app_fingerprint
from sentri.sandbox import SandboxError, make_sandbox
from sentri.verdict.scorer import AgenticFinding, ScoreBreakdown, compute, parse_hits

log = logging.getLogger(__name__)


@dataclass
class RunOptions:
    commit: str = "local"
    branch: str = "local"
    reuse: bool = True
    only: list[str] = field(default_factory=list)  # "flow" or "flow:attack"
    preflight: bool = True
    exclude_paths: list[Path] = field(default_factory=list)  # contract/report files inside the app dir
    mode: str = "agentic"  # agentic (recon, strategy, exploration, then the baseline suite) | baseline


@dataclass
class RunResult:
    run_id: int
    status: str  # finished | aborted
    breakdown: ScoreBreakdown | None
    regressions: int = 0
    error: str | None = None


@dataclass
class Cell:
    flow: Flow
    attack_id: str
    applies: bool
    na_reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.flow.id}:{self.attack_id}"


def _selected(only: list[str], flow_id: str, attack_id: str) -> bool:
    if not only:
        return True
    return any(o == flow_id or o == f"{flow_id}:{attack_id}" for o in only)


class RunOrchestrator:
    def __init__(
        self,
        contract: Contract,
        workdir: Path,
        settings: Settings,
        ledger: Ledger,
        provider: BudgetedProvider,
        options: RunOptions,
        echo: Any = None,
        on_run_created: Any = None,
    ):
        self.contract = contract
        self.workdir = workdir
        self.settings = settings
        self.ledger = ledger
        self.provider = provider
        self.options = options
        self.echo = echo
        self.current_page: Page | None = None
        self.llm_used = False
        self.static_used = False
        self._last_outcome: CheckOutcome | None = None
        self.on_run_created = on_run_created  # UI jobs learn the run id as soon as it exists
        self.run_id: int | None = None
        self.coverage: dict[str, Any] | None = None  # measured exploration coverage + completion (agentic phase)

    # ---- grid ---------------------------------------------------------------------------------------
    def build_grid(self) -> list[Cell]:
        cells = []
        for flow in self.contract.attacked_flows():
            for attack_id in self.contract.attacks_enabled:
                if not _selected(self.options.only, flow.id, attack_id):
                    continue
                app = ATTACKS[attack_id].applies_to(flow)
                cells.append(Cell(flow, attack_id, app.applies, app.reason))
        return cells

    def order(self, cells: list[Cell]) -> list[Cell]:
        def rank(cell: Cell) -> int:
            if self.ledger.cell_has_problem_bugs(cell.flow.id, cell.attack_id):
                return 0
            if self.ledger.recently_regressed(cell.flow.id, cell.attack_id):
                return 1
            return 2

        return sorted(cells, key=rank)  # stable: contract order within a rank

    # ---- live view ----------------------------------------------------------------------------------
    def _set_page(self, page: Page | None) -> None:
        self.current_page = page

    async def _screenshot_loop(self) -> None:
        live = self.settings.live_path
        live.parent.mkdir(parents=True, exist_ok=True)
        tmp = live.with_suffix(".tmp.jpg")
        while True:
            page = self.current_page
            if page is not None and not page.is_closed():
                with contextlib.suppress(Exception):
                    await page.screenshot(path=str(tmp), type="jpeg", quality=50, timeout=2000)
                    os.replace(tmp, live)
            await asyncio.sleep(1.0)

    # ---- run ----------------------------------------------------------------------------------------
    async def run(self) -> RunResult:
        c = self.contract
        app_root = self.workdir / (c.app.docker.context if c.app.docker else c.app.cwd)
        # Command mode inherits Sentri's environment, so a changed variable must count as a change.
        inherited = dict(os.environ) if c.app.run is not None else {}
        fingerprint = app_fingerprint(app_root, self.options.exclude_paths, inherited)
        chash = contract_hash(c)
        run_id = self.ledger.create_run(self.options.commit, self.options.branch, fingerprint, chash)
        self.run_id = run_id
        if self.on_run_created is not None:
            self.on_run_created(run_id)
        bus = EventBus(self.ledger, run_id, self.echo)
        started = time.monotonic()
        evidence_dir = self.settings.evidence_dir / str(run_id)

        cells = self.order(self.build_grid())
        flows = sorted({cell.flow.id for cell in cells})
        attacks = [a for a in c.attacks_enabled if any(cell.attack_id == a for cell in cells)]
        bus.emit(
            "run_started",
            f"run {run_id} on {self.options.commit[:12]} ({len(cells)} cells, llm: {self.provider.name})",
            run_id=run_id,
            commit_sha=self.options.commit,
            grid={"flows": flows, "attacks": attacks},
        )

        sandbox = make_sandbox(c.app, self.workdir)
        browser = BrowserManager(
            headless=self.settings.headless, fence_origin=c.app.url, offsite_assets=c.app.offsite_assets
        )
        try:
            bus.emit("sandbox_starting", "starting the app in the sandbox")
            t0 = time.monotonic()
            await sandbox.start()
            bus.emit("sandbox_ready", f"app ready in {time.monotonic() - t0:.1f}s", seconds=time.monotonic() - t0)
        except SandboxError as exc:
            return self._abort(run_id, bus, started, f"app failed to start: {exc}")

        if c.duplicate_db is not None:
            bus.emit(
                "warning",
                "duplicate_db is ignored: verdicts are black-box, so duplicates are counted on the list page "
                "(list_selector / list_page), not in the database",
            )
        reset_kind = getattr(sandbox, "reset_kind", "restart")
        bus.emit(
            "replay_state",
            "attempts start from a restarted app (server-side state outside app.clean survives)"
            if reset_kind == "restart"
            else "URL mode: Sentri cannot reset the app; attempts and replays run on its live state",
            reset_kind=reset_kind,
        )
        shots: asyncio.Task[None] | None = None
        try:
            await browser.start()
            if self.settings.live_view:
                shots = asyncio.create_task(self._screenshot_loop())
            runner = CheckRunner(
                c,
                sandbox,
                browser,
                attempts=self.settings.attempts,
                evidence_dir=evidence_dir,
                bad_file_mb=self.settings.bad_file_mb,
                run_tag=f"r{run_id}",
                on_action=lambda key, attempt, text: _emit_action(bus, key, attempt, text),
                on_page=self._set_page,
                workdir=self.workdir,
            )
            if self.options.preflight:
                problems = await self._preflight(runner, cells, bus)
                if problems:
                    return self._abort(run_id, bus, started, "contract preflight failed: " + "; ".join(problems))

            regressions = 0
            agentic_ran = False
            phases: dict[str, str] = {"preflight": "done" if self.options.preflight else "skipped (--skip-preflight)"}
            agentic_cov: dict[str, Any] | None = None
            if self.options.mode != "agentic":
                phases["agentic"] = "skipped (--mode baseline)"
            if self.options.mode == "agentic":
                if self.provider.available:
                    from sentri.agent.explorer import ExploreBudget
                    from sentri.agent.run import run_agentic

                    budget = ExploreBudget(
                        max_steps=self.settings.explore_steps, max_seconds=self.settings.explore_minutes * 60
                    )
                    agentic = await run_agentic(
                        contract=c,
                        sandbox=sandbox,
                        browser=browser,
                        provider=self.provider,
                        ledger=self.ledger,
                        bus=bus,
                        run_id=run_id,
                        commit=self.options.commit,
                        budget=budget,
                        evidence_dir=evidence_dir,
                        on_page=self._set_page,
                    )
                    self.llm_used = True
                    agentic_ran = True
                    regressions += agentic.regressions
                    runner.sandbox_fresh = False
                    phases["agentic"] = f"done; exploration stopped: {agentic.exploration_stop or 'n/a'}"
                    agentic_cov = {
                        "pages": agentic.pages,
                        "endpoints": agentic.endpoints,
                        "hypotheses": agentic.hypotheses,
                        "steps": agentic.steps,
                        "invalid_steps": agentic.invalid,
                        "fallback_steps": agentic.fallbacks,
                        "stalls": agentic.stalls,
                        "candidates": agentic.candidates,
                        "confirmed": agentic.confirmed,
                        "unconfirmed": agentic.unconfirmed,
                        "inconclusive": agentic.inconclusive,
                        "rechecked": agentic.rechecked,
                        "exploration_stop": agentic.exploration_stop,
                        **agentic.stats,
                        "notes": agentic.notes[:10],
                    }
                    bus.emit(
                        "agentic_finished",
                        f"agentic: {agentic.confirmed} confirmed of {agentic.candidates} candidates in "
                        f"{agentic.steps} steps; {agentic.rechecked} earlier findings re-checked",
                        **agentic.__dict__,
                    )
                else:
                    phases["agentic"] = "skipped (no model)"
                    bus.emit(
                        "agentic_skipped",
                        "agentic mode needs a model; running the baseline suite only "
                        "(choose one with --llm-provider, or see `sentri models`)",
                    )
            bus.emit("phase", "baseline suite: the six fixed attacks (comparable score across commits)")
            attacker = Attacker(self.provider)
            prober = Prober(self.provider, self.settings.max_probes_per_flow)
            for cell in cells:
                regressions += await self._run_cell(run_id, cell, runner, attacker, bus, fingerprint, chash)
                if cell.applies:
                    regressions += await self._maybe_probe(run_id, cell, runner, prober, bus)

            if runner.reset_seconds:
                total = sum(runner.reset_seconds)
                bus.emit(
                    "timing",
                    f"{len(runner.reset_seconds)} app resets took {total:.1f}s "
                    f"(avg {total / len(runner.reset_seconds):.1f}s); run so far {time.monotonic() - started:.1f}s",
                    resets=len(runner.reset_seconds),
                    reset_seconds=round(total, 2),
                )
            breakdown = self._score(run_id, regressions, agentic_ran)
            phases["baseline"] = f"done ({len(cells)} cells)" if cells else "no baseline cells in the contract"
            self.coverage = coverage_record(
                self.ledger.checks_for_run(run_id),
                agentic_cov,
                phases,
                getattr(sandbox, "reset_kind", "restart"),
                self.provider.stop_reason,
            )
            self._finish(run_id, "finished", breakdown, started)
            bus.emit(
                "run_finished",
                f"run {run_id} finished: readiness "
                f"{breakdown.score if breakdown.score is not None else 'insufficient coverage'}",
                run_id=run_id,
                readiness=breakdown.score,
                breakdown=breakdown.__dict__,
                llm_calls=self.provider.calls,
                llm_input_tokens=self.provider.input_tokens,
                llm_output_tokens=self.provider.output_tokens,
            )
            bus.emit(
                "llm_usage",
                f"model: {self.provider.calls} calls, {self.provider.input_tokens} input + "
                f"{self.provider.output_tokens} output tokens, ${self.provider.spent_usd:.4f}",
            )
            return RunResult(run_id, "finished", breakdown, regressions)
        finally:
            if shots is not None:
                shots.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await shots
            with contextlib.suppress(Exception):
                await browser.stop()
            with contextlib.suppress(Exception):
                await sandbox.stop()

    async def _preflight(self, runner: CheckRunner, cells: list[Cell], bus: EventBus) -> list[str]:
        problems = []
        flows: dict[str, Flow] = {cell.flow.id: cell.flow for cell in cells if cell.applies}
        for flow in flows.values():
            ok, detail = await runner.happy_path(flow)
            bus.emit("preflight", f"happy path {flow.id}: {'ok' if ok else detail}", flow_id=flow.id, ok=ok)
            if not ok:
                problems.append(f"{flow.id}: {detail}")
        return problems

    def _abort(self, run_id: int, bus: EventBus, started: float, error: str) -> RunResult:
        self._finish(run_id, "aborted", None, started)
        bus.emit("run_aborted", error, run_id=run_id)
        return RunResult(run_id, "aborted", None, error=error)

    def _finish(self, run_id: int, status: str, breakdown: ScoreBreakdown | None, started: float) -> None:
        mode = "mixed" if (self.llm_used and self.static_used) else ("llm" if self.llm_used else "fallback")
        self.ledger.finish_run(
            run_id,
            status,
            breakdown.score if breakdown else None,
            round(time.monotonic() - started, 2),
            round(self.provider.spent_usd, 6) if self.provider.cost_known else None,
            mode,
            breakdown.__dict__ if breakdown else None,
            usage=self.provider.usage(),
            coverage=self.coverage,
        )

    def _score(self, run_id: int, regressions: int, agentic_ran: bool = False) -> ScoreBreakdown:
        return score_run(self.ledger, run_id, regressions, self.settings.regression_penalty, agentic_ran)

    async def _run_cell(
        self,
        run_id: int,
        cell: Cell,
        runner: CheckRunner,
        attacker: Attacker,
        bus: EventBus,
        fingerprint: str,
        chash: str,
    ) -> int:
        flow, attack_id = cell.flow, cell.attack_id
        if not cell.applies:
            check_id = self.ledger.insert_check(CheckRecord(run_id, flow.id, attack_id, "na", error_msg=cell.na_reason))
            bus.emit(
                "check_finished",
                f"{cell.key}: N/A ({cell.na_reason})",
                check_id=check_id,
                flow_id=flow.id,
                attack_id=attack_id,
                verdict="na",
                oracle_hits="",
                bug_ids=[],
                summary=cell.na_reason,
            )
            return 0

        if self.options.reuse:
            prev = self.ledger.find_reusable(flow.id, attack_id, fingerprint, chash)
            if prev is not None:
                check_id = self.ledger.insert_check(
                    CheckRecord(
                        run_id,
                        flow.id,
                        attack_id,
                        "pass",
                        attempts=0,
                        reused_from_check_id=prev["id"],
                        reason="reused: passed on unchanged code",
                    )
                )
                bus.emit(
                    "check_reused",
                    f"{cell.key}: pass (reused from check {prev['id']}, code unchanged)",
                    check_id=check_id,
                    flow_id=flow.id,
                    attack_id=attack_id,
                    verdict="pass",
                    from_check_id=prev["id"],
                )
                return 0

        attack = ATTACKS[attack_id]
        history = self.ledger.cell_history(flow.id, attack_id)
        rotation = self.ledger.cell_run_count(flow.id, attack_id)
        choice = await attacker.choose(attack, flow, history, rotation)
        if choice.llm_error:
            bus.emit(
                "llm_fallback",
                f"model unavailable for {cell.key}, using static payload",
                role="attacker",
                error=choice.llm_error,
            )
        return await self._execute(run_id, cell, attack_id, choice, runner, bus, is_probe=False)

    async def _execute(
        self,
        run_id: int,
        cell: Cell,
        attack_id: str,
        choice: PayloadChoice,
        runner: CheckRunner,
        bus: EventBus,
        is_probe: bool,
    ) -> int:
        flow = cell.flow
        key = f"{flow.id}:{attack_id}" + (":probe" if is_probe else "")
        if choice.source == "llm":
            self.llm_used = True
        elif choice.source == "static":
            self.static_used = True
        bus.emit(
            "check_started",
            f"{key}: {ATTACKS[attack_id].description}",
            reason=choice.reason,
            check_key=key,
            flow_id=flow.id,
            attack_id=attack_id,
            payload=_display_payload(choice.payload),
            payload_source=choice.source,
        )

        outcome = await runner.run_check(flow, attack_id, choice.payload, key, salt="probe" if is_probe else "")
        for i, attempt in enumerate(outcome.attempts, start=1):
            fired = [r.oracle_id for r in attempt.results if r.fired]
            bus.emit(
                "attempt_finished",
                f"{key} attempt {i}: {', '.join(fired) or 'no oracle fired'}",
                check_key=key,
                attempt=i,
                oracles_fired=fired,
            )

        if outcome.verdict is None:
            check_id = self.ledger.insert_check(
                CheckRecord(
                    run_id,
                    flow.id,
                    attack_id,
                    "error",
                    attempts=len(outcome.attempts),
                    payload=choice.payload,
                    payload_source=choice.source,
                    reason=choice.reason,
                    error_msg=outcome.error,
                    is_probe=is_probe,
                    duration_s=round(outcome.duration_s, 2),
                )
            )
            bus.emit(
                "check_finished",
                f"{key}: ERROR (infrastructure, not scored): {outcome.error}",
                check_id=check_id,
                flow_id=flow.id,
                attack_id=attack_id,
                verdict="error",
                oracle_hits="",
                bug_ids=[],
                summary=outcome.error,
            )
            return 0

        v = outcome.verdict
        check_id = self.ledger.insert_check(
            CheckRecord(
                run_id,
                flow.id,
                attack_id,
                v.verdict,
                oracle_hits=v.oracle_hits,
                attempts=v.attempts,
                payload=choice.payload,
                payload_source=choice.source,
                reason=choice.reason,
                screenshot_path=outcome.screenshot_path,
                evidence_path=outcome.attempts[-1].evidence_path if outcome.attempts else None,
                duplicate_method=outcome.duplicate_method,
                is_probe=is_probe,
                duration_s=round(outcome.duration_s, 2),
            )
        )
        events, score_verdict = self.ledger.record_check_result(
            run_id, check_id, flow.id, attack_id, self.options.commit, v
        )
        regressions = 0
        for ev in events:
            if ev.kind == "regression":
                regressions += 1
                bus.emit(
                    "regression",
                    f"{ev.bug_id} came back at {ev.commit_sha[:12]} ({ev.detail})",
                    bug_id=ev.bug_id,
                    flow_id=ev.flow_id,
                    attack_id=ev.attack_id,
                    commit_sha=ev.commit_sha,
                )
            elif ev.kind == "bug_fixed":
                bus.emit(
                    "bug_fixed",
                    f"{ev.bug_id} no longer reproduces",
                    bug_id=ev.bug_id,
                    flow_id=ev.flow_id,
                    attack_id=ev.attack_id,
                    commit_sha=ev.commit_sha,
                )
            elif ev.kind == "bug_needs_review":
                bus.emit(
                    "bug_needs_review",
                    f"{ev.bug_id}: {ev.detail}",
                    bug_id=ev.bug_id,
                    flow_id=ev.flow_id,
                    attack_id=ev.attack_id,
                    commit_sha=ev.commit_sha,
                )
            else:
                bus.emit(
                    "bug_opened",
                    f"{ev.bug_id} opened{' as flaky' if ev.kind == 'bug_flaky' else ''}: {ev.detail}",
                    bug_id=ev.bug_id,
                    flow_id=ev.flow_id,
                    attack_id=ev.attack_id,
                    commit_sha=ev.commit_sha,
                )
        bug_ids = [b["id"] for b in self.ledger.bugs_for_check(check_id)]
        details = "; ".join(f"{h.detail} [{h.k}/{v.attempts}]" for h in v.hits) or "no oracle fired"
        details += f" ({outcome.duration_s:.1f}s)"
        shown = score_verdict.upper() if score_verdict == v.verdict else f"{v.verdict.upper()} (intended, scored pass)"
        bus.emit(
            "check_finished",
            f"{key}: {shown} - {details}",
            check_id=check_id,
            flow_id=flow.id,
            attack_id=attack_id,
            verdict=v.verdict,
            score_verdict=score_verdict,
            oracle_hits=v.oracle_hits,
            bug_ids=bug_ids,
            summary=details,
            is_probe=is_probe,
        )
        self._last_outcome = outcome
        return regressions

    async def _maybe_probe(self, run_id: int, cell: Cell, runner: CheckRunner, prober: Prober, bus: EventBus) -> int:
        outcome, self._last_outcome = self._last_outcome, None
        if outcome is None or outcome.verdict is None:
            return 0
        applicable: list[str] = [a for a in self.contract.attacks_enabled if ATTACKS[a].applies_to(cell.flow).applies]
        proposal = await prober.propose(cell.flow, ATTACKS, applicable, outcome.summary())
        if proposal is None:
            return 0
        attack_id, choice = proposal
        bus.emit(
            "probe_queued",
            f"probe {cell.flow.id}:{attack_id}",
            reason=choice.reason,
            flow_id=cell.flow.id,
            attack_id=attack_id,
        )
        probe_cell = Cell(cell.flow, attack_id, True)
        return await self._execute(run_id, probe_cell, attack_id, choice, runner, bus, is_probe=True)


def coverage_record(
    checks: list[Any],
    agentic: dict[str, Any] | None,
    phases: dict[str, str],
    reset_kind: str,
    model_stop: str | None,
) -> dict[str, Any]:
    """What this run measurably covered and how it ended. Counts only; no whole-app percentages."""
    baseline = [c for c in checks if not c["is_probe"]]
    by_status = {
        "passed": sum(1 for c in baseline if c["score_verdict"] == "pass" and not c["reused_from_check_id"]),
        "reused_pass": sum(1 for c in baseline if c["reused_from_check_id"]),
        "failed": sum(1 for c in baseline if c["score_verdict"] == "fail"),
        "flaky": sum(1 for c in baseline if c["score_verdict"] == "flaky"),
        "inapplicable": sum(1 for c in baseline if c["verdict"] == "na"),
        "inconclusive": sum(1 for c in baseline if c["verdict"] == "error"),
    }
    probes = [c for c in checks if c["is_probe"] and c["flow_id"] != "agentic"]
    return {
        "baseline": {
            "flows": sorted({c["flow_id"] for c in baseline if c["verdict"] != "na"}),
            "checks": by_status,
            "probes": len(probes),
        },
        "agentic": agentic,
        "completion": {"phases": phases, "model_stop": model_stop},
        "replay_state": reset_kind,  # restart | none (URL mode: live state, no clean replays)
        "caveat": "Counts what this run tested. It is not a safety rating; untested behaviour is not covered.",
    }


def score_run(
    ledger: Ledger, run_id: int, regressions: int, penalty: int = 5, agentic_ran: bool = False
) -> ScoreBreakdown:
    """Baseline checks (probes excluded) plus every agentic finding replay confirmed in this run."""
    rows = ledger.checks_for_run(run_id)
    baseline = [r["score_verdict"] for r in rows if not r["is_probe"]]
    agentic = [
        AgenticFinding(r["attack_id"], parse_hits(r["oracle_hits"]), r["attempts"] or 0, r["score_verdict"])
        for r in rows
        if r["flow_id"] == "agentic"
    ]
    return compute(baseline, regressions, penalty, agentic, agentic_ran=agentic_ran or bool(agentic))


def _display_payload(payload: dict[str, str]) -> dict[str, str]:
    return {k: (v if len(v) <= 80 else v[:77] + "...") for k, v in payload.items()}


def _emit_action(bus: EventBus, key: str, attempt: int, text: str) -> None:
    bus.emit("action", text, check_key=key, attempt=attempt)
