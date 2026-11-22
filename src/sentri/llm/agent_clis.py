"""Providers for other coding-agent CLIs the user may have signed in to: OpenAI Codex CLI and Gemini CLI.

Like the Claude Code provider, these use the user's own login on their own machine; Sentri holds no key.
Flags follow each tool's documented non-interactive mode:
- Codex:  `codex exec -` (prompt on stdin), `--output-schema FILE`, `-o FILE` (final message), read-only
          sandbox, `--skip-git-repo-check`, `--ephemeral`. Docs: learn.chatgpt.com/docs/non-interactive-mode
- Gemini: `gemini -p ...` with the prompt piped on stdin, `--output-format json` -> {response, stats, error}.
          No schema flag, so the JSON shape is requested in the prompt. Docs: geminicli.com/docs/cli/headless
A model flag is passed only when the user picked a model; otherwise each CLI uses its own default.
Every answer is validated against the schema here, and anything invalid falls back to built-in payloads.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from sentri.llm.provider import LLMResult

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def find_cli(name: str, env_var: str) -> str | None:
    explicit = os.environ.get(env_var)
    if explicit:
        return explicit if Path(explicit).exists() else None
    return shutil.which(name)


def find_codex() -> str | None:
    return find_cli("codex", "SENTRI_CODEX_BIN")


def find_gemini() -> str | None:
    return find_cli("gemini", "SENTRI_GEMINI_BIN")


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """OpenAI-style strict schema: every object lists all its properties as required and forbids extras."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items()}
            if isinstance(out.get("properties"), dict):
                out["required"] = list(out["properties"])
                out["additionalProperties"] = False
            out.pop("default", None)
            return out
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result: dict[str, Any] = walk(schema)
    return result


def extract_json(text: str) -> str:
    text = _FENCE.sub("", text.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text


async def run_cli(args: list[str], stdin: str, cwd: Path, timeout_s: float) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        return 127, "", f"could not start {args[0]}: {exc}"
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8")), timeout=timeout_s)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return 124, "", f"no answer within {timeout_s:g}s"
    return proc.returncode or 0, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")


def _last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no output"


def _chosen_model(models: dict[str, str], role: str) -> str | None:
    """Only pass a model the user picked for this tool; Sentri's Claude defaults mean 'use the CLI default'."""
    model = models.get(role) or models.get("default")
    return None if not model or model.startswith("claude-") else model


class CodexProvider:
    name = "codex"

    def __init__(self, command: list[str], models: dict[str, str], timeout_s: float = 300):
        self.command = command
        self.models = models
        self.timeout_s = timeout_s
        self._cwd = Path(tempfile.mkdtemp(prefix="sentri-codex-"))

    @property
    def available(self) -> bool:
        return True

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = _chosen_model(self.models, role)
        schema_file = self._cwd / f"schema-{schema.__name__}.json"
        out_file = self._cwd / f"answer-{os.getpid()}-{id(schema)}.json"
        schema_file.write_text(json.dumps(strict_schema(schema.model_json_schema())), encoding="utf-8")
        out_file.unlink(missing_ok=True)
        args = [
            *self.command,
            "exec",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--output-schema",
            str(schema_file),
            "-o",
            str(out_file),
        ]
        if model:
            args += ["--model", model]
        args.append("-")  # read the prompt from stdin
        code, out, err = await run_cli(args, f"{system}\n\n{user}", self._cwd, self.timeout_s)
        label = model or "codex default"
        if code != 0:
            return LLMResult(ok=False, model=label, error=f"Codex exited {code}: {_last_line(err or out)}")
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else out
        try:
            return LLMResult(ok=True, value=schema.model_validate_json(extract_json(text)), model=label)
        except ValidationError as exc:
            return LLMResult(ok=False, model=label, error=f"schema validation failed ({exc.error_count()})")


class GeminiCliProvider:
    name = "gemini_cli"

    def __init__(self, command: list[str], models: dict[str, str], timeout_s: float = 300):
        self.command = command
        self.models = models
        self.timeout_s = timeout_s
        self._cwd = Path(tempfile.mkdtemp(prefix="sentri-gemini-"))

    @property
    def available(self) -> bool:
        return True

    async def complete_json[T: BaseModel](
        self, role: str, system: str, user: str, schema: type[T], max_tokens: int = 4000
    ) -> LLMResult[T]:
        model = _chosen_model(self.models, role)
        prompt = (
            f"{system}\n\n{user}\n\nReply with one JSON object only, no prose and no tool use, matching this "
            f"JSON Schema:\n{json.dumps(schema.model_json_schema())}"
        )
        args = [*self.command, "-p", "Follow the instructions above.", "--output-format", "json"]
        if model:
            args += ["--model", model]
        code, out, err = await run_cli(args, prompt, self._cwd, self.timeout_s)
        label = model or "gemini default"
        try:
            data = json.loads(out)
        except ValueError:
            return LLMResult(ok=False, model=label, error=f"Gemini CLI exited {code}: {_last_line(err or out)}")
        if data.get("error"):
            detail = data["error"].get("message") if isinstance(data["error"], dict) else data["error"]
            return LLMResult(ok=False, model=label, error=f"Gemini CLI: {str(detail)[:200]}")
        try:
            value = schema.model_validate_json(extract_json(str(data.get("response", ""))))
        except ValidationError as exc:
            return LLMResult(ok=False, model=label, error=f"schema validation failed ({exc.error_count()})")
        return LLMResult(ok=True, value=value, model=label)
