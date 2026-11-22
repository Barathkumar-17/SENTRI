from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from sentri.config import Settings
from sentri.control.jobs import JobConflict, JobManager
from sentri.control.models import ModelSelection, discover_local, pull_ollama
from sentri.control.projects import inspect_project
from sentri.dashboard import create_app
from tests.conftest import BUGGY_APP


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.db_path = tmp_path / ".sentri" / "ledger.db"
    s.evidence_dir = tmp_path / "evidence"
    s.live_path = tmp_path / "live.jpg"
    s.api_key = None
    return s


def make_app(settings: Settings):
    return create_app(settings.db_path, settings.evidence_dir, settings.live_path, settings=settings)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(make_app(settings), base_url="http://127.0.0.1")


# ---- local safety without any login -------------------------------------------------------------------
def test_no_login_needed_for_local_use(client: TestClient):
    assert client.get("/api/providers").status_code == 200
    assert client.post("/api/projects/inspect", json={"path": str(BUGGY_APP)}).status_code == 200
    same_origin = {"Origin": "http://127.0.0.1"}
    assert client.put("/api/settings/model", json={"preset": "none"}, headers=same_origin).status_code == 200


def test_foreign_host_is_refused(settings: Settings):
    evil = TestClient(make_app(settings), base_url="http://attacker.example")
    assert evil.get("/api/providers").status_code == 403


def test_cross_site_requests_are_refused(client: TestClient):
    evil = {"Origin": "https://attacker.example"}
    assert client.post("/api/projects/inspect", json={"path": str(BUGGY_APP)}, headers=evil).status_code == 403
    assert client.post("/api/runs", json={"contract": "x.yaml"}, headers=evil).status_code == 403
    other_port = {"Origin": "http://127.0.0.1:9999"}
    assert client.put("/api/settings/model", json={"preset": "none"}, headers=other_port).status_code == 403


def test_actions_must_be_json(client: TestClient):
    form = client.post(
        "/api/projects/inspect",
        content=f"path={BUGGY_APP}",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code == 415
    plain = client.post("/api/runs", content='{"contract": "x.yaml"}', headers={"Content-Type": "text/plain"})
    assert plain.status_code == 415


# ---- model choice -------------------------------------------------------------------------------------
def test_providers_include_no_model_local_and_hosted(client: TestClient):
    ids = {p["id"]: p for p in client.get("/api/providers").json()}
    expected = {"none", "ollama", "lmstudio", "anthropic", "openai", "groq", "openrouter", "gemini", "custom"}
    assert expected <= set(ids)
    assert not ids["ollama"]["needs_key"] and ids["groq"]["needs_key"]


def test_model_settings_never_echo_the_key(client: TestClient, settings: Settings):
    r = client.put("/api/settings/model", json={"preset": "groq", "api_key": "test-key-not-real", "model": "llama-x"})
    assert r.status_code == 200
    body = r.json()
    assert body["has_key"] is True and "api_key" not in body and "test-key-not-real" not in json.dumps(body)
    assert client.get("/api/settings/model").json()["preset"] == "groq"
    prefs = (settings.db_path.parent / "ui-settings.json").read_text()
    assert "test-key-not-real" not in prefs  # the key is kept in memory only


def test_model_settings_validation(client: TestClient):
    assert client.put("/api/settings/model", json={"preset": "groq", "model": "m"}).status_code == 422  # no key
    assert client.put("/api/settings/model", json={"preset": "ollama"}).status_code == 422  # no model
    assert client.put("/api/settings/model", json={"preset": "nope"}).status_code == 422
    assert client.put("/api/settings/model", json={"preset": "ollama", "model": "qwen2.5:7b"}).status_code == 200


def test_selection_apply(settings: Settings):
    settings.llm_base_url = None
    ModelSelection(preset="ollama", model="qwen2.5:7b").apply(settings)
    assert settings.llm_provider == "openai_compatible" and settings.llm_base_url == "http://localhost:11434/v1"
    assert settings.model_fast == settings.model_smart == "qwen2.5:7b" and settings.llm_timeout_s >= 300


def test_test_endpoint_for_no_model(client: TestClient):
    assert client.post("/api/models/test", json={"preset": "none"}).json()["ok"] is True


async def test_discover_local_models():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5:7b", "size": 4_700_000_000}]})
        raise httpx.ConnectError("no lm studio")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        servers = {s["preset"]: s for s in await discover_local(http)}
    assert servers["ollama"]["running"] and servers["ollama"]["models"][0]["id"] == "qwen2.5:7b"
    assert not servers["lmstudio"]["running"] and servers["lmstudio"]["install_hint"]


async def test_pull_streams_progress_and_errors():
    lines = [
        {"status": "pulling manifest"},
        {"status": "downloading", "completed": 5, "total": 10},
        {"status": "success"},
    ]

    def ok(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"model": "qwen2.5:0.5b", "stream": True}
        return httpx.Response(200, content="\n".join(json.dumps(x) for x in lines).encode())

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as http:
        events = [e async for e in pull_ollama("qwen2.5:0.5b", http)]
    assert events[-1]["status"] == "success" and events[1]["completed"] == 5

    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'{"error": "pull model manifest: file does not exist"}\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(missing)) as http:
        events = [e async for e in pull_ollama("nope", http)]
    assert events == [{"status": "error", "error": "pull model manifest: file does not exist"}]


# ---- projects and contracts ---------------------------------------------------------------------------
def test_inspect_buggy_app():
    info = inspect_project(BUGGY_APP)
    kinds = {(s["kind"], s.get("framework")) for s in info["suggestions"]}
    assert ("docker", None) in kinds and ("command", "flask") in kinds
    flask = next(s for s in info["suggestions"] if s.get("framework") == "flask")
    assert flask["port"] == 5055 and flask["run"] == "{python} app.py"
    assert any(p.endswith("contract.yaml") for p in info["contracts"])


def test_inspect_node_project(tmp_path: Path):
    pkg = {"scripts": {"dev": "vite --port 4000"}, "devDependencies": {"vite": "7"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    s = inspect_project(tmp_path)["suggestions"][0]
    assert (s["run"], s["port"], s["framework"], s["needs_install"]) == ("npm run dev", 4000, "vite", True)


def test_inspect_unknown_folder(client: TestClient, tmp_path: Path):
    assert client.post("/api/projects/inspect", json={"path": str(tmp_path / "missing")}).status_code == 404
    assert inspect_project(tmp_path)["notes"]


def test_validate_and_save_with_human_approval(client: TestClient, tmp_path: Path, minimal_contract):
    text = yaml.safe_dump(minimal_contract)
    assert client.post("/api/contracts/validate", json={"yaml": text}).json()["valid"] is True
    bad = client.post("/api/contracts/validate", json={"yaml": "version: 2"}).json()
    assert bad["valid"] is False and bad["errors"]
    target = tmp_path / "contract.yaml"
    saved = client.post("/api/contracts/save", json={"path": str(target), "yaml": text}).json()
    assert saved["approved"] is False
    body = {"path": str(target), "yaml": text, "approve_as": "alice"}
    saved = client.post("/api/contracts/save", json=body).json()
    assert saved["approved"] is True and saved["approved_by"] == "alice"
    wrong = {"path": str(tmp_path / "x.txt"), "yaml": text}
    assert client.post("/api/contracts/save", json=wrong).status_code == 422


def test_run_requires_an_approved_contract(client: TestClient, tmp_path: Path, minimal_contract):
    path = tmp_path / "contract.yaml"
    path.write_text(yaml.safe_dump(minimal_contract))
    r = client.post("/api/runs", json={"contract": str(path)})
    assert r.status_code == 422 and "not approved" in r.text


# ---- jobs ---------------------------------------------------------------------------------------------
async def test_job_manager_lifecycle():
    jobs = JobManager()

    async def ok(job):
        return {"value": 1}

    async def boom(job):
        raise ValueError("bad")

    async def slow(job):
        await asyncio.sleep(10)

    a, b = jobs.start("plan", ok), jobs.start("plan", boom)
    run = jobs.start("run", slow)
    with pytest.raises(JobConflict):
        jobs.start("run", slow)  # one run at a time
    await asyncio.sleep(0.05)
    assert a.status == "finished" and a.result == {"value": 1}
    assert b.status == "failed" and "bad" in (b.error or "")
    await jobs.cancel(run.id)
    assert run.status == "cancelled" and jobs.active_run() is None
