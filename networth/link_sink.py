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
every failure the proof can reach has been excluded rather than hoped about.

*(That sentence read "every failure it could still hit" until PR #129, and the review
found two it could: the lock ``TokenStore.put`` takes in the credential directory's
**parent**, and a dangling symlink at the artifact path that ``exists()`` reports as
absent. Both are proven now — see :func:`_prove_lockable` and
:meth:`EmergencyArtifactSink.prepare` — and the claim is narrowed anyway, because the
honest version is bounded by what a proof can observe. The world can still change
between the two calls; what that costs is bounded instead by* :meth:`commit` *raising
this module's own error type for a filesystem refusal rather than letting an*
``OSError`` *past every* ``except SinkError`` *in the callers.)*

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

**The fence rides with the credential, and is not optional.** Criterion 6 requires the
owner's typed confirmation that the old worker is off to be *"recorded with the
recovery artifact"*, so :class:`RecoveredItem` carries a
:class:`~networth.link_fence.FenceAttestation` as a required field, the artifact seals
it beside the token, and :func:`read_artifact` refuses a credential that arrives
without one. Asking for it is :mod:`networth.link_fence`'s job; this module's is
making it impossible to store a recovered credential that nobody fenced.

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
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.link_fence import FenceAttestation, FenceRecordInvalid
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
#: Bumped from ``.1`` when criterion 6's fence confirmation became a required field.
#: The bump costs nothing: criterion 5's rehearsal has not been run, so no artifact of
#: either version exists anywhere. It is still a bump rather than an optional field,
#: because a reader that accepts an artifact with no attestation cannot tell a recovery
#: that was fenced from one that skipped the only step protecting it.
ARTIFACT_SCHEMA: Final = "networth.link-recovery-artifact.2"

#: The four fields `07b` criterion 2 requires be durable before recovery is reported
#: successful: *"``access_token`` **and** ``item_id`` are written, ``fsync``ed, **read
#: back**, and only then is recovery reported successful. Also persist
#: ``link_session_id`` and the exchange ``request_id`` (issue #14) — in this scenario
#: the support ticket is the fallback."*
REQUIRED_FIELDS: Final = ("access_token", "item_id", "link_session_id", "request_id")

#: Criterion 6's attestation, named as a receipt field because it is one: it is
#: *recorded with the recovery artifact*, so on that branch it lands and on the
#: replacement-host branch it is owed. Separate from :data:`REQUIRED_FIELDS`, which is
#: criterion 2's list and should not silently grow a criterion 6 member.
FENCE_FIELD: Final = "fence_attestation"

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


class ArtifactReadFailure(Enum):
    """*Why* an artifact did not yield its fields — the part a caller may branch on.

    :func:`read_artifact` has one failure type and three very different facts behind
    it, and PR #136's review found :func:`networth.link_crash.classify` reading all
    three as proven credential loss. Only :attr:`NOT_AN_ARTIFACT` proves anything
    about the credential; the other two are statements about *this attempt*.

    This exists so the cause travels as a value. Deciding it by matching the
    exception's prose would make every message in :func:`read_artifact` load-bearing
    for a different module's control flow, and the review that asked for this said
    so explicitly: *"do not infer the cause by parsing exception prose."*
    """

    #: The bytes could not be read at all — ``EACCES``, ``EIO``, a vanished mount.
    #: Says **nothing** about the content: the same file read again after the access
    #: problem is fixed may hand over the credential, which is measured rather than
    #: argued (see ``tests/test_link_crash.py``, the denial/restore pair).
    ACCESS_FAILED = "the artifact's bytes could not be read"

    #: The bytes were read and did not authenticate under the key we were given.
    #: **Three causes are not separated here, and cannot be**: a well-formed but
    #: wrong 32-byte key, a torn write, and a file that is not one of `03a`'s
    #: envelopes at all all fail the same tag comparison. A wrong key is the
    #: owner's mistake and is recoverable; a torn write is not; nothing on this
    #: disk tells them apart, so the honest answer names the ambiguity instead of
    #: resolving it.
    UNAUTHENTICATED = "the artifact did not authenticate under this key"

    #: The envelope opened **and authenticated** under this key, so the plaintext
    #: is exactly what was sealed by someone holding it — and it is not a usable
    #: artifact: another schema (a `03a` backup archive shares this envelope), a
    #: missing required field, or no fence. This one *is* proven, and it is the
    #: only cause for which :attr:`~networth.link_crash.CrashState.CREDENTIAL_LOST`
    #: is an honest verdict.
    NOT_AN_ARTIFACT = "the artifact opened under this key and is not a usable artifact"


class ArtifactUnreadable(SinkError):
    """A sealed artifact is not this schema, or will not open under this key.

    ``cause`` is required and has no default, for the reason every other required
    field in this module has none: a default would be a guess at the one thing the
    caller is about to act on, and the wrong guess here is the F1 defect itself.
    """

    def __init__(self, message: str, *, cause: ArtifactReadFailure) -> None:
        super().__init__(message)
        self.cause = cause


def read_failure_cause(exc: BaseException) -> ArtifactReadFailure:
    """What a failed :func:`read_artifact` proves, for any exception it can raise.

    Two callers act on this — :meth:`EmergencyArtifactSink._describe_existing` and
    :func:`networth.link_crash.classify` — and F1 was *both of them agreeing on the
    same wrong verdict*. One function so they cannot drift apart, rather than the
    same three-way branch written twice.

    A bare :class:`OSError` maps to :attr:`ArtifactReadFailure.ACCESS_FAILED`.
    :func:`read_artifact` already wraps its own read failures that way, so this is
    the belt for a reader that someday does not — and it fails toward *unknown*,
    never toward loss, which is the direction the whole finding is about.
    """
    if isinstance(exc, ArtifactUnreadable):
        return exc.cause
    return ArtifactReadFailure.ACCESS_FAILED


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

    **``fence`` is required and has no default**, which is criterion 6 expressed in the
    type rather than in a review comment: *"the script takes an explicit typed
    confirmation naming that instance, recorded with the recovery artifact"*. A
    recovered credential that reaches this module without one is a recovery that was
    never fenced, and the moment to find that out is before anything is written — not
    by noticing an absent field in an artifact a year later. The cost of the choice is
    that every caller must have asked; that is the point of it.
    """

    access_token: Secret
    item_id: str
    flow_id: str
    link_session_id: str | None
    request_id: str | None
    fence: FenceAttestation

    def __repr__(self) -> str:
        # Presence, never values. ``item_id`` is redacted for a different reason than
        # the token: it is not a secret, it names one of the owner's institutions
        # (`AGENTS.md` rule 0), and this object is what a traceback renders. The fence
        # renders as its instance because that is infra this repo already names, and a
        # traceback saying *which* host was fenced is worth more than one saying a
        # fence existed.
        return (
            f"RecoveredItem(flow_id={self.flow_id!r}, item_id=<redacted>, "
            f"access_token=<Secret: redacted>, "
            f"link_session_id={'<redacted>' if self.link_session_id else 'None'}, "
            f"request_id={self.request_id!r}, fence={self.fence.instance!r})"
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

        Called **before** the exchange. It must leave no recovery material behind on
        success — but it may leave a prerequisite the commit would have created
        anyway, and one kind does: proving a ``TokenStore`` means *taking its lock*,
        and the lock file is the same file :meth:`~networth.tokenstore.TokenStore.put`
        creates. This read *"must leave nothing behind"* until PR #129, which is how
        the proof came to probe the credential directory and skip the lock in its
        parent — a promise narrow enough to exclude the real prerequisite excluded the
        proof of it too.
        """

    def prepare_for(self, flow_id: str) -> None:
        """:meth:`prepare`, plus whatever refusal knowing the flow makes possible.

        Both kinds implement it so a caller that *does* know the flow — the
        recovery command does — has one call to make rather than a branch on
        which sink it built. A branch there would be the caller deciding which
        refusals apply, which is the decision this module exists to own.
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


def _prove_lockable(lock_path: Path, *, what: str) -> None:
    """Take and release the lock the commit will need, in the directory it lives in.

    PR #129 finding 2. :meth:`~networth.tokenstore.TokenStore.put` takes
    ``.tokenstore.lock`` in the credential directory's **parent** — deliberately, so
    the lock can never be mistaken for archive payload (``DESIGN.md`` §14a) — and
    :func:`_prove_durable` probes the credential directory and nothing above it. A
    writable store beneath an unwritable parent therefore passed a proof whose whole
    claim is that the write after the exchange cannot fail, and then failed at
    ``os.open`` on the lock with :class:`PermissionError`: an ``OSError``, so it did
    not even arrive as a :class:`SinkError`.

    The proof is the real acquisition rather than a probe beside it, because what has
    to hold is not "this directory accepts files" — that was already checked, on the
    wrong directory — but "this exact path can be opened ``O_NOFOLLOW``, chmodded and
    flocked", which is three syscalls a probe file does not make.

    Non-blocking, so a preflight cannot hang before the emergency's clock. A lock
    someone else holds is still a refusal and not a wait: the alternative is spending
    the one-time token and *then* queueing behind a backup capture.
    """
    try:
        with exclusive_file_lock(lock_path, blocking=False, reentrant=False):
            pass
    except LockUnavailable:
        raise SinkNotWritable(
            f"{what}: another operation holds {lock_path}, so the write after the "
            "exchange would queue behind it. Nothing has been exchanged"
        ) from None
    except OSError as exc:
        raise SinkNotWritable(
            f"{what}: the lock the write needs, {lock_path}, cannot be taken: "
            f"{exc.strerror}. Nothing has been exchanged and no Item slot is at risk"
        ) from None


class ReplacementHostSink:
    """Criterion 1's first branch: a replacement host with a ready ``TokenStore``.

    The credential lands where every other credential in this system lands, which is
    the whole appeal of this branch — there is no second format to restore from and no
    second thing to get right. What it cannot hold is the rest of criterion 2:
    :class:`~networth.tokenstore.SecretRecord` carries ``item_id`` and nothing else
    Plaid returned, so ``link_session_id`` and ``request_id`` belong to the
    ``link_flow`` row on that host and are reported as :attr:`SinkReceipt.owed` rather
    than quietly dropped.

    Criterion 6's fence confirmation is owed for the same reason and is listed with
    them. It is *"recorded with the recovery artifact"* and this branch has no
    artifact — so here the attestation belongs to the ``link_flow`` row as well, and a
    receipt that omitted it would let a recovery whose only protection was the owner's
    power-off report itself complete with that fact stored nowhere.
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
        _prove_lockable(store.lock_path, what="replacement-host TokenStore")
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
        # `OSError` alongside `TokenStoreError`, because the store raises its own type
        # for what it decided and the filesystem's for what it could not do — the lock
        # `put` opens is an `os.open`, and a `PermissionError` from it used to leave
        # this method entirely, past every `except SinkError` the callers have. A
        # failure this expensive must arrive as this module's own type whatever it was
        # underneath. `LockUnavailable` is a `RuntimeError`, so it is named too.
        except (TokenStoreError, LockUnavailable, OSError) as exc:
            raise SinkWriteFailed(
                f"the exchange succeeded and the credential did not reach {self._directory}: {exc}"
            ) from None
        try:
            stored = store.get(reference).reveal()
            record = store.record(reference)
        except (TokenStoreError, LockUnavailable, OSError) as exc:
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
            # `item.fence` is deliberately not written anywhere by this branch rather
            # than dropped quietly: a `SecretRecord` has one metadata field and it is
            # `item_id`. Inventing a place for the attestation inside the token store
            # would put a second format next to the one `05a` owns, in the middle of an
            # emergency, to hold a fact criterion 3's flow row is already the home for.
            owed=("link_session_id", "request_id", FENCE_FIELD),
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
        # PR #129 finding 2, second half. This asked `self._path.exists()`, which
        # *follows* the link: a dangling symlink at the artifact path answered `False`,
        # so the proof passed and commit's `O_EXCL` then failed `EEXIST` — after the
        # exchange, which is the one place this refusal must never be.
        #
        # `os.lstat` rather than `os.path.lexists`, and the difference is the whole
        # point: `lexists` returns `False` for *every* `OSError`, so an unreadable
        # parent component would read as "nothing is there" and re-create exactly the
        # bug one level up. Three outcomes, each answered on its own terms — an entry
        # exists, nothing is there, or we cannot tell — and the third refuses, because
        # a preflight that cannot see is a preflight that has not proven anything.
        try:
            os.lstat(self._path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise SinkNotWritable(
                f"cannot tell whether an artifact already exists at {self._path}: "
                f"{exc.strerror}. Refusing rather than sealing over an unknown, and "
                "nothing has been exchanged"
            ) from None
        else:
            # `O_EXCL` at commit would raise this *after* the exchange. The same
            # refusal, moved to the side of the irreversible step where it is free.
            # Any directory entry counts, not just a file that opens: a symlink whose
            # target is absent is still a name `O_EXCL` will refuse to create.
            #
            # **Which refusal, though, is `07b` criterion 4** (see `link_crash`). This
            # used to be a single message ending *"Move it aside — it may hold an
            # earlier recovery"*, and that advice is followed by a re-run. On an
            # artifact that opens, the re-run exchanges a second time — measured as
            # **ACCEPTED** with the first credential still healthy (`06a` (ii)), with
            # the Item count unmeasured — after moving aside the only copy of the
            # credential it was describing. So the one place that can tell these apart
            # for free now does.
            raise SinkNotWritable(self._describe_existing())
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

    def _describe_existing(self) -> str:
        """Why the path is occupied, in the terms the owner has to act on.

        *"This file holds your credential"*, *"this file will never give it to
        you"* and *"we could not look"* imply different next actions, and the
        third must not be rounded to either. It reads the artifact with this
        module's own :func:`read_artifact` rather than through
        :mod:`networth.link_crash`, which imports this one; the record half of
        that classifier is not needed to answer a question about this path.

        **The "will never give it to you" branch used to swallow two more
        answers** — PR #136 review finding F1. A wrong escrowed key and a
        transient ``EACCES`` both arrive as :class:`ArtifactUnreadable`, and both
        were answered *"holds no usable credential … treat the lifetime slot as
        spent"*, which is the most expensive sentence in this module to get wrong:
        the owner is told the only surviving credential is gone while the file is
        still sitting there holding it. Branching on
        :class:`ArtifactReadFailure` is what separates the proof from the attempt.

        Loading the key here is a second load in the refusing case only, and that
        case does not go on to exchange anything, so it cannot move a failure to
        the far side of the irreversible step — the hazard the held key exists to
        avoid.
        """
        try:
            key = crypto.load_backup_key(self._key_file)
        except crypto.BackupKeyError as exc:
            return (
                f"an artifact already exists at {self._path} and the escrowed backup key "
                f"could not be loaded to see what it holds ({exc}), so whether your "
                "credential is in it is unknown. Do not exchange again until you have "
                "opened it: a second exchange is accepted on the wire and may cost a "
                "lifetime Item"
            )
        try:
            read_artifact(self._path, key_bytes=key)
        except (ArtifactUnreadable, OSError) as exc:
            cause = read_failure_cause(exc)
            if cause is ArtifactReadFailure.ACCESS_FAILED:
                return (
                    f"an artifact already exists at {self._path} and could not be read to "
                    f"see what it holds ({exc}), so whether your credential is in it is "
                    "unknown. Fix the access problem and look before concluding anything: "
                    "do not exchange again and do not move or delete this file"
                )
            if cause is ArtifactReadFailure.UNAUTHENTICATED:
                return (
                    f"an artifact already exists at {self._path} and did not authenticate "
                    f"under {self._key_file} ({exc}), so whether your credential is in it "
                    "is unknown. A wrong backup key and a torn write fail identically "
                    "here. Do not exchange again and do not move or delete this file; "
                    "check you are using the escrowed 03a key before concluding anything. "
                    "Only if the key is certainly the right one is the credential gone — "
                    "and then the lifetime slot is spent, so quote the request_id from the "
                    "crashed run's transcript rather than exchanging again blind"
                )
            return (
                f"an artifact already exists at {self._path} and holds no usable "
                f"credential ({exc}). It opened under the escrowed key, so that is "
                "settled rather than suspected. It will not be overwritten. The exchange "
                "it was writing may already have happened — so treat the lifetime slot "
                "as spent and quote the request_id from that run's transcript rather "
                "than exchanging again blind"
            )
        return (
            f"an artifact already exists at {self._path} and it opens under the "
            "escrowed backup key with the credential and the fence intact. DO NOT "
            "EXCHANGE again and do not move this file: your recovery already "
            "succeeded and this is the only copy of what it recovered. Restore it "
            "onto the replacement host instead"
        )

    def prepare_for(self, flow_id: str) -> None:
        """:meth:`prepare`. This kind's flow-specific refusal is already in it.

        The artifact's path names one recovery, so *"a file is already here"* is
        the same refusal the replacement host expresses as *"this flow already has
        material"* — it is in :meth:`prepare` because it is a fact about the path
        rather than about the id. Accepting the argument and not needing it keeps
        the caller from having to know that.
        """
        del flow_id
        self.prepare()

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
                # Criterion 6: the typed confirmation is *"recorded with the recovery
                # artifact"*, and this is that record. Inside the sealed plaintext
                # rather than beside the file, so it cannot be separated from the
                # credential it fenced — an attestation in a neighbouring log is
                # evidence about a run, not about this token.
                "fence": item.fence.to_record(),
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
            durable=(*REQUIRED_FIELDS, FENCE_FIELD),
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
        raise ArtifactUnreadable(
            f"cannot read the artifact at {path}: {exc.strerror}",
            cause=ArtifactReadFailure.ACCESS_FAILED,
        ) from None
    try:
        plaintext = crypto.open_sealed(envelope, key_bytes)
    except crypto.AuthenticationError as exc:
        raise ArtifactUnreadable(
            f"the artifact at {path} did not authenticate under the escrowed backup key: {exc}",
            cause=ArtifactReadFailure.UNAUTHENTICATED,
        ) from None
    try:
        payload = json.loads(plaintext)
    except ValueError:
        raise ArtifactUnreadable(
            f"the artifact at {path} opened but is not JSON",
            cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
        ) from None
    if not isinstance(payload, dict):
        raise ArtifactUnreadable(
            f"the artifact at {path} opened but is not an object",
            cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
        )
    # The envelope is `03a`'s, shared with backup archives, so the schema check is what
    # distinguishes the two. Refused on the name rather than on a missing field later.
    if payload.get("schema") != ARTIFACT_SCHEMA:
        raise ArtifactUnreadable(
            f"the file at {path} opened under the backup key but is not a "
            f"{ARTIFACT_SCHEMA} artifact",
            cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
        )
    missing = [field for field in ("flow_id", "item_id", "access_token") if not payload.get(field)]
    if missing:
        raise ArtifactUnreadable(
            f"the artifact at {path} is missing {', '.join(missing)}; a partial "
            "artifact is not a recovery",
            cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
        )
    for optional in ("link_session_id", "request_id"):
        if optional not in payload:
            # Present-and-``None`` is a measurement; absent is an older or truncated
            # writer, and the two must not be read as the same thing (issue #14: the
            # support id is the fallback when a credential is lost).
            raise ArtifactUnreadable(
                f"the artifact at {path} does not record {optional}, even as null",
                cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
            )
    # Criterion 6, on the reading side. Validated here rather than only where it is
    # used, because every reader of an artifact — the commit's own read-back included —
    # has to agree on what a complete one is. A credential that opens without an
    # attestation is the case *"an artifact that carries the credential without the
    # pairing recreates the defect one step later"* is about, one criterion over: the
    # restore would put a token into a real store with nothing saying the host that
    # could still exchange it was ever turned off.
    try:
        FenceAttestation.from_record(payload.get("fence"))
    except FenceRecordInvalid as exc:
        raise ArtifactUnreadable(
            f"the artifact at {path} is not fenced: {exc}",
            cause=ArtifactReadFailure.NOT_AN_ARTIFACT,
        ) from None
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

    The fence attestation travels the same way and is read back into the item rather
    than re-asked for. Re-prompting on the replacement host would be a *second*
    attestation about a moment that has passed — the exchange it fenced already
    happened — and the one this artifact carries is the one that was true then.
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
        fence=FenceAttestation.from_record(payload["fence"]),
    )
    sink = ReplacementHostSink(token_store_directory)
    sink.prepare_for(item.flow_id)
    receipt = sink.commit(item, now=datetime.now().astimezone())
    return SinkReceipt(
        kind=receipt.kind,
        destination=receipt.destination,
        # What the restore established is the union of what the artifact carried and
        # what the store accepted: the optional fields and the fence attestation were
        # durable in the artifact before this ran, and remain readable there.
        #
        # **The union is now computed rather than described.** This passed the
        # replacement-host sink's own tuples straight through, which contradicted the
        # sentence above it — that sink reports as `owed` exactly what a `SecretRecord`
        # cannot hold, and every one of those things is in the file this function just
        # opened. Harmless while the two were optional support ids; not harmless once
        # criterion 6's attestation joined them, because it would have reported the
        # fence as missing while reading it out of the artifact in the same call.
        durable=(*REQUIRED_FIELDS, FENCE_FIELD),
        owed=(),
        # The pairing came *out of the artifact*, which is the destination criterion 3
        # names for this branch — not the flow row the replacement-host sink reports.
        pairing=Pairing.IN_ARTIFACT,
    )
