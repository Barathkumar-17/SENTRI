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
sentri contract approve examples/buggy-app/contract.yaml --as "<your name>" --out examples/buggy-app/approved.yaml
sentri run --contract examples/buggy-app/approved.yaml --out report.md
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

## Web app (local)

`sentri ui` starts a local web app for the whole process, as an alternative to the CLI: point at a project
folder, choose a model, review and approve the proposed flows, run, and read the results. Everything stays on
your machine and there is no login. The server answers only on localhost and refuses actions sent by other
websites, so pages open in the same browser cannot drive it. The screens are being built; the API they use
(`/docs` on the server) is complete.

## Choosing a model

**You bring the model, or none.** Sentri ships no API key and uses none by default: with no model it runs
every check with its built-in payloads. The model only picks attack payloads, proposes flows and drafts
fixes; plain code decides every verdict.

`sentri models` lists what this computer has: signed-in coding-agent CLIs, running local model servers and
their models, and which API-key variables are set (names only). `--llm-provider auto` picks a signed-in CLI
first, then a local server. It never picks a paid API key on its own.

| Option | How |
|---|---|
| No model (default) | Nothing to set. |
| Claude Code you are signed in to (no API key) | `--llm-provider claude_code` (found on PATH or inside the VS Code, Cursor or Windsurf extension) |
| OpenAI Codex CLI you are signed in to (no API key) | `--llm-provider codex` |
| Gemini CLI you are signed in to (no API key) | `--llm-provider gemini_cli` |
| Local model with Ollama (no key, nothing leaves your machine) | `ollama pull qwen2.5:7b`, then `--ollama qwen2.5:7b` |
| Other local servers (LM Studio, vLLM, llama.cpp) | `--llm-provider openai_compatible --llm-base-url http://localhost:1234/v1 --model <id>` |
| Anthropic API with your key | `--llm-provider anthropic` with `ANTHROPIC_API_KEY` (read only after you choose this provider) |
| Any OpenAI-compatible API with your key (OpenAI, Gemini, OpenRouter, Groq, Together, Mistral, DeepSeek...) | `--llm-provider openai_compatible --llm-base-url <url> --model <id>` and `SENTRI_LLM_API_KEY=...` |

Claude models default to `claude-sonnet-5-5` for every role. Other CLIs use their own default model unless you
pass `--model`. Each flag has an environment variable: `SENTRI_LLM_PROVIDER`, `SENTRI_LLM_BASE_URL`,
`SENTRI_LLM_API_KEY`, `SENTRI_MODEL_FAST` (`--model`), `SENTRI_MODEL_SMART` (`--model-smart`, used by the
fixer). `sentri doctor` shows which model will be used and whether it is reachable. Paid APIs are capped per
run by `SENTRI_LLM_BUDGET_USD` (default 1.00).

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
| `sentri models` | List the model sources on this machine (agent CLIs, local servers, key variables) |
| `sentri plan` | Crawl the app and propose a draft contract |
| `sentri contract check FILE` / `approve FILE --as NAME` | Validate / approve a contract |
| `sentri run` | Run the attack grid (`--serve` also starts the dashboard API, `--fresh` disables reuse, `--only flow:attack`) |
| `sentri report` | Rebuild `report.md` from the ledger (`--efficiency` shows time and cost per run) |
| `sentri bug list` / `bug mark BUG-001 intended --note "..."` | Inspect bugs / record a human decision |
| `sentri ui` | Start the local web app and open the browser (full workflow; the screens are in progress, the API behind them works today) |
| `sentri serve` | Same server without opening a browser, for scripts (REST + live event stream at http://127.0.0.1:8787) |
| `sentri fix BUG-001` | Optional: propose a patch, run the gate, open a draft PR |

Exit codes: 0 finished, 1 gate (`--fail-under`, `--fail-on-regression`), 2 contract or config error, 3 app failed to start, 4 infrastructure missing.

## Development

```bash
pip install -e ".[dev]" && sentri setup
pytest                         # fast unit tests
python scripts/smoke_test.py   # end-to-end check of the CLI on this machine
pytest -m integration          # full attack grid against the planted-bug app
```

## CI (GitHub Actions)

Sentri runs as an action in **your** repository, on **your** runner, with **your** model settings. Copy
[`examples/ci/sentri.yml`](examples/ci/sentri.yml) to `.github/workflows/sentri.yml` and pick one option:

```yaml
- uses: Barathkumar-17/SENTRI@main
  with:
    contract: contract.yaml
    # no model:            leave the llm inputs out (built-in payloads)
    # your Anthropic key:  llm-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    # any OpenAI-compatible API:
    #   llm-base-url: https://...   llm-api-key: ${{ secrets.MY_KEY }}   model: <id>
    # a local model pulled onto the runner (no key):
    #   ollama-model: qwen2.5:7b
    # your own model server (e.g. a self-hosted runner next to Ollama):
    #   llm-base-url: http://my-gpu-box:11434/v1   model: <id>
```

Keys go in your repository's *Settings > Secrets and variables > Actions*; GitHub masks them in logs and
Sentri scrubs them from evidence and reports. A GitHub-hosted runner cannot reach a model on your laptop: use
`ollama-model` (runs the model on the runner) or a self-hosted runner.

Each pull request gets a comment with the readiness score and its change against `main`, newly failing checks,
regressions, and a link to the evidence. It is report-only by default; add `fail-under: "80"` or
`fail-on-regression: "true"` to gate merges.

## Troubleshooting

- `sentri doctor` lists anything missing and how to fix it.
- **Chromium missing:** run `sentri setup` (on Linux CI, `sentri setup --with-deps`).
- **App starts slowly on macOS** (doctor shows "Local DNS: slow"): Python dev servers do a reverse DNS lookup
  of 127.0.0.1 while starting. Make sure `/etc/hosts` maps `127.0.0.1` to `localhost`.
- **Preflight failed:** a flow's happy path did not reach its `success` condition, usually a wrong selector.
  Fix the contract; Sentri never scores a contract that cannot complete its own flows.

## Safety

- Only the sandboxed copy of your app is attacked, never a live system.
- A human approves the contract, decides whether a failure is intended, and merges every fix.
- API keys come from environment variables and are scrubbed from the ledger, evidence files and prompts.
