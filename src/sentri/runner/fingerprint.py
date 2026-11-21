"""App fingerprint (tree hash) used by the reuse rule, plus commit/branch discovery."""

from __future__ import annotations

import fnmatch
import hashlib
import subprocess
from pathlib import Path

# Files Sentri itself writes next to the app must not count as code changes.
_SENTRI_FILES = {"report.md", "contract.draft.yaml"}
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".sentri", "evidence", ".mypy_cache"}


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def git_commit(cwd: Path) -> str | None:
    return _git(["rev-parse", "HEAD"], cwd)


def git_branch(cwd: Path) -> str | None:
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return None if branch in (None, "HEAD") else branch


def _dockerignore(root: Path) -> list[str]:
    path = root / ".dockerignore"
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")]


def _hash_files(root: Path, exclude: frozenset[Path] = frozenset()) -> str:
    ignore = _dockerignore(root)
    h = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if any(fnmatch.fnmatch(rel, pat) for pat in ignore) or not path.is_file():
            continue
        if path.resolve() in exclude or path.name in _SENTRI_FILES or path.name.startswith(".contract-"):
            continue
        h.update(rel.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def app_fingerprint(root: Path, exclude: list[Path] | None = None) -> str:
    """Hash of the app's files. Git-independent so a dirty or untracked app dir still changes it.
    The contract is covered by its own hash, so contract and report files are excluded."""
    return _hash_files(root.resolve(), frozenset(p.resolve() for p in exclude or []))
