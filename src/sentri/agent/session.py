"""Executes validated actions in the sandboxed app and records everything the oracles need.

One browser context per identity: `anon` (never logged in), `a` and `b` (two separate fresh accounts, created
by the contract's login flow). All requests and same-origin response bodies are recorded per identity, so
plain code can later decide whether a logged-out or other-user request saw data it should not have.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin, urlsplit

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
from sentri.evidence.agent_models import AgentEvidence, CountProbe, HealthProbe, IdorProbe, ResponseRecord
from sentri.evidence.models import ConsoleRecord, Evidence, HttpRecord
from sentri.redact import redact
from sentri.runner.browser import BrowserManager
from sentri.runner.fence import blocked_summary, set_identity_headers
from sentri.sandbox.base import Sandbox
from sentri.verdict.signature import normalize_path

TEXT_TYPES = ("text/", "application/json", "application/xml", "application/javascript")
MAX_BODY = 20_000
LoginFn = Callable[[Page, str], Awaitable[dict[str, str] | None]]


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
    location: str | None = None  # Location header of a redirect or 201 response

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
    action: Action | None = None  # what actually ran (with Sentri's canary added), for exact replay


@dataclass
class CreatedRecord:
    """Something an identity created, seen by plain code: the create request and where the record lives."""

    identity: str
    method: str
    endpoint: str  # normalized path of the create request
    record_id: str
    urls: list[str]  # same-origin paths that should show this record (probed for IDOR)


@dataclass
class Mark:
    requests: int
    console: int
    log_cursor: int
    idor: int = 0


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
        # Recon tells the session which paths need login. A canary is private only if its owner submitted it
        # from or to protected paths and never anywhere public.
        self.protected: set[str] = set()
        self.identity_headers: dict[str, dict[str, str]] = {}  # token auth: sent on every request of an identity
        self._submitted: dict[str, dict[str, bool]] = {"a": {}, "b": {}}  # who -> canary -> sent publicly?
        # Code-owned cross-user checks: records a/b created (found from their requests) and the direct-by-id
        # fetches run against them as owner, logged out and other user.
        self.created: list[CreatedRecord] = []
        self.idor_probes: list[IdorProbe] = []
        self._probed_urls: set[str] = set()
        self.auto_canary = True

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
        ctx = await self.browser.new_context(fence_origin=self.base_url)
        self.contexts[who] = ctx
        page = await ctx.new_page()
        self._attach(page, who)
        self.tabs[who] = [page]
        if who in ("a", "b"):
            if self.login_fn is None:
                raise ActionRefused("this contract has no login, so only the anon identity exists")
            headers = await self.login_fn(page, who)
            if headers:
                self.identity_headers[who] = headers
                await set_identity_headers(ctx, headers)
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
            self._note_submission(who, rec, page.url)

        def on_response(resp: Any) -> None:
            rec = self._pending.get(resp.request)
            if rec is None:
                return
            rec.status = resp.status
            rec.response_type = (resp.headers or {}).get("content-type")
            rec.location = (resp.headers or {}).get("location")
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

    def _is_protected(self, url: str) -> bool:
        return normalize_path(url) in self.protected

    def _note(self, who: str, text: str | None, page_url: str, target_url: str | None = None) -> None:
        canary = self.canaries.get(who)
        if canary is None or not text or canary not in text:
            return
        public = not (self._is_protected(page_url) or (target_url is not None and self._is_protected(target_url)))
        self._submitted[who][canary] = self._submitted[who].get(canary, False) or public

    def _note_submission(self, who: str, rec: ObservedRequest, page_url: str) -> None:
        # Bodies are not always visible (multipart uploads), so typing is tracked too; see _execute.
        self._note(who, rec.body, page_url, rec.url)

    def private_markers(self) -> dict[str, tuple[str, ...]]:
        return {who: tuple(c for c, public in sent.items() if not public) for who, sent in self._submitted.items()}

    def responses_since(self, mark: Mark) -> tuple[ResponseRecord, ...]:
        out = []
        for r in self.requests[mark.requests :]:
            if not self.same_origin(r.url) or r.status is None:
                continue
            path = "/" + r.url.split("://", 1)[-1].split("/", 1)[-1] if "/" in r.url.split("://", 1)[-1] else "/"
            out.append(ResponseRecord(r.identity, r.method, path.split("?")[0], r.status, r.response_text or ""))
        return tuple(out)

    def agent_evidence_since(
        self,
        mark: Mark,
        count_probes: tuple[CountProbe, ...] = (),
        health_probes: tuple[HealthProbe, ...] = (),
    ) -> AgentEvidence:
        return AgentEvidence(
            base=self.evidence_since(mark),
            responses=self.responses_since(mark),
            protected_paths=tuple(sorted(self.protected)),
            private_markers=self.private_markers(),
            count_probes=count_probes,
            health_probes=health_probes,
            idor_probes=tuple(self.idor_probes[mark.idor :]),
        )

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
        return Mark(len(self.requests), len(self.console), self.sandbox.log_cursor(), len(self.idor_probes))

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
        self._note_submission(self.current, rec, self._url() or url)
        headers = {**self.identity_headers.get(self.current, {}), **({"content-type": ctype} if ctype else {})}
        try:
            resp = await ctx.request.fetch(
                url, method=method, data=body, headers=headers, timeout=self.timeout_ms, max_redirects=0
            )
            rec.status = resp.status
            rec.response_type = resp.headers.get("content-type")
            rec.location = resp.headers.get("location")
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
            action = await self._with_canary(action)
            await self._execute(action)
        except ActionRefused as exc:
            return ActionResult(False, f"refused: {exc}", self._url(), [], action)
        except PlaywrightError as exc:
            new = [r.id for r in self.requests[before:]]
            await self.after_requests(before)
            return ActionResult(False, exc.message.splitlines()[0][:300], self._url(), new, action)
        await self.settle()
        await self.after_requests(before)
        left = await self._return_if_off_origin()
        if left is not None:
            return ActionResult(False, left, self._url(), [r.id for r in self.requests[before:]], action)
        return ActionResult(True, None, self._url(), [r.id for r in self.requests[before:]], action)

    # ---- code-owned canaries and cross-user checks ---------------------------------------------------
    async def _with_canary(self, action: Action) -> Action:
        """As a or b, content the agent writes carries that identity's canary, whether or not the model put it
        there: appended to free text typed into a text field, added to a JSON body sent as a raw request.
        Empty, huge or typed (email, number, password...) inputs are left alone so input abuse stays exact."""
        canary = self.canaries.get(self.current)
        if canary is None or not self.auto_canary:
            return action
        if isinstance(action, TypeText):
            if canary in action.text or not action.text.strip() or action.repeat != 1 or len(action.text) > 2000:
                return action
            kind = ""
            with contextlib.suppress(PlaywrightError):
                kind = await self.page.locator(action.selector).first.evaluate(_FIELD_KIND_JS, timeout=2000)
            if kind in ("textarea", "editable", "text", "search"):
                return action.model_copy(update={"text": f"{action.text} {canary}"})
            return action
        if isinstance(action, SendRequest) and action.method in ("POST", "PUT", "PATCH") and action.body:
            body = action.body.replace("{canary}", canary)
            if canary not in body:
                body = with_canary_in_json(body, canary) or body
            return action.model_copy(update={"body": body}) if body != action.body else action
        return action

    async def after_requests(self, before: int) -> None:
        """Find records the requests since `before` created, then fetch each one directly as the other user."""
        if self._bodies:
            await asyncio.wait(list(self._bodies), timeout=3)
        for rec in self.requests[before:]:
            found = created_record(rec, self.canaries.get(rec.identity), self.base_url)
            if found is not None and all(c.urls != found.urls for c in self.created):
                self.created.append(found)
                await self._idor_probe(found)

    async def _probe_get(self, who: str, path: str) -> tuple[int | None, str]:
        try:
            ctx = await self._context(who)
            resp = await ctx.request.get(
                self.base_url + path,
                headers=self.identity_headers.get(who, {}),
                max_redirects=0,
                timeout=self.timeout_ms,
            )
            return resp.status, redact((await resp.text())[:MAX_BODY])
        except (PlaywrightError, ActionRefused):
            return None, ""

    async def _idor_probe(self, rec: CreatedRecord) -> None:
        canary = self.canaries.get(rec.identity)
        if self.login_fn is None or canary is None:
            return
        other = "b" if rec.identity == "a" else "a"
        for path in rec.urls[:3]:
            if path in self._probed_urls:
                continue
            self._probed_urls.add(path)
            owner_status, owner_text = await self._probe_get(rec.identity, path)
            if canary not in owner_text:
                continue  # not the record's page; nothing to compare
            anon_status, anon_text = await self._probe_get("anon", path)
            other_status, other_text = await self._probe_get(other, path)
            self.idor_probes.append(
                IdorProbe(
                    rec.identity,
                    other,
                    path,
                    owner_status,
                    True,
                    anon_status,
                    canary in anon_text,
                    other_status,
                    canary in other_text,
                )
            )

    async def _return_if_off_origin(self) -> str | None:
        """A redirect (not seen by request routing) can land the tab on another origin. Nothing of the app's goes
        with it (cookies are origin-scoped, tokens are fenced), but Sentri must not keep acting there."""
        url = self._url() or ""
        if not url.startswith(("http://", "https://")) or self.same_origin(url):
            return None
        with contextlib.suppress(PlaywrightError):
            await self.page.goto(self.base_url + "/", wait_until="load", timeout=self.timeout_ms)
        return f"refused: the page left the app (landed on {url[:120]}); returned to the app"

    def _blocked(self) -> list[dict[str, Any]]:
        ctx = self.contexts.get(self.current)
        return blocked_summary(ctx, 5) if ctx is not None else []

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
            self._note(self.current, action.text, page.url)  # where the canary was entered decides privacy
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
            if self.identity_headers.pop(self.current, None) is not None:
                await set_identity_headers(self.contexts[self.current], {})  # drop the token too
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
        from sentri.runner.extract import EXTRACT_JS

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
            "blocked_offsite": [f"{b['how']} {b['method']} {b['url'][:120]}" for b in self._blocked()],
            "canary_for_this_identity": self.canaries.get(self.current),
            "created_records": [
                f"{c.identity}: {c.method} {c.endpoint} -> id {c.record_id}" for c in self.created[-5:]
            ],
        }


# ---- plain-code helpers (no browser) ---------------------------------------------------------------------
_FIELD_KIND_JS = (
    "el => el.tagName === 'TEXTAREA' ? 'textarea' : (el.isContentEditable ? 'editable' :"
    " (el.tagName === 'INPUT' ? (el.getAttribute('type') || 'text').toLowerCase() : ''))"
)
_CONTENT_KEYS = ("title", "name", "text", "content", "description", "comment", "note", "body", "message", "subject")
_ID_KEYS = ("id", "_id", "uuid", "key", "slug")
_ID_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_ID_SEGMENT = re.compile(r"^(\d+|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{24})$", re.I)


def with_canary_in_json(body: str, canary: str) -> str | None:
    """Append the canary to the first content-like string field of a JSON object body."""
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    for key in _CONTENT_KEYS:
        for k, v in data.items():
            if k.lower() == key and isinstance(v, str) and v.strip():
                data[k] = f"{v} {canary}"
                return json.dumps(data)
    return None


def _find_id(data: Any, canary: str, depth: int = 0) -> str | None:
    """The id of the object holding the canary (or of the top-level / `data` object of a create response)."""
    if depth > 4:
        return None
    if isinstance(data, dict):
        ident = next((str(data[k]) for k in _ID_KEYS if isinstance(data.get(k), (int, str)) and str(data[k])), None)
        if ident is not None and _ID_VALUE.match(ident) and (depth == 0 or canary in json.dumps(data)):
            return ident
        for v in data.values():
            if isinstance(v, (dict, list)) and canary in json.dumps(v):
                found = _find_id(v, canary, depth + 1)
                if found:
                    return found
        inner = data.get("data")
        if isinstance(inner, dict):
            return _find_id(inner, canary, depth + 1)
    if isinstance(data, list):
        for item in data:
            if canary in json.dumps(item):
                return _find_id(item, canary, depth + 1)
    return None


def _path_only(url: str, base_url: str) -> str | None:
    full = urljoin(base_url + "/", url)
    if origin_of(full) != origin_of(base_url):
        return None
    return urlsplit(full).path or "/"


def created_record(rec: ObservedRequest, canary: str | None, base_url: str) -> CreatedRecord | None:
    """A successful POST/PUT whose body carried the identity's canary is a create. Find the record's id and the
    URLs that should show it: the Location it points to, `<collection>/<id>`, and `/x/<id>` for `/x/new`-style
    form actions."""
    if canary is None or rec.method not in ("POST", "PUT") or rec.status is None or rec.status >= 400:
        return None
    if canary not in (rec.body or ""):
        return None
    path = _path_only(rec.url, base_url)
    if path is None:
        return None
    record_id = None
    urls: list[str] = []
    if rec.location:
        loc = _path_only(rec.location, base_url)
        last = (loc or "").rstrip("/").rsplit("/", 1)[-1]
        if loc and _ID_SEGMENT.match(last):
            record_id = last
            urls.append(loc)
    if record_id is None and rec.response_text and "json" in (rec.response_type or ""):
        with contextlib.suppress(ValueError):
            record_id = _find_id(json.loads(rec.response_text), canary)
    if record_id is None:
        return None
    collection = path.rstrip("/")
    for suffix in ("/new", "/create", "/add"):
        if collection.endswith(suffix):
            collection = collection[: -len(suffix)]
    for candidate in (f"{collection}/{record_id}", f"{path.rstrip('/')}/{record_id}"):
        if candidate not in urls:
            urls.append(candidate)
    return CreatedRecord(rec.identity, rec.method, normalize_path(path), record_id, urls)
