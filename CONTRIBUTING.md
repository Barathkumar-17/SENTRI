# Contributing to Sentri

Thanks for taking the time to help. This guide covers setup, the checks every change must pass, and how
commits and branches are handled. The full developer guide is in
[documentation/development.md](documentation/development.md).

Only run Sentri against apps you own or have permission to test, including while developing it.

## Setup

You need Python 3.12 or newer and git. Docker and Node are optional (see below).

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git && cd SENTRI
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
sentri setup                                        # downloads the Chromium build Playwright drives
sentri doctor                                       # shows anything still missing
```

No model or API key is needed for development. Tests that call a real model are marked `live_llm` and are
skipped by default.

## Checks

Run these before opening a pull request. CI runs the same commands.

```bash
ruff check src tests scripts examples
ruff format --check src tests scripts examples
mypy src                         # strict on the verdict package
pytest                           # fast tests (unit, verdict, trust boundary)
python scripts/smoke_test.py     # the CLI and the web workflow end to end
pytest -m integration            # browser and demo-app tests, including the full baseline grid (minutes)
```

Optional, when you have the infrastructure:

```bash
python scripts/docker_lifecycle_check.py    # needs a running Docker daemon
pytest -m live_llm                          # needs a configured model
```

### The trust boundary

`tests/test_trust_boundary.py` must always pass. It fails if the verdict, ledger or report code imports any
model-related code, or if the replay and confirmation path reaches it through any chain of imports. If your
change needs the model to influence a verdict, it needs a different design.

### The web app

The web app's source is in `frontend/` (React, Vite, TypeScript), and its build is committed to
`src/sentri/dashboard/static/` so users never need Node. If you change the frontend:

```bash
cd frontend
npm install
npm run build      # writes src/sentri/dashboard/static/
```

Commit the rebuilt `static/` folder together with the source change.

## Ground rules

- The model proposes and acts; anything that decides is plain code with tests.
- Every model answer goes through a schema; invalid answers are discarded, never guessed at.
- Nothing hard-coded: no accounts, secrets or API keys in code, examples or tests.
- It must keep working on Windows, macOS and Linux, with Python 3.12+, and with Docker optional.
- Docs describe what the code does today. Do not document planned behaviour as if it exists.

## Commits

- One change per commit, with its tests in the same commit or the next one.
- Plain, imperative subject lines that say what changed, for example
  `Publish the Docker target port on 127.0.0.1 only`.
- Keep the subject under about 72 characters; use the body for the why when it is not obvious.

## Branches and pull requests

1. Branch from `main` (for example `fix/replay-timeout` or `docs/contract-guide`).
2. Make small commits and run the checks above.
3. Open a pull request against `main` describing what changed and how you checked it.
4. A pull request is merged only when the full test suite passes.

## Reporting bugs and security issues

Open a GitHub issue for ordinary bugs, with the command you ran, the Sentri version (`sentri --version`) and
the relevant part of the report. For security issues, follow [SECURITY.md](SECURITY.md) instead.

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE), and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md).
