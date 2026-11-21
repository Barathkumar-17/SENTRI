"""End to end: run Sentri against the planted-bug app and compare the grid with expected_grid.yaml.

Needs Chromium (`sentri setup`). Runs the app in command mode, so Docker is not required.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
import yaml

from sentri.config import Settings
from sentri.contract import load_contract
from sentri.ledger import Ledger
from sentri.llm import make_provider
from sentri.report import build_report
from sentri.runner.orchestrator import RunOptions, RunOrchestrator
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _contract(tmp_path: Path, env: dict[str, str] | None = None):
    """The example contract with a free port and an isolated flaky-counter file."""
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"]["port"] = port
    data["app"]["base_url"] = f"http://127.0.0.1:{port}"
    data["app"]["env"] = {"PORT": str(port), "FLAKY_STATE": str(tmp_path / "flaky.txt"), **(env or {})}
    path = BUGGY_APP / f".contract-{port}.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def _settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.db_path = tmp_path / "ledger.db"
    s.evidence_dir = tmp_path / "evidence"
    s.live_path = tmp_path / "live.jpg"
    s.api_key = None
    s.bad_file_mb = 6
    return s


async def _run(
    tmp_path: Path,
    commit: str,
    env: dict[str, str] | None = None,
    only: list[str] | None = None,
    path: Path | None = None,
):
    own = path is None
    path = path or _contract(tmp_path, env)
    try:
        contract = load_contract(path)
        settings = _settings(tmp_path)
        ledger = Ledger(settings.db_path)
        orch = RunOrchestrator(
            contract,
            BUGGY_APP,
            settings,
            ledger,
            make_provider(settings, enabled=False),
            RunOptions(commit=commit, branch="main", only=only or [], exclude_paths=[path]),
        )
        result = await orch.run()
        return result, ledger
    finally:
        if own:
            path.unlink(missing_ok=True)


def _grid(ledger: Ledger, run_id: int) -> dict[str, dict[str, str]]:
    grid: dict[str, dict[str, str]] = {}
    for row in ledger.checks_for_run(run_id, include_probes=False):
        grid.setdefault(row["flow_id"], {})[row["attack_id"]] = row["score_verdict"]
    return grid


async def test_full_grid_matches_expected(tmp_path: Path):
    expected = yaml.safe_load((BUGGY_APP / "expected_grid.yaml").read_text(encoding="utf-8"))
    result, ledger = await _run(tmp_path, "c1")
    assert result.status == "finished", result.error
    grid = _grid(ledger, result.run_id)
    mismatches = {
        f"{f}:{a}": {"expected": v, "got": grid.get(f, {}).get(a)}
        for f, attacks in expected.items()
        for a, v in attacks.items()
        if grid.get(f, {}).get(a) != v
    }
    report = build_report(ledger, result.run_id, None)
    (tmp_path / "report.md").write_text(report, encoding="utf-8")
    assert not mismatches, json.dumps(mismatches, indent=1) + "\n" + report
    assert result.breakdown is not None and result.breakdown.score is not None
    assert ledger.bugs(), "planted bugs must be recorded in the ledger"


async def test_fix_then_regression(tmp_path: Path):
    only = ["signup:empty_input"]
    r1, ledger = await _run(tmp_path, "c1", only=only)
    assert _grid(ledger, r1.run_id)["signup"]["empty_input"] == "fail"
    bug_ids = [b["id"] for b in ledger.bugs("open")]
    assert bug_ids

    r2, ledger = await _run(tmp_path, "c2", env={"FIX_SIGNUP_EMPTY": "1"}, only=only)
    assert _grid(ledger, r2.run_id)["signup"]["empty_input"] == "pass"
    assert all(ledger.bug(b)["status"] == "fixed" for b in bug_ids)

    r3, ledger = await _run(tmp_path, "c3", only=only)
    assert r3.regressions >= 1
    assert all(ledger.bug(b)["regressions"] == 1 for b in bug_ids)
    assert r3.breakdown is not None and r3.breakdown.score == 0


async def test_reuse_on_unchanged_code(tmp_path: Path):
    only = ["profile:double_submit"]
    path = _contract(tmp_path)  # same contract (same port) for both runs, so its hash is unchanged
    try:
        r1, ledger = await _run(tmp_path, "c1", only=only, path=path)
        assert _grid(ledger, r1.run_id)["profile"]["double_submit"] == "pass"
        r2, ledger = await _run(tmp_path, "c1", only=only, path=path)
    finally:
        path.unlink(missing_ok=True)
    row = ledger.checks_for_run(r2.run_id)[0]
    assert row["reused_from_check_id"] is not None and row["verdict"] == "pass"


@pytest.mark.skipif(os.name == "nt" and not os.environ.get("CI"), reason="covered by the full-grid test")
async def test_unreachable_app_aborts(tmp_path: Path):
    path = _contract(tmp_path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    path.unlink()
    data["app"]["run"] = '{python} -c "import sys; sys.exit(3)"'
    data["app"]["startup_timeout_s"] = 5
    bad = tmp_path / "contract.yaml"
    bad.write_text(yaml.safe_dump(data), encoding="utf-8")
    settings = _settings(tmp_path)
    ledger = Ledger(settings.db_path)
    result = await RunOrchestrator(
        load_contract(bad), tmp_path, settings, ledger, make_provider(settings, enabled=False), RunOptions()
    ).run()
    assert result.status == "aborted" and "failed to start" in (result.error or "")
