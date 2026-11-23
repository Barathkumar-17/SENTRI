"""LLM provider interface, structured-output schemas, budget wrapper.

Everything a model returns is validated against these schemas. Free text only lands in `reason` fields.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel, Field

from sentri.llm.prices import price_known
from sentri.redact import redact

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


def _quota_reason(error: str) -> str:
    """The stop reason, keeping what the provider said: which limit and how long it asked to wait."""
    m = re.search(r"provider quota\): ((?:[a-z]+ per [a-z]+ \([A-Z]+\); )?retry in \d+s)", error)
    return "provider quota exhausted (rate limit beyond the wait cap)" + (f": {m.group(1)}" if m else "")


@dataclass
class LLMResult[T: BaseModel]:
    ok: bool
    value: T | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    model: str | None = None
    error: str | None = None
    retries: int = 0  # retries inside this call (rate-limit waits, re-asks after invalid JSON)
    waits: list[dict[str, object]] = field(default_factory=list)  # each rate-limit wait: limit, asked_s, waited_s

    def __post_init__(self) -> None:
        # Provider errors quote keys, organization and project ids: scrub them before anyone sees the text.
        if self.error:
            self.error = redact(self.error)


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


@dataclass
class RoleUsage:
    calls: int = 0
    ok: int = 0
    failed: int = 0
    retries: int = 0  # provider-level retries (rate-limit waits, re-asks) inside those calls
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # only priced calls
    unpriced_calls: int = 0  # calls whose price Sentri does not know: total cost is then "unknown"
    seconds: float = 0.0
    rate_wait_s: float = 0.0  # time spent waiting out the provider's rate limits (inside `seconds`)


class BudgetedProvider:
    """Every model call of a run goes through here (strategist, explorer, planner, attacker, prober, fixer).

    Measures calls, tokens, retries and time per role, and enforces the limits: dollars (only where the price is
    known), calls, total tokens (input + reserved output) and the per-call timeout. A refused or failed call
    returns `ok=False` with the reason; `stop_reason` says which limit ended model use. Limits are never raised
    by Sentri itself."""

    def __init__(
        self,
        inner: LLMProvider,
        budget_usd: float,
        timeout_s: float,
        max_calls: int = 0,  # 0 = no limit
        max_tokens: int = 0,  # 0 = no limit; input (estimated before the call) + the call's max output tokens
    ):
        self.inner = inner
        self.name = inner.name
        self.budget_usd = budget_usd
        self.timeout_s = timeout_s
        self.max_calls = max_calls
        self.max_tokens = max_tokens
        self.spent_usd = 0.0
        self.calls = 0
        self.failures = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.retries = 0
        self.unpriced_calls = 0
        self.refused = 0
        self.stop_reason: str | None = None
        self.rate_waits: list[dict[str, object]] = []  # every rate-limit wait: role, limit, asked_s, waited_s
        self.quota_exhausted = False
        self.roles: dict[str, RoleUsage] = {}

    def _limit_reason(self, reserve_tokens: int = 0) -> str | None:
        if self.quota_exhausted:
            return "provider quota exhausted (rate limit beyond the wait cap)"
        if self.spent_usd >= self.budget_usd:
            return f"llm budget ${self.budget_usd:.2f} exhausted"
        if self.max_calls and self.calls >= self.max_calls:
            return f"call limit {self.max_calls} reached"
        if self.max_tokens and self.input_tokens + self.output_tokens + reserve_tokens > self.max_tokens:
            return f"token limit {self.max_tokens} reached"
        return None

    @property
    def available(self) -> bool:
        if not self.inner.available:
            return False
        reason = self._limit_reason()
        if reason is not None:
            self.stop_reason = self.stop_reason or reason
            return False
        return True

    @property
    def cost_known(self) -> bool:
        return self.unpriced_calls == 0

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        if not self.inner.available:
            return LLMResult(ok=False, error="llm unavailable")
        # Reserve the prompt (about 4 characters per token) and the whole output allowance before calling.
        reason = self._limit_reason(reserve_tokens=(len(system) + len(user)) // 4 + max_tokens)
        if reason is not None:
            self.refused += 1
            self.stop_reason = self.stop_reason or reason
            return LLMResult(ok=False, error=reason)
        usage = self.roles.setdefault(role, RoleUsage())
        self.calls += 1
        usage.calls += 1
        started = time.monotonic()
        try:
            result = await self._answer_within_timeout(role, system, user, schema, max_tokens, started)
        except TimeoutError:
            self.failures += 1
            usage.failed += 1
            usage.seconds += time.monotonic() - started
            return LLMResult(ok=False, error=f"timeout after {self.timeout_s:g}s")
        usage.seconds += time.monotonic() - started
        for w in result.waits:
            usage.rate_wait_s += float(str(w.get("waited_s", 0.0)))
            if len(self.rate_waits) < 100:
                self.rate_waits.append({"role": role, **w})
        priced = result.cost_usd > 0 or price_known(result.model)
        self.spent_usd += result.cost_usd
        self.input_tokens += result.input_tokens
        self.output_tokens += result.output_tokens
        self.retries += result.retries
        usage.input_tokens += result.input_tokens
        usage.output_tokens += result.output_tokens
        usage.retries += result.retries
        usage.cost_usd += result.cost_usd
        if not priced:
            self.unpriced_calls += 1
            usage.unpriced_calls += 1
        if result.ok:
            usage.ok += 1
        else:
            self.failures += 1
            usage.failed += 1
            if "provider quota" in (result.error or ""):
                self.quota_exhausted = True  # no further calls this run: they would only be refused again
                self.stop_reason = self.stop_reason or _quota_reason(result.error or "")
        return result

    async def _answer_within_timeout[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int, started: float
    ) -> LLMResult[T]:
        """The per-call timeout bounds how long the provider takes to answer, not how long it asked us to wait out
        its rate limit: time the provider reports as rate-limit waiting extends the deadline by that much."""
        waited_before = float(getattr(self.inner, "rate_wait_total", 0.0))
        task = asyncio.ensure_future(self.inner.complete_json(role, system, user, schema, max_tokens))
        while True:
            waited = float(getattr(self.inner, "rate_wait_total", 0.0)) - waited_before
            remaining = self.timeout_s + waited - (time.monotonic() - started)
            if remaining <= 0:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise TimeoutError
            done, _ = await asyncio.wait({task}, timeout=min(remaining, 5.0))
            if done:
                result: LLMResult[T] = task.result()
                return result

    def usage(self) -> dict[str, object]:
        """Run-level usage for the ledger, report and API. Cost is None (unknown) if any call was unpriced."""
        return {
            "provider": self.name,
            "calls": self.calls,
            "failed_calls": self.failures,
            "refused_calls": self.refused,
            "retries": self.retries,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.spent_usd, 6) if self.cost_known else None,
            "priced_cost_usd": round(self.spent_usd, 6),
            "unpriced_calls": self.unpriced_calls,
            "limits": {
                "spend_usd": self.budget_usd,
                "spend_enforced": self.cost_known,
                "calls": self.max_calls or None,
                "tokens": self.max_tokens or None,
                "per_call_timeout_s": self.timeout_s,
            },
            "stop_reason": self.stop_reason,
            "rate_waits": list(self.rate_waits),
            # which structured-output mode the server accepted (json_schema: the schema travels once, in the request)
            "response_format": (
                None
                if not hasattr(self.inner, "_schema_mode")
                else ("json_schema" if self.inner._schema_mode else "json_object")
            ),
            "roles": {role: u.__dict__ for role, u in sorted(self.roles.items())},
        }
