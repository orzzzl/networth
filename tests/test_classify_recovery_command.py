"""``networth classify-recovery`` — the reachable half of `07b` criterion 4.

:mod:`networth.link_crash` is covered by ``tests/test_link_crash.py``, which
drives the real sink into each crash boundary. What these add is the property that
file cannot check: that the classification is **reachable from a command the owner
can type**, with the states mapped onto an exit status and an instruction. A
classifier with tests and no caller is the failure this verb exists to avoid, so
the test that matters most here is simply that the verb runs.
"""

from __future__ import annotations

import argparse
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth import link_recovery
from networth.backup import crypto
from networth.commands import classify_recovery
from networth.link_fence import ATTESTATION, FENCED_INSTANCE, FenceAttestation
from networth.link_sink import EmergencyArtifactSink, RecoveredItem
from networth.tokenstore import Secret

NOW = datetime(2026, 9, 30, 4, 30, tzinfo=UTC)
ACCESS_TOKEN = "access-sandbox-" + secrets.token_hex(16)
FLOW_ID = "1f0c9a2b3d4e5f60718293a4b5c6d7e8"
FENCE = FenceAttestation(
    instance=FENCED_INSTANCE,
    statement=ATTESTATION,
    confirmed_at=datetime(2026, 9, 30, 4, 25, tzinfo=UTC),
)


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def recovery_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Mac's recovery directory, pointed somewhere disposable.

    Through the documented override rather than by patching the function: the
    variable exists for exactly this, and it names a directory, never material.
    """
    directory = tmp_path / "recovery"
    monkeypatch.setenv(link_recovery.RECOVERY_DIRECTORY_ENV, str(directory))
    link_recovery.store_and_verify(
        directory,
        link_recovery.RecoveryRecord(
            flow_id=FLOW_ID,
            link_token=Secret("link-sandbox-" + secrets.token_hex(8)),
            minted_at=NOW,
            link_token_expires_at=None,
            url_lifetime_seconds=None,
            reap_after=link_recovery.reap_after_from(NOW),
        ),
        holder="zelengs-macbook-air-2",
        now=NOW,
    )
    return directory


def _write_artifact(path: Path, key_file: Path) -> None:
    sink = EmergencyArtifactSink(path, key_file=key_file)
    sink.prepare()
    sink.commit(
        RecoveredItem(
            access_token=Secret(ACCESS_TOKEN),
            item_id="item-0000000000000000",
            flow_id=FLOW_ID,
            link_session_id="session-0001",
            request_id="request-0001",
            fence=FENCE,
        ),
        now=NOW,
    )


def _run(artifact: Path, *, backup_key: Path | None, flow: str = FLOW_ID) -> int:
    return classify_recovery.run(
        argparse.Namespace(
            flow=flow,
            artifact=str(artifact),
            backup_key=str(backup_key) if backup_key else None,
        )
    )


def test_a_durable_credential_exits_zero_and_says_do_not_exchange(
    tmp_path: Path, key_file: Path, recovery_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "recovery.artifact"
    _write_artifact(artifact, key_file)

    assert _run(artifact, backup_key=key_file) == 0

    out = capsys.readouterr().out
    assert "CREDENTIAL_DURABLE" in out
    assert "credential    DURABLE" in out
    assert "do not exchange again" in out.lower()
    # The instruction must not name a command that does not exist. There is no
    # restore verb in this checkout and inventing one would send the owner looking
    # for it under a 30-minute clock.
    assert "restore-link-artifact" not in out


def test_a_torn_artifact_exits_one_and_points_at_the_transcript(
    tmp_path: Path, key_file: Path, recovery_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    artifact = tmp_path / "recovery.artifact"
    artifact.write_bytes(b"torn")

    assert _run(artifact, backup_key=key_file) == 1

    out = capsys.readouterr().out
    assert "CREDENTIAL_LOST" in out
    assert "request_id" in out


def test_no_artifact_is_undecidable_rather_than_clean(
    tmp_path: Path, key_file: Path, recovery_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(tmp_path / "absent.artifact", backup_key=key_file) == 1

    out = capsys.readouterr().out
    assert "EXCHANGE_UNDECIDABLE" in out
    assert "record        present" in out


def test_an_unusable_key_is_unknown_never_empty(
    tmp_path: Path, key_file: Path, recovery_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A key that will not load must not turn a durable credential into an absence.

    This is the fail-closed direction and the one with a price: reported as
    "nothing here", the owner exchanges again.
    """
    artifact = tmp_path / "recovery.artifact"
    _write_artifact(artifact, key_file)
    unusable = tmp_path / "broken.key"
    unusable.write_text("not a key\n")
    unusable.chmod(0o600)

    assert _run(artifact, backup_key=unusable) == 2

    out = capsys.readouterr().out
    assert "CANNOT_TELL" in out
    assert "NOT usable" in out
    assert "CREDENTIAL_LOST" not in out


def test_a_malformed_flow_id_is_refused_without_echoing_it(
    tmp_path: Path, key_file: Path, recovery_directory: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`record_path` refuses a non-flow-id, and the refusal must not repeat it.

    The reason is `link_recovery`'s own: a caller that arrived here by passing
    token material where a flow id belonged would otherwise have the module that
    exists to contain material be the one that prints it.
    """
    secret_looking = "link-sandbox-deadbeefdeadbeef"

    assert _run(tmp_path / "a.artifact", backup_key=key_file, flow=secret_looking) == 2

    captured = capsys.readouterr()
    assert secret_looking not in captured.out + captured.err


def test_the_verb_is_discoverable_by_the_cli() -> None:
    """The property this whole verb exists for: it is reachable.

    Autodiscovery keys off the module name, so this asserts the wiring the rest of
    the file assumes — a module that were named wrongly would still pass every
    test above while being untypeable.
    """
    from networth import cli

    assert "classify-recovery" in cli.discover()
