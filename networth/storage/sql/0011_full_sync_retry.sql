-- Data-fetch retry state only. Link exchanges must never use this policy.
CREATE TABLE full_sync_retry (
    item_id INTEGER PRIMARY KEY REFERENCES item(id),
    failures INTEGER NOT NULL CHECK (failures BETWEEN 1 AND 4),
    next_attempt_at TEXT NOT NULL
) STRICT;
