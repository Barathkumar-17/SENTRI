# Limitations

| Limitation | Detail |
|---|---|
| Business logic | Sentri catches crashes, errors, hangs, duplicates, logged-out access, cross-user exposure, unescaped reflected input, missing CSRF checks and state corruption. It does not know that a total is wrong or a discount is too big. |
| A pass is narrow | It means the app survived these attacks in this run, not that it is safe. A run with no applicable baseline check shows "insufficient coverage", not a score. |
| Black box | Crashes are seen only through what the app shows (HTTP 5xx, error text in responses, browser errors). An exception the app swallows and only logs is not detected. |
| URL mode replays | For an app Sentri did not start there is no reset: replays run on the live state, so three replays are not three independent confirmations. |
| Exploration varies | The agent decides what to try, so two runs explore differently. Confirmed findings are replayed on every later run; the baseline score is the comparable number. |
| Model quality | A weak model explores less well. It cannot make Sentri report a false bug, because replay and plain code decide. |
| Canvas interfaces | Apps drawn on a canvas (Flutter web) expose little to a browser; Sentri then works through their API (OpenAPI spec, token login). |
| Privacy rules need recon | "Private" means entered only on pages or endpoints recon proved need login. Data an app shows publicly by design is not flagged. |
| XSS is detection only | `xss_reflection` flags Sentri's inert marker echoed unescaped in an HTML response. It does not execute scripts, so XSS that only happens in the browser (DOM-based, or inside JavaScript strings) is not judged. |
| CSRF check is narrow | `csrf_missing` runs only where the app's own form or request carries a CSRF token, and re-sends the request as the same user without it. Apps that rely on SameSite cookies or Origin checks instead are not flagged. |
| Replays repeat the whole session | A finding is replayed from the first step of the session, so long explorations make confirmation slower. |
| Origin fence and CDNs | Off-origin scripts, styles, images and fonts load by default (never with test tokens); everything else off-origin is blocked, which can change how apps behave that call third-party APIs from the browser. Page loads are fetched by the fence with redirects off, so an open redirect cannot take the browser elsewhere; only while a slow-network attack is throttling does the browser load pages by itself (a page that still lands elsewhere is detected and left at once). |
| Throttling | Page loads, and with `auth.api` tokens also the app's API calls, are fetched by the origin fence, which slow-network emulation does not slow; while Sentri throttles on purpose the browser fetches them itself. |
| Not a security scanner | No CVE templates, TLS or header checks; use ZAP, Burp or Nuclei for those. |
| Noise | Network timing can change results; three attempts and the flaky verdict reduce this but do not remove it. |
| Setup-heavy apps | Apps that need several services, keys or special environments may not start in the sandbox. |
| Web only | Desktop and mobile apps are out of scope (their APIs are not). |
| Duplicate counts | The baseline duplicates check needs a list selector, an expected count or a database count. |
| Desktop browsers only | The web app has no phone layout. |
| Redaction is pattern-based | Keys are scrubbed by their exact values (environment, CI, typed into the web app) and by known shapes (`sk-`, `gsk_`, `AIza`, JWTs, bearer tokens, provider account ids). A secret in an unknown shape that is not configured anywhere can still appear in recorded app responses. |
| Replay steps are stored exactly | What a replay must re-send (including the text typed into test accounts' forms) is kept unscrubbed in the local ledger, so replays work; keep `.sentri/` out of git and shared drives. |
| Model speed and size | CLI providers start a process per model call (seconds per step). Small local models are untested for driving exploration. |
| Slow DNS on some Macs | If `sentri doctor` reports slow local DNS, Python dev servers start slowly; map `127.0.0.1` to `localhost` in `/etc/hosts`. |
| Docker mode | Lifecycle: `scripts/docker_lifecycle_check.py` passes on real Docker daemons (no model). One model-driven run (Sonnet 5.5 through Claude Code) was done in Docker mode on flow-shop only; command mode and URL mode have more runs. Sentri publishes the container port on **all host interfaces** (`ports={"<port>/tcp": <port>}` in `sandbox/docker_sandbox.py`); no contract setting restricts it to `127.0.0.1`. Smallest fix, not made: publish with `("127.0.0.1", port)`. |
