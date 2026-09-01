"""Narrow interface over secret storage — the only code that touches token material.

Task `05a`. Normative: `DESIGN.md` §2 reservation 3, §14a, §15; issues #11 and #15.

Everything here exists because of one asymmetry (§14a). A Plaid `access_token`
cannot be regenerated: there is no recovery API, and re-linking creates a **new
Item**, spending one of ten *lifetime* slots. So of the two ways a crash can
desynchronise this store from the database, only one is survivable:

- material with no ``item`` row is an **orphan** — harmless, and recoverable by
  re-reading the Item from Plaid;
- an ``item`` row whose material is missing is **unrecoverable** and strands a
  slot forever.

Every ordering rule below picks the first failure over the second. That is the
whole design; the rest is bookkeeping.

Three properties this module is responsible for:

**The database stores a name, never a value** (§15). Rows carry a ``secret_ref``;
resolving one to material happens here and nowhere else, which is what lets the
storage location move without touching sync logic (§2 reservation 3) — it already
moved once, between rev 9 and rev 10.

**A pending token is attributable to the flow that earned it** (issue #15). A
worker can die between ``fsync`` and ``COMMIT``, leaving a perfectly good
credential on disk with nothing in the database pointing at it. The
``secret_ref`` therefore *encodes* the ``flow_id``, and the record carries the
``item_id`` Plaid returned with it, so :meth:`TokenStore.reconcile` can find it
after a restart. Without that, recovery cannot tell a recoverable crash from a
lost credential, and would report a live token to Plaid support as gone.

**The directory is passed in and never discovered.** There is no default, no
search path and no fallback, because the two credential directories in this
project belong to two different machines and must never be searched for one
another (§15 rule 3): ``/etc/networth/`` is the sync host's and
``~/agents/secrets/`` is ``zelengs-macbook-air-2``'s. A constructor that cannot
guess is a rule that cannot be violated by a path bug.

**A ref holds exactly two names, and both are predictable.** ``{ref}.json`` is
the published record; ``.{ref}.pending`` is the one it is built under. Neither is
random, and that is the point: a random temporary name is invisible to recovery
(the material is durable and :meth:`TokenStore.reconcile` cannot find it) and
invisible to deletion (a leftover link keeps the credential alive after the
database reference is gone — issue #11's immortal orphan, arrived at from the
other side). Both names are therefore reconciled and both are deleted.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

#: On-disk record format. Bumped when the record's shape changes, so a reader
#: that predates the change refuses it rather than misreading a field.
RECORD_SCHEMA = 1

DIRECTORY_MODE = 0o700
FILE_MODE = 0o600


class SecretKind(Enum):
    """What a piece of material is. Part of the ``secret_ref``, so it is stable."""

    ACCESS_TOKEN = "access-token"
    LINK_TOKEN = "link-token"


class TokenStoreError(Exception):
    """Base class. No subclass ever carries material in its message."""


class InvalidSecretRef(TokenStoreError):
    """A ``secret_ref`` or ``flow_id`` this store could not have produced."""


class UnknownSecretRef(TokenStoreError):
    """No material is stored under this ``secret_ref``."""


class SecretRefExists(TokenStoreError):
    """Material already exists for this flow and kind, and was not overwritten."""


class CorruptRecord(TokenStoreError):
    """A record on disk could not be read as this store's format."""


# A `flow_id` is 32 lowercase hex characters — a minted uuid4 and nothing else.
#
# Two jobs, and the second is why the grammar is this narrow. The first is
# confinement: no dot, no slash, no separator of any kind, so a `flow_id` cannot
# spell a relative path and a `secret_ref` cannot address a file outside the
# store. Validation happens before any name becomes a path, so the confinement is
# in the grammar rather than in a check after the fact.
#
# The second is that a `secret_ref` is the value the *database* stores (§15), and
# under the previous, looser grammar (letters, digits, hyphens, under 64) a Plaid
# `access_token` was a well-formed `flow_id`. Passing material where a flow id
# belonged would have written the credential into a filename and handed it back
# as the `secret_ref` a caller persists — §15 violated by the one module that
# exists to enforce it. A shape no credential can have makes that unrepresentable
# rather than merely discouraged, which is why this is a generated format and not
# a list of prefixes to refuse.
_FLOW_ID_RE = re.compile(r"\A[0-9a-f]{32}\Z")
_SECRET_REF_RE = re.compile(
    r"\A(?P<kind>"
    + "|".join(re.escape(k.value) for k in SecretKind)
    + r")\.(?P<flow_id>[0-9a-f]{32})\Z"
)

_SUFFIX = ".json"
_PENDING_SUFFIX = ".pending"


class Secret:
    """Material that does not render itself.

    ``repr``, ``str`` and ``format`` all redact, so the value cannot reach a log
    line, a traceback frame or an f-string by accident. Reading it is a call you
    have to write on purpose: :meth:`reveal`.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        """Return the material. The only way out, and it is greppable."""
        return self._value

    def __repr__(self) -> str:
        return "<Secret: redacted>"

    def __str__(self) -> str:
        return "<Secret: redacted>"

    def __format__(self, spec: str) -> str:
        # Without this, `f"{secret:>20}"` would fall through to object.__format__,
        # which raises for a non-empty spec — a crash whose message is fine, but
        # the empty-spec case would have used __str__ anyway. Overriding both
        # keeps every formatting path identical and boring.
        return "<Secret: redacted>"


@dataclass(frozen=True, repr=False)
class SecretRecord:
    """Everything about a stored secret **except** the secret.

    Returned by the metadata reads so that "does material exist for this flow?"
    never has to move a credential to answer.

    ``item_id`` is not secret — §7 keeps it in a plain ``link_flow`` column — and
    it is still withheld from ``repr``, for a different reason: it is the only
    field here whose contents this store cannot constrain. ``secret_ref``,
    ``kind`` and ``flow_id`` are each checked against the name the file was found
    under, so they can only ever be ``kind.<32 hex>``; ``created_at`` has to parse
    as an aware timestamp. ``item_id`` is any non-empty string Plaid handed back,
    which means it is the one place a bad or hostile write can park a credential
    and have a reader render it. It used to: a record with ``item_id`` set to
    token material passed validation, and this dataclass's generated ``repr``
    printed it into whatever log line or traceback held the record. Reading it is
    a call you write on purpose — ``record.item_id`` — exactly like
    :meth:`Secret.reveal`.
    """

    secret_ref: str
    kind: SecretKind
    flow_id: str
    item_id: str | None
    created_at: datetime

    def __repr__(self) -> str:
        # Presence, never the value: "did this flow's material arrive with an
        # Item id?" is the question recovery actually asks of a rendered record
        # (issue #15), and it is answerable without printing one.
        item_id = "None" if self.item_id is None else "<redacted>"
        return (
            f"SecretRecord(secret_ref={self.secret_ref!r}, kind={self.kind!r}, "
            f"flow_id={self.flow_id!r}, item_id={item_id}, "
            f"created_at={self.created_at!r})"
        )


def new_flow_id() -> str:
    """Mint a ``flow_id``.

    §7 has the ``flow_id`` minted before the ``link_token`` and carried by
    ``link_flow.flow_id``; this is the minter, and it lives here because this
    module is the one that has to be able to *refuse* anything it did not mint.
    """
    return uuid.uuid4().hex


def secret_ref_for(kind: SecretKind, flow_id: str) -> str:
    """The ``secret_ref`` naming scheme (issue #15).

    It encodes the ``flow_id`` precisely so that material written before its
    database row exists is still attributable after a restart.
    """
    if not _FLOW_ID_RE.match(flow_id):
        # The rejected value is never repeated. A caller that got here by passing
        # a credential where a flow id belonged would otherwise have the module
        # that exists to contain material be the one that prints it — into a
        # traceback, a log line, or an alert (§15).
        raise InvalidSecretRef("not a usable flow_id; expected a minted flow id")
    return f"{kind.value}.{flow_id}"


def parse_secret_ref(secret_ref: str) -> tuple[SecretKind, str]:
    """Split a ``secret_ref`` back into its kind and ``flow_id``."""
    match = _SECRET_REF_RE.match(secret_ref)
    if match is None:
        raise InvalidSecretRef("not a well-formed secret_ref")
    return SecretKind(match.group("kind")), match.group("flow_id")


class TokenStore:
    """A mode-0600 file per secret, in a mode-0700 directory.

    Deliberately absent: any read that returns more than one secret. There is no
    ``all()``, no iteration and no mapping view, because the callers that would
    use one — the sync loop, a report, a debug dump — have no business holding
    every credential at once, and an interface that offers it will eventually be
    used that way.
    """

    def __init__(self, directory: Path) -> None:
        self._dir = Path(directory)
        self._ensure_directory()

    def __repr__(self) -> str:
        return f"TokenStore({str(self._dir)!r})"

    @property
    def directory(self) -> Path:
        return self._dir

    # --- writing ---------------------------------------------------------------

    def put(
        self,
        kind: SecretKind,
        flow_id: str,
        material: str,
        *,
        item_id: str | None = None,
    ) -> str:
        """Write material durably and return its ``secret_ref``.

        Durable when this returns: the record is ``fsync``ed, published under its
        final name, and the directory is ``fsync``ed so that name survives too.
        The caller commits the ``item`` row **after** this call (§14a ordering) —
        that way a crash in between leaves an orphan, never a stranded slot.

        Refuses to overwrite, and refuses *atomically*. An exchange writes once,
        and a worker that has crashed mid-flow is required to :meth:`reconcile`
        before it does anything else (issue #15); blind re-writing is how a
        recoverable crash turns into a second Plaid call and a second Item. If you
        meant to replace material, delete it first and say so.

        The publish step is therefore :func:`os.link`, not :func:`os.replace`.
        ``replace`` overwrites silently, so guarding it with an existence check
        leaves a window in which two workers both find nothing and the second
        destroys the first's credential — the `item` row then references material
        that was replaced by a different Item's, which is the unrecoverable
        direction. ``link`` fails with ``EEXIST`` in the kernel, so the loser of
        that race is told rather than ignored. §7 already claims the exchange in
        the database and expects at most one worker here; this is the same
        invariant held a second time, by the filesystem, because the cost of
        being wrong about it is a lifetime slot.

        The name it is built under is ``.{ref}.pending``, and it is **derived,
        not random**. A random temporary name opens two crash windows that this
        store's whole purpose is to close: die after the record's ``fsync`` and
        before the ``link``, and the credential is durable under a name
        :meth:`reconcile` does not know to look at — reported as a lost token,
        which re-spends a lifetime slot (issue #15). Die after the ``link`` and
        before the temporary name is removed, and two names hold the material
        while :meth:`delete` removes one — the invisible orphan issue #11 exists
        to prevent, reached from the other side. A derived name is reconciled and
        deleted like any other, so neither window survives.

        The pending name is released on exactly two outcomes, and both of them
        are "this ref provably has durable published material": this call
        published it, or the ``link`` was refused because another call already
        had. **Every other failure leaves the pending record alone**, because on
        those paths it is the only copy of a credential that cannot be re-fetched.
        A broad ``finally`` here read as tidiness and was the stranded slot this
        module exists to prevent: an ``EIO`` from ``link`` — after the record and
        its directory entry were both ``fsync``ed — deleted the one durable copy
        and left :meth:`reconcile` answering ``None``, which is the answer that
        sends the owner back to Plaid to spend a lifetime slot.

        The rule is deliberately not "unlink if the failure happened before the
        material was durable". That boundary is unknowable: data can reach the
        platter before ``fsync`` returns, so even a failing write may have left a
        readable credential behind. Where a failure *happened* is the wrong
        question; whether the ref has published material is the answerable one.

        Publication is made durable **before** the durable pending name is
        removed — ``link``, ``fsync`` the directory, then unlink, then ``fsync``
        again. Doing both in one barrier meant the only ordering between them was
        the filesystem's, and nothing in POSIX promises the ``link`` reaches the
        platter before the ``unlink`` does. A crash in that window could persist
        the removal without the publication and leave neither name holding the
        material. Two barriers cost one ``fsync`` and make every crash point land
        on at least one name.

        The cost is that a half-written pending record blocks the ref until it is
        explicitly deleted, rather than being silently replaced by the next
        attempt. That is the right way round: refusing is recoverable, and
        overwriting material this store cannot re-fetch is not.
        """
        if not material:
            # Writing empty material would let a flow reach EXCHANGED holding
            # nothing, which reads as success and strands the slot exactly as a
            # lost token would — with none of the evidence that it happened.
            raise ValueError("refusing to store empty material")
        ref = secret_ref_for(kind, flow_id)
        final = self._path(ref)
        pending = self._pending_path(ref)

        record = {
            "schema": RECORD_SCHEMA,
            "secret_ref": ref,
            "kind": kind.value,
            "flow_id": flow_id,
            "item_id": item_id,
            "created_at": datetime.now(UTC).isoformat(),
            # Named "material" rather than after any credential type: this file's
            # shape is the same for every kind, and the project's own term for
            # what a TokenStore holds is its material (§14a).
            "material": material,
        }

        try:
            fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, FILE_MODE)
        except FileExistsError:
            # Claiming the pending name is the first of the two kernel refusals.
            # Whatever is under it is either a live write by another worker or a
            # crashed one, and both are material this call must not touch.
            raise SecretRefExists(
                f"a write for {ref!r} is already in progress or was interrupted; "
                f"reconcile before writing (issue #15), or delete it explicitly"
            ) from None

        try:
            # O_CREAT's mode is a request that umask can clear; fchmod is the
            # guarantee, and the mode on disk is the acceptance criterion.
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
                json.dump(record, handle)
                handle.flush()
            os.fsync(fd)
        finally:
            os.close(fd)

        # The pending *entry* has to be durable, not just its contents: recovery
        # reads this name, and an unsynced directory can lose it. This is the
        # barrier that makes the first crash window recoverable.
        self._fsync_directory()

        try:
            os.link(pending, final)
        except FileExistsError:
            # The one failure that may release the pending name: `final` exists,
            # so this ref already has durable published material and nothing is
            # lost by dropping our own refused copy. Keeping it would wedge a ref
            # whose only escape — `delete` — would take the live credential with
            # it.
            self._release(pending)
            raise SecretRefExists(
                f"material already stored under {ref!r}; "
                f"reconcile before writing (issue #15), or delete it explicitly"
            ) from None

        # Publication first, and durably, before the name that currently holds
        # the only durable copy is taken away.
        self._fsync_directory()
        self._release(pending)
        return ref

    # --- reading ---------------------------------------------------------------

    def get(self, secret_ref: str) -> Secret:
        """Resolve one ``secret_ref`` to its material.

        No coercion: :meth:`_read` has already established that ``material`` is a
        non-empty string. ``str(...)`` here would have turned a corrupt record
        into a plausible credential — an empty string, a list's ``repr`` — and
        handed it to a caller about to mark a flow ``EXCHANGED``.
        """
        return Secret(cast(str, self._read(secret_ref)["material"]))

    def record(self, secret_ref: str) -> SecretRecord:
        """Metadata for one ``secret_ref``, without moving the material."""
        return self._to_record(self._read(secret_ref), secret_ref)

    def reconcile(self, flow_id: str) -> SecretRecord | None:
        """Is there already durable material for this flow? (issue #15)

        The question a worker must ask after a crash, **before** it classifies a
        stale exchange claim as uncertain or calls Plaid again. A ``None`` here
        means the token really is not on this disk; a record means the credential
        survived and the flow needs its local transaction finished, not another
        exchange.

        Answers from the two paths the naming scheme predicts — no directory
        listing, and nothing about any other flow is read or returned. The second
        of them is the pending name, which is the whole of issue #15's crash: a
        worker that died between the record's ``fsync`` and the ``link`` left a
        durable credential that only this lookup can find.
        """
        ref = secret_ref_for(SecretKind.ACCESS_TOKEN, flow_id)
        try:
            return self.record(ref)
        except UnknownSecretRef:
            return None

    # --- deleting --------------------------------------------------------------

    def delete(self, secret_ref: str) -> bool:
        """Remove material. Returns whether anything was there.

        Idempotent on purpose. The crash this store is built around leaves a
        dangling ``secret_ref`` in the database with its material already gone
        (issue #11), and the repair for that is to run the same deletion again —
        so "already absent" is a success, not an error. An operation that threw
        here would make the visible failure the harder one to fix.

        Both names go, not just the published one. A crash between :meth:`put`'s
        ``link`` and its cleanup leaves the pending name holding the same
        material, and removing only the record a caller knows about would leave a
        credential on disk that nothing refers to and no reaper will ever visit.

        The directory barrier runs **even when both names were already absent**,
        and that is not defensive tidiness. A previous call can unlink and die
        before its own ``fsync``: the entry is gone from this process's view and
        still recoverable on the platter, so returning ``False`` without the
        barrier lets :meth:`deleting` clear the database reference while a power
        loss can still bring the material back — with nothing left pointing at
        it. "Absent" has to be made durable before it may be acted on.
        """
        removed = False
        for path in (self._path(secret_ref), self._pending_path(secret_ref)):
            try:
                os.unlink(path)
            except FileNotFoundError:
                continue
            removed = True
        self._fsync_directory()
        return removed

    @contextmanager
    def deleting(self, secret_ref: str) -> Iterator[bool]:
        """Delete material, **then** let the caller clear the database ref.

        The order is issue #11's, and it is expressed as a context manager so it
        cannot be got wrong by reading the docs in the wrong order::

            with store.deleting(ref):
                db.clear_secret_ref(flow_id)   # runs only once material is gone

        A crash inside the body therefore leaves a **visible dangling ref** — a
        row pointing at material that is not there — rather than an invisible
        orphan, which is material no row mentions and nothing will ever reap. The
        first is a repair; the second is a credential that lives on disk forever.
        """
        yield self.delete(secret_ref)

    # --- internals -------------------------------------------------------------

    def _ensure_directory(self) -> None:
        self._dir.mkdir(mode=DIRECTORY_MODE, parents=True, exist_ok=True)
        # `mkdir` applies umask, and an existing directory keeps whatever mode it
        # already had. Neither is good enough for a directory of credentials.
        os.chmod(self._dir, DIRECTORY_MODE)

    # Every `secret_ref` reaching these two has been through the grammar, so it is
    # `kind.<32 hex>` and provably not material — which is what makes it safe to
    # name in an exception message when nothing else here is.
    def _path(self, secret_ref: str) -> Path:
        parse_secret_ref(secret_ref)  # rejects anything that could name another file
        return self._dir / f"{secret_ref}{_SUFFIX}"

    def _pending_path(self, secret_ref: str) -> Path:
        parse_secret_ref(secret_ref)
        return self._dir / f".{secret_ref}{_PENDING_SUFFIX}"

    def _release(self, pending: Path) -> None:
        """Drop a pending name and make its absence durable.

        Only ever called once this ref has published material, so the removal can
        never be the thing that loses a credential. `missing_ok` because a
        concurrent :meth:`delete` reaches the same two names.
        """
        pending.unlink(missing_ok=True)
        self._fsync_directory()

    def _fsync_directory(self) -> None:
        fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read_text(self, path: Path, secret_ref: str) -> str:
        try:
            # O_NOFOLLOW: an entry inside the store that is a symlink is refused
            # rather than followed. A store that follows links can be pointed at a
            # file outside its directory by anything that can write one name in
            # it, which is exactly the confinement the ref grammar just bought.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise UnknownSecretRef(f"no material stored under {secret_ref!r}") from None
        except OSError as exc:
            raise CorruptRecord(
                f"cannot read the record for {secret_ref!r}: {exc.strerror}"
            ) from None
        try:
            with os.fdopen(fd, encoding="utf-8", closefd=False) as handle:
                return handle.read()
        finally:
            os.close(fd)

    def _read(self, secret_ref: str) -> dict[str, Any]:
        """The one validated record for ``secret_ref``, from either of its names."""
        kind, flow_id = parse_secret_ref(secret_ref)
        try:
            text = self._read_text(self._path(secret_ref), secret_ref)
        except UnknownSecretRef:
            # Published name absent — but a pending write may hold material that
            # was fsynced before the crash (issue #15). Only *absence* falls
            # through: a published record that exists and does not parse is
            # corrupt, and corruption must raise rather than look absent.
            text = self._read_text(self._pending_path(secret_ref), secret_ref)
        return self._validate(text, secret_ref, kind, flow_id)

    def _validate(
        self, text: str, secret_ref: str, kind: SecretKind, flow_id: str
    ) -> dict[str, Any]:
        """Establish the whole record shape once, so no reader has to coerce.

        Key presence is not enough. A record whose ``material`` is ``""``, a
        list, or a number used to reach :meth:`get` and become a credential by
        ``str(...)`` — an empty access token handed to a caller that is about to
        mark a flow ``EXCHANGED``, which is the stranded slot :meth:`put` refuses
        to create, arriving through the read path instead.

        No value from the file appears in any message raised here. The file holds
        a credential, and a reader cannot know which field an attacker or a bad
        write put it in.
        """
        try:
            record = json.loads(text)
        except json.JSONDecodeError as exc:
            # `exc.doc` is the entire file and `str(exc)` names a column in it.
            # Neither is repeated: this record holds a credential, and a parse
            # failure is not a reason to print one. Only the decoder's own
            # description survives.
            raise CorruptRecord(f"record for {secret_ref!r} is not valid JSON: {exc.msg}") from None

        if not isinstance(record, dict):
            raise CorruptRecord(f"record for {secret_ref!r} is not an object")

        schema = record.get("schema")
        # `bool` is a subclass of `int` and `True == 1`, so a record carrying
        # `"schema": true` would pass a bare equality check against schema 1.
        if isinstance(schema, bool) or not isinstance(schema, int) or schema != RECORD_SCHEMA:
            raise CorruptRecord(
                f"record for {secret_ref!r} does not carry schema {RECORD_SCHEMA}, "
                f"which is the only shape this reader understands"
            )

        for key in ("secret_ref", "kind", "flow_id", "created_at", "material"):
            value = record.get(key)
            if not isinstance(value, str) or not value:
                raise CorruptRecord(
                    f"record for {secret_ref!r} has no usable {key}; a non-empty string is required"
                )

        # Every redundant identity field is bound to the name the file was found
        # under. reconcile() attributes material to a flow by its *name*, so a
        # name and contents that may disagree is how the wrong Item gets the
        # wrong access token — and the stored `secret_ref` was previously written
        # and never checked, which is a field that could only ever have lied.
        if (record["secret_ref"], record["kind"], record["flow_id"]) != (
            secret_ref,
            kind.value,
            flow_id,
        ):
            raise CorruptRecord(
                f"record stored as {secret_ref!r} describes a different secret; refusing to use it"
            )

        item_id = record.get("item_id")
        if item_id is not None and (not isinstance(item_id, str) or not item_id):
            raise CorruptRecord(
                f"record for {secret_ref!r} has an unusable item_id; "
                f"a non-empty string or null is required"
            )

        # The clock is validated here rather than where it is converted, because
        # `get` does not convert it. Leaving it to `_to_record` meant a record
        # `record()` refused was still a credential `get()` handed out — one file
        # with two verdicts, which is exactly the split "validate the whole shape
        # once" exists to remove.
        self._parse_created_at(record, secret_ref)
        return record

    @staticmethod
    def _parse_created_at(record: dict[str, Any], secret_ref: str) -> datetime:
        try:
            created_at = datetime.fromisoformat(cast(str, record["created_at"]))
        except ValueError:
            raise CorruptRecord(f"record for {secret_ref!r} has an unreadable created_at") from None
        if created_at.tzinfo is None:
            # A naive stamp cannot be compared across hosts, and every staleness
            # figure in this project is computed in UTC (AGENTS.md).
            raise CorruptRecord(f"record for {secret_ref!r} has a created_at with no timezone")
        return created_at.astimezone(UTC)

    def _to_record(self, record: dict[str, Any], secret_ref: str) -> SecretRecord:
        kind, flow_id = parse_secret_ref(secret_ref)
        return SecretRecord(
            secret_ref=secret_ref,
            kind=kind,
            flow_id=flow_id,
            item_id=cast("str | None", record.get("item_id")),
            created_at=self._parse_created_at(record, secret_ref),
        )
