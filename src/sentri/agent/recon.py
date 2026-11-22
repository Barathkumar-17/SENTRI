"""Recon: explore the app before attacking it and produce an app map.

Crawls every reachable same-origin page as `anon`, then as user `a` when the contract has a login flow;
submits each form once with harmless generated values so the endpoints behind buttons show up in network
traffic; and marks which pages need login (reachable as `a`, redirected or refused as `anon`). Links that look
destructive (logout, delete, ...) are not followed during recon.
"""

from __future__ import annotations

import contextlib
import json
import re
import secrets
from collections import deque
from typing import Any
from urllib.parse import urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from sentri.agent.actions import ActionRefused, resolve_same_origin
from sentri.agent.session import AgentSession
from sentri.contract.placeholders import DataGenerator
from sentri.redact import redact
from sentri.runner.extract import EXTRACT_JS
from sentri.verdict.signature import normalize_path

SPEC_PATHS = (
    "/openapi.json",
    "/swagger.json",
    "/v3/api-docs",
    "/api-docs",
    "/api/openapi.json",
    "/api/swagger.json",
    "/swagger/v1/swagger.json",
)
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")


def _body_fields(op: dict[str, Any], schemas: dict[str, Any]) -> dict[str, str]:
    """name -> 'type (constraints)' for a JSON request body, one level deep, $ref resolved."""
    body = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema") or {}
    if "$ref" in body:
        body = schemas.get(body["$ref"].rsplit("/", 1)[-1], {})
    out = {}
    for name, prop in (body.get("properties") or {}).items():
        if "$ref" in prop:
            prop = schemas.get(prop["$ref"].rsplit("/", 1)[-1], {})
        kind = prop.get("type") or "/".join(str(x.get("type", "?")) for x in prop.get("anyOf", [])) or "any"
        limits = [
            f"{k}={prop[k]}"
            for k in ("format", "minLength", "maxLength", "minimum", "maximum", "exclusiveMinimum", "pattern")
            if k in prop
        ]
        out[name] = kind + (f" ({', '.join(limits)})" if limits else "")
    return out


SKIP_WORDS = ("logout", "log-out", "signout", "sign-out", "delete", "remove", "destroy", "unsubscribe")
NAV_WORDS = re.compile(
    r"^\s*(next|continue|get started|start|begin|proceed|submit|go|view|open|more|let'?s go|sign up|try it)\b", re.I
)
# Buttons outside forms (forms are probed separately), with a selector built like runner/extract.py builds them.
NAV_BUTTONS_JS = r"""
() => {
  const esc = (v) => (window.CSS && CSS.escape) ? CSS.escape(v) : v;
  const sel = (el) => {
    const tag = el.tagName.toLowerCase();
    if (el.dataset && el.dataset.testid) return `[data-testid="${el.dataset.testid}"]`;
    if (el.id) return `#${esc(el.id)}`;
    const text = (el.innerText || el.value || '').trim();
    return `${tag}:has-text("${text.slice(0, 40)}")`;
  };
  return Array.from(document.querySelectorAll('button, [role=button], input[type=button], input[type=submit]'))
    .filter((el) => !el.closest('form') && el.offsetParent !== null)
    .map((el) => ({selector: sel(el), text: (el.innerText || el.value || '').trim().slice(0, 60)}));
}
"""


class PageEntry(BaseModel):
    path: str
    title: str = ""
    seen_as: list[str] = Field(default_factory=list)
    requires_login: bool = False
    anon_outcome: str | None = None  # e.g. "redirect -> /login", "401", "200"
    auth_evidence: str | None = None  # behaviour that proved requires_login: "redirect", "401", "403"
    forms: list[dict[str, Any]] = Field(default_factory=list)
    buttons: list[dict[str, Any]] = Field(default_factory=list)
    list_selectors: list[str] = Field(default_factory=list)
    leads_to: list[dict[str, str]] = Field(default_factory=list)  # navigational buttons: {button, text, to}


class EndpointEntry(BaseModel):
    method: str
    path: str  # normalized template, ids replaced by :id
    examples: list[str] = Field(default_factory=list)
    statuses: list[int] = Field(default_factory=list)
    seen_as: list[str] = Field(default_factory=list)
    content_type: str | None = None
    state_changing: bool = False
    request_ids: list[int] = Field(default_factory=list)
    requires_login: bool = False
    auth_evidence: str | None = None  # "401"/"403" as anon while a user got 2xx; None = declared or unknown
    body_fields: dict[str, str] = Field(default_factory=dict)  # from the API spec: name -> type/constraints
    query_params: list[str] = Field(default_factory=list)
    from_spec: bool = False


class AppMap(BaseModel):
    base_url: str
    pages: list[PageEntry] = Field(default_factory=list)
    endpoints: list[EndpointEntry] = Field(default_factory=list)
    login_path: str | None = None
    has_login_flow: bool = False
    api_spec: str | None = None  # where an OpenAPI/Swagger spec was found
    notes: list[str] = Field(default_factory=list)
    anon_shells: list[str] = Field(default_factory=list)  # logged-out bodies of /, the login page, an unknown path
    redirects: dict[str, str] = Field(default_factory=dict)  # path -> where it redirected (trailing slash, locale...)
    flows: list[dict[str, Any]] = Field(default_factory=list)  # multi-step sequences: {kind, start, steps, end}

    def protected_paths(self) -> set[str]:
        """Normalized paths recon proved (or the spec declares) need login: pages and endpoints."""
        out = {normalize_path(p.path) for p in self.pages if p.requires_login}
        return out | {e.path for e in self.endpoints if e.requires_login}

    def proven_protected_paths(self) -> set[str]:
        """Only paths where login was proven by behaviour (anon redirected, 401 or 403 while a user got the page).
        The auth_bypass oracle uses these: a spec declaration or "never seen logged out" is not enough."""
        out = {normalize_path(p.path) for p in self.pages if p.requires_login and p.auth_evidence}
        return out | {e.path for e in self.endpoints if e.requires_login and e.auth_evidence}

    def page(self, path: str) -> PageEntry | None:
        return next((p for p in self.pages if p.path == path), None)

    def known_paths(self) -> set[str]:
        return (
            {p.path for p in self.pages}
            | {e.path for e in self.endpoints}
            | {x for e in self.endpoints for x in e.examples}
        )

    def summary(self, max_pages: int = 40, max_endpoints: int = 80) -> dict[str, Any]:
        """Compact view for the model."""
        return {
            "pages": [
                {
                    "path": p.path,
                    "title": p.title,
                    "requires_login": p.requires_login,
                    "forms": [
                        {
                            "selector": f.get("selector"),
                            "method": f.get("method"),
                            "action": f.get("action"),
                            "fields": [
                                {"selector": x.get("selector"), "type": x.get("type"), "name": x.get("name")}
                                for x in f.get("fields", [])
                            ],
                            "buttons": [b.get("selector") for b in f.get("buttons", [])],
                        }
                        for f in p.forms
                    ],
                    "buttons": [b.get("selector") for b in p.buttons][:10],
                    "lists": p.list_selectors,
                }
                for p in self.pages[:max_pages]
            ],
            "endpoints": [
                {
                    "method": e.method,
                    "path": e.path,
                    "example": e.examples[0] if e.examples else e.path,
                    "statuses": e.statuses,
                    "as": e.seen_as,
                    "requires_login": e.requires_login,
                    "state_changing": e.state_changing,
                    "body_fields": e.body_fields,
                    "query": e.query_params,
                    "observed_request_ids": e.request_ids[:3],
                }
                for e in self.endpoints[:max_endpoints]
            ],
            "api_spec": self.api_spec,
            "login_path": self.login_path,
            "redirects": dict(list(self.redirects.items())[:20]),
            "flows": self.flows[:10],
            "navigation": [{"page": p.path, **edge} for p in self.pages[:max_pages] for edge in p.leads_to[:3]][:30],
            "identities": ["anon", "a", "b"] if self.has_login_flow else ["anon"],
        }


def _path(url: str) -> str:
    parts = urlsplit(url)
    return (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _skip(path: str) -> bool:
    return any(w in path.lower() for w in SKIP_WORDS)


class Recon:
    def __init__(self, session: AgentSession, gen: DataGenerator, max_pages: int = 40, max_depth: int = 3):
        self.s = session
        self.gen = gen
        self.max_pages = max_pages
        self.max_depth = max_depth
        self.map = AppMap(base_url=session.base_url, has_login_flow=session.login_fn is not None)
        self._spec_endpoints: dict[tuple[str, str], EndpointEntry] = {}

    async def _visit(self, path: str) -> dict[str, Any] | None:
        page = self.s.page
        try:
            url = resolve_same_origin(self.s.base_url, path)
            resp = await page.goto(url, wait_until="load", timeout=self.s.timeout_ms)
            await self.s.settle()
            data: dict[str, Any] = await page.evaluate(EXTRACT_JS)
        except (PlaywrightError, ActionRefused):
            return None
        if not self.s.same_origin(page.url):
            self.map.notes.append(f"{path} left the app (landed on {page.url[:100]}); not recorded")
            return None  # an open redirect or a link elsewhere: never part of the app map
        data["final_path"] = _path(page.url)
        data["status"] = resp.status if resp is not None else None
        return data

    def _record_page(self, path: str, data: dict[str, Any], who: str) -> None:
        entry = self.map.page(path)
        if entry is None:
            entry = PageEntry(path=path, title=data.get("title", ""))
            self.map.pages.append(entry)
        if who not in entry.seen_as:
            entry.seen_as.append(who)
        if not entry.forms:
            entry.forms = data.get("forms", [])
            entry.buttons = data.get("loose_buttons", [])
            entry.list_selectors = [f'[data-testid="{t}"]' for t in data.get("list_testids", [])]
        if "password" in str(data.get("forms", "")).lower() and "login" in path.lower():
            self.map.login_path = self.map.login_path or path

    async def crawl(self, who: str, seeds: list[str]) -> None:
        if who != "anon":
            await self.s.execute_switch(who)
        queue: deque[tuple[str, int]] = deque((p, 0) for p in seeds)
        seen: set[str] = set()
        while queue and len([p for p in self.map.pages if who in p.seen_as]) < self.max_pages:
            path, depth = queue.popleft()
            if path in seen or _skip(path):
                continue
            seen.add(path)
            data = await self._visit(path)
            if data is None:
                continue
            final = data["final_path"]
            if final != path:
                # Redirected (trailing slash, locale prefix, login wall...): remember it and crawl the target under
                # its own path. The redirecting path itself is not a page for this identity (the auth check uses
                # exactly that), but its target is.
                self.map.redirects[path] = final
                if final in seen or _skip(final):
                    continue
                seen.add(final)
                path = final
            self._record_page(path, data, who)
            if depth >= self.max_depth:
                continue
            for href in data.get("links", []):
                with contextlib.suppress(ActionRefused):
                    # Resolved against the current page, then fenced to the app itself (not to the page's origin).
                    target = _path(resolve_same_origin(self.s.base_url, urljoin(self.s.page.url, href))).split("#")[0]
                    if target not in seen:
                        queue.append((target, depth + 1))
            for target in await self._follow_nav_buttons(path, who):
                if target not in seen:
                    queue.append((target, depth + 1))

    async def _follow_nav_buttons(self, path: str, who: str, limit: int = 3) -> list[str]:
        """Click buttons that look navigational (Next, Continue, Get started...) and record where they lead."""
        entry = self.map.page(path)
        page = self.s.page
        buttons: list[dict[str, str]] = []
        with contextlib.suppress(PlaywrightError):
            buttons = await page.evaluate(NAV_BUTTONS_JS)
        targets: list[str] = []
        clicked = 0
        for b in buttons:
            text, selector = b.get("text", ""), b.get("selector", "")
            if clicked >= limit or not selector or not NAV_WORDS.match(text) or _skip(text):
                continue
            if _path(page.url) != path and await self._visit(path) is None:
                break
            clicked += 1
            with contextlib.suppress(PlaywrightError):
                await self.s.page.locator(selector).first.click(timeout=3000)
                await self.s.settle()
            to = _path(self.s.page.url).split("#")[0]
            if to != path and self.s.same_origin(self.s.page.url):
                edge = {"button": selector, "text": text, "to": to}
                if entry is not None and edge not in entry.leads_to:
                    entry.leads_to.append(edge)
                targets.append(to)
        return targets

    async def _submit(self, form: dict[str, Any]) -> bool:
        """Fill a form on the current page with ordinary generated values and submit it."""
        page = self.s.page
        try:
            for f in form.get("fields", []):
                sel, typ = f.get("selector"), (f.get("type") or "").lower()
                if not sel or typ in ("hidden", "submit", "button", "checkbox", "radio"):
                    continue
                if typ == "file":
                    await page.locator(sel).first.set_input_files(self.gen.resolve("{file}"), timeout=3000)
                elif f.get("tag") == "select":
                    continue
                else:
                    value = {"email": "{email}", "password": "{password}", "number": "{number}"}.get(typ, "{text}")
                    await page.locator(sel).first.fill(self.gen.resolve(value), timeout=3000)
            await page.locator(form["buttons"][0]["selector"]).first.click(timeout=3000)
            await self.s.settle()
            return True
        except PlaywrightError:
            return False

    async def probe_forms(self, who: str, max_chain: int = 4) -> None:
        """Submit each form once with ordinary generated values, to reveal the endpoints behind it. When a submit
        lands on another page with its own form (a checkout, a wizard), keep going and record the sequence."""
        if who != "anon":
            await self.s.execute_switch(who)
        for entry in list(self.map.pages):
            if who not in entry.seen_as:
                continue
            for form in entry.forms:
                if not form.get("buttons") or _skip(str(form.get("action", ""))):
                    continue
                if await self._visit(entry.path) is None:
                    continue
                steps: list[dict[str, str]] = []
                here, current = entry.path, form
                while len(steps) < max_chain and await self._submit(current):
                    to = _path(self.s.page.url).split("#")[0]
                    steps.append({"page": here, "form": str(current.get("selector")), "to": to})
                    if to == here or not self.s.same_origin(self.s.page.url) or _skip(to):
                        break
                    data: dict[str, Any] = {}
                    with contextlib.suppress(PlaywrightError):
                        data = await self.s.page.evaluate(EXTRACT_JS)
                    nxt = next(
                        (f for f in data.get("forms", []) if f.get("buttons") and not _skip(str(f.get("action", "")))),
                        None,
                    )
                    if nxt is None:
                        break
                    if self.map.page(to) is None:
                        self._record_page(to, {**data, "final_path": to}, who)
                    here, current = to, nxt
                if len(steps) >= 2:
                    flow = {"kind": "form_sequence", "start": entry.path, "steps": steps, "end": steps[-1]["to"]}
                    if flow not in self.map.flows:
                        self.map.flows.append(flow)

    def button_flows(self, max_len: int = 5) -> None:
        """Chains of navigational buttons (Next -> Next -> Finish) become flows in the app map."""
        edges = {p.path: p.leads_to for p in self.map.pages if p.leads_to}
        targets = {e["to"] for es in edges.values() for e in es}
        for start in edges:
            if start in targets:
                continue  # only chains that start somewhere nothing leads to
            steps: list[dict[str, str]] = []
            here, seen = start, {start}
            while here in edges and len(steps) < max_len:
                edge = edges[here][0]
                steps.append({"page": here, "button": edge["button"], "to": edge["to"]})
                if edge["to"] in seen:
                    break
                seen.add(edge["to"])
                here = edge["to"]
            if len(steps) >= 2:
                self.map.flows.append({"kind": "button_sequence", "start": start, "steps": steps, "end": here})

    async def auth_boundary(self) -> None:
        """Pages seen only as a signed-in user: does a logged-out visitor get them too?"""
        await self.s.execute_switch("anon")
        for entry in self.map.pages:
            if "anon" in entry.seen_as or "a" not in entry.seen_as:
                continue
            data = await self._visit(entry.path)
            if data is None:
                entry.anon_outcome = "error"  # unknown: an error is not evidence that login is needed
            elif data["final_path"] != entry.path:
                entry.anon_outcome, entry.requires_login = f"redirect -> {data['final_path']}", True
                entry.auth_evidence = "redirect"
            elif (data.get("status") or 200) in (401, 403):
                entry.anon_outcome, entry.requires_login = str(data["status"]), True
                entry.auth_evidence = str(data["status"])
            else:
                entry.anon_outcome = str(data.get("status") or 200)

    async def record_shells(self) -> None:
        """What a logged-out visitor gets anyway: the home page, the login page and an unknown route (a
        single-page app answers every route with the same shell). auth_bypass ignores anon pages like these."""
        probe = f"/sentri-unknown-{secrets.token_hex(4)}"
        for path in dict.fromkeys(["/", self.map.login_path or "/login", probe]):
            status, text = await self._get(path, "anon")
            if status is not None and 200 <= status < 300 and text.strip():
                self.map.anon_shells.append(redact(text[:20000]))

    async def from_spec(self) -> None:
        """Read an OpenAPI/Swagger spec if the app publishes one: every endpoint, its body and auth."""
        spec = None
        for path in SPEC_PATHS:
            status, text = await self._get(path, "anon")
            if status == 200 and text.lstrip().startswith("{"):
                with contextlib.suppress(ValueError):
                    data = json.loads(text)
                    if isinstance(data, dict) and isinstance(data.get("paths"), dict):
                        spec, self.map.api_spec = data, path
                        break
        if spec is None:
            return
        schemas = (spec.get("components") or {}).get("schemas") or spec.get("definitions") or {}
        global_security = spec.get("security")
        for raw_path, ops in spec["paths"].items():
            if not isinstance(ops, dict):
                continue
            for method, op in ops.items():
                if method.upper() not in HTTP_METHODS or not isinstance(op, dict):
                    continue
                example = re.sub(r"\{[^}]+\}", "1", raw_path)
                security = op.get("security", global_security)
                self._spec_endpoints[(method.upper(), normalize_path(example))] = EndpointEntry(
                    method=method.upper(),
                    path=normalize_path(example),
                    examples=[example],
                    requires_login=any(bool(s) for s in (security or [])),
                    state_changing=method.upper() in ("POST", "PUT", "PATCH", "DELETE"),
                    body_fields=_body_fields(op, schemas),
                    query_params=[q.get("name", "") for q in op.get("parameters", []) if q.get("in") == "query"],
                    from_spec=True,
                )

    async def _get(self, path: str, who: str) -> tuple[int | None, str]:
        """Unrecorded GET as `who` (with its token, if any)."""
        try:
            ctx = self.s.contexts[who]
            headers = self.s.identity_headers.get(who, {})
            resp = await ctx.request.get(
                self.s.base_url + path, headers=headers, max_redirects=0, timeout=self.s.timeout_ms
            )
            return resp.status, await resp.text()
        except (PlaywrightError, KeyError):
            return None, ""

    async def probe_endpoints(self) -> None:
        """Call parameter-free GET endpoints logged out and logged in: the auth boundary, by behaviour."""
        for ep in self._spec_endpoints.values():
            if ep.method != "GET" or ":id" in ep.path or ep.query_params and "month" in ep.query_params:
                continue
            anon, _ = await self._get(ep.path, "anon")
            user, _ = await self._get(ep.path, "a") if "a" in self.s.contexts else (None, "")
            for st in (anon, user):
                if st is not None and st not in ep.statuses:
                    ep.statuses.append(st)
            if anon in (401, 403) and user is not None and 200 <= user < 300:
                ep.requires_login = True
                ep.auth_evidence = str(anon)
            elif anon is not None and 200 <= anon < 300:
                ep.requires_login = False  # behaviour wins over the spec

    def collect_endpoints(self) -> None:
        by_key: dict[tuple[str, str], EndpointEntry] = dict(self._spec_endpoints)
        for r in self.s.requests:
            if not self.s.same_origin(r.url) or r.resource_type not in ("fetch", "xhr", "document"):
                continue
            if r.resource_type == "document" and r.method == "GET":
                continue  # pages are in self.map.pages
            key = (r.method, normalize_path(r.url))
            ep = by_key.setdefault(key, EndpointEntry(method=r.method, path=key[1], content_type=r.content_type))
            ex = _path(r.url)
            if ex not in ep.examples and len(ep.examples) < 5:
                ep.examples.append(ex)
            if r.status is not None and r.status not in ep.statuses:
                ep.statuses.append(r.status)
            if r.identity not in ep.seen_as:
                ep.seen_as.append(r.identity)
            if len(ep.request_ids) < 5:
                ep.request_ids.append(r.id)
            ep.state_changing = ep.state_changing or r.method in ("POST", "PUT", "PATCH", "DELETE")
        self.map.endpoints = sorted(by_key.values(), key=lambda e: (e.path, e.method))

    async def run(self) -> AppMap:
        await self.from_spec()
        await self.crawl("anon", ["/"])
        if self.map.has_login_flow:
            await self.crawl("a", ["/", *[p.path for p in self.map.pages]])
            await self.probe_forms("a")
            await self.auth_boundary()
        else:
            await self.probe_forms("anon")
        await self.probe_endpoints()
        self.button_flows()
        await self.record_shells()
        self.collect_endpoints()
        if not self.map.pages:
            self.map.notes.append("no pages could be loaded")
        return self.map
