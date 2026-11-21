"""SQLite connection and numbered migrations."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def _migrations() -> list[tuple[int, str]]:
    folder = resources.files("sentri.ledger") / "migrations"
    out = []
    for entry in folder.iterdir():
        if entry.name.endswith(".sql"):
            out.append((int(entry.name.split("_", 1)[0]), entry.read_text(encoding="utf-8")))
    return sorted(out)


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, sql in _migrations():
        if number > current:
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version={number}")
    conn.commit()
    return conn
