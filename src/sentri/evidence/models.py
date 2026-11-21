"""Evidence collected during one attempt. Frozen and JSON-serialisable."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

DuplicateMethod = Literal["db", "list_selector", "post_heuristic", "text_heuristic", "none"]


@dataclass(frozen=True)
class HttpRecord:
    url: str
    method: str
    status: int | None
    duration_ms: float
    started_ms: float
    request_body_sha256: str | None
    same_origin: bool
    failed: bool = False
    failure: str | None = None
    resource_type: str = "other"


@dataclass(frozen=True)
class ConsoleRecord:
    kind: Literal["console_error", "page_error"]
    text: str
    source_url: str | None = None


@dataclass(frozen=True)
class LogLine:
    ts: str
    stream: Literal["stdout", "stderr"]
    text: str


@dataclass(frozen=True)
class Evidence:
    http: tuple[HttpRecord, ...] = ()
    console: tuple[ConsoleRecord, ...] = ()
    logs: tuple[LogLine, ...] = ()
    list_count_before: int | None = None
    list_count_after: int | None = None
    duplicate_method: DuplicateMethod = "none"
    page_text_rows: tuple[str, ...] = ()
    unique_values: tuple[str, ...] = ()
    timed_out: bool = False
    timeout_detail: str | None = None
    page_crashed: bool = False
    step_blocked: str | None = None
    expected_creates: int | None = None
    screenshot_path: str | None = None
    actions: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> Evidence:
        return Evidence(
            http=tuple(HttpRecord(**h) for h in data.get("http", ())),
            console=tuple(ConsoleRecord(**c) for c in data.get("console", ())),
            logs=tuple(LogLine(**line) for line in data.get("logs", ())),
            list_count_before=data.get("list_count_before"),
            list_count_after=data.get("list_count_after"),
            duplicate_method=data.get("duplicate_method", "none"),
            page_text_rows=tuple(data.get("page_text_rows", ())),
            unique_values=tuple(data.get("unique_values", ())),
            timed_out=data.get("timed_out", False),
            timeout_detail=data.get("timeout_detail"),
            page_crashed=data.get("page_crashed", False),
            step_blocked=data.get("step_blocked"),
            expected_creates=data.get("expected_creates"),
            screenshot_path=data.get("screenshot_path"),
            actions=tuple(data.get("actions", ())),
        )
