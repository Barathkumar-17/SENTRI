"""Model usage is measured across every role, limits stop model use cleanly, unknown prices stay unknown."""

from __future__ import annotations

from pydantic import BaseModel

from sentri.llm import BudgetedProvider, LLMResult


class Out(BaseModel):
    x: int = 1


class Fake:
    name = "fake"
    available = True

    def __init__(self, model: str = "local-model", cost: float = 0.0, retries: int = 0):
        self.model, self.cost, self.retries, self.seen = model, cost, retries, 0

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        self.seen += 1
        return LLMResult(True, Out(), 100, 20, self.cost, self.model, retries=self.retries)


async def test_usage_is_counted_per_role_and_cost_is_unknown_for_unpriced_models():
    p = BudgetedProvider(Fake(retries=2), 1.0, 5)
    for role in ("strategist", "explorer", "explorer"):
        await p.complete_json(role, "s", "u", Out, 100)
    u = p.usage()
    assert u["calls"] == 3 and u["input_tokens"] == 300 and u["output_tokens"] == 60 and u["retries"] == 6
    assert u["roles"]["explorer"]["calls"] == 2 and u["roles"]["strategist"]["calls"] == 1
    assert u["cost_usd"] is None and u["unpriced_calls"] == 3  # unknown, never $0
    assert u["limits"]["spend_enforced"] is False


async def test_known_prices_are_summed():
    p = BudgetedProvider(Fake("claude-sonnet-5-5", cost=0.01), 1.0, 5)
    await p.complete_json("explorer", "s", "u", Out, 100)
    assert p.usage()["cost_usd"] == 0.01 and p.cost_known


async def test_call_limit_stops_cleanly_with_a_reason():
    inner = Fake()
    p = BudgetedProvider(inner, 1.0, 5, max_calls=2)
    results = [await p.complete_json("explorer", "s", "u", Out, 100) for _ in range(4)]
    assert [r.ok for r in results] == [True, True, False, False] and inner.seen == 2
    assert not p.available and p.stop_reason == "call limit 2 reached" and p.usage()["refused_calls"] == 2


async def test_token_limit_reserves_the_prompt_and_the_output_before_calling():
    inner = Fake()
    p = BudgetedProvider(inner, 1.0, 5, max_tokens=500)
    first = await p.complete_json("strategist", "s", "u" * 400, Out, 300)  # ~100 in + 300 out reserved: fits
    second = await p.complete_json("explorer", "s", "u" * 400, Out, 300)  # 120 used + 400 reserved > 500
    assert first.ok and not second.ok and inner.seen == 1
    assert p.stop_reason == "token limit 500 reached"


def test_usage_line_reports_unknown_cost_and_stop_reason():
    from sentri.report.markdown import usage_line

    line = usage_line(
        {
            "calls": 2,
            "input_tokens": 2387,
            "output_tokens": 1226,
            "retries": 0,
            "cost_usd": None,
            "unpriced_calls": 2,
            "stop_reason": "provider quota exhausted (rate limit beyond the wait cap)",
            "roles": {"strategist": {"calls": 1}, "explorer": {"calls": 1}},
        }
    )
    assert "2 calls (strategist 1, explorer 1)" in line and "2387 input + 1226 output" in line
    assert "cost unknown" in line and "stopped: provider quota exhausted" in line
    assert usage_line({"calls": 0}) == ""


async def test_a_call_refused_for_the_token_cap_ends_model_use_for_the_run():
    """Found by the 2026-11-23 flow-shop run: the refused call left the provider 'available', so the explorer ran two
    code fallback steps and called the stop 'model failed 3 times in a row'."""
    from pydantic import BaseModel

    from sentri.llm import BudgetedProvider, LLMResult

    class Out(BaseModel):
        ok: bool = True

    class Inner:
        name = "inner"
        available = True

        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            return LLMResult(True, Out(), input_tokens=900, output_tokens=50)

    budgeted = BudgetedProvider(Inner(), 1.0, 5.0, max_tokens=1500)
    assert (await budgeted.complete_json("explorer", "s", "u", Out, 500)).ok
    assert budgeted.available  # 950 of 1500 used: a small call could still fit
    refused = await budgeted.complete_json("explorer", "s", "u" * 4000, Out, 900)  # needs ~1900: does not fit
    assert not refused.ok and "token limit 1500 reached" in (refused.error or "")
    assert not budgeted.available and budgeted.stop_reason == "token limit 1500 reached"
