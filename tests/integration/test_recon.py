"""Recon against the demo app: pages, auth boundaries and the endpoints behind forms."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest
import yaml

from sentri.agent.identities import make_login
from sentri.agent.recon import Recon
from sentri.agent.session import AgentSession
from sentri.contract import parse_contract
from sentri.contract.placeholders import DataGenerator
from sentri.runner.browser import BrowserManager
from sentri.sandbox import make_sandbox
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def test_recon_maps_the_demo_app(tmp_path: Path):
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"].update(port=port, base_url=f"http://127.0.0.1:{port}", env={"FLAKY_STATE": str(tmp_path / "f")})
    contract = parse_contract(data)
    sandbox = make_sandbox(contract.app, BUGGY_APP)
    await sandbox.start()
    browser = BrowserManager()
    await browser.start()
    tmp = Path(tempfile.mkdtemp())
    session = AgentSession(browser, sandbox, contract.app.url, make_login(contract, tmp, "r"), tmp)
    try:
        await session.start()
        app_map = await Recon(session, DataGenerator(1, {}, tmp, "recon")).run()
    finally:
        await session.close()
        await browser.stop()
        await sandbox.stop()

    paths = {p.path for p in app_map.pages}
    assert {"/", "/signup", "/users", "/login", "/comments", "/tasks", "/tasks/new", "/profile"} <= paths, paths
    tasks = app_map.page("/tasks")
    assert tasks is not None and tasks.requires_login and tasks.anon_outcome == "redirect -> /login"
    comments = app_map.page("/comments")
    assert comments is not None and comments.requires_login is False
    endpoints = {(e.method, e.path) for e in app_map.endpoints}
    expected = {("POST", "/api/signup"), ("POST", "/api/comments"), ("POST", "/tasks/new"), ("POST", "/api/profile")}
    assert expected <= endpoints, endpoints
    assert app_map.login_path == "/login"
    assert app_map.summary()["identities"] == ["anon", "a", "b"]
