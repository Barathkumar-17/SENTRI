"""Placeholder resolution with seeded, replayable test data."""

from __future__ import annotations

import hashlib
import os
import random
import struct
import zlib
from pathlib import Path

from sentri.contract.models import PLACEHOLDER_RE


def tiny_png() -> bytes:
    """A valid 1x1 PNG, built in code so no binary assets are needed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    idat = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def seed_for(*parts: object) -> int:
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


class DataGenerator:
    """Produces placeholder values. One instance per attempt, seeded from (run, flow, attack, attempt)."""

    def __init__(self, seed: int, fixtures: dict[str, str], tmp_dir: Path, tag: str):
        self.rng = random.Random(seed)
        self.fixtures = fixtures
        self.tmp_dir = tmp_dir
        self.tag = tag
        self.values: dict[str, str] = {}

    def _token(self) -> str:
        return "".join(self.rng.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(6))

    def value(self, name: str) -> str:
        if name in self.values:
            return self.values[name]
        if name in self.fixtures:
            v = self.fixtures[name]
        elif name == "email":
            v = f"sentri+{self.tag}-{self._token()}@example.test"
        elif name == "password":
            v = f"Pw-{self._token()}-{self.rng.randint(100, 999)}!"
        elif name == "title":
            v = f"Sentri item {self.tag}-{self._token()}"
        elif name == "name":
            v = f"Sentri User {self._token()}"
        elif name == "text":
            v = f"Sentri text {self.tag}-{self._token()}"
        elif name == "number":
            v = str(self.rng.randint(1, 999))
        elif name == "file":
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
            path = self.tmp_dir / f"sentri-{self._token()}.png"
            path.write_bytes(tiny_png())
            v = str(path)
        elif name in ("seed_email", "seed_password"):
            env = "SENTRI_SEED_EMAIL" if name == "seed_email" else "SENTRI_SEED_PASSWORD"
            env_value = os.environ.get(env)
            if not env_value:
                raise KeyError(f"placeholder {{{name}}} needs fixtures.{name} or env {env}")
            v = env_value
        else:
            raise KeyError(f"unknown placeholder {{{name}}}")
        self.values[name] = v
        return v

    def resolve(self, text: str) -> str:
        return PLACEHOLDER_RE.sub(lambda m: self.value(m.group(1)), text)

    def unique_values(self) -> list[str]:
        """Generated values that should appear once in the app after one create (for duplicate checks)."""
        return [self.values[k] for k in ("title", "email", "text", "name") if k in self.values]
