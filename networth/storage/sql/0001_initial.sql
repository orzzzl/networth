-- Networth schema from DESIGN.md section 7, amended by issues #14 and #16.
-- Money uses INTEGER minor units. All *_at / *_as_of values are UTC ISO-8601
-- TEXT with an explicit Z suffix; writers own timestamp validation.

CREATE TABLE institution (
    id INTEGER PRIMARY KEY,
    plaid_institution_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    is_oauth INTEGER NOT NULL CHECK (is_oauth IN (0, 1))
) STRICT;

CREATE TABLE item (
    id INTEGER PRIMARY KEY,
    institution_id INTEGER NOT NULL REFERENCES institution(id),
    plaid_item_id TEXT NOT NULL UNIQUE,
    secret_ref TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('HEALTHY', 'DEGRADED', 'NEEDS_REAUTH', 'REVOKED')),
    status_since TEXT NOT NULL,
    last_successful_sync TEXT,
    last_attempted_sync TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    consent_expiration_time TEXT,
    replaces_item_id INTEGER REFERENCES item(id),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE account (
    id INTEGER PRIMARY KEY,
    item_id INTEGER REFERENCES item(id),
    plaid_account_id TEXT,
    name TEXT NOT NULL,
    official_name TEXT,
    mask TEXT,
    type TEXT NOT NULL,
    subtype TEXT,
    currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    sign INTEGER NOT NULL CHECK (sign IN (-1, 1)),
    freshness_policy TEXT NOT NULL CHECK (
        freshness_policy IN (
            'SYNCED_HOLDINGS',
            'SYNCED_BALANCE',
            'MANUAL_STATIC',
            'MANUAL_QTY_LIVE_PRICE'
        )
    ),
    include_in_net_worth INTEGER NOT NULL CHECK (include_in_net_worth IN (0, 1)),
    lineage_id INTEGER REFERENCES account(id) DEFERRABLE INITIALLY DEFERRED,
    reconciliation_state TEXT NOT NULL CHECK (
        reconciliation_state IN ('NEW', 'CONFIRMED', 'ARCHIVED')
    ),
    superseded_by_account_id INTEGER REFERENCES account(id),
    superseded_at TEXT,
    last_fetch_at TEXT,
    last_source_as_of TEXT,
    created_at TEXT NOT NULL,
    archived_at TEXT,
    CHECK ((item_id IS NULL) = (plaid_account_id IS NULL))
) STRICT;

CREATE UNIQUE INDEX one_plaid_account_per_item
    ON account(item_id, plaid_account_id)
    WHERE plaid_account_id IS NOT NULL;

CREATE TRIGGER account_default_lineage_id
AFTER INSERT ON account
WHEN NEW.lineage_id IS NULL
BEGIN
    UPDATE account SET lineage_id = NEW.id WHERE id = NEW.id;
END;

CREATE TABLE manual_asset (
    account_id INTEGER PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('REAL_PROPERTY', 'EQUITY_SHARES')),
    static_value_minor INTEGER,
    symbol TEXT,
    share_count TEXT,
    valued_as_of TEXT NOT NULL,
    note TEXT,
    CHECK (
        (kind = 'REAL_PROPERTY' AND static_value_minor IS NOT NULL
            AND symbol IS NULL AND share_count IS NULL)
        OR
        (kind = 'EQUITY_SHARES' AND static_value_minor IS NULL
            AND symbol IS NOT NULL AND share_count IS NOT NULL)
    )
) STRICT;

CREATE TABLE sync_run (
    id TEXT PRIMARY KEY NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    "trigger" TEXT NOT NULL,
    ok INTEGER CHECK (ok IN (0, 1)),
    error_summary TEXT,
    CHECK ((finished_at IS NULL) = (ok IS NULL))
) STRICT;

CREATE TABLE observation (
    id INTEGER PRIMARY KEY,
    sync_run_id TEXT NOT NULL REFERENCES sync_run(id),
    account_id INTEGER NOT NULL REFERENCES account(id),
    observed_at TEXT NOT NULL,
    value_minor INTEGER NOT NULL,
    currency TEXT NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    source TEXT NOT NULL CHECK (source IN ('PLAID_HOLDINGS', 'PLAID_BALANCE', 'MANUAL', 'QUOTE')),
    fetched_at TEXT NOT NULL,
    source_as_of TEXT,
    source_clock TEXT NOT NULL,
    is_carried_forward INTEGER NOT NULL CHECK (is_carried_forward IN (0, 1)),
    UNIQUE (sync_run_id, account_id),
    CHECK ((source_clock = 'UNKNOWN') = (source_as_of IS NULL))
) STRICT;

CREATE TABLE snapshot (
    id INTEGER PRIMARY KEY,
    sync_run_id TEXT NOT NULL UNIQUE REFERENCES sync_run(id),
    taken_at TEXT NOT NULL,
    total_net_worth_minor INTEGER NOT NULL,
    total_assets_minor INTEGER NOT NULL,
    total_liabilities_minor INTEGER NOT NULL,
    account_count INTEGER NOT NULL CHECK (account_count >= 0),
    stale_account_count INTEGER NOT NULL CHECK (stale_account_count >= 0),
    unknown_freshness_account_count INTEGER NOT NULL CHECK (
        unknown_freshness_account_count >= 0
    ),
    static_account_count INTEGER NOT NULL CHECK (static_account_count >= 0),
    reauth_account_count INTEGER NOT NULL CHECK (reauth_account_count >= 0),
    unreconciled_account_count INTEGER NOT NULL CHECK (unreconciled_account_count >= 0),
    is_complete INTEGER NOT NULL CHECK (is_complete IN (0, 1)),
    age_state TEXT NOT NULL CHECK (age_state IN ('KNOWN', 'UNKNOWN', 'STATIC_ONLY')),
    as_of TEXT,
    oldest_known_source_as_of TEXT,
    CHECK ((age_state = 'KNOWN') = (as_of IS NOT NULL))
) STRICT;

CREATE TABLE alert (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_id INTEGER REFERENCES item(id),
    account_id INTEGER REFERENCES account(id),
    message TEXT NOT NULL,
    notified_at TEXT,
    acknowledged_at TEXT,
    resolved_at TEXT
) STRICT;

CREATE TABLE pairing (
    id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    key_ref TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (state IN ('ACTIVE', 'REVOKED')),
    revoked_at TEXT,
    CHECK ((state = 'REVOKED') = (revoked_at IS NOT NULL))
) STRICT;

CREATE UNIQUE INDEX one_active_pairing
    ON pairing(state)
    WHERE state = 'ACTIVE';

CREATE TABLE publication (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshot(id),
    pairing_id TEXT NOT NULL REFERENCES pairing(id),
    seq INTEGER NOT NULL UNIQUE CHECK (seq > 0),
    schema_version TEXT NOT NULL,
    published_at TEXT NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)),
    error TEXT,
    CHECK ((ok = 1 AND error IS NULL) OR (ok = 0 AND error IS NOT NULL))
) STRICT;

CREATE TRIGGER publication_seq_must_increase
BEFORE INSERT ON publication
WHEN NEW.seq <= coalesce((SELECT max(seq) FROM publication), 0)
BEGIN
    SELECT raise(ABORT, 'publication.seq must increase monotonically');
END;

CREATE TABLE published_envelope (
    publication_id INTEGER PRIMARY KEY REFERENCES publication(id) ON DELETE CASCADE,
    pairing_id TEXT NOT NULL REFERENCES pairing(id),
    schema_version TEXT NOT NULL,
    seq TEXT NOT NULL,
    published_at TEXT NOT NULL,
    nonce BLOB NOT NULL CHECK (length(nonce) = 12),
    ciphertext BLOB NOT NULL CHECK (length(ciphertext) >= 16),
    is_active INTEGER CHECK (is_active = 1 OR is_active IS NULL)
) STRICT;

CREATE UNIQUE INDEX one_active_envelope
    ON published_envelope(is_active)
    WHERE is_active = 1;

CREATE TABLE backup_archive (
    id INTEGER PRIMARY KEY,
    archive_id TEXT NOT NULL UNIQUE,
    built_at TEXT NOT NULL,
    archive_sha256 TEXT NOT NULL CHECK (length(archive_sha256) = 64),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    manifest_sha256 TEXT NOT NULL CHECK (length(manifest_sha256) = 64),
    pulled_verified_at TEXT,
    pulled_by TEXT,
    verify_error TEXT,
    CHECK ((pulled_verified_at IS NULL) = (pulled_by IS NULL))
) STRICT;

CREATE TABLE backup_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    key_escrow_confirmed_at TEXT,
    last_verified_restore_at TEXT,
    last_verified_restore_archive_id TEXT,
    last_verified_restore_error TEXT
) STRICT;

INSERT INTO backup_state(id) VALUES (1)
ON CONFLICT(id) DO UPDATE SET id = excluded.id;

CREATE TABLE daemon_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    publish_epoch INTEGER NOT NULL DEFAULT 0 CHECK (publish_epoch >= 0),
    epoch_bumped_at TEXT,
    epoch_bumped_reason TEXT
) STRICT;

INSERT INTO daemon_state(id, publish_epoch) VALUES (1, 0)
ON CONFLICT(id) DO UPDATE SET id = excluded.id;

CREATE TABLE link_flow (
    id INTEGER PRIMARY KEY,
    flow_id TEXT NOT NULL UNIQUE,
    secret_ref TEXT,
    minted_at TEXT NOT NULL,
    hosted_url_expires_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    token_exchange_expires_at TEXT,
    session_retention_expires_at TEXT,
    second_copy_verified_at TEXT,
    second_copy_holder TEXT,
    state TEXT NOT NULL CHECK (
        state IN (
            'URL_MINTED',
            'SESSION_STARTED',
            'SESSION_EXITED',
            'SUCCESS_PENDING_EXCHANGE',
            'EXCHANGING',
            'EXCHANGED',
            'EXCHANGE_UNCERTAIN',
            'URL_EXPIRED',
            'TOKEN_EXPIRED',
            'ABANDONED'
        )
    ),
    exchange_claimed_at TEXT,
    exchange_claim_owner TEXT,
    exchange_attempts INTEGER NOT NULL DEFAULT 0 CHECK (exchange_attempts >= 0),
    last_poll_at TEXT,
    poll_error TEXT,
    link_session_id TEXT UNIQUE,
    item_id TEXT UNIQUE,
    material_reaped_at TEXT,
    secret_ref_cleared_at TEXT,
    CHECK ((second_copy_verified_at IS NULL) = (second_copy_holder IS NULL)),
    CHECK ((exchange_claimed_at IS NULL) = (exchange_claim_owner IS NULL))
) STRICT;

CREATE TABLE link_exchange_attempt (
    link_flow_id INTEGER NOT NULL REFERENCES link_flow(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    request_id TEXT,
    PRIMARY KEY (link_flow_id, attempt_number)
) STRICT;

CREATE VIEW stranded_link_flow AS
SELECT id, flow_id, state, link_session_id, item_id,
       token_exchange_expires_at, session_retention_expires_at
FROM link_flow
WHERE state IN ('TOKEN_EXPIRED', 'EXCHANGE_UNCERTAIN');
