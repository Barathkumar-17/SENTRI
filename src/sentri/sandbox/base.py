"""Sandbox protocol and shared helpers: log buffer, port and health waiting.

Black-box rule: a sandbox may start, reset and stop the app (lifecycle). What decides a verdict is only what the
app shows from outside: HTTP, the browser, and error text it sends back in responses (`ResponseErrors`). Server
logs are still captured for the separately authorized, source-aware fixer, but no oracle reads them."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Literal, Protocol

from sentri.evidence.models import LogLine


class SandboxError(Exception):
    """The app could not be started, reset or reached. Never an app verdict."""


class LogBuffer:
    """Thread-safe, append-only log store addressed by a monotonically increasing cursor."""

    def __init__(self, max_lines: int = 200_000):
        self._lines: list[LogLine] = []
        self._offset = 0
        self._lock = threading.Lock()
        self._max = max_lines

    def append(self, text: str, stream: Literal["stdout", "stderr", "response"] = "stdout") -> None:
        line = LogLine(ts=datetime.now(UTC).isoformat(), stream=stream, text=text.rstrip("\r\n"))
        with self._lock:
            self._lines.append(line)
            if len(self._lines) > self._max:
                drop = len(self._lines) - self._max
                self._lines = self._lines[drop:]
                self._offset += drop

    def cursor(self) -> int:
        with self._lock:
            return self._offset + len(self._lines)

    def since(self, cursor: int) -> list[LogLine]:
        with self._lock:
            start = max(0, cursor - self._offset)
            return list(self._lines[start:])

    def tail(self, n: int = 40) -> list[LogLine]:
        with self._lock:
            return list(self._lines[-n:])


# A response is kept when it is an error (>= 400) or prints something trace-like into a normal page.
_TRACE_HINT = re.compile(
    r"Traceback \(most recent call last\)|^\s*at .+\(.+:\d+:\d+\)$|Exception|Error:|\bpanic:|Fatal error|"
    r"Stack trace|\bFATAL\b|\bCRITICAL\b",
    re.MULTILINE,
)


class ResponseErrors:
    """Error text the app itself sent back over HTTP: the black-box exception oracle's only input."""

    def _response_buffer(self) -> LogBuffer:
        buf = self.__dict__.get("_response_errors")
        if buf is None:
            buf = LogBuffer()
            self.__dict__["_response_errors"] = buf
        return buf

    def record_response(self, url: str, status: int | None, text: str | None) -> None:
        if not text or ((status or 0) < 400 and not _TRACE_HINT.search(text)):
            return
        buf = self._response_buffer()
        for line in text.splitlines()[:400]:
            if line.strip():
                buf.append(line[:2000], "response")

    def response_cursor(self) -> int:
        return self._response_buffer().cursor()

    def responses_since(self, cursor: int) -> list[LogLine]:
        return self._response_buffer().since(cursor)


class Sandbox(Protocol):
    base_url: str
    # How an attempt gets a clean state: "restart" (the app is restarted, `app.clean` files deleted; server-side
    # state outside those files, e.g. an external database, survives) or "none" (a URL Sentri did not start).
    reset_kind: str

    async def start(self) -> None: ...
    async def reset(self) -> None: ...
    async def stop(self) -> None: ...
    def is_alive(self) -> bool: ...
    def log_cursor(self) -> int: ...
    def logs_since(self, cursor: int) -> list[LogLine]: ...
    def log_tail(self, n: int = 40) -> list[LogLine]: ...
    def record_response(self, url: str, status: int | None, text: str | None) -> None: ...
    def response_cursor(self) -> int: ...
    def responses_since(self, cursor: int) -> list[LogLine]: ...


async def wait_for_port(host: str, port: int, deadline: float, alive: object = None) -> None:
    while time.monotonic() < deadline:
        if callable(alive) and not alive():
            raise SandboxError("app process exited before opening its port")
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=1)
            writer.close()
            await writer.wait_closed()
            return
        except (OSError, TimeoutError):
            await asyncio.sleep(0.2)
    raise SandboxError(f"port {host}:{port} did not open in time")


async def wait_for_port_closed(host: str, port: int, timeout_s: float = 10) -> bool:
    """True once nothing accepts connections on the port; False if still taken after the timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.5)
            writer.close()
            await writer.wait_closed()
            await asyncio.sleep(0.2)
        except (OSError, TimeoutError):
            return True
    return False


def _health_status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 - local sandbox URL
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


async def wait_for_health(url: str, deadline: float, alive: object = None) -> None:
    last: str = "no response"
    while time.monotonic() < deadline:
        if callable(alive) and not alive():
            raise SandboxError("app process exited during startup")
        try:
            status = await asyncio.to_thread(_health_status, url)
            if status < 500:
                return
            last = f"HTTP {status}"
        except OSError as exc:
            last = str(exc)
        await asyncio.sleep(0.3)
    raise SandboxError(f"health check {url} failed: {last}")
