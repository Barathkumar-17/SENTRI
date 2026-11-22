# Local web app and API

`sentri ui` starts a local server at `http://127.0.0.1:8787` and opens it. **The web screens are in progress**;
for now, use the CLI. What the server provides today is the API the screens will use, so a script can already
call each step below; the full list is at `http://127.0.0.1:8787/docs`.

## The API, step by step

| Step | Endpoint |
|---|---|
| Model choices | `GET /api/providers`, `GET /api/models/available` (agent CLIs, local servers, key variables by name), `GET /api/models/local` |
| Pull a local model | `POST /api/models/pull {model}` (Server-Sent Events progress from Ollama) |
| Test a model | `POST /api/models/test` |
| Choose the model for this session | `GET`/`PUT /api/settings/model` (a key is kept in memory only and never sent back) |
| Point at a project | `POST /api/projects/inspect {path}`: suggested start command and port, README, existing contracts |
| Draft a contract | `POST /api/plan {path, run or dockerfile, port}`: a background job; its result holds the draft |
| Validate, save, approve | `POST /api/contracts/validate`, `POST /api/contracts/save {path, yaml, approve_as}` |
| Run | `POST /api/runs {contract, mode, only, attempts, ...}`: a background job, one run at a time |
| Follow a job | `GET /api/jobs/{id}`, `POST /api/jobs/{id}/cancel` |
| Live progress | `GET /api/events/stream?run_id=...` (Server-Sent Events), `GET /api/live/screenshot.jpg` |
| Results | `GET /api/runs`, `/api/runs/{id}`, `/api/runs/{id}/grid`, `/api/runs/{id}/agentic` (findings and attack tree), `/api/checks/{id}`, `/api/bugs`, `/api/trend` |
| Human decision | `POST /api/bugs/{id}/status {status, note, by}` |

## Safety without a login

The local app has no login. It can start your app, which runs a command, so it must not be drivable by other
websites open in the same browser:
- it answers only requests addressed to localhost (this blocks DNS rebinding);
- a state-changing request that carries an `Origin` header must come from Sentri's own page (browsers always
  send it on cross-site requests; scripts and `curl` on your machine send none);
- state-changing requests must be JSON, which a plain HTML form on another site cannot send.

`sentri serve --host 0.0.0.0` binds beyond localhost; only do that on a network you trust.
