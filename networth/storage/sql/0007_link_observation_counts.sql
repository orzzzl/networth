-- An unresolved observation groups unidentified success evidence, not one slot.
-- Keep a bound from the provider's counts for the operator; never use it as an
-- adjudicated additional-slot count. NULL preserves unknown legacy evidence.
ALTER TABLE link_success_observation ADD COLUMN max_reported_results INTEGER
    CHECK (max_reported_results > 0);
ALTER TABLE link_success_observation ADD COLUMN last_observed_at TEXT;

-- Keep every explicit decision when later success evidence reopens the same
-- unresolved set. The observation projects only the latest applicable outcome.
CREATE TABLE link_observation_adjudication (
    id INTEGER PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES link_success_observation(observation_id),
    resolved_at TEXT NOT NULL,
    resolution_note TEXT NOT NULL CHECK (length(trim(resolution_note)) > 0),
    additional_slots INTEGER NOT NULL CHECK (additional_slots >= 0)
) STRICT;
INSERT INTO link_observation_adjudication (
    observation_id, resolved_at, resolution_note, additional_slots
)
SELECT observation_id, resolved_at, resolution_note, additional_slots
FROM link_success_observation WHERE resolved_at IS NOT NULL;
