"""Scrub secrets before anything is written to disk or sent to a model."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASSWORD", re.IGNORECASE)
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),  # OpenAI, Anthropic (sk-ant-), OpenRouter (sk-or-)
    re.compile(r"\bgsk_[A-Za-z0-9]{16,}"),  # Groq
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),  # Slack
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),  # JWTs
    # provider account identifiers quoted in API errors (OpenAI, Groq, Anthropic, ...)
    re.compile(r"\b(?:org|proj|user|acct|ws|wrkspc)[_-][A-Za-z0-9]{12,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"(?i)(authorization|cookie|set-cookie|x-api-key|api-key)\s*[:=]\s*[^\r\n]+"),
    re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd)=([^&\s\"']+)"),
]
REDACTED = "[REDACTED]"

# Secrets that never pass through os.environ (a key typed into the web app, a key read from a settings file).
_REGISTERED: set[str] = set()


def register_secret(value: str | None) -> None:
    """Treat this exact value as a secret everywhere `redact` runs, for the rest of the process."""
    if value and len(value) >= 6:
        _REGISTERED.add(value)


def _secret_values() -> list[str]:
    env = [v for k, v in os.environ.items() if _SECRET_ENV.search(k) and v and len(v) >= 6]
    return sorted({*env, *_REGISTERED}, key=len, reverse=True)


def redact(text: str) -> str:
    for value in _secret_values():
        text = text.replace(value, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrub every log record (message and arguments) before any handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = None
        except Exception:  # noqa: BLE001 - a log line must never break the caller
            pass
        return True


def install_log_redaction() -> None:
    """Attach the filter to the root logger's handlers (and the root logger itself) once."""
    root = logging.getLogger()
    for target in (root, *root.handlers):
        if not any(isinstance(f, RedactingFilter) for f in target.filters):
            target.addFilter(RedactingFilter())


def redact_obj(obj: Any) -> Any:
    """Recursively redact strings inside dicts, lists and tuples."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
