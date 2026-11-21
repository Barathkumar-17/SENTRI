"""Calls the real model API. Run with: pytest -m live_llm (needs LLM_API_KEY or ANTHROPIC_API_KEY)."""

from __future__ import annotations

import os

import pytest

from sentri.attacker import Attacker
from sentri.attacks import ATTACKS
from sentri.config import Settings
from sentri.contract import load_contract
from sentri.llm import make_provider
from tests.conftest import BUGGY_APP

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(not (os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")), reason="no API key"),
]


async def test_sonnet_proposes_valid_payload():
    settings = Settings()
    settings.model_fast = os.environ.get("SENTRI_MODEL_FAST", "claude-sonnet-5-5")
    provider = make_provider(settings)
    flow = load_contract(BUGGY_APP / "contract.yaml").flow("create_task")
    choice = await Attacker(provider).choose(ATTACKS["bad_file"], flow, [], 0)
    assert choice.llm_error is None, choice.llm_error
    assert choice.payload["shape"] in ATTACKS["bad_file"].SHAPES  # type: ignore[attr-defined]
    assert provider.spent_usd > 0
