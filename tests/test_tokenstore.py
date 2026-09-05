"""Task 05a's acceptance criteria, one test apiece.

Every credential-shaped string here is assembled at run time from parts. A public
repository that proved its secret handling with a committed fixture would have
put that shape in a permanent history in order to demonstrate it would not
(AGENTS.md rule 0) — and `scripts/check-no-secrets.sh` says so in its own
failure message.
"""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import stat
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from networth.storage import migrate
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
    UnverifiedMaterial,
    new_flow_id,
    parse_secret_ref,
    secret_ref_for,
)

# Minted flow ids, fixed here so a failure names the same file every run.
FLOW = "1f0c9a2b3d4e5f60718293a4b5c6d7e8"
OTHER_FLOW = "9a8b7c6d5e4f30211203a4b5c6d7e8f9"
ITEM = "item-synthetic-0001"
NOW = "2026-09-01T08:00:00Z"


def pending_name(ref: str) -> str:
    """The name `put` builds a record under, before it is published."""
    return f".{ref}.pending"


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
    assert store.reconcile(OTHER_FLOW) is None


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

    There are *three* directory barriers, and each one is load-bearing. The first
    makes the pending entry durable before the link, so a crash in that window
    leaves material `reconcile` can still find; without it the record's own fsync
    is a promise about contents under a name that may not survive. The second
    makes the *publication* durable, and it has to land before the release: the
    pending name is durable by then, so if the removal reached the platter and
    the link did not, neither name would hold the material. Nothing in POSIX
    orders those two directory operations, so the barrier between them is the
    only thing that does. The third makes the release itself durable.

    The sequence is the assertion. "Both happen" was true of the version that
    lost a credential.
    """
    calls: list[str] = []
    real_fsync, real_link, real_unlink = os.fsync, os.link, Path.unlink

    def spy_fsync(fd: int) -> None:
        calls.append("fsync-dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-file")
        real_fsync(fd)

    def spy_link(src: Any, dst: Any) -> None:
        calls.append("publish")
        real_link(src, dst)

    def spy_unlink(path: Path, **kwargs: Any) -> None:
        calls.append("release")
        real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "link", spy_link)
    monkeypatch.setattr(Path, "unlink", spy_unlink)
    store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("fsync"))

    assert calls == ["fsync-file", "fsync-dir", "publish", "fsync-dir", "release", "fsync-dir"]


def test_a_publication_that_fails_keeps_the_only_durable_copy(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stranded slot a broad `finally` created, driven by an injected `EIO`.

    By the time `link` runs, the record and its directory entry have both been
    fsynced: there is a live credential on this disk under the pending name, and
    `reconcile` is built to find it. A cleanup that runs on *every* path then
    deleted it — leaving no published name, no pending name, and `reconcile`
    answering `None`, which is the answer that sends the owner back to Plaid to
    spend one of ten lifetime slots on an Item he already has.

    An ordinary publication error, not a crash: the process is alive and raising,
    which is exactly the case a `finally` is written for and exactly the case
    where it must not fire.
    """
    material = synthetic_material("failed-publication")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)

    def link_fails(src: Any, dst: Any) -> None:
        raise OSError(errno.EIO, "input/output error")

    monkeypatch.setattr(os, "link", link_fails)
    with pytest.raises(OSError) as caught:
        store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    monkeypatch.undo()
    assert caught.value.errno == errno.EIO

    assert not (store.directory / f"{ref}.json").exists(), "publication was meant to fail"
    assert (store.directory / pending_name(ref)).exists(), "the only durable copy was deleted"

    recovered = store.reconcile(FLOW)
    assert recovered is not None, "a live credential is on disk and recovery cannot see it"
    assert recovered.item_id == ITEM
    assert store.get(ref).reveal() == material

    # And the retry is refused rather than allowed to write a second credential
    # over the surviving one — the same rule as every other pending record.
    with pytest.raises(SecretRefExists):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("retry"))


@pytest.mark.parametrize("failing_barrier", ["record", "directory"])
def test_a_put_whose_barrier_failed_is_never_reported_as_durable(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch, failing_barrier: str
) -> None:
    """A pending record that was never made durable must not answer `reconcile`.

    Both crashes leave the identical artefact — a parseable `.pending` file — so
    the reader cannot tell "durable, died before `link`" (issue #15, and the
    answer must be the record) from "the `fsync` itself failed" (nothing is
    established, and the data is in a page cache a power loss discards). Before
    the fix `reconcile` returned the second case as durable, and task 07a is
    required to commit an `item` row on that answer: a database row against
    material that may not survive, which is the unrecoverable direction and one
    permanently burned Item slot.

    So the barrier is completed on read. This test drives the half that cannot
    be completed: `fsync` keeps failing, and the answer has to be neither the
    record nor `None`.
    """
    material = synthetic_material(f"barrier-{failing_barrier}")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    real_fsync = os.fsync

    def fsync_fails(fd: int) -> None:
        is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if is_directory == (failing_barrier == "directory"):
            raise OSError(errno.EIO, "input/output error")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync_fails)
    with pytest.raises(OSError) as caught:
        store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)
    assert caught.value.errno == errno.EIO

    # The artefact the reader has to judge: present, parseable, not durable.
    assert not (store.directory / f"{ref}.json").exists()
    assert (store.directory / pending_name(ref)).exists()

    for call in (lambda: store.reconcile(FLOW), lambda: store.record(ref), lambda: store.get(ref)):
        with pytest.raises(UnverifiedMaterial):
            call()

    # It is emphatically not "absent" either: answering `None` sends a recovering
    # worker to Plaid for a replacement Item, which spends a second slot.
    monkeypatch.undo()
    assert (store.directory / pending_name(ref)).exists()

    # Once the barrier can be completed, the same record is durable and answers
    # normally — the recovery path issue #15 exists for is not broken by this.
    recovered = store.reconcile(FLOW)
    assert recovered is not None
    assert recovered.item_id == ITEM
    assert store.get(ref).reveal() == material


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


def test_a_competitor_is_refused_in_the_kernel_in_both_windows(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race an existence check cannot close, refused twice.

    Two workers both find nothing, both write, and the second silently destroys
    the first's credential — after which the `item` row references a token that
    belongs to a different Item. That is the unrecoverable direction, so the
    refusal has to happen in the kernel rather than in a check that ran earlier.

    A second worker can arrive in either of two windows, and each has its own
    kernel refusal: `O_EXCL` while the winner still holds the pending name, and
    `os.link` → `EEXIST` once the winner has published. Both are exercised here,
    because closing only the later one leaves the earlier one open.
    """
    winner = synthetic_material("winner")
    loser = synthetic_material("loser")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    competitor = TokenStore(store.directory)
    real_link = os.link

    # Window 1: the loser arrives while the winner's pending record is on disk,
    # written and fsynced, one instruction before publication.
    def loser_arrives_mid_write(src: Any, dst: Any) -> None:
        with pytest.raises(SecretRefExists):
            competitor.put(SecretKind.ACCESS_TOKEN, FLOW, loser)
        real_link(src, dst)

    monkeypatch.setattr(os, "link", loser_arrives_mid_write)
    store.put(SecretKind.ACCESS_TOKEN, FLOW, winner, item_id=ITEM)
    monkeypatch.undo()

    # Window 2: the winner has published and released the pending name.
    with pytest.raises(SecretRefExists):
        competitor.put(SecretKind.ACCESS_TOKEN, FLOW, loser)

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


# --- issue #15 and #11: the two windows a random temporary name left open --------


def _child_that_dies(directory: Path, material: str, at: str) -> int:
    """Run a `put` in a subprocess that dies at `at`, and return its exit code.

    A subprocess rather than an injected exception, because `os._exit` is the
    only way to skip a `finally` block — and the cleanup this is about lives in
    one. An in-process test would tidy up on the way out and prove nothing.
    """
    program = f"""
import os
from pathlib import Path
from networth.tokenstore import SecretKind, TokenStore

real_link = os.link

def die_before_link(src, dst):
    os._exit(17)

def die_after_link(src, dst):
    real_link(src, dst)
    os._exit(17)

os.link = {{"before-link": die_before_link, "after-link": die_after_link}}[{at!r}]
TokenStore(Path({str(directory)!r})).put(
    SecretKind.ACCESS_TOKEN, {FLOW!r}, {material!r}, item_id={ITEM!r}
)
"""
    return subprocess.run([sys.executable, "-c", program], check=False).returncode


def test_a_crash_before_the_link_leaves_material_reconcile_can_still_find(
    tmp_path: Path,
) -> None:
    """Issue #15's exact case: post-fsync, pre-publication.

    The record is durable — `put` fsynced it and fsynced the directory entry
    — and the worker died before the name it will be published under exists. If
    recovery cannot see it, a live credential is reported to Plaid support as
    lost and the owner re-links, spending one of ten lifetime slots on an Item he
    already has. Under a random temporary name this returned `None`.
    """
    directory = tmp_path / "tokenstore"
    material = synthetic_material("crash-before-link")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)

    assert _child_that_dies(directory, material, "before-link") == 17
    assert not (directory / f"{ref}.json").exists(), "the child was meant to die before publishing"
    assert (directory / pending_name(ref)).exists()

    found = TokenStore(directory).reconcile(FLOW)
    assert found is not None, "a durable credential was written and recovery cannot see it"
    assert found.item_id == ITEM
    assert TokenStore(directory).get(ref).reveal() == material


def test_a_crash_after_the_link_leaves_no_name_that_delete_cannot_reach(
    tmp_path: Path,
) -> None:
    """Issue #11's orphan, arrived at from the write side.

    The child publishes and dies before releasing the name it built under, so two
    names hold one credential. A `delete` that removes only the published one
    leaves material on disk after the database reference is cleared — invisible,
    referenced by nothing, and reaped by nothing. The check is not "delete
    returned True"; it is that no name in the directory still holds the material.
    """
    directory = tmp_path / "tokenstore"
    material = synthetic_material("crash-after-link")
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)

    assert _child_that_dies(directory, material, "after-link") == 17
    assert (directory / f"{ref}.json").exists()
    assert (directory / pending_name(ref)).exists(), "the child was meant to die before cleanup"

    store = TokenStore(directory)
    with store.deleting(ref) as existed:
        assert existed

    survivors = [path for path in directory.iterdir() if material in path.read_text("utf-8")]
    assert survivors == [], f"material survived deletion under {[p.name for p in survivors]}"


def test_a_half_written_pending_record_wedges_the_ref_until_it_is_deleted(
    tmp_path: Path,
) -> None:
    """The cost of a derived pending name, pinned so it stays a known cost.

    A crash *during* the write leaves a record that does not parse. It is not a
    credential and never was, but this store cannot prove that, so it refuses
    rather than silently replacing material it can never re-fetch: `reconcile`
    raises instead of reporting absence, and `put` refuses instead of
    overwriting. `delete` is the way out, and it is the same call the issue #16
    reaper already makes on a terminal flow — so the escape hatch is a documented
    operation, not a manual `rm`.
    """
    directory = tmp_path / "tokenstore"
    store = TokenStore(directory)
    ref = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW)
    (directory / pending_name(ref)).write_text('{"schema": 1, "mat', encoding="utf-8")

    with pytest.raises(CorruptRecord):
        store.reconcile(FLOW)
    with pytest.raises(SecretRefExists):
        store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("wedged"))

    assert store.delete(ref) is True
    assert store.reconcile(FLOW) is None
    assert store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("wedged")) == ref


def test_a_record_get_would_serve_is_not_one_record_refuses(store: TokenStore) -> None:
    """One file must not have two verdicts.

    `created_at` used to be checked only where it was converted, which `get` does
    not do — so a record `record()` called corrupt was still a credential `get()`
    handed out. Any field validated on one read path has to be validated on both.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("verdict"), item_id=ITEM)
    raw = read_raw(store, ref)
    raw["created_at"] = "2026-09-01T08:00:00"  # parseable, and naive
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorruptRecord):
        store.record(ref)
    with pytest.raises(CorruptRecord):
        store.get(ref)


def test_delete_establishes_the_directory_barrier_even_when_nothing_was_there(
    store: TokenStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The absent branch is a durability claim, not a no-op.

    Run one: the unlink lands and the directory fsync fails, so the removal is
    visible to this process and not yet on the platter. Run two sees the entry
    absent — and if it returns `False` without its own barrier, `deleting()` lets
    the caller clear `secret_ref` while a power loss can still restore the entry,
    leaving material nothing refers to. "Absent" has to be made durable before it
    may be acted on.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("barrier"))
    real_fsync = os.fsync

    def failing_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "injected directory fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", failing_fsync)
    with pytest.raises(OSError):
        store.delete(ref)
    monkeypatch.undo()
    assert not (store.directory / f"{ref}.json").exists(), "the unlink was meant to land"

    synced: list[int] = []

    def counting_fsync(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", counting_fsync)
    with store.deleting(ref) as existed:
        assert existed is False
        assert synced, "the absent branch cleared the ref without making the removal durable"


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
        f"access-token.{FLOW}/../other",
        f"unknown-kind.{FLOW}",
        f"access-token.{FLOW}.extra",
        "",
        f"access-token.{FLOW}\x00",
        "access-token.f" + "o" * 200,
        f"access-token.{FLOW.upper()}",  # the grammar is lowercase hex, exactly
    ],
)
def test_a_ref_that_could_name_another_file_is_refused(store: TokenStore, ref: str) -> None:
    for call in (store.get, store.record, store.delete):
        with pytest.raises(InvalidSecretRef):
            call(ref)


@pytest.mark.parametrize(
    "flow_id",
    ["../x", "a/b", ".", "", "flow id", "flow\n", "flow0123456789", FLOW[:31], FLOW + "0"],
)
def test_a_flow_id_that_could_name_another_file_is_refused(store: TokenStore, flow_id: str) -> None:
    with pytest.raises(InvalidSecretRef):
        store.put(SecretKind.ACCESS_TOKEN, flow_id, synthetic_material("escape"))
    with pytest.raises(InvalidSecretRef):
        store.reconcile(flow_id)


def test_material_cannot_be_mistaken_for_a_flow_id(store: TokenStore) -> None:
    """The leak that had no exception in it at all.

    Under the previous grammar — letters, digits, hyphens, under 64 characters —
    an ordinary Plaid access token was a *well-formed* `flow_id`. A caller that
    passed material where a flow id belonged got no error: the credential went
    into the filename and came back as the `secret_ref` the caller then writes to
    the database, which is §15 violated by the module that exists to enforce it.
    A minted-only shape makes that unrepresentable rather than merely unlikely.
    """
    material = synthetic_material("as-a-flow-id")

    with pytest.raises(InvalidSecretRef) as caught:
        store.put(SecretKind.ACCESS_TOKEN, material, material)
    assert material not in str(caught.value)

    with pytest.raises(InvalidSecretRef):
        secret_ref_for(SecretKind.ACCESS_TOKEN, material)
    assert [p.name for p in store.directory.iterdir()] == []


def test_a_minted_flow_id_is_accepted_and_a_credential_shape_is_not() -> None:
    minted = new_flow_id()
    assert parse_secret_ref(secret_ref_for(SecretKind.ACCESS_TOKEN, minted))[1] == minted
    assert new_flow_id() != minted
    with pytest.raises(InvalidSecretRef):
        secret_ref_for(SecretKind.ACCESS_TOKEN, synthetic_material("minted"))


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


@pytest.mark.parametrize("field", ["flow_id", "kind", "secret_ref"])
def test_a_record_whose_contents_disagree_with_its_name_is_refused(
    store: TokenStore, field: str
) -> None:
    """`reconcile` attributes material to a flow by the file's *name*. If the name
    and the contents can disagree, the wrong Item gets the wrong token.

    Every redundant identity field is checked, not just the two that used to be:
    the record's own `secret_ref` was written by `put` and then never read back,
    so it was a field that could only ever have lied.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("mismatch"), item_id=ITEM)
    raw = read_raw(store, ref)
    raw[field] = {
        "flow_id": OTHER_FLOW,
        "kind": SecretKind.LINK_TOKEN.value,
        "secret_ref": secret_ref_for(SecretKind.ACCESS_TOKEN, OTHER_FLOW),
    }[field]
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorruptRecord):
        store.record(ref)
    with pytest.raises(CorruptRecord):
        store.get(ref)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("material", ""),
        ("material", None),
        ("material", 12345),
        ("material", ["a", "b"]),
        ("material", {"reveal": "me"}),
        ("item_id", 12345),
        ("item_id", ""),
        ("item_id", ["item"]),
        ("created_at", 0),
        ("schema", True),
        ("schema", "1"),
    ],
)
def test_a_corrupt_field_is_refused_rather_than_coerced(
    store: TokenStore, field: str, value: Any
) -> None:
    """Presence was never the contract; shape is.

    `get` used to coerce whatever it found with `str(...)`, so a record whose
    material had been emptied returned an empty credential — and `put` refuses to
    *write* that exact value because a flow holding nothing reads as EXCHANGED and
    strands the slot. The read path was undoing the write path's own rule.
    """
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, synthetic_material("shape"), item_id=ITEM)
    raw = read_raw(store, ref)
    raw[field] = value
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(CorruptRecord):
        store.get(ref)
    with pytest.raises(CorruptRecord):
        store.reconcile(FLOW)


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
    """Material through every public argument, and through every metadata field.

    The rule is not "do not print the material field". A caller can pass a
    credential anywhere a string is taken, and a corrupt record can carry one in
    any field, so an exception that interpolates *any* untrusted value is a leak
    waiting for the wrong argument. This drives material through all of them.
    """
    material = synthetic_material("exceptions")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)

    raised: list[Exception] = []

    def capture(fn: Any, *args: Any, **kwargs: Any) -> None:
        with pytest.raises(Exception) as caught:  # noqa: B017, PT011
            fn(*args, **kwargs)
        raised.append(caught.value)

    # ...as every public argument that takes a string.
    capture(store.get, material)
    capture(store.record, material)
    capture(store.delete, material)
    capture(store.reconcile, material)
    capture(store.put, SecretKind.ACCESS_TOKEN, material, material)
    capture(secret_ref_for, SecretKind.ACCESS_TOKEN, material)
    capture(parse_secret_ref, material)
    # ...and the ordinary refusals, which see the material in play.
    capture(store.put, SecretKind.ACCESS_TOKEN, FLOW, material)  # SecretRefExists
    capture(store.get, secret_ref_for(SecretKind.LINK_TOKEN, FLOW))  # UnknownSecretRef

    # ...as every metadata field a corrupt record can be refused on. The file
    # keeps its real material throughout, so a reader that echoes any field can
    # be caught.
    #
    # Each case is rebuilt from the pristine record. Re-reading the file instead
    # meant the poisoning accumulated: once `schema` held material, every later
    # field was refused on that stale first field and never reached its own path
    # at all — the loop looked like six cases and was one, repeated.
    pristine = read_raw(store, ref)
    for field in ("schema", "kind", "flow_id", "secret_ref", "created_at"):
        raw = dict(pristine)
        raw[field] = material
        (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")
        capture(store.get, ref)  # CorruptRecord

    assert len(raised) == 14
    for exc in raised:
        assert material not in str(exc), f"{type(exc).__name__} leaked material"
        assert material not in repr(exc), f"{type(exc).__name__} leaked material in repr"


def test_the_one_field_that_cannot_be_refused_is_not_rendered(store: TokenStore) -> None:
    """`item_id` is the record's only unconstrained field, so it is the only leak.

    Every other field is pinned to something this reader already knows:
    `secret_ref`, `kind` and `flow_id` are checked against the name the file was
    found under, and `created_at` has to parse as an aware timestamp. None of
    them can hold a credential and survive validation. `item_id` is any non-empty
    string Plaid returned, so there is nothing here to refuse — a record carrying
    material in it is *valid*, both reads succeed, and the generated dataclass
    `repr` used to print the credential into whatever log line or traceback held
    the record.

    It is withheld from the rendering, not from the caller: `record.item_id` is
    still there, on purpose, like `Secret.reveal`.
    """
    material = synthetic_material("item-id-leak")
    held = synthetic_material("held")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, held, item_id=ITEM)
    raw = read_raw(store, ref)
    raw["item_id"] = material
    (store.directory / f"{ref}.json").write_text(json.dumps(raw), encoding="utf-8")

    record = store.record(ref)
    assert store.get(ref).reveal() == held, "this record is valid; nothing here can be refused"
    assert record.item_id == material, "the value is still reachable on purpose"

    for rendering in (repr(record), str(record), f"{record}", f"{record!r}", f"{record}"):
        assert material not in rendering, "a rendered record leaked material"
    assert "<redacted>" in repr(record)

    # Presence still survives the redaction: "did material arrive with an Item
    # id?" is what recovery asks of a rendered record (issue #15).
    without = store.record(store.put(SecretKind.ACCESS_TOKEN, OTHER_FLOW, held))
    assert "item_id=None" in repr(without)


def test_no_token_material_reaches_any_database_table(tmp_path: Path) -> None:
    """05a's last acceptance criterion. It needed task 03's schema to exist.

    §15's rule is that a row holds a *name* and never a value, and that is a fact
    about this module and the schema **together** — which is why the boundary
    test above ("no field a caller would persist contains material") could not
    stand in for it, and why the PR said so rather than faking it while there was
    no table to look in. Task 03 has merged, so there is.

    Two things make the sweep worth more than the assertion it looks like.
    `sqlite_master` drives it, not a list of columns, so a table added later is
    covered the day it is added and not the day someone remembers this test. And
    the byte scan reads the `-wal` as well as the `.db`, because which of the two
    holds a committed row is not a fact this test should have to know: measured
    here, a row committed on an open connection is in `networth.db-wal` and *not*
    in `networth.db` until the connection closes and checkpoints it. Closing
    first usually makes the `-wal` moot, and "usually" is the reason both are
    read rather than the one that ought to be enough.
    """
    store = TokenStore(tmp_path / "tokenstore")
    material = synthetic_material("no-table-holds-this")
    ref = store.put(SecretKind.ACCESS_TOKEN, FLOW, material, item_id=ITEM)

    database = tmp_path / "networth.db"
    connection = sqlite3.connect(database)
    try:
        migrate(connection)
        # The two rows §7 points at this store with, written the way the schema
        # expects them: the name, never the value behind it.
        connection.execute(
            "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
            "VALUES ('ins-synthetic-0001', 'Synthetic Institution', 0)"
        )
        connection.execute(
            "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
            "status_since, created_at) VALUES (1, ?, ?, 'HEALTHY', ?, ?)",
            (ITEM, ref, NOW, NOW),
        )
        connection.execute(
            "INSERT INTO link_flow(flow_id, secret_ref, minted_at, hosted_url_expires_at, "
            "state, item_id) VALUES (?, ?, ?, ?, 'EXCHANGED', ?)",
            (FLOW, ref, NOW, NOW, ITEM),
        )
        connection.commit()

        # Without these two the sweep would be run against an empty database and
        # would pass without ever seeing a row that references the store.
        assert connection.execute(
            "SELECT count(*) FROM item WHERE secret_ref = ?", (ref,)
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM link_flow WHERE secret_ref = ?", (ref,)
        ).fetchone() == (1,)

        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        assert len(tables) > 10, "the schema did not load; this sweep would prove nothing"
        for table in tables:
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                for value in row:
                    assert material not in str(value), f"table {table!r} holds token material"
    finally:
        connection.close()

    for path in (database, Path(f"{database}-wal")):
        if path.exists():
            assert material.encode() not in path.read_bytes(), f"{path.name} holds material"


def test_the_store_itself_does_not_render_material(store: TokenStore) -> None:
    material = synthetic_material("store-repr")
    store.put(SecretKind.ACCESS_TOKEN, FLOW, material)
    assert material not in repr(store)
    assert str(store.directory) in repr(store)
