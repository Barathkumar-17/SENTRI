"""Verdict engine: the 3-attempt rule (docs/reference/oracles-and-verdicts.md section 3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sentri.verdict.oracles import ORACLE_IDS, OracleResult
from sentri.verdict.signature import signature

Verdict = Literal["pass", "fail", "flaky", "na", "error"]


@dataclass(frozen=True)
class OracleHit:
    oracle_id: str
    k: int
    signature: str
    detail: str


@dataclass(frozen=True)
class CheckVerdict:
    verdict: Verdict
    attempts: int
    hits: tuple[OracleHit, ...] = field(default=())

    @property
    def oracle_hits(self) -> str:
        return ",".join(f"{h.oracle_id}:{h.k}" for h in self.hits)


def decide(flow_id: str, attack_id: str, attempts: list[list[OracleResult]]) -> CheckVerdict:
    if not attempts:
        raise ValueError("decide() needs at least one attempt")
    total = len(attempts)
    hits: list[OracleHit] = []
    # The five universal oracles first, then any others that reported (agentic oracles), in first-seen order.
    oracle_ids = list(ORACLE_IDS)
    for attempt in attempts:
        for r in attempt:
            if r.oracle_id not in oracle_ids:
                oracle_ids.append(r.oracle_id)
    for oracle_id in oracle_ids:
        fired = [r for attempt in attempts for r in attempt if r.oracle_id == oracle_id and r.fired]
        if not fired:
            continue
        # One bug per distinct signature; k counts attempts in which that signature fired.
        by_sig: dict[str, list[OracleResult]] = {}
        for r in fired:
            sig = signature(oracle_id, flow_id, attack_id, r.route, r.error_key)
            by_sig.setdefault(sig, []).append(r)
        for sig, results in by_sig.items():
            k = sum(
                1
                for attempt in attempts
                if any(
                    r.oracle_id == oracle_id
                    and r.fired
                    and signature(oracle_id, flow_id, attack_id, r.route, r.error_key) == sig
                    for r in attempt
                )
            )
            hits.append(OracleHit(oracle_id, k, sig, results[0].detail))

    verdict: Verdict
    oracle_k = {
        oid: sum(1 for attempt in attempts if any(r.oracle_id == oid and r.fired for r in attempt))
        for oid in oracle_ids
    }
    if any(k == total for k in oracle_k.values()):
        verdict = "fail"
    elif any(k > 0 for k in oracle_k.values()):
        verdict = "flaky"
    else:
        verdict = "pass"
    return CheckVerdict(verdict=verdict, attempts=total, hits=tuple(hits))
