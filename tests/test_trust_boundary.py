"""The AI proposes, plain code decides: the verdict path must never import model-facing code."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "sentri"
TRUSTED = ("verdict", "ledger", "report")
FORBIDDEN = ("sentri.llm", "sentri.attacker", "sentri.planner", "sentri.fixer", "anthropic", "httpx")


def imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.append(node.module)
    return out


def test_trusted_zone_has_no_llm_imports():
    offenders = []
    for package in TRUSTED:
        for path in (SRC / package).rglob("*.py"):
            for name in imports(path):
                if name.startswith(FORBIDDEN):
                    offenders.append(f"{path.relative_to(SRC)} imports {name}")
    assert not offenders, offenders


def test_intended_status_only_set_by_humans():
    """HumanDecision is constructed only by the CLI and the dashboard."""
    allowed = {"cli.py", "server.py", "ledger.py"}
    for path in SRC.rglob("*.py"):
        if "HumanDecision(" in path.read_text(encoding="utf-8") and path.name not in allowed:
            raise AssertionError(f"{path.relative_to(SRC)} constructs HumanDecision")
