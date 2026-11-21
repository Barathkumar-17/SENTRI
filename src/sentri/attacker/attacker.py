"""Chooses payloads for each check: model proposals validated slot by slot, static fallback otherwise."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sentri.attacks.base import Attack, Payload
from sentri.contract.models import Flow
from sentri.llm import BudgetedProvider, PayloadSet, ProbeRequest, load_prompt

log = logging.getLogger(__name__)


@dataclass
class PayloadChoice:
    payload: Payload
    source: str  # llm | static | none
    reason: str
    llm_error: str | None = None


def _slot_view(attack: Attack, flow: Flow) -> list[dict[str, Any]]:
    return [
        {"slot": s.name, "description": s.description, **({"allowed": list(s.choices)} if s.choices else {})}
        for s in attack.payload_slots(flow)
    ]


def _flow_view(flow: Flow) -> dict[str, Any]:
    return {
        "id": flow.id,
        "description": flow.description,
        "start": flow.start,
        "steps": [s.to_yaml() for s in flow.steps],
    }


class Attacker:
    def __init__(self, provider: BudgetedProvider):
        self.provider = provider

    def _static(self, attack: Attack, flow: Flow, rotation: int, why: str) -> PayloadChoice:
        payload = attack.finalize(flow, attack.static_payload(flow, rotation))
        return PayloadChoice(payload, "static", why)

    async def choose(self, attack: Attack, flow: Flow, history: list[dict[str, Any]], rotation: int) -> PayloadChoice:
        slots = attack.payload_slots(flow)
        if not slots:
            return PayloadChoice({}, "none", f"{attack.id}: timing attack, no payload to choose")
        if not self.provider.available:
            return self._static(attack, flow, rotation, "static payload (model unavailable)")

        request = {
            "attack": {"id": attack.id, "what_is_done": attack.description},
            "flow": _flow_view(flow),
            "slots": _slot_view(attack, flow),
            "recent_history": history,
        }
        result = await self.provider.complete_json(
            "attacker", load_prompt("attacker"), json.dumps(request, indent=1), PayloadSet, max_tokens=2000
        )
        if not result.ok or result.value is None:
            choice = self._static(attack, flow, rotation, f"static payload (model: {result.error})")
            choice.llm_error = result.error
            return choice

        proposed = {sv.slot: sv.value for sv in result.value.values}
        static = attack.static_payload(flow, rotation)
        payload: Payload = {}
        fell_back = False
        for slot in slots:
            value = proposed.get(slot.name)
            if value is not None and attack.validate(slot, value):
                payload[slot.name] = value
            else:
                payload[slot.name] = static[slot.name]
                fell_back = True
        payload = attack.finalize(flow, payload)
        reason = (result.value.reason or "").strip()[:120] or "model-chosen payload"
        return PayloadChoice(payload, "static" if fell_back else "llm", reason)


class Prober:
    """Proposes at most `cap` follow-up probes per flow, each one of the fixed attack types."""

    def __init__(self, provider: BudgetedProvider, cap: int):
        self.provider = provider
        self.cap = cap
        self.used: dict[str, int] = {}

    async def propose(
        self,
        flow: Flow,
        attacks: dict[str, Attack],
        applicable: list[str],
        summary: dict[str, Any],
    ) -> tuple[str, PayloadChoice] | None:
        if self.used.get(flow.id, 0) >= self.cap or not self.provider.available:
            return None
        options = {aid: _slot_view(attacks[aid], flow) for aid in applicable if attacks[aid].payload_slots(flow)}
        if not options:
            return None
        request = {"flow": _flow_view(flow), "evidence_summary": summary, "applicable_attacks": options}
        result = await self.provider.complete_json(
            "prober", load_prompt("prober"), json.dumps(request, indent=1), ProbeRequest, max_tokens=2000
        )
        if not result.ok or result.value is None or result.value.skip:
            return None
        probe = result.value
        if probe.attack_id not in options:
            log.info("prober proposed non-applicable attack %r; ignored", probe.attack_id)
            return None
        attack = attacks[probe.attack_id]
        proposed = {sv.slot: sv.value for sv in probe.values}
        payload: Payload = {}
        for slot in attack.payload_slots(flow):
            value = proposed.get(slot.name)
            if value is None or not attack.validate(slot, value):
                return None
            payload[slot.name] = value
        self.used[flow.id] = self.used.get(flow.id, 0) + 1
        reason = (probe.reason or "").strip()[:120] or "follow-up probe"
        return probe.attack_id, PayloadChoice(attack.finalize(flow, payload), "llm", reason)
