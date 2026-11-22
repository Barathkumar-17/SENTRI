from __future__ import annotations

import os
from importlib import resources

from sentri.config import Settings
from sentri.llm.provider import (
    BudgetedProvider,
    ContractDraft,
    LLMProvider,
    LLMResult,
    NullProvider,
    PatchProposal,
    PayloadSet,
    ProbeRequest,
    SlotValue,
)

PROVIDERS = ("anthropic", "claude_code", "openai_compatible", "none")


class LLMConfigError(ValueError):
    pass


def make_provider(settings: Settings, enabled: bool = True) -> BudgetedProvider:
    """Build the configured provider. Missing configuration means NullProvider (static fallbacks)."""
    if settings.llm_provider not in PROVIDERS:
        raise LLMConfigError(f"SENTRI_LLM_PROVIDER must be one of {PROVIDERS}, got {settings.llm_provider!r}")
    models = {
        "default": settings.model_fast,
        "attacker": settings.model_fast,
        "prober": settings.model_fast,
        "planner": settings.model_fast,
        "fixer": settings.model_smart,
    }
    inner: LLMProvider = NullProvider()
    if enabled and settings.llm_provider == "anthropic":
        # The user chose Anthropic, so its standard variable may be used.
        settings.api_key = settings.api_key or os.environ.get("ANTHROPIC_API_KEY") or None
        if not settings.api_key:
            raise LLMConfigError("anthropic needs an API key: set SENTRI_LLM_API_KEY or ANTHROPIC_API_KEY")
        from sentri.llm.anthropic_provider import AnthropicProvider

        inner = AnthropicProvider(
            settings.api_key,
            models=models,
            efforts={"attacker": "low", "prober": "low", "planner": "high", "fixer": "high"},
        )
    elif enabled and settings.llm_provider == "claude_code":
        from sentri.llm.claude_code import ClaudeCodeProvider, find_claude

        binary = find_claude()
        if binary is None:
            raise LLMConfigError(
                "Claude Code CLI not found. Install Claude Code (or its VS Code extension) and sign in, "
                "or set SENTRI_CLAUDE_BIN to the claude executable"
            )
        inner = ClaudeCodeProvider([binary], models, max(settings.llm_timeout_s, 180))
    elif enabled and settings.llm_provider == "openai_compatible":
        if not settings.llm_base_url:
            raise LLMConfigError("openai_compatible needs SENTRI_LLM_BASE_URL (e.g. http://localhost:11434/v1)")
        if settings.model_fast.startswith("claude-") and settings.model_smart.startswith("claude-"):
            raise LLMConfigError("openai_compatible needs model ids: set SENTRI_MODEL_FAST and SENTRI_MODEL_SMART")
        from sentri.llm.openai_compatible import OpenAICompatibleProvider

        inner = OpenAICompatibleProvider(settings.llm_base_url, settings.api_key, models, settings.llm_timeout_s)
    return BudgetedProvider(inner, settings.llm_budget_usd, settings.llm_timeout_s)


def describe_provider(settings: Settings, provider: BudgetedProvider) -> str:
    if provider.inner.name == "none":
        return "none (static payloads)"
    where = f" @ {settings.llm_base_url}" if settings.llm_provider == "openai_compatible" else ""
    if settings.llm_provider == "claude_code":
        where = " (your Claude Code login, no API key)"
    return f"{provider.inner.name}{where}: {settings.model_fast} (payloads, planner), {settings.model_smart} (fixer)"


def load_prompt(name: str) -> str:
    return (resources.files("sentri.llm") / "prompts" / f"{name}.md").read_text(encoding="utf-8")


__all__ = [
    "BudgetedProvider",
    "ContractDraft",
    "LLMConfigError",
    "LLMProvider",
    "LLMResult",
    "NullProvider",
    "PatchProposal",
    "PayloadSet",
    "ProbeRequest",
    "SlotValue",
    "load_prompt",
    "describe_provider",
    "make_provider",
]
