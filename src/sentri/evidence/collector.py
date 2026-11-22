"""Collects evidence from Playwright page events inside an attack window. Verdicts read only what the app
shows from outside; error text it sends back in responses goes to the sandbox's ResponseErrors. Server logs are
recorded for the source-aware fixer only."""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sentri.evidence.models import ConsoleRecord, DuplicateMethod, Evidence, HttpRecord
from sentri.sandbox.base import Sandbox

_LOCAL = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in _LOCAL:
        host = "localhost"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, host, port


@dataclass
class _Pending:
    url: str
    method: str
    started: float
    body_sha: str | None
    resource_type: str
    status: int | None = None


class EvidenceCollector:
    def __init__(self, sandbox: Sandbox, base_url: str):
        self.sandbox = sandbox
        self._bodies: set[asyncio.Task[None]] = set()
        self.origin = _origin(base_url)
        self.active = False
        self._t0 = time.monotonic()
        self._log_cursor = 0
        self._response_cursor = 0
        self._pending: dict[Any, _Pending] = {}
        self._http: list[HttpRecord] = []
        self._console: list[ConsoleRecord] = []
        self._actions: list[str] = []
        self.timed_out = False
        self.timeout_detail: str | None = None
        self.page_crashed = False
        self.step_blocked: str | None = None

    # ---- wiring -------------------------------------------------------------------------------------
    def attach(self, page: Any) -> None:
        page.on("request", self._on_request)
        page.on("response", self._on_response)
        page.on("requestfinished", self._on_finished)
        page.on("requestfailed", self._on_failed)
        page.on("console", self._on_console)
        page.on("pageerror", self._on_pageerror)
        page.on("crash", self._on_crash)

    def same_origin(self, url: str) -> bool:
        return _origin(url) == self.origin

    def _now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000

    def _on_request(self, request: Any) -> None:
        if not self.active:
            return
        body = None
        try:
            buf = request.post_data_buffer
            if buf:
                body = hashlib.sha256(buf).hexdigest()
        except Exception:  # noqa: BLE001 - body may be unavailable for some requests
            body = None
        self._pending[request] = _Pending(request.url, request.method, self._now_ms(), body, request.resource_type)

    def _on_response(self, response: Any) -> None:
        pending = self._pending.get(response.request)
        if pending is not None:
            pending.status = response.status
        if self.active and self.same_origin(response.url):
            task = asyncio.ensure_future(self._read_body(response))
            self._bodies.add(task)
            task.add_done_callback(self._bodies.discard)

    async def _read_body(self, response: Any) -> None:
        ctype = ((response.headers or {}).get("content-type") or "").lower()
        if not any(t in ctype for t in ("text/", "json", "xml")):
            return
        try:
            text = (await response.text())[:20000]
        except Exception:  # noqa: BLE001 - body gone (navigation, closed page): nothing to read
            return
        self.sandbox.record_response(response.url, response.status, text)

    async def drain(self) -> None:
        """Wait for response bodies still being read (they feed the exception oracle)."""
        if self._bodies:
            await asyncio.wait(list(self._bodies), timeout=3)

    def _finish(self, request: Any, failed: bool, failure: str | None) -> None:
        pending = self._pending.pop(request, None)
        if pending is None:
            return
        self._http.append(
            HttpRecord(
                url=pending.url,
                method=pending.method,
                status=pending.status,
                duration_ms=round(self._now_ms() - pending.started, 1),
                started_ms=round(pending.started, 1),
                request_body_sha256=pending.body_sha,
                same_origin=self.same_origin(pending.url),
                failed=failed,
                failure=failure,
                resource_type=pending.resource_type,
            )
        )

    def _on_finished(self, request: Any) -> None:
        self._finish(request, False, None)

    def _on_failed(self, request: Any) -> None:
        self._finish(request, True, request.failure or "failed")

    def _on_console(self, msg: Any) -> None:
        if self.active and msg.type == "error":
            location = msg.location or {}
            self._console.append(ConsoleRecord("console_error", msg.text, location.get("url")))

    def _on_pageerror(self, error: Any) -> None:
        if self.active:
            text = getattr(error, "message", None) or str(error)
            self._console.append(ConsoleRecord("page_error", text, None))

    def _on_crash(self, _page: Any) -> None:
        if self.active:
            self.page_crashed = True

    # ---- window -------------------------------------------------------------------------------------
    def open_window(self) -> None:
        self._pending.clear()
        self._http.clear()
        self._console.clear()
        self._actions.clear()
        self.timed_out = False
        self.timeout_detail = None
        self.page_crashed = False
        self.step_blocked = None
        self._t0 = time.monotonic()
        self._log_cursor = self.sandbox.log_cursor()
        self._response_cursor = self.sandbox.response_cursor()
        self.active = True

    def action(self, text: str) -> None:
        self._actions.append(text)

    def mark_timeout(self, detail: str) -> None:
        if not self.timed_out:
            self.timed_out = True
            self.timeout_detail = detail

    def in_flight(self) -> list[_Pending]:
        return [p for p in self._pending.values() if self.same_origin(p.url) and p.status is None]

    async def wait_for_quiet(self, hang_timeout_s: float) -> None:
        """Wait until no same-origin request is in flight; one older than the limit counts as a hang."""
        while True:
            pending = self.in_flight()
            if not pending:
                return
            oldest = min(pending, key=lambda p: p.started)
            age_s = (self._now_ms() - oldest.started) / 1000
            if age_s > hang_timeout_s:
                path = urlsplit(oldest.url).path
                self.mark_timeout(f"no response within {hang_timeout_s:g}s: {oldest.method} {path}")
                return
            await asyncio.sleep(0.1)

    def close_window(self) -> None:
        self.active = False
        # Requests that got a status but whose body never completed are still recorded.
        for request, pending in list(self._pending.items()):
            if pending.status is not None:
                self._finish(request, False, None)

    def build(
        self,
        *,
        list_count_before: int | None = None,
        list_count_after: int | None = None,
        duplicate_method: DuplicateMethod = "none",
        page_text_rows: tuple[str, ...] = (),
        unique_values: tuple[str, ...] = (),
        expected_creates: int | None = None,
        screenshot_path: str | None = None,
    ) -> Evidence:
        logs = tuple(self.sandbox.logs_since(self._log_cursor))
        return Evidence(
            http=tuple(sorted(self._http, key=lambda r: r.started_ms)),
            console=tuple(self._console),
            logs=logs,
            response_errors=tuple(self.sandbox.responses_since(self._response_cursor)),
            list_count_before=list_count_before,
            list_count_after=list_count_after,
            duplicate_method=duplicate_method,
            page_text_rows=page_text_rows,
            unique_values=unique_values,
            timed_out=self.timed_out,
            timeout_detail=self.timeout_detail,
            page_crashed=self.page_crashed,
            step_blocked=self.step_blocked,
            expected_creates=expected_creates,
            screenshot_path=screenshot_path,
            actions=tuple(self._actions),
        )
