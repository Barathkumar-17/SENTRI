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
        "tier `on_demand` on tokens per minute (TPM): Limit 8000, Used 7950, Requested 4200. "
        "Please try again in 1m19.6s."
    )
    inner = provider_with(lambda r: httpx.Response(429, json={"error": {"message": message}}))
    budgeted = BudgetedProvider(inner, 1.0, 5)
    await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert budgeted.stop_reason == (
        "provider quota exhausted (rate limit beyond the wait cap): tokens per minute (TPM); retry in 80s"
    )
    assert "org_0000" not in budgeted.stop_reason

    daily = {"error": {"message": "Rate limit reached on tokens per day (TPD). Please try again in 7m12.5s."}}
    budgeted = BudgetedProvider(provider_with(lambda r: httpx.Response(429, json=daily)), 1.0, 5)
    await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert budgeted.stop_reason.endswith("tokens per day (TPD); retry in 432s")


def _groq_429(wait: str, limit: str = "tokens per minute (TPM)") -> httpx.Response:
    message = f"Rate limit reached for model `m` on {limit}: Limit 8000, Used 7950. Please try again in {wait}."
    return httpx.Response(429, json={"error": {"message": message}})


async def test_a_per_minute_limit_is_waited_out_within_one_window_and_each_wait_is_recorded(monkeypatch):
    """TPM wait policy: Groq's 8k tokens-per-minute window asks for ~40 s; that frees itself, so wait instead of
    stopping. Every wait is recorded with the limit the provider named, what it asked for and what was waited."""
    import asyncio

    from sentri.llm import BudgetedProvider

    slept: list[float] = []

    async def no_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    answers = iter([_groq_429("39.8s"), reply('{"payloads": []}')])
    budgeted = BudgetedProvider(provider_with(lambda r: next(answers)), 1.0, 5)
    result = await budgeted.complete_json("explorer", "s", "u", PayloadSet)
    assert result.ok and budgeted.available and budgeted.stop_reason is None
    assert slept == [40.3]
    assert budgeted.usage()["rate_waits"] == [
        {"role": "explorer", "limit": "tokens per minute (TPM)", "asked_s": 39.8, "waited_s": 40.3}
    ]
    assert budgeted.roles["explorer"].rate_wait_s == 40.3


async def test_longer_or_unstated_waits_still_stop_model_use(monkeypatch):
    import asyncio

    from sentri.llm import BudgetedProvider

    async def no_sleep(seconds):
        raise AssertionError("must not wait")

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    for answer, reason in (
        (_groq_429("1m19.6s"), "tokens per minute (TPM); retry in 80s"),  # more than one window
        (httpx.Response(429, json={"error": {"message": "slow down, try again in 41s"}}), "retry in 41s"),  # unstated
    ):
        budgeted = BudgetedProvider(provider_with(lambda r, a=answer: a), 1.0, 5)
        await budgeted.complete_json("explorer", "s", "u", PayloadSet)
        assert not budgeted.available and budgeted.stop_reason.endswith(reason)


async def test_the_per_call_timeout_excludes_rate_limit_waiting_but_still_catches_a_slow_answer():
    import asyncio

    from sentri.llm import BudgetedProvider, LLMResult

    class Slow:
        name = "slow"
        available = True

        def __init__(self, waited: float, work: float):
            self.rate_wait_total, self.waited, self.work = 0.0, waited, work

        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            self.rate_wait_total += self.waited  # the provider asked to wait this long
            await asyncio.sleep(self.waited + self.work)
            return LLMResult(True, PayloadSet(payloads=[]))

    waiting = BudgetedProvider(Slow(waited=0.6, work=0.1), 1.0, 0.3)
    assert (await waiting.complete_json("explorer", "s", "u", PayloadSet)).ok  # 0.7 s, of which 0.6 s waiting
    slow = BudgetedProvider(Slow(waited=0.0, work=0.7), 1.0, 0.3)
    result = await slow.complete_json("explorer", "s", "u", PayloadSet)
    assert not result.ok and "timeout" in (result.error or "")


async def test_the_answer_schema_travels_once_and_the_fallback_writes_it_out():
    """Measured on flow-shop: the ~3.8k-character schema went in the rules text and in response_format."""
    from sentri.llm import BudgetedProvider

    seen: list[dict] = []

    def schema_ok(request):
        seen.append(json.loads(request.content))
        return reply('{"payloads": []}')

    budgeted = BudgetedProvider(provider_with(schema_ok), 1.0, 5)
    assert (await budgeted.complete_json("attacker", "RULES", "u", PayloadSet)).ok
    body = seen[0]
    assert body["response_format"]["type"] == "json_schema" and '"properties"' in json.dumps(body["response_format"])
    assert '"properties"' not in body["messages"][0]["content"]  # not repeated in the rules
    assert budgeted.usage()["response_format"] == "json_schema"

    seen.clear()

    def rejects_schema(request):
        sent = json.loads(request.content)
        seen.append(sent)
        if sent["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": {"message": "json_schema not supported"}})
        return reply('{"payloads": []}')

    budgeted = BudgetedProvider(provider_with(rejects_schema), 1.0, 5)
    assert (await budgeted.complete_json("attacker", "RULES", "u", PayloadSet)).ok
    assert seen[-1]["response_format"]["type"] == "json_object"
    assert '"properties"' in seen[-1]["messages"][0]["content"]  # written out where the server cannot enforce it
    assert budgeted.usage()["response_format"] == "json_object"


def test_prompts_are_compact_json_with_every_value_kept():
    from sentri.llm.untrusted import build_user_prompt

    task = {"goal": "Complete a purchase", "steps_left": 3}
    view = {"options": [{"id": "L1", "do": "navigate /cart"}], "text": "Shop"}
    prompt = build_user_prompt(task, view, nonce="n1")
    assert '\n "' not in prompt  # no indentation
    task_part = prompt.split("\n", 1)[1].split("\n\n<<<APP DATA", 1)[0]
    data_part = prompt.split(">>>\n", 1)[1].rsplit("\n<<<END", 1)[0]
    assert json.loads(task_part) == task and json.loads(data_part) == view
