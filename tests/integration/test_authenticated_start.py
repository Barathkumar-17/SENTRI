"""Exploration that starts as a verified, logged-in test account (`auth.start_as`), against the demo app.

SCRIPTED MODEL: the provider only plans one hypothesis and then finishes; the test is about the setup precondition
(order relative to resets, verification against real app state, the recorded identity), not about exploration."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest
import yaml

from sentri.agent.explorer import ActionSpec, Decision, ExploreBudget
from sentri.agent.run import run_agentic
from sentri.agent.strategy import Hypothesis, Strategy
from sentri.contract import parse_contract
from sentri.feed.bus import EventBus
from sentri.ledger import Ledger
from sentri.llm import BudgetedProvider, LLMResult
from sentri.runner.browser import BrowserManager
from sentri.sandbox import make_sandbox
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Planner:
    """Plans one hypothesis aimed at a page, then ends exploration at its first step."""

    name = "planner"
    available = True

    async def complete_json(self, role, system, user, schema, max_tokens=4000):
        if schema is Strategy:
            return LLMResult(
                True,
                Strategy(app_kind="tasks", hypotheses=[Hypothesis(id="h1", title="look around", targets=["/tasks"])]),
            )
        return LLMResult(True, Decision(hypothesis_id="h1", action=ActionSpec(kind="finish", reason="enough")))


async def _run(tmp_path: Path, start_as: str | None):
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"].update(port=port, base_url=f"http://127.0.0.1:{port}", env={"FLAKY_STATE": str(tmp_path / "f")})
    if start_as:
        data["auth"]["start_as"] = start_as
    contract = parse_contract(data)
    sandbox = make_sandbox(contract.app, BUGGY_APP)
    resets: list[int] = []
    real_reset = sandbox.reset

    async def spy_reset():
        resets.append(len(resets) + 1)
        await real_reset()

    sandbox.reset = spy_reset  # type: ignore[method-assign]
    await sandbox.start()
    browser = BrowserManager()
    await browser.start()
    ledger = Ledger(tmp_path / "l.db")
    run_id = ledger.create_run("abc1234", "main", "", "ch")
    bus = EventBus(ledger, run_id)
    marks: dict[str, int] = {}
    bus.subscribe(lambda _id, kind, data: marks.setdefault(kind, len(resets)))
    try:
        out = await run_agentic(
            contract=contract,
            sandbox=sandbox,
            browser=browser,
            provider=BudgetedProvider(Planner(), 1.0, 60.0),
            ledger=ledger,
            bus=bus,
            run_id=run_id,
            commit="abc1234",
            budget=ExploreBudget(max_steps=3),
            evidence_dir=tmp_path / "ev",
        )
    finally:
        await browser.stop()
        await sandbox.stop()
    events = [(e["kind"], e["message"], json.loads(e["data"] or "{}")) for e in ledger.events_after(0, run_id)]
    steps = ledger.steps_for_run(run_id)
    ledger.close()
    return out, events, steps, marks, resets


async def test_exploration_starts_as_a_verified_account_after_the_last_reset(tmp_path: Path):
    out, events, steps, marks, resets = await _run(tmp_path, "a")
    setup = [e for e in events if e[0] == "setup"]
    assert len(setup) == 1 and "login-only page" in setup[0][1] and setup[0][2].get("verified") is True
    assert out.start_as == "a"
    assert steps and steps[0]["identity"] == "a"  # the first step is taken as the account the setup logged in
    # order: the reset that clears the app comes BEFORE the account is created, and nothing resets in between
    # recon's reset and the exploration reset come before the login; none between it and the first step (the later
    # resets are replays of a candidate Sentri's own reflection probe raised)
    assert marks["setup"] == marks["explore_step"] == 2 and len(resets) >= 2
    assert "credential" not in str(events).lower() and "password" not in str(setup).lower()
