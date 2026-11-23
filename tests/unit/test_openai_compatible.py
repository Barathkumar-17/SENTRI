from __future__ import annotations

import json

import httpx

from sentri.llm import PayloadSet
from sentri.llm.openai_compatible import OpenAICompatibleProvider


def provider_with(handler) -> OpenAICompatibleProvider:
    p = OpenAICompatibleProvider("http://llm.test/v1", "key", {"default": "m"})
    p.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=p.client.headers)
    return p


def reply(content: str, status: int = 200) -> httpx.Response:
    body = {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    return httpx.Response(status, json=body)


async def test_parses_json_inside_code_fence():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer key"
        return reply('```json\n{"values": [{"slot": "a", "value": ""}], "reason": "r"}\n```')

    result = await provider_with(handler).complete_json("attacker", "s", "u", PayloadSet)
    assert result.ok and result.value.values[0].slot == "a" and result.input_tokens == 5


async def test_falls_back_to_json_object_when_schema_unsupported():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "unsupported"})
        return reply('{"values": [], "reason": "ok"}')

    result = await provider_with(handler).complete_json("attacker", "s", "u", PayloadSet)
    assert result.ok and seen == ["json_schema", "json_object"]


async def test_invalid_output_is_a_failure_not_an_exception():
    result = await provider_with(lambda r: reply("I cannot do that")).complete_json("a", "s", "u", PayloadSet)
    assert not result.ok and "schema" in (result.error or "")


async def test_rate_limits_are_waited_out_not_counted_as_bad_answers(monkeypatch):
    import asyncio

    from sentri.llm import openai_compatible as oc

    waits: list[float] = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": {"message": "Rate limit reached ... Please try again in 7.5s."}})
        if calls["n"] == 2:
            return httpx.Response(429, headers={"retry-after": "3"}, json={})
        return reply('{"values": [], "reason": "ok"}')

    result = await provider_with(handler).complete_json("attacker", "s", "u", PayloadSet)
    assert result.ok and waits == [8.0, 3.5]
    assert oc.rate_limit_wait(httpx.Response(429, text="try again in 1m30s")) == oc.MAX_RATE_WAIT_S  # capped


async def test_rate_limit_retries_are_bounded(monkeypatch):
    import asyncio

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    result = await provider_with(lambda r: httpx.Response(429, json={})).complete_json("attacker", "s", "u", PayloadSet)
    assert not result.ok and "429" in (result.error or "")


async def test_a_daily_quota_stops_model_use_instead_of_waiting(monkeypatch):
    """Found by the flow-shop run: Groq's tokens-per-day limit answered every call with 429 'try again in 7m'."""
    import asyncio

    from sentri.llm import BudgetedProvider

    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    body = {"error": {"message": "Rate limit reached on tokens per day (TPD). Please try again in 7m12.5s."}}
    inner = provider_with(lambda r: httpx.Response(429, json=body))
    budgeted = BudgetedProvider(inner, 1.0, 5)
    result = await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert not result.ok and "provider quota" in (result.error or "") and slept == []  # no pointless waiting
    assert not budgeted.available and budgeted.stop_reason.startswith("provider quota exhausted")


async def test_a_quota_stop_records_which_limit_and_how_long_the_provider_asked_to_wait(monkeypatch):
    """Found by the 2026-11-23 flow-shop run: the stop said only 'provider quota exhausted', so a per-minute window
    could not be told from a daily quota. The limit sits late in Groq's message, past where it was shortened."""
    import asyncio

    from sentri.llm import BudgetedProvider

    async def no_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    message = (
        "Rate limit reached for model `openai/gpt-oss-120b` in organization `org_0000fakeorganizationid00` service "
        "tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 7950, Requested 4200. Please try again in 39.8s."
    )
    inner = provider_with(lambda r: httpx.Response(429, json={"error": {"message": message}}))
    budgeted = BudgetedProvider(inner, 1.0, 5)
    await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert budgeted.stop_reason == (
        "provider quota exhausted (rate limit beyond the wait cap): tokens per minute (TPM); retry in 40s"
    )
    assert "org_0000" not in budgeted.stop_reason

    daily = {"error": {"message": "Rate limit reached on tokens per day (TPD). Please try again in 7m12.5s."}}
    budgeted = BudgetedProvider(provider_with(lambda r: httpx.Response(429, json=daily)), 1.0, 5)
    await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert budgeted.stop_reason.endswith("tokens per day (TPD); retry in 432s")
