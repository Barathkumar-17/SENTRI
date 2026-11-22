# CI with GitHub Actions

Sentri runs as an action in **your** repository, on **your** runner, with **your** model settings. Copy
[`examples/ci/sentri.yml`](../examples/ci/sentri.yml) to `.github/workflows/sentri.yml`.

```yaml
- uses: actions/checkout@v4
- uses: Barathkumar-17/SENTRI@main
  with:
    contract: contract.yaml
    # Pick one (or none for the baseline suite only):
    # llm-api-key: ${{ secrets.ANTHROPIC_API_KEY }}
    # llm-base-url: https://...   llm-api-key: ${{ secrets.MY_KEY }}   model: <id>
    # ollama-model: qwen2.5:7b     # a local model pulled onto the runner, no key
```

## Inputs

| Input | Meaning |
|---|---|
| `contract` | Path to the approved contract |
| `base-branch` | Branch the score change is compared with (default `main`) |
| `fail-under`, `fail-on-regression` | Opt-in gates; report only by default |
| `llm-provider` | Empty = automatic: `ollama-model` -> local model; `llm-base-url` -> OpenAI-compatible; `llm-api-key` -> Anthropic; nothing -> no model |
| `llm-api-key`, `llm-base-url`, `model`, `model-smart` | Your model settings |
| `ollama-model` | Install Ollama on the runner and pull this model (Linux and macOS runners) |
| `extra-args` | Extra `sentri run` arguments, e.g. `--mode baseline` |
| `sentri-source`, `python-version` | Where to install Sentri from; Python version |

Outputs: `report` and `exit-code`. The report is also written to the job summary.

## What a pull request gets
A comment (updated in place) with the readiness score and its change against the base branch, newly failing
checks, regressions and a link to the evidence artifact. The ledger is kept in the Actions cache: pushes to the
base branch save it, pull requests read it.

A GitHub-hosted runner cannot reach a model on your laptop; use `ollama-model`, an API, or a self-hosted runner.
Keys belong in *Settings > Secrets and variables > Actions*.
