"""Understand phase: the model reads the app map and plans app-specific attack hypotheses.

Plain code keeps the plan honest: every hypothesis must reference at least one page or endpoint that recon
actually found; anything generic or invented is dropped before exploration starts.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, Field

from sentri.agent.recon import AppMap
from sentri.llm import BudgetedProvider, load_prompt
from sentri.verdict.signature import normalize_path

Category = Literal["input", "access", "flow", "resource", "other"]
_METHOD_TARGET = re.compile(r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(\S+)$", re.IGNORECASE)


class Hypothesis(BaseModel):
    id: str = Field(max_length=60)
    title: str = Field(max_length=300)
    category: Category = "other"
    targets: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=1000)
    first_moves: list[str] = Field(default_factory=list, max_length=6)
    priority: int = Field(default=3, ge=1, le=5)


class Strategy(BaseModel):
    app_kind: str = Field(default="", max_length=200)
    sensitive_areas: list[str] = Field(default_factory=list, max_length=12)
    fragile_flows: list[str] = Field(default_factory=list, max_length=12)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=20)


class PlannedStrategy(BaseModel):
    strategy: Strategy
    dropped: list[str] = Field(default_factory=list)  # hypotheses removed by code, with the reason
    source: Literal["llm", "none"] = "llm"
    error: str | None = None


def _known_targets(app_map: AppMap) -> tuple[set[str], set[tuple[str, str]]]:
    pages = {p.path.split("?")[0] for p in app_map.pages}
    pages |= {normalize_path(p) for p in pages}
    endpoints = {(e.method.upper(), e.path) for e in app_map.endpoints}
    endpoints |= {(e.method.upper(), normalize_path(x)) for e in app_map.endpoints for x in e.examples}
    return pages, endpoints


def target_exists(target: str, app_map: AppMap) -> bool:
    pages, endpoints = _known_targets(app_map)
    target = target.strip()
    m = _METHOD_TARGET.match(target)
    if m:
        method, path = m.group(1).upper(), m.group(2).split("?")[0]
        return (method, normalize_path(path)) in endpoints or (method, path) in endpoints
    path = target.split("?")[0]
    return path in pages or normalize_path(path) in pages or any(path == e[1] for e in endpoints)


def validate(strategy: Strategy, app_map: AppMap, limit: int = 12) -> tuple[Strategy, list[str]]:
    kept: list[Hypothesis] = []
    dropped: list[str] = []
    seen_ids: set[str] = set()
    for h in strategy.hypotheses:
        real = [t for t in h.targets if target_exists(t, app_map)]
        if not real:
            dropped.append(f"{h.id}: no target found in the app map ({', '.join(h.targets) or 'none given'})")
            continue
        hid = re.sub(r"[^a-z0-9_]+", "_", h.id.lower()).strip("_")[:40] or f"h{len(kept) + 1}"
        while hid in seen_ids:
            hid += "_2"
        seen_ids.add(hid)
        kept.append(h.model_copy(update={"id": hid, "targets": real}))
    kept.sort(key=lambda h: h.priority)
    return strategy.model_copy(update={"hypotheses": kept[:limit]}), dropped


async def plan_strategy(app_map: AppMap, provider: BudgetedProvider, readme: str = "") -> PlannedStrategy:
    if not provider.available:
        return PlannedStrategy(strategy=Strategy(), source="none", error="no model: agentic mode needs one")
    request = {"app_map": app_map.summary(), "readme": readme[:8000]}
    result = await provider.complete_json(
        "strategist", load_prompt("strategist"), json.dumps(request, indent=1), Strategy, max_tokens=8000
    )
    if not result.ok or result.value is None:
        return PlannedStrategy(strategy=Strategy(), source="llm", error=result.error or "no strategy")
    strategy, dropped = validate(result.value, app_map)
    return PlannedStrategy(strategy=strategy, dropped=dropped, source="llm")
