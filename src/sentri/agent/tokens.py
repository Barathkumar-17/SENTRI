"""Sentri-generated values (placeholders, canaries, markers): unique by construction, so plain code can find
them again in request bodies and responses. Shared by exploration and replay; imports nothing model-facing."""

from __future__ import annotations

import re

_TOKEN = re.compile(r"sentri[\w.+@-]{4,}")


def tokens_in(text: str | None) -> list[str]:
    """Sentri-generated values (placeholders, canaries) in a request body: unique by construction."""
    return sorted(set(_TOKEN.findall(text or "")))
