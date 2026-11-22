"""Extra evidence for agentic checks. Built by plain code from what the session recorded."""

from __future__ import annotations

from dataclasses import dataclass, field

from sentri.evidence.models import Evidence


@dataclass(frozen=True)
class ResponseRecord:
    identity: str  # anon | a | b
    method: str
    path: str
    status: int | None
    text: str = ""  # same-origin response body (redacted, truncated)
    redirected_to: str | None = None


@dataclass(frozen=True)
class CountProbe:
    """Occurrences of a unique value on a check page before and after a replay or race."""

    token: str
    page: str
    before: int
    after: int
    expected_increase: int = 1


@dataclass(frozen=True)
class HealthProbe:
    """A page that worked before the abuse, loaded again afterwards."""

    path: str
    identity: str
    status_before: int | None
    status_after: int | None
    errors_before: int = 0
    errors_after: int = 0


@dataclass(frozen=True)
class AgentEvidence:
    base: Evidence
    responses: tuple[ResponseRecord, ...] = ()
    protected_paths: tuple[str, ...] = ()  # normalized paths recon proved need login
    private_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)  # identity -> its private values
    count_probes: tuple[CountProbe, ...] = ()
    health_probes: tuple[HealthProbe, ...] = ()
