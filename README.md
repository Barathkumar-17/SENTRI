# Sentri

Sentri attacks your running web app like a hostile or careless user, so it breaks in testing instead of in a live demo.

**Core rule:** the AI proposes attacks; plain code decides pass or fail. The AI never grades the result.

- Runs your app in a throwaway sandbox (Docker container, or a plain local process).
- Drives a real browser (Chromium via Playwright) through the journeys your demo must survive.
- Applies six fixed attacks: empty input, double submit, expired session, slow network, bad file, rapid repeat.
- Decides pass, fail or flaky with five deterministic checks: HTTP 5xx, unhandled exception in the logs, browser console error, duplicate records, timeout.
- Keeps a bug ledger across commits, flags regressions, and gives a 0-100 readiness score per commit.
- Optionally proposes fixes as draft pull requests that must pass a fixed gate first.

## Install

Works on Windows, macOS and Linux with Python 3.12 or newer.

```bash
pip install git+https://github.com/Barathkumar-17/SENTRI.git
sentri setup      # downloads the Chromium build Playwright drives
sentri doctor     # checks Python, browser, Docker, git and your LLM settings
```

Docker is optional. Without it, Sentri starts your app as a local command.

## Quick start

Try it on the bundled demo app, which has planted bugs:

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git && cd SENTRI
pip install -e ".[dev]" && sentri setup
sentri run --contract examples/buggy-app/contract.yaml --no-llm --out report.md
```

On your own app:

```bash
# 1. Propose a contract: the flows your demo must survive
sentri plan --app-dir . --run "npm start" --port 3000

# 2. Review contract.draft.yaml, fix selectors, then approve it (a human decision)
sentri contract approve contract.draft.yaml --as "you@example.com"

# 3. Attack
sentri run --contract contract.yaml --out report.md
```

## Choosing a model

The model only picks payloads, proposes flows and drafts fixes. Without one, Sentri still runs every check using built-in payloads.

| Provider | Settings |
|---|---|
| Anthropic Claude (default) | `LLM_API_KEY` or `ANTHROPIC_API_KEY`. Defaults: `claude-sonnet-5-5` for payloads, probes and the planner (`SENTRI_MODEL_FAST`), `claude-opus-5-5` for the fixer (`SENTRI_MODEL_SMART`) |
| Any OpenAI-compatible API (OpenAI, Gemini, OpenRouter, Groq, Together, Mistral, DeepSeek...) | `SENTRI_LLM_PROVIDER=openai_compatible`, `SENTRI_LLM_BASE_URL`, `SENTRI_LLM_API_KEY`, `SENTRI_MODEL_FAST`, `SENTRI_MODEL_SMART` |
| Local models (Ollama, LM Studio, vLLM) | as above, for example `SENTRI_LLM_BASE_URL=http://localhost:11434/v1 SENTRI_MODEL_FAST=qwen3` |
| None | `--no-llm` or `SENTRI_LLM_PROVIDER=none` |

Every command also accepts `--llm-provider`, `--llm-base-url`, `--model` and `--model-smart`. Spending is capped per run by `SENTRI_LLM_BUDGET_USD` (default 1.00).

## The contract

`contract.yaml` lists the flows, how to start the app, and which attacks to run:

```yaml
version: 1
app:
  run: "npm start"            # or: docker: {context: ".", dockerfile: "Dockerfile"}
  port: 3000
flows:
  - id: signup
    description: "User can create an account"
    start: "/signup"
    steps:
      - fill: {selector: "input[name=email]", value: "{email}"}
      - fill: {selector: "input[name=password]", value: "{password}"}
      - click: "button[type=submit]"
    success: {url_contains: "/dashboard"}
    list_selector: "[data-testid=user-row]"   # optional; improves the duplicate check
    list_page: "/admin/users"
attacks_enabled: [empty_input, double_submit, expired_session, slow_network, bad_file, rapid_repeat]
```

`{email}`, `{password}`, `{title}`, `{name}`, `{text}`, `{number}` and `{file}` are filled with fresh test data on each attempt. Pin values in a `fixtures:` section. Flows that need a logged-in user set `requires_login: true` and name a login flow in `auth.login_flow`.

## Verdicts and score

Each check (flow x attack) runs 3 times from a clean app state:

| Verdict | Rule |
|---|---|
| fail | an oracle fires in 3 of 3 runs |
| flaky | an oracle fires in 1 or 2 of 3 runs |
| pass | no oracle fires |
| N/A | the attack does not apply to the flow |

`score = 100 x (passed + 0.5 x flaky) / applicable - 5 per regressed bug` (never below 0).

A pass means "survived these attacks", nothing more. Sentri does not check business logic, such as a wrong total.

## Commands

| Command | What it does |
|---|---|
| `sentri setup` / `sentri doctor` | Install Chromium / check this machine |
| `sentri plan` | Crawl the app and propose a draft contract |
| `sentri contract check FILE` / `approve FILE --as NAME` | Validate / approve a contract |
| `sentri run` | Run the attack grid (`--serve` also starts the dashboard API, `--fresh` disables reuse, `--only flow:attack`) |
| `sentri report` | Rebuild `report.md` from the ledger (`--efficiency` shows time and cost per run) |
| `sentri bug list` / `bug mark BUG-001 intended --note "..."` | Inspect bugs / record a human decision |
| `sentri serve` | Dashboard API over the ledger at http://127.0.0.1:8787 (REST + live event stream; the web UI is coming) |
| `sentri fix BUG-001` | Optional: propose a patch, run the gate, open a draft PR |

Exit codes: 0 finished, 1 gate (`--fail-under`, `--fail-on-regression`), 2 contract or config error, 3 app failed to start, 4 infrastructure missing.

## Development

```bash
pip install -e ".[dev]" && sentri setup
pytest                         # fast unit tests
python scripts/smoke_test.py   # end-to-end check of the CLI on this machine
pytest -m integration          # full attack grid against the planted-bug app
```

## CI

`examples/ci/sentri.yml` is a GitHub Actions workflow that runs Sentri on every pull request, comments the score and its change against `main`, and uploads the evidence.

## Safety

- Only the sandboxed copy of your app is attacked, never a live system.
- A human approves the contract, decides whether a failure is intended, and merges every fix.
- API keys come from environment variables and are scrubbed from the ledger, evidence files and prompts.
