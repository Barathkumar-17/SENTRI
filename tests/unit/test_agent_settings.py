"""Contract settings apply to the agentic phase exactly as they do to the baseline suite."""

from __future__ import annotations

from sentri.agent.replay import agentic_oracle_config
from sentri.contract import parse_contract


def test_agentic_oracles_use_the_contract_console_and_log_settings(minimal_contract):
    minimal_contract["app"].update(ignore_console=["third-party widget"], log_patterns_extra=["OOPS"])
    cfg = agentic_oracle_config(parse_contract(minimal_contract))
    assert cfg.ignore_console == ("third-party widget",)
    assert cfg.extra_log_patterns == ("OOPS",)
    assert cfg.app_origin == "http://localhost:5055"


def test_no_readme_reaches_the_strategist_or_the_planner():
    """Black box: the tester chooses tests from what the running app shows, never from the target's README."""
    import inspect

    from sentri.agent import run, strategy
    from sentri.planner import planner

    for module in (run, strategy, planner):
        source = inspect.getsource(module)
        assert "README" not in source.replace("no README", "").replace("No README", ""), module.__name__
    assert "readme" not in inspect.signature(strategy.plan_strategy).parameters
    assert "readme" not in inspect.signature(planner.plan).parameters
