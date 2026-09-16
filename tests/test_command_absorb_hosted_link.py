"""``networth absorb-hosted-link`` — the half that is allowed to show the URL.

Every test here is about an ordering rather than a value: the URL may not appear
unless this Mac's copy of the recovery record was written *and read back first*.
That is `DESIGN.md` §4's requirement, and by **F2a** refusing costs nothing — no
Item slot is spent until Link completes, and a URL nobody was given cannot be
completed.
"""

from __future__ import annotations

import argparse
import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth import link_recovery, mac_identity
from networth.cli import discover
from networth.commands import absorb_hosted_link
from networth.link_recovery import MintResult, SecondCopyUnverified
from networth.tokenstore import Secret

FLOW_ID = "0123456789abcdef0123456789abcdef"
LINK_TOKEN = "link-sandbox-synthetic"
HOSTED_URL = "https://sandbox.plaid.example/hosted/synthetic"
COMMIT = "a" * 40
MINTED_AT = datetime(2026, 9, 15, 11, 0, tzinfo=UTC)

#: What the transport prints around the payload. The driver reads a mixed stream,
#: so "does the transcript survive" is part of the contract rather than a detail.
TRANSPORT_NOISE = (
    f"commit        {COMMIT}",
    "runner        scripts/sandbox-rehearsal.sh (piped to stdin)",
    "verb          networth start-hosted-link",
    "environment   sandbox",
    f"flow id       {FLOW_ID}",
)


def _wire(**overrides: Any) -> str:
    fields: dict[str, Any] = {
        "flow_id": FLOW_ID,
        "link_token": Secret(LINK_TOKEN),
        "minted_at": MINTED_AT,
        "link_token_expires_at": None,
        "url_lifetime_seconds": None,
        "hosted_link_url": HOSTED_URL,
    }
    fields.update(overrides)
    return MintResult(**fields).to_wire()


def _feed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lines: tuple[str, ...]
) -> argparse.Namespace:
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(tmp_path / "link-recovery"))
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    # The absorber measures which machine it is on before it writes the holder
    # field, so the suite has to say which machine it expects to be. Pointing it
    # at an address every machine holds keeps the bind real — the check still has
    # to pass for the record to exist — while letting these run off the Mac.
    # The pinned address belongs to one computer, so the measurement is injected
    # at its seam rather than redefined — there is no longer any way to redefine
    # it, which is the point of the seam.
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: True)
    return argparse.Namespace(commit=COMMIT)


def test_the_verb_is_discovered_without_a_registry_edit() -> None:
    assert "absorb-hosted-link" in discover()


def test_the_url_appears_only_after_the_second_copy_reads_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))

    assert absorb_hosted_link.run(args) == 0

    out = capsys.readouterr().out
    record = link_recovery.load(link_recovery.mac_recovery_directory(), FLOW_ID)
    assert record.link_token.reveal() == LINK_TOKEN
    # The holder is whatever the machine check returned, never a literal the
    # absorber chose — which is the point of the field. It is now the pinned
    # identity in tests too, because the required machine can no longer be
    # redefined from outside; only the measurement is substituted.
    assert record.second_copy_holder == mac_identity.REQUIRED_HOLDER
    assert record.second_copy_verified_at is not None
    assert HOSTED_URL in out
    # Ordering, asserted as ordering: the verification line is above the URL in the
    # transcript the owner reads, because that is the claim being made.
    assert out.index("second copy") < out.index(HOSTED_URL)


def test_the_token_is_withheld_while_the_rest_of_the_transcript_passes_through(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One line withheld, every other line kept.

    A driver that swallowed the transcript to protect one line of it would have made
    the run unreadable to protect a secret the other lines do not carry.
    """
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))

    assert absorb_hosted_link.run(args) == 0

    captured = capsys.readouterr()
    assert LINK_TOKEN not in captured.out
    assert LINK_TOKEN not in captured.err
    assert link_recovery.MINT_WIRE_MARKER not in captured.out
    for line in TRANSPORT_NOISE:
        assert line in captured.out


def test_a_copy_that_does_not_verify_prints_no_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§4's refusal, and the one this whole driver exists to make possible.

    The mint has already succeeded — Plaid has issued a real, openable URL — and this
    Mac cannot record it. Showing the URL anyway would let the owner spend a slot
    whose only recovery path is a file that was just not written.
    """
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))

    def refuse(*a: Any, **k: Any) -> Any:
        raise SecondCopyUnverified("synthetic read-back mismatch")

    monkeypatch.setattr(link_recovery, "store_and_verify", refuse)

    assert absorb_hosted_link.run(args) == 2

    captured = capsys.readouterr()
    assert HOSTED_URL not in captured.out
    assert HOSTED_URL not in captured.err
    assert "no URL is printed" in captured.err
    assert LINK_TOKEN not in captured.out + captured.err


def test_a_transcript_with_no_payload_is_a_refusal_not_a_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = _feed(monkeypatch, tmp_path, TRANSPORT_NOISE)

    assert absorb_hosted_link.run(args) == 1

    captured = capsys.readouterr()
    assert HOSTED_URL not in captured.out
    assert "no mint payload" in captured.err
    for line in TRANSPORT_NOISE:
        assert line in captured.out, "the transcript is the owner's evidence of why"


def test_two_payloads_are_refused_rather_than_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """One pipeline mints once. Two payloads means the stream is not what this
    thinks it is, and picking either would be picking which token is recoverable."""
    second = _wire(flow_id="f" * 32, link_token=Secret("link-sandbox-synthetic-2"))
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire(), second))

    assert absorb_hosted_link.run(args) == 2

    captured = capsys.readouterr()
    assert HOSTED_URL not in captured.out
    assert "one run mints once" in captured.err
    assert not link_recovery.mac_recovery_directory().exists()


def test_a_malformed_payload_never_echoes_the_line_it_could_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The branch where a line is unparseable is the branch where its contents are
    arbitrary *and* adjacent to a link token — the one combination that must not
    reach a message."""
    args = _feed(
        monkeypatch,
        tmp_path,
        (*TRANSPORT_NOISE, link_recovery.MINT_WIRE_MARKER + '{"link_token":"leaked-'),
    )

    assert absorb_hosted_link.run(args) == 2

    captured = capsys.readouterr()
    assert "leaked-" not in captured.out + captured.err
    assert "not JSON" in captured.err


def test_the_next_command_carries_a_mode_and_therefore_parses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The printed follow-up used to be `complete-hosted-link --flow <id>`, which
    argparse refuses: the mode is a required mutually-exclusive group, because a
    default that exchanges is a default that spends. A command that dies at argument
    parsing is worse than none on a path read against a 30-minute clock.

    Asserted against the *parser*, not against the string: a test that matched text
    would keep passing if the parser's requirements changed underneath it.
    """
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))

    assert absorb_hosted_link.run(args) == 0

    printed = [
        line.strip()
        for line in capsys.readouterr().out.splitlines()
        if "--verb complete-hosted-link" in line
    ]
    assert len(printed) == 3, "all three modes are offered; choosing is the owner's"

    from networth.commands import complete_hosted_link

    parser = argparse.ArgumentParser(prog="networth complete-hosted-link")
    complete_hosted_link.add_arguments(parser)
    for line in printed:
        words = line.split()
        assert COMMIT in words
        # Everything the remote runner will hand to the verb, in the verb's own
        # spelling: `--link-mode exchange` reaches it as `--exchange`.
        mode = words[words.index("--link-mode") + 1]
        parsed = parser.parse_args(["--flow", FLOW_ID, f"--{mode}"])
        assert parsed.flow == FLOW_ID


def test_the_wrong_machine_writes_no_record_and_shows_no_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The blocker this check exists for, stated as an outcome rather than a call.

    Before PR #75's re-review the holder field was a literal, so this run produced
    a complete, valid-looking record on the wrong computer — and the URL with it.
    The assertion that matters is not "verify was called": it is that the directory
    is empty afterwards and no URL was printed.
    """
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))
    # This machine does not hold the pinned address.
    monkeypatch.setattr(mac_identity, "holds_address", lambda _address: False)

    assert absorb_hosted_link.run(args) == 2

    captured = capsys.readouterr()
    assert HOSTED_URL not in captured.out
    assert HOSTED_URL not in captured.err
    # The token was on stdin and must not have been echoed while refusing.
    assert LINK_TOKEN not in captured.out
    assert LINK_TOKEN not in captured.err
    directory = link_recovery.mac_recovery_directory()
    assert not directory.exists() or list(directory.glob("*.json")) == []


def test_the_holder_field_is_the_verified_identity_and_not_a_literal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The stamp is whatever the check returned, not a string this module owns.

    A hardcoded holder passes any single-value assertion, which is how the original
    defect survived a green suite. The identity is pinned now, so it can no longer
    be *varied* through configuration — and it must not be, since that was the
    bypass. What still distinguishes a measurement from a constant is where the
    value comes from: substituting the check changes the record, which a literal
    in the absorber could not do.
    """
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))
    monkeypatch.setattr(mac_identity, "verify", lambda **_kwargs: "some-other-machine")

    assert absorb_hosted_link.run(args) == 0
    capsys.readouterr()

    record = link_recovery.load(link_recovery.mac_recovery_directory(), FLOW_ID)
    assert record.second_copy_holder == "some-other-machine"


def test_the_stamp_is_the_pinned_holder_on_the_real_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """And with only the measurement injected, it is the design's machine."""
    args = _feed(monkeypatch, tmp_path, (*TRANSPORT_NOISE, _wire()))

    assert absorb_hosted_link.run(args) == 0
    capsys.readouterr()

    record = link_recovery.load(link_recovery.mac_recovery_directory(), FLOW_ID)
    assert record.second_copy_holder == mac_identity.REQUIRED_HOLDER
