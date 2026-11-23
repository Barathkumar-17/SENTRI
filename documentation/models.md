# Models

**You bring the model, or none.** Sentri ships no API key and the default is no model. **No model = no agent,
baseline suite only**: the model writes the strategy and drives exploration, so without one Sentri runs only
the six fixed attacks with built-in inputs. Verdicts never depend on the model.

## What Sentri can use

```bash
sentri models     # what this computer has
sentri doctor     # what will be used, and whether it is reachable
```

| Option | No key needed | How |
|---|---|---|
| No model (default) | yes | nothing to set |
| **Claude Code** you are signed in to | yes | `--llm-provider claude_code`. Found on PATH or inside the VS Code, Cursor or Windsurf extension (`SENTRI_CLAUDE_BIN` to point elsewhere). Sentri calls it in headless mode with tools and MCP servers switched off. |
| **OpenAI Codex CLI** you are signed in to | yes | `--llm-provider codex`, read-only sandbox (`SENTRI_CODEX_BIN`) |
| **Gemini CLI** you are signed in to | yes | `--llm-provider gemini_cli` (`SENTRI_GEMINI_BIN`) |
| **Ollama** on this computer | yes | `ollama pull qwen2.5:7b`, then `--ollama qwen2.5:7b` (or `SENTRI_OLLAMA_MODEL`) |
| LM Studio, vLLM, llama.cpp or any OpenAI-compatible server | yes | `--llm-provider openai_compatible --llm-base-url http://localhost:1234/v1 --model <id>` |
| Anthropic API | your key | `--llm-provider anthropic` with `ANTHROPIC_API_KEY` or `SENTRI_LLM_API_KEY` |
| OpenAI, Groq, OpenRouter, Gemini API, Mistral, DeepSeek, Together... | your key | `--llm-provider openai_compatible --llm-base-url <url> --model <id>` and `SENTRI_LLM_API_KEY` |
| Whatever is available | depends | `--llm-provider auto`: a signed-in CLI first (Claude Code, Codex, Gemini), then a running local server. Never a paid API key. |

Claude models default to `claude-sonnet-5-5` (Sonnet 5.5) for every role, the fixer included. Other CLIs and
servers use their own default unless you pass `--model`. `--model-smart` sets the model for the fixer.

## Speed and model size
- **Claude Code, Codex and Gemini CLI** start one CLI process per model call, so every exploration step pays
  for a process start and a fresh session: expect seconds per step (see the timings in the
  [example report](example-report.md)). An API provider (Anthropic or OpenAI-compatible) is much faster.
- **Small local models** (for example 7B with Ollama) are fine for choosing payloads but are **untested for
  driving exploration**: they tend to give invalid actions, which the loop retries, replaces with code-chosen
  steps and eventually stops on (see below).

## Safety around keys
- Vendor variables such as `ANTHROPIC_API_KEY` are read only after you choose that provider, so a key that
  happens to be in your environment is never spent silently.
- API keys (from environment variables, CI secrets or typed into the web app) and provider account ids (organization and project ids that providers quote in error messages) are scrubbed from everything Sentri stores or shows: evidence, reports, the ledger, log lines, prompts and the web app. A key typed into the web app is
  kept in memory only and scrubbed the same way. `sentri models` shows which key variables exist by name, never
  their values.
- Paid APIs are capped per run by `SENTRI_LLM_BUDGET_USD` (default 1.00). The cap is only enforceable where
  Sentri knows the model's price; for other models the report says the cost is **unknown** (never $0).
- Further limits: `SENTRI_LLM_MAX_CALLS` and `SENTRI_LLM_MAX_TOKENS` (input + output, with each call's output
  reserved before it is made), and `SENTRI_LLM_TIMEOUT_S` per call (default 60 s). When one is reached, model use
  stops cleanly and the report names the limit. Every report shows calls, tokens and retries per role.

## How answers are checked
Every model answer must match a schema (for example a typed action, or a list of hypotheses). During
exploration:
- an unusable answer is asked for again once, with the problem shown to the model;
- if it is still unusable, code picks the step itself: the next unvisited link, else a button not clicked yet;
- an invalid action (wrong fields or out-of-range values) or a refused one (outside the app) uses up its step
  and is shown in the report;
- three unusable, invalid or refused answers **in a row** end exploration; a usable answer resets the count.

In the baseline suite an unusable answer falls back to built-in inputs.

**Rate limits** (HTTP 429, common on free API tiers such as Groq's) are not unusable answers: the
OpenAI-compatible provider waits as long as the server asks (`Retry-After`, or "try again in 7.5s" in the
message) and tries again, at most 4 waits per call. When the provider says the limit is per minute (tokens or
requests per minute), it waits up to one window (65 s), since such a limit frees itself; otherwise at most 30 s.
A daily or hourly quota, or any longer wait, stops model use for the rest of the run at once, and the stop
reason says which limit the provider named and how long it asked to wait. Time spent waiting out a rate limit
does not count toward the per-call timeout `SENTRI_LLM_TIMEOUT_S` (default 60 s), which bounds how long the
provider takes to answer; it does count toward the exploration time. Every wait (role, limit, asked, waited) is
recorded with the run's model use. A provider that is briefly overloaded (HTTP 500, 502, 503 or 504, such as "high demand, try again later") is
retried twice with short pauses (4 s, then 12 s), recorded the same way; other errors (for example an unknown model)
are not retried.

## What the model is shown
Page text, console errors and network summaries come from the app under test, which may contain text written
to manipulate a model. Sentri puts all of it in one fenced **APP DATA** block, separate from its own
instructions, and every prompt starts with a rule never to follow instructions inside APP DATA. Secrets are
scrubbed first. Source code, READMEs, databases and server logs are never shown to the model at all.
