"""Inspect a project folder the user points at and suggest how Sentri should start it.

Suggestions only: the user confirms the command and port before anything runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_EXPOSE = re.compile(r"^\s*EXPOSE\s+(\d+)", re.IGNORECASE | re.MULTILINE)
_PORT_IN_SCRIPT = re.compile(r"(?:--port[ =]|-p |PORT=)(\d{2,5})")
CONTRACT_NAMES = ("contract.yaml", "contract.yml", "sentri.yaml", "contract.draft.yaml")


def _read(path: Path, limit: int = 200_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _node(root: Path) -> list[dict[str, Any]]:
    pkg_path = root / "package.json"
    if not pkg_path.exists():
        return []
    try:
        pkg = json.loads(_read(pkg_path))
    except ValueError:
        return []
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    framework = next(
        (f for f in ("next", "vite", "nuxt", "@sveltejs/kit", "react-scripts", "express") if f in deps), None
    )
    default_port = {"vite": 5173, "@sveltejs/kit": 5173, "nuxt": 3000, "next": 3000, "react-scripts": 3000}.get(
        framework or "", 3000
    )
    manager = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() else "npm"
    out = []
    for name in ("start", "dev", "serve", "preview"):
        if name in scripts:
            m = _PORT_IN_SCRIPT.search(str(scripts[name]))
            port = int(m.group(1)) if m else default_port
            run = f"{manager} run {name}" if not (manager == "npm" and name == "start") else "npm start"
            out.append(
                {
                    "kind": "command",
                    "run": run,
                    "port": port,
                    "source": f"package.json scripts.{name}",
                    "framework": framework,
                    "needs_install": not (root / "node_modules").exists(),
                }
            )
    return out


def _python(root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    reqs = " ".join(_read(root / f).lower() for f in ("requirements.txt", "pyproject.toml", "Pipfile"))
    if (root / "manage.py").exists():
        out.append(
            {
                "kind": "command",
                "run": "{python} manage.py runserver 127.0.0.1:8000 --noreload",
                "port": 8000,
                "source": "manage.py (Django)",
                "framework": "django",
            }
        )
    for name in ("app.py", "main.py", "server.py", "wsgi.py"):
        path = root / name
        if not path.exists():
            continue
        text = _read(path)
        if "FastAPI(" in text or "fastapi" in reqs:
            out.append(
                {
                    "kind": "command",
                    "run": f"{{python}} -m uvicorn {path.stem}:app --port 8000",
                    "port": 8000,
                    "source": name,
                    "framework": "fastapi",
                }
            )
        elif "Flask(" in text or "flask" in reqs:
            m = re.search(r"port\s*=\s*(\d{2,5})", text) or re.search(r"PORT[\"']?\s*,\s*[\"'](\d{2,5})", text)
            port = int(m.group(1)) if m else 5000
            out.append(
                {"kind": "command", "run": f"{{python}} {name}", "port": port, "source": name, "framework": "flask"}
            )
    return out


def _docker(root: Path) -> list[dict[str, Any]]:
    dockerfile = root / "Dockerfile"
    if not dockerfile.exists():
        return []
    m = _EXPOSE.search(_read(dockerfile))
    port = int(m.group(1)) if m else None
    return [
        {
            "kind": "docker",
            "dockerfile": "Dockerfile",
            "port": port,
            "source": "Dockerfile",
            "framework": None,
            "port_unknown": port is None,
        }
    ]


def inspect_project(path: str | Path) -> dict[str, Any]:
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"{root} is not a folder")
    readme = next(
        (root / n for n in ("README.md", "README.rst", "README.txt", "readme.md") if (root / n).exists()), None
    )
    contracts = [str(root / n) for n in CONTRACT_NAMES if (root / n).exists()]
    suggestions = _docker(root) + _node(root) + _python(root)
    return {
        "path": str(root),
        "name": root.name,
        "is_git": (root / ".git").exists(),
        "readme": str(readme) if readme else None,
        "contracts": contracts,
        "suggestions": suggestions,
        "notes": [] if suggestions else ["Could not tell how to start this app; enter the start command and port."],
    }
