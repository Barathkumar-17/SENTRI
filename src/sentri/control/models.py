"""Model choice for the UI: provider presets, local model discovery, pulling, and connection tests.

The user always brings the model. No preset carries a key; keys come from the user per session.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from sentri.config import Settings

OLLAMA_URL = "http://localhost:11434"
LMSTUDIO_URL = "http://localhost:1234"


class ProviderPreset(BaseModel):
    id: str
    label: str
    kind: Literal["none", "anthropic", "claude_code", "codex", "gemini_cli", "openai_compatible"]
    base_url: str | None = None
    needs_key: bool
    local: bool = False
    key_env: str | None = None
    note: str = ""


PRESETS: list[ProviderPreset] = [
    ProviderPreset(
        id="none",
        label="No model (basic tests only)",
        kind="none",
        needs_key=False,
        note="Every check runs with Sentri's built-in payloads.",
    ),
    ProviderPreset(
        id="ollama",
        label="Ollama (on this computer)",
        kind="openai_compatible",
        base_url=f"{OLLAMA_URL}/v1",
        needs_key=False,
        local=True,
        note="Pick an installed model or pull one.",
    ),
    ProviderPreset(
        id="lmstudio",
        label="LM Studio (on this computer)",
        kind="openai_compatible",
        base_url=f"{LMSTUDIO_URL}/v1",
        needs_key=False,
        local=True,
    ),
    ProviderPreset(
        id="claude_code",
        label="Claude Code (your Claude login, no API key)",
        kind="claude_code",
        needs_key=False,
        local=True,
        note="Uses the Claude Code installed and signed in on this computer.",
    ),
    ProviderPreset(
        id="codex",
        label="OpenAI Codex CLI (your login, no API key)",
        kind="codex",
        needs_key=False,
        local=True,
        note="Uses the Codex CLI installed and signed in on this computer.",
    ),
    ProviderPreset(
        id="gemini_cli",
        label="Gemini CLI (your login, no API key)",
        kind="gemini_cli",
        needs_key=False,
        local=True,
        note="Uses the Gemini CLI installed and signed in on this computer.",
    ),
    ProviderPreset(
        id="anthropic", label="Anthropic Claude", kind="anthropic", needs_key=True, key_env="ANTHROPIC_API_KEY"
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        kind="openai_compatible",
        base_url="https://api.openai.com/v1",
        needs_key=True,
        key_env="OPENAI_API_KEY",
    ),
    ProviderPreset(
        id="groq",
        label="Groq",
        kind="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        needs_key=True,
        key_env="GROQ_API_KEY",
    ),
    ProviderPreset(
        id="openrouter",
        label="OpenRouter",
        kind="openai_compatible",
        base_url="https://openrouter.ai/api/v1",
        needs_key=True,
        key_env="OPENROUTER_API_KEY",
    ),
    ProviderPreset(
        id="gemini",
        label="Google Gemini (OpenAI-compatible endpoint)",
        kind="openai_compatible",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        needs_key=True,
        key_env="GEMINI_API_KEY",
    ),
    ProviderPreset(
        id="custom",
        label="Other OpenAI-compatible server",
        kind="openai_compatible",
        needs_key=False,
        note="vLLM, llama.cpp, a self-hosted gateway...",
    ),
]
PRESET_BY_ID = {p.id: p for p in PRESETS}


class ModelSelection(BaseModel):
    """What the user picked in the UI. `api_key` is a secret: never echoed back, never written to the ledger."""

    preset: str = "none"
    base_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    model: str | None = None
    model_smart: str | None = None

    def public(self) -> dict[str, Any]:
        data = self.model_dump(exclude={"api_key"})
        data["has_key"] = bool(self.api_key)
        return data

    def apply(self, settings: Settings) -> Settings:
        preset = PRESET_BY_ID.get(self.preset)
        if preset is None:
            raise ValueError(f"unknown provider preset {self.preset!r}")
        settings.llm_provider = preset.kind
        # The user picked this provider, so its standard key variable may be used if no key was typed in.
        env_key = os.environ.get(preset.key_env) if preset.key_env else None
        settings.api_key = self.api_key or settings.api_key or env_key or None
        if preset.kind == "openai_compatible":
            settings.llm_base_url = self.base_url or preset.base_url
            if not settings.llm_base_url:
                raise ValueError("this provider needs a base URL")
            if not self.model:
                raise ValueError("choose a model")
        if self.model:
            settings.model_fast = self.model
            settings.model_smart = self.model_smart or self.model
        elif self.model_smart:
            settings.model_smart = self.model_smart
        if preset.local:
            settings.llm_timeout_s = max(settings.llm_timeout_s, 300)
        if preset.needs_key and not settings.api_key:
            raise ValueError(f"{preset.label} needs an API key")
        return settings


async def _get_json(client: httpx.AsyncClient, url: str) -> Any | None:
    try:
        resp = await client.get(url, timeout=3)
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


async def discover_local(client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    """Find local model servers and their installed models."""
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        servers: list[dict[str, Any]] = []
        tags = await _get_json(client, f"{OLLAMA_URL}/api/tags")
        servers.append(
            {
                "preset": "ollama",
                "running": tags is not None,
                "base_url": f"{OLLAMA_URL}/v1",
                "can_pull": tags is not None,
                "models": [
                    {"id": m.get("name"), "size_bytes": m.get("size"), "family": (m.get("details") or {}).get("family")}
                    for m in (tags or {}).get("models", [])
                ],
                "install_hint": None
                if tags is not None
                else "Install Ollama from ollama.com, then run `ollama serve`.",
            }
        )
        lm = await _get_json(client, f"{LMSTUDIO_URL}/v1/models")
        servers.append(
            {
                "preset": "lmstudio",
                "running": lm is not None,
                "base_url": f"{LMSTUDIO_URL}/v1",
                "can_pull": False,
                "models": [{"id": m.get("id")} for m in (lm or {}).get("data", [])],
                "install_hint": None if lm is not None else "Start LM Studio's local server.",
            }
        )
        return servers
    finally:
        if own:
            await client.aclose()


async def pull_ollama(model: str, client: httpx.AsyncClient | None = None) -> AsyncIterator[dict[str, Any]]:
    """Stream Ollama pull progress as dicts: {status, completed?, total?}. Ends with {status: 'success'} or error."""
    own = client is None
    client = client or httpx.AsyncClient(timeout=None)
    try:
        async with client.stream("POST", f"{OLLAMA_URL}/api/pull", json={"model": model, "stream": True}) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode(errors="replace")[:300]
                yield {"status": "error", "error": f"HTTP {resp.status_code}: {body}"}
                return
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "error" in event:
                    yield {"status": "error", "error": event["error"]}
                    return
                yield {k: event[k] for k in ("status", "completed", "total", "digest") if k in event}
    except httpx.HTTPError as exc:
        yield {"status": "error", "error": f"Ollama not reachable: {type(exc).__name__}. Is `ollama serve` running?"}
    finally:
        if own:
            await client.aclose()


async def check_openai_compatible(base_url: str, api_key: str | None) -> tuple[bool, str, list[str]]:
    """GET {base}/models. Returns (ok, detail, model ids)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(base_url.rstrip("/") + "/models", headers=headers)
    except httpx.HTTPError as exc:
        hint = " (is `ollama serve` running?)" if "11434" in base_url else ""
        return False, f"not reachable: {type(exc).__name__}{hint}", []
    if resp.status_code in (401, 403):
        return False, f"the server rejected the API key (HTTP {resp.status_code})", []
    if resp.status_code >= 400:
        return True, f"reachable (HTTP {resp.status_code} on /models)", []
    try:
        ids = [str(m.get("id")) for m in resp.json().get("data", [])]
    except (ValueError, AttributeError):
        ids = []
    return True, f"reachable, {len(ids)} models listed" if ids else "reachable", ids


async def check_anthropic(api_key: str | None) -> tuple[bool, str, list[str]]:
    if not api_key:
        return False, "an API key is required", []
    import anthropic

    try:
        async with anthropic.AsyncAnthropic(api_key=api_key, max_retries=0, timeout=10) as client:
            ids = [m.id async for m in client.models.list(limit=50)]
    except anthropic.AuthenticationError:
        return False, "the API key was rejected", []
    except anthropic.APIConnectionError as exc:
        return False, f"not reachable: {exc}", []
    except anthropic.APIStatusError as exc:
        return False, f"API error {exc.status_code}", []
    return True, f"key accepted, {len(ids)} models available", ids


async def test_selection(selection: ModelSelection) -> dict[str, Any]:
    preset = PRESET_BY_ID.get(selection.preset)
    if preset is None:
        return {"ok": False, "detail": f"unknown provider {selection.preset!r}", "models": []}
    if preset.kind == "none":
        return {"ok": True, "detail": "no model: basic tests with built-in payloads", "models": []}
    if preset.kind in ("claude_code", "codex", "gemini_cli"):
        from sentri.llm.agent_clis import find_codex, find_gemini
        from sentri.llm.claude_code import find_claude

        finder = {"claude_code": find_claude, "codex": find_codex, "gemini_cli": find_gemini}[preset.kind]
        binary = finder()
        if binary is None:
            return {"ok": False, "detail": f"{preset.label.split(' (')[0]} not found on this computer", "models": []}
        models = ["claude-sonnet-5-5"] if preset.kind == "claude_code" else []
        return {"ok": True, "detail": f"found at {binary}", "models": models}
    if preset.kind == "anthropic":
        ok, detail, ids = await check_anthropic(selection.api_key)
    else:
        base = selection.base_url or preset.base_url
        if not base:
            return {"ok": False, "detail": "a base URL is required", "models": []}
        ok, detail, ids = await check_openai_compatible(base, selection.api_key)
    if ok and selection.model and ids and selection.model not in ids:
        detail += f"; note: {selection.model!r} is not in the server's model list"
    return {"ok": ok, "detail": detail, "models": ids}
