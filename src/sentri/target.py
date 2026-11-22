"""The app under test, seen strictly from outside: a URL.

Sentri is a black-box tester. It never starts, stops, restarts or resets the app, never reads its source, files,
logs or databases. It only sends requests to `app.url` and drives a browser there, like a user. You start the app
(locally, in Docker, in CI, wherever) and hand Sentri its URL.

`AppTarget` keeps the small interface the runners use (`start`, `reset`, `stop`, `is_alive`): `start` only waits
until the URL answers its health check, `reset` and `stop` do nothing (a black box cannot be reset; attempts run
on the live state). The "log" stream the oracles read is made of what the app itself sent back: stack traces and
error text found in its HTTP responses (`record_response`), never its server logs.
"""

from __future__ import annotations

import asyncio
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime

from sentri.contract.models import AppConfig
from sentri.evidence.models import LogLine

# Lines that make a response worth keeping even when its status is < 400 (a trace printed into a 200 page).
_TRACE_HINT = re.compile(
    r"Traceback \(most recent call last\)|^\s*at .+\(.+:\d+:\d+\)$|Exception|Error:|\bpanic:|Fatal error|"
    r"Stack trace|\bFATAL\b|\bCRITICAL\b",
    re.MULTILINE,
)
MAX_LINES = 200_000


class TargetError(Exception):
    """The app could not be reached at its URL. Sentri does not start apps: start it, then run Sentri."""


def _status(url: str, timeout_s: float = 5) -> int | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


class AppTarget:
    def __init__(self, app: AppConfig):
        self.app = app
        self.url = app.url
        self._lines: list[LogLine] = []

    @property
    def health_url(self) -> str:
        return self.url + "/" + self.app.health_path.lstrip("/")

    async def start(self) -> None:
        """Wait until the app answers its health check below 500. Nothing is launched."""
        deadline = time.monotonic() + self.app.startup_timeout_s
        last: int | None = None
        while time.monotonic() < deadline:
            last = await asyncio.to_thread(_status, self.health_url)
            if last is not None and last < 500:
                return
            await asyncio.sleep(1.0)
        seen = f"HTTP {last}" if last is not None else "no answer"
        raise TargetError(
            f"the app does not answer at {self.health_url} ({seen} after {self.app.startup_timeout_s:g}s). "
            "Sentri tests a running app as a black box: start the app first, then run Sentri"
        )

    async def reset(self) -> None:
        """A black box cannot be reset; every attempt runs on the app's live state."""

    async def stop(self) -> None:
        """Sentri did not start the app, so it does not stop it."""

    def is_alive(self) -> bool:
        status = _status(self.health_url, timeout_s=3)
        return status is not None and status < 500

    # ---- what the app sent back (the oracles' "log" stream) ------------------------------------------
    def record_response(self, url: str, status: int | None, text: str | None) -> None:
        """Keep the body of an error response (>= 400), or of any response that prints a stack trace."""
        if not text:
            return
        if (status or 0) < 400 and not _TRACE_HINT.search(text):
            return
        ts = datetime.now(UTC).isoformat()
        for line in text.splitlines()[:400]:
            if line.strip():
                self._lines.append(LogLine(ts, "response", line[:2000]))
        if len(self._lines) > MAX_LINES:
            del self._lines[: len(self._lines) - MAX_LINES]

    def log_cursor(self) -> int:
        return len(self._lines)

    def logs_since(self, cursor: int) -> list[LogLine]:
        return self._lines[cursor:]

    def log_tail(self, n: int = 40) -> list[LogLine]:
        return self._lines[-n:]
