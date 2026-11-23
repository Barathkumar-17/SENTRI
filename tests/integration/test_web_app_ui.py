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

        page.goto(f"{base}/")  # Home: the latest run's outcome, with the way to its report
        expect(page.get_by_role("link", name="Home")).to_have_attribute("aria-current", "page")
        expect(page.get_by_role("heading", name=f"Latest run · Run {run}")).to_be_visible()
        page.get_by_role("link", name="Open report").click()
        expect(page).to_have_url(f"{base}/#/runs/{run}/report")
        expect(page.get_by_role("heading", name=f"Report · Run {run}")).to_be_visible()
        expect(page.get_by_role("heading", name="What broke")).to_be_visible()
        expect(page.get_by_text("Suggested next steps")).to_be_visible()

        page.goto(f"{base}/#/runs")
        expect(page.get_by_role("link", name=f"Run {run}", exact=True)).to_be_visible()
        # readiness carries its scale and what it was built from, visibly (not only on hover)
        expect(page.get_by_text("0 of 1 applicable check passed")).to_be_visible()
        expect(page.get_by_text("1 of 1 applicable check passed")).to_be_visible()
        expect(page.get_by_text("is not a safety rating")).to_be_visible()

        page.goto(f"{base}/#/runs/{run}")
        expect(page.get_by_role("heading", name=f"Run {run}")).to_be_visible()
        expect(page.get_by_text("signup:double_submit: FAIL")).to_be_visible()

        # the old Ledger link lands on the bugs view; filters live in the URL and survive reload and back
        page.goto(f"{base}/#/ledger")
        expect(page).to_have_url(f"{base}/#/findings?view=bugs")
        page.get_by_role("link", name="Open", exact=True).click()
        expect(page).to_have_url(f"{base}/#/findings?view=bugs&status=open")
        page.reload()
        expect(page.get_by_role("link", name="Open", exact=True)).to_have_attribute("aria-current", "true")
        page.get_by_role("link", name="BUG-001").focus()
        page.keyboard.press("Enter")  # keyboard reaches the bug's own page
        expect(page).to_have_url(f"{base}/#/bugs/BUG-001")
        expect(page.get_by_role("heading", name="Across runs")).to_be_visible()
        page.go_back()
        expect(page).to_have_url(f"{base}/#/findings?view=bugs&status=open")
        page.goto(f"{base}/#/bugs/BUG-001")  # a direct link works too
        mark = page.get_by_role("button", name="Mark intended")
        expect(mark).to_be_disabled()  # a reason is required
        page.get_by_label("Why is this intended?").fill("double submit is idempotent by design")
        page.get_by_label("Your name").fill("reviewer")
        mark.click()
        expect(page.get_by_text("by a person")).to_be_visible()  # the decision is in the bug's history

        page.goto(f"{base}/#/findings/999")  # a finding that does not exist says so
        expect(page.get_by_text("This finding does not exist")).to_be_visible()
        browser.close()
    assert not errors, errors

    lg = Ledger(db)
    try:
        bug = lg.bug("BUG-001")
    finally:
        lg.close()
    assert bug is not None
    assert bug["status"] == "intended" and bug["human_note"] == "double submit is idempotent by design (by reviewer)"
