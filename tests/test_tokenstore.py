"""Task 05a's acceptance criteria, one test apiece.

Every credential-shaped string here is assembled at run time from parts. A public
repository that proved its secret handling with a committed fixture would have
put that shape in a permanent history in order to demonstrate it would not
(AGENTS.md rule 0) — and `scripts/check-no-secrets.sh` says so in its own
failure message.
"""

from __future__ import annotations

import json
import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from networth.tokenstore import (
    DIRECTORY_MODE,
    FILE_MODE,
    CorruptRecord,
    InvalidSecretRef,
    Secret,
    SecretKind,
    SecretRefExists,
    TokenStore,
    UnknownSecretRef,
    parse_secret_ref,
    secret_ref_for,
)

FLOW = "flow0123456789"
ITEM = "item-synthetic-0001"


def synthetic_material(seed: str) -> str:
    """A string shaped exactly like a Plaid access token. Not a credential.

    Built from a hash of `seed` so two records in one test hold provably
    different material, and split across `join` arguments so this file does not
    contain the shape it is about.
    """
    digest = sha256(seed.encode()).hexdigest()
    uuid_like = "-".join((digest[:8], digest[8:12], digest[12:16], digest[16:20], digest[20:32]))
    return "-".join(("access", "sandbox", "")) + uuid_like


@pytest.fixture
def store(tmp_path: Path) -> TokenStore:
    return TokenStore(tmp_path / "tokenstore")


def read_raw(store: TokenStore, ref: str) -> dict[str, Any]:
    record: dict[str, Any] = json.loads((store.directory / f"{ref}.json").read_text("utf-8"))
    return record


# --- criterion: files are mode 600, asserted on disk ----------------------------


def test_the_record_is_mode_600_on_disk(store: TokenStore) -> None:
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("mode"))
    mode = stat.S_IMODE((store.directory / f"{ref}.json").stat().st_mode)
    assert mode == FILE_MODE, f"expected {FILE_MODE:o}, found {mode:o}"


def test_the_directory_is_mode_700_on_disk(store: TokenStore) -> None:
    assert stat.S_IMODE(store.directory.stat().st_mode) == DIRECTORY_MODE


def test_an_existing_directory_with_a_loose_mode_is_tightened(tmp_path: Path) -> None:
    """The store is pointed at a directory it did not create.

    That is the deployed case whenever the operator makes the directory first,
    and `mkdir(exist_ok=True)` silently keeps whatever mode it already had.
    """
    loose = tmp_path / "loose"
    loose.mkdir(mode=0o755)
    assert stat.S_IMODE(TokenStore(loose).directory.stat().st_mode) == DIRECTORY_MODE


def test_a_mode_600_record_survives_umask_zero(store: TokenStore, monkeypatch: Any) -> None:
    """umask can only clear bits, so O_CREAT's mode is a request; fchmod is the
    guarantee. With umask 0 a store relying on the open mode alone still passes,
    which is why this asserts under the umask that would hide the difference."""
    previous = os.umask(0)
    try:
        ref = store.put(SecretKind.LINK_TOKEN, FLOW, synthetic_material("umask"))
    finally:
        os.umask(previous)
    assert stat.S_IMODE((store.directory / f"{ref}.json").stat().st_mode) == FILE_MODE


# --- criterion: a token written but not committed is discoverable after restart --


def test_material_written_before_the_db_commit_survives_a_restart(tmp_path: Path) -> None:
    """Issue #15's exact interleaving.

    Plaid returns, the worker makes the material durable, and the process dies
    before committing the `item` row. On restart the credential must be findable
    *and* attributable, or a recoverable crash gets reported as a lost token and
    a lifetime slot is re-spent for nothing.
    """
    directory = tmp_path / "tokenstore"
    material = synthetic_material("pending")

    writer = TokenStore(directory)
    writer.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    # ...and here the worker dies: no `item` row, no `link_flow.state='EXCHANGED'`.

    restarted = TokenStore(directory)
    found = restarted.reconcile(FLOW)

    assert found is not None, "a durable token was written and the restart cannot see it"
    assert found.flow_id == FLOW
    assert found.item_id == ITEM, "without the item_id the token cannot be matched to an Item"
    assert restarted.get(found.secret_ref).reveal() == material


def test_reconcile_is_none_when_nothing_was_written(store: TokenStore) -> None:
    assert store.reconcile(FLOW) is None


def test_reconcile_does_not_answer_for_a_different_flow(store: TokenStore) -> None:
    store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("a"), item_id=ITEM)
    assert store.reconcile("someotherflow") is None


def test_reconcile_ignores_a_link_token_for_the_same_flow(store: TokenStore) -> None:
    """A flow always has a link token; that is not evidence the exchange landed.
    Confusing the two would let a worker skip an exchange it never made."""
    store.put(SecretKind.LINK_TOKEN, FLOW, synthetic_material("link"))
    assert store.reconcile(FLOW) is None


def test_reconcile_returns_metadata_and_not_material(store: TokenStore) -> None:
    material = synthetic_material("meta")
    store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    found = store.reconcile(FLOW)
    assert found is not None
    assert material not in repr(found)


def test_created_at_is_utc_and_timezone_aware(store: TokenStore) -> None:
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("clock"))
    created_at = store.record(ref).created_at
    offset = created_at.utcoffset()
    assert offset is not None, "a naive timestamp cannot be compared across hosts"
    assert offset.total_seconds() == 0, "staleness math is done in UTC (AGENTS.md)"


# --- criterion: the stored name is a key name, never a value --------------------


def test_the_secret_ref_encodes_the_flow_and_carries_no_material(store: TokenStore) -> None:
    """What a database row gets to hold. `secret_ref` is the whole of it (§15)."""
    material = synthetic_material("ref")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)

    assert ref == secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    assert parse_secret_ref(ref) == (SecretKind.ACCESS_TOKEN, FLOW)
    assert material not in ref
    assert FLOW in ref


def test_no_field_a_caller_would_persist_contains_material(store: TokenStore) -> None:
    """The database-facing surface, checked as a whole.

    Task 05a cannot assert "no token material appears in any table" — no table
    exists until task 03 lands. What it *can* pin down is the boundary: every
    value this module hands back for persistence, and the record's repr, are free
    of material. The table-level assertion belongs to 03/04 and is called out in
    the PR rather than faked here.
    """
    material = synthetic_material("persist")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    record = store.record(ref)

    persistable = (record.secret_ref, record.kind.value, record.flow_id, record.item_id)
    for value in persistable:
        assert value is None or material not in value
    assert material not in repr(record)
    assert material not in repr(store)


# --- criterion: material is durable before the row that references it -----------


def test_put_fsyncs_the_record_and_the_directory_before_it_returns(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordering §14a depends on, checked as a sequence rather than asserted.

    Publishing the name does not make it durable because the file's contents
    were; the directory entry needs its own fsync. Both must happen before `put`
    returns, because the caller's very next act is to commit the `item` row that
    points here.
    """
    calls: list[str] = []
    real_fsync, real_link = os.fsync, os.link

    def spy_fsync(fd: int) -> None:
        calls.append("fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file")
        real_fsync(fd)

    def spy_link(src: Any, dst: Any) -> None:
        calls.append("publish")
        real_link(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "link", spy_link)
    store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("fsync"))

    assert calls == ["fsync-file", "publish", "fsync-dir"]


def test_a_crash_between_the_write_and_the_commit_leaves_a_findable_orphan(
    tmp_path: Path,
) -> None:
    """The survivable direction, driven by an injected crash.

    An orphan is recoverable; an `item` row with no material strands a lifetime
    slot. So the commit is what fails here, and the material must still be there.
    """
    directory = tmp_path / "tokenstore"
    material = synthetic_material("orphan")

    def commit_the_item_row() -> None:
        raise RuntimeError("worker died before COMMIT")

    store = TokenStore(directory)
    store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    with pytest.raises(RuntimeError):
        commit_the_item_row()

    recovered = TokenStore(directory).reconcile(FLOW)
    assert recovered is not None and recovered.item_id == ITEM


def test_a_competitor_that_lands_mid_put_is_refused_not_overwritten(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race an existence check cannot close.

    Two workers both find nothing, both write, and the second silently destroys
    the first's credential — after which the `item` row references a token that
    belongs to a different Item. That is the unrecoverable direction, so the
    refusal has to happen in the kernel (`os.link` → `EEXIST`) rather than in a
    check that ran earlier.

    The competitor is injected at the last possible moment: `fsync` of our own
    record, which is the instruction immediately before publication.
    """
    winner = synthetic_material("winner")
    loser = synthetic_material("loser")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    real_fsync = os.fsync
    landed = False

    def competitor(fd: int) -> None:
        nonlocal landed
        real_fsync(fd)
        if not landed and not stat.S_ISDIR(os.fstat(fd).st_mode):
            landed = True
            TokenStore(store.directory).put(SecretKind.ACCESS_TOKEN, FLOW, winner, item_id=ITEM)

    monkeypatch.setattr(os, "fsync", competitor)
    with pytest.raises(SecretRefExists):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, loser)

    monkeypatch.undo()
    assert store.get(ref).reveal() == winner, "the loser overwrote a durable credential"
    assert store.record(ref).item_id == ITEM


def test_empty_material_is_refused(store: TokenStore) -> None:
    """A flow holding nothing reads as EXCHANGED and strands the slot silently."""
    with pytest.raises(ValueError):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, "")
    assert store.reconcile(FLOW) is None


def test_reconcile_raises_on_a_corrupt_record_rather_than_reporting_absence(
    store: TokenStore,
) -> None:
    """ "No token" and "unreadable token" are different facts.

    Collapsing the second into the first is this project's own failure mode: the
    caller would call the credential lost, re-link, and spend a lifetime slot on
    an Item it already has.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("unreadable"))
    (store.directory / f"{ref}.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(CorruptRecord):
        store.reconcile(FLOW)


def test_put_refuses_to_overwrite_existing_material(store: TokenStore) -> None:
    """Blind rewriting is how a recoverable crash becomes a second exchange."""
    store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("first"), item_id=ITEM)
    with pytest.raises(SecretRefExists):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("second"))
    assert store.get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)).reveal() == synthetic_material(
        "first"
    )


def test_a_failed_put_leaves_no_temporary_file_behind(store: TokenStore) -> None:
    store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("first"))
    with pytest.raises(SecretRefExists):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("second"))
    assert [p.name for p in store.directory.iterdir()] == [
        f"{secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)}.json"
    ]


# --- criterion: deletion is material first, then the ref (issue #11) ------------


def test_deleting_removes_material_before_the_body_clears_the_ref(store: TokenStore) -> None:
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("order"), item_id=ITEM)
    row = {"secret_ref": ref}

    with store.deleting(ref) as existed:
        assert existed
        # The body is the second step, and it runs in a world where the material
        # is already gone. That is the whole ordering guarantee.
        assert store.reconcile(FLOW) is None
        row["secret_ref"] = ""

    assert row["secret_ref"] == ""


def test_a_crash_while_clearing_the_ref_leaves_a_visible_dangling_ref(
    store: TokenStore,
) -> None:
    """Issue #11's injected crash.

    Material gone, row still pointing at it: loud, and repairable. The forbidden
    outcome is the mirror image — a row cleared first, leaving material nothing
    refers to and nothing will ever reap.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("dangle"), item_id=ITEM)
    row = {"secret_ref": ref}

    with pytest.raises(RuntimeError), store.deleting(ref):
        raise RuntimeError("worker died before clearing secret_ref")

    assert row["secret_ref"] == ref, "the ref survived the crash — it is the visible half"
    with pytest.raises(UnknownSecretRef):
        store.get(ref)


def test_delete_is_idempotent_so_a_dangling_ref_can_be_repaired(store: TokenStore) -> None:
    """Re-running the repair must not throw, or the visible failure becomes the
    harder one to fix."""
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("repair"))
    assert store.delete(ref) is True
    assert store.delete(ref) is False


# --- must not: hand back every token, or address a file outside the directory ---


def test_there_is_no_interface_that_returns_every_token() -> None:
    """Structural, because the rule is about the shape of the API, not a call.

    An interface that offers "all the tokens" gets used that way eventually — by
    a report, a debug dump, or a sync loop that finds it convenient.
    """
    forbidden = {"all", "items", "keys", "values", "list", "list_all", "iter_all", "load_all"}
    public = {name for name in dir(TokenStore) if not name.startswith("_")}
    assert public & forbidden == set()
    assert not hasattr(TokenStore, "__iter__")


@pytest.mark.parametrize(
    "ref",
    [
        "../escape",
        "access-token./../../etc/passwd",
        "/etc/networth/plaid-items",
        "access-token.",
        "access-token..",
        "access-token.flow/../other",
        "unknown-kind.flow",
        "access-token.flow.extra",
        "",
        "access-token.flow\x00",
        "access-token.f" + "o" * 200,
    ],
)
def test_a_ref_that_could_name_another_file_is_refused(store: TokenStore, ref: str) -> None:
    for call in (store.get, store.record, store.delete):
        with pytest.raises(InvalidSecretRef):
            call(ref)


@pytest.mark.parametrize("flow_id", ["../x", "a/b", ".", "", "flow id", "flow\n"])
def test_a_flow_id_that_could_name_another_file_is_refused(store: TokenStore, flow_id: str) -> None:
    with pytest.raises(InvalidSecretRef):
        store.put(SecretKind.ACCESS_TOKEN, flow_id, synthetic_material("escape"))
    with pytest.raises(InvalidSecretRef):
        store.reconcile(flow_id)


def test_a_symlink_inside_the_store_is_refused_rather_than_followed(
    store: TokenStore, tmp_path: Path
) -> None:
    """The confinement the ref grammar buys is only real if the filesystem cannot
    give it back. Anything able to write one name in this directory could
    otherwise point it at a file anywhere on the host."""
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"schema": 1, "material": synthetic_material("outside")}))
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    (store.directory / f"{ref}.json").symlink_to(outside)

    with pytest.raises(CorruptRecord):
        store.get(ref)


def test_a_record_whose_contents_disagree_with_its_name_is_refused(store: TokenStore) -> None:
    """`reconcile` attributes material to a flow by the file's *name*. If the name
    and the contents can disagree, the wrong Item gets the wrong token."""
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("mismatch"), item_id=ITEM)
    raw = read_raw(store, ref)
    raw["flow_id"] = "adifferentflow"
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorruptRecord):
        store.record(ref)


def test_an_unreadable_ref_is_distinguishable_from_an_absent_one(store: TokenStore) -> None:
    with pytest.raises(UnknownSecretRef):
        store.get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW))


# --- criterion: no value is ever logged, echoed, or put in an exception ---------


def test_a_secret_redacts_itself_in_every_rendering() -> None:
    material = synthetic_material("render")
    secret = Secret(material)
    assert material not in repr(secret)
    assert material not in str(secret)
    assert material not in f"{secret}"
    assert material not in f"{secret!r}"
    assert material not in f"{secret:>40}"
    assert material not in "{}".format(secret)  # noqa: UP032
    assert secret.reveal() == material


def test_a_corrupt_record_reports_neither_the_material_nor_the_file(store: TokenStore) -> None:
    """`json.JSONDecodeError` carries the whole document in `.doc` and points at a
    column of it in `str()`. A parse failure on a file holding a credential is not
    a reason to print one."""
    material = synthetic_material("corrupt")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material)
    (store.directory / f"{ref}.json").write_text(
        '{"schema": 1, "material": "' + material + '"', encoding="utf-8"
    )

    with pytest.raises(CorruptRecord) as caught:
        store.get(ref)
    assert material not in str(caught.value)
    assert material not in repr(caught.value)


def test_no_exception_path_carries_material(store: TokenStore) -> None:
    """Every raise this module can reach with material in play, checked at once."""
    material = synthetic_material("exceptions")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)

    raised: list[Exception] = []

    def capture(fn: Any, *args: Any, **kwargs: Any) -> None:
        with pytest.raises(Exception) as caught:  # noqa: B017, PT011
            fn(*args, **kwargs)
        raised.append(caught.value)

    capture(store.put, SecretKind.ACCESS_TOKEN, FLOW, material)  # SecretRefExists
    capture(store.get, "../escape")  # InvalidSecretRef
    capture(store.get, secret_ref_for(SecretKind.LINK_TOKEN, FLOW))  # UnknownSecretRef

    raw = read_raw(store, ref)
    raw["schema"] = 99
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")
    capture(store.get, ref)  # CorruptRecord, and the file still holds the material

    assert len(raised) == 4
    for exc in raised:
        assert material not in str(exc), f"{type(exc).__name__} leaked material"
        assert material not in repr(exc), f"{type(exc).__name__} leaked material in repr"


def test_the_store_itself_does_not_render_material(store: TokenStore) -> None:
    material = synthetic_material("store-repr")
    store.put(SecretKind.ACCESS_TOKEN, FLOW, material)
    assert material not in repr(store)
    assert str(store.directory) in repr(store)
