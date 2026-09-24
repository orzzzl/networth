-- Existing rows have unknown release history. Only the automatic minter opts in.
ALTER TABLE link_request ADD COLUMN lifecycle_protocol TEXT;
ALTER TABLE link_request ADD COLUMN url_release_authorized_at TEXT;
ALTER TABLE link_request ADD COLUMN abandon_requested_at TEXT;
ALTER TABLE link_request ADD COLUMN closure_audit_id TEXT;

CREATE TABLE link_poll_history (
    id INTEGER PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    observed_at TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('OBSERVED', 'FAILED'))
) STRICT;
INSERT INTO link_poll_history(flow_id, observed_at, outcome)
SELECT flow_id, last_poll_at, 'OBSERVED' FROM link_request WHERE last_poll_at IS NOT NULL;

-- Save the referencing ledger, then rebuild both tables in the same transaction.
-- Coverage is an unknown set, never an invented result or a guessed slot count.
CREATE TEMP TABLE link_adjudication_backup AS SELECT * FROM link_observation_adjudication;
DROP TABLE link_observation_adjudication;
CREATE TABLE link_success_observation_new (
    observation_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    link_session_id TEXT,
    reason TEXT NOT NULL CHECK (reason IN (
        'MISSING_SESSION_ID', 'MISSING_PUBLIC_TOKEN', 'DIGEST_KEY_UNAVAILABLE',
        'LEGACY_ATTRIBUTION_AMBIGUOUS', 'COVERAGE_UNPROVEN'
    )),
    observed_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT,
    additional_slots INTEGER CHECK (additional_slots >= 0),
    max_reported_results INTEGER CHECK (max_reported_results > 0),
    last_observed_at TEXT,
    FOREIGN KEY (flow_id, link_session_id) REFERENCES link_session(flow_id, link_session_id),
    CHECK (
        (resolved_at IS NULL AND resolution_note IS NULL AND additional_slots IS NULL)
        OR
        (resolved_at IS NOT NULL AND resolution_note IS NOT NULL
         AND length(trim(resolution_note)) > 0 AND additional_slots IS NOT NULL)
    )
) STRICT;
INSERT INTO link_success_observation_new SELECT * FROM link_success_observation;
DROP TABLE link_success_observation;
ALTER TABLE link_success_observation_new RENAME TO link_success_observation;

CREATE TABLE link_observation_adjudication (
    id INTEGER PRIMARY KEY,
    observation_id TEXT NOT NULL REFERENCES link_success_observation(observation_id),
    resolved_at TEXT NOT NULL,
    resolution_note TEXT NOT NULL CHECK (length(trim(resolution_note)) > 0),
    additional_slots INTEGER NOT NULL CHECK (additional_slots >= 0)
) STRICT;
INSERT INTO link_observation_adjudication SELECT * FROM link_adjudication_backup;
DROP TABLE link_adjudication_backup;
