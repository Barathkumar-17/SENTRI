"""Contract settings apply to the agentic phase exactly as they do to the baseline suite."""

from __future__ import annotations

from pathlib import Path

from sentri.agent.replay import agentic_oracle_config
from sentri.agent.run import _readme
from sentri.contract import parse_contract


def test_agentic_oracles_use_the_contract_console_and_log_settings(minimal_contract):
    minimal_contract["app"].update(ignore_console=["third-party widget"], log_patterns_extra=["OOPS"])
    cfg = agentic_oracle_config(parse_contract(minimal_contract))
    assert cfg.ignore_console == ("third-party widget",)
    assert cfg.extra_log_patterns == ("OOPS",)
    assert cfg.app_origin == "http://localhost:5055"


def test_readme_is_redacted_before_the_strategist_sees_it(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DEMO_API_TOKEN", "hunter2-very-secret")
    (tmp_path / "README.md").write_text(
        "Run with OPENAI key sk-abcdefghijklmnopqrstuvwx and token hunter2-very-secret", encoding="utf-8"
    )
    text = _readme(tmp_path)
    assert "sk-abcdefghijklmnopqrstuvwx" not in text and "hunter2-very-secret" not in text
    assert "[REDACTED]" in text
