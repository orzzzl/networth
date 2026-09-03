"""Hourly `/item/get` polling: the complete v0 input to Axis A.

Scheduling the worker process belongs to task 16.  This module owns the due
boundary, resolves exactly one Item token at a time, performs the call through
the classified Plaid seam, and records both the connection state and the
independent Investments source clock.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from networth.model import ItemHealth, ItemHealthUpdate, ItemState
from networth.model.figure import require_utc
from networth.plaid import Classification, ItemStatus, malformed_response
from networth.store import ItemNotFoundError, ItemRepository

POLL_INTERVAL = timedelta(hours=1)

logger = logging.getLogger(__name__)


class _ItemGetter(Protocol):
    def item_get(self, access_token: str) -> ItemStatus: ...


class _Secret(Protocol):
    def reveal(self) -> str: ...


class _TokenResolver(Protocol):
    def get(self, secret_ref: str) -> _Secret: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class PollBatchResult:
    """Caller-visible outcome of one multi-Item polling attempt.

    Failed observations carry only exception type names.  The caller can commit
    :attr:`recorded` while still marking the enclosing run degraded, without an
    exception message accidentally carrying token or Item material across the
    poller's boundary.
    """

    attempted_count: int
    recorded: tuple[ItemHealth, ...]
    failure_types: tuple[str, ...]

    @property
    def recorded_count(self) -> int:
        return len(self.recorded)

    @property
    def failed_count(self) -> int:
        return len(self.failure_types)

    @property
    def ok(self) -> bool:
        return not self.failure_types


class ItemHealthPoller:
    """Poll due Items and persist the classified outcome.

    The token resolver is structural on purpose: task 10 is not blocked on the
    concrete TokenStore task, while that store's ``get(...).reveal()`` contract
    plugs in directly when it lands.  Token material is held only for the call
    and never enters a return value, database field, exception message, or log.

    The repository keeps transaction ownership with the caller, matching the
    rest of :class:`networth.store.Store`.
    """

    def __init__(
        self,
        items: ItemRepository,
        client: _ItemGetter,
        tokens: _TokenResolver,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._items = items
        self._client = client
        self._tokens = tokens
        self._clock = clock

    def poll_due(self, *, at: datetime | None = None) -> PollBatchResult:
        """Poll due Items, reporting any target skipped without an observation."""

        polled_at = self._poll_time(at)
        due = self._items.due_at_or_before(polled_at - POLL_INTERVAL)
        return self._observe_then_record(due, polled_at)

    def poll_all(self, *, at: datetime | None = None) -> PollBatchResult:
        """Poll every Item, reporting skipped targets alongside recorded results.

        This unconditional sweep is for probes and first-install use.  Scheduled
        work uses :meth:`poll_due` so downtime catch-up follows stored state.
        """

        polled_at = self._poll_time(at)
        return self._observe_then_record(self._items.all(), polled_at)

    def poll_item(self, item_id: int, *, at: datetime | None = None) -> ItemHealth:
        """Poll one Item immediately, such as directly after Link completes."""

        target = self._items.get(item_id)
        if target is None:
            raise ItemNotFoundError(f"item {item_id} does not exist")
        update = self._observe(target, self._poll_time(at))
        return self._items.record_poll(target.id, update)

    def _poll_time(self, at: datetime | None) -> datetime:
        value = self._clock() if at is None else at
        require_utc(value, field="poll time")
        return value

    def _observe_then_record(
        self,
        targets: tuple[ItemHealth, ...],
        polled_at: datetime,
    ) -> PollBatchResult:
        # Finish every network call before the first UPDATE.  SQLite's default
        # transaction opens on that UPDATE, so interleaving these loops would
        # hold the write transaction across later network waits.
        # One unusable token or unexpected client failure must not suppress the
        # remaining Items, or discard observations already made.  Skip that
        # target and report only safe exception types: re-raising after the
        # writes could make a caller-owned transaction roll all of them back.
        observations: list[tuple[int, ItemHealthUpdate]] = []
        failure_types: list[str] = []
        for target in targets:
            try:
                update = self._observe(target, polled_at)
            except Exception as exc:
                failure_types.append(type(exc).__name__)
            else:
                observations.append((target.id, update))

        recorded = tuple(
            self._items.record_poll(item_id, update) for item_id, update in observations
        )
        result = PollBatchResult(
            attempted_count=len(targets),
            recorded=recorded,
            failure_types=tuple(failure_types),
        )
        if not result.ok:
            logger.error(
                "%d Item poll attempt(s) produced no observation; skipped exception types: %s",
                result.failed_count,
                ", ".join(sorted(set(result.failure_types))),
            )
        return result

    def _observe(self, target: ItemHealth, polled_at: datetime) -> ItemHealthUpdate:
        secret = self._tokens.get(target.secret_ref)
        status = self._client.item_get(secret.reveal())
        classification, investments_observed, investments_update = self._validated_result(
            target, status
        )

        next_state = classification.state
        if not classification.item_state_observed and target.status.owner_actionable:
            next_state = None

        if next_state in (None, ItemState.HEALTHY):
            error_code = None
            error_detail = None
        else:
            error_code = classification.error_code
            error_detail = classification.detail

        return ItemHealthUpdate(
            polled_at=polled_at,
            status=next_state,
            error_code=error_code,
            error_detail=error_detail,
            investments_status_observed=investments_observed,
            investments_last_successful_update=investments_update,
        )

    @staticmethod
    def _validated_result(
        target: ItemHealth,
        status: ItemStatus,
    ) -> tuple[Classification, bool, datetime | None]:
        """Reject a response about a different Item before it mutates this one."""

        wrong_item = status.item_id is not None and status.item_id != target.plaid_item_id
        healthy_without_identity = (
            status.classification.state is ItemState.HEALTHY and status.item_id is None
        )
        if wrong_item or healthy_without_identity:
            return (
                malformed_response("the response Item id did not match the polled Item"),
                False,
                None,
            )
        return (
            status.classification,
            status.investments_status_observed,
            status.investments_last_successful_update,
        )


__all__ = ["POLL_INTERVAL", "ItemHealthPoller", "PollBatchResult"]
