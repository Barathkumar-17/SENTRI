"""Code-owned canaries and the direct-by-id check against the demo app's notes API (planted IDOR)."""

from __future__ import annotations

import contextlib
import socket
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import yaml

from sentri.agent.actions import ACTION_ADAPTER
from sentri.agent.identities import make_login
from sentri.agent.session import AgentSession
from sentri.contract import parse_contract
from sentri.runner.browser import BrowserManager
from sentri.sandbox import make_sandbox
from sentri.verdict.agent_oracles import cross_user_exposure
from sentri.verdict.oracles import OracleConfig
from tests.conftest import BUGGY_APP

pytestmark = pytest.mark.integration
CFG = OracleConfig(flow_id="agentic", attack_id="explore")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextlib.asynccontextmanager
async def demo_session(tmp_path: Path, **env: str) -> AsyncIterator[AgentSession]:
    data = yaml.safe_load((BUGGY_APP / "contract.yaml").read_text(encoding="utf-8"))
    port = _free_port()
    data["app"].update(port=port, base_url=f"http://127.0.0.1:{port}", env={"FLAKY_STATE": str(tmp_path / "f"), **env})
    contract = parse_contract(data)
    sandbox = make_sandbox(contract.app, BUGGY_APP)
    await sandbox.start()
    browser = BrowserManager()
    await browser.start()
    tmp = Path(tempfile.mkdtemp())
    session = AgentSession(browser, sandbox, contract.app.url, make_login(contract, tmp, "i"), tmp)
    await session.start()
    try:
        yield session
    finally:
        await session.close()
        await browser.stop()
        await sandbox.stop()


def act(**kw):
    return ACTION_ADAPTER.validate_python(kw)


async def _create_note_as_a(session: AgentSession):
    await session.execute(act(kind="switch_user", user="a"))
    await session.execute(act(kind="navigate", path="/notes"))
    mark = session.mark()
    typed = await session.execute(act(kind="type", selector="input[name=title]", text="my private note"))
    await session.execute(act(kind="click", selector="#note-btn"))
    return typed, session.agent_evidence_since(mark)


async def test_sentri_writes_the_canary_and_checks_the_new_record_by_id(tmp_path: Path):
    async with demo_session(tmp_path) as session:
        typed, ev = await _create_note_as_a(session)
        canary = session.canaries["a"]
        assert typed.action is not None and typed.action.text == f"my private note {canary}"  # code, not model
        assert [(c.endpoint, c.record_id) for c in session.created] == [("/api/notes", "1")]
        (probe,) = ev.idor_probes
        assert (probe.path, probe.owner_status, probe.anon_status, probe.other_status) == (
            "/api/notes/1",
            200,
            401,
            200,
        )
        hit = cross_user_exposure(ev, CFG)
        assert hit.fired and hit.route == "GET /api/notes/:id"

        # Typed inputs are never touched: input abuse stays exact.
        await session.execute(act(kind="navigate", path="/profile"))
        empty = await session.execute(act(kind="type", selector="input[name=display_name]", text=""))
        assert empty.action is not None and empty.action.text == ""


async def test_an_owner_check_makes_the_probe_pass(tmp_path: Path):
    async with demo_session(tmp_path, FIX_NOTES_IDOR="1") as session:
        _typed, ev = await _create_note_as_a(session)
        (probe,) = ev.idor_probes
        assert probe.other_status == 404 and not cross_user_exposure(ev, CFG).fired


async def test_reflected_input_is_flagged_and_escaping_clears_it(tmp_path: Path):
    from sentri.verdict.agent_oracles import xss_reflection

    for fix, expected in (("0", True), ("1", False)):
        async with demo_session(tmp_path, FIX_SEARCH_XSS=fix) as session:
            mark = session.mark()
            marker = session.xss_marker
            await session.execute(act(kind="navigate", path="/search?q=" + marker.replace('"', "%22")))
            hit = xss_reflection(session.agent_evidence_since(mark), CFG)
            assert hit.fired is expected, (fix, hit)
            if expected:
                assert hit.route == "GET /search"


async def test_a_form_accepted_without_its_csrf_token_is_flagged(tmp_path: Path):
    from sentri.verdict.agent_oracles import csrf_missing

    for fix, expected in (("0", True), ("1", False)):
        async with demo_session(tmp_path, FIX_SETTINGS_CSRF=fix) as session:
            await session.execute(act(kind="switch_user", user="a"))
            await session.execute(act(kind="navigate", path="/settings"))
            mark = session.mark()
            await session.execute(act(kind="type", selector="input[name=theme]", text="dark"))
            await session.execute(act(kind="click", selector="#settings-btn"))
            ev = session.agent_evidence_since(mark)
            (probe,) = ev.csrf_probes
            assert probe.token == "csrf_token" and probe.path == "/settings"
            assert csrf_missing(ev, CFG).fired is expected, (fix, probe)
