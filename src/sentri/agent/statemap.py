"""Exploration memory: which states the tester has seen, what it tried in each, what happened, what is left.

Pure code, built only from observations of the running app (black box). The model gets a compact view of it every
step (current state, numbered options with their outcomes, the frontier) instead of the whole page and a long
transcript, and code uses it to refuse actions already proven to be dead ends.

A *state* is who is acting (identity, and whether its session was cleared) plus where (route with ids normalized)
plus what the page offers (form, control and link structure). A new URL with the same structure, e.g. /tasks/7
after /tasks/3, is the same state; changed text alone is not a new state.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sentri.verdict.signature import normalize_path


@dataclass
class Tried:
    count: int = 0
    errors: int = 0
    changed: int = 0  # times it led to another state or a different page
    signals: int = 0
    to_states: list[str] = field(default_factory=list)
    last_error: str | None = None
    last_step: int = 0


@dataclass
class StateNode:
    sid: str
    key: str
    identity: str
    path: str
    title: str
    first_step: int
    visits: int = 0
    text_hash: str = ""
    tried: dict[str, Tried] = field(default_factory=dict)
    options: list[dict[str, str]] = field(default_factory=list)  # what the page offers: {id, sig, do}
    # Raw requests per route ("POST /create"): the outcome of each try (response statuses, state changed, signals).
    routes: dict[str, list[tuple[tuple[int | None, ...], bool, int]]] = field(default_factory=dict)


def _path(url: str) -> str:
    parts = urlsplit(url or "")
    return normalize_path(parts.path or "/") + (f"#{parts.fragment}" if parts.fragment.startswith("/") else "")


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]


def option_list(obs: dict[str, Any], limit: int = 30) -> list[dict[str, str]]:
    """The page's links, forms and loose controls as numbered options, each mapped to the action that uses it."""
    out: list[dict[str, str]] = []
    for i, link in enumerate(dict.fromkeys(obs.get("links") or [])):
        out.append({"id": f"L{i + 1}", "sig": f"navigate {link}", "do": f"navigate {link}"})
    for i, form in enumerate(obs.get("forms") or []):
        sel = form.get("selector") or "form"
        fields = ", ".join(
            f"{f.get('selector')}({f.get('type') or f.get('tag') or 'text'})" for f in (form.get("fields") or [])[:8]
        )
        buttons = " ".join(b.get("selector", "") for b in (form.get("buttons") or [])[:2])
        out.append({"id": f"F{i + 1}", "sig": f"fill_form {sel}", "do": f"fill_form {sel} [{fields}] submit {buttons}"})
    for i, b in enumerate(obs.get("buttons") or []):
        sel = b.get("selector") or ""
        out.append({"id": f"B{i + 1}", "sig": f"click {sel}", "do": f"click {sel} '{(b.get('text') or '')[:30]}'"})
    return out[:limit]


def route_of(action: dict[str, Any], base_paths: dict[int, str] | None = None) -> str | None:
    """`METHOD /path` of a raw request action (path normalized), for route-level dead ends."""
    if action.get("kind") != "request":
        return None
    path = action.get("path") or (base_paths or {}).get(action.get("from_request") or -1)
    if not path:
        return None
    return f"{action.get('method', 'GET')} {normalize_path('/' + str(path).lstrip('/'))}"


def action_sig(action: dict[str, Any]) -> str:
    """Coarse signature matching option sigs: kind + target. Values are part of it for typing, so an input-abuse
    test with a different value is a different test."""
    kind = action.get("kind", "")
    if kind == "navigate":
        return f"navigate {action.get('path')}"
    if kind == "fill_form":
        values = sorted((f.get("selector"), f.get("text")) for f in action.get("fields") or [])
        base = f"fill_form {action.get('selector')}"
        return base if not values else f"{base} {_hash(values)}"
    if kind in ("click", "burst", "upload"):
        return f"{kind} {action.get('selector')}" + (f" {action.get('shape')}" if kind == "upload" else "")
    if kind == "switch_user":
        return f"switch_user {action.get('user')}"
    if kind == "type":
        return f"type {action.get('selector')} {_hash([action.get('text'), action.get('repeat')])}"
    return f"{kind} " + _hash({k: v for k, v in action.items() if k != "kind"})


class ExplorationMap:
    def __init__(self) -> None:
        self.states: dict[str, StateNode] = {}
        self.order: list[str] = []
        self.cleared: set[str] = set()  # identities whose session was cleared during exploration
        self.transitions = 0

    def key(self, obs: dict[str, Any]) -> str:
        who = obs.get("identity") or "anon"
        session = "cleared" if who in self.cleared else ("in" if who != "anon" else "out")
        structure = {
            "forms": sorted(
                (
                    f.get("selector"),
                    tuple(sorted(x.get("name") or x.get("selector") or "" for x in f.get("fields") or [])),
                )
                for f in obs.get("forms") or []
            ),
            "buttons": sorted(b.get("selector") or "" for b in obs.get("buttons") or []),
            "links": sorted({_path(link) for link in obs.get("links") or []}),
        }
        return f"{who}|{session}|{_path(obs.get('url') or '')}|{_hash(structure)}"

    def visit(self, obs: dict[str, Any], step: int) -> tuple[StateNode, bool, bool]:
        """The state this observation is in, whether it is new, and whether its text changed since last time."""
        key = self.key(obs)
        node = self.states.get(key)
        is_new = node is None
        if node is None:
            node = StateNode(
                sid=f"S{len(self.states) + 1}",
                key=key,
                identity=obs.get("identity") or "anon",
                path=_path(obs.get("url") or ""),
                title=str(obs.get("title") or "")[:80],
                first_step=step,
            )
            self.states[key] = node
            self.order.append(key)
        node.visits += 1
        text_hash = _hash(obs.get("text") or "")
        text_changed = node.text_hash != text_hash
        node.text_hash = text_hash
        node.options = option_list(obs)
        return node, is_new, text_changed

    def record(self, node: StateNode, sig: str, ok: bool, error: str | None, after: StateNode | None,
               changed: bool, signals: int, step: int) -> Tried:  # fmt: skip
        tried = node.tried.setdefault(sig, Tried())
        tried.count += 1
        tried.last_step = step
        if not ok:
            tried.errors += 1
            tried.last_error = (error or "")[:120]
        if changed:
            tried.changed += 1
            self.transitions += 1
        tried.signals += signals
        if after is not None and after.sid not in tried.to_states:
            tried.to_states.append(after.sid)
        return tried

    @staticmethod
    def dead_end(node: StateNode, sig: str) -> str | None:
        """Why this action in this state is already known to be pointless, if it is. Code refuses it without
        running it: failed twice here, or ran twice here with no change and no signal."""
        tried = node.tried.get(sig)
        if tried is None:
            return None
        if tried.errors >= 2:
            return f"failed {tried.errors}x here ({tried.last_error})"
        if tried.count >= 2 and tried.changed == 0 and tried.signals == 0:
            return f"tried {tried.count}x here with no change"
        return None

    ROUTE_DEAD_END_TRIES = 3

    @staticmethod
    def record_route(
        node: StateNode, route: str, statuses: tuple[int | None, ...], changed: bool, signals: int
    ) -> None:
        node.routes.setdefault(route, []).append((statuses, changed, signals))

    @classmethod
    def route_dead_end(cls, node: StateNode, route: str) -> str | None:
        """A route that answered the same way several times here, with no change and no signal: more body variants
        of the same request are a dead end (found by the flaskr run: 7 near-identical logged-out POSTs)."""
        tries = node.routes.get(route, [])
        if len(tries) < cls.ROUTE_DEAD_END_TRIES:
            return None
        if len({t[0] for t in tries}) == 1 and not any(t[1] or t[2] for t in tries):
            return f"{route} answered {tries[0][0]} {len(tries)}x here with no change"
        return None

    def options_view(self, node: StateNode) -> list[dict[str, str]]:
        out = []
        for opt in node.options:
            tried = next((t for s, t in node.tried.items() if s == opt["sig"] or s.startswith(opt["sig"] + " ")), None)
            if tried is None:
                status = "untried"
            elif self.dead_end(node, opt["sig"]):
                status = f"dead end: {self.dead_end(node, opt['sig'])}"
            elif tried.errors and not tried.changed:
                status = f"failed: {tried.last_error}"
            else:
                status = f"tried {tried.count}x -> {', '.join(tried.to_states) or 'same state'}"
            out.append({"id": opt["id"], "do": opt["do"], "status": status})
        return out

    def untried(self, node: StateNode) -> int:
        return sum(1 for opt in node.options if not any(s.startswith(opt["sig"]) for s in node.tried))

    def frontier(self, limit: int = 6) -> list[dict[str, Any]]:
        """States that still offer untried options, oldest first (a breadth hint for the model)."""
        out = []
        for key in self.order:
            node = self.states[key]
            n = self.untried(node)
            if n:
                out.append({"state": node.sid, "as": node.identity, "path": node.path, "untried": n})
        return out[:limit]

    def stats(self) -> dict[str, Any]:
        return {
            "distinct_states": len(self.states),
            "transitions": self.transitions,
            "states_by_identity": {
                who: sum(1 for n in self.states.values() if n.identity == who)
                for who in sorted({n.identity for n in self.states.values()})
            },
            "untried_options_left": sum(self.untried(n) for n in self.states.values()),
        }
