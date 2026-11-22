# Limitations

| Limitation | Detail |
|---|---|
| Business logic | Sentri catches crashes, errors, hangs, duplicates, logged-out access, cross-user exposure and state corruption. It does not know that a total is wrong or a discount is too big. |
| A pass is narrow | It means the app survived these attacks in this run, not that it is safe. |
| Exploration varies | The agent decides what to try, so two runs explore differently. Confirmed findings are replayed on every later run; the baseline score is the comparable number. |
| Model quality | A weak model explores less well. It cannot make Sentri report a false bug, because replay and plain code decide. |
| Canvas interfaces | Apps drawn on a canvas (Flutter web) expose little to a browser; Sentri then works through their API (OpenAPI spec, token login). |
| Privacy rules need recon | "Private" means entered only on pages or endpoints recon proved need login. Data an app shows publicly by design is not flagged. |
| No XSS oracle yet | Injected markup is reported by the model but not judged by plain code yet. |
| Noise | Network timing can change results; three attempts and the flaky verdict reduce this but do not remove it. |
| Setup-heavy apps | Apps that need several services, keys or special environments may not start in the sandbox. |
| Web only | Desktop and mobile apps are out of scope (their APIs are not). |
| Duplicate counts | The baseline duplicates check needs a list selector, an expected count or a database count. |
| Web screens | The local web app's API is complete; its screens are still being built. |
| Slow DNS on some Macs | If `sentri doctor` reports slow local DNS, Python dev servers start slowly; map `127.0.0.1` to `localhost` in `/etc/hosts`. |
