from __future__ import annotations

import itertools

import pytest

from sentri.verdict.engine import decide
from sentri.verdict.oracles import ORACLE_IDS, OracleResult
from sentri.verdict.scorer import compute
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
