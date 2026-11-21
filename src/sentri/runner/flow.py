"""Executes contract steps on a page, with per-step overrides for attacks."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from sentri.contract.models import Flow, Step
from sentri.contract.placeholders import DataGenerator


class AttackStop(Exception):
    """Ends an attack early without it being an infrastructure error."""


class StepBlocked(AttackStop):
    """The app prevented the next step (element gone, disabled, redirected). Not an oracle signal."""


class NavigationTimeout(AttackStop):
    """A navigation did not complete in time. The timeout oracle fires."""


Override = str | dict[str, Any]


class FlowExecutor:
    def __init__(
        self,
        page: Page,
        flow: Flow,
        gen: DataGenerator,
        base_url: str,
        timeout_ms: float,
        on_action: Callable[[str], None] | None = None,
    ):
        self.page = page
        self.flow = flow
        self.gen = gen
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms
        self.on_action = on_action or (lambda _t: None)

    def url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else self.base_url + "/" + path.lstrip("/")

    def locator(self, step: Step) -> Locator:
        assert step.selector is not None
        return self.page.locator(step.selector).first

    async def goto(self, path: str, wait_until: str = "load") -> None:
        self.on_action(f"open {path}")
        try:
            await self.page.goto(self.url(path), wait_until=wait_until, timeout=self.timeout_ms)  # type: ignore[arg-type]
        except PlaywrightTimeout:
            raise NavigationTimeout(f"navigation to {path} did not finish within {self.timeout_ms / 1000:g}s") from None

    async def reload(self, wait_until: str = "load") -> None:
        self.on_action("reload page")
        try:
            await self.page.reload(wait_until=wait_until, timeout=self.timeout_ms)  # type: ignore[arg-type]
        except PlaywrightTimeout:
            raise NavigationTimeout(f"reload did not finish within {self.timeout_ms / 1000:g}s") from None

    async def start(self) -> None:
        await self.goto(self.flow.start)

    async def _ready(self, step: Step, state: str = "visible") -> Locator:
        loc = self.locator(step)
        try:
            await loc.wait_for(state=state, timeout=self.timeout_ms)  # type: ignore[arg-type]
        except PlaywrightTimeout:
            raise StepBlocked(
                f"{step.kind}: '{step.selector}' not {state} within {self.timeout_ms / 1000:g}s"
            ) from None
        return loc

    def resolved_value(self, step: Step) -> str:
        return self.gen.resolve(step.value or "")

    async def run_step(self, index: int, override: Override | None = None) -> None:
        step = self.flow.steps[index]
        try:
            if step.kind == "goto":
                assert step.path is not None
                await self.goto(self.gen.resolve(step.path))
            elif step.kind == "fill":
                value = override if isinstance(override, str) else self.resolved_value(step)
                self.on_action(f"type {value!r} into {step.selector}")
                await (await self._ready(step)).fill(value, timeout=self.timeout_ms)
            elif step.kind == "select":
                value = override if isinstance(override, str) else self.resolved_value(step)
                self.on_action(f"select {value!r} in {step.selector}")
                await (await self._ready(step)).select_option(value, timeout=self.timeout_ms)
            elif step.kind == "upload":
                files: Any = override if override is not None else self.gen.resolve(step.file or "")
                name = files["name"] if isinstance(files, dict) else files
                self.on_action(f"upload {str(name)[:80]!r} to {step.selector}")
                await (await self._ready(step, "attached")).set_input_files(files, timeout=self.timeout_ms)
            elif step.kind == "click":
                self.on_action(f"click {step.selector}")
                await (await self._ready(step)).click(timeout=self.timeout_ms)
            elif step.kind == "press":
                assert step.key is not None
                self.on_action(f"press {step.key} in {step.selector}")
                await (await self._ready(step)).press(step.key, timeout=self.timeout_ms)
            elif step.kind == "wait_for":
                self.on_action(f"wait for {step.selector} {step.state}")
                await self._ready(step, step.state)
        except PlaywrightTimeout:
            raise StepBlocked(f"{step.kind} on '{step.selector}' did not complete") from None

    async def run_steps(self, indexes: Iterable[int], overrides: dict[int, Override] | None = None) -> None:
        overrides = overrides or {}
        for i in indexes:
            await self.run_step(i, overrides.get(i))

    async def run_prefix(self, overrides: dict[int, Override] | None = None) -> None:
        await self.run_steps(self.flow.prefix_indexes(), overrides)

    async def run_all(self) -> None:
        await self.run_steps(range(len(self.flow.steps)))

    def submit_step(self) -> Step:
        index = self.flow.submit_index()
        assert index is not None
        return self.flow.steps[index]

    async def submit(self) -> None:
        index = self.flow.submit_index()
        assert index is not None
        await self.run_step(index)

    async def submit_element(self) -> Locator:
        return await self._ready(self.submit_step())

    async def js_click_burst(self, delays_ms: list[int]) -> None:
        """Click the submit element at the given offsets, from inside the page, so the browser
        dispatches every click even while a navigation is pending (how real double-clicks behave)."""
        step = self.submit_step()
        loc = await self.submit_element()
        self.on_action(f"click {step.selector} x{len(delays_ms)} at {delays_ms} ms")
        try:
            if step.kind == "press":
                for _ in delays_ms:
                    assert step.key is not None
                    await loc.press(step.key, timeout=self.timeout_ms, no_wait_after=True)
                return
            await loc.evaluate(
                "(el, delays) => { for (const d of delays) setTimeout(() => el.click(), d); }", delays_ms
            )
        except PlaywrightError as exc:
            raise StepBlocked(f"submit burst interrupted: {exc.message.splitlines()[0]}") from None
        await self.page.wait_for_timeout(max(delays_ms) + 150)

    async def check_success(self) -> tuple[bool, str]:
        s = self.flow.success
        deadline_ms = self.timeout_ms
        try:
            if s.url_contains:
                await self.page.wait_for_url(f"**{s.url_contains}**", timeout=deadline_ms)
            if s.text_visible:
                await self.page.get_by_text(s.text_visible).first.wait_for(state="visible", timeout=deadline_ms)
            if s.selector_visible:
                await self.page.locator(s.selector_visible).first.wait_for(state="visible", timeout=deadline_ms)
        except PlaywrightTimeout:
            return False, f"success condition not met (url={self.page.url})"
        return True, "ok"
