"""URL mode: an app Sentri did not start and never touches. `app.base_url` only, no `run` or `docker`.

`start` waits until the URL answers its health check, `reset` and `stop` do nothing: Sentri has no way to give an
attempt a clean server-side state here, so replays run on the live state and reports say so (`reset_kind`).
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request

from sentri.contract.models import AppConfig
from sentri.evidence.models import LogLine
from sentri.sandbox.base import ResponseErrors, SandboxError


def _status(url: str, timeout_s: float = 5) -> int | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout_s) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (urllib.error.URLError, OSError, ValueError):
        return None


class ExternalSandbox(ResponseErrors):
    reset_kind = "none"

    def __init__(self, app: AppConfig):
        self.app = app
        self.base_url = app.url

    @property
    def health_url(self) -> str:
        return self.base_url + "/" + self.app.health_path.lstrip("/")

    async def start(self) -> None:
        deadline = time.monotonic() + self.app.startup_timeout_s
        last: int | None = None
        while time.monotonic() < deadline:
            last = await asyncio.to_thread(_status, self.health_url)
            if last is not None and last < 500:
                return
            await asyncio.sleep(1.0)
        seen = f"HTTP {last}" if last is not None else "no answer"
        raise SandboxError(
            f"the app does not answer at {self.health_url} ({seen} after {self.app.startup_timeout_s:g}s); "
            "in URL mode Sentri does not start the app: start it first"
        )

    async def reset(self) -> None:
        """No reset available for an app Sentri did not start."""

    async def stop(self) -> None:
        """Sentri did not start the app, so it does not stop it."""

    def is_alive(self) -> bool:
        status = _status(self.health_url, timeout_s=3)
        return status is not None and status < 500

    def log_cursor(self) -> int:
        return 0

    def logs_since(self, cursor: int) -> list[LogLine]:
        return []

    def log_tail(self, n: int = 40) -> list[LogLine]:
        return []
