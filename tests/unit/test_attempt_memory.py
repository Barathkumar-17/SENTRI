"""Run-wide attempt memory: a changed URL or a renamed hypothesis must not erase what already failed.

SCRIPTED MODEL / FAKE SESSION: these are cheap regressions for the orchestration rules (flow-shop run b6db7b8 sent the
same logged-out add-to-cart five times from different pages and hypotheses, each ending on the login page). They are
not evidence that a real model explores well; autonomous behaviour is validated by live runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sentri.agent.attempts import AttemptMemory, auth_block, operation_after, operation_before
from sentri.agent.explorer import ActionSpec, Decision, ExploreBudget, Explorer
from sentri.agent.recon import AppMap
from sentri.agent.session import ActionResult, Mark
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.evidence.agent_models import AgentEvidence
from sentri.evidence.models import Evidence
from sentri.llm import BudgetedProvider, LLMResult

# ---- the rules, on their own ---------------------------------------------------------------------------------


def test_the_same_operation_from_different_pages_is_one_operation():
    post_cart = {"kind": "request", "method": "POST", "path": "/cart/add"}
    a = operation_before(post_cart, "http://127.0.0.1/product/1")
    b = operation_before(post_cart, "http://127.0.0.1/cart")
    assert a == b == "POST /cart/add"  # keyed by what it does, not by where it started
    form = {"kind": "fill_form", "selector": "form"}
    assert operation_before(form, "http://x/product/1") == operation_before(form, "http://x/product/1")
    assert operation_before({"kind": "burst", "selector": "#b"}, "http://x/") is None  # repeating is the point
    assert operation_before({"kind": "switch_user", "user": "a"}, "http://x/") is None


def _tries(mem: AttemptMemory, n: int, identity="anon", intent="POST /cart/add", outcome="-> /login", **kw):
    for _ in range(n):
        mem.record(identity, intent, intent, outcome, new_state=False, signals=0, wrote=False, **kw)


def test_an_evidence_free_repeat_is_refused_after_two_tries_whatever_the_hypothesis():
    mem = AttemptMemory()
    _tries(mem, 1)
    assert mem.check("anon", "POST /cart/add") is None
    _tries(mem, 1)
    why = mem.check("anon", "POST /cart/add")
    assert why is not None and "POST /cart/add as anon" in why and "-> /login" in why and "nothing changed" in why
    assert mem.check("anon", "GET /orders") is None  # a different operation is untouched


def test_the_first_try_and_a_new_outcome_always_count_as_evidence():
    mem = AttemptMemory()
    first = mem.record("anon", "POST /x", "POST /x", "-> /login", new_state=False, signals=0, wrote=False)
    again = mem.record("anon", "POST /x", "POST /x", "-> /login", new_state=False, signals=0, wrote=False)
    other = mem.record("anon", "POST /x", "POST /x", "500 -> /x", new_state=False, signals=0, wrote=False)
    assert first and not again and other  # a new outcome for the same operation is evidence


def test_a_justified_retry_is_allowed_once_and_a_changed_world_reopens_it():
    mem = AttemptMemory()
    _tries(mem, 2)
    assert mem.check("anon", "POST /cart/add") is not None
    assert mem.check("anon", "POST /cart/add", repeat_reason="testing double submission on purpose") is None
    _tries(mem, 1, justified=True)  # that deliberate repeat ran; another one is refused again
    assert mem.check("anon", "POST /cart/add", repeat_reason="still testing") is not None
    mem.identity_changed()  # the app's world changed (another identity acted): a retry can add evidence
    assert mem.check("anon", "POST /cart/add") is None


def test_a_successful_write_changes_the_world_for_every_identity():
    mem = AttemptMemory()
    _tries(mem, 2, identity="a", outcome="200 -> /cart")
    assert mem.check("a", "POST /cart/add") is not None
    mem.record("b", "POST /orders", "POST /orders", "302 -> /orders/7", new_state=True, signals=0, wrote=True)
    assert mem.check("a", "POST /cart/add") is None  # something real changed since the last try


def test_the_same_operation_as_another_identity_is_a_new_experiment():
    mem = AttemptMemory()
    _tries(mem, 3)
    assert mem.check("anon", "POST /cart/add") is not None
    assert mem.check("a", "POST /cart/add") is None  # identity is part of the key


def test_run_wide_progress_counts_steps_without_evidence_and_resets_on_evidence():
    mem = AttemptMemory()
    _tries(mem, 3)
    assert mem.unproductive == 2  # the first try was new outcome = evidence
    mem.record("anon", "GET /new", "GET /new", "-> /new", new_state=True, signals=0, wrote=False)
    assert mem.unproductive == 0


def test_auth_facts_come_from_what_was_observed():
    login = {"url": "http://x/login", "forms": [{"fields": [{"type": "email"}, {"type": "password"}]}]}
    denied = SimpleNamespace(method="POST", url="http://x/cart/add", status=403)
    assert "HTTP 403" in (auth_block([denied], {"url": "http://x/"}, None, "http://x/") or "")
    # sent to a login page it did not ask for: blocked
    assert "login form at /login" in (auth_block([], login, "/cart", "http://x/product/1") or "")
    # opening the login page on purpose, or already being there, is not a block
    assert auth_block([], login, "/login", "http://x/") is None
    assert auth_block([], login, None, "http://x/login") is None  # already there, nothing sent
    posted = SimpleNamespace(method="POST", url="http://x/cart/add", status=302)
    assert "login form at /login" in (auth_block([posted], login, None, "http://x/login") or "")  # sent, bounced back
    assert auth_block([], {"url": "http://x/cart", "forms": []}, "/cart", "http://x/") is None


def test_the_operation_actually_hit_names_the_request_not_the_form():
    reqs = [
        SimpleNamespace(method="GET", url="http://127.0.0.1:5088/product/1", status=200),
        SimpleNamespace(method="POST", url="http://127.0.0.1:5088/cart/add", status=302),
    ]
    assert operation_after("fill_form form @ /product/1", reqs, "http://127.0.0.1:5088") == "POST /cart/add"
    assert operation_after("click #x @ /", [], "http://127.0.0.1:5088") == "click #x @ /"


# ---- the explorer, with a login wall ----------------------------------------------------------------------------

STRATEGY = Strategy(
    hypotheses=[
        Hypothesis(id="anon_cart", title="Add to cart logged out", targets=["/product/1"]),
        Hypothesis(id="cart_totals", title="Cart totals", targets=["/cart"]),
        Hypothesis(id="checkout", title="Checkout", targets=["/checkout"]),
    ]
)

PAGES: dict[str, dict[str, Any]] = {
    "/": {"title": "Shop", "text": "Shop", "links": ["/product/1", "/product/2", "/cart"], "forms": [], "buttons": []},
    "/product/1": {"title": "P1", "text": "Mug", "links": ["/"], "forms": [], "buttons": []},
    "/product/2": {"title": "P2", "text": "Pen", "links": ["/"], "forms": [], "buttons": []},
    "/cart": {"title": "Cart", "text": "Your cart", "links": ["/"], "forms": [], "buttons": []},
    "/login": {
        "title": "Log in",
        "text": "Please log in",
        "links": ["/"],
        "forms": [{"selector": "#login", "fields": [{"selector": "#pw", "name": "pw", "type": "password"}]}],
        "buttons": [],
    },
}


@dataclass
class WallSession:
    """Anonymous visitors are bounced to /login on every cart write; users a and b can write. A login can be made to
    fail, as the real session does when a contract's login flow does not succeed."""

    current: str = "anon"
    base_url: str = "http://127.0.0.1"
    path: str = "/"
    requests: list = field(default_factory=list)
    executed: list = field(default_factory=list)
    login_failed: dict = field(default_factory=dict)
    fail_logins: bool = False
    cart: int = 0

    def mark(self) -> Mark:
        return Mark(0, 0, 0)

    async def observe(self):
        page = PAGES[self.path]
        return {"identity": self.current, "url": f"http://127.0.0.1{self.path}", **page, "recent_requests": []}

    def _req(self, method: str, path: str, status: int):
        r = SimpleNamespace(
            id=len(self.requests) + 1,
            method=method,
            url=f"http://127.0.0.1{path}",
            status=status,
            identity=self.current,
        )
        self.requests.append(r)
        return r.id

    async def execute(self, action):
        self.executed.append((self.current, action))
        if action.kind == "switch_user":
            if action.user != "anon" and self.fail_logins:
                self.login_failed[action.user] = "login flow did not succeed: text 'Shop' not visible"
                return ActionResult(False, f"refused: login as {action.user} failed", None, [], action)
            self.current = action.user
            return ActionResult(True, None, f"http://127.0.0.1{self.path}", [], action)
        if action.kind == "navigate":
            self.path = action.path
            return ActionResult(
                True, None, f"http://127.0.0.1{self.path}", [self._req("GET", action.path, 200)], action
            )
        if action.kind in ("request", "fill_form"):
            if self.current == "anon":
                self.path = "/login"
                rid = self._req("POST", "/cart/add", 302)
                return ActionResult(True, None, "http://127.0.0.1/login", [rid], action)
            self.cart += 1
            self.path = "/cart"
            rid = self._req("POST", "/cart/add", 302)
            return ActionResult(True, None, "http://127.0.0.1/cart", [rid], action)
        return ActionResult(True, None, f"http://127.0.0.1{self.path}", [], action)

    def agent_evidence_since(self, mark, count_probes=(), health_probes=()):
        return AgentEvidence(base=Evidence())


class Scripted:
    """Fake model that plays a fixed list of decisions, then finishes. It records what it was shown."""

    name = "scripted"
    available = True

    def __init__(self, decisions: list[Decision]):
        self.decisions = list(decisions)
        self.prompts: list[str] = []

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        self.prompts.append(user)
        if self.decisions:
            return LLMResult(True, self.decisions.pop(0))
        return LLMResult(True, Decision(hypothesis_id="checkout", action=ActionSpec(kind="finish", reason="done")))


def _add_to_cart(hyp: str, **extra) -> Decision:
    return Decision(
        hypothesis_id=hyp,
        action=ActionSpec(kind="request", method="POST", path="/cart/add", content_type="application/json"),
        **extra,
    )


async def _explore(model, session=None, budget=None, login=True):
    session = session or WallSession()
    steps: list[dict] = []
    explorer = Explorer(
        session,
        BudgetedProvider(model, 1.0, 60.0),
        STRATEGY.model_copy(deep=True),
        AppMap(base_url="http://127.0.0.1", has_login_flow=login),
        budget or ExploreBudget(max_steps=14),
        steps.append,
    )
    return await explorer.run(), steps, session, explorer


def _navigate(path: str, hyp: str) -> Decision:
    return Decision(hypothesis_id=hyp, action=ActionSpec(kind="navigate", path=path))


async def test_a_login_loop_across_pages_and_hypotheses_is_cut_off_and_the_model_is_told_the_facts():
    model = Scripted(
        [
            _add_to_cart("anon_cart"),
            _navigate("/product/2", "anon_cart"),  # a different page ...
            _add_to_cart("cart_totals"),  # ... and a renamed hypothesis: the history must survive both
            _navigate("/cart", "checkout"),
            _add_to_cart("checkout"),  # third logged-out POST /cart/add: refused, not run
        ]
    )
    result, steps, session, _ = await _explore(model)
    ran = [a for who, a in session.executed if a.kind == "request"]
    assert len(ran) == 2  # two real tries; the third never reached the app
    refused = [s for s in steps if "nothing changed since" in (s.get("error") or "")]
    assert len(refused) >= 1 and "POST /cart/add as anon" in refused[0]["error"]
    assert result.stats["repeat_refusals"] >= 1
    # the model was told, factually, what blocked it and which identities it may use
    shown = " ".join(model.prompts)
    assert '"blocked":"ended on a login form at /login"' in shown.replace(" ", "") or "login form at /login" in shown
    assert '"authorized_identities_available"' in shown and '"a"' in shown and '"b"' in shown


async def test_switching_identity_reopens_the_same_operation_as_a_new_experiment():
    model = Scripted(
        [
            _add_to_cart("anon_cart"),
            _add_to_cart("anon_cart"),
            Decision(hypothesis_id="anon_cart", action=ActionSpec(kind="switch_user", user="a")),
            _add_to_cart("cart_totals"),  # as a: allowed, and it works
        ]
    )
    _, steps, session, _ = await _explore(model)
    by_a = [a for who, a in session.executed if who == "a" and a.kind == "request"]
    assert len(by_a) == 1 and session.cart == 1
    assert not any("nothing changed since" in (s.get("error") or "") for s in steps)


async def test_a_deliberate_repeat_with_a_stated_reason_is_not_refused_once():
    model = Scripted(
        [
            _add_to_cart("anon_cart"),
            _add_to_cart("anon_cart"),
            _add_to_cart("anon_cart", repeat_reason="checking whether the second POST answers differently"),
        ]
    )
    _, steps, session, _ = await _explore(model)
    assert len([1 for _, a in session.executed if a.kind == "request"]) == 3
    assert not any("nothing changed since" in (s.get("error") or "") for s in steps)


async def test_retries_that_add_nothing_end_the_run_honestly_and_restarts_cannot_reset_the_budget():
    # the model never stops asking; hypotheses rotate underneath it
    decisions = [_add_to_cart(h) for h in ("anon_cart", "cart_totals", "checkout") * 6]
    result, steps, session, _ = await _explore(
        Scripted(decisions), budget=ExploreBudget(max_steps=40, no_progress_warn=3, no_progress_stop=6)
    )
    assert result.stopped.startswith("no progress:") and "POST /cart/add as anon" in result.stopped
    assert len([1 for _, a in session.executed if a.kind == "request"]) == 2  # never more than the allowance
    assert result.steps < 40  # stopped well before the step budget, not at it


async def test_the_model_is_warned_before_the_run_stops_for_lack_of_progress():
    model = Scripted([_add_to_cart("anon_cart") for _ in range(12)])
    await _explore(model, budget=ExploreBudget(max_steps=30, no_progress_warn=3, no_progress_stop=8))
    assert any('"progress"' in p and "added no new evidence" in p for p in model.prompts)


async def test_a_failed_login_is_not_offered_as_a_working_identity_and_never_retried():
    session = WallSession(fail_logins=True)
    model = Scripted(
        [
            Decision(hypothesis_id="anon_cart", action=ActionSpec(kind="switch_user", user="a")),
            _navigate("/product/1", "anon_cart"),
        ]
    )
    result, steps, session, _ = await _explore(model, session=session)
    assert steps[0]["ok"] is False and "login as a failed" in (steps[0]["error"] or "")
    assert session.current == "anon"  # the failed switch did not leave the session half logged in
    assert "a" in session.login_failed
    shown = " ".join(model.prompts[1:])
    assert "login failed" in shown  # the option says so instead of offering a working account a


# ---- option ids: valid only for the observation they were shown with -----------------------------------------


def _decision(kind: str, hyp: str = "anon_cart", **fields) -> Decision:
    return Decision(hypothesis_id=hyp, action=ActionSpec(kind=kind, **fields))


async def test_an_option_id_means_what_the_current_observation_says():
    # on "/" the link labels are L1=/product/1, L2=/product/2, L3=/cart; the model uses L2, then (on the product
    # page, where L1 is "/") L1 again: each id is read against the page it was just shown with.
    model = Scripted([_decision("navigate", path="L2"), _decision("navigate", path="L1")])
    _, steps, session, _ = await _explore(model)
    paths = [a.path for _, a in session.executed if a.kind == "navigate"]
    assert paths[:2] == ["/product/2", "/"]
    assert steps[0]["ok"] and steps[1]["ok"] and steps[0]["origin"] == "ai"


async def test_a_stale_or_unknown_option_id_is_refused_at_once_and_never_guessed():
    # L9 never existed; B1 existed on no page here. Neither may be run, nor mapped to some other option.
    model = Scripted([_decision("click", selector="L9"), _decision("click", selector="B1")])
    result, steps, session, _ = await _explore(model)
    assert [a for _, a in session.executed if a.kind == "click"] == []  # nothing reached the app
    assert steps[0]["ok"] is False and "option L9 is not offered in this state" in steps[0]["error"]
    assert steps[1]["ok"] is False and "option B1" in steps[1]["error"]
    assert result.stats["unknown_option_refusals"] == 2


async def test_a_full_action_is_never_remapped_even_when_it_resembles_an_option():
    # real selectors and paths pass through unchanged: '#L1', '/L1' and 'a.L1' are not labels
    PAGES["/L1"] = {"title": "L1", "text": "a page really named L1", "links": ["/"], "forms": [], "buttons": []}
    model = Scripted(
        [_decision("click", selector="#L1"), _decision("navigate", path="/L1"), _decision("click", selector="a.L1")]
    )
    _, steps, session, _ = await _explore(model)
    PAGES.pop("/L1")
    ran = [(a.kind, getattr(a, "selector", None) or getattr(a, "path", None)) for _, a in session.executed]
    assert ("click", "#L1") in ran and ("navigate", "/L1") in ran and ("click", "a.L1") in ran
    assert not any("not offered" in (s.get("error") or "") for s in steps)


def test_identical_parameters_are_refused_sooner_than_input_variants():
    mem = AttemptMemory()
    _tries(mem, 2, params='{"body": "a=1"}')
    assert "with these parameters" in (mem.check("anon", "POST /cart/add", '{"body": "a=1"}') or "")
    assert mem.check("anon", "POST /cart/add", '{"body": "a=2"}') is None  # a different input is a new experiment
    _tries(mem, 1, params='{"body": "a=2"}')
    why = mem.check("anon", "POST /cart/add", '{"body": "a=3"}')
    assert why is not None and "with different parameters" in why  # but variants that change nothing are bounded


async def test_when_a_limit_refuses_the_next_call_exploration_ends_with_that_reason_and_no_fallback_steps():
    """Found by the 2026-11-23 flow-shop run: token-cap refusals ran code fallback steps and were reported as the
    model failing."""

    class Capped:
        name = "capped"
        available = True

        def __init__(self):
            self.n = 0

        async def complete_json(self, role, system, user, schema, max_tokens=4000):
            self.n += 1
            return LLMResult(True, _navigate("/product/1", "anon_cart"), input_tokens=1400, output_tokens=100)

    session = WallSession()
    steps: list[dict] = []
    explorer = Explorer(
        session,
        BudgetedProvider(Capped(), 1.0, 60.0, max_tokens=3200),  # one call fits, the second is refused
        STRATEGY.model_copy(deep=True),
        AppMap(base_url="http://127.0.0.1", has_login_flow=True),
        ExploreBudget(max_steps=10),
        steps.append,
    )
    result = await explorer.run()
    assert result.stopped == "model use stopped: token limit 3200 reached"
    assert result.fallbacks == 0 and all(s["origin"] == "ai" for s in steps)
    assert len(steps) == 1  # nothing ran after the cap
