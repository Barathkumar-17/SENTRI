"""Readiness score (docs/reference/oracles-and-verdicts.md section 5).

The score has two parts: the baseline suite (comparable across commits) and the agentic impact, a deduction
for every agentic finding confirmed by replay, weighted by how severe the worst oracle it tripped is.
Unconfirmed candidates never reach this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

# Severity classes and what one confirmed finding (k == attempts) costs. A finding that reproduced in only some
# attempts (flaky) costs half.
SEVERITY_POINTS = {"data_exposure": 15, "state_corruption": 10, "crash": 5, "minor": 2}
ORACLE_SEVERITY = {
    "auth_bypass": "data_exposure",
    "cross_user_exposure": "data_exposure",
    "xss_reflection": "data_exposure",
    "state_corruption": "state_corruption",
    "replay_duplicate": "state_corruption",
    "duplicates": "state_corruption",
    "csrf_missing": "state_corruption",
    "http_500": "crash",
    "exception": "crash",
    "timeout": "crash",
    "console_error": "minor",
}
SEVERITY_LABEL = {
    "data_exposure": "data exposure",
    "state_corruption": "state corruption",
    "crash": "crash",
    "minor": "minor",
}


@dataclass(frozen=True)
class AgenticFinding:
    """One confirmed agentic check: its stable key, oracle hits as (oracle_id, k), attempts, score verdict."""

    key: str
    hits: tuple[tuple[str, int], ...]
    attempts: int
    verdict: str  # score verdict: fail | flaky | pass (pass = every bug behind it marked intended by a human)


@dataclass(frozen=True)
class FindingImpact:
    key: str
    severity: str
    oracle_id: str
    points: float


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
    baseline: int | None = None  # the baseline suite alone, regressions included
    agentic_impact: int = 0  # points deducted for confirmed agentic findings
    agentic_counts: dict[str, int] = field(default_factory=dict)  # severity -> confirmed findings
    agentic_findings: list[dict[str, object]] = field(default_factory=list)  # per finding: key, severity, points
    # "scored": the number describes the baseline checks that ran (minus confirmed agentic impact).
    # "insufficient_coverage": no applicable baseline check ran, so there is no number at all; findings and
    # measured coverage are still reported. Exploration can lower a score but never create one.
    status: str = "scored"
    reused: int = 0


def parse_hits(oracle_hits: str | None) -> tuple[tuple[str, int], ...]:
    """`CheckVerdict.oracle_hits` ("auth_bypass:3,http_500:1") back into pairs."""
    out = []
    for part in (oracle_hits or "").split(","):
        oracle_id, _, k = part.strip().partition(":")
        if oracle_id:
            out.append((oracle_id, int(k) if k.isdigit() else 1))
    return tuple(out)


def finding_impact(f: AgenticFinding) -> FindingImpact | None:
    """The most expensive oracle hit decides; a hit seen in every attempt costs full points, otherwise half."""
    if f.verdict not in ("fail", "flaky"):
        return None
    best: FindingImpact | None = None
    for oracle_id, k in f.hits:
        severity = ORACLE_SEVERITY.get(oracle_id, "minor")
        points = SEVERITY_POINTS[severity] * (1.0 if f.attempts and k >= f.attempts else 0.5)
        if best is None or points > best.points:
            best = FindingImpact(f.key, severity, oracle_id, points)
    if best is None:  # a failing check always has hits; be safe anyway
        best = FindingImpact(f.key, "minor", "", SEVERITY_POINTS["minor"] * (1.0 if f.verdict == "fail" else 0.5))
    return best


def agentic_impact(findings: Iterable[AgenticFinding]) -> tuple[int, list[FindingImpact]]:
    """Total deduction over distinct findings (one per key; the costliest wins if a key appears twice)."""
    by_key: dict[str, FindingImpact] = {}
    for f in findings:
        impact = finding_impact(f)
        if impact is not None and (f.key not in by_key or impact.points > by_key[f.key].points):
            by_key[f.key] = impact
    impacts = sorted(by_key.values(), key=lambda i: (-i.points, i.key))
    return round(sum(i.points for i in impacts)), impacts


def compute(
    verdicts: Iterable[str],
    regressed: int,
    penalty: int = 5,
    agentic: Iterable[AgenticFinding] = (),
    agentic_ran: bool = False,
) -> ScoreBreakdown:
    vs = list(verdicts)
    passed = vs.count("pass")
    flaky = vs.count("flaky")
    failed = vs.count("fail")
    na = vs.count("na")
    errors = vs.count("error")
    applicable = passed + flaky + failed
    impact, impacts = agentic_impact(agentic)
    counts: dict[str, int] = {}
    for i in impacts:
        counts[i.severity] = counts.get(i.severity, 0) + 1
    details: list[dict[str, object]] = [
        {"key": i.key, "severity": i.severity, "oracle_id": i.oracle_id, "points": i.points} for i in impacts
    ]
    agentic_part = ""
    if impacts:
        agentic_part = ", ".join(
            f"{counts[s]} {SEVERITY_LABEL[s]}" for s in SEVERITY_POINTS if s in counts
        )  # e.g. "1 data exposure, 2 crash"

    if applicable == 0:
        # No applicable check is not a pass: there is nothing a number could describe. Confirmed agentic findings
        # are still listed with their impact, but they cannot turn "untested" into a readiness score.
        why = f"insufficient coverage: no applicable baseline check ({na} inapplicable, {errors} inconclusive)"
        if impacts:
            why += f"; {len(impacts)} confirmed agentic finding(s): {agentic_part}"
        return ScoreBreakdown(
            None,
            0,
            0,
            0,
            0,
            na,
            errors,
            regressed,
            penalty,
            why,
            None,
            impact,
            counts,
            details,
            "insufficient_coverage",
        )

    base = 100 * (passed + 0.5 * flaky) / applicable
    baseline = max(0, round(base - penalty * regressed))
    score = max(0, round(base - penalty * regressed - impact))
    formula = f"score = 100 x ({passed} + 0.5 x {flaky}) / {applicable}"
    if regressed:
        formula += f" - {penalty} x {regressed}"
    if impacts:
        formula += f" - agentic {impact} ({agentic_part})"
    formula += f" = {score}"
    return ScoreBreakdown(
        score,
        passed,
        flaky,
        failed,
        applicable,
        na,
        errors,
        regressed,
        penalty,
        formula,
        baseline,
        impact,
        counts,
        details,
    )
