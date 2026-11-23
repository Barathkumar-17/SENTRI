# Local web app and API

`sentri ui` starts a local server at `http://127.0.0.1:8787` and opens the web app. `sentri serve` starts the
same server without opening a browser. The web app is built into the Python package, so it needs no Node.
It is laid out for desktop browsers; there is no phone layout.

## Screens

- **Home** (the landing page): how to use Sentri from the web app in six steps, with links to each screen,
  and a link to the latest run. The menu is a bar across the top; the current screen is highlighted.
- **Runs**: every run in this machine's ledger, newest first: status, readiness out of 100 with what it was
  built from (for example "3 of 10 applicable checks passed"; *insufficient coverage* when nothing applicable
  was tested, never a pass), confirmed findings, exploration and model use. Readiness counts only what the run
  tested; it is not a safety rating.
- **Run**: the outcome (readiness, findings, exploration, model use), why it stopped (for example a provider
  quota), and tabs for the activity log (live while the run is going), findings, exploration steps with who
  chose each one (the model, a code probe or a fallback), the baseline grid, model use per role and coverage.
- **Findings**: every finding across runs; each opens with what broke (the checks that fired, as recorded) and
  the replay outcome, then where it came from, its three replays (each from a
  restarted app), the steps replayed, per-attempt evidence (what fired, the responses seen, the server log tail
  shown to you only) and its limitations.
- **Ledger**: runs side by side (readiness, agentic impact, checks, exploration, model use) and every bug with
  its status. Marking a bug *intended* needs a reason and is recorded with your name; Sentri never does it.
- **New run**: choose a model, point at your app folder or URL, draft or paste a contract, validate, approve,
  and start the run.

If the server stops, the app says so and reconnects on its own when it is back.

## The API, step by step

Everything the screens do goes through this API, so a script can do the same; the full list is at
`http://127.0.0.1:8787/docs`.

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
| Results | `GET /api/runs`, `/api/runs/{id}`, `/api/runs/{id}/grid`, `/api/runs/{id}/agentic` (findings and attack tree), `/api/findings`, `/api/findings/{id}` (replay evidence per attempt), `/api/checks/{id}`, `/api/bugs`, `/api/trend` |
| Human decision | `POST /api/bugs/{id}/status {status, note, by}` |

## Safety without a login

The local app has no login. It can start your app, which runs a command, so it must not be drivable by other
websites open in the same browser:
- it answers only requests addressed to localhost (this blocks DNS rebinding);
- a state-changing request that carries an `Origin` header must come from Sentri's own page (browsers always
  send it on cross-site requests; scripts and `curl` on your machine send none);
- state-changing requests must be JSON, which a plain HTML form on another site cannot send.

`sentri serve --host 0.0.0.0` binds beyond localhost; only do that on a network you trust.
