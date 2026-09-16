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
import errno
import io
import os
import stat
from collections.abc import Callable
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
    """Arm one run: the Mac's own directory, a transcript on stdin, a host answer.

    The outcome file is always wired, so every test exercises the report the shell
    driver reads — the half of this verb that PR #75's re-review found the driver
    was getting wrong by inferring instead.
    """
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(tmp_path / "mac-link-recovery"))
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: here)
    return argparse.Namespace(
        flow=flow,
        expect_completion=expect_completion,
        outcome_file=str(tmp_path / "outcome"),
    )


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


def _outcome(args: argparse.Namespace) -> dict[str, str]:
    """The report as the shell driver parses it: two named facts, never one word."""
    text = Path(args.outcome_file).read_text(encoding="utf-8")
    return dict(line.split("=", 1) for line in text.splitlines() if line)


def test_the_outcome_file_says_the_exchange_landed_and_the_record_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The facts the driver reports afterwards, taken from the process that acted.

    Its exit status cannot carry either: the verb exits 0 both when it deletes and
    when it deliberately keeps, and describing those two the same way is the
    contradiction the first re-review found.
    """
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    record = _record()

    assert retire_hosted_link.run(args) == 0
    capsys.readouterr()

    assert not record.exists()
    assert _outcome(args) == {
        "exchange": retire_hosted_link.EXCHANGED,
        "record": retire_hosted_link.ABSENT,
    }


def test_the_two_facts_cannot_be_reported_one_at_a_time(tmp_path: Path) -> None:
    """The structural half of the fix, which no behavioural test can reach.

    Collapsing the two states back into one is not a bug that shows up as a wrong
    value — it shows up as a *call site that compiles*, and the previous signature
    took exactly one word. Mutation testing is blind to it: revert the signature
    and every call site reverts with it, leaving the suite green. So the rule is
    asserted directly instead.

    The four `type: ignore[call-arg]` comments below are the more interesting half
    of the evidence, and they are load-bearing rather than noise: `mypy --strict`
    rejects every one of these calls *statically*, and the gate runs it over
    `tests/` as well as `networth/`. So a future edit that drops one of the two
    facts fails at type-check time, before this test runs at all. What is checked
    here is the runtime behaviour behind that — the belt under the braces, for a
    caller that reaches this function without being type-checked.
    """
    path = str(tmp_path / "outcome")

    with pytest.raises(TypeError):
        retire_hosted_link._record_outcome(path, exchange=retire_hosted_link.EXCHANGED)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        retire_hosted_link._record_outcome(path, record=retire_hosted_link.ABSENT)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        retire_hosted_link._record_outcome(path)  # type: ignore[call-arg]
    # Positional is refused too: an order nobody can see at the call site is how
    # two same-typed facts get silently swapped.
    with pytest.raises(TypeError):
        retire_hosted_link._record_outcome(path, "exchanged", "absent")  # type: ignore[call-arg]

    assert not Path(path).exists(), "a refused call reports nothing at all"


@pytest.mark.parametrize(
    ("lines", "expect_completion", "here", "exchange"),
    [
        (TRANSPORT_NOISE, True, True, retire_hosted_link.UNPROVEN),
        (TRANSPORT_NOISE, False, True, retire_hosted_link.UNPROVEN),
        ((*TRANSPORT_NOISE, _exchanged(OTHER_FLOW_ID)), True, True, retire_hosted_link.UNPROVEN),
        (
            (*TRANSPORT_NOISE, _exchanged(), _exchanged(OTHER_FLOW_ID)),
            True,
            True,
            retire_hosted_link.UNPROVEN,
        ),
        ((*TRANSPORT_NOISE, _exchanged()), True, False, retire_hosted_link.EXCHANGED),
    ],
    ids=["no-marker-expected", "retrieve-only", "other-flow", "two-markers", "wrong-machine"],
)
def test_every_path_that_leaves_the_record_says_why_without_guessing_at_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    lines: tuple[str, ...],
    expect_completion: bool,
    here: bool,
    exchange: str,
) -> None:
    """Enumerated rather than sampled: the driver's message is only as honest as
    the least-covered exit path, and an unreported one reads as "cannot tell".

    Each case now carries its expected *exchange* fact, and `wrong-machine` is why
    the column is worth having. It used to report the same `kept` as the four
    above it, and it is the opposite situation: a valid marker proved the exchange
    landed, and the only thing missing is the authority to look at the file. Every
    one of these reports `record=unknown`, because all of them return before this
    process has established it is the machine that holds the record — and reading
    that directory from anywhere else resolves some other computer's
    `~/agents/secrets`, which is the defect the previous round removed.
    """
    args = _mac(monkeypatch, tmp_path, lines, expect_completion=expect_completion, here=here)
    if not here:
        monkeypatch.setattr(mac_identity, "holds_address", lambda _address: True)
    record = _record()
    if not here:
        monkeypatch.setattr(mac_identity, "holds_address", lambda _address: False)

    retire_hosted_link.run(args)
    capsys.readouterr()

    assert record.exists()
    assert _outcome(args) == {"exchange": exchange, "record": retire_hosted_link.UNKNOWN}


def _break_directory_fsync(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail `os.fsync` for directory descriptors only, leaving file writes alone.

    Aimed at `os.fsync` rather than at `_fsync_directory`, so the code under test
    is the real one: `delete()` really unlinks, `_fsync_directory` really opens
    the directory, and the fault arrives at the one instruction between "the name
    is gone" and "the caller is told it worked".
    """
    real = os.fsync

    def failing(fd: int) -> None:
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(errno.EIO, "synthetic directory fsync failure")
        real(fd)

    monkeypatch.setattr(os, "fsync", failing)


def test_a_fault_after_the_unlink_reports_the_record_gone_not_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The re-review's reproduction, at the boundary it actually happens on.

    `delete()` unlinks first and fsyncs the directory second. A fault in that
    fsync leaves the pathname already gone — and the old report said `kept`, which
    the driver rendered as "the flow may still be live and that file is the way
    back", about a file that no longer existed, for a flow whose exchange a marker
    had already proved landed. Both halves of the sentence were false.
    """
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    record = _record()
    _break_directory_fsync(monkeypatch)

    assert retire_hosted_link.run(args) == 0, "a cleanup fault after an exchange is a note"
    captured = capsys.readouterr()

    assert not record.exists(), "the unlink really happened; that is the point of the case"
    assert _outcome(args) == {
        "exchange": retire_hosted_link.EXCHANGED,
        "record": retire_hosted_link.ABSENT,
    }
    assert "not removed cleanly" in captured.err
    assert "absent afterwards" in captured.err


def test_a_fault_before_the_unlink_reports_the_record_still_here(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other side of the same boundary, and the reason the test above is not
    enough on its own.

    A helper that answered `absent` unconditionally would pass that one. What has
    to be true is that the state is *looked at*: same exception class, same exit
    path, opposite answer, decided by the filesystem rather than by the failure.
    """
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    record = _record()

    def refuse(_self: Path, **_kwargs: object) -> None:
        raise PermissionError(errno.EACCES, "synthetic unlink refusal")

    monkeypatch.setattr(Path, "unlink", refuse)

    assert retire_hosted_link.run(args) == 0
    captured = capsys.readouterr()

    assert record.exists(), "nothing was removed, so the record is still on this disk"
    assert _outcome(args) == {
        "exchange": retire_hosted_link.EXCHANGED,
        "record": retire_hosted_link.PRESENT,
    }
    assert "present afterwards" in captured.err
    assert "inert either way" in captured.err, (
        "an exchanged flow has nothing to recover; residue is not a recovery path"
    )


# --- the record's state is read off this disk, never spent to make a boolean --
#
# `Path.exists()` has to consume the lookup error to return a bool. Measured for
# a record that is present and unreadable, on two interpreters this project's
# `requires-python = ">=3.12"` admits:
#
#     3.12.3   exists() raises PermissionError   ->  this verb said `unknown`
#     3.14.7   exists() returns False            ->  this verb said `absent`
#
# The same source reported opposite facts about the same disk. CI is on the top
# row (`ubuntu-latest` resolves `/usr/bin/python3`, 3.12.3), which is the part
# that matters here: the defect was invisible to the gate, so a regression built
# around the reported `EACCES` case alone would be green in CI forever. These
# tests are therefore about the *lookup* rather than about a value, and the one
# that pins the rule uses an error no interpreter above reports faithfully.


def _loop_directory(tmp_path: Path) -> Path:
    """A directory path whose lookup cannot complete: a symlink to itself.

    `ELOOP` is the failure worth building a regression on, because `exists()`
    answers `False` for it on **both** interpreters above (measured, not assumed).
    A test resting on `EACCES` alone passes against the defect on 3.12 — which is
    CI's interpreter, so reproducing the report faithfully and stopping there
    would have left the suite green against the very bug it was written for.
    """
    directory = tmp_path / "loop"
    directory.symlink_to(directory.name)
    return directory


def _directory_holding_a_record(tmp_path: Path) -> Path:
    directory = link_recovery.ensure_directory(tmp_path / "records")
    (directory / f"{FLOW_ID}.json").write_text("{}", encoding="utf-8")
    return directory


def _empty_directory(tmp_path: Path) -> Path:
    return link_recovery.ensure_directory(tmp_path / "records")


def _a_regular_file(tmp_path: Path) -> Path:
    path = tmp_path / "not-a-directory"
    path.write_text("", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("arrange", "expected"),
    [
        (_directory_holding_a_record, retire_hosted_link.PRESENT),
        (_empty_directory, retire_hosted_link.ABSENT),
        (_a_regular_file, retire_hosted_link.ABSENT),
        (_loop_directory, retire_hosted_link.UNKNOWN),
    ],
    ids=["on-disk", "no-such-file", "not-a-directory", "lookup-loops"],
)
def test_only_a_lookup_that_says_there_is_no_file_reports_the_record_absent(
    tmp_path: Path, arrange: Callable[[Path], Path], expected: str
) -> None:
    """The mapping, stated as a table because its two halves are easy to blur.

    `ENOENT` and `ENOTDIR` are statements that nothing is at the pathname — the
    second one reaching that conclusion a component earlier. Every other error is
    a lookup that did not finish, and the difference matters to the owner: after
    an exchange, `absent` means the cleanup is done and this Mac holds nothing,
    while `unknown` means go and look.
    """
    assert retire_hosted_link._observe_record(arrange(tmp_path), FLOW_ID) == expected


def _search_is_denied(directory: Path) -> bool:
    """Did `chmod 0o000` actually stop *this* process from looking inside?"""
    try:
        (directory / "probe").lstat()
    except PermissionError:
        return True
    except OSError:
        return False
    return False


def test_a_record_that_cannot_be_read_is_not_a_record_that_is_gone(tmp_path: Path) -> None:
    """The re-review's own case, on a real filesystem rather than a stub.

    Skipped rather than failed when the mode bit does not stop this process (as
    root it does not), because that is a fact about the sandbox and not a finding
    about the code. The skip costs no coverage: the rule is pinned
    root-independently, and interpreter-independently, by `lookup-loops` above.
    """
    directory = link_recovery.ensure_directory(tmp_path / "records")
    record = directory / f"{FLOW_ID}.json"
    record.write_text("{}", encoding="utf-8")

    directory.chmod(0o000)
    try:
        if not _search_is_denied(directory):
            pytest.skip("a 0o000 directory is searchable here, so there is no barrier to test")
        assert retire_hosted_link._observe_record(directory, FLOW_ID) == retire_hosted_link.UNKNOWN
    finally:
        directory.chmod(0o700)

    assert record.exists(), "it was there the whole time — which is what made `absent` a lie"


def test_a_delete_that_could_not_look_reports_unknown_rather_than_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The same rule at the level the owner reads, where it decides what he does.

    Whatever stops the unlink usually also stops the lookup that follows it, so
    this is the realistic shape of the fault rather than a contrived one. The
    report it produces is acted on: after an exchange, `record=absent` says the
    cleanup finished and there is nothing left on this Mac. Saying that because
    the lookup failed is the previous round's defect one level further in — a
    state inferred from an operation instead of measured.
    """
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(_loop_directory(tmp_path)))

    assert retire_hosted_link.run(args) == 0, "a cleanup fault after an exchange is still a note"
    captured = capsys.readouterr()

    assert _outcome(args) == {
        "exchange": retire_hosted_link.EXCHANGED,
        "record": retire_hosted_link.UNKNOWN,
    }
    assert "not removed cleanly" in captured.err
    assert "unknown afterwards" in captured.err


def test_an_unwritable_outcome_file_never_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A diagnostic that can fail turns a report into a second fault. The driver
    reads an absent file as "I cannot tell", which is the honest reading."""
    args = _mac(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _exchanged()))
    args.outcome_file = str(tmp_path / "no-such-directory" / "outcome")
    record = _record()

    assert retire_hosted_link.run(args) == 0
    capsys.readouterr()

    assert not record.exists(), "the real work still happened"
    assert not Path(args.outcome_file).exists()


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
