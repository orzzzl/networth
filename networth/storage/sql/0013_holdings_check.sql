-- An account valued from its holdings, checked against its institution's own
-- total, once per sync run.
--
-- The total in a holdings reply can trail its holdings by a session. While it
-- does, the holdings are the account's value only if that total confirms them:
-- it must equal the holdings revalued at one session's close prices (Plaid's
-- own `close_price` for each security). One row records which session the
-- total was found to describe, and how:
--
--   CONFIRMED   the total adds up to the holdings at `close_session`'s prices;
--   UNCHANGED   the total is the same figure an earlier check established, so
--               it still describes that check's session;
--   UNCONFIRMED no check has established what this total describes, and the
--               account is valued by the total instead.
--
-- `total_minor` is what UNCHANGED compares, and the two close columns keep the
-- comparison itself so a later read can measure how far apart they were.
CREATE TABLE holdings_check (
    id INTEGER PRIMARY KEY,
    sync_run_id TEXT NOT NULL REFERENCES sync_run(id),
    account_id INTEGER NOT NULL REFERENCES account(id),
    checked_at TEXT NOT NULL,
    total_minor INTEGER NOT NULL,
    close_session TEXT,
    at_close_minor INTEGER,
    outcome TEXT NOT NULL CHECK (outcome IN ('CONFIRMED', 'UNCHANGED', 'UNCONFIRMED')),
    session TEXT,
    UNIQUE (sync_run_id, account_id),
    CHECK ((close_session IS NULL) = (at_close_minor IS NULL)),
    CHECK ((outcome = 'UNCONFIRMED') = (session IS NULL)),
    CHECK (outcome != 'CONFIRMED' OR session = close_session)
) STRICT;
