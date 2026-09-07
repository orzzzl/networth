-- Task 03a: durable probe generation and visible forced-command refusals.
-- These belong to the singleton backup_state row so the dispatcher and doctor
-- share one monotonic source of truth.  The probe timestamp is VPS-local and is
-- used only by the VPS to enforce its cooldown; it is never compared with a Mac
-- clock.

ALTER TABLE backup_state ADD COLUMN probe_generation INTEGER NOT NULL DEFAULT 0
    CHECK (probe_generation >= 0);
ALTER TABLE backup_state ADD COLUMN probe_built_at TEXT;
ALTER TABLE backup_state ADD COLUMN probe_refusal_count INTEGER NOT NULL DEFAULT 0
    CHECK (probe_refusal_count >= 0);
ALTER TABLE backup_state ADD COLUMN dispatch_rejection_count INTEGER NOT NULL DEFAULT 0
    CHECK (dispatch_rejection_count >= 0);
