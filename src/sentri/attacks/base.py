"""Attack protocol, payload slots, registry and static payloads."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

import yaml
from playwright.async_api import BrowserContext, Page

from sentri.contract.models import Flow
from sentri.runner.flow import FlowExecutor

Payload = dict[str, str]


@dataclass(frozen=True)
class Applicability:
    applies: bool
    reason: str = ""


APPLIES = Applicability(True)


@dataclass(frozen=True)
class PayloadSlot:
    name: str
    kind: str  # key into static.yaml for this attack
    description: str
    choices: tuple[str, ...] | None = None  # when set, only these values are valid


@dataclass
class AttackContext:
    page: Page
    context: BrowserContext
    executor: FlowExecutor
    timeout_ms: float
    tmp_dir: Path
    bad_file_mb: int = 50
    cdp: Any = None
    emit: Callable[[str], None] = field(default=lambda _t: None)


@cache
def static_payloads() -> dict[str, dict[str, list[str]]]:
    text = (resources.files("sentri.attacks") / "payloads" / "static.yaml").read_text(encoding="utf-8")
    data: dict[str, dict[str, list[str]]] = yaml.safe_load(text)
    return data


class Attack:
    id: ClassVar[str]
    description: ClassVar[str]

    def applies_to(self, flow: Flow) -> Applicability:
        return APPLIES

    def payload_slots(self, flow: Flow) -> list[PayloadSlot]:
        return []

    def validate(self, slot: PayloadSlot, value: Any) -> bool:
        if not isinstance(value, str):
            return False
        if slot.choices is not None:
            return value in slot.choices
        return len(value) <= 255 and "\x00" not in value

    def static_value(self, slot: PayloadSlot, rotation: int) -> str:
        if slot.choices:
            pool = [c for c in static_payloads().get(self.id, {}).get(slot.kind, []) if c in slot.choices]
            pool = pool or list(slot.choices)
        else:
            pool = static_payloads().get(self.id, {}).get(slot.kind, [""])
        return pool[rotation % len(pool)]

    def static_payload(self, flow: Flow, rotation: int) -> Payload:
        return {slot.name: self.static_value(slot, rotation) for slot in self.payload_slots(flow)}

    def finalize(self, flow: Flow, payload: Payload) -> Payload:
        """Hook to enforce cross-slot rules after validation."""
        return payload

    async def execute(self, ctx: AttackContext, payload: Payload) -> None:
        raise NotImplementedError
