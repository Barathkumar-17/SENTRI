# Limitations

| Limitation | Detail |
|---|---|
| Business logic | Sentri catches crashes, errors, hangs, duplicates, logged-out access, cross-user exposure, unescaped reflected input, missing CSRF checks and state corruption. It does not know that a total is wrong or a discount is too big. |
| A pass is narrow | It means the app survived these attacks in this run, not that it is safe. |
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
| Web screens | The local server's web screens are in progress; use the CLI. The API they will use works today. |
| Model speed and size | CLI providers start a process per model call (seconds per step). Small local models are untested for driving exploration. |
| Slow DNS on some Macs | If `sentri doctor` reports slow local DNS, Python dev servers start slowly; map `127.0.0.1` to `localhost` in `/etc/hosts`. |
