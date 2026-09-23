"""Finalization contract guards using synthetic SQLite, TokenStore and metadata."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth.link_finalization import FinalizationError, finalize_durable_result
from networth.plaid.client import ItemInstitution
from networth.storage import migrate
from networth.tokenstore import SecretKind, TokenStore, secret_ref_for

FLOW = "1" * 32
RESULT = "2" * 32
NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
STAMP = "2026-09-22T12:00:00Z"
TOKEN = "synthetic-finalization-material"
ITEM = "synthetic-finalization-item"
INSTITUTION = "synthetic-finalization-institution"
NAME = "Synthetic metadata sentinel"


class Metadata:
    """A conforming MetadataClient. ``outcome`` is what the seam returns."""

    def __init__(self, outcome: ItemInstitution | None = None) -> None:
        self.calls = 0
        self.outcome = outcome or ItemInstitution(ITEM, INSTITUTION, NAME, True)

    def item_institution(
        self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution:
        self.calls += 1
        return self.outcome


@pytest.fixture
def db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(connection)
    connection.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'URL_MINTED')",
        (FLOW, STAMP, STAMP),
    )
    connection.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, item_id) "
        "VALUES (?, ?, ?, 'EXCHANGING', ?)",
        (RESULT, FLOW, RESULT * 2, ITEM),
    )
    connection.commit()
    yield connection
    connection.close()


def finish(
    db: sqlite3.Connection,
    store: TokenStore,
    metadata: Any,
    ref: str,
    *,
    now: datetime = NOW,
) -> int:
    return finalize_durable_result(
        db, store, metadata, result_id=RESULT, secret_ref=ref, country_codes=("US",), now=now
    )


def stored(db: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in db.execute("SELECT plaid_item_id, secret_ref, status FROM item")]


# --- the caller contract ----------------------------------------------------


def test_naive_timestamp_is_refused_rather_than_read_as_local_time(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """``astimezone`` would silently read a naive ``now`` as local time.

    Nothing rejects the value downstream: it becomes ``status_since`` and
    ``created_at`` on the Item, off by this machine's UTC offset, with no later
    reader able to tell.  Guard: the aware-timestamp check.
    """
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    metadata = Metadata()
    with pytest.raises(FinalizationError, match="aware timestamp"):
        finish(db, store, metadata, ref, now=datetime(2026, 9, 22, 12))
    assert metadata.calls == 0
    assert stored(db) == []


def test_a_link_token_reference_cannot_become_an_items_credential(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """The link token for a flow is stored under that same flow id.

    So ``link-token.<flow>`` parses, and its flow id passes the attribution
    check exactly like the access-token ref would.  ``reconcile()`` then looks
    the ACCESS-token record up regardless, the correct material is read, the
    metadata call succeeds — and the row committed against the Item records a
    *link-token* ref as the credential to sync with.  Guard: the SecretKind
    check.  Without it this test does not raise; it commits that row.
    """
    store = TokenStore(tmp_path / "tokens")
    store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    store.put(SecretKind.LINK_TOKEN, RESULT, "synthetic-link-token")
    wrong_kind = secret_ref_for(SecretKind.LINK_TOKEN, RESULT)

    with pytest.raises(FinalizationError, match="not attributed"):
        finish(db, store, Metadata(), wrong_kind)
    assert stored(db) == []


def test_material_without_a_captured_item_identity_is_refused(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """``item_id`` on a stored record is optional — material can be written
    before the exchange identifiers are captured.  Finalizing against it would
    ask the metadata seam about ``expected_item_id=None`` and commit whatever
    came back.  Guard: the ``record.item_id is None`` check.
    """
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN)
    metadata = Metadata()
    with pytest.raises(FinalizationError, match="Item identity is unavailable"):
        finish(db, store, metadata, ref)
    assert metadata.calls == 0
    assert stored(db) == []


def test_result_already_recording_a_different_credential_is_refused(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Both an attributed result ref and its uniquely mapped legacy ref satisfy
    the attribution check, so a result that already names one can be finalized
    with the other.  The Item then commits under the second while the first
    stays attributed to the same result.  Guard: the captured-ref conflict
    check.  (Only this component writes ``link_result.secret_ref`` today, and
    only with EXCHANGED — the guard is for the worker that will write it at
    attribution time, which is why the row is set up directly here.)
    """
    db.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'EXCHANGING')",
        (FLOW, STAMP, STAMP),
    )
    db.execute("UPDATE link_request SET legacy_link_flow_id = 1")
    db.execute(
        "UPDATE link_result SET legacy_link_flow_id = 1, secret_ref = ?",
        (secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW),),
    )
    db.commit()
    store = TokenStore(tmp_path / "tokens")
    store.put(SecretKind.ACCESS_TOKEN, FLOW, TOKEN, item_id=ITEM)
    other = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)

    metadata = Metadata()
    with pytest.raises(FinalizationError, match="already records a different credential"):
        finish(db, store, metadata, other)
    assert metadata.calls == 0
    assert stored(db) == []


@pytest.mark.parametrize("missing", ["item_id", "secret_ref"])
def test_exchanged_result_without_captured_identity_is_not_reported_finalized(
    db: sqlite3.Connection, tmp_path: Path, missing: str
) -> None:
    """The idempotent retry path returns an Item row id without re-reading
    metadata.  An EXCHANGED row whose ``item_id``/``secret_ref`` were never
    captured has no evidence tying it to the Item it would name, and returning
    one says finalization happened.  Guard: the two identity disjuncts in the
    EXCHANGED branch (reachable only when the captured column is NULL — the
    non-NULL cases are caught earlier, which is worth knowing when reading it).
    """
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    finish(db, store, Metadata(), ref)
    assert len(stored(db)) == 1
    assert missing in ("item_id", "secret_ref")
    db.execute(f"UPDATE link_result SET {missing} = NULL WHERE result_id = ?", (RESULT,))
    db.commit()

    metadata = Metadata()
    with pytest.raises(FinalizationError, match="missing its committed Item or reference"):
        finish(db, store, metadata, ref)
    assert metadata.calls == 0


# --- the metadata the component verifies for itself -------------------------
#
# PlaidClient.item_institution checks these too and is tested for them, but the
# component takes a MetadataClient Protocol: the checks below are the only ones
# that hold for an implementation other than that one, and the task file states
# them as this component's own ("verifies both returned identities, requires an
# institution name and a real boolean OAuth flag").


@pytest.mark.parametrize(
    ("label", "outcome"),
    [
        ("other-item", ItemInstitution("synthetic-other-item", INSTITUTION, NAME, True)),
        ("no-institution-id", ItemInstitution(ITEM, "", NAME, True)),
        ("no-name", ItemInstitution(ITEM, INSTITUTION, "", True)),
        ("material-as-institution", ItemInstitution(ITEM, TOKEN, NAME, True)),
        ("material-as-name", ItemInstitution(ITEM, INSTITUTION, TOKEN, True)),
        ("oauth-not-a-bool", ItemInstitution(ITEM, INSTITUTION, NAME, 1)),  # type: ignore[arg-type]
    ],
)
def test_unattributable_metadata_commits_nothing(
    db: sqlite3.Connection, tmp_path: Path, label: str, outcome: ItemInstitution
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    with pytest.raises(FinalizationError, match="not attributable"):
        finish(db, store, Metadata(outcome), ref)
    assert stored(db) == []
    assert db.execute("SELECT count(*) FROM institution").fetchone()[0] == 0
    assert (
        db.execute("SELECT state FROM link_result WHERE result_id = ?", (RESULT,)).fetchone()[0]
        == "EXCHANGING"
    )
