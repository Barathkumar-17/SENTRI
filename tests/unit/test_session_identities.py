"""Identity switching in the real AgentSession, with stub browser objects (no browser needed).

A failed login must never leave a half-logged-in context that a later switch would silently act through, and it is
not retried within the run. A successful login switches identity. SCRIPTED STUBS: this proves the session's
bookkeeping, not that a real login flow works (the integration tests and live runs cover that)."""

from __future__ import annotations

from sentri.agent.actions import SwitchUser
from sentri.agent.session import AgentSession


class _Page:
    url = "http://127.0.0.1/"

    def on(self, *a, **k):
        pass

    def set_default_timeout(self, *a, **k):
        pass

    async def wait_for_load_state(self, *a, **k):
        pass

    async def goto(self, *a, **k):
        pass


class _Ctx:
    def __init__(self):
        self.closed = False
        self.pages = []

    async def new_page(self):
        page = _Page()
        self.pages.append(page)
        return page

    async def close(self):
        self.closed = True

    async def cookies(self):
        return []

    async def route(self, *a, **k):
        pass

    async def set_extra_http_headers(self, *a, **k):
        pass


class _Browser:
    def __init__(self):
        self.contexts: list[_Ctx] = []

    async def new_context(self, **kw):
        ctx = _Ctx()
        self.contexts.append(ctx)
        return ctx


def _session(login, tmp_path):
    return AgentSession(_Browser(), object(), "http://127.0.0.1", login, tmp_path)


async def test_a_failed_login_leaves_no_context_is_reported_and_is_not_retried(tmp_path):
    calls = []

    async def failing_login(page, who):
        calls.append(who)
        raise RuntimeError(f"login flow for identity {who!r} did not succeed: text 'Shop' not visible")

    s = _session(failing_login, tmp_path)
    first = await s.execute(SwitchUser(user="a"))
    assert first.ok is False and "login as a failed" in (first.error or "")
    assert s.current == "anon" and "a" not in s.contexts  # never half logged in
    assert all(c.closed for c in s.browser.contexts)  # the half-made context was closed
    assert "a" in s.login_failed
    second = await s.execute(SwitchUser(user="a"))
    assert second.ok is False and "not retried" in (second.error or "")
    assert calls == ["a"]  # the login flow ran once


async def test_a_successful_login_switches_identity_and_keeps_its_context(tmp_path):
    async def login(page, who):
        return None

    s = _session(login, tmp_path)
    res = await s.execute(SwitchUser(user="b"))
    assert res.ok and s.current == "b" and "b" in s.contexts and not s.login_failed
    back = await s.execute(SwitchUser(user="anon"))
    assert back.ok and s.current == "anon" and "b" in s.contexts  # b's session is kept for later


async def test_without_a_login_flow_only_anon_exists(tmp_path):
    s = _session(None, tmp_path)
    res = await s.execute(SwitchUser(user="a"))
    assert res.ok is False and "no login" in (res.error or "") and s.current == "anon"
