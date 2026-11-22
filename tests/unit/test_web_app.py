"""The built web app is served by the dashboard server and speaks the backend's event vocabulary."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from sentri.dashboard import create_app
from sentri.dashboard.server import STATIC_DIR

ROOT = Path(__file__).resolve().parents[2]


def test_built_web_app_is_served(tmp_path):
    client = TestClient(
        create_app(tmp_path / "l.db", tmp_path / "evidence", tmp_path / "live.jpg"), base_url="http://127.0.0.1"
    )
    page = client.get("/")
    assert page.status_code == 200
    assert "<title>Sentri</title>" in page.text
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+)"', page.text)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for asset in assets:
        assert client.get(asset).status_code == 200, asset
    assert client.get("/favicon.svg").headers["content-type"].startswith("image/svg")
    assert (STATIC_DIR / "index.html").exists()


def test_web_app_listens_for_every_event_kind_the_backend_emits():
    emitted = set()
    for path in (ROOT / "src" / "sentri").rglob("*.py"):
        emitted |= set(re.findall(r'\bemit\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    client_src = (ROOT / "frontend" / "src" / "api.ts").read_text(encoding="utf-8")
    known = set(re.findall(r"'([a-z_]+)'", client_src.split("KNOWN_EVENT_KINDS", 2)[2]))
    assert emitted, "no emitted kinds found; the scan is broken"
    assert emitted <= known, f"the web app ignores: {sorted(emitted - known)}"
