"""Evaluate DESIGN section 11's alerts, and section 12's, for the payload.

The channel decision — in-app only — is what shapes this module.  There is no
way to reach the owner between publishes, so an alert is a **durable row that
is written before it travels**: if the process dies between evaluating and
publishing, the row is still there and the next cycle raises it again.  Nothing
here sends anything.

These rules are enforced structurally rather than described.  (The count used to
be written out here and was already one short of the list; task 27 added another
and dropped the number rather than correcting it to a figure that drifts again.)

- **The frozen-data threshold is never restated.**  This module reads
  :attr:`~networth.model.staleness.FreshnessAssessment.frozen_alert_required`,
  which task 11 computes from section 11's single definition.  It does not
  import the market-day count, does not count market days, and owns no
  calendar — so the alert and the display state cannot disagree about what
  "frozen" means.
- **Absence of evidence resolves nothing.**  Every input is a positive
  statement about one subject.  A subject the caller did not mention keeps
  whatever alerts it has, because "I did not look" and "the condition is over"
  are different facts and only one of them may close an alert.  An *observed*
  absence — "I read the manual side and there is no share count" — is the
  second of those, and it does close one; the two are separate values here
  (:class:`ShareCountObservation`) rather than one shared ``None``, because a
  rule this important cannot rest on remembering which absence is which.
- **No observed clock may be in the future.**  Every kind that resolves on an
  advancing clock would read a future instant as the largest advance there is,
  resolve, and then decline to raise the replacement — losing the alert
  entirely.  Refused up front, before anything is written.
- **A frozen-data alert resolves only on an advancing source clock.**  Not on a
  successful call, and — the case that is easy to get wrong — not when the Item
  leaves ``HEALTHY`` and the account therefore stops being classified
  ``FROZEN`` at all.  The data is just as frozen as it was; only the evidence
  changed.
- **Resolving that row is not the same as the condition ending.**  A feed that
  is a week late can advance a day and still be frozen, so an evaluation that
  resolves on the advance raises the replacement against the new clock *in the
  same evaluation*.  Otherwise a still-frozen account publishes an empty
  bulletin for a full cycle — the alert would be lost exactly where this module
  claims it cannot be.

Publication overdue is deliberately absent; see
:mod:`networth.model.alert`.

Task ``27``'s share-count nudge is here rather than in a module of its own
because it is the same machinery pointed at the manual side: a durable row, one
per subject, resolving only when a clock the owner controls actually advances.
It is the fifth kind, and :mod:`networth.model.alert` carries the argument for
why it is not one of the four.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from networth.model import (
    Alert,
    AlertDraft,
    AlertKind,
    FreshnessAssessment,
    ItemHealth,
    ItemState,
)
from networth.model.figure import require_utc
from networth.store import AlertRepository

# Section 11's anti-fatigue rule: an unresolved alert may prompt the phone again
# after this long, and not sooner.  It is a property of the owner's attention
# rather than of any data source, which is why it is a plain constant here and
# not read from a policy object.
REPROMPT_AFTER = timedelta(hours=24)

# Section 12 asks for a *periodic* nudge to re-confirm a manual share count and
# names no period, so one is chosen here and named rather than buried in a
# comparison.  Ninety days, for three reasons that are preferences rather than
# facts and should be read as such: vest schedules are commonly quarterly, so a
# quarterly question lands near the event that changes the answer; a share count
# that drifts is a slow error and not an urgent one; and section 11's "the
# anti-fatigue rule matters more, not less" on a single-channel design argues
# for the longer end of any defensible range.  Changing it is this one line —
# nothing derives a threshold from it and no stored row encodes it.
RECONFIRM_SHARE_COUNT_AFTER = timedelta(days=90)

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
    AlertKind.SHARE_COUNT_UNCONFIRMED: (
        "This account's share count has not been confirmed since you set it. If shares "
        "have vested since then, its value is out of date until you confirm the new count."
    ),
}


@dataclass(frozen=True, slots=True)
class ShareCountObservation:
    """The caller read this account's manual side; here is what was there.

    ``confirmed_on`` is :attr:`~networth.model.manual.EquityHolding.set_on` when
    the account holds a share count, and ``None`` when it holds none — which is
    a **positive statement** ("I looked; there is nothing to confirm") and so
    resolves a standing nudge.  Omitting the observation from
    :class:`AccountSignal` says the opposite: the caller did not look, and an
    open nudge stands.

    Those two facts shared one ``None`` in this module's first version and could
    not be told apart, so an account whose holding was deleted — or changed to a
    manual asset that has no share count at all — kept its nudge forever, asking
    the owner to re-confirm a number that no longer exists and offering him no
    action that would clear it.  A tri-state is the whole fix: *unread*, *absent*,
    *confirmed on a date*.

    It carries a clock and nothing else, for the reason in :class:`AccountSignal`.
    """

    confirmed_on: datetime | None

    def __post_init__(self) -> None:
        if self.confirmed_on is not None:
            require_utc(self.confirmed_on, field="confirmed_on")


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

    ``share_count`` is a :class:`ShareCountObservation` when the caller read the
    account's manual side this cycle, and ``None`` when it did not.  As with
    ``freshness``, ``None`` is not evidence about the count — see that class for
    the difference between not looking and looking at nothing.

    **The observation carries the clock and not the holding on purpose.**
    Passing the :class:`~networth.model.manual.EquityHolding` would hand this
    module a symbol and a quantity it has no use for, and the messages composed
    here travel to the phone and into logs (AGENTS.md section 1).  What is
    actually enforced is narrower than "this module cannot see a quantity", and
    the tests say so: the account-side input surface — this class, that one, and
    :meth:`AlertEvaluator.evaluate`'s parameters — is pinned field by field, so
    widening it enough to admit a quantity is an edit someone has to argue for
    rather than one that arrives as a convenience.
    """

    account_id: int
    is_pending_reconciliation: bool
    freshness: FreshnessAssessment | None = None
    share_count: ShareCountObservation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, int) or isinstance(self.account_id, bool):
            raise TypeError("account_id must be an integer")
        if self.account_id <= 0:
            raise ValueError("account_id must be positive")
        if not isinstance(self.is_pending_reconciliation, bool):
            raise TypeError("is_pending_reconciliation must be a bool")
        if self.freshness is not None and not isinstance(self.freshness, FreshnessAssessment):
            raise TypeError("freshness must be a FreshnessAssessment or None")
        if self.share_count is not None and not isinstance(self.share_count, ShareCountObservation):
            raise TypeError("share_count must be a ShareCountObservation or None")


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
            _refuse_future_clocks(account, at=at)

        open_alerts = self._alerts.open()
        raised: list[Alert] = []
        resolved: list[Alert] = []

        for item in observed_items:
            wanted = AlertKind.for_item_state(item.status)
            # Section 11 resolves these "on the transition back to HEALTHY" — and
            # DEGRADED is not that transition.  A connection that needs re-auth
            # and then has a transient failure still needs re-auth; clearing the
            # alert there would drop a live, owner-actionable fault on the way
            # past.  Task 10 protects the same invariant one layer down, where an
            # unobserved state is recorded as no transition rather than as a
            # downgrade.  A *different* owner-actionable state does supersede,
            # because the owner's next action changed.
            supersedes = item.status is ItemState.HEALTHY or wanted is not None
            for existing in _for_subject(open_alerts, item_id=item.id):
                if existing.kind is not wanted and supersedes:
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
            self._evaluate_share_count(account, existing_by_kind, at, raised, resolved)

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
        if open_alert is not None:
            raised_for = open_alert.raised_source_as_of
            if raised_for is None:  # pragma: no cover - the model requires it for this kind
                raise ValueError(
                    "a stored FROZEN_DATA alert must carry the clock it was raised for"
                )
            if assessment.source_as_of is None or assessment.source_as_of <= raised_for:
                return
            # The clock advanced, so this row's claim — "stuck at ``raised_for``"
            # — is over, and section 11 resolves it on exactly that.  What it
            # does not do is stop the account being frozen: task 11 counts the
            # market closes *after* ``source_as_of``, not the time since the
            # clock last moved, so a feed running a week late can advance a day
            # and still be five closes behind.  Falling through to the raise
            # below is therefore not an optimisation — resolving without it
            # would drop a live condition, and the next ``bulletin()`` would
            # carry nothing at all for an account that is still frozen.
            #
            # The order is forced rather than chosen: migration 0004's partial
            # index allows one open alert per subject, so the old row must close
            # before the replacement can exist.
            resolved.append(self._alerts.resolve(open_alert.id, at=at))

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

    def _evaluate_share_count(
        self,
        account: AccountSignal,
        existing_by_kind: dict[AlertKind, Alert],
        at: datetime,
        raised: list[Alert],
        resolved: list[Alert],
    ) -> None:
        """Section 12's periodic nudge: ask, and never change the number.

        The only manual value this method is handed is a date, and the only
        writes it makes are to the alert table — so task ``27``'s "must not" is
        not a rule this method remembers to follow.  It is not enforced by that
        fact either, which would be an argument about today's code rather than a
        guard: what is enforced is the pinned input surface described on
        :class:`AccountSignal`, which is what a future edit would have to widen
        before a quantity could arrive here at all.
        """

        observation = account.share_count
        if observation is None:
            return
        open_alert = existing_by_kind.get(AlertKind.SHARE_COUNT_UNCONFIRMED)
        set_on = observation.confirmed_on
        if set_on is None:
            # Read, and there is no share count on this account at all.  The
            # subject of the nudge is gone — the holding was deleted, or the
            # manual asset is now one that has no quantity to confirm — so the
            # row asking about it goes too.  This is the module's one place
            # where an absence resolves something, and it is allowed only
            # because :class:`ShareCountObservation` makes this absence a
            # statement the caller made rather than a silence.
            if open_alert is not None:
                resolved.append(self._alerts.resolve(open_alert.id, at=at))
            return
        if open_alert is not None:
            raised_for = open_alert.raised_source_as_of
            if raised_for is None:  # pragma: no cover - the model requires it for this kind
                raise ValueError(
                    "a stored SHARE_COUNT_UNCONFIRMED alert must carry the clock it was raised for"
                )
            if set_on <= raised_for:
                # Nothing was re-confirmed.  The standing row already says
                # exactly this, and re-raising it would restart the anti-fatigue
                # window every cycle — the nudge would become the noise it is
                # meant not to be.
                return
            # The owner confirmed a count, so this row's claim is over.  Whether
            # the condition is over is a second question: a confirmation may be
            # *back-dated*, and one dated more than the period ago leaves the
            # holding just as unconfirmed as before.  Falling through to the
            # raise below handles that, and it is the same shape as frozen data
            # advancing a day while staying five closes behind.
            resolved.append(self._alerts.resolve(open_alert.id, at=at))

        if at - set_on >= RECONFIRM_SHARE_COUNT_AFTER:
            raised.append(
                self._alerts.raise_alert(
                    AlertDraft(
                        kind=AlertKind.SHARE_COUNT_UNCONFIRMED,
                        created_at=at,
                        message=_MESSAGES[AlertKind.SHARE_COUNT_UNCONFIRMED],
                        account_id=account.account_id,
                        raised_source_as_of=set_on,
                    )
                )
            )

    @staticmethod
    def _may_prompt(alert: Alert, at: datetime) -> bool:
        if alert.notified_at is None:
            return True
        return at - alert.notified_at >= REPROMPT_AFTER


def _refuse_future_clocks(account: AccountSignal, *, at: datetime) -> None:
    """An observation may not claim an instant that has not happened yet.

    Both of an account's clocks mean *the last time something happened* —
    task 11's ``source_as_of`` and the owner's ``confirmed_on`` — and both kinds
    that carry one resolve when it advances.  A clock in the future therefore
    reads as the largest advance possible: the open row resolves, and then the
    replacement is not raised, because the condition that would raise it is a
    subtraction against ``at`` that has gone negative.  The alert leaves the
    bulletin entirely and no later cycle brings it back while the bad clock
    stands.  That is the one outcome this module exists to prevent, so it is
    refused rather than interpreted.

    **It is refused before anything is written.**  ``evaluate`` validates every
    account and only then mutates, so one bad clock raises instead of leaving a
    half-applied evaluation behind — the row it would have resolved is still
    open and still in the bulletin.

    This is a caller bug of the same class as a naive datetime, and it gets the
    same treatment: there is no safe guess about which direction the clock was
    wrong in, so the answer is to fix the clock.  Both kinds are checked here
    rather than one, because the defect was found on the new kind and the merged
    frozen path had it too — it is a property of resolving on a clock, not of
    task 27.  The value itself is never put in the message: a confirmation date
    is the owner's, and an exception travels into logs.
    """

    freshness = account.freshness
    share_count = account.share_count
    for field, clock in (
        ("freshness.source_as_of", None if freshness is None else freshness.source_as_of),
        ("share_count.confirmed_on", None if share_count is None else share_count.confirmed_on),
    ):
        if clock is not None and clock > at:
            raise ValueError(
                f"{field} is after the instant being evaluated; a clock that "
                "records when something last happened cannot be in the future"
            )


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
    "RECONFIRM_SHARE_COUNT_AFTER",
    "REPROMPT_AFTER",
    "AccountSignal",
    "AlertEvaluation",
    "AlertEvaluator",
    "DeliverableAlert",
    "ShareCountObservation",
]
