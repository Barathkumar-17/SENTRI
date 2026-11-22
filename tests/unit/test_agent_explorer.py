from __future__ import annotations

import json
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


async def test_invalid_actions_and_unusable_replies_count_and_burn_steps_visibly():
    result, steps, session = await run(
        [
            d("type", selector="#x", text="a", repeat=0),  # invalid: repeat below 1 -> failure 1, visible step
            "bad json",  # unusable ...
            "bad json",  # ... and its retry -> failure 2, code picks a fallback step
            "bad json",
            "bad json",  # failure 3 -> stop
        ]
    )
    assert result.stopped.startswith("model failed 3 times in a row")
    assert [s["error"].split(":")[0] for s in steps if not s["ok"]] == ["invalid action"]
    assert len(steps) == 2 and result.invalid == 1 and result.fallbacks == 1
    assert [a.kind for a in session.executed] == ["navigate"]  # the deterministic fallback, not the model
    assert steps[1]["thought"].startswith("fallback (model reply unusable")


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


async def test_contract_console_ignores_apply_during_exploration():
    from sentri.evidence.models import ConsoleRecord
    from sentri.verdict.oracles import OracleConfig

    class Noisy(FakeSession):
        def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
            noise = (ConsoleRecord("console_error", "analytics beacon failed"),)
            return AgentEvidence(base=Evidence(console=noise))

    async def explore(cfg):
        steps: list[dict] = []
        explorer = Explorer(
            Noisy(),
            BudgetedProvider(ScriptedModel([d("wait", ms=1), d("finish")]), 1, 5),
            STRATEGY,
            AppMap(base_url="http://127.0.0.1"),
            ExploreBudget(max_steps=3),
            steps.append,
            oracle_cfg=cfg,
        )
        await explorer.run()
        return steps[0]["signals"]

    assert [s["oracle_id"] for s in await explore(None)] == ["console_error"]
    ignored = OracleConfig(flow_id="agentic", attack_id="explore", ignore_console=(r"analytics beacon",))
    assert await explore(ignored) == []


async def test_every_candidate_keeps_the_whole_session_prefix():
    """State-dependent bugs need their setup: nothing is dropped after a flagged break or a focus change."""

    class LeaksTwice(FakeSession):
        def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
            n = len(self.executed)
            path = {3: "/tasks/1", 6: "/admin"}.get(n)
            responses = (ResponseRecord("anon", "GET", path, 200, "<h1>task</h1>"),) if path else ()
            return AgentEvidence(base=Evidence(), responses=responses, protected_paths=("/tasks/:id", "/admin"))

    class FailsOn(LeaksTwice):
        async def execute(self, action):
            r = await super().execute(action)
            if getattr(action, "selector", None) == "#missing":
                return ActionResult(False, "selector not found", r.url, [])
            return r

    steps: list[dict] = []
    decisions = [
        d("switch_user", user="a"),
        d("navigate", path="/tasks/new"),
        d("navigate", path="/tasks/1", hyp="other"),  # break 1, and a hypothesis change
        d("click", selector="#missing"),  # failed and did nothing: not part of the state
        d("type", selector="#t", text="x"),
        d("navigate", path="/admin", hyp="third"),  # break 2
        d("finish"),
    ]
    explorer = Explorer(
        FailsOn(),
        BudgetedProvider(ScriptedModel(decisions), 1, 5),
        STRATEGY,
        AppMap(base_url="http://127.0.0.1"),
        ExploreBudget(max_steps=10),
        steps.append,
    )
    result = await explorer.run()
    first, second = result.candidates
    kinds = lambda c: [(r.action["kind"], r.action.get("path") or r.action.get("selector")) for r in c.repro]  # noqa: E731
    assert kinds(first) == [
        ("switch_user", None),
        ("switch_user", None),
        ("navigate", "/tasks/new"),
        ("navigate", "/tasks/1"),
    ]
    assert kinds(second) == kinds(first) + [("type", "#t"), ("navigate", "/admin")]
    assert all(r.expect_ok for r in second.repro) and "#missing" not in str(second.repro)
    assert (first.from_step, second.from_step) == (3, 6)  # the attack tree still shows lines of attack


class Recording(ScriptedModel):
    def __init__(self, decisions):
        super().__init__(decisions)
        self.prompts: list[str] = []

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        self.prompts.append(user)
        return await super().complete_json(role, system, user, schema, max_tokens)


async def test_one_retry_shows_the_parse_error_then_the_model_continues():
    model = Recording(["schema validation failed (2 errors)", d("navigate", path="/a"), d("finish")])
    steps: list[dict] = []
    explorer = Explorer(
        FakeSession(),
        BudgetedProvider(model, 1, 5),
        STRATEGY,
        AppMap(base_url="http://127.0.0.1"),
        ExploreBudget(max_steps=5),
        steps.append,
    )
    result = await explorer.run()
    assert "could not be used (schema validation failed (2 errors))" in model.prompts[1]
    assert result.fallbacks == 0 and steps[0]["action"] == {"kind": "navigate", "path": "/a"}


def test_fallback_picks_the_next_unexplored_element():
    explorer = Explorer(
        FakeSession(),
        BudgetedProvider(ScriptedModel([]), 1, 5),
        STRATEGY,
        AppMap(base_url="http://x"),
        ExploreBudget(),
        lambda s: None,
    )
    obs = {
        "links": ["/logout", "/seen", "/fresh"],
        "buttons": [{"selector": "#delete", "text": "Delete all"}, {"selector": "#go", "text": "Go"}],
    }
    explorer._visited.add("/seen")
    assert explorer._fallback(obs, None, "x").action.path == "/fresh"  # destructive and visited links skipped
    explorer._visited.add("/fresh")
    assert explorer._fallback(obs, None, "x").action.selector == "#go"
    explorer._clicked.add("#go")
    assert explorer._fallback(obs, None, "x").action.path == "/"


def _focus_of(prompt: str) -> str:
    task = prompt.split(chr(10) * 2 + "<<<APP DATA")[0].split(chr(10), 1)[1]
    return json.loads(task)["focus_hypothesis"]


THREE = Strategy(
    hypotheses=[Hypothesis(id=h, title=h, targets=["/"]) for h in ("h1", "h2", "h3")],
)


async def _explore(session, decisions, budget):
    steps: list[dict] = []
    model = Recording(decisions)
    explorer = Explorer(
        session, BudgetedProvider(model, 1, 5), THREE, AppMap(base_url="http://127.0.0.1"), budget, steps.append
    )
    return await explorer.run(), model


async def test_the_same_action_three_times_with_no_change_rotates_the_focus():
    result, model = await _explore(
        FakeSession(leak_on=99), [d("click", selector="#same")] * 4, ExploreBudget(max_steps=4)
    )
    assert result.stalls == 1
    focus = [_focus_of(p) for p in model.prompts]
    assert focus == ["h1", "h1", "h1", "h2"]
    assert "stalled" in model.prompts[3] and "try something different" in model.prompts[3]


class Moving(FakeSession):
    """Every step shows a new page: the hypothesis is making progress (mid-flow)."""

    async def observe(self):
        return {"identity": self.current, "url": f"http://127.0.0.1/p{len(self.executed)}", "text": "", "links": []}


async def test_focus_rotates_by_progress_with_a_ceiling():
    budget = ExploreBudget(max_steps=26, steps_per_hypothesis=5, max_steps_per_hypothesis=8)
    decisions = [d("navigate", path=f"/p{i}") for i in range(26)]
    _r, model = await _explore(Moving(), decisions, budget)
    focus = [_focus_of(p) for p in model.prompts]
    assert focus[:8] == ["h1"] * 8 and focus[8] == "h2"  # kept past the base 5 while progressing, cut at 8

    still = [d("wait", ms=i + 1) for i in range(12)]  # different actions, nothing changes on the page
    _r, model = await _explore(FakeSession(leak_on=99), still, ExploreBudget(max_steps=12, steps_per_hypothesis=5))
    focus = [_focus_of(p) for p in model.prompts]
    assert focus[:5] == ["h1"] * 5 and focus[5] == "h2"  # no progress: rotated at the base allotment


async def test_refused_actions_count_toward_the_failure_limit():
    class Refusing(FakeSession):
        async def execute(self, action):
            self.executed.append(action)
            return ActionResult(False, "refused: outside the app under test", "http://127.0.0.1/x", [])

    result, _m = await _explore(
        Refusing(), [d("navigate", path=f"https://e{i}.test") for i in range(5)], ExploreBudget(max_steps=5)
    )
    assert result.stopped.startswith("3 unusable or refused actions in a row") and result.steps == 3
