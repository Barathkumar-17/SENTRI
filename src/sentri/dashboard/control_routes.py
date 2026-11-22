"""Control API for the local web UI: choose a model, point at a project, plan a contract, start a run.

FRONTEND: the React app (not built yet) drives the whole local workflow through these endpoints:
  1. GET  /api/providers, /api/models/local      -> model picker (installed models, presets, "no model")
  2. POST /api/models/pull (SSE), /api/models/test -> pull a model with progress / check a key or endpoint
  3. PUT  /api/settings/model                    -> the session's model choice (key kept in memory only)
  4. POST /api/projects/inspect                  -> point at a folder, get start-command suggestions
  5. POST /api/plan -> job; POST /api/contracts/save (with approve_as = the human approval)
  6. POST /api/runs -> job; progress via /api/events/stream?run_id=..., live view via /api/live/screenshot.jpg
No login: the local server only accepts actions from its own page or local tools (see server.py).
"""

from __future__ import annotations

import copy
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from sentri.config import Settings
from sentri.contract.loader import ContractError, approve_contract, load_contract, parse_contract
from sentri.control.discovery import discover_all
from sentri.control.jobs import Job, JobConflict, JobManager
from sentri.control.models import PRESETS, ModelSelection, discover_local, pull_ollama, test_selection
from sentri.control.projects import inspect_project

log = logging.getLogger(__name__)


@dataclass
class ControlState:
    settings: Settings
    selection: ModelSelection = field(default_factory=ModelSelection)
    jobs: JobManager = field(default_factory=JobManager)

    @property
    def prefs_path(self) -> Path:
        return self.settings.db_path.parent / "ui-settings.json"

    def load_prefs(self) -> None:
        """Restore the last model choice (never the key)."""
        try:
            data = json.loads(self.prefs_path.read_text(encoding="utf-8"))
            self.selection = ModelSelection.model_validate({**data, "api_key": None})
        except (OSError, ValueError):
            pass

    def save_prefs(self) -> None:
        self.prefs_path.parent.mkdir(parents=True, exist_ok=True)
        self.prefs_path.write_text(json.dumps(self.selection.model_dump(exclude={"api_key"}), indent=1), "utf-8")

    def settings_for_job(self, selection: ModelSelection | None = None) -> tuple[Settings, bool]:
        """A copy of the base settings with the user's model applied. Returns (settings, model enabled)."""
        chosen = selection or self.selection
        s = copy.deepcopy(self.settings)
        if chosen.preset == "none":
            s.llm_provider = "none"
            return s, False
        chosen.apply(s)
        return s, True


class PullRequest(BaseModel):
    model: str = Field(min_length=1, max_length=200)


class InspectRequest(BaseModel):
    path: str


class PlanRequest(BaseModel):
    path: str
    run: str | None = None
    dockerfile: str | None = None
    port: int = Field(ge=1, le=65535)
    readme: str | None = None
    model: ModelSelection | None = None


class ValidateRequest(BaseModel):
    yaml: str


class SaveContractRequest(BaseModel):
    path: str
    yaml: str
    approve_as: str | None = None  # a human's name: saving with it IS the approval


class RunRequest(BaseModel):
    contract: str
    fresh: bool = False
    only: list[str] = Field(default_factory=list)
    attempts: int = Field(default=3, ge=1, le=10)
    skip_preflight: bool = False
    commit: str | None = None
    branch: str | None = None
    model: ModelSelection | None = None


def _sse(events: AsyncIterator[dict[str, Any]]) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        async for event in events:
            yield f"event: {event.get('status', 'progress')}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


def register(app: FastAPI, state: ControlState) -> None:
    @app.get("/api/providers")
    def providers() -> list[dict[str, Any]]:
        return [p.model_dump() for p in PRESETS]

    @app.get("/api/models/available")
    async def available() -> dict[str, Any]:
        """Everything usable on this machine: agent CLIs, local servers, which key variables are set."""
        return await discover_all()

    @app.get("/api/models/local")
    async def local_models() -> list[dict[str, Any]]:
        return await discover_local()

    @app.post("/api/models/pull")
    def pull(body: PullRequest) -> StreamingResponse:
        return _sse(pull_ollama(body.model))

    @app.post("/api/models/test")
    async def test(body: ModelSelection) -> dict[str, Any]:
        return await test_selection(body)

    @app.get("/api/settings/model")
    def get_model() -> dict[str, Any]:
        return state.selection.public()

    @app.put("/api/settings/model")
    def put_model(body: ModelSelection) -> dict[str, Any]:
        if body.preset != "none":
            try:
                body.apply(copy.deepcopy(state.settings))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        if body.api_key is None and body.preset == state.selection.preset:
            body.api_key = state.selection.api_key  # keep the key already given this session
        state.selection = body
        state.save_prefs()
        return state.selection.public()

    @app.post("/api/projects/inspect")
    def inspect(body: InspectRequest) -> dict[str, Any]:
        try:
            return inspect_project(body.path)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post("/api/contracts/validate")
    def validate(body: ValidateRequest) -> dict[str, Any]:
        try:
            c = parse_contract(yaml.safe_load(body.yaml))
        except ContractError as exc:
            return {"valid": False, "errors": exc.errors}
        except yaml.YAMLError as exc:
            return {"valid": False, "errors": [f"invalid YAML: {exc}"]}
        return {"valid": True, "errors": [], "approved": c.approved, "flows": [f.id for f in c.flows]}

    @app.post("/api/contracts/save")
    def save_contract(body: SaveContractRequest) -> dict[str, Any]:
        path = Path(body.path).expanduser()
        if path.suffix not in (".yaml", ".yml"):
            raise HTTPException(422, "contract files must end in .yaml or .yml")
        if not path.parent.is_dir():
            raise HTTPException(404, f"folder {path.parent} does not exist")
        try:
            parse_contract(yaml.safe_load(body.yaml))
        except (ContractError, yaml.YAMLError) as exc:
            errors = exc.errors if isinstance(exc, ContractError) else [str(exc)]
            raise HTTPException(422, {"errors": errors}) from None
        path.write_text(body.yaml, encoding="utf-8")
        if body.approve_as:
            approve_contract(path, body.approve_as, path)
        c = load_contract(path)
        return {"path": str(path), "approved": c.approved, "approved_by": c.meta.approved_by}

    @app.post("/api/plan")
    async def plan(body: PlanRequest) -> dict[str, Any]:
        root = Path(body.path).expanduser().resolve()
        if not root.is_dir():
            raise HTTPException(404, f"{root} is not a folder")
        if (body.run is None) == (body.dockerfile is None):
            raise HTTPException(422, "give exactly one of run or dockerfile")
        try:
            settings, enabled = state.settings_for_job(body.model)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

        async def work(job: Job) -> dict[str, Any]:
            from sentri.contract.models import AppConfig
            from sentri.llm import make_provider
            from sentri.planner import plan as run_plan
            from sentri.planner import read_readme
            from sentri.runner.browser import BrowserManager
            from sentri.sandbox import make_sandbox

            section: dict[str, Any] = {"port": body.port, "base_url": f"http://127.0.0.1:{body.port}"}
            if body.run:
                section["run"] = body.run
            else:
                section["docker"] = {"context": ".", "dockerfile": body.dockerfile}
            sandbox = make_sandbox(AppConfig.model_validate(section), root)
            provider = make_provider(settings, enabled)
            readme = read_readme(Path(body.readme) if body.readme else root / "README.md")
            await sandbox.start()
            try:
                async with BrowserManager(headless=settings.headless) as browser:
                    result = await run_plan(browser, provider, section, readme)
            finally:
                await sandbox.stop()
            draft = root / "contract.draft.yaml"
            draft.write_text(result.yaml_text, encoding="utf-8")
            return {
                "draft_path": str(draft),
                "yaml": result.yaml_text,
                "source": result.source,
                "unverified": result.unverified,
                "errors": result.errors,
                "llm_cost_usd": provider.spent_usd,
            }

        return state.jobs.start("plan", work).public()

    @app.post("/api/runs")
    async def start_run(body: RunRequest) -> dict[str, Any]:
        path = Path(body.contract).expanduser().resolve()
        try:
            contract = load_contract(path)
        except ContractError as exc:
            raise HTTPException(422, {"errors": exc.errors}) from None
        if not contract.approved:
            raise HTTPException(422, "the contract is not approved yet; save it with approve_as first")
        try:
            settings, enabled = state.settings_for_job(body.model)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        settings.attempts = body.attempts
        settings.live_view = True

        async def work(job: Job) -> dict[str, Any]:
            from sentri.ledger import Ledger
            from sentri.llm import make_provider
            from sentri.runner.fingerprint import git_branch, git_commit
            from sentri.runner.orchestrator import RunOptions, RunOrchestrator

            workdir = path.parent
            options = RunOptions(
                commit=body.commit or git_commit(workdir) or "local",
                branch=body.branch or git_branch(workdir) or "local",
                reuse=not body.fresh,
                only=body.only,
                preflight=not body.skip_preflight,
                exclude_paths=[path],
            )
            ledger = Ledger(settings.db_path)
            try:
                result = await RunOrchestrator(
                    contract,
                    workdir,
                    settings,
                    ledger,
                    make_provider(settings, enabled),
                    options,
                    on_run_created=lambda run_id: setattr(job, "run_id", run_id),
                ).run()
            finally:
                ledger.close()
            if result.status != "finished":
                raise RuntimeError(result.error or "run aborted")
            return {
                "run_id": result.run_id,
                "breakdown": result.breakdown.__dict__ if result.breakdown else None,
                "regressions": result.regressions,
            }

        try:
            return state.jobs.start("run", work).public()
        except JobConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/jobs")
    def jobs() -> list[dict[str, Any]]:
        return [j.public() for j in reversed(list(state.jobs.jobs.values()))]

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str) -> dict[str, Any]:
        j = state.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return j.public()

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str) -> dict[str, Any]:
        j = await state.jobs.cancel(job_id)
        if j is None:
            raise HTTPException(404, "job not found")
        return j.public()
