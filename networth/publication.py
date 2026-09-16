"""Read the freshness of the last committed publication.

The publication ledger is success-only from migration 0005 onward.  A row is
therefore evidence that the envelope transaction committed; failed attempts do
not need a second outcome vocabulary and must not advance this clock.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from networth.model.figure import require_utc
from networth.publisher import GRACE_SECONDS, PUBLISH_INTERVAL_SECONDS


class PublicationMonitorError(RuntimeError):
    """The database cannot support an honest publication-freshness reading."""


@dataclass(frozen=True, slots=True)
class PublicationFreshness:
    """One point-in-time reading of section 6.4's success clock."""

    checked_at: datetime
    last_successful_at: datetime | None
    age: timedelta | None
    overdue: bool

    def __post_init__(self) -> None:
        require_utc(self.checked_at, field="checked_at")
        if self.last_successful_at is not None:
            require_utc(self.last_successful_at, field="last_successful_at")
        if (self.last_successful_at is None) != (self.age is None):
            raise ValueError("age is present exactly when a successful publication exists")
        if self.last_successful_at is None and not self.overdue:
            raise ValueError("a database with no successful publication is overdue")


def _stored_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise PublicationMonitorError("stored publication timestamp is not UTC")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError:
        raise PublicationMonitorError("stored publication timestamp is not ISO-8601") from None
    if parsed.utcoffset() != UTC.utcoffset(parsed):
        raise PublicationMonitorError("stored publication timestamp is not UTC")
    return parsed


class PublicationMonitor:
    """Read section 6.4 from committed rows, never from attempted work."""

    __slots__ = ("_connection", "_grace", "_interval")

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        publish_interval_seconds: int = PUBLISH_INTERVAL_SECONDS,
        grace_seconds: int = GRACE_SECONDS,
    ) -> None:
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        for field, value in (
            ("publish_interval_seconds", publish_interval_seconds),
            ("grace_seconds", grace_seconds),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"{field} must be an integer")
            if value <= 0:
                raise ValueError(f"{field} must be positive")
        self._connection = connection
        self._interval = timedelta(seconds=publish_interval_seconds)
        self._grace = timedelta(seconds=grace_seconds)

    def status(self, *, at: datetime) -> PublicationFreshness:
        """Return the latest committed success and whether its promise expired."""

        require_utc(at, field="at")
        if self._connection.in_transaction:
            raise PublicationMonitorError(
                "publication freshness requires a connection with no active transaction"
            )
        row = self._connection.execute(
            "SELECT published_at FROM publication ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return PublicationFreshness(
                checked_at=at,
                last_successful_at=None,
                age=None,
                overdue=True,
            )

        published_at = _stored_timestamp(row[0])
        return PublicationFreshness(
            checked_at=at,
            last_successful_at=published_at,
            age=at - published_at,
            overdue=at > published_at + self._interval + self._grace,
        )


__all__ = [
    "PublicationFreshness",
    "PublicationMonitor",
    "PublicationMonitorError",
]
