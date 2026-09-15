"""The Mac's second copy of a Link flow's recovery record (`DESIGN.md` §4, §15).

Every test here is about one of three properties, because those are the three
this record exists for and the three a later refactor can quietly destroy:

* it is **durable and verified before anything is printed** — refusing costs
  nothing (**F2a**), printing a URL whose recovery record failed to store costs a
  lifetime slot;
* it **carries no deadline**, because mint time cannot know one;
* its local bound is computed from **this machine's clock**, never from the
  instant the VPS stamped.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from networth import link_recovery
from networth.link_recovery import (
    REAP_AFTER,
    CorruptRecord,
    LinkRecoveryError,
    RecordExists,
    RecoveryRecord,
    SecondCopyUnverified,
)
from networth.tokenstore import Secret, new_flow_id

MINTED_AT = datetime(2026, 9, 14, 19, 0, tzinfo=UTC)
NOW = datetime(2026, 9, 14, 19, 5, tzinfo=UTC)
TOKEN = "link-sandbox-not-a-real-token"


def a_record(*, flow_id: str | None = None, now: datetime = NOW) -> RecoveryRecord:
    return RecoveryRecord(
        flow_id=flow_id or new_flow_id(),
        link_token=Secret(TOKEN),
        minted_at=MINTED_AT,
        link_token_expires_at=MINTED_AT + timedelta(minutes=30),
        url_lifetime_seconds=None,
        reap_after=link_recovery.reap_after_from(now),
    )


# --- the record carries only what mint time knows -----------------------------


def test_a_serialised_record_carries_no_deadline() -> None:
    assert link_recovery.record_has_no_deadline(a_record().to_json())


@pytest.mark.parametrize("field", sorted(link_recovery.FORBIDDEN_FIELDS))
def test_a_record_carrying_a_deadline_is_refused_on_read(field: str) -> None:
    """Rev 17 put one in. The refusal is what stops it coming back.

    Each of these is a value that could only have been *guessed from mint time*:
    both of Plaid's clocks start at the session's ``finished_at``, and when this
    record is written the owner has not opened the page yet.
    """
    payload = json.loads(a_record().to_json())
    payload[field] = MINTED_AT.isoformat()

    with pytest.raises(CorruptRecord, match="mint time cannot know"):
        RecoveryRecord.from_json(json.dumps(payload))


def test_record_has_no_deadline_actually_looks_at_the_payload() -> None:
    """The control: the checker must be able to say no.

    Without this, `record_has_no_deadline` could `return True` and the test above
    it would still pass.
    """
    payload = json.loads(a_record().to_json())
    payload["session_retention_expires_at"] = MINTED_AT.isoformat()

    assert not link_recovery.record_has_no_deadline(json.dumps(payload))


# --- two clocks, kept apart ---------------------------------------------------


def test_an_unasked_url_lifetime_round_trips_as_none_not_as_a_number() -> None:
    """``None`` means "did not ask, Plaid's default applies, we do not know it".

    That is a different fact from any number, and merging it into one would be
    the two-clock collapse issue #3 exists to prevent.
    """
    restored = RecoveryRecord.from_json(a_record().to_json())

    assert restored.url_lifetime_seconds is None
    assert restored.link_token_expires_at == MINTED_AT + timedelta(minutes=30)


def test_the_round_trip_preserves_every_field() -> None:
    original = a_record()
    restored = RecoveryRecord.from_json(original.to_json())

    assert restored.flow_id == original.flow_id
    assert restored.link_token.reveal() == TOKEN
    assert restored.minted_at == original.minted_at
    assert restored.reap_after == original.reap_after


# --- reap_after is a local number ---------------------------------------------


def test_reap_after_is_measured_from_this_machines_clock_not_from_minted_at() -> None:
    """§9.1 rule 1: written on one machine, compared on that machine.

    ``minted_at`` is stamped on the VPS. A ``reap_after`` derived from it is a
    cross-machine clock comparison — the defect rev 17 shipped in the backup
    canary. The gap here is deliberately huge so an implementation that used
    ``minted_at`` cannot coincidentally agree.
    """
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    record = RecoveryRecord(
        flow_id=new_flow_id(),
        link_token=Secret(TOKEN),
        minted_at=long_ago,
        link_token_expires_at=None,
        url_lifetime_seconds=None,
        reap_after=link_recovery.reap_after_from(NOW),
    )

    assert record.reap_after == NOW + REAP_AFTER
    assert record.reap_after != long_ago + REAP_AFTER


def test_reap_after_is_generous_by_construction() -> None:
    """30 min URL + 30 min token + 6 h retention, rounded up.

    Reaping late costs one inert file; reaping early destroys the disaster copy
    while the flow is still live. The bound must stay on the late side of every
    clock in §4's probe table.
    """
    every_clock_in_the_table = timedelta(minutes=30) + timedelta(minutes=30) + timedelta(hours=6)

    assert every_clock_in_the_table <= REAP_AFTER


def test_expired_is_false_before_the_bound_and_true_at_it() -> None:
    record = a_record(now=NOW)

    assert not record.expired(NOW + REAP_AFTER - timedelta(seconds=1))
    assert record.expired(NOW + REAP_AFTER)


# --- nothing renders that should not -------------------------------------------


def test_the_link_token_does_not_render_in_the_repr() -> None:
    assert TOKEN not in repr(a_record())


def test_the_flow_id_does_not_render_in_the_repr() -> None:
    """It is the handle naming *which* of the owner's Link attempts this is."""
    flow_id = new_flow_id()

    assert flow_id not in repr(a_record(flow_id=flow_id))


def test_the_instants_do_render_because_they_are_the_diagnostic() -> None:
    """The counterpart to the two tests above: redaction must not eat everything.

    These timestamps name no institution and no person, and a record that hid
    them could not support the measurement it is kept for.
    """
    rendered = repr(a_record())

    assert repr(MINTED_AT) in rendered


# --- boundary refusals ----------------------------------------------------------


def test_a_flow_id_that_is_not_one_is_refused_without_being_echoed() -> None:
    """A caller that passed token material where a flow id belonged.

    The module that exists to contain material must not be the one that prints
    it — into a traceback, a log line, or an alert (§15).
    """
    with pytest.raises(LinkRecoveryError) as caught:
        a_record(flow_id=TOKEN)

    assert TOKEN not in str(caught.value)


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(LinkRecoveryError, match="timezone-aware"):
        RecoveryRecord(
            flow_id=new_flow_id(),
            link_token=Secret(TOKEN),
            minted_at=datetime(2026, 9, 14, 19, 0),  # noqa: DTZ001 — the point of the test
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(NOW),
        )


def test_text_that_is_not_a_record_is_refused_rather_than_half_parsed(tmp_path: Path) -> None:
    for text in ("not json at all", "[]", json.dumps({"schema": "something.else"})):
        with pytest.raises(CorruptRecord):
            RecoveryRecord.from_json(text)


# --- store_and_verify: the step that gates printing the URL ---------------------


def test_store_and_verify_writes_reads_back_and_stamps(tmp_path: Path) -> None:
    record = a_record()

    verified = link_recovery.store_and_verify(
        tmp_path, record, holder="zelengs-macbook-air-2", now=NOW
    )

    assert verified.second_copy_verified_at == NOW
    assert verified.second_copy_holder == "zelengs-macbook-air-2"
    assert link_recovery.load(tmp_path, record.flow_id).link_token.reveal() == TOKEN


def test_the_stamp_is_on_disk_not_only_on_the_returned_object(tmp_path: Path) -> None:
    """The stamp is evidence the copy exists, so it has to be in the copy.

    A version that stamped only the in-memory object would satisfy the test above
    and leave the disaster procedure reading an unstamped file.
    """
    record = a_record()
    link_recovery.store_and_verify(tmp_path, record, holder="zelengs-macbook-air-2", now=NOW)

    assert link_recovery.load(tmp_path, record.flow_id).second_copy_verified_at == NOW


def test_the_record_is_mode_0600_in_a_mode_0700_directory(tmp_path: Path) -> None:
    directory = tmp_path / "recovery"
    record = a_record()

    link_recovery.store_and_verify(directory, record, holder="mac", now=NOW)

    path = link_recovery.record_path(directory, record.flow_id)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_pre_existing_loose_directory_is_tightened_rather_than_accepted(tmp_path: Path) -> None:
    """``mkdir`` honours its mode only on creation.

    So the interesting case is the second run, into a directory someone else
    made — which is every run after the first.
    """
    directory = tmp_path / "recovery"
    directory.mkdir(mode=0o755)

    link_recovery.store_and_verify(directory, a_record(), holder="mac", now=NOW)

    assert stat.S_IMODE(directory.stat().st_mode) == 0o700


def test_a_second_record_for_the_same_flow_is_refused(tmp_path: Path) -> None:
    """Refused in the kernel with ``O_EXCL``, not by a pre-check.

    Two writers that both find nothing and both proceed is how one flow's
    recovery material is destroyed by another's.
    """
    record = a_record()
    link_recovery.store_and_verify(tmp_path, record, holder="mac", now=NOW)

    with pytest.raises(RecordExists):
        link_recovery.store_and_verify(tmp_path, record, holder="mac", now=NOW)


def test_a_read_back_that_disagrees_raises_so_no_url_is_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of reading it back.

    Simulated by making the read return something else — a truncated write, a
    filesystem that lied about the ``fsync``, a full disk. The caller's
    obligation on this exception is to print nothing.
    """
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: "{}")

    with pytest.raises(SecondCopyUnverified, match="did not read back identical"):
        link_recovery.store_and_verify(tmp_path, a_record(), holder="mac", now=NOW)


def test_the_read_back_compares_bytes_not_a_reparse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-parse can agree while the file on disk is wrong.

    Here the read returns a *semantically equal* payload serialised differently.
    A check that parsed both sides and compared objects would pass; the byte
    comparison this function actually does catches it, which is what makes the
    verification about the file rather than about the parser.
    """
    record = a_record()
    verified_shape = RecoveryRecord(
        flow_id=record.flow_id,
        link_token=record.link_token,
        minted_at=record.minted_at,
        link_token_expires_at=record.link_token_expires_at,
        url_lifetime_seconds=record.url_lifetime_seconds,
        reap_after=record.reap_after,
        second_copy_verified_at=NOW,
        second_copy_holder="mac",
    )
    reordered = json.dumps(json.loads(verified_shape.to_json()), indent=2)
    monkeypatch.setattr(Path, "read_text", lambda self, **kwargs: reordered)

    with pytest.raises(SecondCopyUnverified):
        link_recovery.store_and_verify(tmp_path, record, holder="mac", now=NOW)


def test_a_write_that_fails_raises_the_refusal_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A full disk must land on "print no URL", not on a bare OSError.

    The exception type is the contract the caller branches on, so it is pinned
    rather than left to whatever escapes.
    """

    def explode(*args: object, **kwargs: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(os, "fsync", explode)

    with pytest.raises(SecondCopyUnverified):
        link_recovery.store_and_verify(tmp_path, a_record(), holder="mac", now=NOW)


# --- deletion: the two callers §4 allows ----------------------------------------


def test_delete_removes_the_record_and_reports_whether_it_was_there(tmp_path: Path) -> None:
    record = a_record()
    link_recovery.store_and_verify(tmp_path, record, holder="mac", now=NOW)

    assert link_recovery.delete(tmp_path, record.flow_id) is True
    assert link_recovery.delete(tmp_path, record.flow_id) is False


def test_loading_a_flow_with_no_record_says_so(tmp_path: Path) -> None:
    with pytest.raises(LinkRecoveryError, match="no recovery record"):
        link_recovery.load(tmp_path, new_flow_id())
