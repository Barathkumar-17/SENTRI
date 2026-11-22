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

Open `report.md`, or run `sentri bug list`.

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

This starts the local web app and opens it. The screens are being built; the API behind them already covers
the whole workflow (see [Local web app and API](web-app.md)).

## Where results live

| What | Where |
|---|---|
| Bug ledger (runs, checks, bugs, exploration steps, findings) | `.sentri/ledger.db` (SQLite), or `SENTRI_DB` |
| Evidence (requests, console, logs, screenshots per attempt) | `evidence/<run id>/`, or `SENTRI_EVIDENCE_DIR` |
| Report | the file passed to `--out`, or `sentri report` |
