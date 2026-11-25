# Security Policy

## Use Sentri only on apps you may test

Sentri behaves like a hostile user on purpose: it sends malformed input, replays and races requests, and tries
to read one test account's data as another. **Only run it against apps you own or have explicit permission to
test.** Point it at a sandboxed copy of your app (a Docker container or a local process that Sentri starts), not
at a live system with real users or data.

## Supported versions

Sentri is pre-release (version 0.1.0, no tagged releases yet). Security fixes go to the `main` branch only.

## Reporting a vulnerability

Please report security issues privately. Do not open a public issue or pull request for them.

- Preferred: GitHub's private vulnerability reporting, from the repository's **Security** tab
  (**Report a vulnerability**), if it is enabled.
- Otherwise, email **barathkumar2k7@gmail.com** with the subject line `Sentri security`.

Include what you found, the steps or a minimal contract that reproduces it, the Sentri version
(`sentri --version`) and your operating system. Remove any real API keys or credentials from what you send.

You can expect an acknowledgement within 7 days. Once a fix is ready, it is merged to `main` and you are credited
in the changelog unless you prefer not to be.

## In scope

Issues in Sentri itself, for example:

- an API key, token or provider account id that is not scrubbed from the ledger, evidence, reports, logs or the
  web app;
- a way for app content to make Sentri act outside the app's origin, or send test-account tokens elsewhere;
- the local web app or API answering requests from other machines or from other websites;
- model output that changes a verdict without going through the plain-code oracles.

Vulnerabilities that Sentri finds in *your* app are not Sentri vulnerabilities; report those to that app's
maintainers.
