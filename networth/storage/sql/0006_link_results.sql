-- Task 07a: requests, observed sessions and successful results have independent
-- identities. Keep legacy rows/attempts for audit and credential-name recovery.
-- The migration runner wraps backfill and view replacement in one transaction.
CREATE TABLE link_request (
    flow_id TEXT PRIMARY KEY,
    legacy_link_flow_id INTEGER UNIQUE REFERENCES link_flow(id),
    secret_ref TEXT,
    minted_at TEXT NOT NULL,
    hosted_url_expires_at TEXT NOT NULL,
    second_copy_verified_at TEXT,
    second_copy_holder TEXT,
    state TEXT NOT NULL CHECK (state IN ('URL_MINTED', 'URL_EXPIRED', 'ABANDONED')),
    last_poll_at TEXT,
    poll_error TEXT,
    polling_closed_at TEXT,
    material_reaped_at TEXT,
    secret_ref_cleared_at TEXT,
    CHECK ((second_copy_verified_at IS NULL) = (second_copy_holder IS NULL))
) STRICT;

CREATE TABLE link_session (
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    link_session_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('SESSION_STARTED', 'SESSION_EXITED')),
    started_at TEXT,
    finished_at TEXT,
    PRIMARY KEY (flow_id, link_session_id)
) STRICT;

CREATE TABLE link_result (
    result_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    link_session_id TEXT,
    legacy_link_flow_id INTEGER UNIQUE REFERENCES link_flow(id),
    token_digest TEXT,
    state TEXT NOT NULL CHECK (state IN (
        'SUCCESS_PENDING_EXCHANGE', 'EXCHANGING', 'EXCHANGED',
        'TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN'
    )),
    finished_at TEXT,
    token_exchange_expires_at TEXT,
    session_retention_expires_at TEXT,
    item_id TEXT,
    secret_ref TEXT,
    exchange_claimed_at TEXT,
    exchange_claim_owner TEXT,
    exchange_attempts INTEGER NOT NULL DEFAULT 0 CHECK (exchange_attempts >= 0),
    UNIQUE (flow_id, token_digest),
    FOREIGN KEY (flow_id, link_session_id) REFERENCES link_session(flow_id, link_session_id),
    CHECK ((exchange_claimed_at IS NULL) = (exchange_claim_owner IS NULL)),
    CHECK (token_digest IS NOT NULL OR legacy_link_flow_id IS NOT NULL)
) STRICT;

CREATE TABLE link_result_attempt (
    result_id TEXT NOT NULL REFERENCES link_result(result_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    request_id TEXT,
    PRIMARY KEY (result_id, attempt_number)
) STRICT;

-- A hold is evidence even when there is no result row. Resolution is explicit
-- adjudication, retained alongside the original observation, never deletion.
-- additional_slots is the adjudicated cost beyond Items/results already counted.
CREATE TABLE link_success_observation (
    observation_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    link_session_id TEXT,
    reason TEXT NOT NULL CHECK (reason IN (
        'MISSING_SESSION_ID', 'MISSING_PUBLIC_TOKEN', 'DIGEST_KEY_UNAVAILABLE',
        'LEGACY_ATTRIBUTION_AMBIGUOUS'
    )),
    observed_at TEXT NOT NULL,
    resolved_at TEXT,
    resolution_note TEXT,
    additional_slots INTEGER CHECK (additional_slots >= 0),
    FOREIGN KEY (flow_id, link_session_id) REFERENCES link_session(flow_id, link_session_id),
    CHECK (
        (resolved_at IS NULL AND resolution_note IS NULL AND additional_slots IS NULL)
        OR
        (resolved_at IS NOT NULL AND resolution_note IS NOT NULL
         AND length(trim(resolution_note)) > 0 AND additional_slots IS NOT NULL)
    )
) STRICT;

INSERT INTO link_request (
    flow_id, legacy_link_flow_id, secret_ref, minted_at, hosted_url_expires_at,
    second_copy_verified_at, second_copy_holder, state, last_poll_at, poll_error,
    material_reaped_at, secret_ref_cleared_at
)
SELECT flow_id, id, secret_ref, minted_at, hosted_url_expires_at,
       second_copy_verified_at, second_copy_holder,
       CASE WHEN state IN ('URL_EXPIRED', 'ABANDONED') THEN state ELSE 'URL_MINTED' END,
       last_poll_at, poll_error, material_reaped_at, secret_ref_cleared_at
FROM link_flow;

INSERT INTO link_session (flow_id, link_session_id, state, started_at, finished_at)
SELECT flow_id, link_session_id,
       CASE WHEN state = 'SESSION_EXITED' THEN state ELSE 'SESSION_STARTED' END,
       started_at, finished_at
FROM link_flow WHERE link_session_id IS NOT NULL;

INSERT INTO link_result (
    result_id, flow_id, link_session_id, legacy_link_flow_id, state, finished_at,
    token_exchange_expires_at, session_retention_expires_at, item_id,
    exchange_claimed_at, exchange_claim_owner, exchange_attempts
)
SELECT lower(hex(randomblob(16))), flow_id, link_session_id, id, state, finished_at,
       token_exchange_expires_at, session_retention_expires_at, item_id,
       exchange_claimed_at, exchange_claim_owner, exchange_attempts
FROM link_flow WHERE state IN (
    'SUCCESS_PENDING_EXCHANGE', 'EXCHANGING', 'EXCHANGED',
    'TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN'
);

INSERT INTO link_result_attempt (result_id, attempt_number, request_id)
SELECT r.result_id, a.attempt_number, a.request_id
FROM link_exchange_attempt AS a
JOIN link_result AS r ON r.legacy_link_flow_id = a.link_flow_id;

-- New results and unmigrated legacy evidence form one projection. The latter
-- also keeps imported legacy evidence visible; migrated parents never count
-- twice. New code must write results, not the archived scalar table.
CREATE VIEW link_success_evidence AS
SELECT result_id, flow_id, state, link_session_id, item_id,
       token_exchange_expires_at, session_retention_expires_at
FROM link_result
UNION ALL
SELECT NULL, f.flow_id, f.state, f.link_session_id, f.item_id,
       f.token_exchange_expires_at, f.session_retention_expires_at
FROM link_flow AS f
WHERE f.state IN (
    'SUCCESS_PENDING_EXCHANGE', 'EXCHANGING', 'EXCHANGED',
    'TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN'
)
AND NOT EXISTS (SELECT 1 FROM link_request AS q WHERE q.legacy_link_flow_id = f.id);

DROP VIEW stranded_link_flow;
CREATE VIEW stranded_link_flow AS
SELECT result_id, flow_id, state, link_session_id, item_id,
       token_exchange_expires_at, session_retention_expires_at
FROM link_success_evidence WHERE state IN ('TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN');
