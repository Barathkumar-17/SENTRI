# How Sentri works

## The core rule

**The AI proposes and acts. Plain code decides.** The model writes the attack strategy, drives the exploration
and chooses inputs. It never decides whether something is a bug. Every verdict comes from deterministic code
reading measured evidence, and a test in the repository fails if the verdict, ledger, report or replay code
reaches anything model-related, even through another module. Without a model there is no agent: only the
baseline suite runs.

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
- follows redirects (trailing-slash and localized variants, login walls) and records where each path leads;
- clicks buttons that look navigational (Next, Continue, Get started, Submit...) outside forms and records
  where they lead, so wizard-style pages are found;
- submits each form once with ordinary generated values, so the API calls behind buttons show up; when a
  submit lands on another form (checkout, multi-step signup) it keeps going and records the sequence as a flow;
- reads an OpenAPI or Swagger spec if the app publishes one (`/openapi.json`, `/swagger.json`, ...): every
  endpoint with its method, body fields, constraints and declared auth;
- finds the **auth boundary by behaviour**: a page or endpoint is login-only when a logged-out request is
  redirected or refused (401/403) while a signed-in request succeeds. A page that merely failed to load, or one
  an API spec only declares as protected, is not treated as proven;
- records what a logged-out visitor gets anyway (the home page, the login page, an unknown route), so a
  single-page app that serves the same HTML shell on every route is not mistaken for an auth bypass.

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

Code validates every action before it runs. **Every browser context is fenced to the app's own origin**:
links, form posts, popups, scripts' requests and websockets to other sites are blocked or stubbed; static
assets from a CDN still load (set `app.offsite_assets: block` to stop them too); test-account tokens are only
ever attached to the app's own requests, and a redirect that would carry one elsewhere is stopped. After each
step, the plain-code oracles look at what happened. A path where an oracle fired, or where
the model suspects a break for a new reason, becomes a **candidate**. Its reproduction steps are the **whole
session up to that point**, because the app is not reset during exploration and a bug can depend on setup done
many steps earlier. Each break is flagged once; the model is told what is already flagged and moves on. Exploration is bounded by steps,
minutes and the model budget.

What the model sees comes in two parts: Sentri's task (strategy, focus, budget) and **APP DATA** (page text,
console errors, requests, step outcomes), fenced with markers the app cannot forge. Its instructions start with
a hard rule never to follow instructions found in APP DATA, so a page that says "ignore your instructions" is
just page text.

The loop is hardened in code:
- an unusable answer is retried once with the problem shown; if it is still unusable, code picks the next
  unexplored link or button itself;
- unusable answers, invalid actions and refused actions each use up a visible step, and three in a row end
  exploration;
- each hypothesis gets 10 steps, more while it keeps reaching new page states (up to 20); repeating the same
  action three times with no change moves the focus to the next hypothesis at once.

To test privacy, each identity has a unique **canary** string, and **Sentri itself** adds it to free text the
agent types and to JSON bodies it sends as user `a` or `b`. Typed fields such as email, number or password,
and empty or huge inputs, are left exactly as chosen. Whether a canary counts as private is decided by code:
it was entered only on login-only pages or sent only to login-only endpoints. When code sees a create succeed,
it takes the new record's id (from the redirect or the JSON response) and opens the record directly as the
owner, logged out and as the other user: the other user reading a record the logged-out visitor cannot see is
cross-user exposure (IDOR), with no model involved.

Code also loads every GET form and query string with an inert marker (`"><sentri-xss-...>`) before the model's
first step, and re-sends each form submit that carried a CSRF token once without it.

### 5. Confirmation by replay
Each distinct candidate is replayed **3 times from a clean state**: the app is restarted, fresh accounts and
fresh canaries are created, the whole session prefix is replayed, and references to earlier requests are
re-matched by method and path (replays send the original request's exact bytes). Health of key pages and the
number of distinct records holding a unique value are measured before and after. A step that ran during
exploration but cannot run now (its element is gone) fails the attempt instead of being skipped; if any
attempt fails that way the finding can be at most flaky, and with no break seen it is inconclusive, never a
pass. All eleven oracles run on each attempt, and the verdict engine turns the three attempts into **fail**,
**flaky** or **pass**. Confirmed findings become bugs
in the ledger with their reproduction steps; unconfirmed ones are kept as findings only.

### 6. Baseline suite
Six fixed attacks run on every browser flow in the contract: empty input, double submit, expired session,
slow network, bad file and rapid repeat. Because the attacks never change, the readiness score is comparable
from commit to commit. In this suite a model, if available, only chooses the inputs (and up to two follow-up
probes per flow).

### 7. Report
`report.md` (and the dashboard API) shows the readiness score (baseline suite plus the agentic impact of
confirmed findings) and its change against the base branch, newly failing checks, regressions, the grid of the baseline suite, and for agentic runs: the recon summary, the
strategy, every finding with its verdict, and the **attack tree**, the path the agent actually took, with its
reasoning next to each step and the oracle signals flagged.

`--mode baseline` skips phases 1-5. With no model, agentic mode says so and runs the baseline suite only.

## The trust boundary

| Proposes (may be wrong) | Decides (deterministic code) |
|---|---|
| Planner: draft contract | Contract validation and human approval |
| Strategist: hypotheses | Grounding check against the app map |
| Explorer: next action, reasoning, suspicion | Action validation, origin fence, canaries and IDOR/XSS/CSRF probes, oracles |
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
