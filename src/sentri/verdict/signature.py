"""Bug signatures: stable identity for "the same bug" across runs and commits."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit

_ID_SEGMENT = re.compile(
    r"^(\d+|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|[0-9a-fA-F]{16,})$"
)
_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_LINE_NO = re.compile(r"(line\s+)\d+|:\d+(:\d+)?")
_NUMBER = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def normalize_path(url: str) -> str:
    path = urlsplit(url).path or "/"
    parts = [":id" if _ID_SEGMENT.match(p) else p for p in path.split("/")]
    return "/".join(parts) or "/"


def normalize_route(method: str, url: str) -> str:
    return f"{method.upper()} {normalize_path(url)}"


def normalize_error(text: str) -> str:
    out = text.lower()
    out = _HEX_ADDR.sub("<addr>", out)
    out = _QUOTED.sub("<str>", out)
    out = _LINE_NO.sub(r"\1", out)
    out = _NUMBER.sub("<n>", out)
    out = _SPACE.sub(" ", out).strip()
    return out[:200]


def signature(oracle_id: str, flow_id: str, attack_id: str, route: str | None, error_key: str | None) -> str:
    raw = "|".join([oracle_id, flow_id, attack_id, route or "", error_key or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
