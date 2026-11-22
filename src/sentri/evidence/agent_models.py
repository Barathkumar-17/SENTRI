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
    content_type: str | None = None


@dataclass(frozen=True)
class CountProbe:
    """Occurrences of a unique value on a check page before and after a replay or race."""

    token: str
    page: str
    before: int
    after: int
    expected_increase: int = 1
    inconclusive: bool = False  # no list page, or no record structure to count in: never fires


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
class IdorProbe:
    """A record identity `owner` created, fetched directly by its URL as the owner, logged out and as `other`.
    Run by plain code after every create it sees; no model involved."""

    owner: str
    other: str
    path: str
    owner_status: int | None
    owner_sees: bool  # the owner's canary is in the response: this URL really is the record
    anon_status: int | None
    anon_sees: bool
    other_status: int | None
    other_sees: bool


@dataclass(frozen=True)
class CsrfProbe:
    """A state-changing browser request that carried a CSRF token, re-sent once by code without the token."""

    identity: str
    method: str
    path: str
    token: str  # the field or header that was removed
    with_status: int | None
    with_location: str | None
    with_text: str
    without_status: int | None
    without_location: str | None
    without_text: str


@dataclass(frozen=True)
class AgentEvidence:
    base: Evidence
    responses: tuple[ResponseRecord, ...] = ()
    protected_paths: tuple[str, ...] = ()  # normalized paths recon proved need login (behaviour, not "unseen")
    anon_shells: tuple[str, ...] = ()  # what a logged-out visitor gets for the app shell / login page
    private_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)  # identity -> its private values
    count_probes: tuple[CountProbe, ...] = ()
    health_probes: tuple[HealthProbe, ...] = ()
    idor_probes: tuple[IdorProbe, ...] = ()
    csrf_probes: tuple[CsrfProbe, ...] = ()
    xss_markers: tuple[str, ...] = ()  # Sentri's unique script-ish markers for this session
    login_path: str | None = None
