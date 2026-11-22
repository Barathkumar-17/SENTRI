"""Provider for any OpenAI-compatible chat-completions API.

Covers OpenAI, Google Gemini (OpenAI endpoint), OpenRouter, Groq, Together, Mistral, DeepSeek, and local
servers such as Ollama, LM Studio and vLLM. The reply must be JSON; it is validated against the schema
and anything invalid is treated as "model unavailable" (callers then use static fallbacks).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

import httpx
from pydantic import BaseModel, ValidationError

from sentri.llm.prices import cost_usd
from sentri.llm.provider import LLMResult

log = logging.getLogger(__name__)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRY_AGAIN = re.compile(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", re.IGNORECASE)
_TRY_AGAIN_LONG = re.compile(r"try again in (?:(\d+)h)?(?:(\d+)m)?([\d.]+)(ms|s)", re.IGNORECASE)
MAX_RATE_WAITS = 4  # rate-limit retries per call
MAX_RATE_WAIT_S = 30.0  # longest single wait; the per-call timeout still bounds the total


QUOTA_ERROR = "rate limit beyond the wait cap (provider quota)"


def server_wait(resp: httpx.Response) -> float:
    """What the server asks for: the Retry-After header, or "try again in 1h2m7.5s" in the message (Groq, OpenAI).
    2 s when it gives no hint."""
    header = resp.headers.get("retry-after", "")
    try:
        return float(header)
    except ValueError:
        pass
    m = _TRY_AGAIN_LONG.search(resp.text)
    if m is None:
        return 2.0
    hours, minutes, value, unit = m.group(1), m.group(2), float(m.group(3)), m.group(4).lower()
    seconds = value / 1000 if unit == "ms" else value
    return (int(hours) * 3600 if hours else 0) + (int(minutes) * 60 if minutes else 0) + seconds


def rate_limit_wait(resp: httpx.Response) -> float:
    """Seconds to wait before retrying a 429: the Retry-After header, or the "try again in 7.5s" that some
    servers (Groq, OpenAI) put in the message. Capped; 2 s when the server gives no hint."""
    header = resp.headers.get("retry-after", "")
    try:
        wait = float(header)
    except ValueError:
        m = _TRY_AGAIN.search(resp.text)
        if m:
            minutes, value, unit = m.group(1), float(m.group(2)), m.group(3).lower()
            wait = (int(minutes) * 60 if minutes else 0) + (value / 1000 if unit == "ms" else value)
        else:
            wait = 2.0
    return max(0.5, min(wait + 0.5, MAX_RATE_WAIT_S))


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
        rate_waits = 0
        attempt = 0
        while attempt < 3:
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
                if resp.status_code == 429 and server_wait(resp) > MAX_RATE_WAIT_S:
                    # A daily or long quota: waiting cannot help within this run. Stop instead of burning time.
                    return LLMResult(
                        ok=False,
                        model=model,
                        error=f"{QUOTA_ERROR}: retry in {server_wait(resp):.0f}s; {resp.text[:160]}",
                        retries=rate_waits,
                    )
                if resp.status_code == 429 and rate_waits < MAX_RATE_WAITS:
                    rate_waits += 1  # a rate limit is temporary: wait as asked, then the same attempt again
                    await asyncio.sleep(rate_limit_wait(resp))
                    continue
                if resp.status_code == 400 and self._schema_mode:
                    self._schema_mode = False  # server does not support json_schema; retry with json_object
                    continue
                if resp.status_code >= 400:
                    return LLMResult(
                        ok=False, model=model, error=f"http {resp.status_code}: {resp.text[:200]}", retries=rate_waits
                    )
                data = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                return LLMResult(ok=False, model=model, error=f"connection error: {exc}")
            usage = data.get("usage") or {}
            tokens_in += int(usage.get("prompt_tokens") or 0)
            tokens_out += int(usage.get("completion_tokens") or 0)
            try:
                text = data["choices"][0]["message"]["content"] or ""
                value = schema.model_validate_json(_extract_json(text))
                return LLMResult(
                    True,
                    value,
                    tokens_in,
                    tokens_out,
                    cost_usd(model, tokens_in, tokens_out),
                    model,
                    retries=rate_waits + attempt,
                )
            except (KeyError, IndexError, TypeError):
                last_error = "unexpected response shape"
            except ValidationError as exc:
                last_error = f"schema validation failed ({exc.error_count()} errors)"
                if attempt < 2:
                    messages = [*messages, {"role": "user", "content": "That was not valid. Reply with JSON only."}]
            attempt += 1
        return LLMResult(
            False,
            None,
            tokens_in,
            tokens_out,
            cost_usd(model, tokens_in, tokens_out),
            model,
            last_error,
            retries=rate_waits + attempt,
        )
