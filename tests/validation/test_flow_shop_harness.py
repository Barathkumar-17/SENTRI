"""The validation target's hidden failures are real. This exercises examples/flow-shop directly (the harness's own
knowledge), so a later Sentri run can be judged against facts; Sentri itself never sees this file."""

from __future__ import annotations

import http.cookiejar
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.integration
APP = Path(__file__).resolve().parents[2] / "examples" / "flow-shop" / "app.py"
EXPECTED = yaml.safe_load((Path(__file__).parent / "flow_shop_expected.yaml").read_text(encoding="utf-8"))


def _port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture
def shop():
    port = _port()
    proc = subprocess.Popen([sys.executable, str(APP)], env={**os.environ, "PORT": str(port)})
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    try:
        yield base
    finally:
        proc.terminate()
        proc.wait(10)


def _client(base: str):
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def post(path: str, **form) -> int:
        try:
            return opener.open(base + path, urllib.parse.urlencode(form).encode(), timeout=5).status
        except urllib.error.HTTPError as exc:
            return exc.code

    post("/signup", email="h@x.test", password="pw")
    post("/login", email="h@x.test", password="pw")
    return post


def test_the_three_hidden_failures_exist(shop):
    assert {f["id"] for f in EXPECTED["failures"]} == {"zero_quantity_order", "resubmit_confirm", "non_ascii_coupon"}
    post = _client(shop)
    post("/cart/add", pid="1", qty="0")
    post("/checkout/address", street="Main 1")
    post("/checkout/payment", card="4242", coupon="")
    assert post("/checkout/confirm", ref="web") == 500  # zero_quantity_order

    post = _client(shop)
    post("/cart/add", pid="1", qty="2")
    post("/checkout/address", street="Main 1")
    post("/checkout/payment", card="4242", coupon="")
    assert post("/checkout/confirm", ref="web") == 200  # the normal journey works
    assert post("/checkout/confirm", ref="web") == 500  # resubmit_confirm

    post = _client(shop)
    post("/cart/add", pid="2", qty="1")
    post("/checkout/address", street="Main 1")
    assert post("/checkout/payment", card="4242", coupon="SAVE10") == 200
    assert post("/checkout/payment", card="4242", coupon="RÉDUC") == 500  # non_ascii_coupon
