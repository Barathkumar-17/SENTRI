from __future__ import annotations

import json

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
    assert "❌ confirmed | Other users' tasks are visible | - | cross_user_exposure | 1-2" in md
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


def test_score_and_report_include_confirmed_agentic_findings(tmp_path):
    from sentri.ledger import CheckRecord
    from sentri.runner.orchestrator import score_run

    lg = Ledger(tmp_path / "s.db")
    run = lg.create_run("c1", "main", "fp", "ch")
    for verdict in ("pass", "pass", "pass", "fail"):
        lg.insert_check(CheckRecord(run, "signup", f"a{verdict}{lg.conn.total_changes}", verdict, attempts=3))
    check = lg.insert_check(
        CheckRecord(
            run,
            "agentic",
            "cross_user_exposure GET /tasks",
            "fail",
            oracle_hits="cross_user_exposure:3",
            attempts=3,
            is_probe=True,
        )
    )
    lg.add_finding(
        run,
        {
            "title": "B sees A's tasks",
            "oracle_ids": ["cross_user_exposure"],
            "verdict": "fail",
            "attempts": 3,
            "check_id": check,
            "from_step": 1,
            "to_step": 4,
        },
    )
    b = score_run(lg, run, 0, agentic_ran=True)
    assert (b.baseline, b.agentic_impact, b.score) == (75, 15, 60)
    lg.finish_run(run, "finished", b.score, 1, 0, "llm", b.__dict__)
    md = build_report(lg, run)
    assert "## Sentri readiness: 60" in md
    assert "Baseline suite: **75** · Agentic impact: **-15** (1 data exposure" in md
    assert "| -15 (data exposure) |" in md


def test_findings_sharing_a_key_in_one_run_never_fake_a_regression(tmp_path):
    """Found by the Juice Shop proof run: two findings with the same key but different signatures made the
    first one's bug 'fixed' and then 'regress' inside a single run."""
    from sentri.agent.replay import record
    from sentri.verdict.engine import CheckVerdict, OracleHit

    lg = Ledger(tmp_path / "k.db")
    run = lg.create_run("c1", "main", "fp", "ch")
    key = "http_500 POST /rest/admin/application-configuration"
    first = CheckVerdict(
        "fail", 3, (OracleHit("http_500", 3, "sig-500", "500"), OracleHit("exception", 3, "sig-a", "a"))
    )
    second = CheckVerdict(
        "fail", 3, (OracleHit("http_500", 3, "sig-500", "500"), OracleHit("exception", 3, "sig-b", "b"))
    )
    run_hits: dict = {}
    events = []
    for v in (first, second):
        _c, ev = record(lg, run, "c1", {"title": "t", "repro": []}, v, key, run_hits)
        events += ev
    assert not any(e.kind in ("regression", "bug_fixed") for e in events)
    assert sorted(b["status"] for b in lg.bugs()) == ["open", "open", "open"]
    # a later, non-reproducing re-check of the same key in the same run does not close them either
    _c, ev = record(lg, run, "c1", {"title": "t", "repro": [], "recheck": True}, CheckVerdict("pass", 3), key, run_hits)
    assert ev == [] and all(b["status"] == "open" for b in lg.bugs())


def test_report_says_insufficient_coverage_and_shows_what_was_measured(tmp_path):
    from sentri.ledger import CheckRecord
    from sentri.runner.orchestrator import coverage_record, score_run

    lg = Ledger(tmp_path / "c.db")
    run = lg.create_run("c1", "main", "", "ch")
    lg.insert_check(CheckRecord(run, "signup", "bad_file", "na", error_msg="no upload step"))
    b = score_run(lg, run, 0, agentic_ran=True)
    cov = coverage_record(
        lg.checks_for_run(run),
        {"steps": 8, "ai_steps": 5, "code_probe_steps": 1, "distinct_states": 4, "candidates": 3, "confirmed": 0},
        {"agentic": "done; exploration stopped: time budget used", "baseline": "done (1 cells)"},
        "none",
        None,
    )
    lg.finish_run(run, "finished", b.score, 1, None, "llm", b.__dict__, usage=None, coverage=cov)
    md = build_report(lg, run)
    assert "## Sentri readiness: insufficient coverage" in md and "readiness: 100" not in md
    assert "1 inapplicable" in md and "8 steps (5 chosen by the model, 1 code probes" in md
    assert "4 distinct states" in md and "stopped: time budget used" in md
    assert "live state" in md and "not a safety rating" in md
    assert "cost unknown" in md  # llm_cost_usd None: never shown as $0.00


def test_the_api_returns_usage_and_coverage_as_objects(tmp_path):
    lg = Ledger(tmp_path / "api.db")
    run = lg.create_run("c1", "main", "", "ch")
    b = compute(["pass"], 0)
    usage = {"calls": 3, "input_tokens": 10, "output_tokens": 2, "cost_usd": None, "roles": {}}
    coverage = {"baseline": {"checks": {"passed": 1}}, "agentic": None, "completion": {}, "replay_state": "restart"}
    lg.finish_run(run, "finished", b.score, 1, None, "llm", b.__dict__, usage=usage, coverage=coverage)
    lg.close()
    client = TestClient(
        create_app(tmp_path / "api.db", tmp_path / "e", tmp_path / "live.jpg"), base_url="http://127.0.0.1"
    )
    row = client.get(f"/api/runs/{run}").json()
    assert row["usage"]["calls"] == 3 and row["usage"]["cost_usd"] is None
    assert row["coverage"]["replay_state"] == "restart" and row["breakdown"]["status"] == "scored"


def test_findings_api_lists_across_runs_and_shows_replay_evidence(tmp_path):
    from sentri.agent.replay import record
    from sentri.verdict.engine import CheckVerdict, OracleHit

    lg = Ledger(tmp_path / "f.db")
    run = lg.create_run("abc1234", "main", "", "ch")
    attempt = tmp_path / "e" / "agentic-r1-1-a1.json"
    attempt.parent.mkdir(parents=True)
    attempt.write_text(
        json.dumps(
            {
                "completed": True,
                "notes": [],
                "oracles": [{"oracle_id": "http_500", "fired": True, "detail": "POST /x -> 500"}, {"fired": False}],
                "responses": [{"identity": "a", "method": "POST", "path": "/x", "status": 500}],
                "probes": [],
                "health": [],
                "logs": ["Traceback (most recent call last):"],
            }
        ),
        encoding="utf-8",
    )
    verdict = CheckVerdict("fail", 3, (OracleHit("http_500", 3, "sig", "POST /x -> 500"),))
    record(
        lg,
        run,
        "abc1234",
        {"title": "Crash on /x", "repro": [{"action": {"kind": "navigate", "path": "/x"}}], "origin": "ai"},
        verdict,
        "http_500 POST /x",
        {},
        "restart",
        [str(attempt), str(tmp_path / "gone.json")],
        ["note 1"],
    )
    lg.close()
    client = TestClient(
        create_app(tmp_path / "f.db", tmp_path / "e", tmp_path / "live.jpg"), base_url="http://127.0.0.1"
    )
    (listed,) = client.get("/api/findings").json()
    assert (
        listed["title"] == "Crash on /x" and listed["key"] == "http_500 POST /x" and listed["commit_sha"] == "abc1234"
    )
    assert listed["origin"] == "ai" and listed["replay_state"] == "restart" and listed["notes"] == ["note 1"]
    detail = client.get(f"/api/findings/{listed['id']}").json()
    first, second = detail["attempts"]
    assert first["completed"] and [o["oracle_id"] for o in first["oracles"]] == ["http_500"]
    assert second == {"attempt": 2, "missing": True}
    assert detail["bugs"] and detail["bugs"][0]["oracle_id"] == "http_500"
    assert client.get("/api/findings/999").status_code == 404
