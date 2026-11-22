"""Provider that uses the user's own Claude Code login through its headless mode (`claude -p`).

No API key: the installed Claude Code CLI authenticates with the account it is signed in to. Each call runs
`claude -p` with tools, MCP servers and session persistence switched off, from a neutral temporary folder, and
asks for output matching the schema (`--json-schema`). The answer is validated again here.
"""

from __future__ import annotations

import asyncio
import contextlib
import glob
import json
import logging
import os
import shutil
import tempfile
from pathlib import Path

from pydantic import BaseModel, ValidationError

from sentri.llm.provider import LLMResult

log = logging.getLogger(__name__)

_EXE = "claude.exe" if os.name == "nt" else "claude"
# Editors that bundle the Claude Code CLI inside their extension folder.
_EXTENSION_DIRS = (".vscode", ".vscode-insiders", ".cursor", ".windsurf", ".vscode-oss")


def find_claude() -> str | None:
    """Locate the Claude Code CLI: SENTRI_CLAUDE_BIN, then PATH, then a bundled copy in an editor extension."""
    explicit = os.environ.get("SENTRI_CLAUDE_BIN")
    if explicit:
        return explicit if Path(explicit).exists() else None
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    home = Path.home()
    candidates: list[str] = []
    for editor in _EXTENSION_DIRS:
        pattern = str(home / editor / "extensions" / "anthropic.claude-code-*" / "resources" / "native-binary" / _EXE)
        candidates.extend(glob.glob(pattern))
    # Newest extension version first.
    candidates.sort(key=lambda p: Path(p).stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


class ClaudeCodeProvider:
    name = "claude_code"

    def __init__(self, command: list[str], models: dict[str, str], timeout_s: float = 180):
        self.command = command
        self.models = models
        self.timeout_s = timeout_s
        self._cwd = Path(tempfile.mkdtemp(prefix="sentri-claude-"))  # no CLAUDE.md, no project settings here

    @property
    def available(self) -> bool:
        return True

    def _args(self, model: str, system: str, schema: type[BaseModel]) -> list[str]:
        return [
            *self.command,
            "-p",
            "--output-format",
            "json",
            "--model",
            model,
            "--tools",
            "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt",
            system,
            "--json-schema",
            json.dumps(schema.model_json_schema()),
        ]

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = self.models.get(role) or self.models["default"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *self._args(model, system, schema),
                cwd=self._cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return LLMResult(ok=False, model=model, error=f"could not start Claude Code: {exc}")
        try:
            out, err = await asyncio.wait_for(proc.communicate(user.encode("utf-8")), timeout=self.timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            return LLMResult(ok=False, model=model, error=f"Claude Code did not answer within {self.timeout_s:g}s")
        try:
            data = json.loads(out.decode("utf-8", errors="replace"))
        except ValueError:
            detail = (err or out).decode("utf-8", errors="replace").strip().splitlines()
            return LLMResult(ok=False, model=model, error=f"Claude Code: {(detail or ['no output'])[-1][:200]}")
        usage = data.get("usage") or {}
        tokens_in = int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        cost = float(data.get("total_cost_usd") or 0.0)  # notional on a subscription; recorded for visibility
        if data.get("is_error") or data.get("subtype") != "success":
            msg = str(data.get("result") or data.get("subtype") or "error")[:200]
            if any(w in msg.lower() for w in ("login", "logged in", "log in", "sign in", "auth")):
                msg += " (sign in to Claude Code first: run `claude` once and log in)"
            return LLMResult(False, None, tokens_in, tokens_out, cost, model, f"Claude Code: {msg}")
        raw = data.get("structured_output")
        try:
            value = (
                schema.model_validate(raw) if raw is not None else schema.model_validate_json(data.get("result", ""))
            )
        except ValidationError as exc:
            return LLMResult(
                False, None, tokens_in, tokens_out, cost, model, f"schema validation failed ({exc.error_count()})"
            )
        return LLMResult(True, value, tokens_in, tokens_out, cost, model)
