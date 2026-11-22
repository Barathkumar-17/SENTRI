"""Runtime settings read from environment variables (see docs/reference/cli.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.environ.get("SENTRI_DB", ".sentri/ledger.db")))
    evidence_dir: Path = field(default_factory=lambda: Path(os.environ.get("SENTRI_EVIDENCE_DIR", "evidence")))
    live_path: Path = field(default_factory=lambda: Path(".sentri/live.jpg"))
    # Only Sentri's own variables are read here. Vendor variables such as ANTHROPIC_API_KEY are used only
    # after the user explicitly chooses that provider (see llm.make_provider), so a key that happens to be
    # in the environment is never spent silently.
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("SENTRI_LLM_API_KEY") or os.environ.get("LLM_API_KEY") or None
    )
    llm_provider: str = field(default_factory=lambda: os.environ.get("SENTRI_LLM_PROVIDER", "none"))
    llm_base_url: str | None = field(default_factory=lambda: os.environ.get("SENTRI_LLM_BASE_URL") or None)
    model_fast: str = field(default_factory=lambda: os.environ.get("SENTRI_MODEL_FAST", "claude-sonnet-5-5"))
    model_smart: str = field(default_factory=lambda: os.environ.get("SENTRI_MODEL_SMART", "claude-sonnet-5-5"))
    llm_budget_usd: float = field(default_factory=lambda: _env_float("SENTRI_LLM_BUDGET_USD", 1.0))
    llm_timeout_s: float = field(default_factory=lambda: _env_float("SENTRI_LLM_TIMEOUT_S", 60.0))
    llm_max_calls: int = field(default_factory=lambda: _env_int("SENTRI_LLM_MAX_CALLS", 0))  # 0 = no limit
    llm_max_tokens: int = field(default_factory=lambda: _env_int("SENTRI_LLM_MAX_TOKENS", 0))  # 0 = no limit
    max_probes_per_flow: int = field(default_factory=lambda: _env_int("SENTRI_MAX_PROBES_PER_FLOW", 2))
    regression_penalty: int = field(default_factory=lambda: _env_int("SENTRI_REGRESSION_PENALTY", 5))
    headless: bool = field(default_factory=lambda: os.environ.get("SENTRI_HEADLESS", "1") != "0")
    bad_file_mb: int = field(default_factory=lambda: _env_int("SENTRI_BAD_FILE_MB", 50))
    evidence_url: str | None = field(default_factory=lambda: os.environ.get("SENTRI_EVIDENCE_URL"))
    ollama_model: str | None = field(default_factory=lambda: os.environ.get("SENTRI_OLLAMA_MODEL") or None)
    explore_steps: int = field(default_factory=lambda: _env_int("SENTRI_EXPLORE_STEPS", 60))
    explore_minutes: float = field(default_factory=lambda: _env_float("SENTRI_EXPLORE_MINUTES", 10.0))
    attempts: int = 3
    live_view: bool = False

    def __post_init__(self) -> None:
        if self.ollama_model:
            self.use_ollama(self.ollama_model)

    def use_ollama(self, model: str) -> None:
        """Shortcut for a local Ollama model: no API key, no data leaves the machine."""
        self.ollama_model = model
        self.llm_provider = "openai_compatible"
        self.llm_base_url = self.llm_base_url or "http://localhost:11434/v1"
        self.model_fast = self.model_smart = model
        self.llm_timeout_s = max(self.llm_timeout_s, 300)  # CPU inference can be slow
