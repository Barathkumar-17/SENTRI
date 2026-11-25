# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). The package version is `0.1.0`; no release has been
tagged yet, so everything below is unreleased. Dates are commit dates.

## [Unreleased]

### Added

- Repository files: contributing guide, Contributor Covenant 2.1 code of conduct, security policy and this
  changelog; package metadata (authors, keywords, classifiers, project URLs).
- Docker sandbox publishes the app's port on the host's `127.0.0.1` only (2026-11-24).
- flow-shop Docker recipe (`Dockerfile` and an unapproved contract template) and a recorded, bounded
  Docker + model run on it (2026-11-24).
- A no-model Docker lifecycle check (`scripts/docker_lifecycle_check.py`) and a manual `docker-lifecycle`
  workflow (2026-11-23).
- `auth.start_as`: begin exploration as a logged-in test account (2026-11-23).
- Local web app (React, Vite, TypeScript, built into the package): Home, runs with live progress and a stop
  button, per-run reports, finding pages with replay evidence, and bug decisions (2026-11-23).
- Secret scrubbing across the activity feed, ledger, evidence, reports, logs and API answers, for API keys and
  provider account ids (2026-11-23).
- Provider rate-limit handling: per-minute limits are waited out for up to one window, daily quotas stop the run
  with a recorded reason, brief 5xx overloads are retried twice (2026-11-22 to 2026-11-23).
- Proof runs on OWASP Juice Shop 20.4.0 and flaskr, with a pinned fetch script, saved reports and an example
  report (2026-11-22).
- flow-shop, a multi-step validation app with its hidden failures kept in the test harness (2026-11-22).
- Agentic engine: recon (pages, forms, links, OpenAPI specs), app-specific strategy, model-driven exploration
  in a real browser and over raw HTTP, replay confirmation 3 times from the start of the session, and the attack
  tree in the report (2026-11-22).
- Six agentic oracles: `auth_bypass`, `cross_user_exposure`, `replay_duplicate`, `state_corruption`,
  `xss_reflection` and `csrf_missing`, with code-owned canaries and probes (2026-11-22).
- Readiness score that deducts confirmed agentic findings by severity, and "insufficient coverage" instead of a
  score when no baseline check applies (2026-11-22).
- Origin fence for every browser context, and app content passed to the model only as fenced, untrusted data
  (2026-11-22).
- Token-auth API contracts and two separate test identities (2026-11-22).
- Model providers: Anthropic, Claude Code, Codex CLI, Gemini CLI, any OpenAI-compatible API, Ollama, and
  `--llm-provider auto`; no model is used unless one is chosen (2026-11-22).
- `sentri ui` and `sentri serve`, the local server for the web app and scripts (2026-11-22).
- CI workflows for this repository and a GitHub Action for users' repositories (2026-11-22).
- `sentri` command line: `setup`, `doctor`, `models`, `plan`, `contract check|approve`, `run`, `report`,
  `bug list|mark`, `ui`, `serve` and `fix` (2026-11-22).
- Planner that drafts a contract, and a fixer for baseline crash bugs that opens a draft pull request only after
  a fixed gate passes (2026-11-22).
- First version of the package: contract model, deterministic verdict core with five oracles (`http_500`,
  `exception`, `console_error`, `duplicates`, `timeout`), SQLite bug ledger, command and Docker sandboxes,
  browser runner, six baseline attacks, markdown report and the planted-bug demo app (2026-11-21).

### Changed

- CI runs on every push to `main` as well as on demand (2026-11-24).
- Agentic mode is the default for `sentri run` when a model is configured; `--mode baseline` runs only the six
  fixed attacks (2026-11-22).

### Fixed

- Slow app starts on macOS caused by reverse DNS lookups, with a `sentri doctor` check (2026-11-22).
- A container or app process left behind when startup fails or is cancelled (2026-11-23).
- Runs stopped from outside (a cancelled job, Ctrl+C) are recorded as aborted (2026-11-23).

### Security

- Removed seeded credentials, secrets and pre-approved contracts from the repository (2026-11-22).
- The replay and verdict path is kept free of model code, checked by a test that follows imports transitively
  (2026-11-22).

[Unreleased]: https://github.com/Barathkumar-17/SENTRI/commits/main
