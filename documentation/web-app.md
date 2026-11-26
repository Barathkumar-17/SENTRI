# Local web app and API

`sentri ui` starts a local server at `http://127.0.0.1:8787` and opens the web app. `sentri serve` starts the
same server without opening a browser. The web app is built into the Python package, so it needs no Node.
It is laid out for desktop and laptop browsers, including a window split beside your editor (about 900 px and
wider); there is no phone layout.

## Screens

The menu along the top has Home, Runs, Findings and New run; Getting started is always one click away at the
top right. Every page and filter has its own link, so reload, Back and bookmarks work.

- **Home**: before the first run, a getting-started guide (install, how Sentri reaches your app, keeping
  credentials out of files, first run). After that, a dashboard: the latest run's outcome with its readiness,
  exploration and completion, open bugs that need a decision, recent confirmed findings and recent runs.
- **Getting started**: the same guide, at any time.
- **Runs**: every run, newest first, with readiness out of 100 and what it was built from (*insufficient
  coverage* when nothing applicable was tested, never a pass), confirmed findings, baseline checks,
  exploration and model use. Each row opens the run or its report.
- **Run**: progress (each phase, or live counts while running), why it stopped (for example a token limit or a
  provider quota), the activity log (live while the run is going), the exploration steps with who chose each
  one, and model use. The report is the next step. A run started from the web app can be stopped here (with a
  confirmation); what ran so far is kept and the run is recorded as aborted. A run started with `sentri run`
  is stopped in its terminal with Ctrl+C, and is recorded as aborted the same way.
- **Report** (`#/runs/<id>/report`): one run's analysis from the recorded data, no model involved. What broke
  (findings confirmed by replay, failed and flaky baseline checks with their bugs, counted apart as in `report.md`,
  regressions, candidates that did not reproduce), what was tested (exploration, the model's attack ideas, the baseline grid), coverage limits, and
  suggested next steps. The suggestions come from fixed rules over the record and are labelled as suggestions.
- **Findings**: every finding, filterable by result, run and who found it (the filters live in the link). Each
  opens its own page (`#/findings/<id>`): the outcome (what broke, the three replays, who found it), the replay
  evidence of every attempt (what fired, the responses, the server log tail shown to you only), limitations,
  the technical record (check, steps) and the decision. Fixtures recorded by Sentri's test harness are labelled.
- **Bugs across runs** (in Findings): every bug Sentri tracks with its status. Each opens its own page
  (`#/bugs/<id>`) with every status change across runs and commits, who made it, and the decision. Marking a bug
  *intended* needs a reason and is recorded with your name; Sentri never does it. (This replaces the former
  Ledger screen; old `#/ledger` links land here.)
- **New run**: choose a model, point at your app folder or URL, draft or paste a contract, validate, approve,
  and start the run.

If the server stops, the app says so and reconnects on its own when it is back; a live activity log picks up
where it left off, without losing or repeating events. The live picture of the browser Sentri drives is shown
only for runs this server started.

## The API, step by step

Everything the screens do goes through this API, so a script can do the same; the full list is at
`http://127.0.0.1:8787/docs`.

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

## What the web app never shows

API keys (from environment variables, CI secrets or typed into the web app) and provider account ids (organization and project ids that providers quote in error messages) are scrubbed from everything Sentri stores or shows. Every answer from the server is scrubbed on the way out (JSON, the live event stream, text evidence
files and error messages), so a ledger written before this protection existed shows nothing secret either. A key
you type into the web app is kept in memory for the session only and never sent back. The tester's black-box
rule holds here too: inspecting a project folder returns file paths and start-command suggestions, never file
contents, and the web app has no route to the source-aware fixer.
