# Getting started

## 1. Install

Sentri runs on Windows, macOS and Linux with Python 3.12 or newer. Docker is optional.

```bash
pip install git+https://github.com/Barathkumar-17/SENTRI.git
sentri setup        # downloads the Chromium build that Playwright drives
sentri doctor       # checks Python, the browser, Docker, git, your model settings and local DNS
sentri models       # lists the model sources this computer has
```

`sentri doctor` prints what is missing and how to fix it. Only Python and Chromium are required.

## 2. Try it on the demo app

The repository ships a small Flask app with planted bugs, so you can see every part of Sentri work.

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git
cd SENTRI
pip install -e ".[dev]"
sentri setup

# Approving a contract is a human step: you confirm what Sentri may test.
sentri contract approve examples/buggy-app/contract.yaml --as "<your name>" --out examples/buggy-app/approved.yaml

# Baseline suite only (no model needed): about 9 minutes for the full grid
sentri run --contract examples/buggy-app/approved.yaml --mode baseline --out report.md

# Agentic mode with a model you already have, e.g. a signed-in Claude Code:
sentri run --contract examples/buggy-app/approved.yaml --llm-provider claude_code --out report.md
```

**No model = no agent, baseline suite only.** Agentic mode (recon, strategy, exploration, confirmation) runs
only when you pass a model (`--llm-provider ...` or `--ollama MODEL`); otherwise Sentri says so and runs the
six fixed attacks. **Only run Sentri on apps you own or have permission to test.**

Open `report.md`, or run `sentri bug list`.

### Docker + model run on the flow-shop fixture

Reproduces the bounded run recorded in the [README](../README.md#status-and-limitations). It needs Docker with a
running daemon that your user can reach without sudo (`docker info`), the install from step 1 (`pip install -e
".[dev]"`, `sentri setup`) and, for the model step, a signed-in Claude Code (`sentri models` should list it; no API key
is used). Run everything from the repository root.

- **Build context:** `examples/flow-shop/` (the contract's `docker.context` is relative to the contract file). Its
  `Dockerfile` installs Flask and copies the unchanged `app.py`.
- **Bind address:** `app.py` listens on `127.0.0.1`, which cannot be reached through a published container port, so
  the Dockerfile rewrites that one `host=` argument to `0.0.0.0` *inside the image only*. Routes and behaviour are
  unchanged and the tracked file is not edited.
- **Host port:** Sentri publishes the container port with Docker's default, which listens on **all host
  interfaces** (`0.0.0.0:5088`), not only localhost, for as long as the container runs. See
  [limitations](limitations.md). Run it on a machine or network you trust.
- **Test accounts:** nothing is provisioned by hand. The contract's `login` flow signs up fresh random accounts `a`
  and `b` on every start; `auth.start_as: a` begins exploration logged in as `a`. State is in memory, so each
  attempt and replay starts from a fresh container.
- **Contract:** `examples/flow-shop/contract.docker.yaml` is an unapproved template with one neutral goal and no hints
  about the app. Review it, then approve it yourself; the approved copy is git-ignored.

```bash
# 1. No model, no spending: build, start, reset, stop, failed/cancelled starts, leak check (exit 0 = all passed)
python scripts/docker_lifecycle_check.py

# 2. Approve the template (a human decision)
sentri contract approve examples/flow-shop/contract.docker.yaml --as "<your name>" --out examples/flow-shop/approved.docker.yaml

# 3. One bounded model run: at most 60,000 tokens, 25 exploration steps, 15 minutes
SENTRI_LLM_MAX_TOKENS=60000 SENTRI_EXPLORE_STEPS=25 SENTRI_EXPLORE_MINUTES=15 \
  sentri run --contract examples/flow-shop/approved.docker.yaml --llm-provider claude_code --mode agentic --out report.md
```

Results go to `report.md`, the ledger `.sentri/ledger.db` and `evidence/` (all git-ignored; they hold the replayed
requests and responses, so keep them private), and `sentri ui` shows the run. Each candidate is replayed 3 times
from a fresh container before it counts. The contract has no baseline flows, so the report says "insufficient
coverage" and gives no score. The tester model sees only the running app, never the source or the fixture's
expected-failure list. Sentri removes the container when the run ends; check `docker ps -a`.

## 3. Run it on your own app

```bash
cd path/to/your/app

# Recon explores the running app and drafts a contract for you to review.
sentri plan --app-dir . --run "npm start" --port 3000          # or: --dockerfile Dockerfile

# Read contract.draft.yaml, fix anything that looks wrong, then approve it.
sentri contract approve contract.draft.yaml --as "you@example.com" --out contract.yaml

sentri run --contract contract.yaml --llm-provider auto --out report.md
```

`--llm-provider auto` uses a signed-in coding-agent CLI or a running local model server if this computer has
one. It never picks a paid API key on its own. With no model, Sentri runs the baseline suite only.

If your app is mostly an API, or its interface is drawn on a canvas (Flutter web), see
[API apps](contract.md#api-apps-and-token-login).

## 4. Prefer a browser?

```bash
sentri ui
```

This starts the local server and opens the web app in your desktop browser. Its Home page walks through a run
step by step: choose a model, point at your app, approve a contract, start, then read findings and compare runs
(see [Local web app and API](web-app.md)). Everything the web app does, the CLI does too.

## Where results live

| What | Where |
|---|---|
| Bug ledger (runs, checks, bugs, exploration steps, findings) | `.sentri/ledger.db` (SQLite), or `SENTRI_DB` |
| Evidence (requests, console, logs, screenshots per attempt) | `evidence/<run id>/`, or `SENTRI_EVIDENCE_DIR` |
| Report | the file passed to `--out`, or `sentri report` |
