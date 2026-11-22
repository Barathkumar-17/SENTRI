from __future__ import annotations

from sentri.agent.recon import AppMap, EndpointEntry, PageEntry
from sentri.agent.strategy import Hypothesis, Strategy, plan_strategy, target_exists, validate
from sentri.llm import BudgetedProvider, LLMResult, NullProvider

APP_MAP = AppMap(
    base_url="http://127.0.0.1:5055",
    pages=[PageEntry(path="/tasks", requires_login=True), PageEntry(path="/tasks/new"), PageEntry(path="/signup")],
    endpoints=[
        EndpointEntry(method="POST", path="/tasks/new", examples=["/tasks/new"], state_changing=True),
        EndpointEntry(method="GET", path="/tasks/:id", examples=["/tasks/42"]),
    ],
    has_login_flow=True,
)


def h(id_: str, *targets: str, priority: int = 3) -> Hypothesis:
    return Hypothesis(id=id_, title=f"t {id_}", targets=list(targets), priority=priority)


def test_targets_must_exist_in_the_map():
    assert target_exists("/tasks", APP_MAP)
    assert target_exists("POST /tasks/new", APP_MAP)
    assert target_exists("GET /tasks/7", APP_MAP)  # ids normalize to the recorded template
    assert target_exists("/tasks?page=2", APP_MAP)
    assert not target_exists("/admin", APP_MAP)
    assert not target_exists("DELETE /tasks/new", APP_MAP)  # method matters


def test_validate_drops_generic_or_invented_hypotheses():
    strategy = Strategy(
        hypotheses=[
            h("Generic SQL injection!", "/admin"),
            h("idor", "GET /tasks/1", "/made-up", priority=1),
            h("blank", "/signup", priority=2),
            h("blank", "/tasks/new", priority=4),
        ]
    )
    kept, dropped = validate(strategy, APP_MAP)
    assert [x.id for x in kept.hypotheses] == ["idor", "blank", "blank_2"]  # sorted by priority, ids unique
    assert kept.hypotheses[0].targets == ["GET /tasks/1"]  # invented target removed, real one kept
    assert len(dropped) == 1 and "generic_sql_injection" not in dropped[0] and "/admin" in dropped[0]


class FakeProvider:
    name = "fake"

    def __init__(self, value):
        self.value = value

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        assert role == "strategist" and '"/tasks/new"' in user  # the model sees the real app map
        return LLMResult(ok=True, value=self.value)


async def test_plan_strategy_with_and_without_a_model():
    value = Strategy(app_kind="task tracker", hypotheses=[h("x", "/tasks"), h("y", "/nowhere")])
    planned = await plan_strategy(APP_MAP, BudgetedProvider(FakeProvider(value), 1, 5))
    assert planned.source == "llm" and [x.id for x in planned.strategy.hypotheses] == ["x"] and planned.dropped
    none = await plan_strategy(APP_MAP, BudgetedProvider(NullProvider(), 1, 5))
    assert none.source == "none" and not none.strategy.hypotheses
