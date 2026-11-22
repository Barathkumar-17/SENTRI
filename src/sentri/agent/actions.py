"""The agent's action space: typed browser actions the model may choose, validated before anything runs.

Everything is fenced to the app under test: navigation and raw requests are resolved against the sandbox's
base URL and refused if they would leave its origin. The model never gets a free-form "run this" action.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field, TypeAdapter

MAX_TEXT = 1_000_000  # characters a single type action may produce (input abuse needs huge strings)

FileShape = Literal["valid", "zero_byte", "oversized", "wrong_extension", "corrupt", "weird_name"]
Identity = Literal["anon", "a", "b"]


class _A(BaseModel):
    model_config = {"extra": "forbid"}


class Click(_A):
    kind: Literal["click"] = "click"
    selector: str = Field(max_length=500)


class TypeText(_A):
    """Fill a field. `text` is repeated `repeat` times, so huge inputs do not need huge model outputs."""

    kind: Literal["type"] = "type"
    selector: str = Field(max_length=500)
    text: str = Field(default="", max_length=5000)
    repeat: int = Field(default=1, ge=1, le=MAX_TEXT)
    submit: bool = False  # press Enter afterwards


class Navigate(_A):
    kind: Literal["navigate"] = "navigate"
    path: str = Field(max_length=4000)


class Upload(_A):
    kind: Literal["upload"] = "upload"
    selector: str = Field(max_length=500)
    shape: FileShape = "valid"
    filename: str | None = Field(default=None, max_length=255)
    size_mb: int = Field(default=20, ge=1, le=200)


class SendRequest(_A):
    """A raw HTTP request from the current identity's session (its cookies), e.g. a wrong method or tampered
    body. `from_request` copies an observed request and applies the overrides."""

    kind: Literal["request"] = "request"
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    path: str | None = Field(default=None, max_length=4000)
    from_request: int | None = None
    body: str | None = Field(default=None, max_length=200_000)
    content_type: str | None = Field(default=None, max_length=200)


class Replay(_A):
    """Send an observed request again, `times` times, sequentially or all at once (a race)."""

    kind: Literal["replay"] = "replay"
    request_id: int
    times: int = Field(default=2, ge=1, le=20)
    concurrent: bool = False


class Burst(_A):
    """Click the same element many times quickly, from inside the page."""

    kind: Literal["burst"] = "burst"
    selector: str = Field(max_length=500)
    times: int = Field(default=10, ge=2, le=50)
    interval_ms: int = Field(default=50, ge=0, le=2000)


class NewTab(_A):
    kind: Literal["new_tab"] = "new_tab"
    path: str = Field(default="/", max_length=4000)


class SwitchTab(_A):
    kind: Literal["switch_tab"] = "switch_tab"
    index: int = Field(ge=0, le=9)


class Back(_A):
    kind: Literal["back"] = "back"


class Refresh(_A):
    kind: Literal["refresh"] = "refresh"


class ClearSession(_A):
    """Drop cookies and storage of the current identity: the same tab continues logged out."""

    kind: Literal["clear_session"] = "clear_session"


class Throttle(_A):
    kind: Literal["throttle"] = "throttle"
    on: bool = True


class SwitchUser(_A):
    """Act as another identity: anon (never logged in), a or b (two separate fresh accounts)."""

    kind: Literal["switch_user"] = "switch_user"
    user: Identity


class Wait(_A):
    kind: Literal["wait"] = "wait"
    ms: int = Field(default=500, ge=0, le=5000)


class Finish(_A):
    kind: Literal["finish"] = "finish"
    reason: str = Field(default="", max_length=500)


Action = Annotated[
    Click
    | TypeText
    | Navigate
    | Upload
    | SendRequest
    | Replay
    | Burst
    | NewTab
    | SwitchTab
    | Back
    | Refresh
    | ClearSession
    | Throttle
    | SwitchUser
    | Wait
    | Finish,
    Field(discriminator="kind"),
]
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
ACTION_KINDS = [
    "click",
    "type",
    "navigate",
    "upload",
    "request",
    "replay",
    "burst",
    "new_tab",
    "switch_tab",
    "back",
    "refresh",
    "clear_session",
    "throttle",
    "switch_user",
    "wait",
    "finish",
]


class ActionRefused(Exception):
    """The action would leave the app under test or is otherwise not allowed. Never executed."""


def origin_of(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in ("localhost", "::1"):
        host = "127.0.0.1"
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, f"{host}:{port}"


def resolve_same_origin(base_url: str, target: str) -> str:
    """Resolve a path or URL against the app and refuse anything outside its origin."""
    target = target.strip()
    if not target:
        target = "/"
    lowered = target.lower()
    if lowered.startswith(("javascript:", "data:", "file:", "blob:", "vbscript:")):
        raise ActionRefused(f"scheme not allowed: {target[:40]}")
    url = urljoin(base_url.rstrip("/") + "/", target)
    if urlsplit(url).scheme not in ("http", "https"):
        raise ActionRefused(f"scheme not allowed: {url[:60]}")
    if origin_of(url) != origin_of(base_url):
        raise ActionRefused(f"outside the app under test: {url[:120]}")
    return url


def describe(action: Action) -> str:
    """One-line human description for the feed and the attack tree."""
    d = action.model_dump(exclude={"kind"}, exclude_defaults=True)
    if isinstance(action, TypeText):
        text = action.text if len(action.text) <= 40 else action.text[:37] + "..."
        size = f" x{action.repeat} ({len(action.text) * action.repeat} chars)" if action.repeat > 1 else ""
        return f"type {text!r}{size} into {action.selector}" + (" + Enter" if action.submit else "")
    args = ", ".join(f"{k}={v!r}" for k, v in d.items() if k != "body")
    return f"{action.kind}({args})" if args else action.kind
