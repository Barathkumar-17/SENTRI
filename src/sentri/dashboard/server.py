"""Dashboard backend: REST + Server-Sent Events over the ledger (docs/reference/dashboard-api.md).

The web app (frontend/, built into sentri/dashboard/static/) is served from `/` and `/assets`; it uses only the
endpoints below. Without a build, `/` serves a placeholder that lists the API.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sentri.config import Settings
from sentri.dashboard.control_routes import ControlState, register
from sentri.ledger.ledger import HumanDecision, Ledger
from sentri.report.agentic import agentic_data
from sentri.verdict.scorer import compute

STATIC_DIR = Path(__file__).parent / "static"

PLACEHOLDER = """<!doctype html><html><head><meta charset="utf-8"><title>Sentri</title></head>
<body style="font-family:system-ui;max-width:720px;margin:40px auto;padding:0 16px">
<h1>Sentri dashboard API</h1>
<p>The dashboard frontend is not built yet (see docs/reference/dashboard-api.md section 4).
The backend is running; these endpoints are available:</p>
<ul>
<li><a href="/api/runs">/api/runs</a></li><li>/api/runs/{id}, /api/runs/{id}/grid</li>
<li>/api/checks/{id}</li><li><a href="/api/bugs">/api/bugs</a>, /api/bugs/{id}/history,
POST /api/bugs/{id}/status</li><li><a href="/api/trend">/api/trend</a></li>
<li>/api/live/screenshot.jpg</li><li>/api/events/stream (SSE)</li>
<li>Control: <a href="/api/providers">/api/providers</a>, <a href="/api/models/local">/api/models/local</a>,
/api/models/pull, /api/models/test, /api/settings/model, /api/projects/inspect, /api/plan, /api/contracts/*,
/api/runs (POST), <a href="/api/jobs">/api/jobs</a></li>
<li><a href="/docs">/docs</a> (OpenAPI)</li>
</ul></body></html>"""


class StatusChange(BaseModel):
    status: Literal["intended", "open"]
    note: str = ""
    by: str = "dashboard"


def _row(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("payload", "breakdown", "usage", "coverage"):
        if isinstance(out.get(key), str):
            with contextlib.suppress(json.JSONDecodeError):
                out[key] = json.loads(out[key])
    return out


LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(
    db_path: Path,
    evidence_dir: Path,
    live_path: Path,
    poll_s: float = 0.5,
    *,
    settings: Settings | None = None,
    extra_hosts: tuple[str, ...] = (),
) -> FastAPI:
    """Local app: no login. Three checks keep other websites in the same browser from driving it:
    - Host must be local (blocks DNS rebinding);
    - a state-changing request that carries an Origin header must come from this server's own origin
      (browsers always send Origin on cross-site POST/PUT/DELETE; local scripts and curl send none);
    - state-changing requests must be JSON (a plain HTML form on another site cannot send that).
    Sentri is a local tool: this server has no accounts and never will."""
    app = FastAPI(title="Sentri", version="0.1.0")
    allowed_hosts = LOCAL_HOSTS | set(extra_hosts)

    @app.middleware("http")
    async def guard(request: Request, call_next: Any) -> Response:
        host_header = (request.headers.get("host") or "").lower()
        host = host_header.rsplit(":", 1)[0] if not host_header.startswith("[") else host_header.split("]")[0] + "]"
        if host not in allowed_hosts:
            return Response("Sentri only answers on localhost", status_code=403)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None:
                parsed = urlsplit(origin)
                same = parsed.netloc.lower() == host_header and parsed.scheme in ("http", "https")
                if not same:
                    return Response("cross-site request refused", status_code=403)
            ctype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
            has_body = request.headers.get("content-length") not in (None, "0")
            if has_body and ctype != "application/json":
                return Response("send JSON (Content-Type: application/json)", status_code=415)
        response: Response = await call_next(request)
        return response

    if settings is not None:
        state = ControlState(settings)
        state.load_prefs()
        register(app, state)

    def ledger() -> Ledger:
        # One connection per request: WAL lets the dashboard read while a run writes.
        return Ledger(db_path)

    @app.get("/", response_class=HTMLResponse)
    def index() -> Any:
        page = STATIC_DIR / "index.html"
        if page.exists():  # FRONTEND: served once built
            return FileResponse(page)
        return HTMLResponse(PLACEHOLDER)

    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon() -> Response:
        icon = STATIC_DIR / "favicon.svg"
        return FileResponse(icon) if icon.exists() else Response(status_code=404)

    @app.get("/api/findings")
    def findings(verdict: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            return lg.findings(verdict, limit)
        finally:
            lg.close()

    @app.get("/api/findings/{finding_id}")
    def finding(finding_id: int) -> dict[str, Any]:
        """One finding with its repro steps, linked bugs and per-attempt replay evidence."""
        lg = ledger()
        try:
            row = lg.finding(finding_id)
            if row is None:
                raise HTTPException(404, "finding not found")
            row["bugs"] = [_row(b) for b in lg.bugs_for_check(row["check_id"])] if row.get("check_id") else []
            attempts: list[dict[str, Any]] = []
            for i, path in enumerate(row.get("evidence") or [], start=1):
                target = Path(path)
                if not target.is_file():
                    attempts.append({"attempt": i, "missing": True})
                    continue
                data = json.loads(target.read_text(encoding="utf-8"))
                attempts.append(
                    {
                        "attempt": i,
                        "completed": data.get("completed"),
                        "notes": data.get("notes", []),
                        "oracles": [o for o in data.get("oracles", []) if o.get("fired")],
                        "responses": data.get("responses", [])[:60],
                        "probes": data.get("probes", []),
                        "health": data.get("health", []),
                        "server_log_tail": data.get("logs", [])[-40:],  # shown to the developer; no verdict reads it
                    }
                )
            row["attempts"] = attempts
            return row
        finally:
            lg.close()

    @app.get("/api/runs")
    def runs(limit: int = 50) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            return [_row(r) for r in lg.runs(limit)]  # type: ignore[misc]
        finally:
            lg.close()

    @app.get("/api/runs/{run_id}")
    def run(run_id: int) -> dict[str, Any]:
        lg = ledger()
        try:
            row = _row(lg.run(run_id))
            if row is None:
                raise HTTPException(404, "run not found")
            if row.get("breakdown") is None:
                checks = lg.checks_for_run(run_id, include_probes=False)
                row["breakdown"] = compute([c["score_verdict"] for c in checks], 0).__dict__
            return row
        finally:
            lg.close()

    @app.get("/api/runs/{run_id}/grid")
    def grid(run_id: int) -> dict[str, Any]:
        lg = ledger()
        try:
            if lg.run(run_id) is None:
                raise HTTPException(404, "run not found")
            flows: list[str] = []
            attacks: list[str] = []
            cells = []
            for c in lg.checks_for_run(run_id, include_probes=False):
                if c["flow_id"] not in flows:
                    flows.append(c["flow_id"])
                if c["attack_id"] not in attacks:
                    attacks.append(c["attack_id"])
                cells.append(
                    {
                        "check_id": c["id"],
                        "flow_id": c["flow_id"],
                        "attack_id": c["attack_id"],
                        "verdict": c["verdict"],
                        "score_verdict": c["score_verdict"],
                        "oracle_hits": c["oracle_hits"],
                        "bug_ids": [b["id"] for b in lg.bugs_for_check(c["id"])],
                        "reused": c["reused_from_check_id"] is not None,
                    }
                )
            probes = [_row(c) for c in lg.checks_for_run(run_id) if c["is_probe"]]
            return {"flows": flows, "attacks": attacks, "cells": cells, "probes": probes}
        finally:
            lg.close()

    @app.get("/api/runs/{run_id}/agentic")
    def agentic(run_id: int) -> dict[str, Any]:
        """Recon map, strategy, findings and the attack tree of an agentic run (FRONTEND: tree view)."""
        lg = ledger()
        try:
            if lg.run(run_id) is None:
                raise HTTPException(404, "run not found")
            data = agentic_data(lg, run_id)
            return data or {"app_map": None, "strategy": None, "findings": [], "steps": [], "tree": []}
        finally:
            lg.close()

    @app.get("/api/checks/{check_id}")
    def check(check_id: int) -> dict[str, Any]:
        lg = ledger()
        try:
            row = _row(lg.check(check_id))
            if row is None:
                raise HTTPException(404, "check not found")
            row["bugs"] = [_row(b) for b in lg.bugs_for_check(check_id)]
            shot = row.get("screenshot_path")
            row["screenshot_url"] = (
                f"/evidence/{Path(shot).relative_to(evidence_dir).as_posix()}"
                if shot and Path(shot).is_relative_to(evidence_dir)
                else None
            )
            ev_path = row.get("evidence_path")
            if ev_path and Path(ev_path).exists():
                data = json.loads(Path(ev_path).read_text(encoding="utf-8"))
                ev = data.get("evidence", {})
                row["evidence_summary"] = {
                    "http": ev.get("http", [])[:100],
                    "console": ev.get("console", [])[:50],
                    "log_tail": [line["text"] for line in ev.get("logs", [])][-60:],
                    "actions": ev.get("actions", []),
                    "oracles": data.get("oracles", []),
                    "list_counts": [ev.get("list_count_before"), ev.get("list_count_after")],
                }
            return row
        finally:
            lg.close()

    @app.get("/api/bugs")
    def bugs(status: str | None = None) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            return [_row(b) for b in lg.bugs(status)]  # type: ignore[misc]
        finally:
            lg.close()

    @app.get("/api/bugs/{bug_id}/history")
    def bug_history(bug_id: str) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            if lg.bug(bug_id) is None:
                raise HTTPException(404, "bug not found")
            return [_row(h) for h in lg.bug_history(bug_id)]  # type: ignore[misc]
        finally:
            lg.close()

    @app.post("/api/bugs/{bug_id}/status")
    def set_status(bug_id: str, body: StatusChange) -> dict[str, Any]:
        lg = ledger()
        try:
            if lg.bug(bug_id) is None:
                raise HTTPException(404, "bug not found")
            try:
                decision = HumanDecision(bug_id, body.status, body.note, body.by)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            lg.mark(decision)
            return _row(lg.bug(bug_id)) or {}
        finally:
            lg.close()

    @app.get("/api/trend")
    def trend(branch: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            return [_row(r) for r in lg.trend(branch, limit)]  # type: ignore[misc]
        finally:
            lg.close()

    @app.get("/api/live/screenshot.jpg")
    def live() -> Response:
        if not live_path.exists():
            return Response(status_code=204)
        return Response(live_path.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/evidence/{path:path}")
    def evidence(path: str) -> FileResponse:
        target = (evidence_dir / path).resolve()
        if not target.is_relative_to(evidence_dir.resolve()) or not target.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(target)

    @app.get("/api/events/stream")
    async def stream(request: Request, run_id: int | None = None, after: int = 0) -> StreamingResponse:
        last = request.headers.get("last-event-id")
        start = int(last) if last and last.isdigit() else after

        async def gen() -> AsyncIterator[str]:
            cursor = start
            lg = ledger()
            try:
                while not await request.is_disconnected():
                    rows = lg.events_after(cursor, run_id)
                    for r in rows:
                        cursor = r["id"]
                        payload = {
                            "run_id": r["run_id"],
                            "ts": r["ts"],
                            "message": r["message"],
                            "reason": r["reason"],
                            "data": json.loads(r["data"] or "{}"),
                        }
                        yield f"id: {r['id']}\nevent: {r['kind']}\ndata: {json.dumps(payload)}\n\n"
                    if not rows:
                        yield ": keepalive\n\n"
                        await asyncio.sleep(poll_s)
            finally:
                lg.close()

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    def events(run_id: int | None = None, after: int = 0, limit: int = 500) -> list[dict[str, Any]]:
        lg = ledger()
        try:
            out = []
            for r in lg.events_after(after, run_id, limit):
                d = _row(r) or {}
                d["data"] = json.loads(r["data"] or "{}")
                out.append(d)
            return out
        finally:
            lg.close()

    return app
