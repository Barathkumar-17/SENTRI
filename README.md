<a id="readme-top"></a>

<div align="center">

# Sentri

**Sentri is an agent that attacks a running web app like a hostile user, so it breaks in testing and not in a live demo.**<br>
The AI plans the attacks; plain code decides pass or fail from measured evidence.

[![CI](https://github.com/Barathkumar-17/SENTRI/actions/workflows/ci.yml/badge.svg)](https://github.com/Barathkumar-17/SENTRI/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-local%20server-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black)](https://react.dev)
[![TypeScript 7](https://img.shields.io/badge/TypeScript-7-3178C6?logo=typescript&logoColor=white)](https://www.typescriptlang.org)
[![Vite 8](https://img.shields.io/badge/Vite-8-646CFF?logo=vite&logoColor=white)](https://vite.dev)
[![Playwright](https://img.shields.io/badge/Playwright-Chromium-2EAD33?logo=data:image/svg%2Bxml;base64,PHN2ZyB3aWR0aD0iNDAwIiBoZWlnaHQ9IjQwMCIgdmlld0JveD0iMCAwIDQwMCA0MDAiIGZpbGw9Im5vbmUiIHhtbG5zPSJodHRwOi8vd3d3LnczLm9yZy8yMDAwL3N2ZyI+CjxwYXRoIGQ9Ik0xMzYuNDQ0IDIyMS41NTZDMTIzLjU1OCAyMjUuMjEzIDExNS4xMDQgMjMxLjYyNSAxMDkuNTM1IDIzOC4wMzJDMTE0Ljg2OSAyMzMuMzY0IDEyMi4wMTQgMjI5LjA4IDEzMS42NTIgMjI2LjM0OEMxNDEuNTEgMjIzLjU1NCAxNDkuOTIgMjIzLjU3NCAxNTYuODY5IDIyNC45MTVWMjE5LjQ4MUMxNTAuOTQxIDIxOC45MzkgMTQ0LjE0NSAyMTkuMzcxIDEzNi40NDQgMjIxLjU1NlpNMTA4Ljk0NiAxNzUuODc2TDYxLjA4OTUgMTg4LjQ4NEM2MS4wODk1IDE4OC40ODQgNjEuOTYxNyAxODkuNzE2IDYzLjU3NjcgMTkxLjM2TDEwNC4xNTMgMTgwLjY2OEMxMDQuMTUzIDE4MC42NjggMTAzLjU3OCAxODguMDc3IDk4LjU4NDcgMTk0LjcwNUMxMDguMDMgMTg3LjU1OSAxMDguOTQ2IDE3NS44NzYgMTA4Ljk0NiAxNzUuODc2Wk0xNDkuMDA1IDI4OC4zNDdDODEuNjU4MiAzMDYuNDg2IDQ2LjAyNzIgMjI4LjQzOCAzNS4yMzk2IDE4Ny45MjhDMzAuMjU1NiAxNjkuMjI5IDI4LjA3OTkgMTU1LjA2NyAyNy41IDE0NS45MjhDMjcuNDM3NyAxNDQuOTc5IDI3LjQ2NjUgMTQ0LjE3OSAyNy41MzM2IDE0My40NDZDMjQuMDQgMTQzLjY1NyAyMi4zNjc0IDE0NS40NzMgMjIuNzA3NyAxNTAuNzIxQzIzLjI4NzYgMTU5Ljg1NSAyNS40NjMzIDE3NC4wMTYgMzAuNDQ3MyAxOTIuNzIxQzQxLjIzMDEgMjMzLjIyNSA3Ni44NjU5IDMxMS4yNzMgMTQ0LjIxMyAyOTMuMTM0QzE1OC44NzIgMjg5LjE4NSAxNjkuODg1IDI4MS45OTIgMTc4LjE1MiAyNzIuODFDMTcwLjUzMiAyNzkuNjkyIDE2MC45OTUgMjg1LjExMiAxNDkuMDA1IDI4OC4zNDdaTTE2MS42NjEgMTI4LjExVjEzMi45MDNIMTg4LjA3N0MxODcuNTM1IDEzMS4yMDYgMTg2Ljk4OSAxMjkuNjc3IDE4Ni40NDcgMTI4LjExSDE2MS42NjFaIiBmaWxsPSIjMkQ0NTUyIi8+CjxwYXRoIGQ9Ik0xOTMuOTgxIDE2Ny41ODRDMjA1Ljg2MSAxNzAuOTU4IDIxMi4xNDQgMTc5LjI4NyAyMTUuNDY1IDE4Ni42NThMMjI4LjcxMSAxOTAuNDJDMjI4LjcxMSAxOTAuNDIgMjI2LjkwNCAxNjQuNjIzIDIwMy41NyAxNTcuOTk1QzE4MS43NDEgMTUxLjc5MyAxNjguMzA4IDE3MC4xMjQgMTY2LjY3NCAxNzIuNDk2QzE3My4wMjQgMTY3Ljk3MiAxODIuMjk3IDE2NC4yNjggMTkzLjk4MSAxNjcuNTg0Wk0yOTkuNDIyIDE4Ni43NzdDMjc3LjU3MyAxODAuNTQ3IDI2NC4xNDUgMTk4LjkxNiAyNjIuNTM1IDIwMS4yNTVDMjY4Ljg5IDE5Ni43MzYgMjc4LjE1OCAxOTMuMDMxIDI4OS44MzcgMTk2LjM2MkMzMDEuNjk4IDE5OS43NDEgMzA3Ljk3NiAyMDguMDYgMzExLjMwNyAyMTUuNDM2TDMyNC41NzIgMjE5LjIxMkMzMjQuNTcyIDIxOS4yMTIgMzIyLjczNiAxOTMuNDEgMjk5LjQyMiAxODYuNzc3Wk0yODYuMjYyIDI1NC43OTVMMTc2LjA3MiAyMjMuOTlDMTc2LjA3MiAyMjMuOTkgMTc3LjI2NSAyMzAuMDM4IDE4MS44NDIgMjM3Ljg2OUwyNzQuNjE3IDI2My44MDVDMjgyLjI1NSAyNTkuMzg2IDI4Ni4yNjIgMjU0Ljc5NSAyODYuMjYyIDI1NC43OTVaTTIwOS44NjcgMzIxLjEwMkMxMjIuNjE4IDI5Ny43MSAxMzMuMTY2IDE4Ni41NDMgMTQ3LjI4NCAxMzMuODY1QzE1My4wOTcgMTEyLjE1NiAxNTkuMDczIDk2LjAyMDMgMTY0LjAyOSA4NS4yMDRDMTYxLjA3MiA4NC41OTUzIDE1OC42MjMgODYuMTUyOSAxNTYuMjAzIDkxLjA3NDZDMTUwLjk0MSAxMDEuNzQ3IDE0NC4yMTIgMTE5LjEyNCAxMzcuNyAxNDMuNDVDMTIzLjU4NiAxOTYuMTI3IDExMy4wMzggMzA3LjI5IDIwMC4yODMgMzMwLjY4MkMyNDEuNDA2IDM0MS42OTkgMjczLjQ0MiAzMjQuOTU1IDI5Ny4zMjMgMjk4LjY1OUMyNzQuNjU1IDMxOS4xOSAyNDUuNzE0IDMzMC43MDEgMjA5Ljg2NyAzMjEuMTAyWiIgZmlsbD0iIzJENDU1MiIvPgo8cGF0aCBkPSJNMTYxLjY2MSAyNjIuMjk2VjIzOS44NjNMOTkuMzMyNCAyNTcuNTM3Qzk5LjMzMjQgMjU3LjUzNyAxMDMuOTM4IDIzMC43NzcgMTM2LjQ0NCAyMjEuNTU2QzE0Ni4zMDIgMjE4Ljc2MiAxNTQuNzEzIDIxOC43ODEgMTYxLjY2MSAyMjAuMTIzVjEyOC4xMUgxOTIuODY5QzE4OS40NzEgMTE3LjYxIDE4Ni4xODQgMTA5LjUyNiAxODMuNDIzIDEwMy45MDlDMTc4Ljg1NiA5NC42MTIgMTc0LjE3NCAxMDAuNzc1IDE2My41NDUgMTA5LjY2NUMxNTYuMDU5IDExNS45MTkgMTM3LjEzOSAxMjkuMjYxIDEwOC42NjggMTM2LjkzM0M4MC4xOTY2IDE0NC42MSA1Ny4xNzkgMTQyLjU3NCA0Ny41NzUyIDE0MC45MTFDMzMuOTYwMSAxMzguNTYyIDI2LjgzODcgMTM1LjU3MiAyNy41MDQ5IDE0NS45MjhDMjguMDg0NyAxNTUuMDYyIDMwLjI2MDUgMTY5LjIyNCAzNS4yNDQ1IDE4Ny45MjhDNDYuMDI3MiAyMjguNDMzIDgxLjY2MyAzMDYuNDgxIDE0OS4wMSAyODguMzQyQzE2Ni42MDIgMjgzLjYwMiAxNzkuMDE5IDI3NC4yMzMgMTg3LjYyNiAyNjIuMjkxSDE2MS42NjFWMjYyLjI5NlpNNjEuMDg0OCAxODguNDg0TDEwOC45NDYgMTc1Ljg3NkMxMDguOTQ2IDE3NS44NzYgMTA3LjU1MSAxOTQuMjg4IDg5LjYwODcgMTk5LjAxOEM3MS42NjE0IDIwMy43NDMgNjEuMDg0OCAxODguNDg0IDYxLjA4NDggMTg4LjQ4NFoiIGZpbGw9IiNFMjU3NEMiLz4KPHBhdGggZD0iTTM0MS43ODYgMTI5LjE3NEMzMjkuMzQ1IDEzMS4zNTUgMjk5LjQ5OCAxMzQuMDcyIDI2Mi42MTIgMTI0LjE4NUMyMjUuNzE2IDExNC4zMDQgMjAxLjIzNiA5Ny4wMjI0IDE5MS41MzcgODguODk5NEMxNzcuNzg4IDc3LjM4MzQgMTcxLjc0IDY5LjM4MDIgMTY1Ljc4OCA4MS40ODU3QzE2MC41MjYgOTIuMTYzIDE1My43OTcgMTA5LjU0IDE0Ny4yODQgMTMzLjg2NkMxMzMuMTcxIDE4Ni41NDMgMTIyLjYyMyAyOTcuNzA2IDIwOS44NjcgMzIxLjA5OEMyOTcuMDkzIDM0NC40NyAzNDMuNTMgMjQyLjkyIDM1Ny42NDQgMTkwLjIzOEMzNjQuMTU3IDE2NS45MTcgMzY3LjAxMyAxNDcuNSAzNjcuNzk5IDEzNS42MjVDMzY4LjY5NSAxMjIuMTczIDM1OS40NTUgMTI2LjA3OCAzNDEuNzg2IDEyOS4xNzRaTTE2Ni40OTcgMTcyLjc1NkMxNjYuNDk3IDE3Mi43NTYgMTgwLjI0NiAxNTEuMzcyIDIwMy41NjUgMTU4QzIyNi44OTkgMTY0LjYyOCAyMjguNzA2IDE5MC40MjUgMjI4LjcwNiAxOTAuNDI1TDE2Ni40OTcgMTcyLjc1NlpNMjIzLjQyIDI2OC43MTNDMTgyLjQwMyAyNTYuNjk4IDE3Ni4wNzcgMjIzLjk5IDE3Ni4wNzcgMjIzLjk5TDI4Ni4yNjIgMjU0Ljc5NkMyODYuMjYyIDI1NC43OTEgMjY0LjAyMSAyODAuNTc4IDIyMy40MiAyNjguNzEzWk0yNjIuMzc3IDIwMS40OTVDMjYyLjM3NyAyMDEuNDk1IDI3Ni4xMDcgMTgwLjEyNiAyOTkuNDIyIDE4Ni43NzNDMzIyLjczNiAxOTMuNDExIDMyNC41NzIgMjE5LjIwOCAzMjQuNTcyIDIxOS4yMDhMMjYyLjM3NyAyMDEuNDk1WiIgZmlsbD0iIzJFQUQzMyIvPgo8cGF0aCBkPSJNMTM5Ljg4IDI0Ni4wNEw5OS4zMzI0IDI1Ny41MzJDOTkuMzMyNCAyNTcuNTMyIDEwMy43MzcgMjMyLjQ0IDEzMy42MDcgMjIyLjQ5NkwxMTAuNjQ3IDEzNi4zM0wxMDguNjYzIDEzNi45MzNDODAuMTkxOCAxNDQuNjExIDU3LjE3NDIgMTQyLjU3NCA0Ny41NzA0IDE0MC45MTFDMzMuOTU1NCAxMzguNTYzIDI2LjgzNCAxMzUuNTcyIDI3LjUwMDEgMTQ1LjkyOUMyOC4wOCAxNTUuMDYzIDMwLjI1NTcgMTY5LjIyNCAzNS4yMzk3IDE4Ny45MjlDNDYuMDIyNSAyMjguNDMzIDgxLjY1ODMgMzA2LjQ4MSAxNDkuMDA1IDI4OC4zNDJMMTUwLjk4OSAyODcuNzE5TDEzOS44OCAyNDYuMDRaTTYxLjA4NDggMTg4LjQ4NUwxMDguOTQ2IDE3NS44NzZDMTA4Ljk0NiAxNzUuODc2IDEwNy41NTEgMTk0LjI4OCA4OS42MDg3IDE5OS4wMThDNzEuNjYxNSAyMDMuNzQzIDYxLjA4NDggMTg4LjQ4NSA2MS4wODQ4IDE4OC40ODVaIiBmaWxsPSIjRDY1MzQ4Ii8+CjxwYXRoIGQ9Ik0yMjUuMjcgMjY5LjE2M0wyMjMuNDE1IDI2OC43MTJDMTgyLjM5OCAyNTYuNjk4IDE3Ni4wNzIgMjIzLjk5IDE3Ni4wNzIgMjIzLjk5TDIzMi44OSAyMzkuODcyTDI2Mi45NzEgMTI0LjI4MUwyNjIuNjA3IDEyNC4xODVDMjI1LjcxMSAxMTQuMzA0IDIwMS4yMzIgOTcuMDIyNCAxOTEuNTMyIDg4Ljg5OTRDMTc3Ljc4MyA3Ny4zODM0IDE3MS43MzUgNjkuMzgwMiAxNjUuNzgzIDgxLjQ4NTdDMTYwLjUyNiA5Mi4xNjMgMTUzLjc5NyAxMDkuNTQgMTQ3LjI4NCAxMzMuODY2QzEzMy4xNzEgMTg2LjU0MyAxMjIuNjIzIDI5Ny43MDYgMjA5Ljg2NyAzMjEuMDk3TDIxMS42NTUgMzIxLjVMMjI1LjI3IDI2OS4xNjNaTTE2Ni40OTcgMTcyLjc1NkMxNjYuNDk3IDE3Mi43NTYgMTgwLjI0NiAxNTEuMzcyIDIwMy41NjUgMTU4QzIyNi44OTkgMTY0LjYyOCAyMjguNzA2IDE5MC40MjUgMjI4LjcwNiAxOTAuNDI1TDE2Ni40OTcgMTcyLjc1NloiIGZpbGw9IiMxRDhEMjIiLz4KPHBhdGggZD0iTTE0MS45NDYgMjQ1LjQ1MUwxMzEuMDcyIDI0OC41MzdDMTMzLjY0MSAyNjMuMDE5IDEzOC4xNjkgMjc2LjkxNyAxNDUuMjc2IDI4OS4xOTVDMTQ2LjUxMyAyODguOTIyIDE0Ny43NCAyODguNjg3IDE0OSAyODguMzQyQzE1Mi4zMDIgMjg3LjQ1MSAxNTUuMzY0IDI4Ni4zNDggMTU4LjMxMiAyODUuMTQ1QzE1MC4zNzEgMjczLjM2MSAxNDUuMTE4IDI1OS43ODkgMTQxLjk0NiAyNDUuNDUxWk0xMzcuNyAxNDMuNDUxQzEzMi4xMTIgMTY0LjMwNyAxMjcuMTEzIDE5NC4zMjYgMTI4LjQ4OSAyMjQuNDM2QzEzMC45NTIgMjIzLjM2NyAxMzMuNTU0IDIyMi4zNzEgMTM2LjQ0NCAyMjEuNTUxTDEzOC40NTcgMjIxLjEwMUMxMzYuMDAzIDE4OC45MzkgMTQxLjMwOCAxNTYuMTY1IDE0Ny4yODQgMTMzLjg2NkMxNDguNzk5IDEyOC4yMjUgMTUwLjMxOCAxMjIuOTc4IDE1MS44MzIgMTE4LjA4NUMxNDkuMzkzIDExOS42MzcgMTQ2Ljc2NyAxMjEuMjI4IDE0My43NzYgMTIyLjg2N0MxNDEuNzU5IDEyOS4wOTMgMTM5LjcyMiAxMzUuODk4IDEzNy43IDE0My40NTFaIiBmaWxsPSIjQzA0QjQxIi8+Cjwvc3ZnPgo=)](https://playwright.dev)
[![SQLite](https://img.shields.io/badge/SQLite-ledger-003B57?logo=sqlite&logoColor=white)](https://sqlite.org)
[![Docker](https://img.shields.io/badge/Docker-optional-2496ED?logo=docker&logoColor=white)](https://www.docker.com)

<img src="documentation/images/hero.png" alt="Sentri run report showing readiness score and findings" width="100%">

**[Quickstart](#quickstart)** &nbsp;·&nbsp; **[How it works](#how-it-works)** &nbsp;·&nbsp; **[Documentation](documentation/README.md)**

<sub>The screenshot shows a real run on the bundled demo app (<code>examples/buggy-app</code>) and the bugs planted in it; see <a href="#demo">Demo</a>.</sub>

</div>

> Scope: verified on the included demo apps (`examples/buggy-app` and the `examples/flow-shop` shop) and two short
> proof runs; this is not a general reliability claim. See [Testing and limits](#testing-and-limits).

> [!TIP]
> Reviewing this project? Start with [Problem and solution](#problem-and-solution), then [Demo](#demo), then [Testing and limits](#testing-and-limits).

## Contents

**What you get** &nbsp;·&nbsp; [Highlights](#highlights) · [Features](#features) · [Tech stack](#tech-stack)

**Run it** &nbsp;·&nbsp; [Quickstart](#quickstart) · [Configuration](#configuration)

**Under the hood** &nbsp;·&nbsp; [How it works](#how-it-works) · [Key design principle](#key-design-principle-the-model-proposes-code-decides) · [Project layout](#project-layout)

**For reviewers** &nbsp;·&nbsp; [Problem and solution](#problem-and-solution) · [Demo](#demo) · [Testing and limits](#testing-and-limits) · [Team](#team)

**About** &nbsp;·&nbsp; [Roadmap](#roadmap) · [Documentation](#documentation) · [Acknowledgements](#acknowledgements) · [License](#license)

## Highlights

| | |
| --- | --- |
| **Docker sandbox** | Runs the app in a throwaway container, reset between attempts and published on the host's `127.0.0.1` only; without Docker the app runs as a local process. |
| **11 deterministic oracles** | Plain-code checks judge every replay: HTTP 5xx, stack traces, console errors, duplicates, hangs, logged-out access, cross-user exposure, XSS, missing CSRF checks, replay and race duplicates, and state corruption. |
| **3x replay of every finding** | Each suspected break is replayed three times from the first step of the session; 3 of 3 is fail, 1 or 2 of 3 is flaky. |
| **Bug ledger across commits** | Confirmed bugs go into a SQLite ledger that is re-checked on later runs, so regressions are flagged. |
| **Provider-swappable model** | Claude Code, Codex CLI, Gemini CLI, the Anthropic API, any OpenAI-compatible API or Ollama, or no model at all. |
| **Local dashboard** | `sentri ui` starts runs, follows them live and shows reports and replay evidence, listening on localhost only. |

## Features

- **Agentic exploration** with recon, an app-specific strategy and step-by-step browser and HTTP actions.
- **Replay confirmation**: a suspicion only becomes a bug after plain code sees it reproduce.
- **Two test identities** (`a`, `b`) to check logged-out access and cross-user data exposure.
- **Code-owned checks**: Sentri marks what each test user writes and opens their records as the other user and
  logged out, independent of the model.
- **Baseline suite** of six fixed attacks and a readiness score comparable across commits.
- **Bug ledger** across commits, with regressions flagged and human decisions recorded (`sentri bug mark`).
- **Bring your own model, or none**: Claude Code, Codex CLI, Gemini CLI, Anthropic API, any OpenAI-compatible
  API, or Ollama. Per-run token, call, step, time and budget limits.
- **Local web app** (`sentri ui`) to start runs, follow them live, read reports and inspect replay evidence. It
  listens on localhost only.
- **GitHub Action** that comments the score and regressions on pull requests, with optional merge gates.
- **Optional fixer** (`sentri fix`) for baseline crash bugs with a stack trace: it proposes a patch, runs a fixed
  gate and opens a draft pull request only if the gate passes.
- **Safety by default**: browser traffic is fenced to the app's origin, and API keys and provider account ids
  are scrubbed from the ledger, evidence, reports, logs and the web app.

## Tech stack

| Area | Used |
|---|---|
| Engine and CLI | Python 3.12+, Typer, Pydantic, Rich |
| Browser automation | Playwright (Chromium) |
| Sandbox | Docker SDK for Python, or a local process |
| Ledger | SQLite |
| Local server and API | FastAPI, Uvicorn |
| Web app | React, TypeScript, Vite (built into the package, no Node needed to use it) |
| Models | Anthropic SDK, OpenAI-compatible HTTP via httpx, Claude Code / Codex / Gemini CLIs, Ollama |
| Quality | pytest, Ruff, mypy (strict on the verdict package), GitHub Actions |

## Quickstart

Requires Python 3.12 or newer. Docker is optional. No API key is needed for this quickstart.

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git && cd SENTRI
pip install -e ".[dev]"
sentri setup        # downloads the Chromium build Playwright drives
sentri doctor       # checks Python, Chromium, Docker, git and model settings
```

`sentri doctor` prints one row per check (trimmed here); Docker and the LLM are optional:

```text
│ Python    │ ok       │ 3.13.18 on Linux x86_64                 │
│ Chromium  │ ok       │ Chromium 158.0.8127.36                  │
│ Docker    │ optional │ daemon not reachable ...                │
│ Local DNS │ ok       │ reverse lookup of 127.0.0.1 in 0.00s    │
│ git       │ ok       │ found                                   │
│ LLM       │ optional │ none (static payloads); optional: ...   │
```

Run it against the bundled demo app, which has planted bugs. Approving a contract is a deliberate human step:

```bash
sentri contract approve examples/buggy-app/contract.yaml --as "Your Name" --out examples/buggy-app/approved.yaml
```

```text
approved by Your Name at 2026-11-25T20:20:56Z -> examples/buggy-app/approved.yaml
```

Then run the baseline suite (no model needed):

```bash
sentri run --contract examples/buggy-app/approved.yaml --out report.md
```

It runs 24 checks (4 flows x 6 attacks), each 3 times from a restarted app; this took about 4 minutes on a Linux
test machine. The end of the output:

```text
signup:empty_input: FAIL - POST /api/signup -> 500 [3/3]; ...
signup:double_submit: FAIL - list_selector: 2 created, expected 1 [3/3]
...
comment:empty_input: FLAKY - ...
...
run 1 finished: readiness 61
model: 0 calls, 0 input + 0 output tokens, $0.0000
report written to report.md
readiness: 61   score = 100 x (11 + 0.5 x 1) / 19 = 61
```

The result: 7 failing checks, 1 flaky, 11 passing and 5 not applicable, 13 bugs in the ledger. This matches the
demo app's planted bugs in [`expected_grid.yaml`](examples/buggy-app/expected_grid.yaml) cell for cell.

Open `report.md`, or list the bugs with `sentri bug list`. To let a model explore, add a provider, for example
`--llm-provider claude_code` (a signed-in Claude Code) or `--ollama qwen2.5:7b` (a local model). See
[getting started](documentation/getting-started.md) for Docker mode and your own app.

## Configuration

**A model is optional.** With none, Sentri runs the baseline suite with built-in payloads; it needs no account
and no key. `sentri models` lists the model sources on your machine, and `--llm-provider auto` picks a signed-in
CLI or a local server, never a paid API key on its own.

| Setting | Flag | Environment variable |
|---|---|---|
| Provider | `--llm-provider` (`auto`, `claude_code`, `codex`, `gemini_cli`, `anthropic`, `openai_compatible`) or `--ollama MODEL` | `SENTRI_LLM_PROVIDER` |
| Endpoint for OpenAI-compatible APIs | `--llm-base-url` | `SENTRI_LLM_BASE_URL` |
| API key | (environment only) | `ANTHROPIC_API_KEY`, `SENTRI_LLM_API_KEY` |
| Model | `--model`, `--model-smart` (fixer) | `SENTRI_MODEL_FAST`, `SENTRI_MODEL_SMART` |
| Paid-API budget per run | | `SENTRI_LLM_BUDGET_USD` (default 1.00) |
| Exploration limits | | `SENTRI_EXPLORE_STEPS` (default 60), `SENTRI_EXPLORE_MINUTES` (default 10) |
| Ledger location | `--db` | `SENTRI_DB` |

Keys are read from the environment only, never from a file Sentri ships. What Sentri tests is described in a
`contract.yaml` (flows, how to start the app, which attacks to run); see [the contract](documentation/contract.md)
and [models](documentation/models.md).

Exit codes: 0 finished, 1 gate failed (`--fail-under`, `--fail-on-regression`), 2 contract or config error, 3 app
failed to start, 4 infrastructure missing.

## How it works

```mermaid
flowchart LR
    A[Your app in a sandbox<br/>Docker or local process] --> B[Recon<br/>pages, forms, API map]
    B --> C[Strategy<br/>model plans app-specific attacks]
    C --> D[Exploration<br/>model drives browser and HTTP]
    D --> E[Replay 3x<br/>from the first step]
    E --> F[11 deterministic oracles<br/>plain code decides]
    F --> G[(SQLite bug ledger)]
    G --> H[Readiness score<br/>and report]
```

1. **Sandbox.** Sentri starts your app in a throwaway Docker container, or as a local process when Docker is not
   available, and resets it between attempts.
2. **Recon (code only).** It crawls every reachable page, form and link, logged out and as a signed-in test
   user, reads the OpenAPI spec when there is one, and maps which pages and endpoints need login.
3. **Strategy (model).** A model reads that map and plans attacks for *this* app. Hypotheses that do not point
   at real pages or endpoints are dropped.
4. **Exploration (model proposes).** The model drives a real browser and raw HTTP requests step by step and
   picks each next move from what happened.
5. **Replay 3x.** Every suspected break is replayed three times from the first step of the session, each from a
   restarted app (an app you only give as a URL is replayed on its live state, and the report says so).
6. **11 deterministic oracles** judge each replay: HTTP 5xx, stack traces in responses, browser console errors,
   duplicate records, hangs, logged-out access to protected data, one user seeing another's data, unescaped
   reflected input (XSS), state changes accepted without their CSRF token, duplicates from replays or races,
   and state corruption. A finding is **fail** at 3 of 3, **flaky** at 1 or 2 of 3.
7. **Ledger and score.** Confirmed bugs go into a SQLite ledger that is re-checked on later runs, so regressions
   are flagged. A fixed baseline suite of six attacks gives a **readiness score** that stays comparable across
   commits.

With no model configured, Sentri skips steps 2 to 4 and runs only the baseline suite (empty input, double
submit, expired session, slow network, bad file, rapid repeat).

Details: [how Sentri works](documentation/how-it-works.md).

## Key design principle: the model proposes, code decides

The model can suggest attacks and drive the browser, but it never decides a verdict. The verdict, ledger and
report code may not import any model, attacker, planner or fixer code, and the replay and confirmation path may
not reach it through any chain of imports. This is enforced by a test,
[`tests/test_trust_boundary.py`](tests/test_trust_boundary.py), which runs with the normal test suite. Only a
human can mark a bug as intended.

Sentri also tests as a **black box**: the model sees the running app's pages, requests and responses, never its
source code, README, database or server logs. App content reaches the model only as fenced, untrusted data.

## Project layout

```text
src/sentri/
  agent/        recon, strategy, exploration and replay confirmation
  attacks/      the six baseline attacks
  verdict/      deterministic oracles, verdict engine and readiness score (no model code)
  ledger/       SQLite bug ledger
  report/       markdown report
  sandbox/      Docker and local-process sandboxes
  llm/          model providers
  dashboard/    FastAPI server and the built web app
  fixer/        optional patch proposer and gate
  cli.py        the `sentri` command
frontend/       web app source (React, Vite, TypeScript)
examples/       demo apps with planted bugs, CI example, saved proof runs
documentation/  user and developer guides
tests/          unit, verdict, integration and trust-boundary tests
action.yml      the GitHub Action
```

## Problem and solution

| Problem | Solution |
|---|---|
| Web apps break in front of users or in a live demo because nobody tested them like a hostile user: an empty form, a second click, a stale tab, a bad file, a slow network, a user opening someone else's record by its id. Hand-written tests only cover the paths you thought of, and an AI agent on its own will happily report bugs that are not there. | Sentri attacks the running app in a disposable sandbox (a Docker container, or a local process when Docker is not available). The AI plans the attacks; plain code decides pass or fail from measured evidence. Every suspected break is replayed 3 times before it reaches the report, and bugs go into a ledger that is re-checked from commit to commit, with a readiness score for each run. |

## Demo

The screenshots are from a real run of the [Quickstart](#quickstart) on the bundled demo app
(`examples/buggy-app`, which has planted bugs), from a fresh clone of `main` at `0a576b2`, with no model:

```bash
git clone https://github.com/Barathkumar-17/SENTRI.git && cd SENTRI
pip install -e ".[dev]"
sentri setup
sentri contract approve examples/buggy-app/contract.yaml --as "Your Name" --out examples/buggy-app/approved.yaml
sentri run --contract examples/buggy-app/approved.yaml --out report.md
sentri ui
```

The run scored **readiness 61** (`score = 100 x (11 + 0.5 x 1) / 19 = 61`): 11 checks passed, 7 failed, 1 was
flaky and 5 did not apply, and it took 5 min 8 s on a Windows 11 machine. For example, submitting the signup form
empty made `POST /api/signup` return HTTP 500 in 3 of 3 replays (BUG-001). It also found a double submit that
creates 2 accounts and a rapid repeat that creates 4, HTTP 500s from `POST /tasks/new` on an expired session, a
bad file and rapid repeats, and an HTTP 500 from `POST /api/comments` on empty input in 1 of 3 replays (flaky).

<p align="center">
  <img src="documentation/images/hero.png" alt="Sentri run report for the demo app: readiness 61 of 100, 11 of 19 applicable checks passed, 1 flaky, and 8 baseline checks failed" width="100%">
  <br><sub><b>Run report.</b> The readiness score with its formula, what broke and the evidence behind it.</sub>
</p>

<p align="center">
  <img src="documentation/images/home.png" alt="Sentri Home page after the demo run: the latest run at readiness 61 of 100, the recent runs table and 11 open bugs that need a decision" width="100%">
  <br><sub><b>Home.</b> The latest run, recent runs and the open bugs that need a human decision.</sub>
</p>

## Testing and limits

### Run the tests

```bash
pip install -e ".[dev]" && sentri setup
ruff check src tests scripts examples
ruff format --check src tests scripts examples
mypy src                         # strict on the verdict package
pytest                           # fast tests (unit, verdict, trust boundary)
python scripts/smoke_test.py     # the CLI and the web workflow end to end
pytest -m integration            # browser and demo-app tests, including the full baseline grid (minutes)
```

More detail, including the Docker lifecycle check: [development](documentation/development.md).

### What has and has not been verified

Sentri is an early MVP (version 0.1.0, not yet released). What has actually been run:

- **Fixture apps.** The full baseline grid on `examples/buggy-app` is compared cell by cell against
  [`expected_grid.yaml`](examples/buggy-app/expected_grid.yaml) by the integration tests. `examples/flow-shop` is a
  small shop with hidden failures used for agentic validation.
- **One Docker + model run** on flow-shop (Claude Sonnet 5.5 through Claude Code, 15 steps): it confirmed one HTTP
  500, replayed 3 of 3. That contract had no baseline flows, so this run produced no readiness score, and the
  fixer's draft pull request step was not part of it.
- **Two short proof runs** with a real model on OWASP Juice Shop 20.4.0 and flaskr, in command mode; results are in
  the [example report](documentation/example-report.md).
- **The fixer's draft pull request path** has no end-to-end test or recorded run yet.
- These are existence proofs, not detection rates. Sentri has not been shown to work reliably on arbitrary apps.
- **Not covered:** business logic (a wrong total is not a crash), security scanning (no CVE, TLS or header
  checks; use ZAP, Burp or Nuclei), desktop and mobile apps, and phone layouts for the web app.

The full list is in [limitations](documentation/limitations.md). **Only run Sentri on apps you own or have
permission to test.**

## Team

| Member | GitHub |
|---|---|
| Barathkumar | [@Barathkumar-17](https://github.com/Barathkumar-17) |
| Gokul K | [@Gokul-2k7](https://github.com/Gokul-2k7) |

## Roadmap

- Run Sentri on more apps beyond the fixtures and the two proof apps, and publish the reports.
- More model-driven runs in Docker mode (one so far).
- Test small local models for driving exploration (currently untested).
- Tag the first release.

## Documentation

Full documentation: [`documentation/`](documentation/README.md). Report security issues privately as
described in [SECURITY.md](SECURITY.md). Changes are listed in the [changelog](CHANGELOG.md).

## Acknowledgements

- [PentestGPT](https://github.com/GreyDGL/PentestGPT), for showing how an LLM can drive multi-stage testing.
- [MAPTA](https://arxiv.org/abs/2508.20816) (Multi-Agent Penetration Testing AI for the Web), for the idea that
  every finding needs end-to-end validation before it counts.
- [Playwright](https://playwright.dev), which drives the browser.

## License

MIT. See [LICENSE](LICENSE).
