-- Who chose each exploration step: ai (the model) | code_probe (Sentri's own probe) | code_fallback.
ALTER TABLE exploration_steps ADD COLUMN origin TEXT;
