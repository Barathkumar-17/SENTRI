"""Replay confirmation against the demo app: a real cross-user leak is confirmed 3/3; a harmless path passes."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest
import yaml

from sentri.agent.recon import AppMap, PageEntry
from sentri.agent.replay import ReplayEnv, confirm, record
from sentri.contract import parse_contract
from sentri.ledger import Ledger
from sentri.runner.browser import BrowserManager
from sentri.sandbox import make_sandbox
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration

APP_MAP = AppMap(
    base_url="",
    pages=[
        PageEntry(path="/tasks", requires_login=True, list_selectors=['[data-testid="task-row"]']),
        PageEntry(path="/tasks/new", requires_login=True),
        PageEntry(path="/comments", list_selectors=['[data-testid="comment-row"]']),
    ],
    has_login_flow=True,
)

LEAK = [
    {"action": {"kind": "switch_user", "user": "a"}},
    {"action": {"kind": "navigate", "path": "/tasks/new"}},
    {"action": {"kind": "type", "selector": "input[name=title]", "text": "secret sentri-canary-a-000000"}},
    {"action": {"kind": "upload", "selector": "input[type=file]", "shape": "valid"}},
    {"action": {"kind": "click", "selector": "button#save"}},
    {"action": {"kind": "switch_user", "user": "b"}},
    {"action": {"kind": "navigate", "path": "/tasks"}},
]
HARMLESS = [
    {"action": {"kind": "switch_user", "user": "b"}},
    {"action": {"kind": "navigate", "path": "/comments"}},
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def env(tmp_path: Path):
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"].update(port=port, base_url=f"http://127.0.0.1:{port}", env={"FLAKY_STATE": str(tmp_path / "f")})
    contract = parse_contract(data)
    sandbox = make_sandbox(contract.app, BUGGY_APP)
    await sandbox.start()
    browser = BrowserManager()
    await browser.start()
    try:
        yield ReplayEnv(
            contract,
            sandbox,
            browser,
            APP_MAP.model_copy(update={"base_url": contract.app.url}),
            "t",
            evidence_dir=tmp_path / "evidence",
        )
    finally:
        await browser.stop()
        await sandbox.stop()


async def test_cross_user_leak_is_confirmed_and_recorded(env: ReplayEnv, tmp_path: Path):
    verdict, key, notes = await confirm(env, LEAK)
    assert verdict.verdict == "fail", (verdict, notes)
    assert key == "cross_user_exposure GET /tasks"
    assert any(h.oracle_id == "cross_user_exposure" and h.k == 3 for h in verdict.hits)

    ledger = Ledger(tmp_path / "l.db")
    run_id = ledger.create_run("c1", "main", "fp", "ch")
    check_id, events = record(ledger, run_id, "c1", {"title": "B sees A's tasks", "repro": LEAK}, verdict, key)
    assert check_id and any(e.kind == "bug_opened" for e in events)
    assert ledger.findings_for_run(run_id)[0]["verdict"] == "fail"
    assert ledger.confirmed_repros()[0]["attack_id"] == key  # re-checked on every later run
    assert list((tmp_path / "evidence").glob("agentic-*.json"))


async def test_harmless_path_passes(env: ReplayEnv):
    verdict, _key, _notes = await confirm(env, HARMLESS)
    assert verdict.verdict == "pass", verdict


async def test_a_step_that_cannot_run_fails_the_attempt_instead_of_being_skipped(env: ReplayEnv, tmp_path: Path):
    broken = [*LEAK[:4], {"action": {"kind": "click", "selector": "button#gone"}}, *LEAK[4:]]
    env.attempts = 1
    verdict, _key, notes = await confirm(env, broken)
    assert verdict.verdict == "error", verdict  # inconclusive: never pass, never a confirmed leak
    assert any("failed at step 5/" in n for n in notes), notes

    ledger = Ledger(tmp_path / "l.db")
    run_id = ledger.create_run("c1", "main", "fp", "ch")
    check_id, events = record(
        ledger,
        run_id,
        "c1",
        {"title": "t", "repro": broken, "recheck": True},
        verdict,
        "cross_user_exposure GET /tasks",
    )
    assert check_id is None and events == []  # a re-check that could not replay leaves the bug alone
    assert ledger.findings_for_run(run_id)[0]["verdict"] == "unconfirmed"


async def test_code_owned_idor_check_is_confirmed_by_replay(env: ReplayEnv):
    """The model only created a note; Sentri added the canary, found the id and fetched it as user b."""
    notes = [
        {"action": {"kind": "switch_user", "user": "a"}},
        {"action": {"kind": "navigate", "path": "/notes"}},
        {"action": {"kind": "type", "selector": "input[name=title]", "text": "plan sentri-canary-a-000000"}},
        {"action": {"kind": "click", "selector": "#note-btn"}},
    ]
    verdict, key, notes_out = await confirm(env, notes)
    assert verdict.verdict == "fail", (verdict, notes_out)
    assert key == "cross_user_exposure GET /api/notes/:id"
