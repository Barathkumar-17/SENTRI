<!-- sentri-report -->
## Sentri readiness: 100

`score = 100 x (5 + 0.5 x 0) / 5 = 100`

### Coverage and completion
Baseline checks: 5 passed, 0 reused pass, 0 failed, 0 flaky, 0 inapplicable, 0 inconclusive · flows tested: add_to_cart · probes: 1
Exploration: 8 steps (5 chosen by the model, 0 code probes, 3 code fallbacks, 0 invalid), 3 distinct states, 8 pages and 3 endpoints mapped by recon
Candidates: 0 · confirmed 0 · not reproduced 0 · inconclusive 0 · stopped: time budget used
Phases: preflight: done; agentic: done; exploration stopped: time budget used; baseline: done (5 cells)
_Counts what this run tested. It is not a safety rating; untested behaviour is not covered._

### Grid
| Flow | empty_input | double_submit | expired_session | slow_network | rapid_repeat |
|---|---|---|---|---|---|
| add_to_cart | ✅ | ✅ | ✅ | ✅ | ✅ |

### Agentic exploration
Recon: 8 pages, 3 endpoints, 2 login-only (/cart, /orders).
Strategy: simple e-commerce shop with product pages, cart, and orders, 7 hypotheses.

#### Attack tree
- **anon_add_to_cart_without_login** - Logged-out visitors can add items to the cart via POST /cart/add and then view the cart without being redirected to login
  - 1. `[anon] navigate(path='/product/1')`
    - _why: Navigate to a product page to locate the add‑to‑cart form/button for anon user._
    - 2. `[anon] fill_form form (input[name="qty"]) + submit`
      - _why: Submit add-to-cart form as anon to test cart access._
      - 3. `[anon] navigate(path='/cart')`
        - _why: Attempt to view cart as anon after a prior add-to-cart which redirected to login; if cart is accessible it supports hypothesis_
        - 4. `[anon] request(method='POST', path='cart/add', content_type='application/x-www-form-urlencoded')`
          - _why: Send a raw POST /cart/add with product and quantity while anon to see if the server adds to cart without redirecting to login._
          - 5. `[anon] navigate(path='/')`
            - _why: fallback (model reply unusable: http 429: {"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `[REDACTED])_
            - 6. `[anon] navigate(path='/orders')`
              - _why: fallback (model reply unusable: http 429: {"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `[REDACTED])_
              - 7. `[anon] request(path='cart')`
                - _why: Attempt a raw GET request to /cart to see if the cart is accessible after the earlier POST add, without following browser navigation which redirects to login._
                - 8. `[anon] navigate(path='/login')`
                  - _why: fallback (model reply unusable: http 429: {"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `[REDACTED])_


Flaky: 0 · N/A: 0 · Errors: 0 · Reused: 0 · Probes: 1 (0 fail) · LLM: mixed, cost unknown · Duration: 30m33s
Model use: 22 calls (attacker 4, explorer 12, prober 5, strategist 1), 30882 input + 5492 output tokens, 60 retries, cost unknown (22 calls to a model without a known price)
