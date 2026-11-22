"""Oracles for agentic checks. Pure functions of evidence; no model is ever consulted.

"Private" is defined by what recon proved, never by the model: a page is protected when a logged-out visitor
was redirected or refused while a signed-in user got it; a value is private when its owner submitted it only
from protected pages or to protected endpoints.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from sentri.evidence.agent_models import AgentEvidence, ResponseRecord
from sentri.verdict.oracles import ORACLES as BASE_ORACLES
from sentri.verdict.oracles import OracleConfig, OracleResult
from sentri.verdict.signature import normalize_error, normalize_path, normalize_route

AGENT_ORACLE_IDS = (
    "auth_bypass",
    "cross_user_exposure",
    "xss_reflection",
    "csrf_missing",
    "replay_duplicate",
    "state_corruption",
)
_LOGIN_FORM = ('type="password"', "type='password'", "type=password")


def _looks_like_login_form(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _LOGIN_FORM)


_WORD = re.compile(r"[a-z][a-z_-]{1,40}")
_NOISE = re.compile(r"[0-9a-f]{8,}|\d+", re.I)  # nonces, hashes, build ids, timestamps


def _words(text: str) -> set[str]:
    return set(_WORD.findall(_NOISE.sub(" ", text.lower())))


def is_shell(text: str, shells: tuple[str, ...], threshold: float = 0.95) -> bool:
    """Same page as the app shell or login page a logged-out visitor gets anyway (a single-page app serves the
    same HTML on every route): same words, ignoring nonces and numbers, at >= `threshold` Jaccard similarity."""
    words = _words(text)
    if not words:
        return False
    for shell in shells:
        other = _words(shell)
        if other and len(words & other) / len(words | other) >= threshold:
            return True
    return False


def _leak(ev: AgentEvidence, r: ResponseRecord, owners: tuple[str, ...]) -> str | None:
    for owner in owners:
        for marker in ev.private_markers.get(owner, ()):
            if marker and marker in r.text:
                return owner
    return None


def auth_bypass(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    protected = set(ev.protected_paths)
    for r in ev.responses:
        if r.identity != "anon":
            continue
        owner = _leak(ev, r, ("a", "b"))
        route = normalize_route(r.method, r.path)
        # Private data counts only where login was proven: content an app publishes to everyone (a blog's front
        # page) is not a bypass just because its author wrote it on a login-only page.
        if owner is not None and normalize_path(r.path) in protected:
            return OracleResult(
                "auth_bypass", True, f"logged-out {route} returned user {owner}'s private data", route, "private_data"
            )
        served = r.status is not None and 200 <= r.status < 300 and r.redirected_to is None
        if (
            r.method == "GET"
            and served
            and normalize_path(r.path) in protected
            and not _looks_like_login_form(r.text)
            and not is_shell(r.text, ev.anon_shells)
        ):
            return OracleResult(
                "auth_bypass",
                True,
                f"logged-out {route} served a login-only page ({r.status})",
                route,
                "protected_page",
            )
    return OracleResult("auth_bypass", False)


def cross_user_exposure(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    # Direct-by-id check, run by code on every record a or b created: the owner sees it, a logged-out visitor
    # does not, yet the other user does. Privacy is proven by behaviour, so no recon or model input is needed.
    for p in ev.idor_probes:
        other_ok = p.other_status is not None and 200 <= p.other_status < 300
        if p.owner_sees and not p.anon_sees and other_ok and p.other_sees:
            route = normalize_route("GET", p.path)
            return OracleResult(
                "cross_user_exposure",
                True,
                f"user {p.other} opened user {p.owner}'s record {p.path} by id ({p.other_status}; logged out: "
                f"{p.anon_status})",
                route,
                "other_user_record",
            )
    public = set(ev.public_marker_routes)
    for r in ev.responses:
        if r.identity not in ("a", "b"):
            continue
        other = "b" if r.identity == "a" else "a"
        if f"{other} {normalize_path(r.path)}" in public:
            continue  # a logged-out visitor sees it there as well: published content, not exposure to one user
        if _leak(ev, r, (other,)) is not None:
            route = normalize_route(r.method, r.path)
            return OracleResult(
                "cross_user_exposure",
                True,
                f"user {r.identity} received user {other}'s private data via {route}",
                route,
                "other_user_data",
            )
    return OracleResult("cross_user_exposure", False)


def replay_duplicate(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    for p in ev.count_probes:
        if not p.inconclusive and p.after - p.before > p.expected_increase:
            route = normalize_route("GET", p.page)
            return OracleResult(
                "replay_duplicate",
                True,
                f"{p.after - p.before} new records on {p.page} after a replay, expected {p.expected_increase}",
                route,
                "duplicate_records",
            )
    return OracleResult("replay_duplicate", False)


def state_corruption(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    for h in ev.health_probes:
        ok_before = h.status_before is not None and h.status_before < 400 and h.errors_before == 0
        broken_after = (h.status_after is not None and h.status_after >= 500) or h.errors_after > h.errors_before
        if ok_before and broken_after:
            route = normalize_route("GET", h.path)
            what = f"HTTP {h.status_after}" if (h.status_after or 0) >= 500 else f"{h.errors_after} page errors"
            return OracleResult(
                "state_corruption", True, f"{h.path} worked before the abuse, now {what}", route, normalize_error(what)
            )
    return OracleResult("state_corruption", False)


def xss_reflection(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    """Sentri's marker (a quote, then a made-up tag) came back unescaped in an HTML response: the app writes input
    into its pages without encoding it. Detection only: the marker is inert, nothing is executed."""
    for r in ev.responses:
        if "html" not in (r.content_type or "").lower():
            continue
        for marker in ev.xss_markers:
            if marker and marker in r.text:
                route = normalize_route(r.method, r.path)
                return OracleResult(
                    "xss_reflection", True, f"{route} reflected {marker} without escaping", route, "unescaped_input"
                )
    return OracleResult("xss_reflection", False)


def _accepted(status: int | None, location: str | None, login_path: str | None) -> bool:
    if status is None or status >= 400:
        return False
    if 300 <= status < 400:
        return not (login_path and location and login_path in location)  # bounced to login = refused
    return True


def csrf_missing(ev: AgentEvidence, cfg: OracleConfig) -> OracleResult:
    """A state-changing request accepted without the CSRF token the app's own form sent: same success as with
    it (same redirect target, or a 2xx page that is essentially the same page), never a 4xx or a login bounce."""
    for p in ev.csrf_probes:
        if not (
            _accepted(p.with_status, p.with_location, ev.login_path)
            and _accepted(p.without_status, p.without_location, ev.login_path)
        ):
            continue
        if 300 <= (p.with_status or 0) < 400 or 300 <= (p.without_status or 0) < 400:
            same = (p.with_location or "").split("?")[0] == (p.without_location or "").split("?")[0]
        else:
            a, b = _words(p.with_text), _words(p.without_text)
            same = not (a or b) or (bool(a | b) and len(a & b) / len(a | b) >= 0.9)
        if same:
            route = normalize_route(p.method, p.path)
            return OracleResult(
                "csrf_missing",
                True,
                f"{route} accepted a request without its CSRF token '{p.token}' ({p.without_status})",
                route,
                "no_csrf_check",
            )
    return OracleResult("csrf_missing", False)


AGENT_ORACLES: tuple[Callable[[AgentEvidence, OracleConfig], OracleResult], ...] = (
    auth_bypass,
    cross_user_exposure,
    xss_reflection,
    csrf_missing,
    replay_duplicate,
    state_corruption,
)


def evaluate_agent(ev: AgentEvidence, cfg: OracleConfig) -> list[OracleResult]:
    """The five universal oracles on the base evidence, plus the six agentic ones."""
    return [oracle(ev.base, cfg) for oracle in BASE_ORACLES] + [oracle(ev, cfg) for oracle in AGENT_ORACLES]
