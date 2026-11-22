# Example report: Sentri against two real apps

Two open-source apps, run end to end with a real model on an ordinary laptop: one intentionally vulnerable
(OWASP Juice Shop), one ordinary (flaskr, the official Flask tutorial blog). Everything below comes from the
saved runs in [`examples/proof/`](../examples/proof/): `report.md` (what CI would post), `agentic.json` (the
full app map, strategy, every step with the model's reasoning, findings and attack tree) and `summary.json`.

## Setup

| | |
|---|---|
| Machine | Windows 11 laptop, Python 3.14, Chromium (Playwright) |
| Sandbox | Command mode. Docker was not installed on this machine, so both apps ran as local processes, restarted from a clean state before every replay |
| Model | `openai/gpt-oss-120b` on Groq (`--llm-provider openai_compatible`), a free-tier key limited to 8 000 tokens per minute |
| Budget | Deliberately small: 15 exploration steps or 10 minutes per app, model budget cap $1 |
| Contracts | [`juice-shop/contract.yaml`](../examples/proof/juice-shop/contract.yaml) (token login over the API, no browser flows), [`flaskr/contract.yaml`](../examples/proof/flaskr/contract.yaml) (browser login, one attacked flow). Run with `--allow-unapproved`: approving a contract is a human decision and was left to the reader |

Reproduce:

```bash
python examples/proof/fetch_apps.py            # Juice Shop 20.4.0 release + flaskr at a pinned commit
cd examples/proof/flaskr                        # or examples/proof/juice-shop
SENTRI_EXPLORE_STEPS=15 SENTRI_EXPLORE_MINUTES=10 SENTRI_LLM_TIMEOUT_S=150 \
  sentri run --contract contract.yaml --allow-unapproved \
  --llm-provider openai_compatible --llm-base-url https://api.groq.com/openai/v1 --model openai/gpt-oss-120b \
  --out report.md                               # SENTRI_LLM_API_KEY in the environment (or a git-ignored .env)
```

## Results

| App | Wall time | Model calls | Tokens (in + out) | Steps | Candidates | Confirmed | Score |
|---|---|---|---|---|---|---|---|
| OWASP Juice Shop 20.4.0 | 11 min 31 s | 10 | 30 672 + 4 538 | 8 of 15 (10-minute cap) | 3 | 1 | 95 (agentic -5) |
| flaskr | 10 min 23 s | 25 | 83 937 + 10 233 | 15 of 15 | 0 | 0 | 100 (baseline 5/5 pass) |

**Cost.** Sentri records $0 for these runs because its price table has no Groq entries; the token counts above
are what was used. Most of the wall time is waiting: replays restart the app three times per candidate, and the
free tier's 8 000 tokens per minute made the model wait between steps (Sentri now waits out HTTP 429 instead of
treating it as a bad answer).

### OWASP Juice Shop

| Verdict | Finding | Evidence |
|---|---|---|
| ❌ confirmed 3/3 (crash, -5) | Unhandled server error on `/redirect` | A code-owned probe (Sentri's inert marker in every query parameter, before the model's first step) sent `GET /redirect?to="><sentri-xss-…>`. Juice Shop answered 406 and logged `Error: Unrecognized target URL for redirect` with a full stack trace: its planted error-handling flaw. Reproduced in 3 of 3 clean replays. |
| · not confirmed | "Unauthenticated users can fetch `/rest/admin/application-configuration`" | The model suspected an auth bypass. Replay found no oracle firing: the endpoint is public in Juice Shop by design, and recon never saw it refuse a logged-out visitor, so nothing proves it should need login. Model suspicion alone never becomes a bug. |
| · not confirmed | "Unauthenticated users can fetch `/rest/admin/application-version`" | Same: public by design. |

Also visible in the attack tree: at step 8 the model followed Juice Shop's open redirect
(`/redirect?to=https://github.com/...`); the origin fence stubbed it and the browser stayed on the app.

What this short run did **not** reach: Juice Shop's login-only areas and its known SQL injection, basket IDOR and
XSS challenges. Juice Shop is an Angular app that routes with `#/...` fragments, so recon sees two pages and ten
API endpoints, and eight steps under a rate-limited model are not enough to get far. An earlier, longer run of
this build with Claude Code (Sonnet 5.5, 40 steps) did reach a 500 from the product search
(`q='(`) and user a opening other users' baskets by id, but that run was stopped before confirmation
because it exposed a Sentri bug (see below); treat those as leads, not confirmed findings.

### flaskr

No candidates in 15 steps; all five baseline attacks on "write a post" pass (empty input, double submit,
expired session, slow network, rapid repeat). flaskr redirects logged-out writes to the login page, checks the
author before edits, and shows every post on its public front page by design, which is exactly what Sentri
should not report. This run used an earlier commit of this branch (`5294825`); later commits only tightened the
origin fence, recon and ledger, verified by the integration suite.

## What the proof runs found in Sentri itself

Running against real apps is the point of this exercise: it found nine problems that unit tests and the demo app
had not, all fixed with a test each before the runs above.

| Found while running | Problem | Fix |
|---|---|---|
| flaskr, first run | Posts the blog shows to everyone were reported as user b seeing user a's private data (score 85 instead of 100) | Before calling it a leak, code fetches the page logged out; content a visitor also sees is published, not exposed |
| Juice Shop, first run | Replaying the whole session made every later candidate inherit an earlier break (one search 500 "confirmed" 8 findings) | Earlier lines of attack are replayed as setup only; oracles judge each candidate's own steps |
| Juice Shop | An open redirect took a logged-out recon visit to github.com, and recon mapped GitHub's pages as the app's | The fence fetches every page load with redirects off and stubs off-origin targets; recon never records an off-origin page |
| Juice Shop | Two findings with the same key in one run made a bug "fixed" and then "regress" inside that run | A key's checks carry every hit already confirmed for it in the run |
| Juice Shop | Chromium's Local Network Access checks blocked the app's own websocket, reported as a console error | The check is switched off at launch; the oracle ignores that browser-policy error |
| Groq | HTTP 429 rate limits counted as unusable answers and ended exploration after 2 steps | The OpenAI-compatible provider waits as long as the server asks, then retries |
| Groq | The strategist request was too large for a small per-request limit | One retry with a compact app map |
| Juice Shop | The model called `finish` after 3 of 15 steps with hypotheses untried | `finish` moves to the next untried hypothesis |
| Juice Shop | A Node.js crash was labelled by the log line after its stack trace | JavaScript traces are keyed by the error line above their frames |

## Reading the reports

- `report.md`: the readiness score with its split (baseline suite, agentic impact), the findings table with each
  finding's cost, and the attack tree with the model's reasoning under every step.
- `agentic.json`: everything behind it, including the recon app map and the steps the tree abbreviates.
- Verdicts come only from plain-code oracles over 3 clean replays; the model's reasoning is shown, never trusted.
