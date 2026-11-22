# Command line

## Commands

| Command | What it does |
|---|---|
| `sentri setup [--with-deps]` | Install the Chromium build Playwright drives (`--with-deps` also installs Linux system packages) |
| `sentri doctor` | Check Python, Chromium, Docker, git, gh, local DNS and the model settings; exit 4 if a required item is missing |
| `sentri models` | List signed-in agent CLIs, running local model servers and their models, and which key variables are set |
| `sentri plan --app-dir DIR (--run CMD \| --dockerfile FILE) --port N` or `sentri plan --url URL` | Start the app (or use a running one), explore it from outside and draft `contract.draft.yaml` |
| `sentri contract check FILE` | Validate a contract |
| `sentri contract approve FILE --as NAME [--out FILE]` | Record the human approval |
| `sentri run` | Run Sentri (options below) |
| `sentri report [--run ID] [--out FILE] [--efficiency]` | Rebuild the report from the ledger; `--efficiency` shows time and cost per run |
| `sentri bug list [--status S] [--json]` | List bugs |
| `sentri bug mark BUG-ID intended\|open --note "..." [--by NAME]` | A human decision about a bug (a note is required for `intended`) |
| `sentri ui [--port 8787]` | Start the local web app and open the browser |
| `sentri serve [--port 8787] [--host H]` | The same server without opening a browser |
| `sentri fix BUG-ID [--no-pr]` | Propose a patch, run the gate, open a draft pull request if it passes |

## `sentri run` options

| Option | Default | Meaning |
|---|---|---|
| `--contract FILE` | `contract.yaml` | The approved contract |
| `--mode agentic\|baseline` | `agentic` | Agentic: recon, strategy, exploration, confirmation, then the baseline suite. Baseline: the six fixed attacks only |
| `--out FILE` | none | Write `report.md` |
| `--commit SHA`, `--branch NAME` | from git | Recorded with the run |
| `--base-branch NAME` | `main` | Branch the score change is compared with |
| `--only FLOW[:ATTACK]` | all | Limit the baseline suite (repeatable) |
| `--attempts N` | 3 | Attempts per check |
| `--fresh` | off | Do not reuse passing checks |
| `--skip-preflight` | off | Skip the happy-path check of each flow |
| `--allow-unapproved` | off | Run an unapproved contract (for development) |
| `--serve [--port N]` | off | Also start the local server and live view |
| `--fail-under N` | none | Exit 1 if the score is below N |
| `--fail-on-regression` | off | Exit 1 if a fixed bug came back |
| `--no-llm` | off | No model for this run |
| `--llm-provider`, `--llm-base-url`, `--model`, `--model-smart`, `--ollama MODEL` | | See [Models](models.md); also on `plan`, `doctor` and `fix` |
| `--db FILE` | `.sentri/ledger.db` | The ledger |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Finished (whatever the score) |
| 1 | A gate failed: `--fail-under` or `--fail-on-regression` |
| 2 | Contract or configuration problem, or preflight failed |
| 3 | The app did not start |
| 4 | Infrastructure missing (for example Chromium) |
| 130 | Interrupted |

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SENTRI_DB` | `.sentri/ledger.db` | Ledger file |
| `SENTRI_EVIDENCE_DIR` | `evidence` | Evidence folder |
| `SENTRI_EVIDENCE_URL` | none | Link printed in the report (CI artifact URL) |
| `SENTRI_LLM_PROVIDER` | `none` | `auto`, `claude_code`, `codex`, `gemini_cli`, `anthropic`, `openai_compatible`, `none` |
| `SENTRI_LLM_BASE_URL` | none | For `openai_compatible` |
| `SENTRI_LLM_API_KEY` / `LLM_API_KEY` | none | Your key. Sentri reads the environment only, not a `.env` file: load one first (`set -a; . ./.env; set +a` in bash), and keep it out of git |
| `SENTRI_MODEL_FAST` / `SENTRI_MODEL_SMART` | `claude-sonnet-5-5` | Models for most roles / the fixer |
| `SENTRI_OLLAMA_MODEL` | none | Shortcut for a local Ollama model |
| `SENTRI_LLM_BUDGET_USD` | 1.00 | Spending cap per run for paid APIs |
| `SENTRI_LLM_TIMEOUT_S` | 60 | Per model call (CLIs and local models get more) |
| `SENTRI_LLM_MAX_CALLS` / `SENTRI_LLM_MAX_TOKENS` | 0 (no limit) | Model calls / tokens (input + output) per run |
| `SENTRI_EXPLORE_STEPS` / `SENTRI_EXPLORE_MINUTES` | 60 / 10 | Exploration budget |
| `SENTRI_MAX_PROBES_PER_FLOW` | 2 | Follow-up probes in the baseline suite |
| `SENTRI_REGRESSION_PENALTY` | 5 | Score points per regressed bug |
| `SENTRI_BAD_FILE_MB` | 50 | Size of the oversized file |
| `SENTRI_HEADLESS` | 1 | `0` shows the browser |
| `SENTRI_SEED_EMAIL` / `SENTRI_SEED_PASSWORD` | none | For `{seed_email}` / `{seed_password}` |
| `SENTRI_CLAUDE_BIN` / `SENTRI_CODEX_BIN` / `SENTRI_GEMINI_BIN` | found automatically | Path to an agent CLI |
