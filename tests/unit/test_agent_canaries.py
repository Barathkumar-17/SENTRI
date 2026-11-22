"""Code-owned canaries and the direct-by-id cross-user check: no model cooperation needed."""

from __future__ import annotations

import json

from sentri.agent.session import ObservedRequest, created_record, with_canary_in_json
from sentri.evidence.agent_models import AgentEvidence, IdorProbe
from sentri.evidence.models import Evidence
from sentri.verdict.agent_oracles import cross_user_exposure
from sentri.verdict.oracles import OracleConfig

BASE = "http://127.0.0.1:5055"
CANARY = "sentri-canary-a-abc123"
CFG = OracleConfig(flow_id="agentic", attack_id="explore")


def req(**kw) -> ObservedRequest:
    data = {"id": 1, "identity": "a", "method": "POST", "url": BASE + "/api/notes", "status": 201}
    data.update(kw)
    return ObservedRequest(**data)


def test_json_bodies_get_the_canary_in_a_content_field():
    body = with_canary_in_json('{"email": "x@y.z", "title": "Hello", "n": 3}', CANARY)
    assert body is not None and json.loads(body) == {"email": "x@y.z", "title": f"Hello {CANARY}", "n": 3}
    assert with_canary_in_json('{"n": 3}', CANARY) is None  # nothing content-like: left exactly as the model sent
    assert with_canary_in_json("not json", CANARY) is None


def test_a_create_is_recognised_from_its_json_response():
    rec = req(
        body=json.dumps({"title": f"hi {CANARY}"}),
        response_type="application/json",
        response_text=json.dumps({"status": "ok", "data": {"id": 42, "title": f"hi {CANARY}"}}),
    )
    found = created_record(rec, CANARY, BASE)
    assert found is not None and found.record_id == "42" and found.endpoint == "/api/notes"
    assert found.urls == ["/api/notes/42"]


def test_a_create_is_recognised_from_its_location_and_form_actions():
    rec = req(url=BASE + "/tasks/new", status=302, location="/tasks/7", body=f"title=x+{CANARY}")
    found = created_record(rec, CANARY, BASE)
    assert found is not None and found.record_id == "7" and found.urls == ["/tasks/7", "/tasks/new/7"]


def test_not_a_create():
    assert created_record(req(body='{"title": "no canary"}', response_text='{"id": 1}'), CANARY, BASE) is None
    assert created_record(req(method="GET", body=CANARY), CANARY, BASE) is None
    assert created_record(req(status=500, body=CANARY, response_text='{"id": 1}'), CANARY, BASE) is None
    off_site = req(status=302, body=CANARY, location="https://elsewhere.test/items/5")
    assert created_record(off_site, CANARY, BASE) is None


def _probe(**kw) -> AgentEvidence:
    data = {
        "owner": "a",
        "other": "b",
        "path": "/api/notes/42",
        "owner_status": 200,
        "owner_sees": True,
        "anon_status": 401,
        "anon_sees": False,
        "other_status": 200,
        "other_sees": True,
    }
    data.update(kw)
    return AgentEvidence(base=Evidence(), idor_probes=(IdorProbe(**data),))


def test_other_user_reading_a_record_by_id_is_exposure():
    r = cross_user_exposure(_probe(), CFG)
    assert r.fired and r.route == "GET /api/notes/:id" and "user b opened user a's record" in r.detail


def test_public_or_refused_records_are_not_exposure():
    assert not cross_user_exposure(_probe(anon_sees=True, anon_status=200), CFG).fired  # public content
    assert not cross_user_exposure(_probe(other_status=404, other_sees=False), CFG).fired  # properly refused
    assert not cross_user_exposure(_probe(other_status=403, other_sees=True), CFG).fired  # error page echo
    assert not cross_user_exposure(_probe(owner_sees=False), CFG).fired  # not the record's URL
