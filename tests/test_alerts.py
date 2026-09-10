"""The alerts, and the rules that keep a single channel credible.

Task 15 wrote section 11's four; task 27 added section 12's fifth, which is the
same machinery pointed at a number the owner typed rather than at a connection.
"""

from __future__ import annotations

import ast
import inspect
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import networth.alerts as alerts_module
from networth.alerts import (
    RECONFIRM_SHARE_COUNT_AFTER,
    REPROMPT_AFTER,
    AccountSignal,
    AlertEvaluator,
    DeliverableAlert,
    ShareCountObservation,
)
from networth.model import (
    Alert,
    AlertDraft,
    AlertKind,
    FreshnessAssessment,
    FreshnessState,
    ItemHealth,
    ItemState,
)
from networth.storage import migrate
from networth.store import AlertRepository, Store

NOW = datetime(2026, 3, 10, 15, 0, tzinfo=UTC)
FROZEN_SINCE = datetime(2026, 3, 2, 21, 0, tzinfo=UTC)
MANUAL_ACCOUNT = 2
# Long enough ago that the count is overdue however the boundary is read; the
# boundary itself is measured by its own test rather than inferred from this.
CONFIRMED_LONG_AGO = NOW - RECONFIRM_SHARE_COUNT_AFTER - timedelta(days=110)


def _db_time(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(id, plaid_institution_id, name, is_oauth) "
        "VALUES (1, 'ins-1', 'Synthetic', 0)"
    )
    connection.execute(
        """
        INSERT INTO item(
            id, institution_id, plaid_item_id, secret_ref, status, status_since, created_at
        ) VALUES (1, 1, 'plaid-item-1', 'secret-ref-1', 'HEALTHY', ?, ?)
        """,
        (_db_time(NOW), _db_time(NOW)),
    )
    connection.execute(
        """
        INSERT INTO account(
            id, name, type, currency, sign, freshness_policy,
            include_in_net_worth, reconciliation_state, created_at
        ) VALUES (1, 'Synthetic account', 'synthetic', 'USD', 1, 'SYNCED_HOLDINGS', 1, 'NEW', ?)
        """,
        (_db_time(NOW),),
    )
    # Task 27's subject: an account whose quantity the owner typed, with no
    # Item behind it.  The evaluator never reads either row, so the second one
    # buys fidelity rather than behaviour — a nudge asserted against a synced
    # account would read as evidence about a case that cannot occur.
    connection.execute(
        """
        INSERT INTO account(
            id, name, type, currency, sign, freshness_policy,
            include_in_net_worth, reconciliation_state, created_at
        ) VALUES (
            2, 'Synthetic manual account', 'synthetic', 'USD', 1,
            'MANUAL_QTY_LIVE_PRICE', 1, 'CONFIRMED', ?
        )
        """,
        (_db_time(NOW),),
    )
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def repository(db: sqlite3.Connection) -> AlertRepository:
    return Store(db).alerts


@pytest.fixture
def evaluator(repository: AlertRepository) -> AlertEvaluator:
    return AlertEvaluator(repository)


def item(status: ItemState, *, item_id: int = 1, since: datetime = NOW) -> ItemHealth:
    return ItemHealth(
        id=item_id,
        plaid_item_id=f"plaid-item-{item_id}",
        secret_ref=f"secret-ref-{item_id}",
        status=status,
        status_since=since,
        last_polled_at=None,
        investments_last_successful_update=None,
        last_error_code=None if status is ItemState.HEALTHY else "SYNTHETIC",
        last_error_detail=None if status is ItemState.HEALTHY else "synthetic detail",
    )


def frozen(source_as_of: datetime = FROZEN_SINCE) -> FreshnessAssessment:
    return FreshnessAssessment(
        state=FreshnessState.FROZEN,
        source_as_of=source_as_of,
        market_days_without_advance=5,
        is_carried_forward=False,
        item_state=ItemState.HEALTHY,
    )


def not_frozen(
    source_as_of: datetime = FROZEN_SINCE,
    *,
    state: FreshnessState = FreshnessState.STALE,
    item_state: ItemState | None = ItemState.DEGRADED,
) -> FreshnessAssessment:
    return FreshnessAssessment(
        state=state,
        source_as_of=source_as_of,
        market_days_without_advance=0,
        is_carried_forward=False,
        item_state=item_state,
    )


def only(alerts: tuple[Alert, ...]) -> Alert:
    assert len(alerts) == 1, alerts
    return alerts[0]


def _declared(function: Callable[..., object]) -> dict[str, str]:
    """Every parameter and the return, as annotated in the source.

    ``from __future__ import annotations`` leaves these as strings, so what is
    compared is what a reader of the module sees rather than what it resolves to
    — which is the point: the check is on the declaration.
    """

    signature = inspect.signature(function)
    declared = {
        name: str(parameter.annotation)
        for name, parameter in signature.parameters.items()
        if name != "self"
    }
    declared["return"] = str(signature.return_annotation)
    return declared


def _declared_fields(record: type) -> dict[str, str]:
    return {field.name: str(field.type) for field in fields(record)}


def manual(set_on: datetime) -> AccountSignal:
    """Account 2, whose share count the caller read and found confirmed on a date."""

    return AccountSignal(
        MANUAL_ACCOUNT,
        is_pending_reconciliation=False,
        share_count=ShareCountObservation(confirmed_on=set_on),
    )


def manual_holding_nothing() -> AccountSignal:
    """Account 2, read this cycle, with no share count on it at all.

    The second of the three states, and the one that used to be
    indistinguishable from the third.
    """

    return AccountSignal(
        MANUAL_ACCOUNT,
        is_pending_reconciliation=False,
        share_count=ShareCountObservation(confirmed_on=None),
    )


def manual_unread() -> AccountSignal:
    """Account 2, reported by a caller that did not read its manual side."""

    return AccountSignal(MANUAL_ACCOUNT, is_pending_reconciliation=False)


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------


def test_there_are_exactly_five_kinds_and_publication_overdue_is_not_one() -> None:
    """Acceptance: *publication overdue* must not be added here and called delivered.

    It reports the failure of the very channel it would travel over, so a row
    claiming to deliver it would be a lie.  The phone detects it independently
    (task 22's ``HOST_NOT_PUBLISHING``).

    Task 15 wrote this as *exactly four*.  Task 27 amended it to five rather
    than deleting it, because the half that carries the weight is the second
    assertion and its reason has not changed.  The first exists so that adding a
    kind is a decision someone argues for — as task 27 did, in
    :mod:`networth.model.alert` and in its PR — instead of a one-line diff that
    widens the vocabulary on the way past.
    """

    assert {kind.value for kind in AlertKind} == {
        "NEEDS_REAUTH",
        "REVOKED",
        "FROZEN_DATA",
        "PENDING_RECONCILIATION",
        "SHARE_COUNT_UNCONFIRMED",
    }
    assert not any("PUBLICATION" in kind.value for kind in AlertKind)


def test_which_states_alert_is_read_from_axis_a_rather_than_listed_again() -> None:
    assert AlertKind.for_item_state(ItemState.HEALTHY) is None
    assert AlertKind.for_item_state(ItemState.DEGRADED) is None
    assert AlertKind.for_item_state(ItemState.NEEDS_REAUTH) is AlertKind.NEEDS_REAUTH
    assert AlertKind.for_item_state(ItemState.REVOKED) is AlertKind.REVOKED

    # The two vocabularies are the same set, not two lists that agree today.
    assert {kind.value for kind in AlertKind if kind.is_item_scoped} == {
        state.value for state in ItemState if state.owner_actionable
    }


def test_the_frozen_threshold_is_consumed_from_task_11_not_restated_here() -> None:
    """Acceptance: the frozen-data threshold is read from section 11, not re-declared.

    Checked as a property of the source: this module must not name the constant
    and must not count market days itself, because a second copy of the rule is
    the failure the acceptance criterion is about — not a wrong answer today,
    but two answers tomorrow.
    """

    source = Path(alerts_module.__file__).read_text(encoding="utf-8")
    assert "FROZEN_MARKET_DAYS" not in source
    assert "market_days_without_advance" not in source
    assert "frozen_alert_required" in source
    assert not hasattr(alerts_module, "FROZEN_MARKET_DAYS")


def test_an_alert_is_scoped_to_exactly_one_subject() -> None:
    with pytest.raises(TypeError, match="item_id"):
        AlertDraft(kind=AlertKind.NEEDS_REAUTH, created_at=NOW, message="m")
    with pytest.raises(ValueError, match="cannot also carry"):
        AlertDraft(
            kind=AlertKind.NEEDS_REAUTH,
            created_at=NOW,
            message="m",
            item_id=1,
            account_id=1,
        )
    with pytest.raises(TypeError, match="account_id"):
        AlertDraft(kind=AlertKind.PENDING_RECONCILIATION, created_at=NOW, message="m", item_id=1)


def test_a_frozen_alert_cannot_exist_without_the_clock_it_must_outlive() -> None:
    with pytest.raises(ValueError, match="FROZEN_DATA resolves only when its clock advances"):
        AlertDraft(
            kind=AlertKind.FROZEN_DATA,
            created_at=NOW,
            message="m",
            account_id=1,
        )
    with pytest.raises(ValueError, match="does not resolve on a source clock"):
        AlertDraft(
            kind=AlertKind.REVOKED,
            created_at=NOW,
            message="m",
            item_id=1,
            raised_source_as_of=FROZEN_SINCE,
        )


def test_a_share_count_nudge_cannot_exist_without_the_clock_it_must_outlive() -> None:
    """The second kind that resolves on a clock, held to the same requirement.

    ``carries_source_clock`` covering two kinds is only worth anything if the
    requirement it drives is enforced for both; a rule stated once and checked
    for one member is how the second member ends up exempt.
    """

    with pytest.raises(
        ValueError, match="SHARE_COUNT_UNCONFIRMED resolves only when its clock advances"
    ):
        AlertDraft(
            kind=AlertKind.SHARE_COUNT_UNCONFIRMED,
            created_at=NOW,
            message="m",
            account_id=MANUAL_ACCOUNT,
        )


# --------------------------------------------------------------------------
# Axis A: NEEDS_REAUTH and REVOKED
# --------------------------------------------------------------------------


def test_an_owner_actionable_state_raises_exactly_one_alert_per_state_entry(
    evaluator: AlertEvaluator,
) -> None:
    first = evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])
    second = evaluator.evaluate(at=NOW + timedelta(hours=1), items=[item(ItemState.NEEDS_REAUTH)])

    assert only(first.raised).kind is AlertKind.NEEDS_REAUTH
    assert only(first.raised).item_id == 1
    assert second.raised == ()
    assert second.resolved == ()


def test_a_degraded_item_raises_nothing(evaluator: AlertEvaluator) -> None:
    """Section 11 excludes DEGRADED explicitly: it is a transient failure, and a
    single-channel design cannot afford to spend the owner's attention on it."""

    result = evaluator.evaluate(at=NOW, items=[item(ItemState.DEGRADED)])

    assert result.raised == ()


def test_recovery_resolves_the_alert_the_failure_raised(evaluator: AlertEvaluator) -> None:
    evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])

    recovered = evaluator.evaluate(
        at=NOW + timedelta(hours=2),
        items=[item(ItemState.HEALTHY)],
    )

    assert only(recovered.resolved).kind is AlertKind.NEEDS_REAUTH
    assert only(recovered.resolved).resolved_at == NOW + timedelta(hours=2)


def test_a_transient_failure_does_not_clear_an_owner_actionable_alert(
    evaluator: AlertEvaluator,
) -> None:
    """DEGRADED is not "the transition back to HEALTHY", and the difference matters.

    A connection that needs re-auth and then has a transient failure still needs
    re-auth.  Resolving here would drop a live owner-actionable fault on the way
    past — the same invariant task 10 protects one layer down, where an
    unobserved state is recorded as no transition rather than as a downgrade.
    """

    evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])

    blip = evaluator.evaluate(at=NOW + timedelta(hours=1), items=[item(ItemState.DEGRADED)])

    assert blip.resolved == ()
    assert blip.raised == ()
    assert only_deliverable(evaluator.bulletin(at=NOW + timedelta(hours=1))).alert.kind is (
        AlertKind.NEEDS_REAUTH
    )


def test_a_worse_state_replaces_the_alert_rather_than_stacking_on_it(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])

    revoked = evaluator.evaluate(at=NOW + timedelta(days=1), items=[item(ItemState.REVOKED)])

    assert only(revoked.resolved).kind is AlertKind.NEEDS_REAUTH
    assert only(revoked.raised).kind is AlertKind.REVOKED


def test_the_same_state_entered_again_raises_a_new_alert(evaluator: AlertEvaluator) -> None:
    evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])
    evaluator.evaluate(at=NOW + timedelta(hours=1), items=[item(ItemState.HEALTHY)])

    again = evaluator.evaluate(at=NOW + timedelta(hours=2), items=[item(ItemState.NEEDS_REAUTH)])

    assert only(again.raised).kind is AlertKind.NEEDS_REAUTH


# --------------------------------------------------------------------------
# Axis B: frozen data
# --------------------------------------------------------------------------


def test_frozen_data_raises_and_records_the_clock_it_was_raised_for(
    evaluator: AlertEvaluator,
) -> None:
    result = evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )

    raised = only(result.raised)
    assert raised.kind is AlertKind.FROZEN_DATA
    assert raised.account_id == 1
    assert raised.raised_source_as_of == FROZEN_SINCE


def test_a_frozen_alert_survives_the_item_leaving_healthy(evaluator: AlertEvaluator) -> None:
    """The trap this rule exists for.

    ``FROZEN`` requires a HEALTHY Item, so a connection failure makes the same
    account assess as ``STALE`` — the classification changed, the data did not.
    Resolving here would clear the original failure this product exists to
    catch, at the exact moment the evidence got weaker.
    """

    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )

    degraded = evaluator.evaluate(
        at=NOW + timedelta(days=1),
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=not_frozen())],
    )

    assert degraded.resolved == ()


def test_a_frozen_alert_is_not_resolved_by_a_call_that_merely_succeeded(
    evaluator: AlertEvaluator,
) -> None:
    """Same source clock, any state at all: the data has not advanced."""

    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )

    for state, item_state in (
        (FreshnessState.STALE, ItemState.HEALTHY),
        (FreshnessState.FRESH, ItemState.HEALTHY),
        (FreshnessState.UNKNOWN, None),
    ):
        unchanged = evaluator.evaluate(
            at=NOW + timedelta(days=2),
            accounts=[
                AccountSignal(
                    1,
                    is_pending_reconciliation=False,
                    freshness=(
                        FreshnessAssessment(
                            state=FreshnessState.UNKNOWN,
                            source_as_of=None,
                            market_days_without_advance=0,
                            is_carried_forward=False,
                            item_state=None,
                        )
                        if state is FreshnessState.UNKNOWN
                        else not_frozen(state=state, item_state=item_state)
                    ),
                )
            ],
        )
        assert unchanged.resolved == (), state

    assert len(evaluator.bulletin(at=NOW + timedelta(days=2))) == 1


def test_a_frozen_alert_resolves_when_the_source_clock_finally_advances(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )

    advanced = evaluator.evaluate(
        at=NOW + timedelta(days=3),
        accounts=[
            AccountSignal(
                1,
                is_pending_reconciliation=False,
                freshness=not_frozen(
                    FROZEN_SINCE + timedelta(days=1),
                    state=FreshnessState.FRESH,
                    item_state=ItemState.HEALTHY,
                ),
            )
        ],
    )

    assert only(advanced.resolved).kind is AlertKind.FROZEN_DATA
    # An account that advanced *out* of frozen gets no replacement.  Worth
    # asserting only since the advance became able to raise one: resolving and
    # immediately re-raising would leave the owner an alert he can never clear.
    assert advanced.raised == ()
    assert evaluator.bulletin(at=NOW + timedelta(days=3)) == ()


def test_an_advancing_clock_that_is_still_frozen_never_empties_the_bulletin(
    evaluator: AlertEvaluator,
) -> None:
    """The condition outlives the row that reports it.

    Section 11 resolves a frozen-data alert when ``source_as_of`` advances — and
    that is exactly what a feed running a week late does when it catches up by a
    day.  The clock moved, so the old row's claim is over; the account is still
    five market closes behind, so the condition is not.  Task 11 counts the
    closes *after* ``source_as_of`` rather than the time since the clock last
    changed, which is why this state is reachable at all.

    Resolving without raising the replacement publishes an empty bulletin for an
    account that is still frozen: the failure this product exists to catch, lost
    in the reporting layer instead of in the data.
    """

    first = only(
        evaluator.evaluate(
            at=NOW,
            accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
        ).raised
    )
    later = NOW + timedelta(days=1)
    advanced_clock = FROZEN_SINCE + timedelta(days=1)

    still_frozen = evaluator.evaluate(
        at=later,
        accounts=[
            AccountSignal(1, is_pending_reconciliation=False, freshness=frozen(advanced_clock))
        ],
    )

    assert only(still_frozen.resolved).id == first.id
    replacement = only(still_frozen.raised)
    assert replacement.id != first.id
    assert replacement.raised_source_as_of == advanced_clock
    assert [carried.alert.id for carried in evaluator.bulletin(at=later)] == [replacement.id]


def test_the_replacement_is_anchored_on_the_clock_it_was_raised_for(
    evaluator: AlertEvaluator,
) -> None:
    """The anchor moves with the row, so the next cycle is not a second advance.

    A replacement that kept the *original* clock would see the same advance
    again on every following evaluation and churn a resolve/raise pair each
    time, filling the owner's alert history with rows for one unchanging
    condition.
    """

    advanced_clock = FROZEN_SINCE + timedelta(days=1)
    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )
    replacement = only(
        evaluator.evaluate(
            at=NOW + timedelta(days=1),
            accounts=[
                AccountSignal(1, is_pending_reconciliation=False, freshness=frozen(advanced_clock))
            ],
        ).raised
    )

    unchanged = evaluator.evaluate(
        at=NOW + timedelta(days=2),
        accounts=[
            AccountSignal(1, is_pending_reconciliation=False, freshness=frozen(advanced_clock))
        ],
    )

    assert unchanged.raised == ()
    assert unchanged.resolved == ()
    assert [carried.alert.id for carried in evaluator.bulletin(at=NOW + timedelta(days=2))] == [
        replacement.id
    ]


def test_a_replacement_may_prompt_again_because_the_state_entry_ended(
    evaluator: AlertEvaluator,
) -> None:
    """Asserted deliberately, and raised as a question on task 15's PR.

    Section 11 scopes anti-fatigue to "one alert per item per state entry", and
    the advancing clock is what ends the entry — so the replacement is a new
    entry and may prompt even though the row it replaces was prompted for
    minutes earlier.  A feed that advances partway more than once a day
    therefore prompts more than once a day, which reads against the spirit of
    the same paragraph.

    Whether "state entry" survives an advance that leaves the account frozen is
    a design question about §11 and not one this module may settle quietly, so
    the consequence is pinned here where a reviewer can see it and disagree.
    """

    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )
    evaluator.record_prompted(evaluator.bulletin(at=NOW), at=NOW)
    assert only_deliverable(evaluator.bulletin(at=NOW + timedelta(hours=1))).prompt is False

    evaluator.evaluate(
        at=NOW + timedelta(hours=1),
        accounts=[
            AccountSignal(
                1,
                is_pending_reconciliation=False,
                freshness=frozen(FROZEN_SINCE + timedelta(days=1)),
            )
        ],
    )

    assert only_deliverable(evaluator.bulletin(at=NOW + timedelta(hours=1))).prompt is True


# --------------------------------------------------------------------------
# Pending reconciliation
# --------------------------------------------------------------------------


def test_an_unreconciled_account_alerts_until_it_is_matched(evaluator: AlertEvaluator) -> None:
    pending = evaluator.evaluate(
        at=NOW, accounts=[AccountSignal(1, is_pending_reconciliation=True)]
    )
    still_pending = evaluator.evaluate(
        at=NOW + timedelta(hours=1),
        accounts=[AccountSignal(1, is_pending_reconciliation=True)],
    )
    confirmed = evaluator.evaluate(
        at=NOW + timedelta(hours=2),
        accounts=[AccountSignal(1, is_pending_reconciliation=False)],
    )

    assert only(pending.raised).kind is AlertKind.PENDING_RECONCILIATION
    assert still_pending.raised == ()
    assert only(confirmed.resolved).kind is AlertKind.PENDING_RECONCILIATION


def test_one_account_can_hold_two_different_alerts_at_once(evaluator: AlertEvaluator) -> None:
    result = evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=True, freshness=frozen())],
    )

    assert {alert.kind for alert in result.raised} == {
        AlertKind.FROZEN_DATA,
        AlertKind.PENDING_RECONCILIATION,
    }


# --------------------------------------------------------------------------
# Section 12: re-confirming a manual share count
# --------------------------------------------------------------------------


def test_an_overdue_share_count_is_nudged_and_the_row_records_its_confirmation(
    evaluator: AlertEvaluator,
) -> None:
    """The row remembers *which* confirmation it was raised against.

    Without that, a later evaluation could only ask "is this count old?", and
    would answer yes to a count the owner re-confirmed yesterday for a vest that
    happened last year — resolving nothing and re-raising forever.
    """

    raised = only(evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)]).raised)

    assert raised.kind is AlertKind.SHARE_COUNT_UNCONFIRMED
    assert raised.account_id == MANUAL_ACCOUNT
    assert raised.item_id is None
    assert raised.raised_source_as_of == CONFIRMED_LONG_AGO
    assert [carried.alert.id for carried in evaluator.bulletin(at=NOW)] == [raised.id]


def test_the_period_runs_from_the_confirmation_and_its_boundary_is_inclusive(
    evaluator: AlertEvaluator,
) -> None:
    """One second short is silence; the period itself asks.

    Pinned in both directions because the two failures are asymmetric and both
    are silent: reading the comparison one way nags an owner who confirmed
    inside the period, and the other way lets a count that is exactly due slip
    to the next cycle.
    """

    set_on = NOW - RECONFIRM_SHARE_COUNT_AFTER

    assert evaluator.evaluate(at=NOW - timedelta(seconds=1), accounts=[manual(set_on)]).raised == ()
    assert only(
        evaluator.evaluate(at=NOW, accounts=[manual(set_on)]).raised
    ).raised_source_as_of == (set_on)


def test_an_account_that_holds_no_share_count_is_never_nudged(
    evaluator: AlertEvaluator,
) -> None:
    """Most accounts hold no manual quantity, and they must stay quiet.

    Both silent states are here — the account read and found holding nothing,
    and the account nobody read — because they take different branches and only
    one of them is about this kind at all.  The overdue manual account in the
    same evaluation is the control: without it this test would also pass on an
    evaluator that raises nothing at all.
    """

    read_and_empty = AccountSignal(
        1,
        is_pending_reconciliation=False,
        share_count=ShareCountObservation(confirmed_on=None),
    )

    result = evaluator.evaluate(
        at=NOW,
        accounts=[read_and_empty, manual(CONFIRMED_LONG_AGO)],
    )

    assert [alert.account_id for alert in result.raised] == [MANUAL_ACCOUNT]

    evaluator.evaluate(at=NOW, accounts=[AccountSignal(1, is_pending_reconciliation=False)])

    assert [carried.alert.account_id for carried in evaluator.bulletin(at=NOW)] == [MANUAL_ACCOUNT]


def test_a_standing_nudge_is_not_re_raised_while_the_count_stays_unconfirmed(
    evaluator: AlertEvaluator,
) -> None:
    """The anti-fatigue rule, on the one kind whose condition never lapses.

    An Item recovers on its own; an unconfirmed count only stops being
    unconfirmed when the owner acts.  So this kind sits in the bulletin for as
    long as he ignores it, and every cycle that re-raised it would restart the
    24h prompt window — turning the nudge into the noise section 11 spends its
    single channel avoiding.
    """

    first = only(evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)]).raised)
    later = NOW + timedelta(days=30)

    again = evaluator.evaluate(at=later, accounts=[manual(CONFIRMED_LONG_AGO)])

    assert again.raised == ()
    assert again.resolved == ()
    assert [carried.alert.id for carried in evaluator.bulletin(at=later)] == [first.id]


def test_confirming_the_count_resolves_the_nudge_and_leaves_nothing_behind(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)])
    later = NOW + timedelta(days=1)

    confirmed = evaluator.evaluate(at=later, accounts=[manual(later)])

    assert only(confirmed.resolved).kind is AlertKind.SHARE_COUNT_UNCONFIRMED
    assert confirmed.raised == ()
    assert evaluator.bulletin(at=later) == ()


def test_a_back_dated_confirmation_ends_the_row_but_not_the_condition(
    evaluator: AlertEvaluator,
) -> None:
    """Confirming *as of* an old date is still an advance, and still overdue.

    Section 12 shows a holding as "N shares, set on <date>", so the owner
    supplies the date and can supply one in the past — correcting a count he
    should have entered in the spring is the ordinary case, not an exotic one.
    That advance ends this row's claim while leaving the holding exactly as
    unconfirmed as it was: the same shape as a frozen feed catching up by a day
    while staying five closes behind, and the reason resolving without raising
    the replacement would publish an empty bulletin for an account that still
    needs the owner.

    The third evaluation is the other half: the replacement is anchored on the
    new confirmation, so the same advance is not seen again on every later
    cycle, which would churn a resolve/raise pair per evaluation forever.
    """

    first = only(evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)]).raised)
    later = NOW + timedelta(days=1)
    back_dated = later - RECONFIRM_SHARE_COUNT_AFTER - timedelta(days=30)

    still_unconfirmed = evaluator.evaluate(at=later, accounts=[manual(back_dated)])

    assert only(still_unconfirmed.resolved).id == first.id
    replacement = only(still_unconfirmed.raised)
    assert replacement.id != first.id
    assert replacement.raised_source_as_of == back_dated
    assert [carried.alert.id for carried in evaluator.bulletin(at=later)] == [replacement.id]

    quiet = evaluator.evaluate(at=later + timedelta(days=1), accounts=[manual(back_dated)])

    assert (quiet.raised, quiet.resolved) == ((), ())


def test_a_confirmation_dated_in_the_future_cannot_clear_a_live_nudge(
    evaluator: AlertEvaluator,
) -> None:
    """The branch that loses the alert, which is the half worth pinning.

    A future ``confirmed_on`` is the largest advance there is, so the open row
    resolved — and then the replacement was not raised, because ``at - set_on``
    had gone negative.  The account went quiet with its count still
    unconfirmed, and no later cycle brought it back while the bad clock stood.
    Reproduced at ``1ecc36f``; found in review, not by this suite, because the
    suite only asked whether a bad value was rejected and never asked what it
    did to state that already existed.

    So the assertion that matters is the last one: the alert that was open is
    still open.  ``evaluate`` validates every account before it writes anything,
    and this is what that ordering is for.
    """

    # The boundary, in both directions: an instant that has just happened is a
    # confirmation, and one second later is not yet a fact about the past.
    assert evaluator.evaluate(at=NOW, accounts=[manual(NOW)]).raised == ()
    with pytest.raises(ValueError, match="share_count.confirmed_on is after the instant"):
        evaluator.evaluate(at=NOW, accounts=[manual(NOW + timedelta(seconds=1))])
    assert evaluator.bulletin(at=NOW) == ()

    raised = only(evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)]).raised)
    later = NOW + timedelta(days=1)

    with pytest.raises(ValueError, match="share_count.confirmed_on is after the instant"):
        evaluator.evaluate(at=later, accounts=[manual(later + timedelta(days=364))])

    assert [carried.alert.id for carried in evaluator.bulletin(at=later)] == [raised.id]


def test_one_bad_clock_refuses_the_whole_evaluation_before_anything_is_written(
    evaluator: AlertEvaluator,
) -> None:
    """Validation is a pass over every account, not a step inside each one.

    A cycle evaluates every account in one call, so "refused before anything is
    written" is a claim about the evaluation and not about the account that
    carries the bad clock.  Checking as each account came up would leave the
    accounts ahead of it already committed and the ones behind it unevaluated —
    a partial cycle that no caller asked for and no return value describes,
    since the exception carries neither list.

    The sound account is deliberately first, and deliberately one that would
    write: it raises a reconciliation alert the moment it is evaluated.
    """

    would_raise = AccountSignal(1, is_pending_reconciliation=True)

    with pytest.raises(ValueError, match="share_count.confirmed_on is after the instant"):
        evaluator.evaluate(at=NOW, accounts=[would_raise, manual(NOW + timedelta(days=1))])

    assert evaluator.bulletin(at=NOW) == ()


def test_a_source_clock_in_the_future_cannot_clear_a_live_frozen_alert(
    evaluator: AlertEvaluator,
) -> None:
    """The same defect on the kind task 15 shipped, so the fix is the rule.

    Probed after the finding above and reproduced on merged code: an assessment
    whose ``source_as_of`` is in the future resolves a standing frozen alert and
    raises nothing, leaving an account that is still frozen with an empty
    bulletin.  Nothing produces such an assessment today — task 11 derives the
    clock from stored observations — which is exactly why it is worth a guard
    rather than an argument: the module that will assemble these signals has not
    been written yet (`16`), and this module cannot see where its inputs came
    from.

    Refusing both clocks in one place makes this a property of resolving on a
    clock.  Fixing only the kind the review named would have left the same
    defect standing in the module that the fix was made in.
    """

    raised = only(
        evaluator.evaluate(
            at=NOW,
            accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
        ).raised
    )
    later = NOW + timedelta(days=1)
    ahead = not_frozen(
        later + timedelta(days=364),
        state=FreshnessState.FRESH,
        item_state=ItemState.HEALTHY,
    )

    with pytest.raises(ValueError, match="freshness.source_as_of is after the instant"):
        evaluator.evaluate(
            at=later,
            accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=ahead)],
        )

    assert [carried.alert.id for carried in evaluator.bulletin(at=later)] == [raised.id]


def test_an_account_reported_without_its_share_count_keeps_the_nudge(
    evaluator: AlertEvaluator,
) -> None:
    """ "I did not read the holding" is not "he confirmed it"."""

    evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)])
    later = NOW + timedelta(days=1)

    unread = evaluator.evaluate(at=later, accounts=[manual_unread()])

    assert unread.resolved == ()
    assert len(evaluator.bulletin(at=later)) == 1


def test_a_holding_that_is_gone_resolves_the_nudge_it_left_behind(
    evaluator: AlertEvaluator,
) -> None:
    """The other absence, and it means the opposite thing.

    A share count can stop existing: the holding is deleted, or the manual asset
    becomes one that has no quantity to confirm.  When that happens the subject
    of the nudge is gone, and a row asking the owner to re-confirm a number he
    no longer has is one he cannot act on — it would sit in the bulletin for
    good, since the only thing that resolves this kind is a clock that can never
    advance again.

    Pinned beside the test above on purpose: the two absences are one keystroke
    apart at the call site and take opposite branches, which is exactly why they
    stopped sharing a value.
    """

    raised = only(evaluator.evaluate(at=NOW, accounts=[manual(CONFIRMED_LONG_AGO)]).raised)
    later = NOW + timedelta(days=1)

    removed = evaluator.evaluate(at=later, accounts=[manual_holding_nothing()])

    assert only(removed.resolved).id == raised.id
    assert removed.raised == ()
    assert evaluator.bulletin(at=later) == ()


def test_an_account_read_and_found_empty_raises_nothing_to_resolve(
    evaluator: AlertEvaluator,
) -> None:
    """Resolving an absent holding must not become a way to raise one.

    The branch above resolves; this asserts it does nothing else. Without it a
    resolve-then-fall-through edit — the shape the back-dated case legitimately
    uses two branches down — would raise a nudge for an account that has no
    share count, and the test above would still pass.
    """

    result = evaluator.evaluate(at=NOW, accounts=[manual_holding_nothing()])

    assert (result.raised, result.resolved) == ((), ())
    assert evaluator.bulletin(at=NOW) == ()


def test_the_whole_account_side_input_surface_is_declared_here() -> None:
    """Task 27's "must not": never silently change a share count.

    The behaviour is unobservable — there is no assertion that catches a
    quantity this module was never handed — so what gets checked is that it is
    not handed one.  The first attempt checked that instead by scanning imports,
    and codex's review showed that guard was false twice over: the scan recorded
    only ``ImportFrom.module``, so the ordinary ``from networth.model import
    manual`` passed it; and a new ``evaluate(..., share_counts=...)`` parameter
    would have made quantities reachable without any manual import at all.  A
    guard that cannot fail is worse than no guard, because it is cited.

    This is the replacement, and it pins the thing the guarantee is actually
    about: **every route by which an account fact enters this module**.  There
    are exactly three — the repository handed to the constructor, the parameters
    of :meth:`AlertEvaluator.evaluate`, and the fields of the two records those
    parameters carry — and each is listed with its annotation.  Widening any of
    them, by any spelling, turns this red.

    **What it does not prove**, stated so it is not cited for more than it is:
    it does not stop this module from importing something and reading it
    directly, which is why the constructor's one dependency is pinned above and
    why the import test below exists as a second, narrower check.  Nor does it
    know a quantity from an id by type — both are ``int`` — which is why this is
    an allow-list of names rather than a hunt for suspicious ones.  Its whole
    force is that admitting a new input is a decision someone argues for, in the
    way that the exactly-five-kinds test above makes adding a kind a decision.
    """

    assert _declared(AlertEvaluator.__init__) == {
        "alerts": "AlertRepository",
        "return": "None",
    }
    assert _declared(AlertEvaluator.evaluate) == {
        "at": "datetime",
        "items": "Iterable[ItemHealth]",
        "accounts": "Iterable[AccountSignal]",
        "return": "AlertEvaluation",
    }
    assert _declared_fields(AccountSignal) == {
        "account_id": "int",
        "is_pending_reconciliation": "bool",
        "freshness": "FreshnessAssessment | None",
        "share_count": "ShareCountObservation | None",
    }
    assert _declared_fields(ShareCountObservation) == {"confirmed_on": "datetime | None"}


def test_the_alert_module_borrows_no_manual_vocabulary() -> None:
    """The second route in, closed the same way: by list rather than by hunch.

    Every domain name this module imports is written out, so reaching for the
    manual model — or for anything else that could carry a quantity — is a red
    test rather than an import that reads as housekeeping.  Listing what is
    allowed instead of what is forbidden is deliberate: the first version of
    this check tried to exclude ``manual`` by name and let both
    ``from networth.model import manual`` and a re-export of an ``EquityHolding``
    through :mod:`networth.model` straight past.  Standard-library imports are
    not listed, because they are not what this is about and pinning them would
    make an unrelated edit look like a boundary violation.
    """

    assert alerts_module.__file__ is not None
    tree = ast.parse(Path(alerts_module.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            # Both halves: the module, and each name taken out of it — the
            # missing second half is what let the plain `from networth.model
            # import manual` through.
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)

    assert imported, "the import scan found nothing, so it proves nothing"
    assert {name for name in imported if name.split(".")[0] == "networth"} == {
        "networth.model",
        "networth.model.Alert",
        "networth.model.AlertDraft",
        "networth.model.AlertKind",
        "networth.model.FreshnessAssessment",
        "networth.model.ItemHealth",
        "networth.model.ItemState",
        "networth.model.figure",
        "networth.model.figure.require_utc",
        "networth.store",
        "networth.store.AlertRepository",
    }


# --------------------------------------------------------------------------
# Absence of evidence
# --------------------------------------------------------------------------


def test_a_subject_the_caller_did_not_mention_keeps_its_alerts(
    evaluator: AlertEvaluator,
) -> None:
    """ "I did not look" and "the condition is over" are different facts."""

    evaluator.evaluate(
        at=NOW,
        items=[item(ItemState.REVOKED)],
        accounts=[AccountSignal(1, is_pending_reconciliation=True, freshness=frozen())],
    )

    silent = evaluator.evaluate(at=NOW + timedelta(days=1))

    assert silent.resolved == ()
    assert len(evaluator.bulletin(at=NOW + timedelta(days=1))) == 3


def test_an_account_reported_without_an_assessment_keeps_its_frozen_alert(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(
        at=NOW,
        accounts=[AccountSignal(1, is_pending_reconciliation=False, freshness=frozen())],
    )

    unassessed = evaluator.evaluate(
        at=NOW + timedelta(days=1),
        accounts=[AccountSignal(1, is_pending_reconciliation=False)],
    )

    assert unassessed.resolved == ()


# --------------------------------------------------------------------------
# Written before it travels, and the anti-fatigue window
# --------------------------------------------------------------------------


def test_the_alert_is_durable_before_anything_could_have_carried_it(
    db: sqlite3.Connection,
    evaluator: AlertEvaluator,
) -> None:
    """Acceptance: state is written before it travels.

    Read back through a second repository on the same database, because the
    guarantee is about what survives the process, not about what this object
    remembers.
    """

    evaluator.evaluate(at=NOW, items=[item(ItemState.REVOKED)])

    reread = AlertRepository(db).open()

    assert only(reread).kind is AlertKind.REVOKED
    assert only(reread).notified_at is None


def test_a_crash_between_publishing_and_stamping_re_raises_rather_than_losing_it(
    evaluator: AlertEvaluator,
) -> None:
    """The bulletin is built, the process dies, nothing is stamped.

    The next cycle must offer the same alert to prompt again.  If the stamp were
    written first, this crash would consume the owner's only notification of a
    revoked connection and he would never learn of it.
    """

    evaluator.evaluate(at=NOW, items=[item(ItemState.REVOKED)])
    first = evaluator.bulletin(at=NOW)
    assert only_deliverable(first).prompt is True
    # ...crash here: no record_prompted call.

    after_restart = evaluator.bulletin(at=NOW + timedelta(minutes=5))

    assert only_deliverable(after_restart).prompt is True


def test_an_alert_prompts_once_and_then_not_again_inside_the_window(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(at=NOW, items=[item(ItemState.REVOKED)])
    bulletin = evaluator.bulletin(at=NOW)
    evaluator.record_prompted(bulletin, at=NOW)

    inside = evaluator.bulletin(at=NOW + REPROMPT_AFTER - timedelta(seconds=1))
    at_the_boundary = evaluator.bulletin(at=NOW + REPROMPT_AFTER)

    assert only_deliverable(inside).prompt is False
    assert only_deliverable(at_the_boundary).prompt is True


def test_travelling_quietly_does_not_restart_the_window(
    evaluator: AlertEvaluator,
) -> None:
    """A daily publish must not silently become a daily alert.

    Every open alert travels on every publish; only the ones that may prompt are
    stamped.  Stamping on delivery instead would reset the window on each
    publish, and with a publish interval of 24 hours the two would be
    indistinguishable — until the interval changed.
    """

    evaluator.evaluate(at=NOW, items=[item(ItemState.REVOKED)])
    evaluator.record_prompted(evaluator.bulletin(at=NOW), at=NOW)

    quiet = evaluator.bulletin(at=NOW + timedelta(hours=6))
    stamped = evaluator.record_prompted(quiet, at=NOW + timedelta(hours=6))

    assert stamped == ()
    assert only_deliverable(evaluator.bulletin(at=NOW + timedelta(hours=7))).prompt is False
    assert only_deliverable(evaluator.bulletin(at=NOW + REPROMPT_AFTER)).prompt is True


def test_every_open_alert_travels_even_when_it_may_not_prompt(
    evaluator: AlertEvaluator,
) -> None:
    evaluator.evaluate(
        at=NOW,
        items=[item(ItemState.REVOKED)],
        accounts=[AccountSignal(1, is_pending_reconciliation=True)],
    )
    evaluator.record_prompted(evaluator.bulletin(at=NOW), at=NOW)

    later = evaluator.bulletin(at=NOW + timedelta(hours=1))

    assert len(later) == 2
    assert [deliverable.prompt for deliverable in later] == [False, False]


def test_a_resolved_alert_stops_travelling(evaluator: AlertEvaluator) -> None:
    evaluator.evaluate(at=NOW, items=[item(ItemState.NEEDS_REAUTH)])
    evaluator.evaluate(at=NOW + timedelta(hours=1), items=[item(ItemState.HEALTHY)])

    assert evaluator.bulletin(at=NOW + timedelta(hours=1)) == ()


# --------------------------------------------------------------------------
# Contract enforcement
# --------------------------------------------------------------------------


def test_the_evaluator_refuses_inputs_it_cannot_trust(
    evaluator: AlertEvaluator,
    repository: AlertRepository,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        evaluator.evaluate(at=NOW.replace(tzinfo=None))
    with pytest.raises(TypeError, match="ItemHealth"):
        evaluator.evaluate(at=NOW, items=["NEEDS_REAUTH"])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="AccountSignal"):
        evaluator.evaluate(at=NOW, accounts=[1])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="DeliverableAlert"):
        evaluator.record_prompted([1], at=NOW)  # type: ignore[list-item]
    with pytest.raises(TypeError, match="AlertRepository"):
        AlertEvaluator(repository.open)  # type: ignore[arg-type]


def test_an_account_signal_states_a_fact_about_exactly_one_account() -> None:
    with pytest.raises(TypeError):
        AccountSignal("1", is_pending_reconciliation=False)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="positive"):
        AccountSignal(0, is_pending_reconciliation=False)
    with pytest.raises(TypeError, match="bool"):
        AccountSignal(1, is_pending_reconciliation=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="FreshnessAssessment"):
        AccountSignal(1, is_pending_reconciliation=False, freshness="FROZEN")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="ShareCountObservation"):
        AccountSignal(1, is_pending_reconciliation=False, share_count=NOW)  # type: ignore[arg-type]
    # The nudge's whole answer is a subtraction against the evaluation instant,
    # so a naive clock would be a silently wrong period rather than a crash.
    with pytest.raises(ValueError, match="confirmed_on must be timezone-aware UTC"):
        ShareCountObservation(confirmed_on=datetime(2026, 3, 10, 15, 0))


def only_deliverable(bulletin: tuple[DeliverableAlert, ...]) -> DeliverableAlert:
    assert len(bulletin) == 1, bulletin
    return bulletin[0]
