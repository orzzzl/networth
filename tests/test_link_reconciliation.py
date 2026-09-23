"""All credential candidates are synthetic; real SQLite and TokenStore restarts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth.cli import main
from networth.item_budget import ItemBudgetError, read_item_budget
from networth.link_reconciliation import (
    ReconciledRequest,
    ReconciliationError,
    adjudicate_material_hold,
    reconcile_request,
)
from networth.plaid.environment import Paths
from networth.storage import migrate
from networth.tokenstore import (
    InvalidSecretRef,
    SecretKind,
    SecretRecord,
    TokenStore,
    UnverifiedMaterial,
)

FLOW = "1" * 32
RESULT = "2" * 32
OTHER = "3" * 32
AUDIT = "4" * 32
NOW = datetime(2026, 9, 23, 12, tzinfo=UTC)
STAMP = "2026-09-23T12:00:00Z"
MATERIAL = "synthetic-material-sentinel"
ITEM = "synthetic-item-sentinel"


class RecordingStore(TokenStore):
    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.calls: list[str] = []
        self.broken: set[str] = set()

    def reconcile(self, flow_id: str) -> SecretRecord | None:
        self.calls.append(flow_id)
        if flow_id in self.broken:
            raise UnverifiedMaterial("synthetic-secret-exception-sentinel")
        return super().reconcile(flow_id)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[tuple[sqlite3.Connection, RecordingStore]]:
    db = sqlite3.connect(tmp_path / "synthetic.sqlite")
    migrate(db)
    db.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'URL_MINTED')",
        (FLOW, STAMP, STAMP),
    )
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
        "VALUES (?, ?, ?, 'EXCHANGING')",
        (RESULT, FLOW, "synthetic-digest"),
    )
    db.commit()
    yield db, RecordingStore(tmp_path / "tokens")
    db.close()


def scan(setup: tuple[sqlite3.Connection, TokenStore]) -> ReconciledRequest:
    return reconcile_request(setup[0], setup[1], flow_id=FLOW, now=NOW)


def legacy(db: sqlite3.Connection, ref: str | None = None) -> None:
    cur = db.execute(
        "INSERT INTO link_flow(flow_id, secret_ref, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, ?, 'EXCHANGING')",
        (FLOW, ref, STAMP, STAMP),
    )
    db.execute("UPDATE link_request SET legacy_link_flow_id = ?", (cur.lastrowid,))
    db.execute("UPDATE link_result SET legacy_link_flow_id = ?", (cur.lastrowid,))
    db.commit()


def resolve(db: sqlite3.Connection, hold_id: str) -> None:
    adjudicate_material_hold(db, hold_id=hold_id, audit_id=AUDIT, now=NOW, reviewed=True)


def test_absence_requires_all_names_even_without_references(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    legacy(db)
    assert scan(setup) == ReconciledRequest((), (RESULT,), ())
    assert store.calls == [FLOW, RESULT]
    assert db.execute("SELECT state FROM link_result").fetchone() == ("EXCHANGING",)


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("name", [FLOW, RESULT])
def test_restart_recovers_final_and_pending_under_both_names(
    setup: tuple[sqlite3.Connection, RecordingStore], tmp_path: Path, pending: bool, name: str
) -> None:
    db, store = setup
    legacy(db)
    ref = store.put(SecretKind.ACCESS_TOKEN, name, MATERIAL, item_id=ITEM)
    if pending:
        (store.directory / (ref + ".json")).rename(store.directory / ("." + ref + ".pending"))
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    recovered = RecordingStore(store.directory)
    try:
        outcome = scan((restarted, recovered))
        assert recovered.calls == [FLOW, RESULT]
        assert outcome.hold_ids == ()
        assert outcome.absent_result_ids == ()
        assert [(m.result_id, m.item_id, m.secret_refs) for m in outcome.materials] == [
            (RESULT, ITEM, (ref,))
        ]
        assert recovered.get(ref).reveal() == MATERIAL
        assert "sentinel" not in repr(outcome)
    finally:
        restarted.close()


@pytest.mark.parametrize("same_item", [False, True])
def test_two_names_are_both_preserved_and_identity_controls_attribution(
    setup: tuple[sqlite3.Connection, RecordingStore], same_item: bool
) -> None:
    db, store = setup
    legacy(db)
    a = store.put(SecretKind.ACCESS_TOKEN, FLOW, MATERIAL, item_id=ITEM)
    b = store.put(
        SecretKind.ACCESS_TOKEN,
        RESULT,
        "synthetic-second-material",
        item_id=ITEM if same_item else "synthetic-distinct-item",
    )
    outcome = scan(setup)
    assert store.calls == [FLOW, RESULT]
    if same_item:
        assert outcome.materials[0].secret_refs == (a, b)
        assert not outcome.hold_ids
    else:
        assert outcome.hold_ids
        assert not outcome.materials and not outcome.absent_result_ids
        with pytest.raises(ItemBudgetError, match="material hold"):
            read_item_budget(db)
    assert store.get(a).reveal() == MATERIAL
    assert store.get(b).reveal() == "synthetic-second-material"


@pytest.mark.parametrize("name", [FLOW, RESULT])
@pytest.mark.parametrize("later_found", [False, True])
def test_unverified_hold_survives_restart_and_cannot_be_reinterpreted(
    setup: tuple[sqlite3.Connection, RecordingStore], tmp_path: Path, name: str, later_found: bool
) -> None:
    db, store = setup
    legacy(db)
    store.broken.add(name)
    outcome = scan(setup)
    assert outcome.hold_ids and not outcome.materials and not outcome.absent_result_ids
    assert store.calls == [FLOW, RESULT]  # still inspect the sibling after a failure
    if later_found:
        store.put(SecretKind.ACCESS_TOKEN, name, MATERIAL, item_id=ITEM)
    restarted = sqlite3.connect(tmp_path / "synthetic.sqlite")
    recovered = RecordingStore(store.directory)
    try:
        assert scan((restarted, recovered)) == outcome
        assert recovered.calls == []
        with pytest.raises(ItemBudgetError, match=outcome.hold_ids[0]):
            read_item_budget(restarted)
        resolve(restarted, outcome.hold_ids[0])
        reviewed = scan((restarted, recovered))
        assert not reviewed.hold_ids
        assert bool(reviewed.materials) == later_found
        assert reviewed.absent_result_ids == (() if later_found else (RESULT,))
        assert restarted.execute("SELECT state FROM link_result").fetchone() == ("EXCHANGING",)
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "kind", ["unmapped", "conflicting", "missing-identity", "material-identity"]
)
def test_material_without_unambiguous_identity_is_never_found_or_absent(
    setup: tuple[sqlite3.Connection, RecordingStore], kind: str
) -> None:
    db, store = setup
    identity = (
        None if kind == "missing-identity" else MATERIAL if kind == "material-identity" else ITEM
    )
    name = FLOW if kind == "unmapped" else RESULT
    store.put(SecretKind.ACCESS_TOKEN, name, MATERIAL, item_id=identity)
    if kind == "conflicting":
        db.execute("UPDATE link_result SET item_id = 'synthetic-other-item'")
        db.commit()
    outcome = scan(setup)
    assert outcome.hold_ids
    assert not outcome.materials and not outcome.absent_result_ids
    assert db.execute("SELECT item_id FROM link_result").fetchone()[0] != MATERIAL
    assert MATERIAL not in "\n".join(db.iterdump()) + repr(outcome)


@pytest.mark.parametrize(
    "ref", ["malformed-sentinel", "link-token." + RESULT, "access-token." + OTHER]
)
def test_recorded_reference_cannot_override_deterministic_attribution(
    setup: tuple[sqlite3.Connection, RecordingStore], ref: str
) -> None:
    db, store = setup
    store.put(SecretKind.ACCESS_TOKEN, RESULT, MATERIAL, item_id=ITEM)
    if ref.startswith("access-token."):
        store.put(SecretKind.ACCESS_TOKEN, OTHER, "synthetic-other-material", item_id=ITEM)
    db.execute("UPDATE link_result SET secret_ref = ?", (ref,))
    db.commit()
    outcome = scan(setup)
    assert outcome.hold_ids and not outcome.materials
    if ref.startswith("access-token."):
        assert OTHER in store.calls


def test_recorded_legacy_access_reference_is_inspected_even_when_unmapped(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    ref = store.put(SecretKind.ACCESS_TOKEN, OTHER, MATERIAL, item_id=ITEM)
    legacy(db, ref)
    outcome = scan(setup)
    assert store.calls == [FLOW, RESULT, OTHER]
    assert outcome.hold_ids and not outcome.materials


def test_request_legacy_reference_survives_missing_result_mapping(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    ref = store.put(SecretKind.ACCESS_TOKEN, OTHER, MATERIAL, item_id=ITEM)
    legacy(db, ref)
    db.execute("UPDATE link_result SET legacy_link_flow_id = NULL")
    db.commit()
    assert scan(setup).hold_ids
    assert OTHER in store.calls


def test_one_bad_candidate_blocks_all_siblings(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    db.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
        "VALUES (?, ?, 'synthetic-second-digest', 'SUCCESS_PENDING_EXCHANGE')",
        (OTHER, FLOW),
    )
    db.commit()
    store.put(SecretKind.ACCESS_TOKEN, RESULT, MATERIAL, item_id=ITEM)
    store.broken.add(OTHER)
    outcome = scan(setup)
    assert store.calls == [FLOW, RESULT, OTHER]
    assert outcome.hold_ids and not outcome.materials and not outcome.absent_result_ids


def test_missing_recorded_material_is_held_even_if_all_names_are_absent(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, _ = setup
    db.execute("UPDATE link_result SET secret_ref = ?", ("access-token." + RESULT,))
    db.commit()
    assert scan(setup).hold_ids


def test_resolving_one_hold_keeps_other_holds_and_slot_observations(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    store.broken.add(RESULT)
    store.put(SecretKind.ACCESS_TOKEN, FLOW, MATERIAL, item_id=ITEM)
    ids = scan(setup).hold_ids
    assert len(ids) == 2
    resolve(db, ids[0])
    assert scan(setup).hold_ids == (ids[1],)
    resolve(db, ids[1])
    db.execute(
        "INSERT INTO link_success_observation(observation_id, flow_id, reason, observed_at) "
        "VALUES (?, ?, 'MISSING_SESSION_ID', ?)",
        (OTHER, FLOW, STAMP),
    )
    db.commit()
    with pytest.raises(ItemBudgetError, match="success observation"):
        read_item_budget(db)
    # Still-broken material creates new holds, preserving both previous reviews.
    again = scan(setup)
    assert len(again.hold_ids) == 2 and not set(again.hold_ids) & set(ids)
    assert db.execute(
        "SELECT count(*) FROM link_material_hold WHERE audit_id = ?", (AUDIT,)
    ).fetchone() == (2,)


@pytest.mark.parametrize("reviewed", [False, None, 1, "yes"])
def test_adjudication_requires_literal_confirmation(
    setup: tuple[sqlite3.Connection, RecordingStore], reviewed: Any
) -> None:
    db, store = setup
    store.broken.add(RESULT)
    hold = scan(setup).hold_ids[0]
    with pytest.raises(ReconciliationError, match="explicit reviewed"):
        adjudicate_material_hold(db, hold_id=hold, audit_id=AUDIT, now=NOW, reviewed=reviewed)
    assert scan(setup).hold_ids == (hold,)


def test_adjudication_refuses_invalid_missing_and_repeat_targets(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    store.broken.add(RESULT)
    hold = scan(setup).hold_ids[0]
    with pytest.raises(InvalidSecretRef):
        adjudicate_material_hold(db, hold_id=hold, audit_id=MATERIAL, now=NOW, reviewed=True)
    with pytest.raises(InvalidSecretRef):
        resolve(db, MATERIAL)
    with pytest.raises(ReconciliationError, match="missing or already"):
        resolve(db, OTHER)
    resolve(db, hold)
    with pytest.raises(ReconciliationError, match="missing or already"):
        resolve(db, hold)


def test_hold_commit_failure_refuses_and_rolls_back_without_exposing_error(
    setup: tuple[sqlite3.Connection, RecordingStore],
) -> None:
    db, store = setup
    store.broken.add(RESULT)
    db.execute(
        "CREATE TRIGGER fail_hold BEFORE INSERT ON link_material_hold BEGIN "
        "SELECT RAISE(ABORT, 'synthetic-secret-sentinel'); END"
    )
    with pytest.raises(ReconciliationError, match="transaction failed") as exc:
        scan(setup)
    assert "sentinel" not in str(exc.value)
    assert not db.in_transaction
    assert db.execute("SELECT count(*) FROM link_material_hold").fetchone() == (0,)


def test_cli_uses_selected_existing_database_and_requires_review(
    setup: tuple[sqlite3.Connection, RecordingStore],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
) -> None:
    db, store = setup
    store.broken.add(RESULT)
    hold = scan(setup).hold_ids[0]
    monkeypatch.setenv("NETWORTH_ENV", "sandbox")
    monkeypatch.setattr(
        "networth.commands.adjudicate_link_material.paths_for",
        lambda _: Paths(tmp_path / "unused", store.directory, tmp_path / "synthetic.sqlite"),
    )
    args = ["adjudicate-link-material", "--hold", hold, "--audit-id", AUDIT]
    assert main(args) == 2
    assert scan(setup).hold_ids == (hold,)
    assert main([*args, "--confirm-reviewed"]) == 0
    assert db.execute("SELECT audit_id FROM link_material_hold").fetchone() == (AUDIT,)
    assert MATERIAL not in str(capsys.readouterr())


@pytest.mark.parametrize("kind", ["access-token", "link-token"])
def test_foreign_legacy_reference_is_held_even_when_its_material_is_absent(
    setup: tuple[sqlite3.Connection, RecordingStore], kind: str
) -> None:
    db, store = setup
    legacy(db, kind + "." + OTHER)
    store.put(SecretKind.ACCESS_TOKEN, RESULT, MATERIAL, item_id=ITEM)
    outcome = scan(setup)
    assert outcome.hold_ids and not outcome.materials and not outcome.absent_result_ids
    if kind == "access-token":
        assert OTHER in store.calls


def test_real_pending_durability_failure_persists_a_hold(
    setup: tuple[sqlite3.Connection, RecordingStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    db, store = setup
    ref = store.put(SecretKind.ACCESS_TOKEN, RESULT, MATERIAL, item_id=ITEM)
    (store.directory / (ref + ".json")).rename(store.directory / ("." + ref + ".pending"))

    def fail_barrier() -> None:
        raise OSError("synthetic-durability-sentinel")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_fsync_directory", fail_barrier)
        held = scan(setup)
    assert held.hold_ids and not held.materials and not held.absent_result_ids
    assert store.reconcile(RESULT) is not None  # material is now verifiably durable
    assert scan(setup) == held  # automatic success still cannot clear the hold
    assert "sentinel" not in repr(held)
    assert db.execute("SELECT reason FROM link_material_hold").fetchone() == (
        "UNVERIFIED_MATERIAL",
    )
