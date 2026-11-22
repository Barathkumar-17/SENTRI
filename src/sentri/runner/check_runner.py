"""Runs one check (flow x attack x payload) as N clean attempts and returns the verdict.

Used by the run orchestrator and by the Fixer gate. It records nothing in the ledger itself.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from sentri.attacks import ATTACKS, AttackContext, Payload, remove_throttle
from sentri.contract.models import Contract, Flow
from sentri.contract.placeholders import DataGenerator, seed_for
from sentri.evidence.collector import EvidenceCollector
from sentri.evidence.models import DuplicateMethod, Evidence
from sentri.redact import redact_obj
from sentri.runner.browser import BrowserManager
from sentri.runner.flow import AttackStop, FlowExecutor, NavigationTimeout, StepBlocked
from sentri.sandbox.base import Sandbox, SandboxError
from sentri.verdict.engine import CheckVerdict, decide
from sentri.verdict.oracles import OracleConfig, OracleResult, evaluate_all

log = logging.getLogger(__name__)


class InfraError(Exception):
    """Sentri or the sandbox broke, not the app. Never becomes an app verdict."""


@dataclass
class AttemptResult:
    evidence: Evidence
    results: list[OracleResult]
    evidence_path: str | None = None


@dataclass
class CheckOutcome:
    verdict: CheckVerdict | None
    attempts: list[AttemptResult] = field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0

    @property
    def screenshot_path(self) -> str | None:
        for a in reversed(self.attempts):
            if a.evidence.screenshot_path:
                return a.evidence.screenshot_path
        return None

    @property
    def duplicate_method(self) -> str | None:
        return self.attempts[-1].evidence.duplicate_method if self.attempts else None

    def summary(self) -> dict[str, Any]:
        """Compact evidence summary for the feed, the report and the Prober (never raw logs)."""
        out: dict[str, Any] = {"verdict": self.verdict.verdict if self.verdict else "error", "attempts": []}
        for a in self.attempts:
            ev = a.evidence
            statuses = sorted({f"{r.method} {r.status}" for r in ev.http if r.same_origin and r.status})
            slowest = max((r.duration_ms for r in ev.http if r.same_origin), default=0)
            out["attempts"].append(
                {
                    "oracles_fired": [r.oracle_id for r in a.results if r.fired],
                    "details": [r.detail for r in a.results if r.fired or r.detail],
                    "statuses": statuses[:12],
                    "slowest_ms": slowest,
                    "console": [c.text[:160] for c in ev.console][:5],
                    "step_blocked": ev.step_blocked,
                    "list_counts": [ev.list_count_before, ev.list_count_after],
                }
            )
        return out


class CheckRunner:
    def __init__(
        self,
        contract: Contract,
        sandbox: Sandbox,
        browser: BrowserManager,
        *,
        attempts: int = 3,
        evidence_dir: Path | None = None,
        bad_file_mb: int = 50,
        run_tag: str = "local",
        on_action: Callable[[str, int, str], None] | None = None,
        on_page: Callable[[Page | None], None] | None = None,
        workdir: Path | None = None,
    ):
        self.contract = contract
        self.sandbox = sandbox
        self.browser = browser
        self.attempts = attempts
        self.evidence_dir = evidence_dir
        self.bad_file_mb = bad_file_mb
        self.run_tag = run_tag
        self.on_action = on_action or (lambda _k, _a, _t: None)
        self.on_page = on_page or (lambda _p: None)
        self.workdir = workdir or Path.cwd()
        self.tmp_root = Path(tempfile.mkdtemp(prefix="sentri-"))
        self.sandbox_fresh = True  # the sandbox was just started: the first attempt needs no reset
        self.reset_seconds: list[float] = []  # time spent resetting the app, for the run summary

    # ---- helpers ------------------------------------------------------------------------------------
    def timeouts(self, flow: Flow, attack_id: str) -> tuple[float, int]:
        timeout_s = self.contract.action_timeout_s(flow)
        settle_ms = self.contract.defaults.settle_ms
        if attack_id == "slow_network":
            mult = self.contract.defaults.slow_network_timeout_multiplier
            timeout_s *= mult
            settle_ms = int(settle_ms * mult)
        return timeout_s, settle_ms

    def generator(self, flow: Flow, attack_id: str, attempt: int, salt: str = "") -> DataGenerator:
        seed = seed_for(self.run_tag, flow.id, attack_id, attempt, salt)
        tag = f"{self.run_tag}-{attempt}"
        return DataGenerator(seed, self.contract.fixtures, self.tmp_root / f"{flow.id}-{attack_id}", tag)

    async def _reset(self) -> None:
        if self.sandbox_fresh:
            self.sandbox_fresh = False
            return
        started = time.monotonic()
        try:
            await self.sandbox.reset()
        except SandboxError as exc:
            raise InfraError(f"sandbox reset failed: {exc}") from None
        self.reset_seconds.append(time.monotonic() - started)

    async def login(self, page: Page, flow: Flow, attempt: int) -> None:
        from sentri.agent.identities import LoginFailed, make_login

        login = make_login(self.contract, self.tmp_root / "login", f"{self.run_tag}-{attempt}")
        if login is None:
            raise InfraError("flow requires login but the contract has no auth")
        try:
            await login(page, "a")
        except LoginFailed as exc:
            raise InfraError(str(exc)) from None

    async def _list_count(self, page: Page, flow: Flow, timeout_ms: float) -> int | None:
        if not flow.list_selector:
            return None
        target = flow.list_page or flow.start
        try:
            await page.goto(self.contract.app.url + "/" + target.lstrip("/"), wait_until="load", timeout=timeout_ms)
            with contextlib.suppress(PlaywrightTimeout):
                await page.wait_for_load_state("networkidle", timeout=2000)
            return await page.locator(flow.list_selector).count()
        except PlaywrightError:
            return None

    async def _settle(self, page: Page, collector: EvidenceCollector, timeout_s: float, settle_ms: int) -> None:
        try:
            await page.wait_for_load_state("load", timeout=timeout_s * 1000)
        except PlaywrightTimeout:
            collector.mark_timeout(f"page did not finish loading within {timeout_s:g}s")
        except PlaywrightError:
            pass
        with contextlib.suppress(PlaywrightError):
            await page.wait_for_load_state("networkidle", timeout=max(settle_ms, 1))
        await collector.wait_for_quiet(timeout_s)
        await asyncio.sleep(0.3)  # let the app flush its log lines

    async def _page_rows(self, page: Page) -> tuple[str, ...]:
        try:
            text = await page.locator("body").inner_text(timeout=2000)
        except PlaywrightError:
            return ()
        return tuple(line.strip() for line in text.splitlines() if line.strip())[:2000]

    # ---- one attempt --------------------------------------------------------------------------------
    async def run_attempt(
        self, flow: Flow, attack_id: str, payload: Payload, attempt: int, check_key: str, salt: str = ""
    ) -> Evidence:
        attack = ATTACKS[attack_id]
        timeout_s, settle_ms = self.timeouts(flow, attack_id)
        await self._reset()
        if not self.sandbox.is_alive():
            raise InfraError("app is not running")

        context = await self.browser.new_context(fence_origin=self.contract.app.url)
        page = await context.new_page()
        self.on_page(page)
        collector = EvidenceCollector(self.sandbox, self.contract.app.url)
        collector.attach(page)
        gen = self.generator(flow, attack_id, attempt, salt)

        def act(text: str) -> None:
            collector.action(text)
            self.on_action(check_key, attempt, text)

        ctx = AttackContext(
            page=page,
            context=context,
            executor=FlowExecutor(page, flow, gen, self.contract.app.url, timeout_s * 1000, act),
            timeout_ms=timeout_s * 1000,
            tmp_dir=self.tmp_root,
            bad_file_mb=self.bad_file_mb,
            emit=act,
        )
        try:
            if flow.requires_login:
                await self.login(page, flow, attempt)
            # Black box: duplicates are counted where a user sees them (the list page), never in the database.
            method: DuplicateMethod = "none"
            before = await self._list_count(page, flow, timeout_s * 1000)
            if before is not None:
                method = "list_selector"

            collector.open_window()
            try:
                await attack.execute(ctx, payload)
            except NavigationTimeout as exc:
                collector.mark_timeout(str(exc))
            except StepBlocked as exc:
                collector.step_blocked = str(exc)
            except PlaywrightTimeout as exc:
                collector.mark_timeout(f"action timed out: {exc.message.splitlines()[0]}")
            except PlaywrightError as exc:
                if not collector.page_crashed:
                    if not self.sandbox.is_alive():
                        raise InfraError("app process died during the attack") from None
                    collector.step_blocked = f"browser error: {exc.message.splitlines()[0]}"
            if not page.is_closed() and not collector.page_crashed:
                await self._settle(page, collector, timeout_s, settle_ms)
            collector.close_window()
            await remove_throttle(ctx)

            rows = await self._page_rows(page) if not page.is_closed() else ()
            shot = await self._screenshot(page, check_key, attempt)
            after = None
            if method == "list_selector":
                after = await self._list_count(page, flow, self.contract.action_timeout_s(flow) * 1000)
            return collector.build(
                list_count_before=before,
                list_count_after=after,
                duplicate_method=method,
                page_text_rows=rows,
                unique_values=tuple(gen.unique_values()),
                expected_creates=flow.expected_creates,
                screenshot_path=shot,
            )
        finally:
            self.on_page(None)
            with contextlib.suppress(PlaywrightError):
                await context.close()

    async def _screenshot(self, page: Page, check_key: str, attempt: int) -> str | None:
        if self.evidence_dir is None or page.is_closed():
            return None
        path = self.evidence_dir / f"{check_key.replace(':', '__')}-a{attempt}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(path), type="jpeg", quality=60, timeout=5000)
            return str(path)
        except PlaywrightError:
            return None

    def _write_evidence(self, check_key: str, attempt: int, ev: Evidence, results: list[OracleResult]) -> str | None:
        if self.evidence_dir is None:
            return None
        path = self.evidence_dir / f"{check_key.replace(':', '__')}-a{attempt}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"evidence": ev.to_dict(), "oracles": [r.__dict__ for r in results]}
        path.write_text(json.dumps(redact_obj(data), indent=1, default=str), encoding="utf-8")
        return str(path)

    # ---- one check ----------------------------------------------------------------------------------
    async def run_check(
        self, flow: Flow, attack_id: str, payload: Payload, check_key: str | None = None, salt: str = ""
    ) -> CheckOutcome:
        check_key = check_key or f"{flow.id}:{attack_id}"
        cfg = OracleConfig(
            flow_id=flow.id,
            attack_id=attack_id,
            ignore_console=tuple(self.contract.app.ignore_console),
            extra_log_patterns=tuple(self.contract.app.log_patterns_extra),
            expected_creates=flow.expected_creates,
            app_origin=self.contract.app.url,
        )
        started = time.monotonic()
        outcome = CheckOutcome(verdict=None)
        for attempt in range(1, self.attempts + 1):
            ev: Evidence | None = None
            for infra_try in range(2):
                try:
                    ev = await self.run_attempt(flow, attack_id, payload, attempt, check_key, salt)
                    break
                except InfraError as exc:
                    log.warning("%s attempt %d: infrastructure error: %s", check_key, attempt, exc)
                    if infra_try == 1:
                        outcome.error = str(exc)
                        outcome.duration_s = time.monotonic() - started
                        return outcome
                    self.sandbox_fresh = False
                    try:
                        await self.sandbox.stop()
                        await self.sandbox.start()
                        self.sandbox_fresh = True
                    except SandboxError as restart_exc:
                        outcome.error = f"{exc}; restart failed: {restart_exc}"
                        outcome.duration_s = time.monotonic() - started
                        return outcome
            assert ev is not None
            results = evaluate_all(ev, cfg)
            path = self._write_evidence(check_key, attempt, ev, results)
            outcome.attempts.append(AttemptResult(ev, results, path))
        outcome.verdict = decide(flow.id, attack_id, [a.results for a in outcome.attempts])
        outcome.duration_s = time.monotonic() - started
        return outcome

    async def happy_path(self, flow: Flow) -> tuple[bool, str]:
        """Run a flow unattacked once and check its success condition (contract preflight)."""
        await self._reset()
        context = await self.browser.new_context(fence_origin=self.contract.app.url)
        page = await context.new_page()
        try:
            if flow.requires_login:
                await self.login(page, flow, 0)
            gen = self.generator(flow, "happy", 0)
            ex = FlowExecutor(page, flow, gen, self.contract.app.url, self.contract.action_timeout_s(flow) * 1000)
            try:
                await ex.start()
                await ex.run_all()
            except AttackStop as exc:
                return False, str(exc)
            return await ex.check_success()
        except InfraError as exc:
            return False, str(exc)
        finally:
            with contextlib.suppress(PlaywrightError):
                await context.close()
