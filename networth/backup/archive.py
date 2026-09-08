"""Coherent database + TokenStore capture and self-contained verification.

Build ordering follows ``DESIGN.md`` §14a.1 literally: capture under the lock,
derive the manifest from those copies, seal and fsync a temporary archive,
insert the outside hash row, then atomically rename.  Probe archives share the
same fidelity but never create a ``backup_archive`` row.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import stat
import struct
import tarfile
import tempfile
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from networth.backup.crypto import hkdf_sha256, open_sealed, seal
from networth.backup.state import (
    BackupStateError,
    BackupStateStore,
    open_database,
    timestamp_from_db,
)
from networth.filelock import LockUnavailable, exclusive_file_lock
from networth.tokenstore import RECORD_SCHEMA, InvalidSecretRef, TokenStore, parse_secret_ref

ARCHIVE_FORMAT = 1
CURRENT_ARCHIVE = "current.nwb"
PROBE_ARCHIVE = "probe.nwb"
PROBE_COOLDOWN = timedelta(seconds=60)
FINGERPRINT_INFO = b"networth/token-fingerprint/v1"
DATABASE_MEMBER = "database.sqlite"
MANIFEST_MEMBER = "manifest.json"
TOKEN_PREFIX = "tokenstore/"
_HASH_RE = re.compile(r"\A[0-9a-f]{64}\Z")


class ArchiveError(RuntimeError):
    """An archive could not be built without weakening its guarantees."""


class ArchiveVerificationError(RuntimeError):
    """A sealed archive cannot prove its own manifest and token binding."""


class ProbeBusyError(ArchiveError):
    """A non-blocking probe lost the single-flight lock."""


class ArchiveKind(StrEnum):
    CURRENT = "current"
    PROBE = "probe"


class ProbeOutcome(StrEnum):
    BUILT = "built"
    REUSED = "reused"


@dataclass(frozen=True, slots=True)
class ArchiveManifest:
    archive_format: int
    archive_id: str
    archive_kind: ArchiveKind
    built_at: datetime
    schema_version: int
    db_row_counts: dict[str, int]
    item_count: int
    probe_generation: int
    item_token_binding_sha256: str

    def as_object(self) -> dict[str, object]:
        return {
            "archive_format": self.archive_format,
            "archive_id": self.archive_id,
            "archive_kind": self.archive_kind.value,
            "built_at": _timestamp(self.built_at),
            "schema_version": self.schema_version,
            "db_row_counts": dict(sorted(self.db_row_counts.items())),
            "item_count": self.item_count,
            "probe_generation": self.probe_generation,
            "item_token_binding_sha256": self.item_token_binding_sha256,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.as_object())

    @classmethod
    def parse(cls, data: bytes) -> ArchiveManifest:
        try:
            raw = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArchiveVerificationError("archive manifest is not valid UTF-8 JSON") from None
        expected = {
            "archive_format",
            "archive_id",
            "archive_kind",
            "built_at",
            "schema_version",
            "db_row_counts",
            "item_count",
            "probe_generation",
            "item_token_binding_sha256",
        }
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ArchiveVerificationError("archive manifest has an unsupported shape")
        try:
            archive_format = _integer(raw["archive_format"], "archive_format")
            archive_id = _text(raw["archive_id"], "archive_id")
            archive_kind = ArchiveKind(_text(raw["archive_kind"], "archive_kind"))
            built_at = _manifest_timestamp(raw["built_at"])
            schema_version = _integer(raw["schema_version"], "schema_version")
            item_count = _integer(raw["item_count"], "item_count")
            probe_generation = _integer(raw["probe_generation"], "probe_generation")
            binding = _text(raw["item_token_binding_sha256"], "binding")
        except (BackupStateError, ValueError) as exc:
            raise ArchiveVerificationError("archive manifest contains an invalid value") from exc
        if re.fullmatch(r"[0-9a-f]{32}", archive_id) is None:
            raise ArchiveVerificationError("archive manifest has an invalid archive_id")
        if archive_format != ARCHIVE_FORMAT:
            raise ArchiveVerificationError("archive manifest uses an unsupported format")
        if schema_version < 1 or item_count < 0 or probe_generation < 0:
            raise ArchiveVerificationError("archive manifest contains a negative count or version")
        if _HASH_RE.fullmatch(binding) is None:
            raise ArchiveVerificationError("archive manifest has an invalid binding digest")
        counts_raw = raw["db_row_counts"]
        if not isinstance(counts_raw, dict) or not counts_raw:
            raise ArchiveVerificationError("archive manifest has no database row counts")
        counts: dict[str, int] = {}
        for name, value in counts_raw.items():
            if not isinstance(name, str) or not name or "\x00" in name:
                raise ArchiveVerificationError("archive manifest has an invalid table name")
            count = _integer(value, "row count")
            if count < 0:
                raise ArchiveVerificationError("archive manifest has a negative row count")
            counts[name] = count
        return cls(
            archive_format=archive_format,
            archive_id=archive_id,
            archive_kind=archive_kind,
            built_at=built_at,
            schema_version=schema_version,
            db_row_counts=counts,
            item_count=item_count,
            probe_generation=probe_generation,
            item_token_binding_sha256=binding,
        )


@dataclass(frozen=True, slots=True)
class BuildResult:
    archive_id: str
    path: Path
    archive_sha256: str
    byte_size: int
    manifest: ArchiveManifest


@dataclass(frozen=True, slots=True)
class ProbeResult:
    probe_generation: int
    outcome: ProbeOutcome
    path: Path


@dataclass(frozen=True, slots=True)
class VerificationResult:
    manifest: ArchiveManifest
    orphan_token_count: int


@dataclass(frozen=True, slots=True, repr=False)
class _Captured:
    database: bytes
    token_files: dict[str, bytes]


@dataclass(frozen=True, slots=True, repr=False)
class _OpenedBundle:
    manifest: ArchiveManifest
    database: bytes
    token_files: dict[str, bytes]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("timestamp must be aware UTC")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _manifest_timestamp(value: object) -> datetime:
    parsed = timestamp_from_db(value)
    if parsed is None:
        raise ValueError("manifest timestamp is null")
    return parsed


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is not text")
    return value


def _integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{field} is not an integer")
    return value


def _token_ref_from_filename(name: str) -> str | None:
    if name.endswith(".json") and not name.startswith("."):
        ref = name[: -len(".json")]
    elif name.startswith(".") and name.endswith(".pending"):
        ref = name[1 : -len(".pending")]
    else:
        return None
    parse_secret_ref(ref)
    return ref


class _CopiedTokenStore:
    """Resolve one ref at a time from captured bytes; never expose all material."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self._files = files

    def refs(self) -> frozenset[str]:
        return frozenset(
            ref for name in self._files if (ref := _token_ref_from_filename(name)) is not None
        )

    def material(self, secret_ref: str) -> str:
        parse_secret_ref(secret_ref)
        final = f"{secret_ref}.json"
        pending = f".{secret_ref}.pending"
        name = final if final in self._files else pending
        data = self._files.get(name)
        if data is None:
            raise ArchiveVerificationError(
                f"item secret_ref {secret_ref!r} has no token in the archive"
            )
        try:
            raw = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ArchiveVerificationError(
                f"archived token record for {secret_ref!r} is not valid JSON"
            ) from None
        if not isinstance(raw, dict):
            raise ArchiveVerificationError(
                f"archived token record for {secret_ref!r} is not an object"
            )
        kind, flow_id = parse_secret_ref(secret_ref)
        expected_identity = (RECORD_SCHEMA, secret_ref, kind.value, flow_id)
        actual_identity = (
            raw.get("schema"),
            raw.get("secret_ref"),
            raw.get("kind"),
            raw.get("flow_id"),
        )
        material = raw.get("material")
        if actual_identity != expected_identity or not isinstance(material, str) or not material:
            raise ArchiveVerificationError(
                f"archived token record for {secret_ref!r} fails identity validation"
            )
        return material


def _read_token_files(token_store: TokenStore) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(token_store.directory.iterdir(), key=lambda candidate: candidate.name):
        if path.name == token_store.lock_path.name:
            continue
        try:
            ref = _token_ref_from_filename(path.name)
        except InvalidSecretRef as exc:
            raise ArchiveError("TokenStore contains a malformed record filename") from exc
        if ref is None:
            raise ArchiveError("TokenStore contains an unexpected entry; refusing a partial copy")
        del ref  # the validated filename is the useful fact here
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ArchiveError(
                f"cannot open TokenStore record {path.name!r}: {exc.strerror}"
            ) from None
        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ArchiveError(f"TokenStore entry {path.name!r} is not a regular file")
            with os.fdopen(fd, "rb", closefd=False) as handle:
                files[path.name] = handle.read()
        finally:
            os.close(fd)
    return files


def _snapshot_database(source: Path, destination: Path) -> bytes:
    if destination.exists():
        raise ArchiveError("database snapshot path already exists")
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("VACUUM INTO ?", (str(destination),))
    except sqlite3.Error as exc:
        raise ArchiveError(f"VACUUM INTO failed: {exc}") from None
    finally:
        connection.close()
    try:
        return destination.read_bytes()
    finally:
        destination.unlink(missing_ok=True)


def _capture(
    database: Path, token_store: TokenStore, archive_dir: Path, archive_id: str
) -> _Captured:
    snapshot = archive_dir / f".snapshot-{archive_id}.db"
    database_bytes = _snapshot_database(database, snapshot)
    token_files = _read_token_files(token_store)
    return _Captured(database=database_bytes, token_files=token_files)


def _open_snapshot(
    database_bytes: bytes,
) -> tuple[tempfile.TemporaryDirectory[str], sqlite3.Connection]:
    temporary = tempfile.TemporaryDirectory(prefix="networth-backup-verify-")
    path = Path(temporary.name) / DATABASE_MEMBER
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(database_bytes)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    return temporary, connection


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    counts: dict[str, int] = {}
    for name in names:
        quoted = name.replace('"', '""')
        row = connection.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()
        if row is None:
            raise ArchiveVerificationError(f"could not count archived table {name!r}")
        counts[name] = int(row[0])
    return counts


def _items(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        "SELECT plaid_item_id, secret_ref FROM item ORDER BY plaid_item_id"
    ).fetchall()
    items: list[tuple[str, str]] = []
    for item_id, secret_ref in rows:
        if not isinstance(item_id, str) or not item_id:
            raise ArchiveVerificationError("archived item has no usable item identity")
        if not isinstance(secret_ref, str) or not secret_ref:
            raise ArchiveVerificationError("archived item has no usable secret_ref")
        items.append((item_id, secret_ref))
    return tuple(items)


def _length_prefixed(text: str) -> bytes:
    encoded = text.encode("utf-8")
    if len(encoded) > 0xFFFFFFFF:
        raise ArchiveError("fingerprint field exceeds uint32 length")
    return struct.pack(">I", len(encoded)) + encoded


def _binding_digest(
    *,
    items: tuple[tuple[str, str], ...],
    tokens: _CopiedTokenStore,
    backup_key: bytes,
    archive_id: str,
) -> str:
    fingerprint_key = hkdf_sha256(
        backup_key,
        salt=archive_id.encode("ascii"),
        info=FINGERPRINT_INFO,
    )
    mapping: list[list[str]] = []
    for item_id, secret_ref in sorted(items):
        material = tokens.material(secret_ref)
        message = b"".join(
            (
                _length_prefixed(item_id),
                _length_prefixed(secret_ref),
                _length_prefixed(material),
            )
        )
        fingerprint = hmac.new(fingerprint_key, message, hashlib.sha256).digest()[:16]
        mapping.append([item_id, secret_ref, fingerprint.hex()])
    return hashlib.sha256(_canonical_json(mapping)).hexdigest()


def _manifest_for(
    captured: _Captured,
    *,
    backup_key: bytes,
    archive_id: str,
    archive_kind: ArchiveKind,
    built_at: datetime,
    probe_generation: int,
) -> ArchiveManifest:
    temporary, connection = _open_snapshot(captured.database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ArchiveError("VACUUM snapshot failed SQLite integrity_check")
        schema_row = connection.execute("PRAGMA user_version").fetchone()
        if schema_row is None:
            raise ArchiveError("VACUUM snapshot has no schema version")
        items = _items(connection)
        tokens = _CopiedTokenStore(captured.token_files)
        binding = _binding_digest(
            items=items,
            tokens=tokens,
            backup_key=backup_key,
            archive_id=archive_id,
        )
        return ArchiveManifest(
            archive_format=ARCHIVE_FORMAT,
            archive_id=archive_id,
            archive_kind=archive_kind,
            built_at=built_at,
            schema_version=int(schema_row[0]),
            db_row_counts=_table_counts(connection),
            item_count=len(items),
            probe_generation=probe_generation,
            item_token_binding_sha256=binding,
        )
    finally:
        connection.close()
        temporary.cleanup()


def _tar_member(name: str, data: bytes, *, mode: int, mtime: int) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = mtime
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info, data


def _bundle(captured: _Captured, manifest: ArchiveManifest) -> bytes:
    output = io.BytesIO()
    mtime = int(manifest.built_at.timestamp())
    members = [
        _tar_member(MANIFEST_MEMBER, manifest.canonical_bytes(), mode=0o600, mtime=mtime),
        _tar_member(DATABASE_MEMBER, captured.database, mode=0o600, mtime=mtime),
    ]
    members.extend(
        _tar_member(f"{TOKEN_PREFIX}{name}", data, mode=0o600, mtime=mtime)
        for name, data in sorted(captured.token_files.items())
    )
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for info, data in members:
            archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def _opened_bundle(envelope: bytes, backup_key: bytes) -> _OpenedBundle:
    plaintext = open_sealed(envelope, backup_key)
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            for member in archive.getmembers():
                if not member.isfile() or member.name in members:
                    raise ArchiveVerificationError(
                        "archive bundle has a duplicate or non-file member"
                    )
                allowed = member.name in (
                    MANIFEST_MEMBER,
                    DATABASE_MEMBER,
                ) or member.name.startswith(TOKEN_PREFIX)
                if not allowed or member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ArchiveVerificationError("archive bundle contains an unexpected path")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ArchiveVerificationError("archive member could not be read")
                members[member.name] = extracted.read()
    except (tarfile.TarError, OSError):
        raise ArchiveVerificationError("decrypted archive is not a readable tar bundle") from None
    if MANIFEST_MEMBER not in members or DATABASE_MEMBER not in members:
        raise ArchiveVerificationError("archive bundle is missing its manifest or database")
    token_files: dict[str, bytes] = {}
    for name, data in members.items():
        if not name.startswith(TOKEN_PREFIX):
            continue
        basename = name[len(TOKEN_PREFIX) :]
        if not basename or "/" in basename or _token_ref_from_filename(basename) is None:
            raise ArchiveVerificationError("archive bundle contains a malformed TokenStore path")
        token_files[basename] = data
    return _OpenedBundle(
        manifest=ArchiveManifest.parse(members[MANIFEST_MEMBER]),
        database=members[DATABASE_MEMBER],
        token_files=token_files,
    )


def _verify_opened(opened: _OpenedBundle, backup_key: bytes) -> VerificationResult:
    temporary, connection = _open_snapshot(opened.database)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity != ("ok",):
            raise ArchiveVerificationError("archived database fails SQLite integrity_check")
        schema_row = connection.execute("PRAGMA user_version").fetchone()
        if schema_row != (opened.manifest.schema_version,):
            raise ArchiveVerificationError("archived database schema does not match its manifest")
        if _table_counts(connection) != opened.manifest.db_row_counts:
            raise ArchiveVerificationError("archived database row counts do not match its manifest")
        items = _items(connection)
        if len(items) != opened.manifest.item_count:
            raise ArchiveVerificationError("archived item count does not match its manifest")
        tokens = _CopiedTokenStore(opened.token_files)
        binding = _binding_digest(
            items=items,
            tokens=tokens,
            backup_key=backup_key,
            archive_id=opened.manifest.archive_id,
        )
        if not hmac.compare_digest(binding, opened.manifest.item_token_binding_sha256):
            raise ArchiveVerificationError("item-to-token binding does not match the manifest")
        referenced = frozenset(secret_ref for _, secret_ref in items)
        return VerificationResult(
            manifest=opened.manifest,
            orphan_token_count=len(tokens.refs() - referenced),
        )
    finally:
        connection.close()
        temporary.cleanup()


def verify_archive(path: Path, backup_key: bytes) -> VerificationResult:
    """Verify one archive using no state outside the file and its key."""

    try:
        envelope = path.read_bytes()
    except OSError as exc:
        raise ArchiveVerificationError(f"cannot read archive {path}: {exc.strerror}") from None
    return _verify_opened(_opened_bundle(envelope, backup_key), backup_key)


def _write_temp(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class BackupBuilder:
    """Build real and probe archives from explicit, non-discovered paths."""

    def __init__(
        self,
        *,
        database: Path,
        token_store: TokenStore,
        archive_dir: Path,
        backup_key: bytes,
    ) -> None:
        self.database = database
        self.token_store = token_store
        self.archive_dir = archive_dir
        self._backup_key = backup_key
        self.archive_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.archive_dir, 0o700)
        self._build_lock_path = self.archive_dir.parent / f".{self.archive_dir.name}.build.lock"

    def _cleanup_stale(self) -> None:
        for path in self.archive_dir.iterdir():
            if not (path.name.startswith(".tmp-") or path.name.startswith(".snapshot-")):
                continue
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(metadata.st_mode):
                path.unlink()

    def _build_bytes(
        self,
        *,
        archive_id: str,
        kind: ArchiveKind,
        built_at: datetime,
        probe_generation: int,
    ) -> tuple[bytes, ArchiveManifest]:
        captured = _capture(self.database, self.token_store, self.archive_dir, archive_id)
        manifest = _manifest_for(
            captured,
            backup_key=self._backup_key,
            archive_id=archive_id,
            archive_kind=kind,
            built_at=built_at,
            probe_generation=probe_generation,
        )
        return seal(_bundle(captured, manifest), self._backup_key), manifest

    def build_current(self, *, now: datetime | None = None) -> BuildResult:
        built_at = datetime.now(UTC) if now is None else now
        _timestamp(built_at)
        archive_id = uuid.uuid4().hex
        # This lock protects builder-owned temporary files through publication.
        # The narrower TokenStore lock below remains the database/token capture
        # boundary; separating them keeps another builder from cleaning a live
        # temp after that capture lock is released.
        with exclusive_file_lock(self._build_lock_path):
            self._cleanup_stale()
            with closing(open_database(self.database)) as connection:
                with exclusive_file_lock(self.token_store.lock_path):
                    envelope, manifest = self._build_bytes(
                        archive_id=archive_id,
                        kind=ArchiveKind.CURRENT,
                        built_at=built_at,
                        probe_generation=0,
                    )
                temporary = self.archive_dir / f".tmp-{archive_id}"
                _write_temp(temporary, envelope)
                sealed_on_disk = temporary.read_bytes()
                archive_hash = hashlib.sha256(sealed_on_disk).hexdigest()
                byte_size = len(sealed_on_disk)
                BackupStateStore(connection).insert_archive(
                    archive_id=archive_id,
                    built_at=built_at,
                    archive_sha256=archive_hash,
                    byte_size=byte_size,
                    manifest_sha256=hashlib.sha256(manifest.canonical_bytes()).hexdigest(),
                )
                connection.commit()
                destination = self.archive_dir / CURRENT_ARCHIVE
                os.replace(temporary, destination)
                _fsync_directory(self.archive_dir)
        return BuildResult(
            archive_id=archive_id,
            path=destination,
            archive_sha256=archive_hash,
            byte_size=byte_size,
            manifest=manifest,
        )

    def build_probe(self, *, now: datetime | None = None) -> ProbeResult:
        built_at = datetime.now(UTC) if now is None else now
        _timestamp(built_at)
        try:
            build_lock = exclusive_file_lock(self._build_lock_path, blocking=False)
            build_lock.__enter__()
        except LockUnavailable:
            with closing(open_database(self.database)) as refused:
                BackupStateStore(refused).count_probe_refusal()
                refused.commit()
            raise ProbeBusyError("probe build refused: another archive build is active") from None
        try:
            try:
                token_lock = exclusive_file_lock(self.token_store.lock_path, blocking=False)
                token_lock.__enter__()
            except LockUnavailable:
                with closing(open_database(self.database)) as refused:
                    BackupStateStore(refused).count_probe_refusal()
                    refused.commit()
                raise ProbeBusyError("probe build refused: another token write is active") from None
            try:
                self._cleanup_stale()
                with closing(open_database(self.database)) as connection:
                    state_store = BackupStateStore(connection)
                    state = state_store.probe()
                    probe_path = self.archive_dir / PROBE_ARCHIVE
                    if (
                        state.built_at is not None
                        and built_at - state.built_at < PROBE_COOLDOWN
                        and probe_path.is_file()
                    ):
                        state_store.count_probe_refusal()
                        connection.commit()
                        return ProbeResult(state.generation, ProbeOutcome.REUSED, probe_path)

                    generation = state.generation + 1
                    archive_id = uuid.uuid4().hex
                    envelope, _ = self._build_bytes(
                        archive_id=archive_id,
                        kind=ArchiveKind.PROBE,
                        built_at=built_at,
                        probe_generation=generation,
                    )
                    temporary = self.archive_dir / f".tmp-probe-{archive_id}"
                    _write_temp(temporary, envelope)
                    os.replace(temporary, probe_path)
                    _fsync_directory(self.archive_dir)
                    state_store.mark_probe_built(generation=generation, built_at=built_at)
                    connection.commit()
                    return ProbeResult(generation, ProbeOutcome.BUILT, probe_path)
            finally:
                token_lock.__exit__(None, None, None)
        finally:
            build_lock.__exit__(None, None, None)


def opened_archive_for_restore(
    path: Path, backup_key: bytes
) -> tuple[_OpenedBundle, VerificationResult]:
    """Internal restore seam: authenticate and verify before returning bytes."""

    try:
        opened = _opened_bundle(path.read_bytes(), backup_key)
    except OSError as exc:
        raise ArchiveVerificationError(f"cannot read archive {path}: {exc.strerror}") from None
    return opened, _verify_opened(opened, backup_key)
