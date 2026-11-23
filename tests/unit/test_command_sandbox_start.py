"""A start that is cancelled (a run stopped from the web app during startup) must not leave the app process running."""

from __future__ import annotations

import asyncio
import socket
import sys

import pytest

from sentri.contract.models import AppConfig
from sentri.sandbox.base import SandboxError
from sentri.sandbox.command_sandbox import CommandSandbox


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _sandbox(tmp_path, timeout_s):
    port = _port()
    app = AppConfig.model_validate(
        {
            "run": f'"{sys.executable}" -c "import time; time.sleep(120)"',  # a process that never opens its port
            "port": port,
            "base_url": f"http://127.0.0.1:{port}",
            "startup_timeout_s": timeout_s,
        }
    )
    return CommandSandbox(app, tmp_path)


async def test_a_cancelled_start_stops_the_app_process(tmp_path):
    sb = _sandbox(tmp_path, 60)
    task = asyncio.create_task(sb.start())
    for _ in range(100):
        if sb.is_alive():
            break
        await asyncio.sleep(0.1)
    assert sb.is_alive()  # spawned and waiting for its port
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not sb.is_alive()


async def test_an_app_that_never_opens_its_port_is_stopped_with_its_log_tail_in_the_error(tmp_path):
    sb = _sandbox(tmp_path, 1.0)
    with pytest.raises(SandboxError, match="did not open in time"):
        await sb.start()
    assert not sb.is_alive()
