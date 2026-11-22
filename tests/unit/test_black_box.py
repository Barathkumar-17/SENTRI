"""The black-box rule: the tester chooses tests and decides verdicts only from what the running app shows.

Target source, README contents, database internals and server logs never reach a model's context, the exploration
memory or an oracle. Lifecycle (starting/stopping the app) and the separately authorized source-aware fixer stay."""

from __future__ import annotations

import ast
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sentri.contract import ContractError, parse_contract
from sentri.sandbox import ExternalSandbox, SandboxError, make_sandbox
from sentri.sandbox.base import ResponseErrors
from tests.test_trust_boundary import transitive_imports

SRC = Path(__file__).resolve().parents[2] / "src" / "sentri"


def _attr_reads(path: Path, name: str) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == name]


def test_no_oracle_reads_server_logs():
    for path in (SRC / "verdict").glob("*.py"):
        assert not _attr_reads(path, "logs"), f"{path.name} reads Evidence.logs (server logs)"


@pytest.mark.parametrize(
    "module", ["agent/strategy.py", "agent/explorer.py", "agent/run.py", "planner/planner.py", "attacker/attacker.py"]
)
def test_model_context_never_gets_readme_logs_or_database(module):
    source = (SRC / module).read_text(encoding="utf-8")
    for forbidden in ("read_readme", "README.md", "logs_since", "log_tail", "duplicate_db", "sqlite3"):
        assert forbidden not in source, f"{module} uses {forbidden}"


def test_the_testing_path_never_reaches_the_source_aware_fixer():
    for module in ("sentri.agent.run", "sentri.runner.orchestrator", "sentri.runner.check_runner"):
        reached = transitive_imports(module)
        assert not any(name.startswith("sentri.fixer") for name in reached), module


def test_response_errors_keep_only_what_the_app_sent_back():
    buf = ResponseErrors()
    buf.record_response("http://x/a", 200, "<h1>ok</h1>")  # an ordinary page
    buf.record_response(
        "http://x/b", 500, "Internal Server Error\n\njava.lang.NullPointerException\n  at X.y(X.java:3)"
    )
    buf.record_response("http://x/c", 200, "Traceback (most recent call last):\n  File ...\nKeyError: 'id'")
    lines = [line.text for line in buf.responses_since(0)]
    assert "<h1>ok</h1>" not in lines and "java.lang.NullPointerException" in lines and "KeyError: 'id'" in lines
    assert all(line.stream == "response" for line in buf.responses_since(0))


def _contract(app: dict) -> dict:
    return {"version": 1, "app": app, "flows": []}


def test_url_mode_contract_and_sandbox():
    c = parse_contract(_contract({"base_url": "http://127.0.0.1:9/"}))
    assert c.app.url_mode and c.app.url == "http://127.0.0.1:9"
    sandbox = make_sandbox(c.app, Path("."))
    assert isinstance(sandbox, ExternalSandbox) and sandbox.reset_kind == "none"
    launcher = parse_contract(_contract({"run": "python app.py", "port": 5055}))
    assert not launcher.app.url_mode and make_sandbox(launcher.app, Path(".")).reset_kind == "restart"
    for bad in ({"port": 5055}, {"base_url": "http://x", "clean": ["db.sqlite"]}, {"run": "x"}):
        with pytest.raises(ContractError):
            parse_contract(_contract(bad))


def test_external_sandbox_waits_for_the_url_and_never_resets():
    hits: list[str] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    server = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = parse_contract(
            _contract({"base_url": f"http://127.0.0.1:{server.server_address[1]}", "health_path": "/up"})
        ).app
        sandbox = ExternalSandbox(app)
        asyncio.run(sandbox.start())
        asyncio.run(sandbox.reset())
        asyncio.run(sandbox.stop())
        assert sandbox.is_alive() and set(hits) == {"/up"}  # only health checks, nothing else
    finally:
        server.shutdown()
    dead = ExternalSandbox(parse_contract(_contract({"base_url": "http://127.0.0.1:9", "startup_timeout_s": 1})).app)
    with pytest.raises(SandboxError, match="does not start the app"):
        asyncio.run(dead.start())
