"""07a boundary A: real SQLite/TokenStore, synthetic metadata, no live Plaid."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth.item_budget import read_item_budget
from networth.link_finalization import FinalizationError, finalize_durable_result
from networth.plaid.client import ItemInstitution, PlaidCallError
from networth.storage import migrate
from networth.tokenstore import SecretKind, SecretRecord, TokenStore, UnverifiedMaterial

FLOW = "1" * 32
RESULT = "2" * 32
OTHER = "3" * 32
NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)
STAMP = "2026-09-22T12:00:00Z"
TOKEN = "synthetic-finalization-material"
ITEM = "synthetic-finalization-item"
INSTITUTION = "synthetic-finalization-institution"
NAME = "Synthetic metadata sentinel"


class RecordingStore(TokenStore):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.reconciled: list[str] = []

    def reconcile(self, flow_id: str) -> SecretRecord | None:
        self.reconciled.append(flow_id)
        return super().reconcile(flow_id)


class Metadata:
    def __init__(self) -> None:
        self.calls = 0
        self.outcome: ItemInstitution | Exception = ItemInstitution(ITEM, INSTITUTION, NAME, True)

    def item_institution(
        self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution:
        assert access_token == TOKEN
        assert expected_item_id == ITEM
        assert country_codes == ("US",)
        self.calls += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
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
    add_result(connection, RESULT)
    connection.commit()
    yield connection
    connection.close()


def add_result(db: sqlite3.Connection, result: str, *, item: str = ITEM) -> None:
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, item_id) "
        "VALUES (?, ?, ?, 'EXCHANGING', ?)",
        (result, FLOW, result * 2, item),
    )


def finish(
    db: sqlite3.Connection,
    store: TokenStore,
    metadata: Metadata,
    ref: str,
    *,
    result: str = RESULT,
) -> int:
    return finalize_durable_result(
        db, store, metadata, result_id=result, secret_ref=ref, country_codes=("US",), now=NOW
    )


def state(db: sqlite3.Connection, result: str = RESULT) -> str:
    return str(
        db.execute("SELECT state FROM link_result WHERE result_id = ?", (result,)).fetchone()[0]
    )


@pytest.mark.parametrize("pending", [False, True])
def test_restart_after_metadata_outage_reconciles_and_finalizes(
    db: sqlite3.Connection, tmp_path: Path, pending: bool, capsys: Any, caplog: Any
) -> None:
    store = RecordingStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    if pending:
        (store.directory / f"{ref}.json").rename(store.directory / f".{ref}.pending")
    metadata = Metadata()
    metadata.outcome = PlaidCallError("metadata read unavailable")
    with pytest.raises(PlaidCallError):
        finish(db, store, metadata, ref)
    assert state(db) == "EXCHANGING"
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 0
    assert store.get(ref).reveal() == TOKEN
    assert store.reconciled == [RESULT]

    # A fresh process-equivalent connection/store, not an in-memory continuation.
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(restarted)
    recovered_store = RecordingStore(store.directory)
    recovered_metadata = Metadata()
    try:
        item = finish(restarted, recovered_store, recovered_metadata, ref)
        assert recovered_store.reconciled == [RESULT]
        assert recovered_metadata.calls == 1
        assert state(restarted) == "EXCHANGED"
        assert restarted.execute(
            "SELECT secret_ref, status, last_successful_sync FROM item WHERE id = ?", (item,)
        ).fetchone() == (ref, "DEGRADED", None)
        assert restarted.execute(
            "SELECT item_id, secret_ref FROM link_result WHERE result_id = ?", (RESULT,)
        ).fetchone() == (ITEM, ref)
        assert restarted.execute("SELECT name, is_oauth FROM institution").fetchone() == (NAME, 1)
        assert finish(restarted, recovered_store, recovered_metadata, ref) == item
        assert recovered_metadata.calls == 1  # idempotent, without another network read
    finally:
        restarted.close()
    output = capsys.readouterr()
    assert NAME not in output.out + output.err + caplog.text
    assert TOKEN not in "\n".join(db.iterdump())


def test_same_item_preserves_both_credentials_and_existing_health(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    item = finish(db, store, Metadata(), ref)
    db.execute("UPDATE item SET status = 'NEEDS_REAUTH'")
    add_result(db, OTHER)
    db.commit()
    second = store.put(SecretKind.ACCESS_TOKEN, OTHER, TOKEN, item_id=ITEM)
    assert finish(db, store, Metadata(), second, result=OTHER) == item
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 1
    assert db.execute("SELECT secret_ref, status FROM item").fetchone() == (ref, "NEEDS_REAUTH")
    assert (
        db.execute("SELECT secret_ref FROM link_result WHERE result_id = ?", (OTHER,)).fetchone()[0]
        == second
    )
    assert store.get(ref).reveal() == store.get(second).reveal() == TOKEN
    assert read_item_budget(db).spent_count == 1


def test_distinct_item_keeps_independent_credentials(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    first = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    finish(db, store, Metadata(), first)
    add_result(db, OTHER, item="synthetic-second-item")
    db.commit()
    second = store.put(SecretKind.ACCESS_TOKEN, OTHER, TOKEN, item_id="synthetic-second-item")

    class SecondMetadata(Metadata):
        def item_institution(
            self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
        ) -> ItemInstitution:
            return ItemInstitution("synthetic-second-item", INSTITUTION, NAME, True)

    finish(db, store, SecondMetadata(), second, result=OTHER)
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 2
    assert {r[0] for r in db.execute("SELECT secret_ref FROM item")} == {first, second}
    assert read_item_budget(db).spent_count == 2


def test_commit_failure_rolls_back_institution_item_and_result(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    db.execute(
        "CREATE TRIGGER synthetic_failure BEFORE UPDATE OF state ON link_result "
        "BEGIN SELECT RAISE(ABORT, 'Synthetic metadata sentinel'); END"
    )
    db.commit()
    with pytest.raises(FinalizationError) as exc:
        finish(db, store, Metadata(), ref)
    assert NAME not in str(exc.value)
    assert state(db) == "EXCHANGING"
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM institution").fetchone()[0] == 0
    assert store.get(ref).reveal() == TOKEN
    db.execute("DROP TRIGGER synthetic_failure")
    db.commit()
    finish(db, store, Metadata(), ref)
    assert state(db) == "EXCHANGED"


@pytest.mark.parametrize(
    "failure", ["absent", "unverified", "wrong-item", "material-as-id", "foreign"]
)
def test_refusals_before_metadata(
    db: sqlite3.Connection, tmp_path: Path, monkeypatch: Any, failure: str
) -> None:
    store = TokenStore(tmp_path / "tokens")
    material_id = OTHER if failure == "foreign" else RESULT
    item_id = {"wrong-item": "synthetic-other", "material-as-id": TOKEN}.get(failure, ITEM)
    ref = store.put(SecretKind.ACCESS_TOKEN, material_id, TOKEN, item_id=item_id)
    if failure == "absent":
        store.delete(ref)
    if failure == "unverified":

        def unverified(flow: str) -> None:
            raise UnverifiedMaterial("durability unavailable")

        monkeypatch.setattr(store, "reconcile", unverified)
    metadata = Metadata()
    with pytest.raises((FinalizationError, UnverifiedMaterial)):
        finish(db, store, metadata, ref)
    assert metadata.calls == 0
    assert state(db) == "EXCHANGING"
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 0


@pytest.mark.parametrize("mapped", [False, True])
def test_legacy_reference_requires_explicit_row_mapping(
    db: sqlite3.Connection, tmp_path: Path, mapped: bool
) -> None:
    db.execute(
        "INSERT INTO link_flow(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'EXCHANGING')",
        (FLOW, STAMP, STAMP),
    )
    if mapped:
        db.execute("UPDATE link_request SET legacy_link_flow_id = 1")
        db.execute("UPDATE link_result SET legacy_link_flow_id = 1")
    db.commit()
    store = RecordingStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, TOKEN, item_id=ITEM)
    if mapped:
        finish(db, store, Metadata(), ref)
        assert store.reconciled == [FLOW]
        assert db.execute("SELECT secret_ref FROM item").fetchone()[0] == ref
    else:
        with pytest.raises(FinalizationError, match="not attributed"):
            finish(db, store, Metadata(), ref)


def test_concurrent_attribution_change_refuses_commit(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)

    class ConcurrentMetadata(Metadata):
        def item_institution(
            self, access_token: str, *, expected_item_id: str, country_codes: Sequence[str]
        ) -> ItemInstitution:
            other = sqlite3.connect(tmp_path / "synthetic.sqlite")
            other.execute("UPDATE link_result SET state = 'EXCHANGE_UNCERTAIN'")
            other.commit()
            other.close()
            return ItemInstitution(ITEM, INSTITUTION, NAME, True)

    with pytest.raises(FinalizationError, match="changed"):
        finish(db, store, ConcurrentMetadata(), ref)
    assert state(db) == "EXCHANGE_UNCERTAIN"
    assert db.execute("SELECT count(*) FROM item").fetchone()[0] == 0


@pytest.mark.parametrize(
    "state_name", ["SUCCESS_PENDING_EXCHANGE", "TOKEN_EXPIRED", "EXCHANGE_UNCERTAIN"]
)
def test_non_finalizable_state_does_not_read_material_or_metadata(
    db: sqlite3.Connection, tmp_path: Path, state_name: str
) -> None:
    db.execute("UPDATE link_result SET state = ?", (state_name,))
    db.commit()
    store = RecordingStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    metadata = Metadata()
    with pytest.raises(FinalizationError, match="not ready"):
        finish(db, store, metadata, ref)
    assert not store.reconciled
    assert metadata.calls == 0


def test_existing_item_institution_conflict_preserves_original(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    finish(db, store, Metadata(), ref)
    add_result(db, OTHER)
    db.commit()
    second = store.put(SecretKind.ACCESS_TOKEN, OTHER, TOKEN, item_id=ITEM)
    metadata = Metadata()
    metadata.outcome = ItemInstitution(ITEM, "synthetic-other-institution", NAME, True)
    with pytest.raises(FinalizationError, match="institution identity disagree"):
        finish(db, store, metadata, second, result=OTHER)
    assert state(db, OTHER) == "EXCHANGING"
    assert db.execute("SELECT plaid_institution_id FROM institution").fetchall() == [(INSTITUTION,)]
    assert store.get(second).reveal() == TOKEN


def test_already_exchanged_orphan_is_refused(db: sqlite3.Connection, tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    db.execute("UPDATE link_result SET state = 'EXCHANGED', secret_ref = ?", (ref,))
    db.commit()
    metadata = Metadata()
    with pytest.raises(FinalizationError, match="missing its committed Item"):
        finish(db, store, metadata, ref)
    assert metadata.calls == 0


def test_transaction_owned_by_caller_is_not_committed(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path / "tokens")
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, TOKEN, item_id=ITEM)
    db.execute("UPDATE link_result SET item_id = ?", (ITEM,))
    with pytest.raises(FinalizationError, match="active transaction"):
        finish(db, store, Metadata(), ref)
    assert db.in_transaction
    db.rollback()
