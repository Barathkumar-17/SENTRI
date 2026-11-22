"""LLM provider interface, structured-output schemas, budget wrapper.

Everything a model returns is validated against these schemas. Free text only lands in `reason` fields.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

# ---- schemas the model must fill ----------------------------------------------------------------------


class SlotValue(BaseModel):
    slot: str
    value: str


class PayloadSet(BaseModel):
    values: list[SlotValue] = Field(default_factory=list)
    reason: str = ""


class ProbeRequest(BaseModel):
    skip: bool = False
    attack_id: str = ""
    values: list[SlotValue] = Field(default_factory=list)
    reason: str = ""


class DraftStep(BaseModel):
    kind: str
    selector: str | None = None
    value: str | None = None
    file: str | None = None
    key: str | None = None
    path: str | None = None


class DraftSuccess(BaseModel):
    url_contains: str | None = None
    text_visible: str | None = None
    selector_visible: str | None = None


class DraftFlow(BaseModel):
    id: str
    description: str
    start: str
    steps: list[DraftStep]
    success: DraftSuccess
    requires_login: bool = False
    attack: bool = True
    list_selector: str | None = None
    list_page: str | None = None


class ContractDraft(BaseModel):
    flows: list[DraftFlow]
    login_flow: str | None = None


class PatchProposal(BaseModel):
    diff: str
    summary: str


# ---- provider -----------------------------------------------------------------------------------------


@dataclass
class LLMResult[T: BaseModel]:
    ok: bool
    value: T | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None
    error: str | None = None


class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]: ...


class NullProvider:
    """No key or --no-llm: every call is 'unavailable' so callers use static fallbacks."""

    name = "none"

    @property
    def available(self) -> bool:
        return False

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        return LLMResult(ok=False, error="llm disabled")


class BudgetedProvider:
    """Wraps a provider with a per-run dollar budget and a per-call timeout; tracks spend."""

    def __init__(self, inner: LLMProvider, budget_usd: float, timeout_s: float):
        self.inner = inner
        self.name = inner.name
        self.budget_usd = budget_usd
        self.timeout_s = timeout_s
        self.spent_usd = 0.0
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0

    @property
    def available(self) -> bool:
        return self.inner.available and self.spent_usd < self.budget_usd

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        if not self.inner.available:
            return LLMResult(ok=False, error="llm unavailable")
        if self.spent_usd >= self.budget_usd:
            return LLMResult(ok=False, error=f"llm budget ${self.budget_usd:.2f} exhausted")
        self.calls += 1
        try:
            result = await asyncio.wait_for(
                self.inner.complete_json(role, system, user, schema, max_tokens), timeout=self.timeout_s
            )
        except TimeoutError:
            self.failures += 1
            return LLMResult(ok=False, error=f"timeout after {self.timeout_s:g}s")
        self.spent_usd += result.cost_usd
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        if not result.ok:
            self.failures += 1
        return result
