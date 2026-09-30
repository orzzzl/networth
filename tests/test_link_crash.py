"""`07b` criterion 4: crash injection around the emergency write.

*"Crash injection **after the exchange response and before, during, and after**
the emergency write; each leaves a state the next run can classify correctly."*

The injection is the interesting half and it is deliberately not a mock of the
classifier's inputs. Each boundary below is reached by driving the **real**
:class:`EmergencyArtifactSink` and killing it at that instant, so what the
classifier then reads is a state the sink actually produces. A test that
hand-wrote the on-disk bytes would be checking that this file and
:mod:`networth.link_crash` agree with each other, which is the one thing a crash
never asks.

``Crashed`` stands in for the process dying, as it does in
``tests/sandbox/crash_boundaries.py``; the classifier is then called in a fresh
call with nothing carried over but the filesystem.
"""

from __future__ import annotations

import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

import pytest

from networth import link_recovery
from networth.backup import crypto
from networth.link_crash import Classification, CrashState, classify
from networth.link_fence import ATTESTATION, FENCED_INSTANCE, FenceAttestation
from networth.link_sink import EmergencyArtifactSink, RecoveredItem, SinkNotWritable
from networth.tokenstore import Secret

NOW = datetime(2026, 9, 30, 4, 30, tzinfo=UTC)
ACCESS_TOKEN = "access-sandbox-" + secrets.token_hex(16)
ITEM_ID = "item-0000000000000000"
FLOW_ID = "1f0c9a2b3d4e5f60718293a4b5c6d7e8"
OTHER_FLOW_ID = "9a8b7c6d5e4f30211203a4b5c6d7e8f9"
FENCE = FenceAttestation(
    instance=FENCED_INSTANCE,
    statement=ATTESTATION,
    confirmed_at=datetime(2026, 9, 30, 4, 25, tzinfo=UTC),
)


class Crashed(RuntimeError):
    """The injected failure. Stands in for the process dying at that instant."""


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
    path.chmod(0o600)
    return path


@pytest.fixture
def key_bytes(key_file: Path) -> bytes:
    return crypto.load_backup_key(key_file)


@pytest.fixture
def recovery_directory(tmp_path: Path) -> Path:
    """A recovery directory holding the record for :data:`FLOW_ID`.

    Written through ``store_and_verify`` — the real writer — because *"the record
    is still here"* is half of every classification below, and a hand-made file
    could be the shape no writer produces.
    """
    directory = tmp_path / "recovery"
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


def _item(**overrides: object) -> RecoveredItem:
    fields: dict[str, object] = {
        "access_token": Secret(ACCESS_TOKEN),
        "item_id": ITEM_ID,
        "flow_id": FLOW_ID,
        "link_session_id": "session-0001",
        "request_id": "request-0001",
        "fence": FENCE,
    }
    fields.update(overrides)
    return RecoveredItem(**fields)  # type: ignore[arg-type]


def _classify(recovery_directory: Path, artifact: Path, key_bytes: bytes | None) -> Classification:
    return classify(
        recovery_directory=recovery_directory,
        flow_id=FLOW_ID,
        artifact_path=artifact,
        key_bytes=key_bytes,
    )


class TestTheThreeInjectionPoints:
    """The criterion's three boundaries, each driven through the real sink."""

    def test_before_the_write_is_undecidable(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """Crash after the exchange response, before `commit` is called.

        The state is indistinguishable from a run that never started, and the
        classifier must say so rather than pick the comfortable half.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        # The process dies here: the response is in memory, nothing is on disk.

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.EXCHANGE_UNDECIDABLE
        assert found.record_present is True
        assert found.credential_is_durable is False

    def test_during_the_write_reports_the_credential_lost(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """Crash inside `commit`, after the file exists and before it is durable.

        The injection is at the real `fsync`, which is the instant the sink's own
        code puts between "the name exists" and "the bytes are safe". What
        survives is a file `O_EXCL` will refuse and no key can open.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()

        real_fsync = os.fsync
        truncated: list[int] = []

        def die_mid_write(fd: int) -> None:
            # Leave behind what a torn write leaves: the entry exists, the bytes
            # are not all there. Truncating through the same fd is the honest
            # stand-in for bytes that never reached the platter.
            os.ftruncate(fd, 16)
            real_fsync(fd)
            truncated.append(fd)
            raise Crashed("died between the write and its durability barrier")

        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "fsync", die_mid_write)
            with pytest.raises(Crashed):
                sink.commit(_item(), now=NOW)

        assert truncated, "the injection never reached the sink's fsync"
        assert artifact.exists(), "a torn write must leave the name behind"

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.CREDENTIAL_LOST
        assert found.credential_is_durable is False
        assert found.record_present is True

    def test_after_the_write_reports_the_credential_durable(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """Crash after `commit` returned and before the record was retired.

        This is the boundary whose misreading is expensive: the credential is
        safe and a re-run would exchange again.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(_item(), now=NOW)
        # The process dies here: the artifact is durable, the record is not retired.

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.CREDENTIAL_DURABLE
        assert found.credential_is_durable is True
        assert found.record_present is True
        assert found.rerun_may_exchange_again is True

    def test_after_the_record_is_retired_nothing_is_owed(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """The control for the boundary above: the same artifact, record gone.

        Without this pair, `CREDENTIAL_DURABLE` above is equally satisfied by a
        classifier that ignores the record entirely.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(_item(), now=NOW)
        assert link_recovery.delete(recovery_directory, FLOW_ID) is True

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.CREDENTIAL_DURABLE
        assert found.record_present is False
        assert found.rerun_may_exchange_again is False


class TestTheStatesThatAreNotCrashes:
    def test_no_artifact_and_no_record_is_nothing(self, tmp_path: Path, key_bytes: bytes) -> None:
        found = _classify(tmp_path / "empty", tmp_path / "absent.artifact", key_bytes)

        assert found.state is CrashState.NOTHING_HERE
        assert found.record_present is False
        assert found.rerun_may_exchange_again is False

    def test_an_artifact_for_another_flow_is_not_this_recovery(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """A complete artifact that belongs to a different flow.

        It proves a credential is durable for *some* recovery, and reading that
        as this one's would report a flow recovered on the strength of another
        flow's file.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(_item(flow_id=OTHER_FLOW_ID), now=NOW)

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.CREDENTIAL_LOST
        assert OTHER_FLOW_ID in found.detail

    def test_without_the_key_an_artifact_is_never_read_as_empty(
        self, tmp_path: Path, key_file: Path, recovery_directory: Path
    ) -> None:
        """No key is `CANNOT_TELL`, not `CREDENTIAL_LOST`.

        The distinction is the whole fail-closed direction: a durable credential
        we merely cannot open must not be reported as one that does not exist,
        because that report is followed by a second exchange.
        """
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(_item(), now=NOW)

        found = _classify(recovery_directory, artifact, None)

        assert found.state is CrashState.CANNOT_TELL
        assert found.credential_is_durable is False

    def test_a_non_directory_component_is_absent_not_unknown(
        self, tmp_path: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """`ENOTDIR` states there is no file at the pathname, one component earlier.

        The pair to the test below, and the two must disagree: an unreadable
        parent leaves the question open, a *file* used as a parent answers it.
        Collapsing them into one `CANNOT_TELL` would send the owner to fix an
        access problem that does not exist.
        """
        not_a_directory = tmp_path / "plain-file"
        not_a_directory.write_text("this is a file, not a directory\n")

        found = _classify(recovery_directory, not_a_directory / "recovery.artifact", key_bytes)

        assert found.state is CrashState.EXCHANGE_UNDECIDABLE

    def test_an_unreadable_artifact_directory_cannot_tell(
        self, tmp_path: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """An inspection error is its own answer, never "nothing is there"."""
        blocked = tmp_path / "blocked"
        blocked.mkdir(mode=0o000)
        try:
            found = _classify(recovery_directory, blocked / "recovery.artifact", key_bytes)
        finally:
            blocked.chmod(0o700)

        assert found.state is CrashState.CANNOT_TELL
        assert found.credential_is_durable is False


class TestTheNextRunIsToldWhichStateItIsIn:
    """The defect this slice closes, at the place the next run actually lands.

    `prepare()` is the next run's first contact with an existing artifact. Before
    this slice it raised one message for both states — *"Move it aside — it may
    hold an earlier recovery"* — and following that advice on a **complete**
    artifact means moving the only copy of the credential and exchanging again.
    """

    def test_a_durable_credential_is_not_described_as_movable(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = tmp_path / "recovery.artifact"
        written = EmergencyArtifactSink(artifact, key_file=key_file)
        written.prepare()
        written.commit(_item(), now=NOW)

        with pytest.raises(SinkNotWritable) as raised:
            EmergencyArtifactSink(artifact, key_file=key_file).prepare()

        message = str(raised.value)
        assert "move it aside" not in message.lower()
        assert "do not exchange" in message.lower()

    def test_and_an_unopenable_one_still_says_so(self, tmp_path: Path, key_file: Path) -> None:
        """The pair. Without it, the assertion above is satisfied by a `prepare`
        that stopped mentioning the artifact at all."""
        artifact = tmp_path / "recovery.artifact"
        artifact.write_bytes(b"not an artifact")

        with pytest.raises(SinkNotWritable) as raised:
            EmergencyArtifactSink(artifact, key_file=key_file).prepare()

        message = str(raised.value)
        assert "no usable credential" in message.lower()
