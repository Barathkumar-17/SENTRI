"""`auth.start_as`: the contract field and the verification helper (fake session, no browser needed)."""

from __future__ import annotations

import pytest

from sentri.agent.identities import start_authenticated
from sentri.contract import ContractError, parse_contract


def _contract(**auth):
    return {
        "version": 1,
        "app": {"run": "x", "port": 5000},
        "auth": {"login_flow": "login", **auth},
        "flows": [
            {
                "id": "login",
                "attack": False,
                "start": "/",
                "steps": [{"click": "#go"}],
                "success": {"text_visible": "ok"},
            }
        ],
    }


def test_start_as_is_optional_and_only_a_or_b():
    assert parse_contract(_contract()).auth.start_as is None  # logged out unless declared
    assert parse_contract(_contract(start_as="a")).auth.start_as == "a"
    with pytest.raises(ContractError):
        parse_contract(_contract(start_as="admin"))


class _Locator:
    def __init__(self, count):
        self._count = count

    async def count(self):
        return self._count


class _Page:
    """Serves each protected path as the session's login state says: logged in = the page, else the login page."""

    def __init__(self, session):
        self.session, self.url = session, "http://app/"

    async def goto(self, url, **kw):
        path = url.removeprefix("http://app")
        if path in self.session.protected and not self.session.logged_in.get(self.session.current, False):
            self.url = "http://app/login"  # anon, or a login that left no usable session
        else:
            self.url = url

    def locator(self, selector):
        return _Locator(1 if self.url.endswith("/login") else 0)


class _Session:
    base_url, timeout_ms = "http://app", 1000

    def __init__(self, login_works=True, session_valid=True, protected=("/cart", "/orders")):
        self.current, self.protected = "anon", set(protected)
        self.login_works, self.logged_in = login_works, {}
        self.session_valid, self.page_ = session_valid, None
        self.switches: list[str] = []

    @property
    def page(self):
        self.page_ = self.page_ or _Page(self)
        return self.page_

    async def execute_switch(self, who):
        self.switches.append(who)
        if who != "anon":
            if not self.login_works:
                raise RuntimeError("login flow for identity 'a' did not succeed: text not visible")
            self.logged_in[who] = self.session_valid  # a login that "worked" but left no usable session
        self.current = who


async def test_a_login_that_the_server_honours_is_verified_against_a_protected_page():
    s = _Session()
    ok, detail = await start_authenticated(s, "a", ["/cart", "/orders"])
    assert ok and s.current == "a" and "/cart" in detail and "served to this session" in detail


async def test_a_login_that_throws_leaves_the_session_logged_out():
    s = _Session(login_works=False)
    ok, detail = await start_authenticated(s, "a", ["/cart"])
    assert not ok and s.current == "anon" and "login as a failed" in detail and "starts logged out" in detail


async def test_a_session_the_server_does_not_recognise_is_not_authenticated_even_if_login_ran():
    s = _Session(session_valid=False)  # the flow "succeeded" but the protected page still bounces to /login
    ok, detail = await start_authenticated(s, "a", ["/cart", "/orders"])
    assert not ok and s.current == "anon" and "ended on /login" in detail


async def test_without_a_proven_login_only_page_nothing_can_be_verified():
    s = _Session()
    ok, detail = await start_authenticated(s, "a", [])
    assert not ok and s.current == "anon" and s.switches == ["anon"]  # never logs in unverifiably
