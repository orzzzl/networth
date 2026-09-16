-- Task 20: a publication row is evidence of one committed success, not an
-- attempted write.  Failed Publisher transactions roll back before a row is
-- visible, so `ok` and `error` described states the table must never contain.
--
-- Rebuild both sides of the publication -> published_envelope foreign key in
-- one migration.  Copying the child aside before dropping the old parent is
-- what prevents ON DELETE CASCADE from discarding the active ciphertext.  Only
-- successful legacy publication rows survive; an envelope attached to any
-- other row makes the copy fail closed instead of silently losing ciphertext.

CREATE TABLE publication_v5 (
    id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES snapshot(id),
    pairing_id TEXT NOT NULL REFERENCES pairing(id),
    seq INTEGER NOT NULL UNIQUE CHECK (seq > 0),
    schema_version TEXT NOT NULL,
    published_at TEXT NOT NULL
) STRICT;

INSERT INTO publication_v5(
    id, snapshot_id, pairing_id, seq, schema_version, published_at
)
SELECT id, snapshot_id, pairing_id, seq, schema_version, published_at
FROM publication
WHERE ok = 1 AND error IS NULL;

CREATE TABLE published_envelope_v5 (
    publication_id INTEGER PRIMARY KEY REFERENCES publication_v5(id) ON DELETE CASCADE,
    pairing_id TEXT NOT NULL REFERENCES pairing(id),
    schema_version TEXT NOT NULL,
    seq TEXT NOT NULL,
    published_at TEXT NOT NULL,
    nonce BLOB NOT NULL CHECK (length(nonce) = 12),
    ciphertext BLOB NOT NULL CHECK (length(ciphertext) >= 16),
    is_active INTEGER CHECK (is_active = 1 OR is_active IS NULL)
) STRICT;

INSERT INTO published_envelope_v5(
    publication_id, pairing_id, schema_version, seq, published_at,
    nonce, ciphertext, is_active
)
SELECT publication_id, pairing_id, schema_version, seq, published_at,
       nonce, ciphertext, is_active
FROM published_envelope;

DROP TABLE published_envelope;
DROP TABLE publication;

ALTER TABLE publication_v5 RENAME TO publication;
ALTER TABLE published_envelope_v5 RENAME TO published_envelope;

CREATE UNIQUE INDEX one_active_envelope
    ON published_envelope(is_active)
    WHERE is_active = 1;

CREATE TRIGGER publication_seq_must_increase
BEFORE INSERT ON publication
WHEN NEW.seq <= coalesce((SELECT max(seq) FROM publication), 0)
BEGIN
    SELECT raise(ABORT, 'publication.seq must increase monotonically');
END;
