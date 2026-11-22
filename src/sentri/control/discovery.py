"""Find every model source available on the machine Sentri runs on (any machine, at runtime).

Reports agent CLIs (Claude Code, Codex, Gemini CLI), local model servers (Ollama, LM Studio) and which API-key
variables are set. Key VALUES are never read into the result, only whether a variable exists. Nothing found
is a valid answer: Sentri then runs basic tests with built-in payloads.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from sentri.control.models import OLLAMA_URL, discover_local
from sentri.llm.agent_clis import find_codex, find_gemini
from sentri.llm.claude_code import find_claude

KEY_VARS = {
    "anthropic": ("SENTRI_LLM_API_KEY", "ANTHROPIC_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "groq": ("GROQ_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "gemini": ("GEMINI_API_KEY",),
}

# Order for --llm-provider auto: tools the user is already signed in to, then local servers. API keys are
# never chosen automatically, because using one spends the user's money.
AUTO_ORDER = ("claude_code", "codex", "gemini_cli", "ollama", "lmstudio")


def agent_clis() -> list[dict[str, Any]]:
    found = []
    for preset, label, finder in (
        ("claude_code", "Claude Code", find_claude),
        ("codex", "OpenAI Codex CLI", find_codex),
        ("gemini_cli", "Gemini CLI", find_gemini),
    ):
        path = finder()
        found.append({"preset": preset, "label": label, "available": path is not None, "path": path})
    return found


def key_variables() -> list[dict[str, Any]]:
    return [
        {
            "preset": preset,
            "variables": [v for v in names if os.environ.get(v)],
            "available": any(os.environ.get(v) for v in names),
        }
        for preset, names in KEY_VARS.items()
    ]


async def discover_all(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    servers = await discover_local(client)
    clis = agent_clis()
    keys = key_variables()
    return {
        "agent_clis": clis,
        "local_servers": servers,
        "api_keys": keys,
        "auto_choice": auto_choice(clis, servers),
        "fallback": "none: basic tests with built-in payloads",
    }


def auto_choice(clis: list[dict[str, Any]], servers: list[dict[str, Any]]) -> dict[str, Any] | None:
    available = {c["preset"]: c for c in clis if c["available"]}
    for s in servers:
        if s["running"] and s["models"]:
            available[s["preset"]] = s
    for preset in AUTO_ORDER:
        if preset in available:
            entry = available[preset]
            model = entry["models"][0]["id"] if entry.get("models") else None
            return {"preset": preset, "model": model, "base_url": entry.get("base_url")}
    return None


def resolve_auto_sync() -> dict[str, Any] | None:
    """Synchronous variant for the CLI: same order, short timeouts."""
    clis = agent_clis()
    servers: list[dict[str, Any]] = []
    for preset, url, path, key in (
        ("ollama", OLLAMA_URL, "/api/tags", "models"),
        ("lmstudio", "http://localhost:1234", "/v1/models", "data"),
    ):
        try:
            data = httpx.get(url + path, timeout=2).json()
            models = [{"id": m.get("name") or m.get("id")} for m in data.get(key, [])]
            servers.append({"preset": preset, "running": True, "models": models, "base_url": f"{url}/v1"})
        except (httpx.HTTPError, ValueError):
            continue
    return auto_choice(clis, servers)
