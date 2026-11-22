"""Export a finished run from a ledger: the full agentic data (app map, strategy, every step, findings, attack
tree) as JSON, plus a one-line summary (time, cost, steps, findings).

    python examples/proof/export_run.py LEDGER.db OUT_DIR [RUN_ID]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sentri.ledger import Ledger
from sentri.redact import redact_obj
from sentri.report.agentic import agentic_data


def main(db: str, out_dir: str, run_id: int | None = None) -> None:
    ledger = Ledger(db)
    run = ledger.run(run_id) if run_id else ledger.latest_run()
    assert run is not None, "no finished run in this ledger"
    data = agentic_data(ledger, run["id"]) or {}
    checks = ledger.checks_for_run(run["id"])
    summary = {
        "run_id": run["id"],
        "status": run["status"],
        "readiness": run["readiness"],
        "breakdown": json.loads(run["breakdown"] or "{}"),
        "duration_s": run["duration_s"],
        "llm_cost_usd": run["llm_cost_usd"],
        "llm_mode": run["llm_mode"],
        "steps": len(data.get("steps", [])),
        "findings": [
            {k: f[k] for k in ("title", "verdict", "oracle_ids", "from_step", "to_step", "attempts")}
            for f in data.get("findings", [])
        ],
        "checks": [
            {"flow": c["flow_id"], "attack": c["attack_id"], "verdict": c["score_verdict"], "hits": c["oracle_hits"]}
            for c in checks
        ],
        "bugs": [dict(b) for b in ledger.bugs()],
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "agentic.json").write_text(json.dumps(redact_obj(data), indent=1, default=str), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(redact_obj(summary), indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("readiness", "duration_s", "llm_cost_usd", "steps")}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
