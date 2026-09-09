"""Task 15: the four alerts, and the rules that keep a single channel credible."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import networth.alerts as alerts_module
from networth.alerts import (
    REPROMPT_AFTER,
    AccountSignal,
    AlertEvaluator,
    DeliverableAlert,
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


# --------------------------------------------------------------------------
# The vocabulary
# --------------------------------------------------------------------------


def test_there_are_exactly_four_kinds_and_publication_overdue_is_not_one() -> None:
    """Acceptance: *publication overdue* must not be added here and called delivered.

    It reports the failure of the very channel it would travel over, so a row
    claiming to deliver it would be a lie.  The phone detects it independently
    (task 22's ``HOST_NOT_PUBLISHING``).
    """

    assert {kind.value for kind in AlertKind} == {
        "NEEDS_REAUTH",
        "REVOKED",
        "FROZEN_DATA",
        "PENDING_RECONCILIATION",
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
    with pytest.raises(ValueError, match="source_as_of advances"):
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


def only_deliverable(bulletin: tuple[DeliverableAlert, ...]) -> DeliverableAlert:
    assert len(bulletin) == 1, bulletin
    return bulletin[0]
