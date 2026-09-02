"""Append-only repositories over the section-7 SQLite schema.

The repositories accept an existing connection so the caller owns transaction
boundaries.  They never commit, open another file, call a service, or hide a
figure behind a number-only convenience method.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import cast

from networth.model import (
    Observation,
    ObservationDraft,
    ObservationSource,
    Snapshot,
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SnapshotDraft,
    SourcedFigure,
)
from networth.model.figure import require_utc


class StoreError(RuntimeError):
    """Base class for repository contract failures."""


class StoredDataError(StoreError):
    """A row cannot satisfy the domain contract."""


class ObservationConflictError(StoreError):
    """The run already has an observation for this account."""


class SnapshotConflictError(StoreError):
    """A run id was retried with a different snapshot payload."""


class SnapshotRunNotSuccessfulError(StoreError):
    """A snapshot was requested for a run that did not finish successfully."""


_OBSERVATION_COLUMNS = """
    o.id, o.sync_run_id, o.account_id, o.observed_at,
    o.value_minor, o.currency, o.source, o.fetched_at,
    o.source_as_of, o.source_clock, o.is_carried_forward
"""

_SNAPSHOT_COLUMNS = """
    s.id, s.sync_run_id, s.taken_at,
    s.total_net_worth_minor, s.total_assets_minor, s.total_liabilities_minor,
    s.account_count, s.stale_account_count, s.unknown_freshness_account_count,
    s.static_account_count, s.reauth_account_count, s.unreconciled_account_count,
    s.is_complete, s.age_state, s.as_of, s.oldest_known_source_as_of
"""


def _timestamp_to_db(value: datetime) -> str:
    require_utc(value, field="timestamp")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _timestamp_from_db(value: object, *, field: str) -> datetime:
    text = _text(value, field=field)
    if not text.endswith("Z"):
        raise StoredDataError(f"{field} is not a UTC timestamp with a Z suffix")
    try:
        parsed = datetime.fromisoformat(f"{text[:-1]}+00:00")
    except ValueError:
        raise StoredDataError(f"{field} is not an ISO-8601 timestamp") from None
    try:
        require_utc(parsed, field=field)
    except (TypeError, ValueError) as exc:
        raise StoredDataError(f"{field} is not an aware UTC timestamp") from exc
    return parsed


def _optional_timestamp_from_db(value: object, *, field: str) -> datetime | None:
    if value is None:
        return None
    return _timestamp_from_db(value, field=field)


def _optional_timestamp_to_db(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _timestamp_to_db(value)


def _text(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise StoredDataError(f"{field} is not text")
    return value


def _integer(value: object, *, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise StoredDataError(f"{field} is not an integer")
    return value


def _boolean(value: object, *, field: str) -> bool:
    integer = _integer(value, field=field)
    if integer not in (0, 1):
        raise StoredDataError(f"{field} is not a SQLite boolean")
    return bool(integer)


def _rows(cursor: sqlite3.Cursor) -> tuple[tuple[object, ...], ...]:
    return tuple(cast(list[tuple[object, ...]], cursor.fetchall()))


def _row(cursor: sqlite3.Cursor) -> tuple[object, ...] | None:
    return cast(tuple[object, ...] | None, cursor.fetchone())


def _observation_from_row(row: tuple[object, ...]) -> Observation:
    try:
        source = ObservationSource(_text(row[6], field="observation.source"))
        return Observation(
            id=_integer(row[0], field="observation.id"),
            sync_run_id=_text(row[1], field="observation.sync_run_id"),
            account_id=_integer(row[2], field="observation.account_id"),
            observed_at=_timestamp_from_db(row[3], field="observation.observed_at"),
            figure=SourcedFigure(
                value_minor=_integer(row[4], field="observation.value_minor"),
                currency=_text(row[5], field="observation.currency"),
                as_of=_optional_timestamp_from_db(row[8], field="observation.source_as_of"),
                source_clock=_text(row[9], field="observation.source_clock"),
            ),
            source=source,
            fetched_at=_timestamp_from_db(row[7], field="observation.fetched_at"),
            is_carried_forward=_boolean(row[10], field="observation.is_carried_forward"),
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise StoredDataError("observation row violates the domain model") from exc


def _snapshot_from_row(row: tuple[object, ...]) -> Snapshot:
    try:
        age = SnapshotAge(
            state=SnapshotAgeState(_text(row[13], field="snapshot.age_state")),
            as_of=_optional_timestamp_from_db(row[14], field="snapshot.as_of"),
            oldest_known_source_as_of=_optional_timestamp_from_db(
                row[15], field="snapshot.oldest_known_source_as_of"
            ),
        )
        return Snapshot(
            id=_integer(row[0], field="snapshot.id"),
            sync_run_id=_text(row[1], field="snapshot.sync_run_id"),
            taken_at=_timestamp_from_db(row[2], field="snapshot.taken_at"),
            net_worth=age.figure(_integer(row[3], field="snapshot.total_net_worth_minor")),
            assets=age.figure(_integer(row[4], field="snapshot.total_assets_minor")),
            liabilities=age.figure(_integer(row[5], field="snapshot.total_liabilities_minor")),
            counts=SnapshotCounts(
                account_count=_integer(row[6], field="snapshot.account_count"),
                stale_account_count=_integer(row[7], field="snapshot.stale_account_count"),
                unknown_freshness_account_count=_integer(
                    row[8], field="snapshot.unknown_freshness_account_count"
                ),
                static_account_count=_integer(row[9], field="snapshot.static_account_count"),
                reauth_account_count=_integer(row[10], field="snapshot.reauth_account_count"),
                unreconciled_account_count=_integer(
                    row[11], field="snapshot.unreconciled_account_count"
                ),
            ),
            is_complete=_boolean(row[12], field="snapshot.is_complete"),
            age=age,
        )
    except (IndexError, TypeError, ValueError) as exc:
        raise StoredDataError("snapshot row violates the domain model") from exc


class ObservationRepository:
    """Insert and read observations; intentionally no update or delete API."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, draft: ObservationDraft) -> Observation:
        """Append one account value.

        A second value for the same account and run is refused, even when it is
        identical.  Retrying the whole transaction is safe; changing a value is
        a correction and therefore belongs to a new run and a new row.
        """

        if not isinstance(draft, ObservationDraft):
            raise TypeError("draft must be an ObservationDraft")
        result = _row(
            self._connection.execute(
                """
                INSERT INTO observation(
                    sync_run_id, account_id, observed_at, value_minor, currency,
                    source, fetched_at, source_as_of, source_clock, is_carried_forward
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sync_run_id, account_id) DO NOTHING
                RETURNING id
                """,
                (
                    draft.sync_run_id,
                    draft.account_id,
                    _timestamp_to_db(draft.observed_at),
                    draft.figure.value_minor,
                    draft.figure.currency,
                    draft.source.value,
                    _timestamp_to_db(draft.fetched_at),
                    _optional_timestamp_to_db(draft.figure.as_of),
                    draft.figure.source_clock,
                    int(draft.is_carried_forward),
                ),
            )
        )
        if result is None:
            raise ObservationConflictError(
                "an observation already exists for this sync run and account; "
                "record a correction in a new run"
            )
        return Observation(
            id=_integer(result[0], field="observation.id"),
            sync_run_id=draft.sync_run_id,
            account_id=draft.account_id,
            observed_at=draft.observed_at,
            figure=draft.figure,
            source=draft.source,
            fetched_at=draft.fetched_at,
            is_carried_forward=draft.is_carried_forward,
        )

    def get(self, observation_id: int) -> Observation | None:
        row = _row(
            self._connection.execute(
                f"SELECT {_OBSERVATION_COLUMNS} FROM observation AS o WHERE o.id = ?",
                (observation_id,),
            )
        )
        return None if row is None else _observation_from_row(row)

    def for_sync_run(self, sync_run_id: str) -> tuple[Observation, ...]:
        rows = _rows(
            self._connection.execute(
                f"""
                SELECT {_OBSERVATION_COLUMNS}
                FROM observation AS o
                WHERE o.sync_run_id = ?
                ORDER BY o.account_id, o.id
                """,
                (sync_run_id,),
            )
        )
        return tuple(_observation_from_row(row) for row in rows)

    def latest_for_account(self, account_id: int) -> Observation | None:
        row = _row(
            self._connection.execute(
                f"""
                SELECT {_OBSERVATION_COLUMNS}
                FROM observation AS o
                WHERE o.account_id = ?
                ORDER BY julianday(o.observed_at) DESC, o.id DESC
                LIMIT 1
                """,
                (account_id,),
            )
        )
        return None if row is None else _observation_from_row(row)

    def latest_by_account(self) -> tuple[Observation, ...]:
        """Return one latest row for every account that currently has history."""

        rows = _rows(
            self._connection.execute(
                f"""
                SELECT {_OBSERVATION_COLUMNS}
                FROM observation AS o
                WHERE o.id = (
                    SELECT candidate.id
                    FROM observation AS candidate
                    WHERE candidate.account_id = o.account_id
                    ORDER BY julianday(candidate.observed_at) DESC, candidate.id DESC
                    LIMIT 1
                )
                ORDER BY o.account_id
                """
            )
        )
        return tuple(_observation_from_row(row) for row in rows)

    def history_for_lineage(self, lineage_id: int) -> tuple[Observation, ...]:
        """Read one logical account's append-only history across replacement Items."""

        rows = _rows(
            self._connection.execute(
                f"""
                SELECT {_OBSERVATION_COLUMNS}
                FROM observation AS o
                JOIN account AS a ON a.id = o.account_id
                WHERE a.lineage_id = ?
                ORDER BY julianday(o.observed_at), o.id
                """,
                (lineage_id,),
            )
        )
        return tuple(_observation_from_row(row) for row in rows)


class SnapshotRepository:
    """Append and read successful-run snapshots; no mutation API exists."""

    __slots__ = ("_connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def append(self, draft: SnapshotDraft) -> Snapshot:
        """Append once per successful run, idempotently on ``sync_run_id``.

        An exact retry returns the original row.  Reusing the run id with any
        different payload is refused rather than overwriting history or hiding
        the disagreement.
        """

        if not isinstance(draft, SnapshotDraft):
            raise TypeError("draft must be a SnapshotDraft")
        self._require_successful_run(draft.sync_run_id)

        existing = self.for_sync_run(draft.sync_run_id)
        if existing is not None:
            return self._idempotent_result(existing, draft)

        result = _row(
            self._connection.execute(
                """
                INSERT INTO snapshot(
                    sync_run_id, taken_at,
                    total_net_worth_minor, total_assets_minor, total_liabilities_minor,
                    account_count, stale_account_count, unknown_freshness_account_count,
                    static_account_count, reauth_account_count, unreconciled_account_count,
                    is_complete, age_state, as_of, oldest_known_source_as_of
                )
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                FROM sync_run AS successful_run
                WHERE successful_run.id = ?
                  AND successful_run.finished_at IS NOT NULL
                  AND successful_run.ok = 1
                ON CONFLICT(sync_run_id) DO NOTHING
                RETURNING id
                """,
                (
                    draft.sync_run_id,
                    _timestamp_to_db(draft.taken_at),
                    draft.net_worth.value_minor,
                    draft.assets.value_minor,
                    draft.liabilities.value_minor,
                    draft.counts.account_count,
                    draft.counts.stale_account_count,
                    draft.counts.unknown_freshness_account_count,
                    draft.counts.static_account_count,
                    draft.counts.reauth_account_count,
                    draft.counts.unreconciled_account_count,
                    int(draft.is_complete),
                    draft.age.state.value,
                    _optional_timestamp_to_db(draft.age.as_of),
                    _optional_timestamp_to_db(draft.age.oldest_known_source_as_of),
                    draft.sync_run_id,
                ),
            )
        )
        if result is not None:
            return Snapshot(
                id=_integer(result[0], field="snapshot.id"),
                sync_run_id=draft.sync_run_id,
                taken_at=draft.taken_at,
                net_worth=draft.net_worth,
                assets=draft.assets,
                liabilities=draft.liabilities,
                counts=draft.counts,
                is_complete=draft.is_complete,
                age=draft.age,
            )

        # A concurrent writer may have won the unique sync_run_id between the
        # first read and INSERT.  Read and compare rather than turning a benign
        # identical retry into a uniqueness failure.
        existing = self.for_sync_run(draft.sync_run_id)
        if existing is None:
            self._require_successful_run(draft.sync_run_id)
            raise StoreError("snapshot insert returned no row and no conflicting row exists")
        return self._idempotent_result(existing, draft)

    def _require_successful_run(self, sync_run_id: str) -> None:
        row = _row(
            self._connection.execute(
                "SELECT finished_at, ok FROM sync_run WHERE id = ?",
                (sync_run_id,),
            )
        )
        if row is None or row[0] is None or row[1] != 1:
            raise SnapshotRunNotSuccessfulError(
                "a snapshot can only be appended for a finished successful sync run"
            )

    @staticmethod
    def _idempotent_result(existing: Snapshot, draft: SnapshotDraft) -> Snapshot:
        if existing.as_draft() != draft:
            raise SnapshotConflictError(
                "this sync_run_id already names a different snapshot; corrections require a new run"
            )
        return existing

    def get(self, snapshot_id: int) -> Snapshot | None:
        row = _row(
            self._connection.execute(
                f"SELECT {_SNAPSHOT_COLUMNS} FROM snapshot AS s WHERE s.id = ?",
                (snapshot_id,),
            )
        )
        return None if row is None else _snapshot_from_row(row)

    def for_sync_run(self, sync_run_id: str) -> Snapshot | None:
        row = _row(
            self._connection.execute(
                f"""
                SELECT {_SNAPSHOT_COLUMNS}
                FROM snapshot AS s
                WHERE s.sync_run_id = ?
                """,
                (sync_run_id,),
            )
        )
        return None if row is None else _snapshot_from_row(row)

    def latest(self) -> Snapshot | None:
        row = _row(
            self._connection.execute(
                f"""
                SELECT {_SNAPSHOT_COLUMNS}
                FROM snapshot AS s
                ORDER BY julianday(s.taken_at) DESC, s.id DESC
                LIMIT 1
                """
            )
        )
        return None if row is None else _snapshot_from_row(row)

    def history(self) -> tuple[Snapshot, ...]:
        rows = _rows(
            self._connection.execute(
                f"""
                SELECT {_SNAPSHOT_COLUMNS}
                FROM snapshot AS s
                ORDER BY julianday(s.taken_at), s.id
                """
            )
        )
        return tuple(_snapshot_from_row(row) for row in rows)


class Store:
    """The SQLite-only repository seam used by later components."""

    __slots__ = ("observations", "snapshots")

    observations: ObservationRepository
    snapshots: SnapshotRepository

    def __init__(self, connection: sqlite3.Connection) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        self.observations = ObservationRepository(connection)
        self.snapshots = SnapshotRepository(connection)
