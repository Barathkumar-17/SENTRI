# Proof runs

Sentri against two real open-source apps: OWASP Juice Shop (intentionally vulnerable) and flaskr (the Flask
tutorial blog). Results, timings, token use and what the runs found: [documentation/example-report.md](../../documentation/example-report.md).

| File | What |
|---|---|
| `fetch_apps.py` | Downloads both apps at pinned versions into `*/app/` (git-ignored) |
| `*/contract.yaml` | The contracts the runs used |
| `*/report.md` | The report Sentri wrote (score split, findings, attack tree) |
| `*/agentic.json` | Full run data: app map, strategy, every step with the model's reasoning, findings |
| `*/summary.json` | Score breakdown, duration, checks and bugs |
| `export_run.py` | Writes `agentic.json` and `summary.json` from a ledger |
