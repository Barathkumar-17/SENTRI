# Verdicts, bugs and the score

## The nine oracles

Each oracle is a pure function of measured evidence: requests and responses, browser console, the app's logs,
page text, counts and timing. Same evidence, same answer.

| Oracle | Fires when |
|---|---|
| HTTP 5xx | the app (its own origin) answered with a server error |
| Exception | a stack trace or fatal error appears in the app's logs |
| Console error | the browser reported a JavaScript error (4xx resource messages and `favicon.ico` are ignored) |
| Duplicates | a double submit, rapid repeat or slow-network retry created more records than expected (needs a list or count hint in the contract) |
| Timeout | a navigation or the app's own request did not finish in time, or the page crashed |
| Logged-out access | a logged-out request got a page or endpoint that recon proved needs login, or got a signed-in user's private data |
| Cross-user exposure | user `b` received user `a`'s private data (or the other way round) |
| Replay duplicate | a replay or burst left more copies of a unique value than one action allows |
| State corruption | a page that worked before the steps returns a server error afterwards |

"Login-only" and "private" come from recon, never from the model: a path needs login when a logged-out request
was redirected or refused while a signed-in one succeeded; a value is private when its owner entered it only on
login-only pages.

## Fail, flaky, pass

Every check runs **3 times from a clean state**.

| Verdict | Rule |
|---|---|
| fail | an oracle fired in 3 of 3 attempts |
| flaky | an oracle fired in 1 or 2 of 3 |
| pass | nothing fired |
| N/A | the baseline attack does not apply to the flow (e.g. bad file on a flow without upload) |
| error | Sentri or the sandbox broke, not the app; never scored |

## The bug ledger

Each failure becomes a bug identified by a **signature**: a hash of the oracle, the route (ids replaced by
`:id`) and the normalized error. The same bug in a later run matches the same signature.

| Status | Meaning |
|---|---|
| open | fails |
| flaky | fails sometimes |
| fixed | no longer reproduces |
| intended | a person decided the behaviour is acceptable (`sentri bug mark BUG-007 intended --note "..."`) |

A fixed bug that comes back is a **regression**. If an intended bug's behaviour changes (a different error, a
different status), it is raised again for review. Confirmed agentic findings keep their reproduction steps and
are replayed on every later run.

**Reuse:** a baseline check that passed on unchanged code is reused instead of re-run. "Unchanged" means the
same app files (Sentri's own outputs excluded), the same contract and, in command mode, the same inherited
environment. Any change re-tests.

## The readiness score

```
score = 100 x (passed + 0.5 x flaky) / applicable checks  -  5 per regressed bug   (never below 0)
```

It counts the baseline suite, so it stays comparable between commits. Agentic findings are listed separately;
their regressions do count in the penalty. The report prints the formula with the real numbers.

**A pass means "survived these attacks", nothing more.** See [Limitations](limitations.md).
