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


def _module_path(name: str) -> Path | None:
    rel = Path(*name.split(".")[1:])
    for candidate in (SRC / rel.with_suffix(".py"), SRC / rel / "__init__.py"):
        if candidate.exists():
            return candidate
    return None


def transitive_imports(module: str) -> dict[str, str]:
    """Every module reachable from `module` through imports (package __init__ files included),
    mapped to the module that first imported it."""
    seen: dict[str, str] = {module: ""}
    queue = [module]
    while queue:
        current = queue.pop()
        path = _module_path(current)
        if path is None:
            continue
        for name in imports(path):
            targets = [name]
            if name.startswith("sentri."):  # importing sentri.a.b runs sentri/a/__init__.py first
                parts = name.split(".")
                targets += [".".join(parts[:i]) for i in range(2, len(parts))]
            for target in targets:
                if target not in seen:
                    seen[target] = current
                    if target.startswith("sentri."):
                        queue.append(target)
    return seen


def test_replay_and_confirmation_never_reach_model_code():
    """Everything from replay onwards (re-running the steps, the oracles, the verdict) is plain code: the
    confirmation path must not import model-facing code, even indirectly."""
    for module in ("sentri.agent.replay", "sentri.agent.session", "sentri.verdict.agent_oracles"):
        reached = transitive_imports(module)
        bad = {name: via for name, via in reached.items() if name.startswith(FORBIDDEN)}
        assert not bad, f"{module} reaches model code: {bad}"


def test_transitive_check_catches_an_indirect_import():
    assert any(n.startswith("sentri.llm") for n in transitive_imports("sentri.agent.explorer"))
