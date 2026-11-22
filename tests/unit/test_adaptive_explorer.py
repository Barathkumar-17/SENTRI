"""Adaptive exploration orchestration.

MOCKED MODEL: these tests drive the explorer with scripted or rule-following fake models. They prove that the
orchestration works (state memory, compact context, options with outcomes, dead-end refusal, batching, budgets,
provenance), not that a real model explores well; the real-model demonstration is documented separately.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sentri.agent.explorer import ActionSpec, Decision, ExploreBudget, Explorer, FieldSpec, finding_origin
from sentri.agent.recon import AppMap
from sentri.agent.session import ActionResult, Mark
from sentri.agent.statemap import ExplorationMap, action_sig
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.evidence.agent_models import AgentEvidence
from sentri.evidence.models import Evidence
from sentri.llm import BudgetedProvider, LLMResult

STRATEGY = Strategy(hypotheses=[Hypothesis(id="checkout", title="Finish a checkout", targets=["/"])])

# A small app as a state machine: home -> cart form -> address form -> confirmation.
PAGES: dict[str, dict[str, Any]] = {
    "/": {"title": "Shop", "text": "Welcome", "links": ["/cart"], "forms": [], "buttons": []},
    "/cart": {
        "title": "Cart",
        "text": "Your cart",
        "links": ["/"],
        "forms": [{"selector": "#cart", "fields": [{"selector": "#qty", "name": "qty", "type": "number"}]}],
        "buttons": [],
    },
    "/address": {
        "title": "Address",
        "text": "Where to?",
        "links": ["/cart"],
        "forms": [{"selector": "#addr", "fields": [{"selector": "#street", "name": "street", "type": "text"}]}],
        "buttons": [{"selector": "#help", "text": "Help"}],
    },
    "/done": {"title": "Thanks", "text": "Order placed", "links": ["/"], "forms": [], "buttons": []},
}
NEXT_AFTER_FORM = {"#cart": "/address", "#addr": "/done"}


@dataclass
class FlowSession:
    current: str = "anon"
    base_url: str = "http://127.0.0.1"
    path: str = "/"
    requests: list = field(default_factory=list)
    executed: list = field(default_factory=list)

    def mark(self) -> Mark:
        return Mark(0, 0, 0)

    async def observe(self):
        page = PAGES[self.path]
        return {"identity": self.current, "url": f"http://127.0.0.1{self.path}", **page, "recent_requests": []}

    async def execute(self, action):
        self.executed.append(action)
        if action.kind == "navigate":
            self.path = action.path
        elif action.kind == "fill_form":
            self.path = NEXT_AFTER_FORM.get(action.selector, self.path)
        elif action.kind == "click":
            return ActionResult(True, None, f"http://127.0.0.1{self.path}", [], action)  # does nothing
        return ActionResult(True, None, f"http://127.0.0.1{self.path}", [1], action)

    def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
        return AgentEvidence(base=Evidence())


def _app_data(prompt: str) -> dict[str, Any]:
    body = prompt.split(">>>\n", 1)[1].rsplit("\n<<<END APP DATA", 1)[0]
    return json.loads(body)


class OptionFollower:
    """Fake model with a fixed policy: take the first untried form, else the first untried link, else finish.
    Its choices therefore depend only on what the explorer shows it."""

    name = "option-follower"
    available = True

    def __init__(self):
        self.prompts: list[str] = []

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        self.prompts.append(user)
        data = _app_data(user)
        options = data.get("options", [])
        for prefix in ("F", "L"):
            for opt in options:
                if opt["id"].startswith(prefix) and opt["status"] == "untried":
                    target = opt["do"].split()[1]
                    if prefix == "F":
                        action = ActionSpec(
                            kind="fill_form", selector=target, fields=[FieldSpec(selector="#x", text="1")]
                        )
                    else:
                        action = ActionSpec(kind="navigate", path=target)
                    return LLMResult(True, Decision(thought=opt["id"], hypothesis_id="checkout", action=action))
        return LLMResult(True, Decision(hypothesis_id="checkout", action=ActionSpec(kind="finish", reason="done")))


async def _explore(model, session=None, budget=None, provider=None):
    session = session or FlowSession()
    steps: list[dict] = []
    explorer = Explorer(
        session,
        provider or BudgetedProvider(model, 1, 5),
        STRATEGY.model_copy(deep=True),
        AppMap(base_url="http://127.0.0.1"),
        budget or ExploreBudget(max_steps=12),
        steps.append,
    )
    return await explorer.run(), steps, session, explorer


async def test_what_it_observes_decides_where_it_goes_and_it_completes_the_flow():
    model = OptionFollower()
    result, steps, session, explorer = await _explore(model)
    paths = [a.path for a in session.executed if a.kind == "navigate"]
    forms = [a.selector for a in session.executed if a.kind == "fill_form"]
    assert paths[0] == "/cart" and forms == ["#cart", "#addr"]  # the multi-step journey, in order
    assert session.path in ("/done", "/") and result.stats["distinct_states"] >= 4
    assert all(s["origin"] == "ai" for s in steps)
    # The second prompt shows the new state and the form the first navigation revealed.
    second = _app_data(model.prompts[1])
    assert second["state"]["new"] and any(o["id"] == "F1" for o in second["options"])


async def test_known_states_are_sent_compactly():
    model = OptionFollower()
    await _explore(model)
    revisits = [p for p in model.prompts if not _app_data(p)["state"]["new"]]
    news = [p for p in model.prompts if _app_data(p)["state"]["new"]]
    assert revisits and news
    assert all(_app_data(p)["text"].startswith("(unchanged") for p in revisits)
    assert all("forms" not in _app_data(p) for p in revisits)


async def test_options_carry_their_outcomes_and_dead_ends_are_not_run_again():
    class Clicker(OptionFollower):
        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            self.prompts.append(user)
            return LLMResult(
                True, Decision(hypothesis_id="checkout", action=ActionSpec(kind="click", selector="#help"))
            )

    session = FlowSession(path="/address")
    model = Clicker()
    result, steps, session, _ = await _explore(model, session, ExploreBudget(max_steps=5))
    clicks = [a for a in session.executed if a.kind == "click"]
    assert len(clicks) == 2  # run twice, then refused: it never changed anything
    assert result.stats["known_dead_end_refusals"] >= 1
    assert any(s["error"] and s["error"].startswith("not run: known dead end") for s in steps)
    status = {o["id"]: o["status"] for o in _app_data(model.prompts[2])["options"]}
    assert status["B1"].startswith("dead end")


async def test_a_call_limit_ends_exploration_with_that_reason():
    model = OptionFollower()
    provider = BudgetedProvider(model, 1, 5, max_calls=2)
    result, steps, session, _ = await _explore(model, provider=provider)
    assert len(model.prompts) == 2 and result.stopped == "model use stopped: call limit 2 reached"


async def test_look_shows_detail_once_without_touching_the_app():
    calls = {"n": 0}

    class Looker(OptionFollower):
        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            self.prompts.append(user)
            calls["n"] += 1
            kind = {1: ActionSpec(kind="look", part="requests")}.get(calls["n"], ActionSpec(kind="finish"))
            return LLMResult(True, Decision(hypothesis_id="checkout", action=kind))

    model = Looker()
    _result, steps, session, _ = await _explore(model, budget=ExploreBudget(max_steps=3))
    assert session.executed[0].kind == "finish" and not steps[:-1]  # nothing ran for the look
    assert "requests" in _app_data(model.prompts[1]) and "requests" not in _app_data(model.prompts[0])


async def test_goal_status_moves_the_focus_and_new_goals_are_grounded():
    two = Strategy(
        hypotheses=[Hypothesis(id="a", title="A", targets=["/"]), Hypothesis(id="b", title="B", targets=["/"])]
    )
    replies = iter(
        [
            Decision(
                hypothesis_id="a", action=ActionSpec(kind="wait", ms=1), goal_status="done", new_goal="Try coupons"
            ),
            Decision(hypothesis_id="b", action=ActionSpec(kind="finish")),
            Decision(hypothesis_id="b", action=ActionSpec(kind="finish")),
        ]
    )

    class Scripted(OptionFollower):
        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            self.prompts.append(user)
            return LLMResult(True, next(replies))

    model = Scripted()
    steps: list[dict] = []
    explorer = Explorer(
        FlowSession(),
        BudgetedProvider(model, 1, 5),
        two,
        AppMap(base_url="http://x"),
        ExploreBudget(max_steps=3),
        steps.append,
    )
    result = await explorer.run()
    focus = [json.loads(p.split("\n\n<<<APP DATA")[0].split("\n", 1)[1])["focus_hypothesis"] for p in model.prompts]
    assert focus[:2] == ["a", "b"] and result.stats["goals_done"] == 1 and result.stats["goals_added"] == 1
    added = explorer.strategy.hypotheses[-1]
    assert added.title == "Try coupons" and added.targets == ["/"]  # grounded in the state it came from


def test_state_identity_is_role_session_route_and_structure_not_the_raw_url():
    m = ExplorationMap()
    page = {"identity": "a", "title": "T", "text": "x", "links": ["/x"], "forms": [], "buttons": []}
    s1, new1, _ = m.visit({**page, "url": "http://h/tasks/3"}, 1)
    s2, new2, changed = m.visit({**page, "url": "http://h/tasks/7", "text": "other"}, 2)
    assert new1 and not new2 and s1 is s2 and changed  # same route shape and structure: same state
    s3, new3, _ = m.visit({**page, "identity": "b", "url": "http://h/tasks/3"}, 3)
    assert new3 and s3 is not s1  # another role is another state
    m.cleared.add("a")
    s4, new4, _ = m.visit({**page, "url": "http://h/tasks/3"}, 4)
    assert new4  # same page after the session was cleared: another state
    assert action_sig({"kind": "navigate", "path": "/x"}) == "navigate /x"


def test_finding_origin_separates_ai_discovery_from_sentri_probes():
    idor = [{"oracle_id": "cross_user_exposure", "error_key": "other_user_record", "detail": "", "route": "x"}]
    assert finding_origin(idor, "ai") == "code_probe"  # Sentri's by-id check found it, not the model
    assert finding_origin([{"oracle_id": "csrf_missing", "error_key": "no_csrf_check"}], "ai") == "code_probe"
    assert finding_origin([{"oracle_id": "http_500", "error_key": "500"}], "ai") == "ai"
    assert finding_origin([{"oracle_id": "xss_reflection", "error_key": "x"}], "code_probe") == "code_probe"
    assert finding_origin([], "ai") == "ai_suspicion"
