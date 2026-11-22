from __future__ import annotations

import pytest
from pydantic import ValidationError

from sentri.agent.actions import (
    ACTION_ADAPTER,
    ACTION_KINDS,
    ActionRefused,
    TypeText,
    describe,
    resolve_same_origin,
)

BASE = "http://127.0.0.1:5055"


def test_every_kind_parses():
    samples = {
        "click": {"selector": "#save"},
        "type": {"selector": "input", "text": "x", "repeat": 3},
        "navigate": {"path": "/tasks/1"},
        "upload": {"selector": "input[type=file]", "shape": "zero_byte"},
        "request": {"method": "DELETE", "path": "/api/x"},
        "replay": {"request_id": 3, "times": 5, "concurrent": True},
        "burst": {"selector": "#b"},
        "new_tab": {"path": "/"},
        "switch_tab": {"index": 1},
        "back": {},
        "refresh": {},
        "clear_session": {},
        "throttle": {"on": True},
        "switch_user": {"user": "b"},
        "wait": {"ms": 100},
        "finish": {"reason": "done"},
    }
    assert set(samples) == set(ACTION_KINDS)
    for kind, body in samples.items():
        assert ACTION_ADAPTER.validate_python({"kind": kind, **body}).kind == kind


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "shell", "command": "rm -rf /"},
        {"kind": "type", "selector": "x", "repeat": 0},
        {"kind": "type", "selector": "x", "repeat": 10_000_000},
        {"kind": "replay", "request_id": 1, "times": 500},
        {"kind": "wait", "ms": 60_000},
        {"kind": "click", "selector": "#a", "extra": 1},
        {"kind": "switch_user", "user": "admin"},
    ],
)
def test_invalid_actions_are_rejected(bad):
    with pytest.raises(ValidationError):
        ACTION_ADAPTER.validate_python(bad)


def test_same_origin_fence():
    assert resolve_same_origin(BASE, "/tasks/2?x=1") == "http://127.0.0.1:5055/tasks/2?x=1"
    assert resolve_same_origin(BASE, "http://localhost:5055/a") == "http://localhost:5055/a"
    for target in (
        "https://example.com/",
        "//evil.test/x",
        "http://127.0.0.1:6000/",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/html,hi",
    ):
        with pytest.raises(ActionRefused):
            resolve_same_origin(BASE, target)


def test_describe_is_short_for_huge_input():
    text = describe(TypeText(selector="#t", text="A" * 100, repeat=1000))
    assert "x1000" in text and len(text) < 120
