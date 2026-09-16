"""Local phone pairing, rotation, and revocation (task 19a).

The database part of a rotation is one ``BEGIN IMMEDIATE`` transaction: the new
pairing becomes active, every previous active pairing is revoked, and the served
envelope is removed.  The payload key remains a host file, not database
material.  It is staged before the transaction and installed while the write
lock is held so the publisher cannot observe a new pairing with the old key.

That file replacement and the SQLite commit are deliberately not described as
one atomic operation: they are two resources.  Ordinary failures restore the
old file and roll back SQLite; an abrupt host loss between them is recovered by
running ``networth pair`` again.  No QR is emitted until SQLite has committed.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType

from networth.payload import KEY_BYTES

PAIRING_PREFIX = "networth-pairing:v1"
PAYLOAD_KEY_FILENAME = "networth-payload.key"
PAYLOAD_KEY_REF_PREFIX = "payload-key/"
_PAIRING_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_DNS_NAME = re.compile(
    r"(?=.{1,170}\Z)(?=.+[.])"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:[.][a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\Z"
)


class PairingError(RuntimeError):
    """Pairing state or provisioning material is unsafe to use."""


class PairingTransactionError(PairingError):
    """The caller did not give the operation ownership of its transaction."""


class PairingPayloadError(PairingError):
    """A scanned or typed pairing payload is not the exact v1 shape."""


class TailnetNameError(PairingError):
    """The VPS's full tailnet DNS name could not be established."""


def _encode_key(key: bytes) -> str:
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise PairingPayloadError(f"payload key must be exactly {KEY_BYTES} bytes")
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii")


def _decode_key(value: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise PairingPayloadError("payload key must be unpadded base64url")
    try:
        encoded = value.encode("ascii")
        key = base64.b64decode(encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, binascii.Error):
        raise PairingPayloadError("payload key must be unpadded base64url") from None
    if _encode_key(key) != value:
        raise PairingPayloadError("payload key is not canonical base64url")
    return key


def normalize_tailnet_name(value: str) -> str:
    """Return one full, lower-case DNS name without a trailing root dot."""

    if not isinstance(value, str):
        raise TailnetNameError("tailnet name must be text")
    normalized = value.strip().rstrip(".").lower()
    if _DNS_NAME.fullmatch(normalized) is None:
        raise TailnetNameError("tailnet name must be a full DNS name, not a host prefix or IP")
    return normalized


@dataclass(frozen=True, slots=True, repr=False)
class PairingProvision:
    """The one-time material shown to the phone, with key-safe repr."""

    pairing_id: str
    payload_key: bytes
    tailnet_name: str

    def __post_init__(self) -> None:
        if not isinstance(self.pairing_id, str) or _PAIRING_ID.fullmatch(self.pairing_id) is None:
            raise PairingPayloadError("pairing_id must be a canonical UUIDv4")
        _encode_key(self.payload_key)
        object.__setattr__(self, "tailnet_name", normalize_tailnet_name(self.tailnet_name))

    def __repr__(self) -> str:
        return (
            f"PairingProvision(pairing_id={self.pairing_id!r}, "
            f"payload_key=<redacted>, tailnet_name={self.tailnet_name!r})"
        )

    def encode(self) -> str:
        """Encode the exact QR/typed-fallback contract.

        The five colon-delimited members are a fixed scheme marker, a version,
        the pairing id, the payload key, and the VPS's full tailnet name.  There
        is no read token: tailnet membership plus this key are the credential.
        """

        return ":".join(
            (PAIRING_PREFIX, self.pairing_id, _encode_key(self.payload_key), self.tailnet_name)
        )


def decode_provision(value: str) -> PairingProvision:
    """Decode the strict phone-side contract for tests and non-Flutter clients."""

    if not isinstance(value, str):
        raise PairingPayloadError("pairing payload must be text")
    parts = value.split(":")
    if len(parts) != 5 or ":".join(parts[:2]) != PAIRING_PREFIX:
        raise PairingPayloadError("pairing payload is not the exact v1 shape")
    return PairingProvision(
        pairing_id=parts[2],
        payload_key=_decode_key(parts[3]),
        tailnet_name=parts[4],
    )


def new_provision(
    tailnet_name: str,
    *,
    key_factory: Callable[[int], bytes] = os.urandom,
    id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
) -> PairingProvision:
    key = key_factory(KEY_BYTES)
    pairing_id = id_factory()
    if not isinstance(pairing_id, uuid.UUID) or pairing_id.version != 4:
        raise PairingPayloadError("pairing id factory did not return a UUIDv4")
    return PairingProvision(
        pairing_id=str(pairing_id),
        payload_key=key,
        tailnet_name=tailnet_name,
    )


def payload_key_ref(pairing_id: str) -> str:
    """Return the unique database reference for one pairing's active file key."""

    if not isinstance(pairing_id, str) or _PAIRING_ID.fullmatch(pairing_id) is None:
        raise PairingPayloadError("pairing_id must be a canonical UUIDv4")
    return PAYLOAD_KEY_REF_PREFIX + pairing_id


def discover_tailnet_name() -> str:
    """Read this machine's full MagicDNS name from the local Tailscale daemon."""

    try:
        result = subprocess.run(  # noqa: S603 - the executable and arguments are fixed
            ["tailscale", "status", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise TailnetNameError(
            "cannot read the VPS tailnet name; pass --tailnet-name with its full DNS name"
        ) from None
    if result.returncode != 0:
        raise TailnetNameError(
            "tailscale status did not succeed; pass --tailnet-name with the VPS's full DNS name"
        )
    try:
        document = json.loads(result.stdout)
        raw = document["Self"]["DNSName"]
    except (json.JSONDecodeError, KeyError, TypeError):
        raise TailnetNameError(
            "tailscale status did not report Self.DNSName; pass --tailnet-name explicitly"
        ) from None
    if not isinstance(raw, str):
        raise TailnetNameError("tailscale Self.DNSName is not text")
    return normalize_tailnet_name(raw)


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("pairing timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class PairingStore:
    """Own the complete SQLite transaction for rotate and revoke."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        self._connection = connection
        self._connection.execute("PRAGMA busy_timeout = 5000")

    def rotate(
        self,
        provision: PairingProvision,
        *,
        key_ref: str,
        at: datetime,
        before_commit: Callable[[], None] | None = None,
    ) -> None:
        """Activate ``provision`` and revoke the old phone in one transaction."""

        if not isinstance(provision, PairingProvision):
            raise TypeError("provision must be PairingProvision")
        if not isinstance(key_ref, str) or not key_ref:
            raise ValueError("key_ref must be non-empty text")
        if before_commit is not None and not callable(before_commit):
            raise TypeError("before_commit must be callable")
        self._begin()
        try:
            timestamp = _timestamp(at)
            self._connection.execute(
                "INSERT INTO pairing(id, created_at, key_ref, state) VALUES (?, ?, ?, 'ACTIVE')",
                (provision.pairing_id, timestamp, key_ref),
            )
            self._connection.execute(
                """
                UPDATE pairing
                SET state = 'REVOKED', revoked_at = ?
                WHERE state = 'ACTIVE' AND id <> ?
                """,
                (timestamp, provision.pairing_id),
            )
            self._connection.execute("DELETE FROM published_envelope")
            if before_commit is not None:
                before_commit()
            self._connection.commit()
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise

    def revoke(self, *, at: datetime) -> int:
        """Revoke every active pairing and drop the served bytes atomically."""

        self._begin()
        try:
            cursor = self._connection.execute(
                "UPDATE pairing SET state = 'REVOKED', revoked_at = ? WHERE state = 'ACTIVE'",
                (_timestamp(at),),
            )
            self._connection.execute("DELETE FROM published_envelope")
            self._connection.commit()
        except BaseException:
            if self._connection.in_transaction:
                self._connection.rollback()
            raise
        return cursor.rowcount

    def _begin(self) -> None:
        if self._connection.in_transaction:
            raise PairingTransactionError(
                "pairing operation requires a connection with no active transaction"
            )
        self._connection.execute("BEGIN IMMEDIATE")


class StagedPayloadKey:
    """Stage a mode-600 key and make an ordinary failure recoverable.

    ``install`` is called while SQLite's immediate transaction is open.  The
    caller calls ``committed`` only after SQLite commits.  Until then, leaving
    the context restores the previous file (or removes the newly created one).
    """

    __slots__ = ("_activated", "_committed", "_old", "_path", "_temporary")

    def __init__(self, path: Path, key: bytes) -> None:
        if not isinstance(path, Path):
            raise TypeError("path must be pathlib.Path")
        encoded = (_encode_key(key) + "\n").encode("ascii")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        self._path = path
        self._temporary = Path(temporary)
        self._old: bytes | None = None
        self._activated = False
        self._committed = False
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            with suppress(OSError):
                os.close(descriptor)
            self._temporary.unlink(missing_ok=True)
            raise

    def __enter__(self) -> StagedPayloadKey:
        return self

    def install(self) -> None:
        if self._activated:
            raise PairingError("staged payload key was installed more than once")
        try:
            self._old = self._path.read_bytes()
        except FileNotFoundError:
            self._old = None
        os.replace(self._temporary, self._path)
        self._activated = True
        self._sync_directory()

    def committed(self) -> None:
        if not self._activated:
            raise PairingError("cannot commit a payload key that was not installed")
        self._committed = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        if self._activated and not self._committed:
            self._restore()
        self._temporary.unlink(missing_ok=True)

    def _restore(self) -> None:
        if self._old is None:
            self._path.unlink(missing_ok=True)
            self._sync_directory()
            return
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self._path.name}.restore.", dir=self._path.parent
        )
        temporary_path = Path(temporary)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(self._old)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self._path)
            self._sync_directory()
        finally:
            with suppress(OSError):
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def _sync_directory(self) -> None:
        descriptor = os.open(self._path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def read_payload_key(path: Path, *, key_ref: str | None = None) -> bytes:
    """Resolve the active pairing's key file without ever rendering its value.

    The database reference is pairing-specific because ``pairing.key_ref`` is
    unique. The host holds only one active key file, as DESIGN section 15
    requires; a supplied reference is validated before that file is opened.
    """

    if key_ref is not None:
        if not isinstance(key_ref, str) or not key_ref.startswith(PAYLOAD_KEY_REF_PREFIX):
            raise PairingError("payload key reference has the wrong shape")
        pairing_id = key_ref.removeprefix(PAYLOAD_KEY_REF_PREFIX)
        if _PAIRING_ID.fullmatch(pairing_id) is None:
            raise PairingError("payload key reference has the wrong shape")

    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        raise PairingError(f"cannot read payload key file {path}") from None
    try:
        return _decode_key(value)
    except PairingPayloadError:
        raise PairingError(f"payload key file {path} is malformed") from None
