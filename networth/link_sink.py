"""Where a recovered ``access_token`` will land — decided and **proven** before it exists.

`tasks/README.md` `07b` is one task with one defect at its centre, and the defect is
the *last* step of `DESIGN.md` §19 step 2a. The scenario: a Production Link has
succeeded, so **F2a** has already spent one of ten lifetime Item slots; the VPS is
lost before the exchange; the owner runs `scripts/link-recover.sh` on
`zelengs-macbook-air-2` inside the ``public_token``'s 30 minutes. That script and its
verb already exist — `06a` measurement (iv) built them — and on the Mac path the verb
**persists nothing on purpose** (`complete_hosted_link.py`: *"stored nothing —
--from-tty persists no credential (§15)"*). For a measurement that is correct. For the
emergency it means the procedure consumes the one-time token, receives a long-lived
credential, and holds it in a process with nowhere durable to put it — one laptop
crash from recreating the permanent slot loss it exists to prevent.

This module is that "somewhere durable", and the whole of its value is in *when* it is
consulted rather than in how it writes. `07b`'s first criterion:

    **The durable destination is chosen and verified *before* the exchange is
    attempted, and the script refuses to exchange if it is not writable.** …
    Verifying the sink after spending the token repeats the rev-17 mistake: a
    fallback chosen after the irreversible step is not a fallback.

So the interface is two calls and the order between them is the point.
:meth:`DurableSink.prepare` runs before ``/item/public_token/exchange`` and either
proves the destination or raises; :meth:`DurableSink.commit` runs after, and by then
every failure it could still hit has been excluded rather than hoped about.

**Two kinds, chosen explicitly, never by fallback.** The criterion says *"Either a
replacement host with a ready ``TokenStore``, or a Mac-side emergency artifact
encrypted under the already-escrowed backup key (`00b`) with a restore path into a
real ``TokenStore``"*, and both branches are intended rather than one tolerated: a
script built to the narrow reading would **refuse to run when no replacement host
exists**, which is the actual emergency — standing up a replacement is not a
thirty-minute step. What must not happen is a *fallback* from one to the other.
`AGENTS.md` rule 1 has the reason in general form — a lookup that falls back from one
host's secrets directory to the other's "is how a path bug becomes 'it worked on my
machine' for a file holding access tokens" — and here it has a second one: the two
kinds put the credential on **different computers**, and that is not a detail to
resolve by whichever path happened to open.

**What "verified" means, and it is not ``os.access``.** ``os.access(path, W_OK)``
answers a question about permission bits; it returns true on a read-only mount, on a
full filesystem, and on a directory whose ACL denies what its mode permits. Its pass
condition is the absence of one specific error, and this pass has to mean "the write
that happens after the irreversible step will succeed". So the proof is the write
itself: :func:`_prove_durable` creates a probe through the same syscalls
(``O_CREAT|O_EXCL``, ``fsync``, read back, ``fsync`` the directory), then removes it.

**And for the artifact kind the key is part of the proof.** A sealed file that cannot
be opened is not a durable destination, it is a durable loss — so ``prepare`` loads
the escrowed backup key and round-trips a probe through :func:`crypto.seal` and
:func:`crypto.open_sealed` before the token is spent. The loaded key is then **held
for the commit rather than re-read**, deliberately: re-reading it afterwards would
move a failure that `03a`'s own loader can raise (mode, encoding, length) to the far
side of the exchange, which is the exact mistake criterion 1 exists to forbid.

No new key is invented, per the must-not: the artifact is sealed under the backup key
that `03a` created and `00b` escrowed, with `03a`'s own construction.

**Nothing here prints or logs material.** Every exception message names paths and
field names only; :class:`RecoveredItem` redacts its ``access_token`` *and* its
``item_id``, the second because an Item id names one of the owner's institutions
(`AGENTS.md` rule 0) even though it is not a secret.

**What this module deliberately does not do.** It does not write the recovered
``item_id`` back onto the originating ``link_flow`` row — that is `07b`'s third
criterion, it needs `07a`'s table, and its two destinations differ by sink. What this
module owes that criterion is the ability to *say which destination applies* without a
caller guessing, which is :class:`Pairing`, and the ability to refuse to overstate what
landed, which is :attr:`SinkReceipt.owed`. A receipt with a non-empty ``owed`` is not a
successful recovery, and the caller that reports one as such is wrong in a way a
reader can see.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from networth.backup import crypto
from networth.tokenstore import (
    FILE_MODE,
    Secret,
    SecretKind,
    TokenStore,
    TokenStoreError,
    secret_ref_for,
)

#: The artifact's *inner* schema. The envelope is `03a`'s (``crypto.MAGIC`` and its
#: format version, authenticated as AAD), because the must-not forbids inventing a
#: second key and there is no reason to invent a second envelope either. That makes an
#: artifact indistinguishable from a backup archive until it is opened, which is why
#: the plaintext names itself: a restore pointed at the wrong file must fail on the
#: schema rather than on a ``KeyError`` three fields later.
ARTIFACT_SCHEMA: Final = "networth.link-recovery-artifact.1"

#: The four fields `07b` criterion 2 requires be durable before recovery is reported
#: successful: *"``access_token`` **and** ``item_id`` are written, ``fsync``ed, **read
#: back**, and only then is recovery reported successful. Also persist
#: ``link_session_id`` and the exchange ``request_id`` (issue #14) — in this scenario
#: the support ticket is the fallback."*
REQUIRED_FIELDS: Final = ("access_token", "item_id", "link_session_id", "request_id")

_PROBE_PLAINTEXT: Final = b"networth link-sink durability probe; holds no material\n"


class SinkError(Exception):
    """Base. Every message raised from this module names paths and fields only."""


class SinkNotWritable(SinkError):
    """The destination failed its proof **before** the exchange.

    The one-time ``public_token`` has not been spent, so the caller's obligation is
    simply to stop. Refusing here costs nothing; the Link slot is already spent by
    **F2a** either way, and the token remains exchangeable for the rest of its 30
    minutes once the destination is fixed.
    """


class SinkWriteFailed(SinkError):
    """The write failed **after** the exchange, and this is the expensive one.

    A long-lived credential now exists for an Item the owner has paid a lifetime slot
    for, and it is in this process's memory and nowhere else. Raised only when
    :meth:`DurableSink.prepare` had already succeeded, which is why it should be
    unreachable in the failures ``prepare`` covers and why it is a distinct class: a
    caller may not treat it as "try again", because trying again means exchanging a
    token that has already been consumed.
    """


class ArtifactUnreadable(SinkError):
    """A sealed artifact is not this schema, or will not open under this key."""


class SinkKind(Enum):
    """Which of criterion 1's two destinations a sink is. Never inferred."""

    REPLACEMENT_HOST = "replacement-host"
    EMERGENCY_ARTIFACT = "emergency-artifact"


class Pairing(Enum):
    """Where the recovered ``item_id`` ↔ ``link_flow`` pairing lives, per criterion 3.

    *"The write-back has two destinations and the script must say which one it used:
    onto the restored ``link_flow`` copy when recovery lands on a replacement host, or
    carried inside the Mac-side emergency artifact and applied during restore when the
    originating row is simply gone with the VPS. An artifact that carries the
    credential without the pairing recreates the defect one step later."*

    A two-valued fact with different operator consequences is an enum rather than a
    sentence, so the caller that has to print which one it used cannot print neither.
    """

    #: The artifact holds the pairing itself; :func:`restore` applies it.
    IN_ARTIFACT = "carried inside the emergency artifact, applied during restore"
    #: The replacement host's ``link_flow`` copy is the destination, and the write-back
    #: has not happened yet — see :attr:`SinkReceipt.owed`.
    ON_FLOW_ROW = "onto the replacement host's restored link_flow row"


@dataclass(frozen=True, slots=True, repr=False)
class RecoveredItem:
    """One exchanged Item, with everything criterion 2 requires be persisted.

    ``link_session_id`` and ``request_id`` are optional because the wire makes them
    so — :class:`~networth.plaid.client.ExchangedItem` accepts a missing
    ``request_id`` rather than discarding a valid credential over a support id, and
    the session id comes from the poll, which in the uncertain shapes may not name
    one. They are still *stored* fields: ``None`` is recorded as ``None``, which is a
    measurement, rather than omitted, which is indistinguishable from an older writer.
    """

    access_token: Secret
    item_id: str
    flow_id: str
    link_session_id: str | None
    request_id: str | None

    def __repr__(self) -> str:
        # Presence, never values. ``item_id`` is redacted for a different reason than
        # the token: it is not a secret, it names one of the owner's institutions
        # (`AGENTS.md` rule 0), and this object is what a traceback renders.
        return (
            f"RecoveredItem(flow_id={self.flow_id!r}, item_id=<redacted>, "
            f"access_token=<Secret: redacted>, "
            f"link_session_id={'<redacted>' if self.link_session_id else 'None'}, "
            f"request_id={self.request_id!r})"
        )


@dataclass(frozen=True, slots=True)
class SinkReceipt:
    """What actually landed, in terms a caller cannot round up.

    ``durable`` lists the fields that were written, ``fsync``ed **and read back** at
    ``destination``. ``owed`` lists the ones that still must land elsewhere before
    recovery is complete. Criterion 2 says recovery is reported successful only after
    the read-back, and criterion 3 only after the flow pairing; a receipt with a
    non-empty ``owed`` therefore describes an *incomplete* recovery, and saying so in
    the return value is what stops the next slice from reporting a half-written
    recovery as a finished one.
    """

    kind: SinkKind
    destination: str
    durable: tuple[str, ...]
    owed: tuple[str, ...]
    pairing: Pairing

    @property
    def complete(self) -> bool:
        """Did every field criterion 2 names reach durable storage *here*?"""
        return not self.owed


class DurableSink(Protocol):
    """Two calls, and the order between them is this module's entire subject."""

    kind: SinkKind

    @property
    def destination(self) -> str:
        """Which destination this is, in one printable line naming a real path."""

    def prepare(self) -> None:
        """Prove the destination durable, or raise :class:`SinkNotWritable`.

        Called **before** the exchange. Must leave nothing behind on success.
        """

    def commit(self, item: RecoveredItem, *, now: datetime) -> SinkReceipt:
        """Write, ``fsync``, read back. Called only after :meth:`prepare` returned."""


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_exactly(fd: int, payload: bytes) -> None:
    """``os.write`` may write less than asked; a short write is a lost credential.

    Regular files do not short-write in practice, which is precisely why an unchecked
    ``os.write`` survives every test and then matters once. Checked rather than
    trusted, and looped rather than asserted, because the correct response to a short
    write is to finish the write.
    """
    written = 0
    while written < len(payload):
        chunk = os.write(fd, payload[written:])
        if chunk <= 0:  # pragma: no cover — POSIX forbids it; refuse rather than spin
            raise OSError("write made no progress")
        written += chunk


def _prove_durable(directory: Path, *, what: str) -> None:
    """Prove ``directory`` will accept the write that happens after the exchange.

    A probe through the same syscalls the real write uses, then removed. The name is
    dot-prefixed and randomised: :class:`TokenStore` has no directory listing at all —
    *"deliberately absent: any read that returns more than one secret"* — so a probe
    left behind by a crash cannot be enumerated into anything, and the random suffix
    keeps two concurrent proofs from colliding on ``O_EXCL`` and reporting the
    directory unwritable when it is merely busy.
    """
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise SinkNotWritable(
            f"{what}: cannot create the destination directory {directory}: {exc.strerror}"
        ) from None

    probe = directory / f".link-sink-probe.{secrets.token_hex(8)}"
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
    except OSError as exc:
        raise SinkNotWritable(
            f"{what}: {directory} did not accept a probe file: {exc.strerror}. "
            "Nothing has been exchanged and no Item slot is at risk"
        ) from None
    try:
        try:
            _write_exactly(fd, _PROBE_PLAINTEXT)
            os.fsync(fd)
        finally:
            os.close(fd)
        if probe.read_bytes() != _PROBE_PLAINTEXT:
            raise SinkNotWritable(
                f"{what}: the probe at {directory} did not read back what was written"
            )
        _fsync_directory(directory)
    except OSError as exc:
        raise SinkNotWritable(
            f"{what}: {directory} accepted a probe but could not make it durable: {exc.strerror}"
        ) from None
    finally:
        with contextlib.suppress(OSError):
            probe.unlink()
        with contextlib.suppress(OSError):
            _fsync_directory(directory)


class ReplacementHostSink:
    """Criterion 1's first branch: a replacement host with a ready ``TokenStore``.

    The credential lands where every other credential in this system lands, which is
    the whole appeal of this branch — there is no second format to restore from and no
    second thing to get right. What it cannot hold is the rest of criterion 2:
    :class:`~networth.tokenstore.SecretRecord` carries ``item_id`` and nothing else
    Plaid returned, so ``link_session_id`` and ``request_id`` belong to the
    ``link_flow`` row on that host and are reported as :attr:`SinkReceipt.owed` rather
    than quietly dropped.
    """

    kind = SinkKind.REPLACEMENT_HOST

    def __init__(self, token_store_directory: Path) -> None:
        self._directory = Path(token_store_directory)
        self._store: TokenStore | None = None

    @property
    def destination(self) -> str:
        return f"TokenStore at {self._directory}"

    def prepare(self) -> None:
        _prove_durable(self._directory, what="replacement-host TokenStore")
        store = TokenStore(self._directory)
        self._store = store

    def prepare_for(self, flow_id: str) -> None:
        """:meth:`prepare`, plus the refusal that only a known ``flow_id`` allows.

        ``TokenStore.put`` raises :class:`~networth.tokenstore.SecretRefExists` rather
        than overwrite, which is correct and which would otherwise raise it **after**
        the exchange. Asking now turns that into a pre-exchange refusal.

        Separate from :meth:`prepare` because the ``flow_id`` is not always known when
        the destination is chosen, and a proof that needs an argument the caller may
        not have yet is a proof that gets skipped.
        """
        self.prepare()
        assert self._store is not None
        try:
            existing = self._store.reconcile(flow_id)
        except TokenStoreError as exc:
            # `reconcile` raises `UnverifiedMaterial` when it finds material under the
            # pending name and cannot complete the durability barrier. That is the one
            # answer that must not be read as "nothing there": both "exchange again"
            # and "commit over it" spend or lose an Item. Refuse before the token is
            # spent, which is the cheap side of the uncertainty.
            raise SinkNotWritable(
                f"replacement-host TokenStore at {self._directory} holds material for "
                f"this flow that it cannot verify: {exc}. Resolve that before exchanging"
            ) from None
        if existing is not None:
            raise SinkNotWritable(
                f"replacement-host TokenStore at {self._directory} already holds an "
                f"access token for flow {flow_id}; it will not be overwritten. If that "
                "material is the recovery you are repeating, the exchange is already done"
            )

    def commit(self, item: RecoveredItem, *, now: datetime) -> SinkReceipt:
        del now  # A TokenStore record stamps its own `created_at`; two clocks, one fact.
        store = self._store
        if store is None:
            raise SinkWriteFailed(
                "commit() was called before prepare(); the destination was never proven"
            )
        reference = secret_ref_for(SecretKind.ACCESS_TOKEN, item.flow_id)
        try:
            store.put(
                SecretKind.ACCESS_TOKEN,
                item.flow_id,
                item.access_token.reveal(),
                item_id=item.item_id,
            )
        except TokenStoreError as exc:
            raise SinkWriteFailed(
                f"the exchange succeeded and the credential did not reach {self._directory}: {exc}"
            ) from None
        try:
            stored = store.get(reference).reveal()
            record = store.record(reference)
        except TokenStoreError as exc:
            raise SinkWriteFailed(
                f"the credential was written to {self._directory} and did not read back: {exc}"
            ) from None
        # A byte comparison of the material, not a "the file parses" check: a
        # truncated record can still parse into an object that looks right, which is
        # the reason `link_recovery.store_and_verify` compares bytes too.
        if stored != item.access_token.reveal() or record.item_id != item.item_id:
            raise SinkWriteFailed(
                f"the credential at {self._directory} read back different from what was written"
            )
        return SinkReceipt(
            kind=self.kind,
            destination=self.destination,
            durable=("access_token", "item_id"),
            owed=("link_session_id", "request_id"),
            pairing=Pairing.ON_FLOW_ROW,
        )


class EmergencyArtifactSink:
    """Criterion 1's second branch: one sealed file on this Mac, openable by escrow.

    This is the branch that exists because the emergency is *"the VPS is gone"* and a
    replacement host is not a thirty-minute step. §15 keeps runtime secrets off this
    laptop, and that is satisfied here by the encryption plus the escrowed key rather
    than by refusing to write: a plaintext ``access_token`` on this disk would break
    the rule, and a credential lost because nothing was written breaks the task.

    All four of criterion 2's fields fit, because this format is ours, and the
    ``item_id`` ↔ ``flow_id`` pairing travels with them — *"an artifact that carries
    the credential without the pairing recreates the defect one step later"*.
    """

    kind = SinkKind.EMERGENCY_ARTIFACT

    def __init__(self, path: Path, *, key_file: Path) -> None:
        self._path = Path(path)
        self._key_file = Path(key_file)
        self._key: bytes | None = None

    @property
    def destination(self) -> str:
        return f"sealed emergency artifact at {self._path}"

    def prepare(self) -> None:
        if self._path.exists():
            # `O_EXCL` at commit would raise this *after* the exchange. The same
            # refusal, moved to the side of the irreversible step where it is free.
            raise SinkNotWritable(
                f"an artifact already exists at {self._path}; it will not be "
                "overwritten. Move it aside — it may hold an earlier recovery"
            )
        _prove_durable(self._path.parent, what="emergency artifact directory")

        try:
            key = crypto.load_backup_key(self._key_file)
        except crypto.BackupKeyError as exc:
            raise SinkNotWritable(
                f"the escrowed backup key is not usable, so an artifact sealed under "
                f"it would not be recoverable: {exc}"
            ) from None

        # The part that makes this a proof of a *destination* and not of a directory.
        # A file that seals and will not open is a durable loss, and the one moment it
        # is free to discover that is now.
        #
        # **It cannot currently fail, and that is measured rather than assumed.**
        # Mutation testing removed this round-trip and all 23 tests stayed green, so
        # the honest reading is that `load_backup_key` already excludes everything
        # that would make it fail — mode, encoding, one line, exactly 32 bytes — and
        # ChaCha20-Poly1305 round-trips for every 32-byte key. It stays because it
        # asserts a contract that spans two modules rather than a branch inside this
        # one: it is `crypto`'s guarantee, checked on the cheap side of the exchange,
        # so a future change there surfaces here instead of after the token is spent.
        # What it must not be mistaken for is a tested guard.
        try:
            reopened = crypto.open_sealed(crypto.seal(_PROBE_PLAINTEXT, key), key)
        except (crypto.AuthenticationError, ValueError) as exc:
            raise SinkNotWritable(
                f"the escrowed backup key did not round-trip a probe through 03a's "
                f"sealing: {exc}. Nothing was exchanged"
            ) from None
        if reopened != _PROBE_PLAINTEXT:
            raise SinkNotWritable(
                "the escrowed backup key sealed a probe that reopened as different "
                "bytes; refusing to seal a credential under it"
            )

        # Held rather than re-read at commit. Every failure above is one `03a`'s
        # loader can raise — mode, encoding, length — and re-reading would move them
        # to the far side of the exchange, which is the mistake criterion 1 names.
        self._key = key

    def commit(self, item: RecoveredItem, *, now: datetime) -> SinkReceipt:
        key = self._key
        if key is None:
            raise SinkWriteFailed(
                "commit() was called before prepare(); the backup key was never proven"
            )
        plaintext = json.dumps(
            {
                "schema": ARTIFACT_SCHEMA,
                "flow_id": item.flow_id,
                "item_id": item.item_id,
                "access_token": item.access_token.reveal(),
                "link_session_id": item.link_session_id,
                "request_id": item.request_id,
                "recovered_at": now.isoformat(),
            },
            sort_keys=True,
        ).encode("utf-8")
        envelope = crypto.seal(plaintext, key)

        try:
            fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except OSError as exc:
            raise SinkWriteFailed(
                f"the exchange succeeded and the artifact at {self._path} could not be "
                f"created: {exc.strerror}"
            ) from None
        try:
            try:
                _write_exactly(fd, envelope)
                os.fsync(fd)
            finally:
                os.close(fd)
            _fsync_directory(self._path.parent)
        except OSError as exc:
            raise SinkWriteFailed(
                f"the exchange succeeded and the artifact at {self._path} was not made "
                f"durable: {exc.strerror}"
            ) from None

        # Read back **through the key**, not as bytes. Comparing ciphertext would prove
        # the bytes landed; openability is the property the emergency actually needs,
        # and it is the one a byte comparison cannot see. (Re-sealing to compare would
        # not work either: `seal` draws a fresh random nonce, so two seals of identical
        # plaintext differ — the construction forces the honest check.)
        try:
            reopened = read_artifact(self._path, key_bytes=key)
        except (SinkError, OSError) as exc:
            raise SinkWriteFailed(
                f"the artifact at {self._path} was written and did not read back openable: {exc}"
            ) from None
        if reopened != json.loads(plaintext):
            raise SinkWriteFailed(
                f"the artifact at {self._path} reopened as different content from what was sealed"
            )
        return SinkReceipt(
            kind=self.kind,
            destination=self.destination,
            durable=REQUIRED_FIELDS,
            owed=(),
            pairing=Pairing.IN_ARTIFACT,
        )


def read_artifact(path: Path, *, key_bytes: bytes) -> dict[str, object]:
    """Open one sealed artifact and return its fields, validated as this schema.

    Separate from :func:`restore` so the read-back inside
    :meth:`EmergencyArtifactSink.commit` uses the same reader the restore will — a
    read-back through a second implementation proves the second implementation.
    """
    try:
        envelope = path.read_bytes()
    except OSError as exc:
        raise ArtifactUnreadable(f"cannot read the artifact at {path}: {exc.strerror}") from None
    try:
        plaintext = crypto.open_sealed(envelope, key_bytes)
    except crypto.AuthenticationError as exc:
        raise ArtifactUnreadable(
            f"the artifact at {path} did not authenticate under the escrowed backup key: {exc}"
        ) from None
    try:
        payload = json.loads(plaintext)
    except ValueError:
        raise ArtifactUnreadable(f"the artifact at {path} opened but is not JSON") from None
    if not isinstance(payload, dict):
        raise ArtifactUnreadable(f"the artifact at {path} opened but is not an object")
    # The envelope is `03a`'s, shared with backup archives, so the schema check is what
    # distinguishes the two. Refused on the name rather than on a missing field later.
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactUnreadable(
            f"the file at {path} opened under the backup key but is not a "
            f"{ARTIFACT_SCHEMA} artifact"
        )
    missing = [field for field in ("flow_id", "item_id", "access_token") if not payload.get(field)]
    if missing:
        raise ArtifactUnreadable(
            f"the artifact at {path} is missing {', '.join(missing)}; a partial "
            "artifact is not a recovery"
        )
    for optional in ("link_session_id", "request_id"):
        if optional not in payload:
            # Present-and-``None`` is a measurement; absent is an older or truncated
            # writer, and the two must not be read as the same thing (issue #14: the
            # support id is the fallback when a credential is lost).
            raise ArtifactUnreadable(
                f"the artifact at {path} does not record {optional}, even as null"
            )
    return payload


def restore(path: Path, *, key_file: Path, token_store_directory: Path) -> SinkReceipt:
    """The restore path criterion 1 requires of the artifact branch.

    Opens the artifact under the escrowed key and puts the credential into a real
    :class:`~networth.tokenstore.TokenStore` — the same call the replacement-host
    branch makes directly, so the two branches converge on one place a credential can
    be read from rather than two.

    The ``item_id`` travels with the credential, so the pairing criterion 3 requires
    arrives here rather than having to be reconstructed; applying it to a ``link_flow``
    row remains that criterion's work.
    """
    key = crypto.load_backup_key(key_file)
    payload = read_artifact(path, key_bytes=key)
    item = RecoveredItem(
        access_token=Secret(str(payload["access_token"])),
        item_id=str(payload["item_id"]),
        flow_id=str(payload["flow_id"]),
        link_session_id=None
        if payload["link_session_id"] is None
        else str(payload["link_session_id"]),
        request_id=None if payload["request_id"] is None else str(payload["request_id"]),
    )
    sink = ReplacementHostSink(token_store_directory)
    sink.prepare_for(item.flow_id)
    receipt = sink.commit(item, now=datetime.now().astimezone())
    return SinkReceipt(
        kind=receipt.kind,
        destination=receipt.destination,
        # What the restore established is the union of what the artifact carried and
        # what the store accepted: the two optional fields were durable in the
        # artifact before this ran, and remain readable there.
        durable=receipt.durable,
        owed=receipt.owed,
        # The pairing came *out of the artifact*, which is the destination criterion 3
        # names for this branch — not the flow row the replacement-host sink reports.
        pairing=Pairing.IN_ARTIFACT,
    )
