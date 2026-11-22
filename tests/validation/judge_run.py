"""Judge a Sentri run against the harness-only expectations (flow_shop_expected.yaml). Harness side: Sentri never
reads this file or the expectations.

    python tests/validation/judge_run.py LEDGER.db [RUN_ID] [--json OUT]

Prints what the tester did and found: steps by origin (model-chosen vs Sentri's probes vs code fallbacks), the
states it reached, every candidate with its origin and replay verdict, model usage, and for each expected failure
whether a confirmed finding matches it (same route and oracle) and who found it. A fixed probe never counts as an
AI discovery.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml

from sentri.ledger import Ledger

EXPECTED = yaml.safe_load((Path(__file__).parent / "flow_shop_expected.yaml").read_text(encoding="utf-8"))


def judge(db: str, run_id: int | None = None) -> dict[str, Any]:
    lg = Ledger(db)
    run = lg.run(run_id) if run_id else lg.latest_run()
    assert run is not None, "no finished run"
    rid = run["id"]
    steps = lg.steps_for_run(rid)
    findings = lg.findings_for_run(rid)
    checks = {c["id"]: c for c in lg.checks_for_run(rid)}
    coverage = json.loads(run["coverage"] or "{}")
    usage = json.loads(run["usage"] or "{}")
    confirmed = []
    for f in findings:
        check = checks.get(f["check_id"]) if f.get("check_id") else None
        confirmed.append(
            {
                "title": f["title"],
                "verdict": f["verdict"],
                "origin": f.get("origin"),
                "replay_state": f.get("replay_state"),
                "key": check["attack_id"] if check is not None else None,
                "oracles": f["oracle_ids"],
                "steps": f"{f['from_step']}-{f['to_step']}" if f.get("from_step") else "re-check",
            }
        )
    matches = []
    for exp in EXPECTED["failures"]:
        hit = next(
            (
                c
                for c in confirmed
                if c["verdict"] in ("fail", "flaky")
                and c["key"]
                and c["key"] == f"{exp['expected_oracle']} {exp['route']}"
            ),
            None,
        )
        matches.append(
            {
                "expected": exp["id"],
                "found": hit is not None,
                "by": hit["origin"] if hit else None,
                "ai_discovery": bool(hit and hit["origin"] == "ai"),
            }
        )
    return {
        "run_id": rid,
        "readiness": run["readiness"],
        "status": json.loads(run["breakdown"] or "{}").get("status"),
        "duration_s": run["duration_s"],
        "usage": {
            k: usage.get(k) for k in ("calls", "input_tokens", "output_tokens", "retries", "cost_usd", "stop_reason")
        },
        "roles": {
            r: {k: u[k] for k in ("calls", "input_tokens", "output_tokens", "retries")}
            for r, u in (usage.get("roles") or {}).items()
        },
        "exploration": (coverage.get("agentic") or {}),
        "steps": [
            {
                "step": s["step"],
                "origin": s.get("origin"),
                "as": s["identity"],
                "action": s["action_text"],
                "ok": bool(s["ok"]),
                "error": s["error"],
                "why": (s["thought"] or "")[:160],
                "signals": [x.get("oracle_id") for x in s["signals"]],
            }
            for s in steps
        ],
        "findings": confirmed,
        "expected_failures": matches,
    }


if __name__ == "__main__":
    argv = sys.argv[1:]
    json_out = argv[argv.index("--json") + 1] if "--json" in argv else None
    args = [a for a in argv if a != "--json" and a != json_out]
    out = judge(args[0], int(args[1]) if len(args) > 1 else None)
    text = json.dumps(out, indent=1, default=str)
    if json_out:
        Path(json_out).write_text(text, encoding="utf-8")
    print(text)
