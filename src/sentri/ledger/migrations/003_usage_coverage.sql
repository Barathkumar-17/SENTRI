-- Run usage (model calls, tokens, retries, limits, stop reason), exploration coverage and completion, and the
-- origin of every finding/check (baseline | probe | ai) with the state its replays ran on (restart | none).

ALTER TABLE runs ADD COLUMN usage TEXT;              -- JSON, BudgetedProvider.usage()
ALTER TABLE runs ADD COLUMN coverage TEXT;           -- JSON, measured coverage + completion (see agentic.md)
ALTER TABLE checks ADD COLUMN origin TEXT;           -- baseline | probe | ai | code_probe
ALTER TABLE findings ADD COLUMN origin TEXT;         -- ai (model-selected action) | code_probe (Sentri's own probe)
ALTER TABLE findings ADD COLUMN replay_state TEXT;   -- restart | none (URL mode: live state)
