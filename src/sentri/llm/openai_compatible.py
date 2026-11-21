"""Provider for any OpenAI-compatible chat-completions API.

Covers OpenAI, Google Gemini (OpenAI endpoint), OpenRouter, Groq, Together, Mistral, DeepSeek, and local
servers such as Ollama, LM Studio and vLLM. The reply must be JSON; it is validated against the schema
and anything invalid is treated as "model unavailable" (callers then use static fallbacks).
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from pydantic import BaseModel, ValidationError

from sentri.llm.prices import cost_usd
from sentri.llm.provider import LLMResult

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, base_url: str, api_key: str | None, models: dict[str, str], timeout_s: float = 120):
        self.base_url = base_url.rstrip("/")
        self.models = models
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self.client = httpx.AsyncClient(headers=headers, timeout=timeout_s)
        # Some servers reject json_schema response formats; remember and fall back to json_object.
        self._schema_mode = True

    @property
    def available(self) -> bool:
        return True

    async def _post(self, body: dict[str, object]) -> httpx.Response:
        return await self.client.post(f"{self.base_url}/chat/completions", json=body)

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = self.models.get(role) or self.models["default"]
        json_schema = schema.model_json_schema()
        system_full = (
            f"{system}\n\nReply with a single JSON object only, no prose, matching this JSON Schema:\n"
            f"{json.dumps(json_schema)}"
        )
        messages = [{"role": "system", "content": system_full}, {"role": "user", "content": user}]
        tokens_in = tokens_out = 0
        last_error = "no response"
        for attempt in range(3):
            body: dict[str, object] = {"model": model, "messages": messages, "max_tokens": max_tokens}
            if self._schema_mode:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "schema": json_schema},
                }
            else:
                body["response_format"] = {"type": "json_object"}
            try:
                resp = await self._post(body)
                if resp.status_code == 400 and self._schema_mode:
                    self._schema_mode = False  # server does not support json_schema; retry with json_object
                    continue
                if resp.status_code >= 400:
                    return LLMResult(ok=False, model=model, error=f"http {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return LLMResult(ok=False, model=model, error=f"connection error: {exc}")
            usage = data.get("usage") or {}
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            try:
                text = data["choices"][0]["message"]["content"] or ""
                value = schema.model_validate_json(_extract_json(text))
                return LLMResult(True, value, tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out), model)
            except (KeyError, IndexError, TypeError):
                last_error = "unexpected response shape"
            except ValidationError as exc:
                last_error = f"schema validation failed ({exc.error_count()} errors)"
                if attempt < 2:
                    messages = [*messages, {"role": "user", "content": "That was not valid. Reply with JSON only."}]
        return LLMResult(False, None, tokens_in, tokens_out, cost_usd(model, tokens_in, tokens_out), model, last_error)
