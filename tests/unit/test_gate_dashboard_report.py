from __future__ import annotations

from fastapi.testclient import TestClient

from sentri.contract import load_contract
from sentri.dashboard import create_app
from sentri.fixer.fixer import source_files_from_logs
from sentri.fixer.gate import check_no_test_files, diff_paths, heldout_payload, is_test_path
from sentri.ledger import CheckRecord, Ledger
from sentri.report import build_report
from sentri.verdict.engine import CheckVerdict, OracleHit
from sentri.verdict.scorer import compute
from tests.conftest import BUGGY_APP

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1 +1 @@
-x
+y
"""


def test_gate_test_file_detection():
    assert diff_paths(DIFF) == ["app.py"]
    assert check_no_test_files(DIFF).passed
    for p in ("tests/test_app.py", "src/foo_test.py", "web/__tests__/a.js", "a.spec.ts", "pkg/test/x.go"):
        assert is_test_path(p), p
    assert not is_test_path("src/contest.py")
    bad = DIFF.replace("app.py", "tests/test_app.py")
    assert not check_no_test_files(bad).passed
    assert not check_no_test_files("").passed


def test_heldout_differs_from_original():
    c = load_contract(BUGGY_APP / "contract.yaml")
    flow = c.flow("create_task")
    payload, _ = heldout_payload(flow, "bad_file", {"shape": "zero_byte", "filename": "empty.png"})
    assert payload != {"shape": "zero_byte", "filename": "empty.png"}
    timing, note = heldout_payload(flow, "double_submit", {})
    assert timing == {"gap_ms": "40"} and "weak" in note


def test_source_files_from_container_paths(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "app.py").write_text("x")
    found = source_files_from_logs(
        ['  File "/app/web/app.py", line 3, in f', '  File "/usr/lib/x.py", line 1'], tmp_path
    )
    assert found == [tmp_path / "web" / "app.py"]


def seeded_ledger(tmp_path):
    lg = Ledger(tmp_path / "l.db")
    base = lg.create_run("base", "main", "fp", "ch")
    lg.insert_check(CheckRecord(base, "signup", "double_submit", "pass"))
    lg.finish_run(base, "finished", 100, 1, 0, "fallback", compute(["pass"], 0).__dict__)
    run = lg.create_run("head", "feature", "fp2", "ch")
    cid = lg.insert_check(CheckRecord(run, "signup", "double_submit", "fail", oracle_hits="duplicates:3"))
    lg.insert_check(CheckRecord(run, "signup", "bad_file", "na"))
    lg.record_check_result(
        run,
        cid,
        "signup",
        "double_submit",
        "head",
        CheckVerdict("fail", 3, (OracleHit("duplicates", 3, "s1", "2 created"),)),
    )
    lg.add_event(run, "check_finished", "signup:double_submit: FAIL", "double click", {"verdict": "fail"})
    lg.finish_run(run, "finished", 0, 2, 0.01, "llm", compute(["fail", "na"], 0).__dict__)
    return lg, run


def test_report_markdown(tmp_path):
    lg, run = seeded_ledger(tmp_path)
    text = build_report(lg, run, "main")
    assert text.startswith("<!-- sentri-report -->")
    assert "readiness: 0 (▼ 100 vs main" in text
    assert "BUG-001" in text and "| signup | ❌ | N/A |" in text


def test_dashboard_api(tmp_path):
    lg, run = seeded_ledger(tmp_path)
    lg.close()
    app = create_app(tmp_path / "l.db", tmp_path / "evidence", tmp_path / "live.jpg")
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/").status_code == 200
    assert [r["id"] for r in client.get("/api/runs").json()] == [run, run - 1]
    grid = client.get(f"/api/runs/{run}/grid").json()
    assert grid["flows"] == ["signup"] and {c["verdict"] for c in grid["cells"]} == {"fail", "na"}
    assert client.get("/api/bugs").json()[0]["id"] == "BUG-001"
    assert client.post("/api/bugs/BUG-001/status", json={"status": "intended", "note": ""}).status_code == 422
    r = client.post("/api/bugs/BUG-001/status", json={"status": "intended", "note": "fine", "by": "alice"})
    assert r.status_code == 200 and r.json()["status"] == "intended"
    assert client.get("/api/bugs/BUG-001/history").json()[-1]["actor"] == "human"
    assert client.get("/api/live/screenshot.jpg").status_code == 204
    assert client.get("/evidence/../l.db").status_code == 404
    assert len(client.get("/api/trend").json()) == 2
    assert client.get(f"/api/events?run_id={run}").json()[0]["reason"] == "double click"
    assert client.get("/api/runs/999").status_code == 404


async def test_fixer_declines_agentic_findings_before_calling_a_model(tmp_path, minimal_contract):
    import pytest

    from sentri.config import Settings
    from sentri.contract import parse_contract
    from sentri.fixer.fixer import Fixer, FixerError
    from sentri.ledger import CheckRecord
    from sentri.llm import BudgetedProvider
    from sentri.verdict.engine import CheckVerdict, OracleHit

    class Never:
        name = "never"
        available = True

        async def complete_json(self, *a, **k):
            raise AssertionError("the model must not be called")

    lg = Ledger(tmp_path / "f.db")
    run = lg.create_run("c1", "main", "fp", "ch")
    key = "cross_user_exposure GET /tasks"
    check = lg.insert_check(
        CheckRecord(run, "agentic", key, "fail", oracle_hits="cross_user_exposure:3", is_probe=True)
    )
    verdict = CheckVerdict("fail", 3, (OracleHit("cross_user_exposure", 3, "sig1", "b saw a's tasks"),))
    lg.record_check_result(run, check, "agentic", key, "c1", verdict)
    bug_id = lg.bugs()[0]["id"]
    fixer = Fixer(parse_contract(minimal_contract), tmp_path, Settings(), lg, BudgetedProvider(Never(), 1, 5))
    with pytest.raises(FixerError, match="agentic finding"):
        await fixer.propose(bug_id, tmp_path)
