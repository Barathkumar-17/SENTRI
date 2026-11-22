# Development

## Setup

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git && cd SENTRI
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
sentri setup
```

## Checks

```bash
ruff check src tests scripts examples
ruff format --check src tests scripts examples
mypy src                         # strict on the verdict package
pytest                           # fast tests (about 2 seconds)
python scripts/smoke_test.py     # the CLI and the web workflow end to end (about 40 seconds)
pytest -m integration            # browser and demo-app tests, including the full baseline grid (minutes)
pytest -m live_llm               # calls a real model; needs a key
```

`tests/test_trust_boundary.py` fails if verdict, ledger or report code imports model-related code, or if the
confirmation path (`agent/replay.py`, `agent/session.py`, the agentic oracles) reaches it through any chain of
imports.

## The demo app

`examples/buggy-app` is a small Flask app with planted bugs behind `FIX_*` environment flags, one flaky bug, a
clean flow, no built-in accounts and a random secret per start. Besides the baseline bugs it plants three for
the agentic checks: notes any signed-in user can open by id (`FIX_NOTES_IDOR`), a search page that echoes the
query unescaped (`FIX_SEARCH_XSS`) and a settings form whose CSRF token is never verified (`FIX_SETTINGS_CSRF`). `expected_grid.yaml` holds the expected verdict
of every baseline check; the integration test compares a full run against it exactly.

## Repository layout

```
src/sentri/        the package (see "Architecture" in how-it-works.md)
tests/unit         fast tests, no browser
tests/verdict      oracle, engine, signature and score tests
tests/integration  browser and demo-app tests (pytest -m integration)
examples/          demo app, contracts, CI example
scripts/           smoke test
action.yml         the GitHub Action
.github/workflows  CI for this repository (run manually)
```

## Conventions
- The model proposes and acts; anything that decides is plain code with tests.
- Every model answer goes through a schema; invalid answers are discarded, never guessed at.
- Nothing hard-coded: no accounts, secrets or keys in code, examples or tests.
- Must run on Windows, macOS and Linux, Python 3.12+, Docker optional.
- Small commits with plain messages.

## Web app

The web app's source is in `frontend/` (React, Vite, TypeScript). Its build is committed to
`src/sentri/dashboard/static/`, so users never need Node. After changing it:

```
cd frontend
npm install
npm run build      # writes src/sentri/dashboard/static/
npm run dev        # optional: live reload against `sentri serve` on port 8787
```

Commit the rebuilt `static/` folder with the source change. `tests/unit/test_web_app.py` checks that the build
is served and that the app listens for every event kind the backend emits; `tests/integration/test_web_app_ui.py`
drives the built app in a browser against a real server.
