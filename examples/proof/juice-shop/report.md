<!-- sentri-report -->
## Sentri readiness: 95

`no applicable baseline checks; score = 100 - agentic 5 (1 crash) = 95`

Baseline suite: **n/a** · Agentic impact: **-5** (1 crash; confirmed by replay)

### Agentic exploration
Recon: 2 pages, 10 endpoints, 0 login-only ().
Strategy: OWASP Juice Shop demo e‑commerce, 8 hypotheses.

#### Findings (replayed 3x from a clean state, judged by plain code)
| Verdict | Finding | Oracles | Steps | Score impact |
|---|---|---|---|---|
| ❌ confirmed | reflected_input | exception | 1-1 | -5 (crash) |
| · unconfirmed | Unauthenticated users can fetch /rest/admin/application-configuration | - | 2-2 | - |
| · unconfirmed | Unauthenticated users can fetch /rest/admin/application-version | - | 5-5 | - |

#### Attack tree
- **reflected_input**
  - 1. `[anon] navigate(path='/redirect?to=%22%3E%3Csentri-xss-cae2ce%3E')` **⚑ log: info: solved <n>-star errorhandlingchallenge (error handling)**
    - _why: code probe: does this page write input back without escaping? (xss_reflection)_
- **admin_config_access** - Unauthenticated users can fetch /rest/admin/application-configuration
  - 2. `[anon] request(path='rest/admin/application-configuration')`
    - _why: The anon GET to /rest/admin/application-configuration succeeded with 200, confirming unauthenticated access to admin config._
  - 3. `[anon] request(path='rest/admin/application-version')`
    - _why: Admin config is already exposed; confirm further admin endpoints are accessible_
    - 4. `[anon] finish(reason='Unauthenticated access to admin configuration returned 200, exposing sensitive data')`
      - _why: The recent unauthenticated GET to /rest/admin/application-configuration returned 200, confirming the admin_config_access hypothesis and exposing a security brea_
- **admin_version_access** - Unauthenticated users can fetch /rest/admin/application-version
  - 5. `[anon] finish(reason='Unauthenticated admin version access succeeded')`
    - _why: Observed 200 from admin version endpoint, confirming unauthenticated access_
- **socketio_state_change** - POST to /socket.io/ can be used without authentication to trigger server‑side actions
  - 6. `[anon] request(method='POST', path='socket.io/')`
    - _why: Send a raw POST request to /socket.io/ as anonymous to see if the endpoint responds without authentication, fulfilling the first move of the socketio_state_chan_
    - 7. `[anon] navigate(path='/')`
      - _why: fallback (model reply unusable: http 429: {"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01m81q084weh29v5f)_
      - 8. `[anon] navigate(path='/redirect?to=https://github.com/juice-shop/juice-shop')`
        - _why: fallback (model reply unusable: http 429: {"error":{"message":"Rate limit reached for model `openai/gpt-oss-120b` in organization `org_01m81q084weh29v5f)_


Flaky: 0 · N/A: 0 · Errors: 0 · Reused: 0 · Probes: 0 (0 fail) · LLM: llm, $0.00 · Duration: 11m31s
