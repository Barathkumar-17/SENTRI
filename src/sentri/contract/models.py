"""Pydantic models for contract.yaml (docs/reference/contract-schema.md)."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ATTACK_IDS = ("empty_input", "double_submit", "expired_session", "slow_network", "bad_file", "rapid_repeat")
STEP_KINDS = ("fill", "click", "upload", "select", "press", "wait_for", "goto")
BUILTIN_PLACEHOLDERS = frozenset(
    {"email", "password", "title", "name", "text", "number", "file", "seed_email", "seed_password"}
)
PLACEHOLDER_RE = re.compile(r"\{([a-z_][a-z0-9_]*)\}")

AttackId = Literal["empty_input", "double_submit", "expired_session", "slow_network", "bad_file", "rapid_repeat"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Step(_Strict):
    kind: Literal["fill", "click", "upload", "select", "press", "wait_for", "goto"]
    selector: str | None = None
    value: str | None = None
    file: str | None = None
    key: str | None = None
    state: Literal["visible", "hidden", "attached", "detached"] = "visible"
    path: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_yaml_form(cls, raw: Any) -> Any:
        """Accept the YAML shorthand `{fill: {...}}`, `{click: "sel"}`, `{goto: "/x"}`."""
        if isinstance(raw, dict) and "kind" not in raw:
            if len(raw) != 1:
                raise ValueError(f"a step must have exactly one action key, one of {STEP_KINDS}")
            ((kind, body),) = raw.items()
            if kind not in STEP_KINDS:
                raise ValueError(f"unknown step '{kind}', expected one of {STEP_KINDS}")
            if isinstance(body, str):
                return {"kind": kind, "path": body} if kind == "goto" else {"kind": kind, "selector": body}
            if not isinstance(body, dict):
                raise ValueError(f"step '{kind}' must be a string or a mapping")
            return {"kind": kind, **body}
        return raw

    @model_validator(mode="after")
    def _check_fields(self) -> Step:
        needs_selector = self.kind != "goto"
        if needs_selector and not self.selector:
            raise ValueError(f"{self.kind}: selector is required")
        if self.kind in ("fill", "select") and self.value is None:
            raise ValueError(f"{self.kind}: value is required")
        if self.kind == "upload" and not self.file:
            raise ValueError("upload: file is required")
        if self.kind == "press" and not self.key:
            raise ValueError("press: key is required")
        if self.kind == "goto" and not self.path:
            raise ValueError("goto: path is required")
        return self

    def placeholders(self) -> set[str]:
        found: set[str] = set()
        for text in (self.value, self.file):
            if text:
                found.update(PLACEHOLDER_RE.findall(text))
        return found

    def to_yaml(self) -> dict[str, Any]:
        if self.kind == "click":
            return {"click": self.selector}
        if self.kind == "goto":
            return {"goto": self.path}
        body = self.model_dump(exclude_none=True, exclude={"kind"})
        if self.kind != "wait_for":
            body.pop("state", None)
        return {self.kind: body}


class Success(_Strict):
    url_contains: str | None = None
    text_visible: str | None = None
    selector_visible: str | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> Success:
        if not (self.url_contains or self.text_visible or self.selector_visible):
            raise ValueError("success needs at least one of url_contains, text_visible, selector_visible")
        return self


class DockerApp(_Strict):
    context: str = "."
    dockerfile: str = "Dockerfile"
    container_port: int | None = None
    env: dict[str, str] = Field(default_factory=dict)


class AppConfig(_Strict):
    docker: DockerApp | None = None
    run: str | None = None
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    port: int = Field(ge=1, le=65535)
    base_url: str | None = None
    startup_timeout_s: float = 60
    health_path: str = "/"
    reset: Literal["restart", "command"] = "restart"
    reset_command: str | None = None
    log_patterns_extra: list[str] = Field(default_factory=list)
    ignore_console: list[str] = Field(default_factory=lambda: [r"favicon\.ico"])

    @model_validator(mode="after")
    def _check(self) -> AppConfig:
        if (self.docker is None) == (self.run is None):
            raise ValueError("exactly one of app.docker or app.run is required")
        if self.base_url is None:
            self.base_url = f"http://localhost:{self.port}"
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be http(s)")
        self.base_url = self.base_url.rstrip("/")
        if self.reset == "command" and not self.reset_command:
            raise ValueError("reset: command requires reset_command")
        for pattern in [*self.log_patterns_extra, *self.ignore_console]:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc
        return self

    @property
    def url(self) -> str:
        assert self.base_url is not None
        return self.base_url


class Defaults(_Strict):
    action_timeout_s: float = Field(default=10, ge=1, le=120)
    settle_ms: int = Field(default=1500, ge=0, le=60000)
    slow_network_timeout_multiplier: float = Field(default=3, ge=1, le=20)


class Flow(_Strict):
    id: str
    description: str = ""
    start: str
    steps: list[Step] = Field(min_length=1)
    success: Success
    requires_login: bool = False
    attack: bool = True
    list_selector: str | None = None
    list_page: str | None = None
    expected_creates: int | None = Field(default=None, ge=0)
    action_timeout_s: float | None = Field(default=None, ge=1, le=120)

    @field_validator("id")
    @classmethod
    def _id_format(cls, v: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", v):
            raise ValueError("flow id must match ^[a-z][a-z0-9_]{0,39}$")
        return v

    @model_validator(mode="after")
    def _check(self) -> Flow:
        if self.attack and self.submit_index() is None:
            raise ValueError("an attacked flow needs a click or press step (the submit step)")
        if self.expected_creates is None and self.list_selector:
            self.expected_creates = 1
        return self

    def submit_index(self) -> int | None:
        for i in range(len(self.steps) - 1, -1, -1):
            if self.steps[i].kind in ("click", "press"):
                return i
        return None

    def prefix_indexes(self) -> list[int]:
        submit = self.submit_index()
        return list(range(submit if submit is not None else len(self.steps)))

    def has_kind(self, kind: str) -> bool:
        return any(s.kind == kind for s in self.steps)


class Meta(_Strict):
    approved_by: str | None = None
    approved_at: str | None = None


class AuthConfig(_Strict):
    login_flow: str


class DuplicateDb(_Strict):
    url: str
    tables: dict[str, str] = Field(default_factory=dict)


class Contract(_Strict):
    version: Literal[1]
    meta: Meta = Field(default_factory=Meta)
    app: AppConfig
    auth: AuthConfig | None = None
    fixtures: dict[str, str] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    flows: list[Flow] = Field(min_length=1)
    attacks_enabled: list[AttackId] = Field(default_factory=lambda: list(ATTACK_IDS))  # type: ignore[arg-type]
    duplicate_db: DuplicateDb | None = None

    @model_validator(mode="after")
    def _check(self) -> Contract:
        ids = [f.id for f in self.flows]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate flow ids: {sorted(dupes)}")
        if any(f.requires_login for f in self.flows):
            if self.auth is None:
                raise ValueError("flows with requires_login need auth.login_flow")
            if self.auth.login_flow not in ids:
                raise ValueError(f"auth.login_flow '{self.auth.login_flow}' is not a flow id")
        known = BUILTIN_PLACEHOLDERS | set(self.fixtures)
        for flow in self.flows:
            for step in flow.steps:
                unknown = step.placeholders() - known
                if unknown:
                    raise ValueError(f"flow '{flow.id}': unknown placeholders {sorted(unknown)}")
        if not any(f.attack for f in self.flows):
            raise ValueError("at least one flow must have attack: true")
        return self

    @property
    def approved(self) -> bool:
        return bool(self.meta.approved_by and self.meta.approved_at)

    def flow(self, flow_id: str) -> Flow:
        for f in self.flows:
            if f.id == flow_id:
                return f
        raise KeyError(flow_id)

    def attacked_flows(self) -> list[Flow]:
        return [f for f in self.flows if f.attack]

    def action_timeout_s(self, flow: Flow) -> float:
        return flow.action_timeout_s or self.defaults.action_timeout_s
