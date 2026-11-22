from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from sentri.attacker import Attacker, Prober
from sentri.attacks import ATTACKS
from sentri.config import Settings
from sentri.contract import load_contract
from sentri.llm import BudgetedProvider, LLMConfigError, LLMResult, PayloadSet, ProbeRequest, SlotValue, make_provider
from sentri.llm.prices import cost_usd
from tests.conftest import BUGGY_APP

CONTRACT = load_contract(BUGGY_APP / "contract.yaml")


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self.responses = list(responses)

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, role, system, user, schema: type[BaseModel], max_tokens=4000):
        item = self.responses.pop(0)
        if isinstance(item, float):
            await asyncio.sleep(item)
            return LLMResult(ok=False, error="slow")
        if isinstance(item, str):
            return LLMResult(ok=False, error=item)
        return LLMResult(ok=True, value=item, cost_usd=0.01)


def budgeted(responses, budget=1.0, timeout=5.0):
    return BudgetedProvider(FakeProvider(responses), budget, timeout)


async def test_valid_llm_payload_is_used():
    flow = CONTRACT.flow("create_task")
    ps = PayloadSet(
        values=[SlotValue(slot="shape", value="corrupt"), SlotValue(slot="filename", value="x.png")],
        reason="corrupt file hit BUG-003 before",
    )
    choice = await Attacker(budgeted([ps])).choose(ATTACKS["bad_file"], flow, [], 0)
    assert choice.source == "llm" and choice.payload == {"shape": "corrupt", "filename": "x.png"}
    assert "BUG-003" in choice.reason


@pytest.mark.parametrize("response", ["invalid json", 10.0])
async def test_failures_fall_back_to_static(response):
    flow = CONTRACT.flow("create_task")
    provider = budgeted([response], timeout=0.05)
    choice = await Attacker(provider).choose(ATTACKS["bad_file"], flow, [], 0)
    assert choice.source == "static" and choice.payload["shape"] == "zero_byte"
    assert choice.llm_error


async def test_out_of_slot_values_are_replaced():
    flow = CONTRACT.flow("signup")
    ps = PayloadSet(values=[SlotValue(slot="field_0", value="not blank at all")], reason="r")
    choice = await Attacker(budgeted([ps])).choose(ATTACKS["empty_input"], flow, [], 0)
    assert choice.source == "static"
    assert all(v.strip() == "" for v in choice.payload.values())


async def test_choice_slots_reject_unknown_values():
    flow = CONTRACT.flow("signup")
    ps = PayloadSet(values=[SlotValue(slot="action", value="delete_database")], reason="r")
    choice = await Attacker(budgeted([ps])).choose(ATTACKS["slow_network"], flow, [], 1)
    assert choice.payload["action"] in ("submit", "submit_then_reload") and choice.source == "static"


async def test_timing_attack_needs_no_model():
    choice = await Attacker(budgeted([])).choose(ATTACKS["double_submit"], CONTRACT.flow("signup"), [], 0)
    assert choice.source == "none" and choice.payload == {}


async def test_budget_exhaustion_disables_model():
    provider = budgeted([PayloadSet(values=[], reason="")], budget=0.005)
    await provider.complete_json("attacker", "s", "u", PayloadSet)
    assert not provider.available
    assert (await provider.complete_json("attacker", "s", "u", PayloadSet)).error.startswith("llm budget")


async def test_prober_respects_cap_and_applicability():
    flow = CONTRACT.flow("signup")
    bad = ProbeRequest(attack_id="bad_file", values=[], reason="x")  # not applicable to signup
    good = ProbeRequest(
        attack_id="slow_network",
        values=[SlotValue(slot="action", value="submit_then_reload")],
        reason="retry under slow network",
    )
    prober = Prober(budgeted([bad, good, good]), cap=1)
    applicable = ["empty_input", "double_submit", "slow_network", "rapid_repeat"]
    assert await prober.propose(flow, ATTACKS, applicable, {}) is None
    result = await prober.propose(flow, ATTACKS, applicable, {})
    assert result is not None and result[0] == "slow_network"
    assert await prober.propose(flow, ATTACKS, applicable, {}) is None  # cap reached


def test_make_provider_configs():
    s = Settings()
    s.api_key = None
    s.llm_provider = "anthropic"
    assert make_provider(s).inner.name == "none"
    s.llm_provider = "openai_compatible"
    with pytest.raises(LLMConfigError):
        make_provider(s)
    s.llm_base_url = "http://localhost:11434/v1"
    s.model_fast = s.model_smart = "qwen3"
    assert make_provider(s).inner.name == "openai_compatible"
    s.llm_provider = "bogus"
    with pytest.raises(LLMConfigError):
        make_provider(s)


def test_prices():
    assert cost_usd("claude-opus-5-5", 1_000_000, 0) == 4.0
    assert cost_usd("some-local-model", 10, 10) == 0.0


def test_ollama_shortcut():
    s = Settings()
    s.llm_base_url = None
    s.use_ollama("qwen2.5:7b")
    p = make_provider(s)
    assert p.inner.name == "openai_compatible"
    assert s.llm_base_url == "http://localhost:11434/v1" and s.model_fast == s.model_smart == "qwen2.5:7b"
