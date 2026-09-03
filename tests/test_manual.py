"""Task 13: a revision applies from its own date forward, and history holds still."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from networth.manual import (
    NotARevisionError,
    PropertyRevisionLog,
    Revision,
    revision_draft,
)
from networth.model import (
    MANUAL_VALUED_AS_OF,
    QUOTE_AS_OF,
    EquityHolding,
    ObservationSource,
    PropertyValuation,
    Quote,
    SnapshotAge,
    SnapshotAgeState,
    SnapshotCounts,
    SnapshotDraft,
    SourcedFigure,
    normalize_symbol,
    parse_share_count,
    to_minor_units,
)
from networth.storage import migrate
from networth.store import Store

BOUGHT = datetime(2023, 6, 1, tzinfo=UTC)
DURING_2024 = (
    datetime(2024, 3, 31, tzinfo=UTC),
    datetime(2024, 9, 30, tzinfo=UTC),
)
REVALUED = datetime(2026, 2, 1, tzinfo=UTC)

# Synthetic figures only (AGENTS.md rule 1): a round purchase price and a round
# revaluation, chosen so the arithmetic in every assertion below is obvious.
PURCHASE_MINOR = 40_000_000
REVALUED_MINOR = 55_000_000


def _db_time(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def store(db: sqlite3.Connection) -> Store:
    return Store(db)


def add_property_account(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        INSERT INTO account(
            name, type, currency, sign, freshness_policy,
            include_in_net_worth, reconciliation_state, created_at
        ) VALUES ('synthetic property', 'synthetic', 'USD', 1, 'MANUAL_STATIC', 1,
                  'CONFIRMED', ?)
        """,
        (_db_time(BOUGHT),),
    )
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def add_sync_run(connection: sqlite3.Connection, run_id: str, at: datetime) -> None:
    connection.execute(
        """
        INSERT INTO sync_run(id, started_at, finished_at, "trigger", ok)
        VALUES (?, ?, ?, 'TEST', 1)
        """,
        (run_id, _db_time(at - timedelta(minutes=1)), _db_time(at)),
    )


def valuation(value_minor: int, valued_as_of: datetime) -> PropertyValuation:
    return PropertyValuation(
        value_minor=value_minor,
        currency="USD",
        valued_as_of=valued_as_of,
    )


def snapshot_draft(run_id: str, taken_at: datetime, total_minor: int) -> SnapshotDraft:
    """A one-account curve point: the property is the whole of this net worth."""

    age = SnapshotAge(
        state=SnapshotAgeState.STATIC_ONLY,
        as_of=None,
        oldest_known_source_as_of=None,
    )
    return SnapshotDraft(
        sync_run_id=run_id,
        taken_at=taken_at,
        net_worth=age.figure(total_minor),
        assets=age.figure(total_minor),
        liabilities=age.figure(0),
        counts=SnapshotCounts(
            account_count=1,
            stale_account_count=0,
            unknown_freshness_account_count=0,
            static_account_count=1,
            reauth_account_count=0,
            unreconciled_account_count=0,
        ),
        is_complete=True,
        age=age,
    )


def test_the_value_for_a_day_is_the_latest_revision_as_of_that_day() -> None:
    log = PropertyRevisionLog(
        [
            Revision(valuation(PURCHASE_MINOR, BOUGHT), entered_at=BOUGHT, sequence=1),
            Revision(valuation(REVALUED_MINOR, REVALUED), entered_at=REVALUED, sequence=2),
        ]
    )

    for day in DURING_2024:
        in_force = log.as_of(day)
        assert in_force is not None
        assert in_force.valuation.value_minor == PURCHASE_MINOR

    after = log.as_of(REVALUED + timedelta(days=1))
    assert after is not None
    assert after.valuation.value_minor == REVALUED_MINOR


def test_a_day_before_the_first_revision_has_no_value_rather_than_the_earliest() -> None:
    log = PropertyRevisionLog(
        [Revision(valuation(PURCHASE_MINOR, BOUGHT), entered_at=BOUGHT, sequence=1)]
    )

    assert log.as_of(BOUGHT - timedelta(days=1)) is None
    assert log.as_of(BOUGHT) is not None


def test_a_revision_entered_late_applies_from_the_date_it_carries() -> None:
    """An appraisal dated June, typed in September, is in force in July."""

    dated_june = datetime(2025, 6, 1, tzinfo=UTC)
    typed_september = datetime(2025, 9, 1, tzinfo=UTC)
    log = PropertyRevisionLog(
        [
            Revision(valuation(PURCHASE_MINOR, BOUGHT), entered_at=BOUGHT, sequence=1),
            Revision(
                valuation(REVALUED_MINOR, dated_june),
                entered_at=typed_september,
                sequence=2,
            ),
        ]
    )

    in_force = log.as_of(datetime(2025, 7, 1, tzinfo=UTC))

    assert in_force is not None
    assert in_force.valuation.value_minor == REVALUED_MINOR


def test_a_backfilled_older_appraisal_does_not_override_a_newer_one() -> None:
    """The two clocks disagree here, which is the only way to prove which one orders.

    Entering an older appraisal *later* is ordinary — the owner finds a document
    he had not recorded. Ordering the log by when he typed it in would let that
    March number take over from a July revaluation that is genuinely newer.
    """

    july = Revision(
        valuation(REVALUED_MINOR, datetime(2025, 7, 1, tzinfo=UTC)),
        entered_at=datetime(2025, 7, 1, tzinfo=UTC),
        sequence=1,
    )
    march_entered_in_september = Revision(
        valuation(PURCHASE_MINOR, datetime(2025, 3, 1, tzinfo=UTC)),
        entered_at=datetime(2025, 9, 1, tzinfo=UTC),
        sequence=2,
    )
    log = PropertyRevisionLog([july, march_entered_in_september])

    in_august = log.as_of(datetime(2025, 8, 1, tzinfo=UTC))
    in_may = log.as_of(datetime(2025, 5, 1, tzinfo=UTC))

    assert in_august is not None
    assert in_august.valuation.value_minor == REVALUED_MINOR
    assert in_may is not None
    assert in_may.valuation.value_minor == PURCHASE_MINOR


def test_a_future_dated_revision_is_stored_but_not_yet_in_force() -> None:
    """`current` is `as_of(now)`, not the newest row."""

    future = REVALUED + timedelta(days=30)
    log = PropertyRevisionLog(
        [
            Revision(valuation(PURCHASE_MINOR, BOUGHT), entered_at=BOUGHT, sequence=1),
            Revision(valuation(REVALUED_MINOR, future), entered_at=REVALUED, sequence=2),
        ]
    )

    assert len(log.entries) == 2
    in_force = log.current(now=REVALUED)
    assert in_force is not None
    assert in_force.valuation.value_minor == PURCHASE_MINOR


def test_a_correction_re_entered_for_the_same_date_wins() -> None:
    corrected = datetime(2025, 6, 1, tzinfo=UTC)
    log = PropertyRevisionLog(
        [
            Revision(
                valuation(PURCHASE_MINOR, corrected),
                entered_at=corrected,
                sequence=1,
            ),
            Revision(
                valuation(REVALUED_MINOR, corrected),
                entered_at=corrected + timedelta(days=1),
                sequence=2,
            ),
        ]
    )

    in_force = log.as_of(corrected)

    assert in_force is not None
    assert in_force.valuation.value_minor == REVALUED_MINOR


def test_revaluing_in_2026_leaves_the_2024_points_on_the_curve_unchanged(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    """The acceptance test named in the task board.

    The curve is built the way section 12 says it is built — from *stored*
    snapshots — so this also demonstrates why "recompute from observations at
    render time" is the implementation that would fail it.
    """

    account_id = add_property_account(db)

    add_sync_run(db, "run-purchase", BOUGHT)
    store.observations.append(
        revision_draft(
            sync_run_id="run-purchase",
            account_id=account_id,
            valuation=valuation(PURCHASE_MINOR, BOUGHT),
            observed_at=BOUGHT,
        )
    )

    for index, taken_at in enumerate(DURING_2024):
        run_id = f"run-2024-{index}"
        add_sync_run(db, run_id, taken_at)
        log = PropertyRevisionLog.from_observations(
            store.observations.history_for_lineage(account_id)
        )
        in_force = log.current(now=taken_at)
        assert in_force is not None
        store.snapshots.append(snapshot_draft(run_id, taken_at, in_force.valuation.value_minor))

    curve_before = store.snapshots.history()
    assert [point.net_worth.value_minor for point in curve_before] == [
        PURCHASE_MINOR,
        PURCHASE_MINOR,
    ]

    # 2026: the owner revalues the property. Appending a revision must not be an
    # UPDATE anywhere, and must not move a single point already on the curve.
    add_sync_run(db, "run-revalue", REVALUED)
    store.observations.append(
        revision_draft(
            sync_run_id="run-revalue",
            account_id=account_id,
            valuation=valuation(REVALUED_MINOR, REVALUED),
            observed_at=REVALUED,
        )
    )

    assert store.snapshots.history() == curve_before

    log = PropertyRevisionLog.from_observations(store.observations.history_for_lineage(account_id))
    for taken_at in DURING_2024:
        historic = log.as_of(taken_at)
        assert historic is not None
        assert historic.valuation.value_minor == PURCHASE_MINOR
    today = log.current(now=REVALUED)
    assert today is not None
    assert today.valuation.value_minor == REVALUED_MINOR


def test_a_revision_is_an_append_not_an_update(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_property_account(db)
    for index, (value_minor, at) in enumerate(
        ((PURCHASE_MINOR, BOUGHT), (REVALUED_MINOR, REVALUED))
    ):
        run_id = f"run-{index}"
        add_sync_run(db, run_id, at)
        store.observations.append(
            revision_draft(
                sync_run_id=run_id,
                account_id=account_id,
                valuation=valuation(value_minor, at),
                observed_at=at,
            )
        )

    stored = store.observations.history_for_lineage(account_id)

    assert len(stored) == 2
    assert [row.figure.value_minor for row in stored] == [PURCHASE_MINOR, REVALUED_MINOR]
    assert all(row.source is ObservationSource.MANUAL for row in stored)
    assert all(row.figure.source_clock == MANUAL_VALUED_AS_OF for row in stored)
    assert [row.figure.as_of for row in stored] == [BOUGHT, REVALUED]


def test_the_log_refuses_history_that_is_not_a_property_revision(
    db: sqlite3.Connection,
    store: Store,
) -> None:
    account_id = add_property_account(db)
    add_sync_run(db, "run-quote", REVALUED)
    store.observations.append(
        revision_draft(
            sync_run_id="run-quote",
            account_id=account_id,
            valuation=valuation(PURCHASE_MINOR, BOUGHT),
            observed_at=BOUGHT,
        )
    )
    foreign = store.observations.history_for_lineage(account_id)[0]
    mislabelled = type(foreign)(
        id=foreign.id,
        sync_run_id=foreign.sync_run_id,
        account_id=foreign.account_id,
        observed_at=foreign.observed_at,
        figure=SourcedFigure(foreign.figure.value_minor, "USD", foreign.figure.as_of, QUOTE_AS_OF),
        source=ObservationSource.QUOTE,
        fetched_at=foreign.fetched_at,
        is_carried_forward=False,
    )

    with pytest.raises(NotARevisionError, match="not a property revision"):
        PropertyRevisionLog.from_observations([mislabelled])


def test_a_holding_is_priced_only_by_a_quote_and_dated_only_by_its_as_of() -> None:
    quote_as_of = datetime(2026, 2, 2, 21, 0, tzinfo=UTC)
    holding = EquityHolding(
        symbol=normalize_symbol("synth"),
        shares=parse_share_count("12.5"),
        currency="USD",
        set_on=datetime(2026, 1, 1, tzinfo=UTC),
    )
    quote = Quote(
        symbol=normalize_symbol("Synth"),
        price=Decimal("10.04"),
        currency="USD",
        as_of=quote_as_of,
    )

    figure = holding.value_with(quote)

    assert figure.value_minor == to_minor_units(Decimal("125.50"), currency="USD")
    assert figure.as_of == quote_as_of
    assert figure.source_clock == QUOTE_AS_OF


def test_a_quote_for_another_symbol_or_currency_is_refused_not_computed() -> None:
    holding = EquityHolding(
        symbol=normalize_symbol("synth"),
        shares=parse_share_count("1"),
        currency="USD",
        set_on=datetime(2026, 1, 1, tzinfo=UTC),
    )
    as_of = datetime(2026, 2, 2, 21, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="not for"):
        holding.value_with(Quote(symbol="OTHER", price=Decimal("1"), currency="USD", as_of=as_of))
    with pytest.raises(ValueError, match="priced in"):
        holding.value_with(Quote(symbol="SYNTH", price=Decimal("1"), currency="EUR", as_of=as_of))


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "-1", "not a number"])
def test_a_share_count_that_is_not_a_finite_quantity_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_share_count(raw)


def test_minor_units_round_half_to_even_and_refuse_an_unknown_currency() -> None:
    assert to_minor_units(Decimal("1.005"), currency="USD") == 100
    assert to_minor_units(Decimal("1.015"), currency="USD") == 102
    with pytest.raises(ValueError, match="no minor-unit scale"):
        to_minor_units(Decimal("1"), currency="XXX")
