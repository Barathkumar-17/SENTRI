from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUGGY_APP = ROOT / "examples" / "buggy-app"


@pytest.fixture
def minimal_contract() -> dict:
    return {
        "version": 1,
        "app": {"run": "python app.py", "port": 5055},
        "flows": [
            {
                "id": "signup",
                "start": "/signup",
                "steps": [
                    {"fill": {"selector": "input[name=email]", "value": "{email}"}},
                    {"click": "button[type=submit]"},
                ],
                "success": {"url_contains": "/done"},
            }
        ],
    }
