-- Task 16: success alone does not prove a full sync. Manual revisions and
-- quote-only runs also use sync_run. Preserve existing rows as OTHER instead
-- of guessing their work from an unconstrained trigger string or a snapshot.
-- The scheduler must opt in at run creation and set ok only after full-sync
-- completion; a crash or a failed run never advances the success clock.
ALTER TABLE sync_run ADD COLUMN kind TEXT NOT NULL DEFAULT 'OTHER'
    CHECK (kind IN ('OTHER', 'FULL_SYNC'));
