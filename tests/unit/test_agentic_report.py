from __future__ import annotations

from fastapi.testclient import TestClient

from sentri.dashboard import create_app
from sentri.ledger import Ledger
from sentri.report import build_report
from sentri.report.agentic import agentic_markdown, attack_tree
from sentri.verdict.scorer import compute


def seeded(tmp_path):
    lg = Ledger(tmp_path / "l.db")
    run = lg.create_run("c1", "main", "fp", "ch")
    lg.save_app_map(run, {"pages": [{"path": "/tasks", "requires_login": True}, {"path": "/"}], "endpoints": [{}]})
    lg.save_strategy(
        run,
        "llm",
        {
            "strategy": {
                "app_kind": "task tracker",
                "hypotheses": [{"id": "idor", "title": "Other users' tasks are visible"}],
            }
        },
    )
    lg.add_step(
        run,
        {
            "step": 1,
            "hypothesis_id": "idor",
            "identity": "a",
            "thought": "create a private task",
            "action": {"kind": "navigate", "path": "/tasks/new"},
            "action_text": "navigate('/tasks/new')",
            "ok": True,
        },
    )
    lg.add_step(
        run,
        {
            "step": 2,
            "parent_step": 1,
            "hypothesis_id": "idor",
            "identity": "b",
            "thought": "can b see it?",
            "action": {"kind": "navigate", "path": "/tasks"},
            "action_text": "navigate('/tasks')",
            "ok": True,
            "signals": [{"oracle_id": "cross_user_exposure", "detail": "user b received user a's data"}],
        },
    )
    lg.add_step(
        run,
        {
            "step": 3,
            "hypothesis_id": "blank",
            "identity": "anon",
            "action_text": "type",
            "ok": False,
            "error": "selector not found",
        },
    )
    lg.add_finding(
        run,
        {
            "hypothesis_id": "idor",
            "title": "Other users' tasks are visible",
            "oracle_ids": ["cross_user_exposure"],
            "repro": [],
            "from_step": 1,
            "to_step": 2,
            "verdict": "fail",
            "attempts": 3,
        },
    )
    lg.finish_run(run, "finished", 100, 1, 0, "llm", compute(["pass"], 0).__dict__)
    return lg, run


def test_attack_tree_nests_steps_under_their_parent():
    tree = attack_tree(
        [
            {"step": 1, "hypothesis_id": "h"},
            {"step": 2, "parent_step": 1, "hypothesis_id": "h"},
            {"step": 3, "hypothesis_id": "g"},
        ]
    )
    assert [g["hypothesis_id"] for g in tree] == ["h", "g"]
    assert tree[0]["paths"][0]["children"][0]["step"] == 2


def test_markdown_shows_findings_and_the_path(tmp_path):
    lg, run = seeded(tmp_path)
    md = agentic_markdown(lg, run)
    assert "Recon: 2 pages, 1 endpoints, 1 login-only (/tasks)" in md
    assert "❌ confirmed | Other users' tasks are visible | cross_user_exposure | 1-2" in md
    assert "- **idor** - Other users' tasks are visible" in md
    assert "    - 2. `[b] navigate('/tasks')` **⚑ user b received user a's data**" in md
    assert "_why: can b see it?_" in md and "(failed: selector not found)" in md
    assert "### Agentic exploration" in build_report(lg, run, None)


def test_dashboard_agentic_endpoint(tmp_path):
    lg, run = seeded(tmp_path)
    lg.close()
    client = TestClient(
        create_app(tmp_path / "l.db", tmp_path / "e", tmp_path / "live.jpg"), base_url="http://127.0.0.1"
    )
    data = client.get(f"/api/runs/{run}/agentic").json()
    assert data["findings"][0]["verdict"] == "fail" and data["tree"][0]["paths"][0]["children"]
    assert client.get("/api/runs/999/agentic").status_code == 404
