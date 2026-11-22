# Models

**You bring the model, or none.** Sentri ships no API key and the default is no model. A model is needed for
the agentic phases (strategy and exploration); without one, Sentri runs the baseline suite with built-in
inputs. Verdicts never depend on the model.

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

Claude models default to `claude-sonnet-5-5` for every role. Other CLIs and servers use their own default
unless you pass `--model`. `--model-smart` sets the model for the fixer.

## Safety around keys
- Vendor variables such as `ANTHROPIC_API_KEY` are read only after you choose that provider, so a key that
  happens to be in your environment is never spent silently.
- Keys are scrubbed from evidence, reports, the ledger and prompts. `sentri models` shows which key variables
  exist by name, never their values.
- Paid APIs are capped per run by `SENTRI_LLM_BUDGET_USD` (default 1.00).

## How answers are checked
Every model answer must match a schema (for example a typed action, or a list of hypotheses). Anything that
does not match is discarded: during exploration the step is skipped, three unusable answers in a row end
exploration, and the baseline suite falls back to built-in inputs.
