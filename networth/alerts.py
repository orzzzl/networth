"""Evaluate DESIGN section 11's four alerts and hand them to the payload.

The channel decision — in-app only — is what shapes this module.  There is no
way to reach the owner between publishes, so an alert is a **durable row that
is written before it travels**: if the process dies between evaluating and
publishing, the row is still there and the next cycle raises it again.  Nothing
here sends anything.

Three rules are enforced structurally rather than described:

- **The frozen-data threshold is never restated.**  This module reads
  :attr:`~networth.model.staleness.FreshnessAssessment.frozen_alert_required`,
  which task 11 computes from section 11's single definition.  It does not
  import the market-day count, does not count market days, and owns no
  calendar — so the alert and the display state cannot disagree about what
  "frozen" means.
- **Absence of evidence resolves nothing.**  Every input is a positive
  statement about one subject.  A subject the caller did not mention keeps
  whatever alerts it has, because "I did not look" and "the condition is over"
  are different facts and only one of them may close an alert.
- **A frozen-data alert resolves only on an advancing source clock.**  Not on a
  successful call, and — the case that is easy to get wrong — not when the Item
  leaves ``HEALTHY`` and the account therefore stops being classified
  ``FROZEN`` at all.  The data is just as frozen as it was; only the evidence
  changed.

Publication overdue is deliberately absent; see
:mod:`networth.model.alert`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from networth.model import Alert, AlertDraft, AlertKind, FreshnessAssessment, ItemHealth
from networth.model.figure import require_utc
from networth.store import AlertRepository

# Section 11's anti-fatigue rule: an unresolved alert may prompt the phone again
# after this long, and not sooner.  It is a property of the owner's attention
# rather than of any data source, which is why it is a plain constant here and
# not read from a policy object.
REPROMPT_AFTER = timedelta(hours=24)

_MESSAGES = {
    AlertKind.NEEDS_REAUTH: ("This connection needs you to sign in again before it can update."),
    AlertKind.REVOKED: (
        "This connection was disconnected and needs to be linked again before it can update."
    ),
    AlertKind.FROZEN_DATA: (
        "This account's data has stopped changing even though the connection looks fine. "
        "Its value may be out of date."
    ),
    AlertKind.PENDING_RECONCILIATION: (
        "This account is not counted in your total yet, because it still needs to be "
        "matched to the account it replaces."
    ),
}


@dataclass(frozen=True, slots=True)
class AccountSignal:
    """One account's current facts, as the caller observed them this cycle.

    ``freshness`` is task 11's assessment for the account, or ``None`` when the
    caller has no assessment for it this cycle — which is not evidence that the
    account is fine, only that it was not assessed.

    ``is_pending_reconciliation`` is ``account.reconciliation_state == 'NEW'``.
    It arrives as a boolean rather than as a shared enum because no account
    domain type exists yet: task ``04`` built the model for Items, observations
    and snapshots only.  Inventing one here would put the vocabulary for
    reconciliation in the alerting module, which is the wrong owner (`12b`).
    """

    account_id: int
    is_pending_reconciliation: bool
    freshness: FreshnessAssessment | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or isinstance(self.account_id, bool):
            raise TypeError("account_id must be an integer")
        if self.account_id <= 0:
            raise ValueError("account_id must be positive")
        if not isinstance(self.is_pending_reconciliation, bool):
            raise TypeError("is_pending_reconciliation must be a bool")
        if self.freshness is not None and not isinstance(self.freshness, FreshnessAssessment):
            raise TypeError("freshness must be a FreshnessAssessment or None")


@dataclass(frozen=True, slots=True)
class AlertEvaluation:
    """What one evaluation changed, in the order it changed it."""

    raised: tuple[Alert, ...]
    resolved: tuple[Alert, ...]


@dataclass(frozen=True, slots=True)
class DeliverableAlert:
    """One open alert on its way into a payload, and whether it may prompt.

    Every open alert travels on every publish — section 11's alerts are
    persistent until resolved, so a cached payload carries the whole current
    set.  ``prompt`` is the narrower question of whether the phone may raise a
    local notification for it, which is where the anti-fatigue rule applies.
    """

    alert: Alert
    prompt: bool


class AlertEvaluator:
    """Turn observed conditions into durable alert rows, and back again."""

    __slots__ = ("_alerts",)

    def __init__(self, alerts: AlertRepository) -> None:
        """Take the concrete repository, not a structural stand-in.

        Two of this module's guarantees are the database's — one open alert per
        subject, and a stored frozen alert always carrying its source clock — so
        a test double would be testing the rules against a model of the storage
        that enforces them.  The repository is cheap against an in-memory
        database, and using it means the tests exercise the real index.
        """

        if not isinstance(alerts, AlertRepository):
            raise TypeError("alerts must be an AlertRepository")
        self._alerts = alerts

    def evaluate(
        self,
        *,
        at: datetime,
        items: Iterable[ItemHealth] = (),
        accounts: Iterable[AccountSignal] = (),
    ) -> AlertEvaluation:
        """Raise and resolve alerts for exactly the subjects the caller reports."""

        require_utc(at, field="at")
        observed_items = tuple(items)
        for item in observed_items:
            if not isinstance(item, ItemHealth):
                raise TypeError("items must contain ItemHealth records")
        observed_accounts = tuple(accounts)
        for account in observed_accounts:
            if not isinstance(account, AccountSignal):
                raise TypeError("accounts must contain AccountSignal records")

        open_alerts = self._alerts.open()
        raised: list[Alert] = []
        resolved: list[Alert] = []

        for item in observed_items:
            wanted = AlertKind.for_item_state(item.status)
            for existing in _for_subject(open_alerts, item_id=item.id):
                if existing.kind is not wanted:
                    resolved.append(self._alerts.resolve(existing.id, at=at))
            if wanted is not None and not any(
                existing.kind is wanted for existing in _for_subject(open_alerts, item_id=item.id)
            ):
                raised.append(
                    self._alerts.raise_alert(
                        AlertDraft(
                            kind=wanted,
                            created_at=at,
                            message=_MESSAGES[wanted],
                            item_id=item.id,
                        )
                    )
                )

        for account in observed_accounts:
            existing_by_kind = {
                alert.kind: alert
                for alert in _for_subject(open_alerts, account_id=account.account_id)
            }
            self._evaluate_frozen(account, existing_by_kind, at, raised, resolved)
            self._evaluate_reconciliation(account, existing_by_kind, at, raised, resolved)

        return AlertEvaluation(raised=tuple(raised), resolved=tuple(resolved))

    def bulletin(self, *, at: datetime) -> tuple[DeliverableAlert, ...]:
        """The open set, with the ones that may prompt the phone marked.

        This is the read half of "written before it travels": the caller
        publishes what this returns and only then calls
        :meth:`record_prompted`.  A crash in between leaves ``notified_at``
        unset, so the next cycle offers the same alert to prompt again — the
        alert is re-raised rather than lost.
        """

        require_utc(at, field="at")
        return tuple(
            DeliverableAlert(alert=alert, prompt=self._may_prompt(alert, at))
            for alert in self._alerts.open()
        )

    def record_prompted(
        self,
        delivered: Iterable[DeliverableAlert],
        *,
        at: datetime,
    ) -> tuple[Alert, ...]:
        """Stamp the alerts that actually prompted, and only those.

        An alert that travelled quietly keeps its previous ``notified_at``, so
        the 24-hour window is measured from the last time the owner could have
        been prompted rather than from the last publish.  Measuring it from the
        publish would let a daily publish cadence silently become the alert
        cadence.
        """

        require_utc(at, field="at")
        stamped: list[Alert] = []
        for item in delivered:
            if not isinstance(item, DeliverableAlert):
                raise TypeError("delivered must contain DeliverableAlert records")
            if item.prompt:
                stamped.append(self._alerts.mark_notified(item.alert.id, at=at))
        return tuple(stamped)

    def _evaluate_frozen(
        self,
        account: AccountSignal,
        existing_by_kind: dict[AlertKind, Alert],
        at: datetime,
        raised: list[Alert],
        resolved: list[Alert],
    ) -> None:
        assessment = account.freshness
        if assessment is None:
            return
        open_alert = existing_by_kind.get(AlertKind.FROZEN_DATA)
        if open_alert is None:
            if assessment.frozen_alert_required:
                raised.append(
                    self._alerts.raise_alert(
                        AlertDraft(
                            kind=AlertKind.FROZEN_DATA,
                            created_at=at,
                            message=_MESSAGES[AlertKind.FROZEN_DATA],
                            account_id=account.account_id,
                            raised_source_as_of=assessment.source_as_of,
                        )
                    )
                )
            return
        raised_for = open_alert.raised_source_as_of
        if raised_for is None:  # pragma: no cover - the model requires it for this kind
            raise ValueError("a stored FROZEN_DATA alert must carry the clock it was raised for")
        if assessment.source_as_of is not None and assessment.source_as_of > raised_for:
            resolved.append(self._alerts.resolve(open_alert.id, at=at))

    def _evaluate_reconciliation(
        self,
        account: AccountSignal,
        existing_by_kind: dict[AlertKind, Alert],
        at: datetime,
        raised: list[Alert],
        resolved: list[Alert],
    ) -> None:
        open_alert = existing_by_kind.get(AlertKind.PENDING_RECONCILIATION)
        if account.is_pending_reconciliation:
            if open_alert is None:
                raised.append(
                    self._alerts.raise_alert(
                        AlertDraft(
                            kind=AlertKind.PENDING_RECONCILIATION,
                            created_at=at,
                            message=_MESSAGES[AlertKind.PENDING_RECONCILIATION],
                            account_id=account.account_id,
                        )
                    )
                )
        elif open_alert is not None:
            resolved.append(self._alerts.resolve(open_alert.id, at=at))

    @staticmethod
    def _may_prompt(alert: Alert, at: datetime) -> bool:
        if alert.notified_at is None:
            return True
        return at - alert.notified_at >= REPROMPT_AFTER


def _for_subject(
    alerts: Iterable[Alert],
    *,
    item_id: int | None = None,
    account_id: int | None = None,
) -> tuple[Alert, ...]:
    return tuple(
        alert
        for alert in alerts
        if (item_id is not None and alert.item_id == item_id)
        or (account_id is not None and alert.account_id == account_id)
    )


__all__ = [
    "REPROMPT_AFTER",
    "AccountSignal",
    "AlertEvaluation",
    "AlertEvaluator",
    "DeliverableAlert",
]
