"""Scrub secrets before anything is written to disk or sent to a model."""

from __future__ import annotations

import os
import re
from typing import Any

_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\borg[_-][A-Za-z0-9]{12,}"),  # provider organization ids (OpenAI, Groq) quoted in API errors
    re.compile(r"(?i)(authorization|cookie|set-cookie)\s*[:=]\s*[^\r\n]+"),
]
REDACTED = "[REDACTED]"


def _secret_values() -> list[str]:
    return [v for k, v in os.environ.items() if _SECRET_ENV.search(k) and v and len(v) >= 6]


def redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts, lists and tuples."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
