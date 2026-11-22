from __future__ import annotations

from dataclasses import dataclass, field

from sentri.agent.explorer import ActionSpec, Decision, ExploreBudget, Explorer, to_action
from sentri.agent.recon import AppMap
from sentri.agent.session import ActionResult, Mark
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.agent.tokens import tokens_in
from sentri.evidence.agent_models import AgentEvidence, ResponseRecord
from sentri.evidence.models import Evidence
from sentri.llm import BudgetedProvider, LLMResult


@dataclass
class FakeSession:
    current: str = "anon"
    base_url: str = "http://127.0.0.1"
    requests: list = field(default_factory=list)
    executed: list = field(default_factory=list)
    leak_on: int = 2  # the step whose evidence shows a logged-out view of a login-only page

    def mark(self) -> Mark:
        return Mark(0, 0, 0)

    async def observe(self):
        return {"identity": self.current, "url": "http://127.0.0.1/x", "text": "", "forms": [], "links": []}

    async def execute(self, action):
        self.executed.append(action)
        if action.kind == "switch_user":
            self.current = action.user
        return ActionResult(True, None, "http://127.0.0.1/tasks/1", [])

    def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
        responses = ()
        if len(self.executed) == self.leak_on:
            responses = (ResponseRecord("anon", "GET", "/tasks/1", 200, "<h1>task</h1>"),)
        return AgentEvidence(base=Evidence(), responses=responses, protected_paths=("/tasks/:id",))


class ScriptedModel:
    name = "scripted"

    def __init__(self, decisions):
        self.decisions = list(decisions)

    @property
    def available(self) -> bool:
        return True

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        assert role == "explorer" and '"focus_hypothesis"' in user
        item = self.decisions.pop(0) if self.decisions else None
        if item is None or isinstance(item, str):
            return LLMResult(ok=False, error=item or "no more")
        return LLMResult(ok=True, value=item)


def d(kind, hyp="idor", thought="t", **kw) -> Decision:
    return Decision(thought=thought, hypothesis_id=hyp, action=ActionSpec(kind=kind, **kw))


STRATEGY = Strategy(
    app_kind="tracker", hypotheses=[Hypothesis(id="idor", title="Read others' tasks", targets=["/tasks"])]
)


async def run(decisions, budget=None):
    steps: list[dict] = []
    session = FakeSession()
    explorer = Explorer(
        session,
        BudgetedProvider(ScriptedModel(decisions), 1, 5),
        STRATEGY,
        AppMap(base_url="http://127.0.0.1"),
        budget or ExploreBudget(max_steps=6),
        steps.append,
    )
    return await explorer.run(), steps, session


async def test_a_firing_oracle_becomes_a_replayable_candidate():
    result, steps, _ = await run(
        [
            d("navigate", path="/tasks", thought="look at tasks"),
            d("navigate", path="/tasks/1", thought="open someone's task logged out"),
            d("finish", reason="done"),
        ]
    )
    assert result.stopped.startswith("model finished") and result.steps == 3
    assert len(result.candidates) == 1
    c = result.candidates[0]
    assert c.signals[0]["oracle_id"] == "auth_bypass" and c.hypothesis_id == "idor"
    kinds = [r.action["kind"] for r in c.repro]
    assert kinds == ["switch_user", "navigate", "navigate"]  # starts from the identity in use, then the path
    assert steps[1]["signals"] and steps[1]["thought"] == "open someone's task logged out"
    assert steps[1]["parent_step"] == 1 and steps[0]["parent_step"] is None  # the attack tree


async def test_invalid_actions_are_skipped_and_failures_stop_the_loop():
    result, steps, session = await run(
        [
            d("type", selector="#x", text="a", repeat=0),  # invalid: repeat below 1
            "bad json",
            "bad json",
            "bad json",
        ]
    )
    assert result.stopped.startswith("model failed 3 times") and not session.executed and not steps


async def test_budget_limits_steps():
    result, steps, _ = await run([d("wait", ms=1)] * 10, ExploreBudget(max_steps=3))
    assert result.stopped == "step budget used" and len(steps) == 3


def test_to_action_keeps_only_fields_for_the_kind():
    action = to_action(ActionSpec(kind="navigate", path="/a", selector="ignored", times=3))
    assert action.model_dump() == {"kind": "navigate", "path": "/a"}


def test_tokens_in_request_bodies():
    assert tokens_in('{"title": "Sentri item r1-3-ab12cd", "c": "sentri-canary-a-9f"}') == ["sentri-canary-a-9f"]
    assert tokens_in("email=sentri+r1-1-xyz@example.test") == ["sentri+r1-1-xyz@example.test"]


async def test_the_same_break_is_flagged_once_and_exploration_continues():
    class AlwaysLeaking(FakeSession):
        def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
            r = ResponseRecord("anon", "GET", "/tasks/1", 200, "<h1>task</h1>")
            return AgentEvidence(base=Evidence(), responses=(r,), protected_paths=("/tasks/:id",))

    steps: list[dict] = []
    decisions = [
        d("navigate", path="/tasks/1"),
        d("navigate", path="/tasks/1"),
        Decision(
            thought="t",
            hypothesis_id="idor",
            action=ActionSpec(kind="wait", ms=1),
            suspect=True,
            suspect_reason="anon can read tasks",
        ),
        Decision(
            thought="t",
            hypothesis_id="idor",
            action=ActionSpec(kind="wait", ms=1),
            suspect=True,
            suspect_reason="Anon can read tasks",
        ),
        d("finish", reason="done"),
    ]
    explorer = Explorer(
        AlwaysLeaking(),
        BudgetedProvider(ScriptedModel(decisions), 1, 5),
        STRATEGY,
        AppMap(base_url="http://127.0.0.1"),
        ExploreBudget(max_steps=10, max_candidates=1),
        steps.append,
    )
    result = await explorer.run()
    assert len(result.candidates) == 1  # one break, not one per step
    assert result.steps == 5 and result.stopped.startswith("model finished")  # the cap never stops exploring
    assert explorer.flagged and "already_flagged" in explorer._request(6, None, 60)
