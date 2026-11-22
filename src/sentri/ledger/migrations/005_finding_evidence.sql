-- Replay evidence of each finding: the files written per attempt, and the replay notes (failed steps,
-- inconclusive duplicate probes). JSON lists.
ALTER TABLE findings ADD COLUMN evidence TEXT;
ALTER TABLE findings ADD COLUMN notes TEXT;
