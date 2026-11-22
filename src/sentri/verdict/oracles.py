"""The five universal oracles. Each is a pure function of (evidence, config)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from sentri.evidence.models import Evidence, HttpRecord
from sentri.verdict.signature import normalize_error, normalize_path, normalize_route

ORACLE_IDS = ("http_500", "exception", "console_error", "duplicates", "timeout")
DUPLICATE_ATTACKS = frozenset({"double_submit", "rapid_repeat", "slow_network"})

EXCEPTION_PATTERNS = (
    r"Traceback \(most recent call last\)",
    r"^\s*at .+\(.+:\d+:\d+\)$",
    r"Unhandled(Promise)?Rejection|UnhandledPromiseRejectionWarning",
    r"^panic:",
    r"Exception in thread",
    r"\b(FATAL|CRITICAL)\b",
)
_FRAME = re.compile(r"^\s*(File \"|at |\^|~)")
_RESOURCE_4XX = re.compile(r"Failed to load resource: .*status of 4\d\d")
_RESOURCE_ERROR = re.compile(r"blocked by CORS policy|Failed to load resource|net::ERR_|Failed to fetch", re.I)
_URL = re.compile(r"https?://[^\s'\"<>()]+")


@dataclass(frozen=True)
class OracleConfig:
    flow_id: str
    attack_id: str
    ignore_console: tuple[str, ...] = ()
    extra_log_patterns: tuple[str, ...] = ()
    expected_creates: int | None = None
    app_origin: str | None = None  # e.g. "http://127.0.0.1:8765"; lets oracles ignore third-party noise


@dataclass(frozen=True)
class OracleResult:
    oracle_id: str
    fired: bool
    detail: str = ""
    route: str | None = None
    error_key: str | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _not_fired(oracle_id: str, detail: str = "") -> OracleResult:
    return OracleResult(oracle_id=oracle_id, fired=False, detail=detail)


def http_500(ev: Evidence, cfg: OracleConfig) -> OracleResult:
    for rec in ev.http:
        if rec.same_origin and rec.status is not None and 500 <= rec.status <= 599:
            route = normalize_route(rec.method, rec.url)
            return OracleResult("http_500", True, f"{route} -> {rec.status}", route, str(rec.status))
    return _not_fired("http_500")


def _exception_key(lines: list[str], hit: int) -> str:
    """The exception line of a trace block: the last non-frame line after the hit, before a blank line."""
    key = lines[hit]
    for line in lines[hit + 1 : hit + 80]:
        stripped = line.strip()
        if not stripped:
            break
        if _FRAME.match(line) or line.startswith("    "):
            continue
        key = stripped
        if re.match(r"^[\w.]+(Error|Exception|Warning)\b|^\w+: ", stripped):
            break
    return normalize_error(key)


def exception(ev: Evidence, cfg: OracleConfig) -> OracleResult:
    patterns = [re.compile(p, re.MULTILINE) for p in (*EXCEPTION_PATTERNS, *cfg.extra_log_patterns)]
    lines = [line.text for line in ev.logs]
    for i, text in enumerate(lines):
        if any(p.search(text) for p in patterns):
            key = _exception_key(lines, i)
            return OracleResult("exception", True, f"log: {key}", None, key)
    return _not_fired("exception")


def _origin(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "::1"):
        host = "127.0.0.1"
    return f"{parts.scheme}://{host}:{parts.port or (443 if parts.scheme == 'https' else 80)}"


def _third_party_resource_error(text: str, app_origin: str | None) -> bool:
    """A resource or CORS failure that only involves other origins (a CDN, a font host): not the app's bug,
    the same way http_500 ignores third-party servers."""
    if app_origin is None or not _RESOURCE_ERROR.search(text):
        return False
    urls = _URL.findall(text)
    if not urls:
        return False
    mine = _origin(app_origin)
    resources = [u for u in urls if not text.split(u, 1)[0].rstrip(" '\"").endswith("from origin")]
    return bool(resources) and all(_origin(u) != mine for u in resources)


def console_error(ev: Evidence, cfg: OracleConfig) -> OracleResult:
    ignore = [re.compile(p) for p in cfg.ignore_console]
    for rec in ev.console:
        haystack = f"{rec.text} {rec.source_url or ''}"
        if any(p.search(haystack) for p in ignore):
            continue
        if rec.kind == "console_error" and _RESOURCE_4XX.search(rec.text):
            continue
        if rec.kind == "console_error" and "ERR_BLOCKED_BY_CLIENT" in rec.text:
            continue  # Sentri's own origin fence refused an off-origin request; not the app's bug
        if rec.kind == "console_error" and "ERR_BLOCKED_BY_LOCAL_NETWORK_ACCESS" in rec.text:
            continue  # a browser policy about local addresses (switched off at launch), not the app's bug
        if rec.kind == "console_error" and _third_party_resource_error(rec.text, cfg.app_origin):
            continue
        first = rec.text.splitlines()[0] if rec.text else ""
        key = normalize_error(first)
        return OracleResult("console_error", True, f"{rec.kind}: {first[:160]}", None, key)
    return _not_fired("console_error")


def _repeated_posts(http: tuple[HttpRecord, ...]) -> int:
    """Largest group of identical successful same-origin writes started within 1 s of each other."""
    writes = [
        r
        for r in http
        if r.same_origin
        and r.method in ("POST", "PUT")
        and r.status is not None
        and 200 <= r.status < 400
        and r.resource_type in ("document", "fetch", "xhr", "other")
    ]
    best = 0
    for anchor in writes:
        group = [
            r
            for r in writes
            if r.url == anchor.url
            and r.request_body_sha256 == anchor.request_body_sha256
            and abs(r.started_ms - anchor.started_ms) <= 1000
        ]
        best = max(best, len(group))
    return best


def _text_repeats(ev: Evidence) -> tuple[str, int] | None:
    rows = [" ".join(r.split()) for r in ev.page_text_rows]
    for value in ev.unique_values:
        norm = " ".join(value.split())
        count = sum(1 for r in rows if norm and norm in r)
        if count > 1:
            return value, count
    return None


def duplicates(ev: Evidence, cfg: OracleConfig) -> OracleResult:
    if cfg.attack_id not in DUPLICATE_ATTACKS:
        return _not_fired("duplicates", "n/a for attack")
    if cfg.expected_creates is None:
        # Without a create hint a repeated write may be an idempotent update; do not guess.
        return _not_fired("duplicates", "not checked: flow has no list_selector or expected_creates")
    expected = cfg.expected_creates
    method = ev.duplicate_method
    if method in ("db", "list_selector") and ev.list_count_before is not None and ev.list_count_after is not None:
        delta = ev.list_count_after - ev.list_count_before
        if delta > expected:
            return OracleResult(
                "duplicates", True, f"{method}: {delta} created, expected {expected}", None, f"dup:{method}"
            )
        return _not_fired("duplicates", f"{method}: {delta} created, expected <= {expected}")

    posts = _repeated_posts(ev.http)
    text = _text_repeats(ev)
    if cfg.attack_id == "rapid_repeat":
        # Ten identical POSTs are expected here; only trust the heuristic when the page confirms it.
        if posts > expected and text is not None:
            return OracleResult(
                "duplicates", True, f"post_heuristic+text: {text[1]} copies of {text[0]!r}", None, "dup:post_text"
            )
        return _not_fired("duplicates", "inconclusive")
    if posts > expected:
        return OracleResult(
            "duplicates", True, f"post_heuristic: {posts} identical writes within 1s", None, "dup:post_heuristic"
        )
    if text is not None:
        return OracleResult(
            "duplicates", True, f"text_heuristic: {text[1]} copies of {text[0]!r}", None, "dup:text_heuristic"
        )
    return _not_fired("duplicates")


def timeout(ev: Evidence, cfg: OracleConfig) -> OracleResult:
    if ev.page_crashed:
        return OracleResult("timeout", True, "page crashed", None, "crash")
    if ev.timed_out:
        detail = ev.timeout_detail or "no response within the time limit"
        return OracleResult("timeout", True, detail, None, normalize_error(detail))
    for rec in ev.http:
        if rec.same_origin and rec.failed and rec.failure and "timed_out" in rec.failure.lower().replace(" ", "_"):
            path = normalize_path(rec.url)
            return OracleResult("timeout", True, f"request timed out: {path}", None, f"timeout:{path}")
    return _not_fired("timeout")


ORACLES: tuple[Callable[[Evidence, OracleConfig], OracleResult], ...] = (
    http_500,
    exception,
    console_error,
    duplicates,
    timeout,
)


def evaluate_all(ev: Evidence, cfg: OracleConfig) -> list[OracleResult]:
    return [oracle(ev, cfg) for oracle in ORACLES]
