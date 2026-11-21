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
