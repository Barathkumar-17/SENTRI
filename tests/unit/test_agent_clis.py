"""Agent-CLI providers, driven against small fake CLIs that record their arguments and answer like the real ones."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from sentri.config import Settings
from sentri.control import discovery
from sentri.llm import LLMConfigError, PayloadSet, make_provider
from sentri.llm.agent_clis import CodexProvider, GeminiCliProvider, strict_schema
from sentri.llm.claude_code import ClaudeCodeProvider

ANSWER = {"values": [{"slot": "field_0", "value": ""}], "reason": "blank field"}

FAKE_CODEX = r"""
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
json.dump({"args": args, "prompt": prompt}, open(sys.argv[0] + ".log", "w"))
if "FAIL" in prompt:
    print("error: not signed in", file=sys.stderr); sys.exit(2)
out = args[args.index("-o") + 1]
schema = json.load(open(args[args.index("--output-schema") + 1]))
assert schema["additionalProperties"] is False
open(out, "w").write(json.dumps(ANSWER))
"""

FAKE_GEMINI = r"""
import json, sys
prompt = sys.stdin.read()
json.dump({"args": sys.argv[1:], "prompt": prompt}, open(sys.argv[0] + ".log", "w"))
if "FAIL" in prompt:
    print(json.dumps({"response": "", "error": {"type": "AuthError", "message": "please sign in"}})); sys.exit(1)
print(json.dumps({"response": "```json\n" + json.dumps(ANSWER) + "\n```", "stats": {}}))
"""

FAKE_CLAUDE = r"""
import json, sys
prompt = sys.stdin.read()
json.dump({"args": sys.argv[1:], "prompt": prompt}, open(sys.argv[0] + ".log", "w"))
if "FAIL" in prompt:
    print(json.dumps({"is_error": True, "subtype": "error", "result": "Not logged in"})); sys.exit(1)
print(json.dumps({"is_error": False, "subtype": "success", "structured_output": ANSWER,
                  "total_cost_usd": 0.004, "usage": {"input_tokens": 10, "output_tokens": 20}}))
"""


def fake(tmp_path: Path, name: str, body: str) -> tuple[list[str], Path]:
    script = tmp_path / f"{name}.py"
    script.write_text(f"ANSWER = {ANSWER!r}\n" + body, encoding="utf-8")
    return [sys.executable, str(script)], Path(str(script) + ".log")


async def test_codex_uses_documented_read_only_flags(tmp_path: Path):
    cmd, log = fake(tmp_path, "codex", FAKE_CODEX)
    result = await CodexProvider(cmd, {"default": "claude-sonnet-5-5"}).complete_json(
        "attacker", "sys", "u", PayloadSet
    )
    assert result.ok and result.value is not None and result.value.reason == "blank field"
    args = json.loads(log.read_text())["args"]
    assert args[0] == "exec" and args[-1] == "-"
    assert {"--skip-git-repo-check", "--ephemeral", "--output-schema", "-o"} <= set(args)
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--model" not in args  # a Claude default is never passed to another vendor's CLI


async def test_codex_passes_a_chosen_model_and_reports_failures(tmp_path: Path):
    cmd, log = fake(tmp_path, "codex", FAKE_CODEX)
    ok = await CodexProvider(cmd, {"default": "my-model"}).complete_json("attacker", "s", "u", PayloadSet)
    args = json.loads(log.read_text())["args"]
    assert ok.ok and args[args.index("--model") + 1] == "my-model"
    bad = await CodexProvider(cmd, {"default": "x"}).complete_json("attacker", "s", "FAIL", PayloadSet)
    assert not bad.ok and "not signed in" in (bad.error or "")


async def test_gemini_cli_parses_response_field(tmp_path: Path):
    cmd, log = fake(tmp_path, "gemini", FAKE_GEMINI)
    result = await GeminiCliProvider(cmd, {"default": "claude-sonnet-5-5"}).complete_json("a", "s", "u", PayloadSet)
    assert result.ok and result.value is not None and result.value.values[0].slot == "field_0"
    record = json.loads(log.read_text())
    assert record["args"][record["args"].index("--output-format") + 1] == "json"
    assert "--model" not in record["args"] and "JSON Schema" in record["prompt"]
    bad = await GeminiCliProvider(cmd, {"default": "x"}).complete_json("a", "s", "FAIL", PayloadSet)
    assert not bad.ok and "please sign in" in (bad.error or "")


async def test_claude_code_structured_output(tmp_path: Path):
    cmd, log = fake(tmp_path, "claude", FAKE_CLAUDE)
    result = await ClaudeCodeProvider(cmd, {"default": "claude-sonnet-5-5"}).complete_json("a", "s", "u", PayloadSet)
    assert result.ok and result.cost_usd == 0.004 and result.output_tokens == 20
    args = json.loads(log.read_text())["args"]
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5"
    assert args[args.index("--tools") + 1] == "" and "--strict-mcp-config" in args and "-p" in args
    bad = await ClaudeCodeProvider(cmd, {"default": "s"}).complete_json("a", "s", "FAIL", PayloadSet)
    assert not bad.ok and "sign in" in (bad.error or "")


def test_strict_schema():
    s = strict_schema(PayloadSet.model_json_schema())
    assert s["additionalProperties"] is False and set(s["required"]) == {"values", "reason"}
    item = s["$defs"]["SlotValue"]
    assert item["additionalProperties"] is False and set(item["required"]) == {"slot", "value"}


def test_missing_cli_is_a_clear_error(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SENTRI_CODEX_BIN", str(tmp_path / "nope"))
    s = Settings()
    s.llm_provider = "codex"
    with pytest.raises(LLMConfigError, match="Codex CLI"):
        make_provider(s)


def test_auto_prefers_signed_in_clis_then_local_servers_never_keys():
    clis = [{"preset": "claude_code", "available": False}, {"preset": "codex", "available": True, "path": "x"}]
    servers = [{"preset": "ollama", "running": True, "models": [{"id": "qwen2.5:7b"}], "base_url": "u"}]
    assert discovery.auto_choice(clis, servers)["preset"] == "codex"
    clis[1]["available"] = False
    assert discovery.auto_choice(clis, servers) == {"preset": "ollama", "model": "qwen2.5:7b", "base_url": "u"}
    assert discovery.auto_choice(clis, []) is None  # keys alone never make auto pick a paid API


def test_key_discovery_never_returns_values(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real-123")
    found = discovery.key_variables()
    assert "test-key-not-real-123" not in json.dumps(found)
    assert next(k for k in found if k["preset"] == "groq")["variables"] == ["GROQ_API_KEY"]
