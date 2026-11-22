# Live agent validation on flow-shop: blocked by provider quota

| | |
|---|---|
| Revision tested | `55db884` (engine), contract `examples/flow-shop/contract.agent.yaml` |
| Date | 2026-11-23 (18:50 UTC run start) |
| Model | Groq `openai/gpt-oss-120b` via `openai_compatible`, the user's key from a git-ignored `.env` |
| Limits | 25 steps, 15 min exploration; at most 60 calls, 120 000 tokens; 150 s per call; spend cap $1 (not enforceable: price unknown) |
| Tester input | app URL (Sentri starts it: command mode), a login flow that creates fresh test accounts for identities a and b (setup, disclosed: these identities are pre-authenticated by setup; the agent itself starts logged out), the goal "Complete a purchase". No baseline flows, routes or hints. |
| Initial state | freshly started app, empty in-memory data; reset level for replays: process restart (in-memory state only) |
| Pre-run quota check | one request, 91 tokens: HTTP 200; per-minute headers 7 909 / 8 000 tokens left (daily quota not exposed in headers) |

## What happened
1. Recon (code, no model): 8 pages, 3 endpoints, 2 login-only pages.
2. Strategist: 1 call, 2 387 input + 1 226 output tokens, 8 hypotheses.
3. Explorer, first call: HTTP 429 "tokens per day", provider retry hint 1 728 s. Sentri treated it as a quota (wait
   beyond its 30 s cap), stopped model use at once and did not retry.
4. One code fallback step (`navigate /`) ran before the loop stopped: an engine inefficiency, fixed in `85aab6f`
   (exploration now ends immediately when model use stops; regression test added).
5. No AI-selected step, no candidate, no replay. Readiness: "insufficient coverage" (correct: nothing was tested).

Usage: 2 model calls (strategist 1 ok, explorer 1 refused), 2 387 input + 1 226 output tokens, 0 retries, cost unknown,
28 s run time. Stop reason: `model use stopped: provider quota exhausted (rate limit beyond the wait cap)`.

## Verdict
Blocked by the provider's daily token quota; AI-led discovery is **not** demonstrated by this run. Files:
`report.md` (Sentri's report), `judge.json` (harness judge: steps by origin, expected failures not found).
