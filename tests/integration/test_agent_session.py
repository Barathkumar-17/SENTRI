"""The agent session against the real demo app: actions, recording, identities and the origin fence."""

from __future__ import annotations

import socket
import tempfile
from pathlib import Path

import pytest
import yaml

from sentri.agent.actions import ACTION_ADAPTER
from sentri.agent.identities import make_login
from sentri.agent.session import AgentSession
from sentri.contract import parse_contract
from sentri.runner.browser import BrowserManager
from sentri.sandbox import make_sandbox
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
async def session(tmp_path: Path):
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"].update(port=port, base_url=f"http://127.0.0.1:{port}", env={"FLAKY_STATE": str(tmp_path / "f")})
    contract = parse_contract(data)
    sandbox = make_sandbox(contract.app, BUGGY_APP)
    await sandbox.start()
    browser = BrowserManager()
    await browser.start()
    tmp = Path(tempfile.mkdtemp())
    s = AgentSession(browser, sandbox, contract.app.url, make_login(contract, tmp, "t"), tmp)
    await s.start()
    try:
        yield s
    finally:
        await s.close()
        await browser.stop()
        await sandbox.stop()


def act(**kw):
    return ACTION_ADAPTER.validate_python(kw)


async def test_actions_requests_and_identities(session: AgentSession):
    r = await session.execute(act(kind="navigate", path="/signup"))
    assert r.ok and r.url.endswith("/signup")
    await session.execute(act(kind="type", selector="input[name=email]", text=""))
    r = await session.execute(act(kind="click", selector="#signup-btn"))
    signup = [q for q in session.requests if q.url.endswith("/api/signup")]
    assert signup and signup[-1].status == 500  # the planted blank-email bug, seen as anon

    refused = await session.execute(act(kind="navigate", path="https://example.com/"))
    assert not refused.ok and "outside the app" in (refused.error or "")

    replay = await session.execute(act(kind="replay", request_id=signup[-1].id, times=3, concurrent=True))
    assert replay.ok and sum(1 for q in session.requests if q.via == "replay") == 3

    r = await session.execute(act(kind="switch_user", user="a"))
    assert r.ok and session.current == "a" and "/tasks" in (r.url or "")
    tasks_page = await session.execute(act(kind="navigate", path="/tasks"))
    assert tasks_page.ok
    obs = await session.observe()
    assert obs["identity"] == "a" and obs["canary_for_this_identity"].startswith("sentri-canary-a-")
    html = [q for q in session.requests if q.identity == "a" and q.url.endswith("/tasks") and q.response_text]
    assert html, "same-origin HTML responses are recorded for the oracles"

    await session.execute(act(kind="switch_user", user="b"))
    assert set(session.contexts) == {"anon", "a", "b"}

    ev = session.evidence_since(session.mark())
    assert ev.http == ()  # nothing new since the mark
