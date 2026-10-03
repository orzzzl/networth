"""The sink's whole subject is *when* it is consulted, so that is what these pin.

`07b` criterion 1: *"The durable destination is chosen and verified **before** the
exchange is attempted, and the script refuses to exchange if it is not writable.
Verifying the sink after spending the token repeats the rev-17 mistake: a fallback
chosen after the irreversible step is not a fallback."*

That makes the interesting assertions negative ones — that :meth:`prepare` **raises**
in the cases that would otherwise raise at commit — and a negative assertion is worth
only the proof that its failure has one cause. So every refusal test below is paired
with the positive control that the same construction succeeds when the destination is
sound; without the pair, "prepare raised" is equally satisfied by a sink that refuses
everything.
"""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth.backup import crypto
from networth.link_fence import ATTESTATION, FENCED_INSTANCE, FenceAttestation
from networth.link_pairing import PairingResult, apply_pairing
from networth.link_sink import (
    ARTIFACT_SCHEMA,
    FENCE_FIELD,
    FLOW_PAIRING_FIELD,
    REQUIRED_FIELDS,
    ArtifactUnreadable,
    EmergencyArtifactSink,
    Pairing,
    RecoveredItem,
    ReplacementHostSink,
    SinkKind,
    SinkNotWritable,
    SinkWriteFailed,
    pair_from_artifact,
    restore,
)
from networth.storage import migrate
from networth.tokenstore import Secret, SecretKind, TokenStore, secret_ref_for

NOW = datetime(2026, 9, 29, 4, 30, tzinfo=UTC)

#: Generated at run time, never committed. The value is synthetic either way, but
#: `check-no-secrets.sh` refuses an access-token-shaped *literal* in this public
#: repo on purpose: a scanner that allowed a fixture through would have to tell
#: synthetic from real, and it cannot. It said so when this file first tried one.
ACCESS_TOKEN = "access-sandbox-" + secrets.token_hex(16)
ITEM_ID = "item-0000000000000000"
#: A *minted* flow id: `secret_ref_for` takes 32 lowercase hex and nothing else,
#: because the ref encodes it. A readable stand-in like `flow-0001` is rejected.
FLOW_ID = "1f0c9a2b3d4e5f60718293a4b5c6d7e8"
#: A `link_result` identity, distinct from every flow id here on purpose: the
#: write-back picks its destination by `result_id`, and a constant that happened to
#: equal a flow id would let that lookup be wrong and still pass.
RESULT_ID = "4b5c6d7e8f90a1b2c3d4e5f607182930"
STAMP = "2026-09-29T04:00:00Z"
OTHER_FLOW_ID = "9a8b7c6d5e4f30211203a4b5c6d7e8f9"
#: Criterion 6's attestation, as the verb would hand it over: confirmed a few minutes
#: before the exchange, which is the only ordering the artifact can later show.
FENCE = FenceAttestation(
    instance=FENCED_INSTANCE,
    statement=ATTESTATION,
    confirmed_at=datetime(2026, 9, 29, 4, 25, tzinfo=UTC),
)


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
    path.chmod(0o600)
    return path


def _payload_without(*absent: str) -> dict[str, object]:
    """A complete artifact plaintext, minus the named fields.

    Hand-built rather than produced by a sink, because what these check is the
    **reader**: a sink cannot write an artifact missing a field the type system requires,
    which is the point of that requirement and the reason the reader still needs its own
    refusal for files that were not written by this version.
    """
    payload: dict[str, object] = {
        "schema": ARTIFACT_SCHEMA,
        "flow_id": FLOW_ID,
        "item_id": ITEM_ID,
        "access_token": ACCESS_TOKEN,
        "link_session_id": "session-0001",
        "request_id": "request-0001",
        "fence": FENCE.to_record(),
        "recovered_at": NOW.isoformat(),
    }
    for field in absent:
        del payload[field]
    return payload


def item(**overrides: object) -> RecoveredItem:
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


class TestProofHappensBeforeTheExchange:
    """Criterion 1, for both branches: the refusal lands on the cheap side."""

    def test_replacement_host_refuses_an_unwritable_directory(self, tmp_path: Path) -> None:
        blocked = tmp_path / "locked" / "tokens"
        blocked.parent.mkdir(mode=0o500)
        sink = ReplacementHostSink(blocked)

        with pytest.raises(SinkNotWritable):
            sink.prepare()

    def test_and_the_control_accepts_a_sound_one(self, tmp_path: Path) -> None:
        # Without this the test above is satisfied by a sink that refuses
        # everything, and "prepare raised" would carry no information.
        ReplacementHostSink(tmp_path / "tokens").prepare()

    def test_artifact_refuses_a_key_its_own_loader_rejects(self, tmp_path: Path) -> None:
        # Mode 0644 is a key `03a`'s loader will not read. Discovering that after
        # the exchange is the mistake criterion 1 names, so it must surface here.
        loose = tmp_path / "loose.key"
        loose.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
        loose.chmod(0o644)
        sink = EmergencyArtifactSink(tmp_path / "artifact.sealed", key_file=loose)

        with pytest.raises(SinkNotWritable):
            sink.prepare()

    def test_and_the_control_accepts_an_escrowed_key(self, tmp_path: Path, key_file: Path) -> None:
        EmergencyArtifactSink(tmp_path / "artifact.sealed", key_file=key_file).prepare()

    def test_a_proof_leaves_nothing_behind(self, tmp_path: Path, key_file: Path) -> None:
        # The probe is removed, and the destination is untouched. A prepare that
        # left a file would make the artifact branch's own existence check below
        # refuse the very recovery it just proved possible.
        tokens = tmp_path / "tokens"
        ReplacementHostSink(tokens).prepare()
        assert [p.name for p in tokens.iterdir() if not p.name.startswith(".lock")] == []

        artifact = tmp_path / "artifact.sealed"
        EmergencyArtifactSink(artifact, key_file=key_file).prepare()
        assert not artifact.exists()

    def test_permission_bits_are_not_the_proof(self, tmp_path: Path, key_file: Path) -> None:
        """A key that is mode-0600 and *not a valid key* must still be refused.

        `os.access` and a mode check both pass here; only actually using the key
        fails. This is the test that separates "the file looks right" from "the
        write that happens after the irreversible step will succeed".
        """
        wrong_length = tmp_path / "short.key"
        wrong_length.write_text("00" * 16 + "\n")
        wrong_length.chmod(0o600)
        assert os.access(wrong_length, os.R_OK)

        with pytest.raises(SinkNotWritable):
            EmergencyArtifactSink(tmp_path / "a.sealed", key_file=wrong_length).prepare()


class TestRefusalsMovedOffTheExpensiveSide:
    """Each of these would otherwise raise *after* the token is spent."""

    def test_an_existing_artifact_is_refused_before_the_exchange(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = tmp_path / "artifact.sealed"
        artifact.write_bytes(b"an earlier recovery")
        sink = EmergencyArtifactSink(artifact, key_file=key_file)

        with pytest.raises(SinkNotWritable, match="already exists"):
            sink.prepare()

        assert artifact.read_bytes() == b"an earlier recovery", "it must not be overwritten"

    def test_an_existing_token_for_this_flow_is_refused_before_the_exchange(
        self, tmp_path: Path
    ) -> None:
        # `TokenStore.put` raises rather than overwrite, which is right and which
        # would otherwise raise after the exchange. `prepare_for` asks now.
        tokens = tmp_path / "tokens"
        TokenStore(tokens).put(SecretKind.ACCESS_TOKEN, FLOW_ID, ACCESS_TOKEN, item_id=ITEM_ID)
        sink = ReplacementHostSink(tokens)

        with pytest.raises(SinkNotWritable, match="already holds"):
            sink.prepare_for(FLOW_ID)

    def test_and_the_control_accepts_an_unused_flow(self, tmp_path: Path) -> None:
        tokens = tmp_path / "tokens"
        TokenStore(tokens).put(SecretKind.ACCESS_TOKEN, OTHER_FLOW_ID, ACCESS_TOKEN)

        ReplacementHostSink(tokens).prepare_for(FLOW_ID)

    def test_the_lock_the_write_needs_is_proven_and_not_just_the_directory(
        self, tmp_path: Path
    ) -> None:
        """PR #129 finding 2. The directory was writable; the lock was not.

        ``TokenStore.put`` takes ``.tokenstore.lock`` in the credential directory's
        **parent** (§14a, so it cannot be mistaken for archive payload), and the proof
        probed the credential directory and nothing above it. The token directory is
        created first and left mode-0700 here precisely so ``_prove_durable`` has
        nothing to complain about: the only thing wrong is the one the proof missed.
        """
        tokens = tmp_path / "host" / "tokens"
        tokens.mkdir(mode=0o700, parents=True)
        assert os.access(tokens, os.W_OK), "the directory the old proof checked is fine"
        tokens.parent.chmod(0o500)
        try:
            with pytest.raises(SinkNotWritable, match="tokenstore.lock") as refusal:
                ReplacementHostSink(tokens).prepare_for(FLOW_ID)
        finally:
            tokens.parent.chmod(0o700)

        # Named so the refusal cannot be confused with `_prove_durable`'s, which is
        # what would happen if this test passed for the old reason.
        assert "cannot be taken" in str(refusal.value)
        assert "Nothing has been exchanged" in str(refusal.value)

    def test_and_the_control_accepts_the_same_store_under_a_writable_parent(
        self, tmp_path: Path
    ) -> None:
        tokens = tmp_path / "host" / "tokens"
        tokens.mkdir(mode=0o700, parents=True)

        ReplacementHostSink(tokens).prepare_for(FLOW_ID)

        # And the proof really did take the lock rather than assume it: the file the
        # commit needs now exists, beside the store rather than inside it.
        assert (tokens.parent / ".tokenstore.lock").exists()
        assert not (tokens / ".tokenstore.lock").exists()

    def test_a_dangling_symlink_at_the_artifact_path_is_refused(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """PR #129 finding 2, second half: ``exists()`` follows the link.

        ``O_EXCL`` refuses to create a *name*, and a symlink to an absent target is a
        name. So the old check answered "nothing is there" and commit then failed
        ``EEXIST`` — on the far side of the exchange.
        """
        artifact = tmp_path / "artifact.sealed"
        artifact.symlink_to(tmp_path / "target-that-is-not-there")
        assert not artifact.exists(), "the premise: this is what the old check asked"
        assert os.path.lexists(artifact)

        with pytest.raises(SinkNotWritable, match="already exists"):
            EmergencyArtifactSink(artifact, key_file=key_file).prepare()

        assert os.readlink(artifact) == str(tmp_path / "target-that-is-not-there")
        assert not (tmp_path / "target-that-is-not-there").exists(), (
            "a refusal must not have created the target through the link"
        )

    def test_a_path_it_cannot_even_look_at_is_refused_rather_than_read_as_absent(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """Why ``os.lstat`` and not ``os.path.lexists``.

        ``lexists`` returns ``False`` for *every* ``OSError``, so an unreadable parent
        component would reproduce the same bug one level up — "nothing is there", then
        a commit that cannot create the file. Substituting ``lexists`` for the
        ``os.lstat`` above turns this test red, and it is the only one that does.

        **What it cannot pin, measured rather than assumed:** substituting
        ``Path.exists()`` leaves it *green* on this interpreter, because ``exists()``
        only began swallowing every ``OSError`` in 3.13 and 3.12.14 still propagates
        ``EACCES`` here — so the mutant answers correctly for the wrong reason. The
        dangling-symlink test above is version-independent and catches that one. Two
        tests for one line because the two wrong implementations fail differently.
        """
        blind = tmp_path / "blind"
        blind.mkdir(mode=0o700)
        artifact = blind / "artifact.sealed"
        blind.chmod(0o000)
        try:
            assert not os.path.lexists(artifact), "the premise: lexists says absent"
            with pytest.raises(SinkNotWritable, match="cannot tell whether"):
                EmergencyArtifactSink(artifact, key_file=key_file).prepare()
        finally:
            blind.chmod(0o700)

    def test_and_the_control_accepts_a_path_with_nothing_at_it(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        EmergencyArtifactSink(tmp_path / "artifact.sealed", key_file=key_file).prepare()


class TestCommitRefusesToRunUnprepared:
    def test_replacement_host(self, tmp_path: Path) -> None:
        with pytest.raises(SinkWriteFailed, match="before prepare"):
            ReplacementHostSink(tmp_path / "tokens").commit(item(), now=NOW)

    def test_emergency_artifact(self, tmp_path: Path, key_file: Path) -> None:
        with pytest.raises(SinkWriteFailed, match="before prepare"):
            EmergencyArtifactSink(tmp_path / "a.sealed", key_file=key_file).commit(item(), now=NOW)

    def test_a_filesystem_refusal_at_commit_arrives_as_this_modules_own_error(
        self, tmp_path: Path
    ) -> None:
        """PR #129 finding 2, third part: the normalisation, not the preflight.

        The preflight above now refuses this case, so reaching commit takes a change
        *between* the two calls — which is exactly the window the normalisation is for.
        The lock is an ``os.open``, so what came out was ``PermissionError``: an
        ``OSError``, past ``except TokenStoreError`` here and past every
        ``except SinkError`` in the callers. The most expensive failure this module has
        was the one kind it did not name.
        """
        tokens = tmp_path / "host" / "tokens"
        tokens.mkdir(mode=0o700, parents=True)
        sink = ReplacementHostSink(tokens)
        sink.prepare_for(FLOW_ID)
        (tokens.parent / ".tokenstore.lock").unlink()
        tokens.parent.chmod(0o500)
        try:
            with pytest.raises(SinkWriteFailed, match="did not reach"):
                sink.commit(item(), now=NOW)
        finally:
            tokens.parent.chmod(0o700)


class TestWhatLandsAndWhatIsStillOwed:
    """Criterion 2's four fields, and the receipt that may not overstate them."""

    def test_the_replacement_host_reports_the_three_it_cannot_hold(self, tmp_path: Path) -> None:
        sink = ReplacementHostSink(tmp_path / "tokens")
        sink.prepare_for(FLOW_ID)

        receipt = sink.commit(item(), now=NOW)

        assert receipt.kind is SinkKind.REPLACEMENT_HOST
        assert receipt.durable == ("access_token", "item_id")
        # The fence attestation is the third, and it is owed for the same reason as the
        # other two: a `SecretRecord` holds `item_id` and nothing else. Criterion 6 puts
        # it *"with the recovery artifact"*, and this branch has none.
        assert receipt.owed == ("link_session_id", "request_id", FENCE_FIELD)
        assert receipt.pairing is Pairing.ON_FLOW_ROW
        assert not receipt.complete, "a receipt with fields owed is not a finished recovery"

    def test_the_artifact_holds_all_four_and_the_fence(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        sink = EmergencyArtifactSink(tmp_path / "artifact.sealed", key_file=key_file)
        sink.prepare()

        receipt = sink.commit(item(), now=NOW)

        assert receipt.kind is SinkKind.EMERGENCY_ARTIFACT
        assert receipt.durable == (*REQUIRED_FIELDS, FENCE_FIELD)
        assert receipt.owed == ()
        assert receipt.pairing is Pairing.IN_ARTIFACT
        assert receipt.complete

    def test_the_credential_is_actually_on_disk_and_readable(self, tmp_path: Path) -> None:
        tokens = tmp_path / "tokens"
        sink = ReplacementHostSink(tokens)
        sink.prepare_for(FLOW_ID)

        sink.commit(item(), now=NOW)

        store = TokenStore(tokens)
        reference = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)
        assert store.get(reference).reveal() == ACCESS_TOKEN
        assert store.record(reference).item_id == ITEM_ID


class TestAnUnfencedRecoveryCannotBeConstructed:
    """Criterion 6 in the type, and it needs its own test for a specific reason.

    A required constructor parameter is **invisible to mutation testing**: giving
    ``fence`` a default of ``None`` leaves every call site in the repo valid and every
    other test green, because they all pass one. Only an assertion that the absence is a
    ``TypeError`` can go red for that edit, so the structural guard gets an explicit
    test rather than being trusted to the suite at large.
    """

    def test_a_recovered_item_without_an_attestation_is_a_type_error(self) -> None:
        with pytest.raises(TypeError, match="fence"):
            RecoveredItem(  # type: ignore[call-arg]
                access_token=Secret(ACCESS_TOKEN),
                item_id=ITEM_ID,
                flow_id=FLOW_ID,
                link_session_id=None,
                request_id=None,
            )

    def test_and_the_control_constructs_with_one(self) -> None:
        assert item().fence is FENCE


class TestTheArtifactIsAnArtifactAndNotJustBytes:
    def test_it_is_sealed_rather_than_written(self, tmp_path: Path, key_file: Path) -> None:
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()

        sink.commit(item(), now=NOW)

        raw = artifact.read_bytes()
        assert ACCESS_TOKEN.encode() not in raw, "§15: no runtime secret in the clear on this Mac"
        assert ITEM_ID.encode() not in raw
        assert raw.startswith(crypto.MAGIC), "03a's envelope, not a second one"
        assert artifact.stat().st_mode & 0o077 == 0

    def test_it_carries_the_pairing_criterion_3_needs(self, tmp_path: Path, key_file: Path) -> None:
        # *"An artifact that carries the credential without the pairing recreates
        # the defect one step later."*
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        payload = json.loads(
            crypto.open_sealed(artifact.read_bytes(), crypto.load_backup_key(key_file))
        )

        assert payload["schema"] == ARTIFACT_SCHEMA
        assert payload["flow_id"] == FLOW_ID
        assert payload["item_id"] == ITEM_ID
        assert payload["link_session_id"] == "session-0001"
        assert payload["request_id"] == "request-0001"

    def test_it_carries_the_fence_criterion_6_requires_be_recorded_with_it(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        # *"the script takes an explicit typed confirmation naming that instance,
        # **recorded with the recovery artifact**"*. Inside the sealed plaintext, so the
        # attestation cannot be separated from the credential it fenced — and with the
        # statement, so a reader a year later knows what was confirmed rather than only
        # that something was.
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        payload = json.loads(
            crypto.open_sealed(artifact.read_bytes(), crypto.load_backup_key(key_file))
        )

        assert payload["fence"] == {
            "instance": FENCED_INSTANCE,
            "statement": ATTESTATION,
            "confirmed_at": FENCE.confirmed_at.isoformat(),
        }
        assert FenceAttestation.from_record(payload["fence"]).confirmed_at < NOW, (
            "the fence is confirmed before the exchange, and the artifact must be able "
            "to show that rather than assert it"
        )

    def test_a_credential_that_arrives_unfenced_is_not_a_recovery(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        # The reading side of criterion 6. A sealed file that opens under the escrowed
        # key, names this schema and carries a live credential is still refused when
        # nothing in it says the host that could also exchange was turned off: the
        # restore would otherwise put that token into a real store on the evidence of
        # nobody.
        key = crypto.load_backup_key(key_file)
        unfenced = tmp_path / "unfenced.sealed"
        unfenced.write_bytes(crypto.seal(json.dumps(_payload_without("fence")).encode(), key))

        with pytest.raises(ArtifactUnreadable, match="not fenced"):
            restore(
                unfenced,
                key_file=key_file,
                token_store_directory=tmp_path / "tokens",
                connection=None,
            )

    def test_and_the_control_restores_when_the_same_payload_carries_one(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        # Without this pair, "restore raised" is equally satisfied by a hand-built
        # payload this reader would never have accepted anyway.
        key = crypto.load_backup_key(key_file)
        fenced = tmp_path / "fenced.sealed"
        fenced.write_bytes(crypto.seal(json.dumps(_payload_without()).encode(), key))

        outcome = restore(
            fenced,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=None,
        )

        # The credential half, not `complete` — criterion 3's write-back needs a
        # database this control deliberately does not have, and a control for the
        # *fence* reader must not start failing over an unrelated criterion.
        assert outcome.receipt.durable == (*REQUIRED_FIELDS, FENCE_FIELD)

    @pytest.mark.parametrize(
        "broken",
        [
            {"instance": FENCED_INSTANCE, "statement": ATTESTATION},
            {"instance": "", "statement": ATTESTATION, "confirmed_at": NOW.isoformat()},
            {
                "instance": FENCED_INSTANCE,
                "statement": ATTESTATION,
                "confirmed_at": NOW.replace(tzinfo=None).isoformat(),
            },
            True,
        ],
    )
    def test_a_fence_record_that_says_nothing_usable_is_refused_too(
        self, tmp_path: Path, key_file: Path, broken: object
    ) -> None:
        # An attestation that cannot be read is not weaker evidence than one that is
        # absent; it is the same evidence, and the third case is why the instant must
        # carry an offset: "before the exchange" is the only claim its clock makes.
        key = crypto.load_backup_key(key_file)
        artifact = tmp_path / "broken.sealed"
        payload = _payload_without()
        payload["fence"] = broken
        artifact.write_bytes(crypto.seal(json.dumps(payload).encode(), key))

        with pytest.raises(ArtifactUnreadable, match="not fenced"):
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tmp_path / "tokens",
                connection=None,
            )

    def test_a_backup_archive_is_not_mistaken_for_one(self, tmp_path: Path, key_file: Path) -> None:
        # The envelope is shared with `03a`'s archives, so the schema name is what
        # distinguishes them. A restore pointed at the wrong file must fail on the
        # name rather than on a KeyError three fields later.
        key = crypto.load_backup_key(key_file)
        impostor = tmp_path / "archive.sealed"
        impostor.write_bytes(crypto.seal(json.dumps({"schema": "something.else"}).encode(), key))

        with pytest.raises(ArtifactUnreadable, match=ARTIFACT_SCHEMA):
            restore(
                impostor,
                key_file=key_file,
                token_store_directory=tmp_path / "tokens",
                connection=None,
            )

    def test_a_missing_optional_field_is_not_the_same_as_a_null_one(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        # Present-and-None is a measurement; absent is an older or truncated
        # writer. Issue #14 makes the support id the fallback when a credential is
        # lost, so the two must not be read as the same thing.
        key = crypto.load_backup_key(key_file)
        truncated = tmp_path / "truncated.sealed"
        truncated.write_bytes(
            crypto.seal(
                json.dumps(
                    {
                        "schema": ARTIFACT_SCHEMA,
                        "flow_id": FLOW_ID,
                        "item_id": ITEM_ID,
                        "access_token": ACCESS_TOKEN,
                        "link_session_id": None,
                    }
                ).encode(),
                key,
            )
        )

        with pytest.raises(ArtifactUnreadable, match="request_id"):
            restore(
                truncated,
                key_file=key_file,
                token_store_directory=tmp_path / "tokens",
                connection=None,
            )

    def test_another_key_cannot_open_it(self, tmp_path: Path, key_file: Path) -> None:
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        other = tmp_path / "other.key"
        other.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
        other.chmod(0o600)

        with pytest.raises(ArtifactUnreadable):
            restore(
                artifact, key_file=other, token_store_directory=tmp_path / "tokens", connection=None
            )


class TestTheRestorePathExists:
    """Criterion 1 requires the artifact branch to have one, not to imply one."""

    def test_an_artifact_restores_into_a_real_token_store(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        tokens = tmp_path / "tokens"
        outcome = restore(
            artifact, key_file=key_file, token_store_directory=tokens, connection=None
        )

        store = TokenStore(tokens)
        reference = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)
        assert store.get(reference).reveal() == ACCESS_TOKEN
        assert store.record(reference).item_id == ITEM_ID
        # The pairing came out of the artifact, which is criterion 3's destination
        # for this branch — not the flow row the replacement-host sink reports.
        assert outcome.receipt.pairing is Pairing.IN_ARTIFACT

    def test_a_restore_owes_nothing_it_is_already_carrying(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The receipt describes what the owner durably has, not what this call wrote.

        The replacement-host sink reports as ``owed`` exactly what a ``SecretRecord``
        cannot hold — and on this path every one of those things is in the artifact this
        function just opened. Passing that sink's tuples straight through was harmless
        while they were two support ids; with criterion 6's attestation among them it
        would report the fence missing while reading it out of the file.
        """
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        outcome = restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=None,
        )

        assert outcome.receipt.durable == (*REQUIRED_FIELDS, FENCE_FIELD)
        # **Exactly** criterion 3's field, which is what makes this an assertion about
        # the union rather than about emptiness: the fence and the two support ids are
        # in the artifact this call opened, so none of them may appear here, while the
        # flow pairing has nowhere to land without a database and must.
        assert outcome.receipt.owed == (FLOW_PAIRING_FIELD,)
        assert not outcome.complete

    def test_a_restore_that_would_overwrite_is_refused(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)

        tokens = tmp_path / "tokens"
        TokenStore(tokens).put(SecretKind.ACCESS_TOKEN, FLOW_ID, "an-earlier-token")

        with pytest.raises(SinkNotWritable, match="already holds"):
            restore(artifact, key_file=key_file, token_store_directory=tokens, connection=None)


class TestTheRestoreAppliesTheFlowPairing:
    """Criterion 3's second destination: *"applied during restore"*.

    The restore is where both of the write-back's inputs exist at once — the
    ``item_id`` arrives sealed beside the credential, and the ``link_flow`` row it
    names lives in this host's database. Neither ``commit`` can do it, which is why
    the pairing travels in the artifact at all.
    """

    def sealed(self, tmp_path: Path, key_file: Path) -> Path:
        artifact = tmp_path / "artifact.sealed"
        sink = EmergencyArtifactSink(artifact, key_file=key_file)
        sink.prepare()
        sink.commit(item(), now=NOW)
        return artifact

    def database(self, tmp_path: Path, *, state: str | None = "SUCCESS_PENDING_EXCHANGE") -> Any:
        """A replacement host's database, restored from backup.

        ``state=None`` is the shape this emergency usually has: the newest archive
        predates the Link, so the success row the recovery would name was never in
        it.
        """
        connection = sqlite3.connect(tmp_path / "replacement.sqlite")
        migrate(connection)
        connection.execute(
            "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
            "VALUES (?, ?, ?, 'URL_MINTED')",
            (FLOW_ID, STAMP, STAMP),
        )
        if state is not None:
            connection.execute(
                "INSERT INTO link_result(result_id, flow_id, token_digest, state) "
                "VALUES (?, ?, 'synthetic-digest', ?)",
                (RESULT_ID, FLOW_ID, state),
            )
        connection.commit()
        return connection

    def test_the_recovered_item_reaches_the_flow_row_and_nothing_stays_owed(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = self.sealed(tmp_path, key_file)
        connection = self.database(tmp_path)

        outcome = restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        assert outcome.pairing is not None
        assert outcome.pairing.result is PairingResult.WRITTEN
        assert outcome.receipt.owed == ()
        assert outcome.complete
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (ITEM_ID,)
        connection.close()

    def test_a_database_older_than_the_link_leaves_the_pairing_owed(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = self.sealed(tmp_path, key_file)
        connection = self.database(tmp_path, state=None)

        outcome = restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        assert outcome.pairing is not None
        assert outcome.pairing.result is PairingResult.NO_ROW
        assert outcome.receipt.owed == (FLOW_PAIRING_FIELD,)
        assert not outcome.complete
        # The credential is the half that must never be in doubt: a pairing that had
        # nowhere to go does not make the restore a failure, and the token is spent.
        assert (
            TokenStore(tmp_path / "tokens")
            .get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID))
            .reveal()
            == ACCESS_TOKEN
        )
        connection.close()

    def test_a_refusal_is_reported_rather_than_raised_over_a_durable_credential(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The order criterion 3's write-back is applied in, asserted as behaviour.

        A row that reached ``EXCHANGED`` without an Item name makes the write-back
        refuse. Raising here would present a recovery whose credential is durable as
        a failure and invite a re-run, and a re-run exchanges a token that is already
        spent — `06a` (ii) measured that second exchange as **ACCEPTED**. So the
        refusal becomes an owed field carrying its own words.
        """
        artifact = self.sealed(tmp_path, key_file)
        connection = self.database(tmp_path, state="EXCHANGED")

        outcome = restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        assert outcome.pairing is not None
        assert outcome.pairing.result is PairingResult.REFUSED
        assert "criterion 7" in outcome.pairing.detail
        assert outcome.receipt.owed == (FLOW_PAIRING_FIELD,)
        assert not outcome.complete
        assert (
            TokenStore(tmp_path / "tokens")
            .get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID))
            .reveal()
            == ACCESS_TOKEN
        )
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (None,)
        connection.close()

    def test_restoring_the_same_artifact_twice_is_still_complete(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The second run's ``TokenStore`` refusal comes first, and that is correct.

        The credential is already there, so ``prepare_for`` refuses before anything
        is written — which means an operator who re-runs a restore to *finish the
        pairing* cannot get there this way. Asserted so the limitation is recorded
        rather than discovered during criterion 5's rehearsal: finishing an owed
        pairing needs the write-back on its own, not another restore.
        """
        artifact = self.sealed(tmp_path, key_file)
        connection = self.database(tmp_path)
        restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        with pytest.raises(SinkNotWritable, match="already holds"):
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tmp_path / "tokens",
                connection=connection,
            )

        assert apply_pairing(connection, flow_id=FLOW_ID, item_id=ITEM_ID).result is (
            PairingResult.ALREADY_PAIRED
        )
        connection.close()

    def test_the_pairing_can_be_applied_alone_because_the_restore_cannot_repeat(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """What the previous test's limitation makes necessary.

        ``pair_from_artifact`` reads the artifact for its two identifiers and touches
        no credential, which is the only way an owed pairing can be finished once the
        credential is already stored.
        """
        artifact = self.sealed(tmp_path, key_file)
        connection = self.database(tmp_path)
        restore(
            artifact,
            key_file=key_file,
            token_store_directory=tmp_path / "tokens",
            connection=None,
        )

        outcome = pair_from_artifact(artifact, key_file=key_file, connection=connection)

        assert outcome.result is PairingResult.WRITTEN
        assert connection.execute(
            "SELECT item_id FROM link_result WHERE result_id = ?", (RESULT_ID,)
        ).fetchone() == (ITEM_ID,)
        connection.close()

    def test_applying_the_pairing_alone_still_requires_a_fenced_artifact(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """Criterion 6 is not weaker just because no credential moves.

        It reads through :func:`read_artifact`, so an unfenced file cannot be a source
        of accounting either — the same refusal, one column over.
        """
        key = crypto.load_backup_key(key_file)
        unfenced = tmp_path / "unfenced.sealed"
        unfenced.write_bytes(crypto.seal(json.dumps(_payload_without("fence")).encode(), key))
        connection = self.database(tmp_path)

        with pytest.raises(ArtifactUnreadable, match="not fenced"):
            pair_from_artifact(unfenced, key_file=key_file, connection=connection)

        connection.close()

    def test_the_connection_argument_cannot_be_left_off(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """A structural guard, and mutation testing cannot see it.

        Removing the keyword-only parameter leaves every call site valid, so no
        behavioural test goes red — while a caller that omitted it would silently get
        the credential-only restore this criterion exists to end. The type is the
        guard; this is the test that the type is still there.
        """
        artifact = self.sealed(tmp_path, key_file)

        with pytest.raises(TypeError, match="connection"):
            restore(  # type: ignore[call-arg]
                artifact, key_file=key_file, token_store_directory=tmp_path / "tokens"
            )


class TestNothingPrintsMaterial:
    def test_the_repr_redacts_the_token_and_the_item_id(self) -> None:
        # `item_id` is not a secret; it names one of the owner's institutions
        # (`AGENTS.md` rule 0), and this object is what a traceback renders.
        rendered = repr(item())

        assert ACCESS_TOKEN not in rendered
        assert ITEM_ID not in rendered
        assert "session-0001" not in rendered
        assert FLOW_ID in rendered, "the flow id is the one thing an operator needs to read"

    def test_no_refusal_message_carries_material(self, tmp_path: Path) -> None:
        tokens = tmp_path / "tokens"
        TokenStore(tokens).put(SecretKind.ACCESS_TOKEN, FLOW_ID, ACCESS_TOKEN, item_id=ITEM_ID)

        with pytest.raises(SinkNotWritable) as raised:
            ReplacementHostSink(tokens).prepare_for(FLOW_ID)

        assert ACCESS_TOKEN not in str(raised.value)
        assert ITEM_ID not in str(raised.value)
