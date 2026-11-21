"""Event feed. Every event is persisted to the `events` table (audit trail + SSE replay) and echoed to the console.

`message` is a sentence built by code. `reason` is the attacker's display-only text. They are never mixed.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from sentri.ledger.ledger import Ledger

log = logging.getLogger("sentri.feed")


class EventBus:
    def __init__(
        self, ledger: Ledger, run_id: int | None = None, echo: Callable[[str, str, str | None], None] | None = None
    ):
        self.ledger = ledger
        self.run_id = run_id
        self.echo = echo
        self._listeners: list[Callable[[int, str, dict[str, Any]], None]] = []

    def subscribe(self, fn: Callable[[int, str, dict[str, Any]], None]) -> None:
        self._listeners.append(fn)

    def emit(self, kind: str, message: str, reason: str | None = None, **data: Any) -> int:
        event_id = self.ledger.add_event(self.run_id, kind, message, reason, data)
        if self.echo is not None:
            self.echo(kind, message, reason)
        else:
            log.info("%s: %s%s", kind, message, f"  ({reason})" if reason else "")
        for fn in self._listeners:
            try:
                fn(event_id, kind, data)
            except Exception:  # noqa: BLE001 - a listener must never break a run
                log.exception("feed listener failed")
        return event_id
