"""Readiness score (docs/reference/oracles-and-verdicts.md section 5)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreBreakdown:
    score: int | None
    passed: int
    flaky: int
    failed: int
    applicable: int
    na: int
    errors: int
    regressed: int
    penalty_per_regression: int
    formula: str


def compute(verdicts: Iterable[str], regressed: int, penalty: int = 5) -> ScoreBreakdown:
    vs = list(verdicts)
    passed = vs.count("pass")
    flaky = vs.count("flaky")
    failed = vs.count("fail")
    na = vs.count("na")
    errors = vs.count("error")
    applicable = passed + flaky + failed
    if applicable == 0:
        return ScoreBreakdown(None, 0, 0, 0, 0, na, errors, regressed, penalty, "no applicable checks")
    base = 100 * (passed + 0.5 * flaky) / applicable
    score = max(0, round(base - penalty * regressed))
    formula = f"score = 100 x ({passed} + 0.5 x {flaky}) / {applicable}"
    if regressed:
        formula += f" - {penalty} x {regressed}"
    formula += f" = {score}"
    return ScoreBreakdown(score, passed, flaky, failed, applicable, na, errors, regressed, penalty, formula)
