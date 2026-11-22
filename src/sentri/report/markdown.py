"""report.md for CI comments (docs/reference/cli.md, "report.md format"). Plain code, no LLM."""

from __future__ import annotations

import json
from typing import Any

from sentri.ledger.ledger import Ledger
from sentri.report.agentic import agentic_markdown

MARKER = "<!-- sentri-report -->"
ICONS = {"pass": "✅", "fail": "❌", "flaky": "⚠️", "na": "N/A", "error": "❗"}


def _grid(rows: list[Any]) -> tuple[list[str], list[str], dict[tuple[str, str], Any]]:
    flows: list[str] = []
    attacks: list[str] = []
    cells: dict[tuple[str, str], Any] = {}
    for r in rows:
        if r["flow_id"] not in flows:
            flows.append(r["flow_id"])
        if r["attack_id"] not in attacks:
            attacks.append(r["attack_id"])
        cells[(r["flow_id"], r["attack_id"])] = r
    return flows, attacks, cells


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "0s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s" if m else f"{s}s"


def build_report(ledger: Ledger, run_id: int, base_branch: str | None = "main", evidence_url: str | None = None) -> str:
    run = ledger.run(run_id)
    if run is None:
        raise KeyError(f"run {run_id} not found")
    lines = [MARKER]
    commit = (run["commit_sha"] or "local")[:12]
    if run["status"] != "finished":
        lines += [f"## Sentri: run {run_id} {run['status']}", "", "The run did not finish; see the job log."]
        return "\n".join(lines) + "\n"

    breakdown = json.loads(run["breakdown"] or "{}")
    score = run["readiness"]
    delta = ""
    base = None
    if base_branch and run["branch"] != base_branch:
        base = ledger.conn.execute(
            "SELECT * FROM runs WHERE status='finished' AND branch=? AND id<? ORDER BY id DESC LIMIT 1",
            (base_branch, run_id),
        ).fetchone()
    if base is not None and base["readiness"] is not None and score is not None:
        diff = score - base["readiness"]
        arrow = "▲" if diff > 0 else ("▼" if diff < 0 else "=")
        delta = f" ({arrow} {abs(diff)} vs {base_branch} @ {(base['commit_sha'] or '')[:7]})"
    elif base_branch and run["branch"] != base_branch:
        delta = f" (no {base_branch} run yet)"
    lines.append(f"## Sentri readiness: {score if score is not None else 'n/a'}{delta}")
    lines += ["", f"`{breakdown.get('formula', '')}`", ""]
    if breakdown.get("agentic_findings"):
        counts = breakdown.get("agentic_counts") or {}
        what = ", ".join(f"{n} {sev.replace('_', ' ')}" for sev, n in counts.items())
        baseline = breakdown.get("baseline")
        lines += [
            f"Baseline suite: **{baseline if baseline is not None else 'n/a'}** · "
            f"Agentic impact: **-{breakdown.get('agentic_impact', 0)}** ({what}; confirmed by replay)",
            "",
        ]

    rows = ledger.checks_for_run(run_id, include_probes=False)
    # Newly failing: cells failing now that passed (or did not exist) in the base run.
    base_cells: dict[tuple[str, str], str] = {}
    if base is not None:
        for r in ledger.checks_for_run(base["id"], include_probes=False):
            base_cells[(r["flow_id"], r["attack_id"])] = r["score_verdict"]
    newly = []
    for r in rows:
        if r["score_verdict"] in ("fail", "flaky") and base_cells.get((r["flow_id"], r["attack_id"])) not in (
            "fail",
            "flaky",
        ):
            bugs: list[Any] = list(ledger.bugs_for_check(r["id"])) or [None]
            for b in bugs:
                newly.append(
                    f"| {r['flow_id']} | {r['attack_id']} | {r['score_verdict']} | "
                    f"{(b['oracle_id'] + ': ' + (b['sample_detail'] or '')) if b else r['oracle_hits']} | "
                    f"{b['id'] if b else ''} |"
                )
    if newly:
        lines += [
            "### Newly failing",
            "| Flow | Attack | Verdict | Oracle | Bug |",
            "|---|---|---|---|---|",
            *newly,
            "",
        ]

    regressions = ledger.regressions_in_run(run_id)
    if regressions:
        lines.append("### Regressions")
        for b in regressions:
            lines.append(
                f"- {b['id']} `{b['flow_id']} x {b['attack_id']}` came back at {commit}. "
                f"Regressions: {b['regressions']}"
            )
        lines.append("")

    flows, attacks, cells = _grid(rows)
    if flows:
        lines += ["### Grid", "| Flow | " + " | ".join(attacks) + " |", "|---" * (len(attacks) + 1) + "|"]
        for f in flows:
            row = [ICONS.get(cells[(f, a)]["score_verdict"], "?") if (f, a) in cells else "" for a in attacks]
            lines.append(f"| {f} | " + " | ".join(row) + " |")
        lines.append("")

    agentic = agentic_markdown(ledger, run_id)
    if agentic:
        lines.append(agentic)

    probes = [r for r in ledger.checks_for_run(run_id) if r["is_probe"] and r["flow_id"] != "agentic"]
    probe_fails = sum(1 for r in probes if r["verdict"] == "fail")
    reused = sum(1 for r in rows if r["reused_from_check_id"])
    lines.append(
        f"Flaky: {breakdown.get('flaky', 0)} · N/A: {breakdown.get('na', 0)} · Errors: {breakdown.get('errors', 0)}"
        f" · Reused: {reused} · Probes: {len(probes)} ({probe_fails} fail)"
        f" · LLM: {run['llm_mode']}, ${run['llm_cost_usd'] or 0:.2f} · Duration: {_fmt_duration(run['duration_s'])}"
    )
    if evidence_url:
        lines.append(f"Evidence: {evidence_url}")
    return "\n".join(lines) + "\n"


def efficiency_table(ledger: Ledger, limit: int = 20) -> str:
    lines = ["| Run | Commit | Duration | LLM $ | Checks run | Reused |", "|---|---|---|---|---|---|"]
    for run in ledger.runs(limit):
        rows = ledger.checks_for_run(run["id"])
        ran = sum(1 for r in rows if r["verdict"] not in ("na",) and not r["reused_from_check_id"])
        reused = sum(1 for r in rows if r["reused_from_check_id"])
        lines.append(
            f"| {run['id']} | {(run['commit_sha'] or '')[:7]} | {_fmt_duration(run['duration_s'])} | "
            f"{run['llm_cost_usd'] or 0:.3f} | {ran} | {reused} |"
        )
    return "\n".join(lines) + "\n"
