"""Forward-only SQLite migrations.

Migration files are package resources named ``NNNN_description.sql``. SQLite's
``user_version`` is the sole migration cursor, so the runner does not add a
bookkeeping table to the product schema described by ``DESIGN.md`` section 7.
Each file is applied atomically under ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources

_MIGRATION_PACKAGE = "networth.storage.sql"
_MIGRATION_NAME = re.compile(r"(?P<version>[0-9]{4})_[a-z0-9_]+[.]sql\Z")


class MigrationError(RuntimeError):
    """The migration set or database version is not safe to advance."""


class SchemaTooNewError(MigrationError):
    """The database was written by a newer version of networth."""


@dataclass(frozen=True, slots=True)
class _Migration:
    version: int
    name: str
    sql: str


def _load_migrations() -> tuple[_Migration, ...]:
    migrations: list[_Migration] = []
    for entry in resources.files(_MIGRATION_PACKAGE).iterdir():
        if not entry.is_file() or not entry.name.endswith(".sql"):
            continue
        match = _MIGRATION_NAME.fullmatch(entry.name)
        if match is None:
            raise MigrationError(
                f"invalid migration filename {entry.name!r}; expected NNNN_description.sql"
            )
        migrations.append(
            _Migration(
                version=int(match.group("version")),
                name=entry.name,
                sql=entry.read_text(encoding="utf-8"),
            )
        )

    migrations.sort(key=lambda migration: migration.version)
    versions = [migration.version for migration in migrations]
    expected = list(range(1, len(migrations) + 1))
    if versions != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 1; found {versions}, expected {expected}"
        )
    if not migrations:
        raise MigrationError("no migrations are packaged")
    return tuple(migrations)


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise MigrationError("SQLite did not return PRAGMA user_version")
    return int(row[0])


def _statements(script: str, *, name: str) -> Iterator[str]:
    """Yield complete statements without giving ``executescript`` a hidden commit.

    ``Connection.executescript`` commits a caller's pending transaction before
    running. Splitting with SQLite's own completeness parser lets the migration
    runner own one explicit transaction instead.
    """

    pending: list[str] = []
    for character in script:
        pending.append(character)
        if character != ";":
            continue
        candidate = "".join(pending)
        if sqlite3.complete_statement(candidate):
            if candidate.strip():
                yield candidate
            pending.clear()

    remainder = "".join(pending)
    if remainder.strip():
        raise MigrationError(f"migration {name!r} ends with an incomplete SQL statement")


def migrate(connection: sqlite3.Connection) -> tuple[int, ...]:
    """Advance ``connection`` to the newest packaged schema.

    Returns the versions applied by this call. Calling it again at the newest
    version returns an empty tuple and changes nothing. A newer database is
    refused: this runner only moves forward and never guesses how to downgrade.
    """

    if connection.in_transaction:
        raise MigrationError("migrate() requires a connection with no active transaction")

    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()
    if foreign_keys is None or int(foreign_keys[0]) != 1:
        raise MigrationError("SQLite foreign-key enforcement could not be enabled")

    migrations = _load_migrations()
    newest = migrations[-1].version
    current = _schema_version(connection)
    if current > newest:
        raise SchemaTooNewError(
            f"database schema version {current} is newer than supported version {newest}"
        )

    applied: list[int] = []
    for migration in migrations:
        if migration.version <= current:
            continue

        connection.execute("BEGIN IMMEDIATE")
        try:
            # Another process may have migrated while this connection waited for
            # the write lock. Re-read the cursor inside the transaction.
            locked_version = _schema_version(connection)
            if locked_version > newest:
                raise SchemaTooNewError(
                    f"database schema version {locked_version} is newer than "
                    f"supported version {newest}"
                )
            if locked_version >= migration.version:
                connection.commit()
                current = locked_version
                continue
            if locked_version != migration.version - 1:
                raise MigrationError(
                    f"cannot apply {migration.name}: database is at version {locked_version}"
                )

            for statement in _statements(migration.sql, name=migration.name):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.commit()
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

        current = migration.version
        applied.append(current)

    return tuple(applied)
