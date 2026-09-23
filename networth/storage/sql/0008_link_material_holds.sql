-- Material safety is separate from slot adjudication. Automatic passes cannot
-- reinterpret an unreadable or ambiguously attributed credential on a later run.
CREATE TABLE link_material_hold (
    hold_id TEXT PRIMARY KEY,
    flow_id TEXT NOT NULL REFERENCES link_request(flow_id),
    reason TEXT NOT NULL CHECK (reason IN ('UNVERIFIED_MATERIAL', 'ATTRIBUTION_AMBIGUOUS')),
    observed_at TEXT NOT NULL,
    resolved_at TEXT,
    audit_id TEXT,
    CHECK ((resolved_at IS NULL) = (audit_id IS NULL))
) STRICT;
CREATE UNIQUE INDEX link_material_open_hold ON link_material_hold(flow_id, reason)
    WHERE resolved_at IS NULL;
