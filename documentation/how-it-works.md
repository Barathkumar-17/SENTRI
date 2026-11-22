# How Sentri works

## The core rule

**The AI proposes and acts. Plain code decides.** The model plans attacks, chooses inputs and drives the
browser. It never decides whether something is a bug. Every verdict comes from deterministic code reading
measured evidence, and a test in the repository fails if verdict code ever imports anything model-related.

## A run, phase by phase

`sentri run` (agentic mode, the default) goes through these phases:

### 0. Sandbox and preflight
The app starts in a throwaway sandbox: a Docker container you describe, or a local command. Sentri waits for
the port and a health check, and tells the app its port through `PORT`. Files listed in `app.clean` (for
example a SQLite database) are deleted before every start, so each attempt begins from the same state. Every
browser flow in the contract is run once unattacked; if a flow cannot complete, the run stops, because a
broken selector must never look like an app bug.

### 1. Recon
An agent session explores the app before attacking it:
- crawls every reachable page as a logged-out visitor, then as a signed-in user, and records forms, inputs,
  buttons, lists and links (logout and delete links are not followed);
- submits each form once with ordinary generated values, so the API calls behind buttons show up;
- reads an OpenAPI or Swagger spec if the app publishes one (`/openapi.json`, `/swagger.json`, ...): every
  endpoint with its method, body fields, constraints and declared auth;
- finds the **auth boundary by behaviour**: a page or endpoint is login-only when a logged-out request is
  redirected or refused while a signed-in request succeeds.

The result is an **app map**, stored in the ledger. `sentri plan` uses the same recon to draft the contract.

### 2. Strategy
The model reads the app map and writes a strategy for *this* app: what kind of app it is, where money,
personal data or permissions move, which flows look fragile, and 5-12 prioritized hypotheses such as
"user b can read user a's expenses by changing the id". Plain code **drops any hypothesis whose targets are not
real pages or endpoints from recon**, so the plan cannot be generic.

### 3. Re-check earlier findings
Every finding confirmed in an earlier run is replayed again. If it no longer reproduces, its bugs become
*fixed*; if a fixed bug is back, it is a *regression*.

### 4. Agentic exploration
The model drives the app one action at a time. Each step it sees the strategy, its recent steps, what is
already flagged, and an observation of the current page (identity, URL, text, forms, buttons, links, recent
requests, console errors). It answers with its reasoning and **one** action from a fixed, typed action space:

| Kind | Examples |
|---|---|
| Navigation | click, type (with repeat for huge inputs), navigate, new tab, switch tab, back, refresh |
| Files | upload a valid, empty, oversized, wrong-extension, corrupt or oddly named file |
| Raw HTTP | send a request with any method or a tampered body; replay an observed request, sequentially or as a race |
| Abuse | burst-click an element; throttle to slow 3G; clear the session mid-flow |
| Identities | switch between `anon`, user `a` and user `b` (two separate fresh accounts) |

Code validates every action before it runs and **fences all navigation and requests to the app's own
origin**. After each step, the plain-code oracles look at what happened. A path where an oracle fired, or where
the model suspects a break for a new reason, becomes a **candidate** with exact reproduction steps. Each break
is flagged once; the model is told what is already flagged and moves on. Exploration is bounded by steps,
minutes and the model budget.

To test privacy, each identity has a unique **canary** string, and the model is asked to put it into content
it creates. Whether a canary counts as private is decided by code: it was entered only on login-only pages or
sent only to login-only endpoints.

### 5. Confirmation by replay
Each distinct candidate is replayed **3 times from a clean state**: the app is restarted, fresh accounts and
fresh canaries are created, and references to earlier requests are re-matched by method and path. Health of
key pages and counts of unique values are measured before and after. All nine oracles run on each attempt, and
the verdict engine turns the three attempts into **fail**, **flaky** or **pass**. Confirmed findings become bugs
in the ledger with their reproduction steps; unconfirmed ones are kept as findings only.

### 6. Baseline suite
Six fixed attacks run on every browser flow in the contract: empty input, double submit, expired session,
slow network, bad file and rapid repeat. Because the attacks never change, the readiness score is comparable
from commit to commit. A model, if available, only chooses the inputs (and up to two follow-up probes per flow).

### 7. Report
`report.md` (and the dashboard API) shows the readiness score and its change against the base branch, newly
failing checks, regressions, the grid of the baseline suite, and for agentic runs: the recon summary, the
strategy, every finding with its verdict, and the **attack tree**, the path the agent actually took, with its
reasoning next to each step and the oracle signals flagged.

`--mode baseline` skips phases 1-5. With no model, agentic mode says so and runs the baseline suite only.

## The trust boundary

| Proposes (may be wrong) | Decides (deterministic code) |
|---|---|
| Planner: draft contract | Contract validation and human approval |
| Strategist: hypotheses | Grounding check against the app map |
| Explorer: next action, reasoning, suspicion | Action validation, origin fence, oracles |
| Attacker and prober: inputs | Replay x3, verdict engine, signatures |
| Fixer: a patch | The fixer gate |

Model output enters the system only as validated structured objects. Free text from the model is shown as
reasoning and is never parsed for a decision. Only a human can mark a bug as *intended*.

## Architecture

| Package | Responsibility |
|---|---|
| `contract` | Models, loader, placeholders and test data, approval |
| `sandbox` | Command and Docker sandboxes: start, reset, stop, logs |
| `runner` | Browser lifecycle, flow executor, baseline check runner, run orchestrator, fingerprint |
| `attacks` | The six fixed attacks and their built-in payloads |
| `agent` | Action space, agent session and identities, recon, strategy, explorer, replay, the agentic phase |
| `evidence` | Evidence models and the baseline collector |
| `verdict` | Oracles, verdict engine, signatures, score (pure code) |
| `ledger` | SQLite storage, migrations, bug state machine, reuse rule |
| `llm` | Providers (Anthropic, Claude Code, Codex, Gemini CLI, OpenAI-compatible, none), prompts, budget |
| `control` | Model presets and discovery, project inspection, background jobs for the web app |
| `dashboard` | Local server: REST, live event stream, control API |
| `report` | `report.md` and the attack tree |
| `fixer` | Optional patch proposal and gate |
| `cli` | The `sentri` command |
