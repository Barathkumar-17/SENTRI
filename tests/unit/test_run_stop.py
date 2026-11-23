"""A run stopped from outside (a cancelled web-app job, Ctrl+C) is recorded as aborted, never left "running"."""

from __future__ import annotations

import asyncio
import contextlib
import time

from sentri.feed.bus import EventBus
from sentri.ledger import Ledger
from sentri.runner.orchestrator import RunOrchestrator


class _Provider:
    spent_usd = 0.0
    cost_known = True

    def usage(self) -> dict[str, object]:
        return {"calls": 0}


async def test_a_cancelled_run_is_recorded_as_aborted(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    orch = object.__new__(RunOrchestrator)  # only what run() and _abort() touch
    orch.ledger, orch.provider, orch.echo = ledger, _Provider(), None
    orch.llm_used = orch.static_used = False
    orch.coverage = None
    started = asyncio.Event()

    async def long_run():  # stands in for the real run: it exists, emits, then works for a long time
        run_id = ledger.create_run("abc", "main", "", "ch")
        orch._live = (run_id, EventBus(ledger, run_id), time.monotonic())
        started.set()
        await asyncio.sleep(3600)

    orch._run = long_run  # type: ignore[method-assign]
    task = asyncio.create_task(orch.run())
    await started.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    run = ledger.run(1)
    assert run is not None and run["status"] == "aborted" and run["finished_at"]
    events = [e["kind"] for e in ledger.events_after(0, 1)]
    assert events[-1] == "run_aborted"
    ledger.close()


async def test_a_finished_run_is_not_rewritten_by_a_late_cancel(tmp_path):
    ledger = Ledger(tmp_path / "l.db")
    orch = object.__new__(RunOrchestrator)
    orch.ledger, orch.provider, orch.echo = ledger, _Provider(), None
    orch.llm_used = orch.static_used = False
    orch.coverage = None

    async def finishes_then_hangs():
        run_id = ledger.create_run("abc", "main", "", "ch")
        orch._live = (run_id, EventBus(ledger, run_id), time.monotonic())
        orch._finish(run_id, "finished", None, time.monotonic())
        await asyncio.sleep(3600)  # e.g. still shutting the sandbox down

    orch._run = finishes_then_hangs  # type: ignore[method-assign]
    task = asyncio.create_task(orch.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    run = ledger.run(1)
    assert run is not None and run["status"] == "finished"
    ledger.close()
