"""The built web app drives the real server end to end: it renders a recorded run and a person's bug decision
made in the browser lands in the ledger."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

from sentri.ledger import Ledger
from tests.unit.test_gate_dashboard_report import seeded_ledger

pytestmark = pytest.mark.integration
SENTRI = Path(shutil.which("sentri", path=str(Path(sys.executable).parent)) or "sentri")


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def server(tmp_path):
    lg, run = seeded_ledger(tmp_path)
    lg.close()
    port = _port()
    proc = subprocess.Popen(
        [str(SENTRI), "serve", "--port", str(port), "--db", str(tmp_path / "l.db")],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base}/api/runs", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield base, run, tmp_path / "l.db"
    proc.kill()
    proc.wait()


def test_web_app_renders_a_run_and_records_a_bug_decision(server):
    base, run, db = server
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))

        page.goto(f"{base}/")  # Home is the landing page: how to use Sentri, plus the latest real run
        expect(page.get_by_role("link", name="Home")).to_have_attribute("aria-current", "page")
        expect(page.get_by_role("heading", name="How to use Sentri")).to_be_visible()
        expect(page.get_by_role("main").get_by_role("link", name=f"Run {run}", exact=True)).to_be_visible()

        page.goto(f"{base}/#/runs")
        expect(page.get_by_role("link", name=f"Run {run}", exact=True)).to_be_visible()
        # readiness carries its scale and what it was built from, visibly (not only on hover)
        expect(page.get_by_text("0 of 1 applicable check passed")).to_be_visible()
        expect(page.get_by_text("1 of 1 applicable check passed")).to_be_visible()
        expect(page.get_by_text("is not a safety rating")).to_be_visible()

        page.goto(f"{base}/#/runs/{run}")
        expect(page.get_by_role("heading", name=f"Run {run}")).to_be_visible()
        expect(page.get_by_text("signup:double_submit: FAIL")).to_be_visible()

        page.goto(f"{base}/#/ledger")
        page.get_by_role("tab", name="Bugs").click()
        page.get_by_role("button", name="BUG-001").focus()
        page.keyboard.press("Enter")  # keyboard reaches the decision form
        mark = page.get_by_role("button", name="Mark intended")
        expect(mark).to_be_disabled()  # a reason is required
        page.get_by_label("Why is this intended?").fill("double submit is idempotent by design")
        page.get_by_label("Your name").fill("reviewer")
        mark.click()
        expect(page.get_by_text("Intended").first).to_be_visible()
        browser.close()
    assert not errors, errors

    lg = Ledger(db)
    try:
        bug = lg.bug("BUG-001")
    finally:
        lg.close()
    assert bug is not None
    assert bug["status"] == "intended" and bug["human_note"] == "double submit is idempotent by design (by reviewer)"
