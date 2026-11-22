"""Recon follows redirects, clicks navigational buttons and records multi-step forms as flows."""

from __future__ import annotations

import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sentri.agent.recon import Recon
from sentri.agent.session import AgentSession
from sentri.contract.placeholders import DataGenerator
from sentri.runner.browser import BrowserManager

pytestmark = pytest.mark.integration

PAGES = {
    "/": '<a href="/docs">Docs</a><a href="/wizard">Wizard</a><a href="/order">Order</a>'
    '<a href="/redirect?to=elsewhere">Source code</a>',
    "/docs/": "<h1>Docs</h1><p>reached only through a redirect</p>",
    "/wizard": '<h1>Step 1</h1><button id="next" onclick="location.href=\'/wizard/2\'">Next</button>'
    '<button id="del" onclick="location.href=\'/gone\'">Delete everything</button>',
    "/wizard/2": '<h1>Step 2</h1><button id="cont" onclick="location.href=\'/wizard/done\'">Continue</button>',
    "/wizard/done": "<h1>All set</h1>",
    "/order": '<form method="post" action="/order"><input name="item"><button type="submit">Order</button></form>',
    "/order/confirm": '<form method="post" action="/order/confirm"><input name="card">'
    '<button type="submit">Confirm</button></form>',
    "/order/done": "<h1>Thanks</h1>",
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a: object) -> None:
        pass

    def _send(self, status: int, body: str = "", location: str | None = None) -> None:
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(f"<!doctype html><title>t</title>{body}".encode())

    def do_GET(self) -> None:
        if self.path == "/docs":
            self._send(301, location="/docs/")  # trailing-slash variant
        elif self.path.startswith("/redirect"):
            self._send(302, location="http://127.0.0.2:9/features/copilot")  # an open redirect off the app
        elif self.path in PAGES:
            self._send(200, PAGES[self.path])
        else:
            self._send(404, "nope")

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("content-length") or 0))
        self._send(303, location={"/order": "/order/confirm", "/order/confirm": "/order/done"}.get(self.path, "/"))


class NoLogs:
    def log_cursor(self) -> int:
        return 0

    def logs_since(self, _cursor: int) -> list:
        return []


async def test_recon_depth():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    tmp = Path(tempfile.mkdtemp())
    try:
        async with BrowserManager() as browser:
            session = AgentSession(browser, NoLogs(), base, None, tmp)  # type: ignore[arg-type]
            await session.start()
            try:
                app_map = await Recon(session, DataGenerator(1, {}, tmp, "d")).run()
            finally:
                await session.close()
    finally:
        server.shutdown()

    paths = {p.path for p in app_map.pages}
    assert not any(p.startswith("/features") for p in paths)  # never maps another site's pages
    assert app_map.redirects.get("/docs") == "/docs/" and "/docs/" in paths
    assert {"/wizard/2", "/wizard/done"} <= paths  # only reachable by clicking buttons
    wizard = app_map.page("/wizard")
    assert wizard is not None and [e["to"] for e in wizard.leads_to] == ["/wizard/2"]  # "Delete" never clicked
    kinds = {f["kind"]: f for f in app_map.flows}
    assert [s["to"] for s in kinds["button_sequence"]["steps"]] == ["/wizard/2", "/wizard/done"]
    order = kinds["form_sequence"]
    assert order["start"] == "/order" and [s["to"] for s in order["steps"]] == ["/order/confirm", "/order/done"]
    assert app_map.summary()["flows"] and app_map.summary()["navigation"]
