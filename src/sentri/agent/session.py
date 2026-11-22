"""Executes validated actions in the sandboxed app and records everything the oracles need.

One browser context per identity: `anon` (never logged in), `a` and `b` (two separate fresh accounts, created
by the contract's login flow). All requests and same-origin response bodies are recorded per identity, so
plain code can later decide whether a logged-out or other-user request saw data it should not have.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from playwright.async_api import BrowserContext, Page
from playwright.async_api import Error as PlaywrightError

from sentri.agent.actions import (
    Action,
    ActionRefused,
    Back,
    Burst,
    ClearSession,
    Click,
    Finish,
    Navigate,
    NewTab,
    Refresh,
    Replay,
    SendRequest,
    SwitchTab,
    SwitchUser,
    Throttle,
    TypeText,
    Upload,
    Wait,
    origin_of,
    resolve_same_origin,
)
from sentri.attacks.types import NO_THROTTLE, SLOW_3G, BadFile
from sentri.evidence.models import ConsoleRecord, Evidence, HttpRecord
from sentri.redact import redact
from sentri.runner.browser import BrowserManager
from sentri.sandbox.base import Sandbox

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript")
MAX_BODY = 20_000
LoginFn = Callable[[Page, str], Awaitable[None]]


@dataclass
class ObservedRequest:
    id: int
    identity: str
    method: str
    url: str
    status: int | None = None
    resource_type: str = "other"
    content_type: str | None = None
    body: str | None = None
    response_type: str | None = None
    response_text: str | None = None
    started_ms: float = 0.0
    duration_ms: float = 0.0
    failed: bool = False
    failure: str | None = None
    via: str = "browser"  # browser | request | replay

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "as": self.identity,
            "method": self.method,
            "path": self.url.split("://", 1)[-1].split("/", 1)[-1][:200],
            "status": self.status,
            "type": self.resource_type,
            "via": self.via,
        }


@dataclass
class ActionResult:
    ok: bool
    error: str | None = None
    url: str | None = None
    new_requests: list[int] = field(default_factory=list)


@dataclass
class Mark:
    requests: int
    console: int
    log_cursor: int


class AgentSession:
    def __init__(
        self,
        browser: BrowserManager,
        sandbox: Sandbox,
        base_url: str,
        login: LoginFn | None,
        tmp_dir: Path,
        timeout_ms: float = 10_000,
    ):
        self.browser = browser
        self.sandbox = sandbox
        self.base_url = base_url.rstrip("/")
        self.origin = origin_of(self.base_url)
        self.login_fn = login
        self.tmp_dir = tmp_dir
        self.timeout_ms = timeout_ms
        self.contexts: dict[str, BrowserContext] = {}
        self.tabs: dict[str, list[Page]] = {}
        self.current = "anon"
        self.tab_index = 0
        self.requests: list[ObservedRequest] = []
        self.console: list[ConsoleRecord] = []
        self.crashed = False
        self._pending: dict[Any, ObservedRequest] = {}
        self._bodies: set[asyncio.Task[None]] = set()
        self._t0 = time.monotonic()
        self._cdp: dict[str, Any] = {}
        # Unique strings each identity is asked to put into content it creates. Seeing one identity's canary
        # in another identity's responses is plain-code proof of cross-user exposure.
        run_salt = secrets.token_hex(3)
        self.canaries = {who: f"sentri-canary-{who}-{run_salt}" for who in ("a", "b")}

    # ---- lifecycle ----------------------------------------------------------------------------------
    async def start(self) -> None:
        await self._context("anon")

    async def close(self) -> None:
        for ctx in self.contexts.values():
            with contextlib.suppress(PlaywrightError):
                await ctx.close()
        self.contexts.clear()
        self.tabs.clear()

    @property
    def page(self) -> Page:
        return self.tabs[self.current][self.tab_index]

    def same_origin(self, url: str) -> bool:
        return origin_of(url) == self.origin

    async def _context(self, who: str) -> BrowserContext:
        if who in self.contexts:
            return self.contexts[who]
        ctx = await self.browser.new_context()
        self.contexts[who] = ctx
        page = await ctx.new_page()
        self._attach(page, who)
        self.tabs[who] = [page]
        if who in ("a", "b"):
            if self.login_fn is None:
                raise ActionRefused("this contract has no login flow, so only the anon identity exists")
            await self.login_fn(page, who)
        else:
            await page.goto(self.base_url + "/", wait_until="load", timeout=self.timeout_ms)
        return ctx

    # ---- recording ----------------------------------------------------------------------------------
    def _now(self) -> float:
        return (time.monotonic() - self._t0) * 1000

    def _attach(self, page: Page, who: str) -> None:
        def on_request(req: Any) -> None:
            body = None
            with contextlib.suppress(Exception):
                buf = req.post_data_buffer
                if buf:
                    body = buf[:MAX_BODY].decode("utf-8", errors="replace")
            rec = ObservedRequest(
                id=len(self.requests) + 1,
                identity=who,
                method=req.method,
                url=req.url,
                resource_type=req.resource_type,
                content_type=(req.headers or {}).get("content-type"),
                body=redact(body) if body else None,
                started_ms=self._now(),
            )
            self.requests.append(rec)
            self._pending[req] = rec

        def on_response(resp: Any) -> None:
            rec = self._pending.get(resp.request)
            if rec is None:
                return
            rec.status = resp.status
            rec.response_type = (resp.headers or {}).get("content-type")
            if self.same_origin(rec.url) and any(t in (rec.response_type or "") for t in TEXT_TYPES):
                task = asyncio.ensure_future(self._read_body(resp, rec))
                self._bodies.add(task)
                task.add_done_callback(self._bodies.discard)

        def on_finished(req: Any) -> None:
            rec = self._pending.pop(req, None)
            if rec is not None:
                rec.duration_ms = self._now() - rec.started_ms

        def on_failed(req: Any) -> None:
            rec = self._pending.pop(req, None)
            if rec is not None:
                rec.failed, rec.failure = True, req.failure or "failed"

        page.on("request", on_request)
        page.on("response", on_response)
        page.on("requestfinished", on_finished)
        page.on("requestfailed", on_failed)
        page.on(
            "console",
            lambda m: (
                self.console.append(ConsoleRecord("console_error", m.text, (m.location or {}).get("url")))
                if m.type == "error"
                else None
            ),
        )
        page.on("pageerror", lambda e: self.console.append(ConsoleRecord("page_error", getattr(e, "message", str(e)))))
        page.on("crash", lambda _p: setattr(self, "crashed", True))

    async def _read_body(self, resp: Any, rec: ObservedRequest) -> None:
        with contextlib.suppress(Exception):
            rec.response_text = redact((await resp.text())[:MAX_BODY])

    def in_flight(self) -> list[ObservedRequest]:
        return [r for r in self._pending.values() if r.status is None and not r.failed and self.same_origin(r.url)]

    async def settle(self, idle_ms: int = 800) -> None:
        """Wait for the action's effects: a moment for scripts to start requests, the page load, then until no
        same-origin request is in flight (capped by the action timeout), then for recorded bodies."""
        await asyncio.sleep(0.15)
        with contextlib.suppress(PlaywrightError):
            await self.page.wait_for_load_state("load", timeout=self.timeout_ms)
        for _ in range(int(self.timeout_ms / 50)):  # poll every 50 ms, capped by the action timeout
            if not self.in_flight():
                break
            await asyncio.sleep(0.05)
        with contextlib.suppress(PlaywrightError):
            await self.page.wait_for_load_state("networkidle", timeout=idle_ms)
        if self._bodies:
            await asyncio.wait(list(self._bodies), timeout=3)

    def mark(self) -> Mark:
        return Mark(len(self.requests), len(self.console), self.sandbox.log_cursor())

    def evidence_since(self, mark: Mark) -> Evidence:
        """Step evidence in the format the existing oracles read."""
        reqs = self.requests[mark.requests :]
        http = tuple(
            HttpRecord(
                url=r.url,
                method=r.method,
                status=r.status,
                duration_ms=round(r.duration_ms, 1),
                started_ms=round(r.started_ms, 1),
                request_body_sha256=hashlib.sha256(r.body.encode()).hexdigest() if r.body else None,
                same_origin=self.same_origin(r.url),
                failed=r.failed,
                failure=r.failure,
                resource_type=r.resource_type,
            )
            for r in reqs
        )
        return Evidence(
            http=http,
            console=tuple(self.console[mark.console :]),
            logs=tuple(self.sandbox.logs_since(mark.log_cursor)),
            page_crashed=self.crashed,
        )

    # ---- actions ------------------------------------------------------------------------------------
    async def _api_request(self, method: str, url: str, body: str | None, ctype: str | None, via: str) -> int:
        ctx = await self._context(self.current)
        rec = ObservedRequest(
            id=len(self.requests) + 1,
            identity=self.current,
            method=method,
            url=url,
            resource_type="fetch",
            content_type=ctype,
            body=redact(body) if body else None,
            started_ms=self._now(),
            via=via,
        )
        self.requests.append(rec)
        headers = {"content-type": ctype} if ctype else {}
        try:
            resp = await ctx.request.fetch(
                url, method=method, data=body, headers=headers, timeout=self.timeout_ms, max_redirects=0
            )
            rec.status = resp.status
            rec.response_type = resp.headers.get("content-type")
            if any(t in (rec.response_type or "") for t in TEXT_TYPES):
                rec.response_text = redact((await resp.text())[:MAX_BODY])
        except PlaywrightError as exc:
            rec.failed, rec.failure = True, exc.message.splitlines()[0]
        rec.duration_ms = self._now() - rec.started_ms
        return rec.id

    def _observed(self, request_id: int) -> ObservedRequest:
        for r in self.requests:
            if r.id == request_id:
                return r
        raise ActionRefused(f"no observed request #{request_id}")

    async def execute(self, action: Action) -> ActionResult:
        before = len(self.requests)
        try:
            await self._execute(action)
        except ActionRefused as exc:
            return ActionResult(False, f"refused: {exc}", self._url(), [])
        except PlaywrightError as exc:
            return ActionResult(
                False, exc.message.splitlines()[0][:300], self._url(), [r.id for r in self.requests[before:]]
            )
        await self.settle()
        return ActionResult(True, None, self._url(), [r.id for r in self.requests[before:]])

    def _url(self) -> str | None:
        with contextlib.suppress(Exception):
            return self.page.url
        return None

    async def _execute(self, action: Action) -> None:
        t = self.timeout_ms
        if isinstance(action, SwitchUser):
            await self._context(action.user)
            self.current, self.tab_index = action.user, 0
            return
        await self._context(self.current)
        page = self.page
        if isinstance(action, Click):
            await page.locator(action.selector).first.click(timeout=t)
        elif isinstance(action, TypeText):
            field_ = page.locator(action.selector).first
            await field_.fill(action.text * action.repeat, timeout=t)
            if action.submit:
                await field_.press("Enter", timeout=t)
        elif isinstance(action, Navigate):
            await page.goto(resolve_same_origin(self.base_url, action.path), wait_until="load", timeout=t)
        elif isinstance(action, Upload):
            file = BadFile().build_file(action.shape, action.filename or "", ".png", action.size_mb)
            if action.shape == "valid":
                file["name"] = action.filename or file["name"]
            await page.locator(action.selector).first.set_input_files(cast(Any, file), timeout=t)
        elif isinstance(action, SendRequest):
            method, path, body, ctype = action.method, action.path, action.body, action.content_type
            if action.from_request is not None:
                src = self._observed(action.from_request)
                path = path or src.url
                body = body if body is not None else src.body
                ctype = ctype or src.content_type
            await self._api_request(method, resolve_same_origin(self.base_url, path or "/"), body, ctype, "request")
        elif isinstance(action, Replay):
            src = self._observed(action.request_id)
            url = resolve_same_origin(self.base_url, src.url)
            calls = [
                self._api_request(src.method, url, src.body, src.content_type, "replay") for _ in range(action.times)
            ]
            if action.concurrent:
                await asyncio.gather(*calls)
            else:
                for call in calls:
                    await call
        elif isinstance(action, Burst):
            loc = page.locator(action.selector).first
            await loc.wait_for(state="visible", timeout=t)
            delays = [i * action.interval_ms for i in range(action.times)]
            await loc.evaluate("(el, ds) => { for (const d of ds) setTimeout(() => el.click(), d); }", delays)
            await page.wait_for_timeout(max(delays) + 200)
        elif isinstance(action, NewTab):
            new = await self.contexts[self.current].new_page()
            self._attach(new, self.current)
            self.tabs[self.current].append(new)
            self.tab_index = len(self.tabs[self.current]) - 1
            await new.goto(resolve_same_origin(self.base_url, action.path), wait_until="load", timeout=t)
        elif isinstance(action, SwitchTab):
            if action.index >= len(self.tabs[self.current]):
                raise ActionRefused(f"tab {action.index} does not exist")
            self.tab_index = action.index
        elif isinstance(action, Back):
            await page.go_back(wait_until="load", timeout=t)
        elif isinstance(action, Refresh):
            await page.reload(wait_until="load", timeout=t)
        elif isinstance(action, ClearSession):
            await self.contexts[self.current].clear_cookies()
            await page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
        elif isinstance(action, Throttle):
            cdp = self._cdp.get(self.current) or await self.contexts[self.current].new_cdp_session(page)
            self._cdp[self.current] = cdp
            await cdp.send("Network.enable")
            await cdp.send("Network.emulateNetworkConditions", SLOW_3G if action.on else NO_THROTTLE)
        elif isinstance(action, Wait):
            await page.wait_for_timeout(action.ms)
        elif isinstance(action, Finish):
            return

    async def execute_switch(self, who: str) -> None:
        """Become `who` (creating and logging in its context on first use)."""
        await self._context(who)
        self.current, self.tab_index = who, 0

    # ---- observation for the model -------------------------------------------------------------------
    async def observe(self, max_text: int = 2500) -> dict[str, Any]:
        page = self.page
        text = ""
        with contextlib.suppress(PlaywrightError):
            text = await page.locator("body").inner_text(timeout=2000)
        from sentri.planner.crawler import EXTRACT_JS

        structure: dict[str, Any] = {}
        with contextlib.suppress(PlaywrightError):
            structure = await page.evaluate(EXTRACT_JS)
        links = []
        for href in structure.get("links", [])[:200]:
            with contextlib.suppress(ActionRefused):
                url = resolve_same_origin(page.url or self.base_url, href)
                path = "/" + url.split("://", 1)[-1].split("/", 1)[-1]
                if path not in links:
                    links.append(path)
        return {
            "identity": self.current,
            "tab": self.tab_index,
            "tabs": len(self.tabs.get(self.current, [])),
            "url": page.url,
            "title": structure.get("title", ""),
            "text": redact(text[:max_text]),
            "forms": structure.get("forms", [])[:8],
            "buttons": structure.get("loose_buttons", [])[:15],
            "links": links[:40],
            "recent_requests": [r.summary() for r in self.requests[-12:]],
            "console_errors": [c.text[:200] for c in self.console[-5:]],
            "canary_for_this_identity": self.canaries.get(self.current),
        }
