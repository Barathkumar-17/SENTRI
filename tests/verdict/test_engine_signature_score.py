from __future__ import annotations

import itertools

import pytest

from sentri.verdict.engine import decide
from sentri.verdict.oracles import ORACLE_IDS, OracleResult
from sentri.verdict.scorer import AgenticFinding, compute, parse_hits
from sentri.verdict.signature import normalize_error, normalize_path, normalize_route, signature


def attempt(fired: set[str], key: str = "k") -> list[OracleResult]:
    return [OracleResult(o, o in fired, "d", None, key) for o in ORACLE_IDS]


@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_single_oracle_k(k):
    attempts = [attempt({"http_500"} if i < k else set()) for i in range(3)]
    v = decide("f", "a", attempts)
    assert v.verdict == {0: "pass", 1: "flaky", 2: "flaky", 3: "fail"}[k]
    assert v.oracle_hits == ("" if k == 0 else f"http_500:{k}")


def test_all_k_combinations_for_two_oracles():
    for k1, k2 in itertools.product(range(4), repeat=2):
        attempts = [attempt({o for o, k in (("http_500", k1), ("timeout", k2)) if i < k}) for i in range(3)]
        v = decide("f", "a", attempts).verdict
        expected = "fail" if 3 in (k1, k2) else ("flaky" if k1 or k2 else "pass")
        assert v == expected, (k1, k2)


def test_one_bug_per_signature():
    attempts = [attempt({"exception"}, key="a"), attempt({"exception"}, key="b"), attempt({"exception"}, key="a")]
    v = decide("f", "x", attempts)
    assert v.verdict == "fail"  # the oracle fired in 3 of 3
    assert sorted(h.k for h in v.hits) == [1, 2]  # but as two distinct bugs


def test_signature_normalization():
    assert normalize_path("http://h/tasks/42/files/550e8400-e29b-41d4-a716-446655440000?x=1") == "/tasks/:id/files/:id"
    assert normalize_route("post", "http://h/a/deadbeefdeadbeef") == "POST /a/:id"
    a = normalize_error("KeyError: 'user_17' at 0x7f00ab line 93")
    b = normalize_error("KeyError: 'user_99' at 0x7fffff line 12")
    assert a == b
    assert normalize_error("ValueError: x") != normalize_error("KeyError: x")
    assert signature("o", "f", "a", "r", "e") == signature("o", "f", "a", "r", "e")
    assert len(signature("o", "f", "a", None, None)) == 16


def test_score_examples():
    b = compute(["pass"] * 13 + ["flaky"] * 2 + ["fail"] * 3 + ["na"] * 4, regressed=1)
    assert b.applicable == 18 and b.score == round(100 * 14 / 18 - 5)
    assert b.formula.endswith(f"= {b.score}")
    assert compute(["na", "error"], 0).score is None
    assert compute(["fail"], regressed=3).score == 0  # clamped
    assert compute(["pass", "pass"], 0).score == 100
    assert compute(["pass", "error"], 0).applicable == 1


# ---- agentic impact -----------------------------------------------------------------------------------


def _f(key: str, hits: str, verdict: str = "fail", attempts: int = 3) -> AgenticFinding:
    return AgenticFinding(key, parse_hits(hits), attempts, verdict)


def test_confirmed_agentic_findings_subtract_by_severity():
    findings = [
        _f("cross_user_exposure GET /tasks", "cross_user_exposure:3"),  # data exposure -15
        _f("state_corruption GET /", "state_corruption:3"),  # -10
        _f("http_500 POST /api/x", "http_500:3"),  # crash -5
        _f("console_error", "console_error:3"),  # minor -2
    ]
    b = compute(["pass"] * 4, 0, agentic=findings)
    assert b.baseline == 100 and b.agentic_impact == 32 and b.score == 68
    assert b.agentic_counts == {"data_exposure": 1, "state_corruption": 1, "crash": 1, "minor": 1}
    assert "agentic 32 (1 data exposure, 1 state corruption, 1 crash, 1 minor)" in b.formula
    assert b.formula.endswith("= 68")


def test_worst_oracle_decides_and_partial_reproduction_costs_half():
    # http_500 in all 3 attempts (-5) but the data leak only once (-7.5): the leak still decides.
    mixed = _f("auth_bypass GET /admin", "http_500:3,auth_bypass:1")
    assert compute(["pass"], 0, agentic=[mixed]).agentic_impact == 8  # round(7.5)
    flaky = _f("cross_user_exposure GET /t", "cross_user_exposure:2", verdict="flaky")
    assert compute(["pass"], 0, agentic=[flaky]).agentic_findings[0]["points"] == 7.5


def test_intended_or_unconfirmed_findings_do_not_affect_the_score():
    intended = _f("auth_bypass GET /public", "auth_bypass:3", verdict="pass")  # a human marked it intended
    b = compute(["pass", "fail"], 0, agentic=[intended])
    assert b.score == b.baseline == 50 and b.agentic_impact == 0 and "agentic" not in b.formula


def test_the_same_finding_counts_once_and_the_score_is_clamped():
    twice = [_f("cross_user_exposure GET /t", "cross_user_exposure:3")] * 2
    assert compute(["pass"], 0, agentic=twice).agentic_impact == 15
    many = [_f(f"auth_bypass GET /{i}", "auth_bypass:3") for i in range(10)]
    assert compute(["pass"], 0, agentic=many).score == 0


def test_agentic_only_contract_is_still_scored():
    assert compute(["na"], 0).score is None  # nothing ran at all
    b = compute(["na"], 0, agentic=[_f("http_500 POST /api", "http_500:3")], agentic_ran=True)
    assert b.score == 95 and b.baseline is None
    assert compute([], 0, agentic_ran=True).score == 100


def test_unreplayable_attempts_never_pass_and_cap_at_flaky():
    from sentri.verdict.engine import decide_replays
    from sentri.verdict.oracles import OracleResult

    hit = [OracleResult("cross_user_exposure", True, "leak", "GET /t")]
    quiet: list = []
    assert decide_replays("agentic", "k", [hit, hit, hit]).verdict == "fail"
    assert decide_replays("agentic", "k", [quiet, quiet, quiet]).verdict == "pass"
    assert decide_replays("agentic", "k", [hit, None, hit]).verdict == "flaky"
    assert decide_replays("agentic", "k", [quiet, None, quiet]).verdict == "error"  # inconclusive, not fixed
    assert decide_replays("agentic", "k", [None, None, None]).verdict == "error"
