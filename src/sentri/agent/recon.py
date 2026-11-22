"""Recon: explore the app before attacking it and produce an app map.

Crawls every reachable same-origin page as `anon`, then as user `a` when the contract has a login flow;
submits each form once with harmless generated values so the endpoints behind buttons show up in network
traffic; and marks which pages need login (reachable as `a`, redirected or refused as `anon`). Links that look
destructive (logout, delete, ...) are not followed during recon.
"""

from __future__ import annotations

import contextlib
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from pydantic import BaseModel, Field

from sentri.agent.actions import ActionRefused, resolve_same_origin
from sentri.agent.session import AgentSession
from sentri.contract.placeholders import DataGenerator
from sentri.planner.crawler import EXTRACT_JS
from sentri.verdict.signature import normalize_path

SKIP_WORDS = ("logout", "log-out", "signout", "sign-out", "delete", "remove", "destroy", "unsubscribe")


class PageEntry(BaseModel):
    path: str
    title: str = ""
    seen_as: list[str] = Field(default_factory=list)
    requires_login: bool = False
    anon_outcome: str | None = None  # e.g. "redirect -> /login", "401", "200"
    forms: list[dict[str, Any]] = Field(default_factory=list)
    buttons: list[dict[str, Any]] = Field(default_factory=list)
    list_selectors: list[str] = Field(default_factory=list)


class EndpointEntry(BaseModel):
    method: str
    path: str  # normalized template, ids replaced by :id
    examples: list[str] = Field(default_factory=list)
    statuses: list[int] = Field(default_factory=list)
    seen_as: list[str] = Field(default_factory=list)
    content_type: str | None = None
    state_changing: bool = False
    request_ids: list[int] = Field(default_factory=list)


class AppMap(BaseModel):
    base_url: str
    pages: list[PageEntry] = Field(default_factory=list)
    endpoints: list[EndpointEntry] = Field(default_factory=list)
    login_path: str | None = None
    has_login_flow: bool = False
    notes: list[str] = Field(default_factory=list)

    def page(self, path: str) -> PageEntry | None:
        return next((p for p in self.pages if p.path == path), None)

    def known_paths(self) -> set[str]:
        return (
            {p.path for p in self.pages}
            | {e.path for e in self.endpoints}
            | {x for e in self.endpoints for x in e.examples}
        )

    def summary(self, max_pages: int = 40) -> dict[str, Any]:
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
                    "statuses": e.statuses,
                    "as": e.seen_as,
                    "state_changing": e.state_changing,
                    "observed_request_ids": e.request_ids[:3],
                }
                for e in self.endpoints
            ],
            "login_path": self.login_path,
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

    async def _visit(self, path: str) -> dict[str, Any] | None:
        page = self.s.page
        try:
            url = resolve_same_origin(self.s.base_url, path)
            resp = await page.goto(url, wait_until="load", timeout=self.s.timeout_ms)
            await self.s.settle()
            data: dict[str, Any] = await page.evaluate(EXTRACT_JS)
        except (PlaywrightError, ActionRefused):
            return None
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
                continue  # redirected: recorded by the auth check, crawled under its own path
            self._record_page(path, data, who)
            if depth >= self.max_depth:
                continue
            for href in data.get("links", []):
                with contextlib.suppress(ActionRefused):
                    target = _path(resolve_same_origin(self.s.page.url, href)).split("#")[0]
                    if target not in seen:
                        queue.append((target, depth + 1))

    async def probe_forms(self, who: str) -> None:
        """Submit each form once with ordinary generated values, to reveal the endpoints behind it."""
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
                page = self.s.page
                with contextlib.suppress(PlaywrightError):
                    for f in form.get("fields", []):
                        sel, typ = f.get("selector"), (f.get("type") or "").lower()
                        if not sel or typ in ("hidden", "submit", "button", "checkbox", "radio"):
                            continue
                        if typ == "file":
                            await page.locator(sel).first.set_input_files(self.gen.resolve("{file}"), timeout=3000)
                        elif f.get("tag") == "select":
                            continue
                        else:
                            value = {"email": "{email}", "password": "{password}", "number": "{number}"}.get(
                                typ, "{text}"
                            )
                            await page.locator(sel).first.fill(self.gen.resolve(value), timeout=3000)
                    await page.locator(form["buttons"][0]["selector"]).first.click(timeout=3000)
                    await self.s.settle()

    async def auth_boundary(self) -> None:
        """Pages seen only as a signed-in user: does a logged-out visitor get them too?"""
        await self.s.execute_switch("anon")
        for entry in self.map.pages:
            if "anon" in entry.seen_as or "a" not in entry.seen_as:
                continue
            data = await self._visit(entry.path)
            if data is None:
                entry.anon_outcome, entry.requires_login = "error", True
            elif data["final_path"] != entry.path:
                entry.anon_outcome, entry.requires_login = f"redirect -> {data['final_path']}", True
            elif (data.get("status") or 200) in (401, 403):
                entry.anon_outcome, entry.requires_login = str(data["status"]), True
            else:
                entry.anon_outcome = str(data.get("status") or 200)

    def collect_endpoints(self) -> None:
        by_key: dict[tuple[str, str], EndpointEntry] = {}
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
        await self.crawl("anon", ["/"])
        if self.map.has_login_flow:
            await self.crawl("a", ["/", *[p.path for p in self.map.pages]])
            await self.probe_forms("a")
            await self.auth_boundary()
        else:
            await self.probe_forms("anon")
        self.collect_endpoints()
        if not self.map.pages:
            self.map.notes.append("no pages could be loaded")
        return self.map
