<!-- sentri-report -->
## Sentri readiness: insufficient coverage

`insufficient coverage: no applicable baseline check (0 inapplicable, 0 inconclusive)`

### Coverage and completion
Baseline checks: 0 passed, 0 reused pass, 0 failed, 0 flaky, 0 inapplicable, 0 inconclusive · flows tested: none · probes: 0
Exploration: 1 steps (0 chosen by the model, 0 code probes, 1 code fallbacks, 0 invalid), 1 distinct states, 8 pages and 3 endpoints mapped by recon
Candidates: 0 · confirmed 0 · not reproduced 0 · inconclusive 0 · stopped: model use stopped: provider quota exhausted (rate limit beyond the wait cap)
Phases: preflight: done; agentic: done; exploration stopped: model use stopped: provider quota exhausted (rate limit beyond the wait cap); baseline: no baseline cells in the contract
Model use stopped: provider quota exhausted (rate limit beyond the wait cap)
_Counts what this run tested. It is not a safety rating; untested behaviour is not covered._

### Agentic exploration
Recon: 8 pages, 3 endpoints, 2 login-only (/cart, /orders).
Strategy: simple e‑commerce shop with product pages, cart, and order history, 8 hypotheses.

#### Attack tree
- **anon_cart_add** - Anonymous visitors can add items to a cart via POST /cart/add without being logged in
  - 1. `[anon] navigate(path='/')`
    - _why: fallback (model reply unusable: rate limit beyond the wait cap (provider quota): retry in 1728s; {"error":{"message":"Rate limit reached for model `open)_


Flaky: 0 · N/A: 0 · Errors: 0 · Reused: 0 · Probes: 0 (0 fail) · LLM: llm, cost unknown · Duration: 28s
Model use: 2 calls (explorer 1, strategist 1), 2387 input + 1226 output tokens, 0 retries, cost unknown (2 calls to a model without a known price) · stopped: provider quota exhausted (rate limit beyond the wait cap)
