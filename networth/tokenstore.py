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
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

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


# A conservative identifier: no dot, no slash, no separator of any kind, so a
# `flow_id` cannot spell a relative path and a `secret_ref` cannot address a file
# outside the store. Validation happens before any name becomes a path — the
# confinement is in the grammar, not in a check after the fact.
_NAME = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
_FLOW_ID_RE = re.compile(rf"\A{_NAME}\Z")
_SECRET_REF_RE = re.compile(
    r"\A(?P<kind>"
    + "|".join(re.escape(k.value) for k in SecretKind)
    + rf")\.(?P<flow_id>{_NAME})\Z"
)

_SUFFIX = ".json"


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


@dataclass(frozen=True)
class SecretRecord:
    """Everything about a stored secret **except** the secret.

    Returned by the metadata reads so that "does material exist for this flow?"
    never has to move a credential to answer.
    """

    secret_ref: str
    kind: SecretKind
    flow_id: str
    item_id: str | None
    created_at: datetime


def secret_ref_for(kind: SecretKind, flow_id: str) -> str:
    """The ``secret_ref`` naming scheme (issue #15).

    It encodes the ``flow_id`` precisely so that material written before its
    database row exists is still attributable after a restart.
    """
    if not _FLOW_ID_RE.match(flow_id):
        raise InvalidSecretRef(f"not a usable flow_id: {flow_id!r}")
    return f"{kind.value}.{flow_id}"


def parse_secret_ref(secret_ref: str) -> tuple[SecretKind, str]:
    """Split a ``secret_ref`` back into its kind and ``flow_id``."""
    match = _SECRET_REF_RE.match(secret_ref)
    if match is None:
        raise InvalidSecretRef(f"not a well-formed secret_ref: {secret_ref!r}")
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

        Durable when this returns: the record is ``fsync``ed, renamed into place,
        and the directory itself is ``fsync``ed so the rename survives too. The
        caller commits the ``item`` row **after** this call (§14a ordering) — that
        way a crash in between leaves an orphan, never a stranded slot.

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
        """
        if not material:
            # Writing empty material would let a flow reach EXCHANGED holding
            # nothing, which reads as success and strands the slot exactly as a
            # lost token would — with none of the evidence that it happened.
            raise ValueError("refusing to store empty material")
        ref = secret_ref_for(kind, flow_id)
        path = self._path(ref)

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

        # A unique temporary name, and one that carries the ref it belongs to: a
        # crash between the write and the link leaves this file behind, and a
        # leftover holding material should be attributable rather than anonymous.
        # Uniqueness also means a leftover never blocks the retry — a fixed
        # temporary name would fail O_EXCL forever after one crash.
        fd, tmp_name = tempfile.mkstemp(dir=self._dir, prefix=f".{ref}.", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            # mkstemp already opens 0600, and umask can only clear bits, so both
            # paths agree — but the mode on disk is the acceptance criterion, so
            # it is set rather than inherited.
            os.fchmod(fd, FILE_MODE)
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as handle:
                json.dump(record, handle)
                handle.flush()
            os.fsync(fd)
        finally:
            os.close(fd)

        try:
            os.link(tmp, path)
        except FileExistsError:
            raise SecretRefExists(
                f"material already stored under {ref!r}; "
                f"reconcile before writing (issue #15), or delete it explicitly"
            ) from None
        finally:
            tmp.unlink(missing_ok=True)

        self._fsync_directory()
        return ref

    # --- reading ---------------------------------------------------------------

    def get(self, secret_ref: str) -> Secret:
        """Resolve one ``secret_ref`` to its material."""
        return Secret(str(self._read(secret_ref)["material"]))

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

        Answers from the one path the naming scheme predicts — no directory
        listing, and nothing about any other flow is read or returned.
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
        """
        path = self._path(secret_ref)
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        self._fsync_directory()
        return True

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

    def _path(self, secret_ref: str) -> Path:
        parse_secret_ref(secret_ref)  # rejects anything that could name another file
        return self._dir / f"{secret_ref}{_SUFFIX}"

    def _fsync_directory(self) -> None:
        fd = os.open(self._dir, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _read(self, secret_ref: str) -> dict[str, Any]:
        path = self._path(secret_ref)
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
                text = handle.read()
        finally:
            os.close(fd)

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
        if record.get("schema") != RECORD_SCHEMA:
            raise CorruptRecord(
                f"record for {secret_ref!r} has schema {record.get('schema')!r}, "
                f"and this reader understands {RECORD_SCHEMA}"
            )
        missing = [
            key for key in ("kind", "flow_id", "created_at", "material") if key not in record
        ]
        if missing:
            raise CorruptRecord(f"record for {secret_ref!r} is missing {', '.join(missing)}")
        return record

    def _to_record(self, record: dict[str, Any], secret_ref: str) -> SecretRecord:
        kind, flow_id = parse_secret_ref(secret_ref)
        if record["kind"] != kind.value or record["flow_id"] != flow_id:
            # The name and the contents disagree: the file has been moved,
            # hand-edited or written by something else. Refusing is the only safe
            # answer — reconcile() attributes material to a flow by its *name*,
            # and attributing the wrong token to a flow is how the wrong Item
            # gets its access token.
            raise CorruptRecord(
                f"record stored as {secret_ref!r} describes a different secret; refusing to use it"
            )
        try:
            created_at = datetime.fromisoformat(str(record["created_at"]))
        except ValueError:
            raise CorruptRecord(f"record for {secret_ref!r} has an unreadable created_at") from None
        if created_at.tzinfo is None:
            raise CorruptRecord(f"record for {secret_ref!r} has a created_at with no timezone")
        item_id = record.get("item_id")
        return SecretRecord(
            secret_ref=secret_ref,
            kind=kind,
            flow_id=flow_id,
            item_id=None if item_id is None else str(item_id),
            created_at=created_at.astimezone(UTC),
        )
