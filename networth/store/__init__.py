"""SQLite Store facade and append-only repository errors."""

from networth.store.sqlite import (
    ObservationConflictError,
    ObservationRepository,
    SnapshotConflictError,
    SnapshotRepository,
    SnapshotRunNotSuccessfulError,
    Store,
    StoreConfigurationError,
    StoredDataError,
    StoreError,
)

__all__ = [
    "ObservationConflictError",
    "ObservationRepository",
    "SnapshotConflictError",
    "SnapshotRepository",
    "SnapshotRunNotSuccessfulError",
    "Store",
    "StoreConfigurationError",
    "StoreError",
    "StoredDataError",
]
