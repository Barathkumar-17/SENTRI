from __future__ import annotations

from sentri.evidence.agent_models import AgentEvidence, CountProbe, HealthProbe, ResponseRecord
from sentri.evidence.models import Evidence
from sentri.verdict import agent_oracles as ao
from sentri.verdict.engine import decide
from sentri.verdict.oracles import OracleConfig

CFG = OracleConfig(flow_id="agentic", attack_id="h1")
A_SECRET = "sentri-canary-a-x1"


def ev(**kw) -> AgentEvidence:
    return AgentEvidence(
        base=Evidence(), protected_paths=("/tasks", "/tasks/:id"), private_markers={"a": (A_SECRET,)}, **kw
    )


def resp(identity, path, status=200, text="", method="GET", redirected_to=None):
    return ResponseRecord(identity, method, path, status, text, redirected_to)


def test_auth_bypass_protected_page_served_to_anon():
    hit = ao.auth_bypass(ev(responses=(resp("anon", "/tasks/7", text="<h1>Task 7</h1>"),)), CFG)
    assert hit.fired and hit.route == "GET /tasks/:id" and hit.error_key == "protected_page"


def test_auth_bypass_ignores_correct_behaviour():
    login_page = resp("anon", "/tasks", text='<form><input type="password"></form>')
    redirected = resp("anon", "/tasks", status=200, redirected_to="/login")
    refused = resp("anon", "/tasks", status=401)
    public = resp("anon", "/comments", text="hello")
    logged_in = resp("a", "/tasks", text="my tasks")
    for r in (login_page, redirected, refused, public, logged_in):
        assert not ao.auth_bypass(ev(responses=(r,)), CFG).fired, r


def test_auth_bypass_private_data_leak():
    hit = ao.auth_bypass(ev(responses=(resp("anon", "/api/export", text=f"rows: {A_SECRET}"),)), CFG)
    assert hit.fired and hit.error_key == "private_data"


def test_cross_user_exposure_only_for_private_markers():
    leak = ao.cross_user_exposure(ev(responses=(resp("b", "/tasks", text=f"<li>{A_SECRET}</li>"),)), CFG)
    assert leak.fired and "user b received user a" in leak.detail
    own = ao.cross_user_exposure(ev(responses=(resp("a", "/tasks", text=A_SECRET),)), CFG)
    assert not own.fired  # your own data is fine
    public = AgentEvidence(base=Evidence(), private_markers={}, responses=(resp("b", "/comments", text=A_SECRET),))
    assert not ao.cross_user_exposure(public, CFG).fired  # values posted publicly are not private


def test_replay_duplicate():
    assert ao.replay_duplicate(ev(count_probes=(CountProbe("tok", "/tasks", 0, 3),)), CFG).fired
    assert not ao.replay_duplicate(ev(count_probes=(CountProbe("tok", "/tasks", 0, 1),)), CFG).fired


def test_state_corruption():
    broken = HealthProbe("/tasks", "a", 200, 500)
    assert ao.state_corruption(ev(health_probes=(broken,)), CFG).fired
    noisy = HealthProbe("/tasks", "a", 200, 200, errors_before=0, errors_after=2)
    assert ao.state_corruption(ev(health_probes=(noisy,)), CFG).fired
    already_broken = HealthProbe("/tasks", "a", 500, 500)
    assert not ao.state_corruption(ev(health_probes=(already_broken,)), CFG).fired
    fine = HealthProbe("/tasks", "a", 200, 200)
    assert not ao.state_corruption(ev(health_probes=(fine,)), CFG).fired


def test_evaluate_agent_feeds_the_same_verdict_engine():
    leak = ev(responses=(resp("anon", "/tasks", text="list"),))
    attempts = [ao.evaluate_agent(leak, CFG) for _ in range(3)]
    verdict = decide("agentic", "h1", attempts)
    assert verdict.verdict == "fail" and [h.oracle_id for h in verdict.hits] == ["auth_bypass"]
    assert len(attempts[0]) == 9  # five universal oracles + four agentic ones
