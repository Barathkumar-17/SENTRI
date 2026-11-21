"""Anthropic SDK provider using structured outputs (`messages.parse` with a Pydantic schema)."""

from __future__ import annotations

import logging
from typing import Any, cast

import anthropic
from pydantic import BaseModel, ValidationError

from sentri.llm.prices import cost_usd
from sentri.llm.provider import LLMResult

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str, models: dict[str, str], efforts: dict[str, str] | None = None):
        self.client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=2)
        self.models = models
        self.efforts = efforts or {}

    @property
    def available(self) -> bool:
        return True

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = self.models.get(role) or self.models["default"]
        effort = self.efforts.get(role, "low")
        try:
            response = await self.client.beta.messages.parse(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
                output_config=cast(Any, {"effort": effort}),
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError as exc:
            return LLMResult(ok=False, model=model, error=f"rate limited: {exc.message}")
        except anthropic.APIStatusError as exc:
            return LLMResult(ok=False, model=model, error=f"api error {exc.status_code}: {exc.message}")
        except anthropic.APIConnectionError as exc:
            return LLMResult(ok=False, model=model, error=f"connection error: {exc}")
        except ValidationError as exc:
            return LLMResult(ok=False, model=model, error=f"schema validation failed: {exc.error_count()} errors")

        served = getattr(response, "model", None) or model
        usage = response.usage
        tokens_in = int(getattr(usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(usage, "output_tokens", 0) or 0)
        cost = cost_usd(served, tokens_in, tokens_out)
        if response.stop_reason == "refusal":
            return LLMResult(False, None, tokens_in, tokens_out, cost, served, "model declined the request")
        if response.stop_reason == "max_tokens":
            return LLMResult(False, None, tokens_in, tokens_out, cost, served, "output truncated at max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            return LLMResult(False, None, tokens_in, tokens_out, cost, served, "no parsed output")
        return LLMResult(True, parsed, tokens_in, tokens_out, cost, served)
