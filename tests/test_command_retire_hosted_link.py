"""``networth retire-hosted-link`` — the half that is allowed to delete.

Every test here is about an *authority* rather than a value: a recovery record is
the only way back to a flow that is still live (`DESIGN.md` §4), so the question
is never "did the remote succeed" but "did the transcript positively report *this*
flow exchanged". Absence is always "keep".

The directories are deliberately distinct wherever both hosts appear. A test that
gave the VPS half and the Mac half one directory is what let PR #75 claim a
cleanup the real topology could not perform.
"""

from __future__ import annotations

import argparse
import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth import link_recovery, mac_identity
from networth.cli import discover
from networth.commands import retire_hosted_link
from networth.link_recovery import CompletionOutcome, CorruptRecord, RecoveryRecord
from networth.tokenstore import Secret

FLOW_ID = "0123456789abcdef0123456789abcdef"
OTHER_FLOW_ID = "fedcba9876543210fedcba9876543210"
LINK_TOKEN = "link-sandbox-synthetic"
MINTED_AT = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)

#: What the transport and the VPS verb print around the marker. The driver reads a
#: mixed stream, so "does the transcript survive" is part of the contract.
TRANSPORT_NOISE = (
    "commit        " + "a" * 40,
    "runner        scripts/sandbox-rehearsal.sh (piped to stdin)",
    "verb          networth complete-hosted-link",
    "environment   sandbox",
    "exchanged     1 public_token(s)",
)


def _mac(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    lines: tuple[str, ...],
    *,
    flow: str = FLOW_ID,
    expect_completion: bool = True,
    here: bool = True,
) -> argparse.Namespace:
    """Arm one run: the Mac's own directory, a transcript on stdin, a host answer."""
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(tmp_path / "mac-link-recovery"))
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: here)
    return argparse.Namespace(flow=flow, expect_completion=expect_completion)


def _record(flow_id: str = FLOW_ID) -> Path:
    directory = link_recovery.mac_recovery_directory()
    link_recovery.store_and_verify(
        directory,
        RecoveryRecord(
            flow_id=flow_id,
            link_token=Secret(LINK_TOKEN),
            minted_at=MINTED_AT,
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(MINTED_AT),
        ),
        holder=mac_identity.REQUIRED_HOLDER,
        now=MINTED_AT,
    )
    return link_recovery.record_path(directory, flow_id)


def _exchanged(flow_id: str = FLOW_ID) -> str:
    return CompletionOutcome(flow_id, link_recovery.EXCHANGED).to_wire()


def test_the_verb_is_discovered_without_a_registry_edit() -> None:
    assert "retire-hosted-link" in discover()


def test_a_reported_exchange_retires_this_mac_s_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The normal ending of a flow's life, performed by the machine that holds it."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    record = _record()

    assert retire_hosted_link.run(args) == 0

    assert not record.exists()
    assert "retired" in capsys.readouterr().out


def test_the_whole_transcript_survives_because_nothing_in_it_is_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The mirror of the absorber's rule, and the opposite answer for a reason.

    The absorber withholds one line because it carries a `link_token`. This marker
    carries a flow id and a verb — both already printed in plain text all over the
    same run — so withholding it would hide the evidence for a deletion rather than
    protect anything.
    """
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    _record()

    assert retire_hosted_link.run(args) == 0

    out = capsys.readouterr().out
    for line in TRANSPORT_NOISE:
        assert line in out
    assert _exchanged() in out


def test_a_transcript_with_no_marker_keeps_the_record_and_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The blocker in one test: an exchanging mode that reported nothing.

    A poll that timed out, a refusal, a crash after the ssh connection came up —
    all of them end with a remote process that exited without exchanging, and all
    of them are states where this record is the only way back to a live flow. The
    remote exit status is the driver's business; this half asks for a positive
    statement and treats its absence as "keep".
    """
    args = _mac(monkeypatch, tmp_path, TRANSPORT_NOISE)
    record = _record()

    assert retire_hosted_link.run(args) == 1

    assert record.exists()
    assert "is kept" in capsys.readouterr().err


def test_retrieve_only_ends_with_no_marker_and_that_is_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Measurement (i)'s ending. The flow is deliberately still live and the record
    is the input the measurement comes back to in 30 minutes, so "nothing was
    retired" is the correct outcome rather than a fault."""
    args = _mac(monkeypatch, tmp_path, TRANSPORT_NOISE, expect_completion=False)
    record = _record()

    assert retire_hosted_link.run(args) == 0

    assert record.exists()
    assert "kept" in capsys.readouterr().out


def test_a_marker_for_another_flow_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two rehearsals can overlap, and then the transcript in front of this process
    is about a different flow from the one it was asked to retire. Deleting on the
    strength of "some flow exchanged" would destroy the disaster copy of a flow
    that is still live, while reporting success."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged(OTHER_FLOW_ID)))
    record = _record()

    assert retire_hosted_link.run(args) == 2

    assert record.exists()
    assert "not " + FLOW_ID in capsys.readouterr().err


def test_two_markers_are_a_refusal_rather_than_a_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One run completes one flow. A second marker means the stream is not what this
    verb thinks it is, and picking either would be picking which record to destroy."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged(), _exchanged(OTHER_FLOW_ID)))
    record = _record()

    assert retire_hosted_link.run(args) == 2

    assert record.exists()
    assert "2 completion markers" in capsys.readouterr().err


def test_the_wrong_machine_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Off the Mac, `mac_recovery_directory()` resolves some *other* host's
    `~/agents/secrets`, which `AGENTS.md` forbids this code from touching in either
    direction. Measured before anything is unlinked, like the absorber before
    anything is written."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()), here=False)
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: True)
    record = _record()
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: False)

    assert retire_hosted_link.run(args) == 2

    assert record.exists()
    assert "does not hold" in capsys.readouterr().err


def test_an_unlink_fault_after_a_landed_exchange_is_a_note_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The exchange already happened on the VPS. Exiting non-zero would send the
    owner to a recovery procedure for a flow that has nothing left to recover, and
    the residue is one inert file the puller reaps at `reap_after`."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    _record()

    def refuse(*args: object, **kwargs: object) -> bool:
        raise OSError("synthetic unlink failure")

    monkeypatch.setattr(link_recovery, "delete", refuse)

    assert retire_hosted_link.run(args) == 0

    assert "was not removed" in capsys.readouterr().err


def test_a_finished_flow_with_no_record_here_is_not_a_fault(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`link-recover.sh` completes on this Mac and retires in that process, so a
    re-run of a finished flow legitimately finds nothing."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    link_recovery.ensure_directory(link_recovery.mac_recovery_directory())

    assert retire_hosted_link.run(args) == 0

    assert "had no record on this Mac" in capsys.readouterr().out


def test_only_exchanged_is_a_terminal_outcome() -> None:
    """A future outcome that is also terminal has to be named and considered rather
    than falling into "not a failure"."""
    with pytest.raises(CorruptRecord, match="only 'EXCHANGED'"):
        CompletionOutcome.from_wire(CompletionOutcome(FLOW_ID, "ABANDONED").to_wire())


def test_a_wire_flow_id_is_checked_before_it_becomes_a_filename() -> None:
    """The same rule `reap_expired` applies to a name it finds on disk, applied to a
    name that arrives over a wire — this value is about to select a file to delete."""
    for bad in ("../../etc/passwd", "", FLOW_ID.upper(), FLOW_ID[:-1]):
        with pytest.raises(CorruptRecord, match="does not name a flow id"):
            CompletionOutcome.from_wire(CompletionOutcome(bad, link_recovery.EXCHANGED).to_wire())
