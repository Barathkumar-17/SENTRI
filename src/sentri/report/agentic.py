"""Attack-tree report for agentic runs: the path the agent actually took, not a flat list of attacks.

Plain code over the ledger: recon summary, strategy, findings with verdicts, and every exploration step
nested under the step it followed. The model's reasoning is shown as reasoning, never as a verdict.
"""

from __future__ import annotations

import json
from typing import Any

from sentri.ledger.ledger import Ledger

ORIGIN_LABEL = {
    "ai": "model-chosen action",
    "ai_suspicion": "model suspicion",
    "code_probe": "Sentri probe",
    "code_fallback": "code fallback step",
}


def attack_tree(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group steps by hypothesis, then nest each step under its parent step."""
    nodes = {s["step"]: {**s, "children": []} for s in steps}
    groups: dict[str, list[dict[str, Any]]] = {}
    for s in steps:
        node = nodes[s["step"]]
        parent = s.get("parent_step")
        if parent is not None and parent in nodes:
            nodes[parent]["children"].append(node)
        else:
            groups.setdefault(s.get("hypothesis_id") or "exploration", []).append(node)
    return [{"hypothesis_id": hid, "paths": roots} for hid, roots in groups.items()]


def agentic_data(ledger: Ledger, run_id: int) -> dict[str, Any] | None:
    app_map = ledger.latest_app_map(run_id)
    steps = ledger.steps_for_run(run_id)
    findings = ledger.findings_for_run(run_id)
    strategy = ledger.strategy(run_id)
    if app_map is None and not steps and not findings:
        return None
    return {"app_map": app_map, "strategy": strategy, "findings": findings, "steps": steps, "tree": attack_tree(steps)}


def _line(node: dict[str, Any]) -> str:
    who = node.get("identity") or "?"
    text = node.get("action_text") or ""
    sig = node.get("signals") or []
    flag = " **⚑ " + "; ".join(s["detail"] for s in sig) + "**" if sig else ""
    err = f" (failed: {node['error'][:80]})" if node.get("error") else ""
    return f"{node['step']}. `[{who}] {text[:110]}`{err}{flag}"


def _render(node: dict[str, Any], depth: int, out: list[str], budget: list[int]) -> None:
    if budget[0] <= 0:
        return
    budget[0] -= 1
    out.append("  " * depth + "- " + _line(node))
    if node.get("thought"):
        out.append("  " * (depth + 1) + f"- _why: {node['thought'][:160]}_")
    for child in node["children"]:
        _render(child, depth + 1, out, budget)


def agentic_markdown(ledger: Ledger, run_id: int, max_steps: int = 80) -> str:
    data = agentic_data(ledger, run_id)
    if data is None:
        return ""
    lines = ["### Agentic exploration"]
    app_map = data["app_map"] or {}
    pages = app_map.get("pages", [])
    protected = [p["path"] for p in pages if p.get("requires_login")]
    lines.append(
        f"Recon: {len(pages)} pages, {len(app_map.get('endpoints', []))} endpoints, "
        f"{len(protected)} login-only ({', '.join(protected[:8])})."
    )
    strategy = data["strategy"] or {}
    planned = (strategy.get("strategy") or {}) if "strategy" in strategy else strategy
    hyps = planned.get("hypotheses", [])
    titles = {h["id"]: h["title"] for h in hyps}
    if planned.get("app_kind"):
        lines.append(f"Strategy: {planned['app_kind']}, {len(hyps)} hypotheses.")
    lines.append("")

    findings = data["findings"]
    if findings:
        run = ledger.run(run_id)
        breakdown = json.loads(run["breakdown"] or "{}") if run is not None else {}
        points = {i["key"]: i for i in breakdown.get("agentic_findings") or []}
        lines += [
            "#### Findings (replayed 3x from a clean state, judged by plain code)",
            "| Verdict | Finding | Found by | Oracles | Steps | Score impact |",
            "|---|---|---|---|---|---|",
        ]
        icon = {"fail": "❌ confirmed", "flaky": "⚠️ flaky", "unconfirmed": "· unconfirmed", "pass": "✅ fixed"}
        for f in findings:
            span = f"{f['from_step']}-{f['to_step']}" if f.get("from_step") else "re-check"
            check = ledger.check(f["check_id"]) if f.get("check_id") else None
            hit = points.get(check["attack_id"]) if check is not None else None
            impact = f"-{hit['points']:g} ({str(hit['severity']).replace('_', ' ')})" if hit else "-"
            found_by = ORIGIN_LABEL.get(f.get("origin") or "", f.get("origin") or "-")
            if f.get("replay_state") == "none":
                found_by += " (replayed on live state)"
            lines.append(
                f"| {icon.get(f['verdict'], f['verdict'])} | {f['title'] or ''} | {found_by} | {f['oracle_ids'] or '-'}"
                f" | {span} | {impact} |"
            )
        lines.append("")

    if data["tree"]:
        lines.append("#### Attack tree")
        budget = [max_steps]
        for group in data["tree"]:
            hid = group["hypothesis_id"]
            lines.append(f"- **{hid}**" + (f" - {titles[hid]}" if hid in titles else ""))
            for root in group["paths"]:
                _render(root, 1, lines, budget)
        if budget[0] <= 0:
            lines.append(f"- ... ({len(data['steps'])} steps in total; see the ledger or dashboard)")
        lines.append("")
    return "\n".join(lines) + "\n"
