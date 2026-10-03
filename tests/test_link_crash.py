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
from networth.link_sink import (
    ArtifactReadFailure,
    ArtifactUnreadable,
    EmergencyArtifactSink,
    RecoveredItem,
    SinkNotWritable,
    read_artifact,
)
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

    def test_during_the_write_reports_the_artifact_unverifiable(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """Crash inside `commit`, after the file exists and before it is durable.

        The injection is at the real `fsync`, which is the instant the sink's own
        code puts between "the name exists" and "the bytes are safe". What
        survives is a file `O_EXCL` will refuse and no key can open.

        **It reports `ARTIFACT_UNVERIFIED`, not `CREDENTIAL_LOST`** — this test
        asserted the latter until PR #136's review (F1). A torn envelope fails
        the same tag comparison a *wrong key* fails, so the verdict this run can
        reach is about the attempt and not about the credential. That the real
        cause here is a torn write is something this test knows because it caused
        it; the classifier cannot, and must not say it can. The pair that pins the
        distinction is `TestWhatAFailedReadDoesNotProve`.
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

        assert found.state is CrashState.ARTIFACT_UNVERIFIED
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

    def test_and_an_unopenable_one_is_called_unknown_not_empty(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The pair. Without it, the assertion above is satisfied by a `prepare`
        that stopped mentioning the artifact at all.

        This asserted *"no usable credential"* until PR #136's review (F1), which
        is the claim `prepare` is not entitled to make about a file it could not
        open: the identical refusal fires for a wrong escrowed key. It must still
        name the artifact and still forbid a second exchange — and it must now
        also say the credential's fate is **unknown** and that the file is to be
        kept.
        """
        artifact = tmp_path / "recovery.artifact"
        artifact.write_bytes(b"not an artifact")

        with pytest.raises(SinkNotWritable) as raised:
            EmergencyArtifactSink(artifact, key_file=key_file).prepare()

        message = str(raised.value)
        assert str(artifact) in message
        assert "unknown" in message.lower()
        assert "do not exchange" in message.lower()
        # The two claims it is not entitled to, named rather than implied.
        assert "no usable credential" not in message.lower()

    def test_but_one_that_opens_and_is_the_wrong_schema_is_settled(
        self, tmp_path: Path, key_file: Path, key_bytes: bytes
    ) -> None:
        """The third answer, and the one `CREDENTIAL_LOST` is still for.

        A `03a` backup archive shares this envelope (`ARTIFACT_SCHEMA`'s comment
        says so), so a file sealed under the escrowed key that is not this schema
        *opens* — and openability is the evidence that makes "holds no usable
        credential" a proof rather than a guess. Without this test the F1 fix
        would be indistinguishable from deleting the proven branch.
        """
        artifact = tmp_path / "recovery.artifact"
        artifact.write_bytes(crypto.seal(b'{"schema": "something-else"}', key_bytes))

        with pytest.raises(SinkNotWritable) as raised:
            EmergencyArtifactSink(artifact, key_file=key_file).prepare()

        message = str(raised.value)
        assert "no usable credential" in message.lower()
        assert "unknown" not in message.lower()


class TestWhatAFailedReadDoesNotProve:
    """PR #136 review finding F1, as the measurement that found it.

    The classifier read **every** failure of `read_artifact` as
    `CREDENTIAL_LOST`, so three unrelated facts arrived as one verdict: a torn
    write, a well-formed but *wrong* escrowed key, and a transient `EACCES`.
    Only the first is anywhere near loss, and the cost of the conflation is the
    worst this module can pay — the owner is told the only surviving credential
    will never be recoverable, acts on it, and the file was holding it the whole
    time.

    **Each test here writes its artifact through the real sink and never touches
    its bytes**, which is what makes the claim a measurement rather than a
    restatement: the last test restores both the key and the access and gets the
    credential back out of the identical file. A test that hand-wrote a broken
    artifact could not tell "this run could not open it" from "there is nothing
    in it", which is precisely the distinction at issue.
    """

    @pytest.fixture
    def durable_artifact(self, tmp_path: Path, key_file: Path) -> Path:
        artifact = tmp_path / "recovery.artifact"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(_item(), now=NOW)
        return artifact

    @pytest.fixture
    def wrong_key_file(self, tmp_path: Path) -> Path:
        """A different key, and a *valid* one — that is the whole point.

        An invalid key is refused by `load_backup_key` before it reaches any of
        this. The dangerous input is the one that passes every check and simply
        is not the key the artifact was sealed under, because nothing downstream
        can tell it from ciphertext that was damaged.
        """
        path = tmp_path / "other-backup.key"
        path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
        path.chmod(0o600)
        return path

    def test_a_wrong_but_valid_key_is_not_credential_loss(
        self, durable_artifact: Path, wrong_key_file: Path, recovery_directory: Path
    ) -> None:
        wrong_key = crypto.load_backup_key(wrong_key_file)
        assert len(wrong_key) == crypto.KEY_BYTES, "the wrong key must still be a valid key"

        found = _classify(recovery_directory, durable_artifact, wrong_key)

        assert found.state is CrashState.ARTIFACT_UNVERIFIED
        assert found.credential_is_durable is False

    def test_a_transient_read_denial_is_not_credential_loss(
        self, durable_artifact: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        durable_artifact.chmod(0o000)
        try:
            found = _classify(recovery_directory, durable_artifact, key_bytes)
        finally:
            durable_artifact.chmod(0o600)

        # `CANNOT_TELL` and not `ARTIFACT_UNVERIFIED`: this run did not read the
        # bytes at all, so it has not even reached the ambiguity the other state
        # names. The two share an exit status and differ in what to go fix.
        assert found.state is CrashState.CANNOT_TELL
        assert found.credential_is_durable is False

    def test_and_the_same_bytes_still_hold_the_credential_afterwards(
        self, durable_artifact: Path, key_file: Path, wrong_key_file: Path, key_bytes: bytes
    ) -> None:
        """The assertion that makes the two above *findings* and not preferences.

        Without it, calling an unopenable artifact `CREDENTIAL_LOST` would be a
        defensible pessimism. With it, the file is shown to have been holding the
        credential during both refusals — so the old verdict was not pessimistic,
        it was wrong.
        """
        before = durable_artifact.read_bytes()
        directory = durable_artifact.parent

        for key, denied in ((crypto.load_backup_key(wrong_key_file), False), (key_bytes, True)):
            if denied:
                durable_artifact.chmod(0o000)
            try:
                refused = _classify(directory, durable_artifact, key)
            finally:
                if denied:
                    durable_artifact.chmod(0o600)
            assert refused.state is not CrashState.CREDENTIAL_DURABLE

        assert durable_artifact.read_bytes() == before, "nothing above may alter the artifact"
        recovered = read_artifact(durable_artifact, key_bytes=key_bytes)
        assert recovered["flow_id"] == FLOW_ID
        assert recovered["access_token"] == ACCESS_TOKEN

    def test_the_occupied_path_advice_makes_the_same_three_distinctions(
        self, durable_artifact: Path, key_file: Path, wrong_key_file: Path
    ) -> None:
        """F1's second half: `prepare`'s refusal, not just the classifier.

        Both read the artifact and both gave the same *"holds no usable
        credential … treat the lifetime slot as spent"* answer, so fixing one and
        not the other leaves the defect reachable from the path the emergency
        actually runs — `prepare` is what a re-run hits first.
        """
        under_wrong_key = EmergencyArtifactSink(durable_artifact, key_file=wrong_key_file)
        with pytest.raises(SinkNotWritable) as refused:
            under_wrong_key.prepare()
        assert "unknown" in str(refused.value).lower()
        assert "no usable credential" not in str(refused.value).lower()

        durable_artifact.chmod(0o000)
        try:
            with pytest.raises(SinkNotWritable) as denied:
                EmergencyArtifactSink(durable_artifact, key_file=key_file).prepare()
        finally:
            durable_artifact.chmod(0o600)
        assert "unknown" in str(denied.value).lower()
        assert "no usable credential" not in str(denied.value).lower()

        # And the control: with the right key and access it is the durable answer,
        # so the two refusals above are not "this message now says unknown always".
        with pytest.raises(SinkNotWritable) as durable:
            EmergencyArtifactSink(durable_artifact, key_file=key_file).prepare()
        assert "do not exchange" in str(durable.value).lower()
        assert "unknown" not in str(durable.value).lower()

    def test_but_an_artifact_that_opens_and_is_not_one_is_still_lost(
        self, tmp_path: Path, key_bytes: bytes, recovery_directory: Path
    ) -> None:
        """The positive control, at the classifier rather than through the CLI.

        Without it the F1 fix is indistinguishable from deleting
        `CREDENTIAL_LOST`, and mutation testing showed that gap was real: turning
        `classify`'s proven branch into `ARTIFACT_UNVERIFIED` reddened **only** a
        command test, so the classifier's own proof was pinned one layer away from
        where it is decided.

        A `03a` backup archive shares this envelope, so this input opens and
        authenticates under the escrowed key — which is the evidence that makes
        "holds no usable credential" settled rather than suspected.
        """
        artifact = tmp_path / "recovery.artifact"
        artifact.write_bytes(crypto.seal(b'{"schema": "networth.backup-archive.1"}', key_bytes))

        found = _classify(recovery_directory, artifact, key_bytes)

        assert found.state is CrashState.CREDENTIAL_LOST
        assert found.credential_is_durable is False

    def test_the_cause_travels_as_a_value_not_as_prose(
        self, durable_artifact: Path, wrong_key_file: Path, key_bytes: bytes
    ) -> None:
        """Why `ArtifactReadFailure` exists rather than a message match.

        The review asked for typed causes explicitly — *"do not infer the cause by
        parsing exception prose"* — and this is the test that keeps a later
        refactor of those messages from silently re-deciding a verdict.
        """
        with pytest.raises(ArtifactUnreadable) as wrong_key:
            read_artifact(durable_artifact, key_bytes=crypto.load_backup_key(wrong_key_file))
        assert wrong_key.value.cause is ArtifactReadFailure.UNAUTHENTICATED

        durable_artifact.chmod(0o000)
        try:
            with pytest.raises(ArtifactUnreadable) as denied:
                read_artifact(durable_artifact, key_bytes=key_bytes)
        finally:
            durable_artifact.chmod(0o600)
        assert denied.value.cause is ArtifactReadFailure.ACCESS_FAILED

        not_ours = durable_artifact.parent / "archive.sealed"
        not_ours.write_bytes(crypto.seal(b'{"schema": "networth.backup-archive.1"}', key_bytes))
        with pytest.raises(ArtifactUnreadable) as wrong_schema:
            read_artifact(not_ours, key_bytes=key_bytes)
        assert wrong_schema.value.cause is ArtifactReadFailure.NOT_AN_ARTIFACT


class TestUnknownRecordPresenceIsNotAnAbsence:
    """PR #136 review finding N1.

    `CANNOT_TELL` from an unreadable recovery directory carried
    `record_present=False`, and the CLI printed that as *"record absent"* and
    *"no record remains to exchange from"* — two factual claims, under the one
    state that exists to say nothing was established.
    """

    def test_an_unreadable_record_directory_reports_unknown_not_absent(
        self, tmp_path: Path, recovery_directory: Path, key_bytes: bytes
    ) -> None:
        artifact = tmp_path / "absent.artifact"
        recovery_directory.chmod(0o000)
        try:
            found = _classify(recovery_directory, artifact, key_bytes)
        finally:
            recovery_directory.chmod(0o700)

        assert found.state is CrashState.CANNOT_TELL
        assert found.record_present is None
        # The conservative side, and the honest one: if we cannot see the record we
        # cannot promise there is nothing to exchange from.
        assert found.rerun_may_exchange_again is True

    def test_a_known_absence_still_says_so(self, tmp_path: Path, key_bytes: bytes) -> None:
        """The control. Without it, `record_present is None` everywhere would pass."""
        found = _classify(tmp_path / "empty", tmp_path / "absent.artifact", key_bytes)

        assert found.record_present is False
        assert found.rerun_may_exchange_again is False
