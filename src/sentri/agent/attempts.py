"""What the explorer already tried, for the whole run: across pages, tabs and hypothesis changes.

The state map remembers outcomes per page state, so a changed URL or a new focus hypothesis used to start the count
again: the flow-shop run (b6db7b8) sent the same logged-out add-to-cart five times from different pages and under
different hypotheses, each landing on the login page. Here an attempt is keyed by who acted and the *operation* it
performed (the request route it hit, or the page it opened), not by the page it started from or the hypothesis it
served. Plain code decides only one thing from it: whether repeating an operation can add evidence.

Rules (all code, no model judgement):
- An operation may be repeated while it keeps adding evidence (a new outcome, a state never seen in the run, a
  signal), or after the app changed in between (a successful write, a different identity).
- The same identity, the same operation, the same outcome, no evidence, nothing changed since: after ``MAX_SAME``
  such attempts the next one is refused, unless the model states why repeating adds evidence (``repeat_reason``),
  which buys exactly one more.
- Deliberate repetition actions (burst, replay) and navigation-only steps (back, refresh, look, switch_user, ...) are
  never refused here: repeating is their point, or they change nothing.
- Steps that add no evidence are counted for the run (not per hypothesis): the explorer warns the model, then stops
  honestly ("no progress") instead of spending the budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sentri.verdict.signature import normalize_path

# never refused: repeating is the point (burst, replay) or the step only moves around / looks / ends
EXEMPT_KINDS = {
    "burst",
    "replay",
    "back",
    "refresh",
    "look",
    "finish",
    "wait",
    "switch_user",
    "switch_tab",
    "new_tab",
    "throttle",
    "clear_session",
}
MAX_SAME = 2  # identical outcomes of one operation with identical parameters, in a row, before a try must be justified
MAX_VARIANTS = 3  # identical outcomes of one operation across different parameters (input variants) in a row


def _route(url_or_path: str) -> str:
    parts = urlsplit(url_or_path or "")
    return normalize_path(parts.path or "/")


def operation_before(action: dict[str, Any], page_url: str | None) -> str | None:
    """The operation an action will perform, as far as it is known before running it."""
    kind = action.get("kind")
    if kind in EXEMPT_KINDS or kind is None:
        return None
    if kind == "request":
        return f"{str(action.get('method', 'GET')).upper()} {_route(str(action.get('path', '/')))}"
    if kind == "navigate":
        return f"GET {_route(str(action.get('path', '/')))}"
    # a form or a click: named by what it is and the kind of page it is on (so /product/1 and /product/2 match)
    target = action.get("selector") or ""
    return f"{kind} {target} @ {_route(page_url or '/')}"


def params_of(action: dict[str, Any]) -> str:
    """What the action carries besides where it goes: a body, field values, a method's content type. Different
    parameters are different experiments (input variants); identical ones repeat the same experiment."""
    rest = {k: v for k, v in action.items() if k not in ("kind", "method", "path", "selector")}
    return json.dumps(rest, sort_keys=True, default=str)


def operation_after(intent: str, requests: list[Any], base_url: str) -> str:
    """What the action actually hit: its first state-changing request (method and route), else its intent."""
    origin = urlsplit(base_url).netloc
    for r in requests:
        method = str(getattr(r, "method", "GET")).upper()
        url = str(getattr(r, "url", ""))
        if method != "GET" and urlsplit(url).netloc == origin:
            return f"{method} {_route(url)}"
    return intent


@dataclass
class _Attempt:
    step: int
    outcome: str
    evidence: bool
    world: int
    justified: bool = False  # the model stated why repeating this attempt adds evidence
    params: str = ""


@dataclass
class AttemptMemory:
    attempts: dict[tuple[str, str], list[_Attempt]] = field(default_factory=dict)
    intents: dict[tuple[str, str], str] = field(default_factory=dict)  # (identity, intent) -> operation it performed
    world: int = 0  # how many times the app's state changed for real (a successful write, a new identity)
    unproductive: int = 0  # steps in a row, across the whole run, that added no evidence
    refusals: int = 0

    def check(self, identity: str, intent: str | None, params: str = "", repeat_reason: str = "") -> str | None:
        """Why running this operation again cannot add evidence, or None when it may run.

        As the same identity with nothing changed in the app since: two identical outcomes of identical attempts
        are enough (the first showed what happens, the second confirmed it), and so are three identical outcomes
        across different parameters (input variants stay worth trying a little longer). A further try needs a
        stated reason (allowed once) or a change in the world (another identity acted, a write succeeded)."""
        if intent is None:
            return None
        op = self.intents.get((identity, intent), intent)
        tries = self.attempts.get((identity, op), [])
        same = [t for t in tries if t.params == params]
        for group, limit in ((same, MAX_SAME), (tries, MAX_VARIANTS)):
            if len(group) < limit:
                continue
            tail = group[-limit:]
            if len({t.outcome for t in tail}) > 1 or any(t.evidence for t in tail[1:]):
                continue  # the last tries differed or taught something: the operation is still informative
            if tail[-1].world != self.world:
                continue  # the app changed since: a retry can add evidence
            if repeat_reason.strip() and not any(t.justified for t in tail):
                continue  # one stated, deliberate repeat
            return (
                f"{op} as {identity} already gave '{tail[-1].outcome}' {limit}x "
                f"{'with these parameters' if group is same else 'with different parameters'}, no new evidence, "
                "and nothing changed since"
            )
        return None

    def record(
        self,
        identity: str,
        intent: str | None,
        op: str | None,
        outcome: str,
        new_state: bool,
        signals: int,
        wrote: bool,
        justified: bool = False,
        params: str = "",
    ) -> bool:
        """Remember one executed step; returns whether it added evidence."""
        evidence = new_state or signals > 0
        if op is not None and intent is not None:
            self.intents[(identity, intent)] = op
            tries = self.attempts.setdefault((identity, op), [])
            if outcome not in {t.outcome for t in tries}:
                evidence = True  # a new outcome for this operation is evidence by itself
            tries.append(_Attempt(len(tries) + 1, outcome, evidence, self.world, justified, params))
        if wrote:
            self.world += 1
            evidence = True
        self.unproductive = 0 if evidence else self.unproductive + 1
        return evidence

    def identity_changed(self) -> None:
        self.world += 1


def auth_block(
    new_requests: list[Any], observation: dict[str, Any], intended_path: str | None, start_url: str | None
) -> str | None:
    """Plain facts that an operation was stopped by authentication, as observed: a 401/403 answer, or ending on a
    login form (a password field) that the step did not mean to open. None when nothing says so."""
    for r in new_requests:
        status = getattr(r, "status", None)
        if status in (401, 403):
            return f"{getattr(r, 'method', 'GET')} {_route(str(getattr(r, 'url', '')))} answered HTTP {status}"
    has_password = any(
        str(f.get("type", "")).lower() == "password"
        for form in (observation.get("forms") or [])
        for f in (form.get("fields") or [])
    )
    here = _route(str(observation.get("url") or ""))
    if not has_password:
        return None
    if intended_path is not None and _route(intended_path) == here:
        return None  # the step meant to go to the login page
    if not new_requests and here == _route(start_url or ""):
        return None  # nothing was sent and it was already there: looking at the login form is not a block
    return f"ended on a login form at {here}"
