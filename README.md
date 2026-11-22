# Sentri

Sentri attacks your running web app like a hostile or careless user, so it breaks in testing instead of in a live demo.

**Core rules:** the AI proposes attacks; plain code decides pass or fail. And Sentri tests as a **black box**: it
learns about your app only by using it, never from its source, README, database or logs.

- Runs your app in a throwaway sandbox (Docker container, or a plain local process).
- **Recon:** explores the app first (every reachable page, form and link, logged out and logged in, plus the
  API from its OpenAPI spec when there is one) and maps which pages and endpoints need login.
- **Strategy:** a model reads that map and plans attacks specific to *this* app: where data, money and
  permissions move, which flows look fragile. Hypotheses that do not point at real pages or endpoints are
  dropped.
- **Agentic exploration:** the model drives a real browser and raw HTTP requests step by step (huge or odd
  inputs, logged-out and other-user access, changed ids, wrong methods, back/refresh/two tabs, replays and
  races, bursts, big uploads, slow network) and decides each next move from what happened.
- **Plain-code verdicts:** every suspected break is replayed 3 times from a restarted app (or, for an app you
  started yourself, on its live state), from the very first step of the session, and judged by eleven
  deterministic checks: HTTP 5xx, stack traces the app sends back, browser
  errors, duplicates, hangs, logged-out access to protected data, one user seeing another's data (in pages or
  by opening a record by its id), input written back into a page unescaped (XSS), a state change accepted
  without its CSRF token, duplicates from replays or races, and state corruption.
- **Code-owned checks:** Sentri itself marks the content each test user writes, finds the records they create,
  and opens them as the other user and logged out. These checks do not depend on the model.
- **Baseline suite:** six fixed attacks (empty input, double submit, expired session, slow network, bad file,
  rapid repeat) give a readiness score that stays comparable from commit to commit.
- Keeps a bug ledger across commits, re-checks every confirmed finding on later runs, flags regressions, and
  reports the attack tree: the actual path the agent took through the app.
- Optionally proposes fixes for crash bugs (it needs a stack trace) as draft pull requests that must pass a
  fixed gate first. Agentic findings are fixed by hand; the next run re-checks them.

## Why not ZAP, Burp, Playwright fuzzing or Nuclei?

They are good tools; Sentri answers a different question.

| Tool | What it is best at | Where Sentri differs |
|---|---|---|
| OWASP ZAP, Burp Suite | Proxy-based scanning and expert manual testing of known vulnerability classes (injection, headers, TLS, ...) | Sentri drives the app as users would, logged out and as two separate accounts, through multi-step flows, and asks "does it break or leak": crashes, duplicates, races, state corruption, one user reading another's records. It does not replace a security scan. |
| Playwright tests or fuzzing scripts | Exact, repeatable checks you write and maintain | Sentri writes the plan itself from recon, explores beyond the paths you scripted, and turns every confirmed break into a replayable repro and a ledger entry that is re-checked on every later commit. |
| Nuclei | Fast template matching for known CVEs and misconfigurations across many hosts | Sentri has no CVE templates; it tests your app's own logic and flows in a sandbox, one app at a time. |

Use Sentri next to them: a scanner for known vulnerability classes, Sentri for "what happens when real (and
hostile) users get their hands on this build".

## Documentation

Full documentation is in [`documentation/`](documentation/README.md): [getting started](documentation/getting-started.md),
[how Sentri works](documentation/how-it-works.md), [the contract](documentation/contract.md),
[models](documentation/models.md), [verdicts and the bug ledger](documentation/verdicts-and-ledger.md),
[command line](documentation/cli.md), [local web app and API](documentation/web-app.md), [CI](documentation/ci.md),
[fixer](documentation/fixer.md), [development](documentation/development.md) and [limitations](documentation/limitations.md).

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
sentri run --contract examples/buggy-app/approved.yaml --llm-provider claude_code --out report.md
#   or --ollama qwen2.5:7b, --llm-provider anthropic, ... (see "Choosing a model")
```

**No model = no agent, baseline suite only.** Without `--llm-provider` (or `--ollama`) Sentri skips recon,
strategy and exploration and runs only the six fixed attacks.

What a real run finds, how long it takes and what it costs: [example report](documentation/example-report.md).

On your own app:

```bash
# 1. Propose a contract: the flows your demo must survive
sentri plan --app-dir . --run "npm start" --port 3000

# 2. Review contract.draft.yaml, fix selectors, then approve it (a human decision)
sentri contract approve contract.draft.yaml --as "you@example.com"

# 3. Attack (pick your model; without one, only the baseline suite runs)
sentri run --contract contract.yaml --llm-provider auto --out report.md
```

## Web app (local)

`sentri ui` starts a local server and opens it in your browser: choose a model, draft and approve a contract,
start a run and follow it live, read each finding with its replay evidence, compare runs across commits and
mark a bug intended (with a reason). The same API is listed at `/docs` on the server for scripts. Everything
stays on your machine. The server answers only on localhost and refuses actions sent by other websites.

## Choosing a model

**You bring the model, or none.** Sentri ships no API key and uses none by default. With no model there is
no agent: Sentri runs the baseline suite with built-in payloads. With a model, the model writes the attack
strategy for your app, drives the exploration step by step, picks attack payloads, proposes flows and drafts
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

Claude models default to `claude-sonnet-5-5` (Sonnet 5.5) for every role. The Claude Code provider starts one
`claude` process per model call, so exploration takes seconds per step; an API provider is faster. Small local
models work for payloads but are untested for driving exploration. Other CLIs use their own default model unless you
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

## API apps and single-page apps

For apps whose interface is drawn on a canvas (Flutter web) or that are mostly an API, give the contract an API
login and let recon read the OpenAPI spec:

```yaml
version: 1
app:
  run: "{python} -m uvicorn main:app --port 8765"
  port: 8765
  env: {DATABASE_FILE: "sentri.db"}
  clean: ["sentri.db"]            # deleted before every start: a clean state for each replay
auth:
  api:
    register: {path: "/auth/register", json: {username: "{username}", password: "{password}"}}
    token_field: "token"          # where the token is in the response
    header: "Authorization"
    scheme: "Bearer"
```

Each identity (`a`, `b`) gets its own fresh account and token, so Sentri can check whether one user can read or
change another's data. Agentic mode needs a model; without one, only the baseline suite runs. Exploration
budgets: `SENTRI_EXPLORE_STEPS` (default 60) and `SENTRI_EXPLORE_MINUTES` (default 10).

## Verdicts and score

Each check (flow x attack, or agentic finding) runs 3 times, each from a restarted app when Sentri starts it:

| Verdict | Rule |
|---|---|
| fail | an oracle fires in 3 of 3 runs |
| flaky | an oracle fires in 1 or 2 of 3 runs |
| pass | no oracle fires |
| N/A | the attack does not apply to the flow |

```
baseline = 100 x (passed + 0.5 x flaky) / applicable  -  5 per regressed bug
score    = baseline  -  agentic impact                                        (never below 0)
```

The agentic impact is the sum over agentic findings that replay **confirmed**, by the worst check they
tripped: data exposure (logged-out access, cross-user exposure, XSS) 15, state corruption (duplicates, CSRF,
state corruption) 10, crash (5xx, exception, hang) 5, minor (console error) 2; half for a flaky finding.
Unconfirmed candidates never count. The report shows both parts: "Baseline suite: 75 · Agentic impact: -15".
With no applicable baseline check there is **no number**: the report says "insufficient coverage", lists what was
explored and found, and never implies the app is safe.

A pass means "survived these attacks", nothing more. Sentri does not check business logic, such as a wrong total.

## Commands

| Command | What it does |
|---|---|
| `sentri setup` / `sentri doctor` | Install Chromium / check this machine |
| `sentri models` | List the model sources on this machine (agent CLIs, local servers, key variables) |
| `sentri plan` | Crawl the app and propose a draft contract |
| `sentri contract check FILE` / `approve FILE --as NAME` | Validate / approve a contract |
| `sentri run` | Agentic run (recon, strategy, exploration, confirmation, then the baseline suite); `--mode baseline` runs only the six fixed attacks; `--serve` also starts the dashboard API; `--fresh` disables reuse |
| `sentri report` | Rebuild `report.md` from the ledger (`--efficiency` shows time and cost per run) |
| `sentri bug list` / `bug mark BUG-001 intended --note "..."` | Inspect bugs / record a human decision |
| `sentri ui` | Start the local server and open the web app in the browser |
| `sentri serve` | Same server without opening a browser, for scripts (REST + live event stream at http://127.0.0.1:8787) |
| `sentri fix BUG-001` | Optional: propose a patch for a baseline crash bug (needs a stack trace), run the gate, open a draft PR |

Exit codes: 0 finished, 1 gate (`--fail-under`, `--fail-on-regression`), 2 contract or config error, 3 app failed to start, 4 infrastructure missing.

## License

MIT, see [LICENSE](LICENSE).

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

- **Only run Sentri on apps you own or have permission to test.** It behaves like a hostile user on purpose.
- Only the sandboxed copy of your app is attacked, never a live system.
- Every browser request is fenced to the app's own origin: links, form posts, popups, scripts and redirects
  (including the app's own open redirects) to other sites are blocked, and test-account tokens are only ever
  sent to the app.
- Page text, logs and READMEs reach the model only as fenced, untrusted APP DATA it is told never to obey; and
  whatever it answers is validated and judged by plain code anyway.
- A human approves the contract, decides whether a failure is intended, and merges every fix.
- API keys come from environment variables and are scrubbed from the ledger, evidence files and prompts.
