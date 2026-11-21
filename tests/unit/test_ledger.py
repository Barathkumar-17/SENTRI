from __future__ import annotations

import pytest

from sentri.ledger import CheckRecord, HumanDecision, Ledger
from sentri.verdict.engine import CheckVerdict, OracleHit


@pytest.fixture
def ledger(tmp_path):
    lg = Ledger(tmp_path / "ledger.db")
    yield lg
    lg.close()


def run_check(lg: Ledger, commit: str, verdict: str, hits: tuple[OracleHit, ...], cell=("signup", "double_submit")):
    run_id = lg.create_run(commit, "main", "fp", "ch")
    check_id = lg.insert_check(CheckRecord(run_id, cell[0], cell[1], verdict))
    events, score_verdict = lg.record_check_result(
        run_id, check_id, cell[0], cell[1], commit, CheckVerdict(verdict, 3, hits)
    )  # type: ignore[arg-type]
    lg.finish_run(run_id, "finished", None, 1.0, 0.0, "fallback", None)
    return run_id, events, score_verdict


HIT = OracleHit("duplicates", 3, "sig-a", "2 created")
FLAKY_HIT = OracleHit("duplicates", 1, "sig-a", "2 created")


def test_state_machine_open_fixed_regression(ledger):
    _, events, _ = run_check(ledger, "c1", "fail", (HIT,))
    assert [e.kind for e in events] == ["bug_opened"]
    bug = ledger.bugs()[0]
    assert bug["id"] == "BUG-001" and bug["status"] == "open" and bug["first_commit"] == "c1"

    _, events, _ = run_check(ledger, "c2", "pass", ())
    assert [e.kind for e in events] == ["bug_fixed"]
    assert ledger.bug("BUG-001")["status"] == "fixed" and ledger.bug("BUG-001")["fixed_commit"] == "c2"

    run_id, events, _ = run_check(ledger, "c3", "fail", (HIT,))
    assert [e.kind for e in events] == ["regression"]
    bug = ledger.bug("BUG-001")
    assert bug["status"] == "open" and bug["regressions"] == 1 and bug["fixed_commit"] is None
    assert [b["id"] for b in ledger.regressions_in_run(run_id)] == ["BUG-001"]


def test_flaky_transitions(ledger):
    run_check(ledger, "c1", "flaky", (FLAKY_HIT,))
    assert ledger.bug("BUG-001")["status"] == "flaky"
    run_check(ledger, "c2", "fail", (HIT,))
    assert ledger.bug("BUG-001")["status"] == "open"
    run_check(ledger, "c3", "flaky", (FLAKY_HIT,))
    assert ledger.bug("BUG-001")["status"] == "flaky"


def test_intended_is_human_only_and_scores_as_pass(ledger):
    run_check(ledger, "c1", "fail", (HIT,))
    with pytest.raises(ValueError):
        HumanDecision("BUG-001", "intended", "", "alice")
    with pytest.raises(TypeError):
        ledger.mark({"bug_id": "BUG-001", "status": "intended"})  # type: ignore[arg-type]
    ledger.mark(HumanDecision("BUG-001", "intended", "duplicate accounts are merged later", "alice"))
    assert ledger.bug("BUG-001")["status"] == "intended"
    assert ledger.bug_history("BUG-001")[-1]["actor"] == "human"

    _, events, score_verdict = run_check(ledger, "c2", "fail", (HIT,))
    assert events == [] and score_verdict == "pass"
    assert ledger.bug("BUG-001")["status"] == "intended"


def test_intended_behaviour_change_needs_review(ledger):
    run_check(ledger, "c1", "fail", (HIT,))
    ledger.mark(HumanDecision("BUG-001", "intended", "ok", "alice"))
    changed = OracleHit("duplicates", 3, "sig-b", "3 created")
    _, events, score_verdict = run_check(ledger, "c2", "fail", (changed,))
    assert {e.kind for e in events} == {"bug_opened", "bug_needs_review"}
    new = ledger.bug("BUG-002")
    assert new["related_bug_id"] == "BUG-001" and new["needs_review"] == 1
    assert score_verdict == "fail"


def test_reuse_rule(ledger):
    run_check(ledger, "c1", "pass", ())
    assert ledger.find_reusable("signup", "double_submit", "fp", "ch") is not None
    assert ledger.find_reusable("signup", "double_submit", "other-fp", "ch") is None
    assert ledger.find_reusable("signup", "double_submit", "fp", "other") is None
    run_check(ledger, "c2", "fail", (HIT,))
    assert ledger.find_reusable("signup", "double_submit", "fp", "ch") is None


def test_events_and_history(ledger):
    run_id = ledger.create_run("c", "main", "fp", "ch")
    a = ledger.add_event(run_id, "run_started", "hello", None, {"x": 1})
    ledger.add_event(run_id, "action", "click", "because", {})
    rows = ledger.events_after(a, run_id)
    assert [r["kind"] for r in rows] == ["action"] and rows[0]["reason"] == "because"
