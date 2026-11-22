"""Proposes a draft demo contract from the README and a crawl. A human must approve it."""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from playwright.async_api import Error as PlaywrightError

from sentri.agent.recon import AppMap, Recon
from sentri.agent.session import AgentSession
from sentri.contract.loader import ContractError, parse_contract
from sentri.contract.placeholders import DataGenerator, seed_for
from sentri.llm import BudgetedProvider, ContractDraft, load_prompt
from sentri.llm.untrusted import build_user_prompt
from sentri.planner.crawler import PageInfo, crawl
from sentri.redact import redact
from sentri.runner.browser import BrowserManager
from sentri.sandbox.base import Sandbox

log = logging.getLogger(__name__)

TYPE_PLACEHOLDER = {"email": "{email}", "password": "{password}", "file": "{file}", "number": "{number}"}


@dataclass
class PlanResult:
    yaml_text: str
    source: str  # llm | heuristic
    unverified: dict[str, list[str]]
    errors: list[str]


def _placeholder(field: dict[str, Any]) -> str:
    if field["type"] in TYPE_PLACEHOLDER:
        return TYPE_PLACEHOLDER[field["type"]]
    name = (field["name"] + " " + field["label"]).lower()
    if "title" in name:
        return "{title}"
    if "name" in name:
        return "{name}"
    return "{text}"


def heuristic_flows(pages: list[PageInfo]) -> list[dict[str, Any]]:
    """Fallback without a model: one flow per form, success = still on a page with a body."""
    flows: list[dict[str, Any]] = []
    for page in pages:
        for i, form in enumerate(page.forms or [{"fields": page.loose_fields, "buttons": page.loose_buttons}]):
            if not form["fields"] or not form["buttons"]:
                continue
            steps: list[dict[str, Any]] = []
            for field in form["fields"]:
                if field["type"] == "file":
                    steps.append({"upload": {"selector": field["selector"], "file": "{file}"}})
                elif field["tag"] == "select":
                    continue
                elif field["type"] not in ("checkbox", "radio", "submit", "button"):
                    steps.append({"fill": {"selector": field["selector"], "value": _placeholder(field)}})
            steps.append({"click": form["buttons"][0]["selector"]})
            slug = re.sub(r"[^a-z0-9]+", "_", (page.path.strip("/") or "home").lower()).strip("_")[:30] or "home"
            flow_id = f"{slug}_form{i}" if i else slug
            if not re.match(r"^[a-z]", flow_id):
                flow_id = f"f_{flow_id}"
            flow: dict[str, Any] = {
                "id": flow_id[:40],
                "description": f"Submit the form on {page.path} (EDIT: describe the journey)",
                "start": page.path,
                "steps": steps,
                "success": {"selector_visible": "body"},
            }
            if page.list_testids:
                flow["list_selector"] = f'[data-testid="{page.list_testids[0]}"]'
            flows.append(flow)
    return flows[:5]


def _draft_to_flows(draft: ContractDraft) -> list[dict[str, Any]]:
    flows = []
    for f in draft.flows:
        steps: list[dict[str, Any]] = []
        for s in f.steps:
            if s.kind == "click":
                steps.append({"click": s.selector})
            elif s.kind == "goto":
                steps.append({"goto": s.path or s.value})
            else:
                body: dict[str, Any] = {k: v for k, v in s.model_dump(exclude={"kind"}).items() if v is not None}
                steps.append({s.kind: body})
        flow: dict[str, Any] = {
            "id": f.id,
            "description": f.description,
            "start": f.start,
            "steps": steps,
            "success": {k: v for k, v in f.success.model_dump().items() if v},
        }
        if f.requires_login:
            flow["requires_login"] = True
        if not f.attack:
            flow["attack"] = False
        if f.list_selector:
            flow["list_selector"] = f.list_selector
        if f.list_page:
            flow["list_page"] = f.list_page
        flows.append(flow)
    return flows


async def _verify_selectors(
    browser: BrowserManager, base_url: str, flows: list[dict[str, Any]]
) -> dict[str, list[str]]:
    """Re-visit each flow's start page and report selectors that do not exist there."""
    unverified: dict[str, list[str]] = {}
    context = await browser.new_context(fence_origin=base_url)
    page = await context.new_page()
    try:
        for flow in flows:
            try:
                await page.goto(base_url + "/" + flow["start"].lstrip("/"), wait_until="load", timeout=15000)
            except PlaywrightError:
                unverified[flow["id"]] = ["(start page did not load)"]
                continue
            missing = []
            for step in flow["steps"]:
                ((kind, body),) = step.items()
                selector = body if isinstance(body, str) else body.get("selector")
                if kind == "goto" or not selector:
                    continue
                try:
                    if await page.locator(selector).count() == 0:
                        missing.append(selector)
                except PlaywrightError:
                    missing.append(selector)
            if missing:
                unverified[flow["id"]] = missing
    finally:
        await context.close()
    return unverified


def pages_from_map(app_map: AppMap) -> list[PageInfo]:
    return [
        PageInfo(
            url=app_map.base_url + p.path,
            path=p.path,
            title=p.title,
            forms=p.forms,
            loose_buttons=p.buttons,
            list_testids=[s.split('"')[1] for s in p.list_selectors if '"' in s],
        )
        for p in app_map.pages
    ]


async def recon_map(browser: BrowserManager, sandbox: Sandbox, base_url: str) -> AppMap:
    """Recon as a logged-out visitor (there is no contract, so no login flow yet)."""
    tmp = Path(tempfile.mkdtemp(prefix="sentri-plan-"))
    session = AgentSession(browser, sandbox, base_url, None, tmp)
    try:
        await session.start()
        return await Recon(session, DataGenerator(seed_for("plan"), {}, tmp, "plan")).run()
    finally:
        await session.close()


async def plan(
    browser: BrowserManager,
    provider: BudgetedProvider,
    app_section: dict[str, Any],
    readme: str,
    sandbox: Sandbox | None = None,
) -> PlanResult:
    base_url = app_section.get("base_url") or f"http://localhost:{app_section['port']}"
    app_map: AppMap | None = None
    if sandbox is not None:
        app_map = await recon_map(browser, sandbox, base_url)
        pages = pages_from_map(app_map)
    else:
        context = await browser.new_context(fence_origin=base_url)
        page = await context.new_page()
        try:
            pages = await crawl(page, base_url)
        finally:
            await context.close()

    flows: list[dict[str, Any]] = []
    login_flow: str | None = None
    source = "heuristic"
    if provider.available:
        request: dict[str, Any] = {"readme": readme[:20000], "pages": [p.to_dict() for p in pages]}
        if app_map is not None:
            request["app_map"] = app_map.summary()  # includes endpoints seen behind forms and auth boundaries
        prompt = build_user_prompt({"base_url": base_url}, request)
        result = await provider.complete_json(
            "planner", load_prompt("planner"), prompt, ContractDraft, max_tokens=16000
        )
        if result.ok and result.value is not None and result.value.flows:
            flows = _draft_to_flows(result.value)
            login_flow = result.value.login_flow
            source = "llm"
        else:
            log.warning("planner model unavailable (%s); using heuristic flows", result.error)
    if not flows:
        flows = heuristic_flows(pages)

    unverified = await _verify_selectors(browser, base_url, flows)
    data: dict[str, Any] = {"version": 1, "app": app_section}
    if login_flow and any(f.get("requires_login") for f in flows):
        data["auth"] = {"login_flow": login_flow}
    data["flows"] = flows
    data["attacks_enabled"] = [
        "empty_input",
        "double_submit",
        "expired_session",
        "slow_network",
        "bad_file",
        "rapid_repeat",
    ]

    header = [
        "# DRAFT demo contract proposed by `sentri plan` "
        f"({'model' if source == 'llm' else 'heuristic, no model available'}).",
        "# Review every flow, fix selectors and success conditions, then run:",
        "#   sentri contract approve contract.draft.yaml --as <your name>",
    ]
    for flow_id, sels in unverified.items():
        header.append(f"# UNVERIFIED selectors in flow '{flow_id}': {', '.join(sels)}")
    text = "\n".join(header) + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    errors: list[str] = []
    try:
        parse_contract(yaml.safe_load(text))
    except ContractError as exc:
        errors = exc.errors
    return PlanResult(text, source, unverified, errors)


def read_readme(path: Path | None) -> str:
    if path is None or not path.exists():
        return ""
    return redact(path.read_text(encoding="utf-8", errors="replace"))
