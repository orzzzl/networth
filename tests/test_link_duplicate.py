"""`07b` criterion 8: the old VPS comes back after the recovery already exchanged.

    **The uncertain case is tested: the old VPS comes back after recovery
    exchanged.** Assert the outcome is classified honestly and that ``TokenStore``
    never silently prefers one credential over another. **The Item count asserted
    must follow the identity the responses returned, not a fixed number.** [...]
    Cover all three shapes with synthetic responses — **same Item** (one entry, one
    slot), **distinct Items** (both retained, two slots), **unknown identity**
    (explicit unresolved, nothing discarded) — which needs no live exchange and no
    new owner action.

The sentence that shaped this file is *"not a fixed number"*. An earlier revision of
the criterion required the budget to count **one** Item and not two; `06a` (ii)
measured a duplicate exchange as **ACCEPTED** without establishing the Item it
returned, so a fixed expectation of one *"would make a genuinely consumed second
slot fail the test that was supposed to catch it"*. So every count below is read
against the identities its own fixture returned, and the one shape where the number
is not knowable asserts that nothing produces a number at all.

**The three shapes are built from identities rather than from a wire.** That is what
*"synthetic responses"* buys and it is the whole reason
:func:`~networth.link_duplicate.classify_duplicate` takes no store and no database:
the only thing a live duplicate exchange would add here is the one fact `06a` (ii)
could not measure — whether Plaid charges a second Item — and that is the fact these
three shapes exist to *represent* rather than to discover.

**Each shape is asserted at two altitudes**, because the interesting claims are
about how they differ: the verdict the branch reaches, and what
:func:`~networth.item_budget.read_item_budget` — the module that owns the arithmetic
— says about the same database. Where those two disagree the disagreement is the
finding and is asserted as one.
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from networth.backup import crypto
from networth.item_budget import ItemBudgetError, SlotEvidence, read_item_budget
from networth.link_duplicate import (
    ATTEMPTED_EXCHANGE_STATES,
    DuplicateEvidence,
    DuplicateOutcome,
    DuplicateSource,
    classify_duplicate,
    gather_duplicate_evidence,
)
from networth.link_fence import ATTESTATION, FENCED_INSTANCE, FenceAttestation
from networth.link_pairing import PairingResult
from networth.link_sink import (
    DuplicateExchange,
    EmergencyArtifactSink,
    RecoveredItem,
    SinkNotWritable,
    restore,
)
from networth.storage import migrate
from networth.tokenstore import Secret, SecretKind, SecretRefExists, TokenStore, secret_ref_for

NOW = datetime(2026, 9, 30, 9, 30, tzinfo=UTC)
STAMP = "2026-09-30T09:00:00Z"

#: Generated at run time for the reason `test_link_sink.py` gives: a literal of this
#: shape is refused by `check-no-secrets.sh` in a public repo, and a scanner that let
#: fixtures through would have to tell synthetic from real.
RECOVERED_TOKEN = "access-sandbox-" + secrets.token_hex(16)
#: The *other* host's credential. A second exchange returns a second `access_token`
#: even when it returns the same Item — `06a` (ii) measured the first one staying
#: HEALTHY — so these must never be equal, or "both credentials survived" would be
#: satisfied by one.
OTHER_TOKEN = "access-sandbox-" + secrets.token_hex(16)

#: The Item this recovery's exchange returned, and the one the other host's did.
RECOVERED_ITEM = "item-recovered-00000"
OTHER_ITEM = "item-other-000000000"

FLOW_ID = "0a1b2c3d4e5f60718293a4b5c6d7e8f9"
RESULT_ID = "5f60718293a4b5c6d7e8f90a1b2c3d4e"
FENCE = FenceAttestation(
    instance=FENCED_INSTANCE,
    statement=ATTESTATION,
    confirmed_at=datetime(2026, 9, 30, 9, 25, tzinfo=UTC),
)


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "backup.key"
    path.write_text(secrets.token_hex(crypto.KEY_BYTES) + "\n")
    path.chmod(0o600)
    return path


def sealed(tmp_path: Path, key_file: Path) -> Path:
    """The Mac-side artifact this recovery wrote, for :data:`RECOVERED_ITEM`."""
    artifact = tmp_path / "artifact.sealed"
    sink = EmergencyArtifactSink(artifact, key_file=key_file)
    sink.prepare()
    sink.commit(
        RecoveredItem(
            access_token=Secret(RECOVERED_TOKEN),
            item_id=RECOVERED_ITEM,
            flow_id=FLOW_ID,
            link_session_id="session-0001",
            request_id="request-0001",
            fence=FENCE,
        ),
        now=NOW,
    )
    return artifact


def database(tmp_path: Path, *, state: str, item_id: str | None, stored: str | None = None) -> Any:
    """The recovered VPS's database, in the state one of the three shapes leaves.

    ``state``/``item_id`` describe the Link success row the *other* host wrote;
    ``stored`` is the Item it also committed an ``item`` row for, if it got that far.
    Separate arguments because the pair *"row says EXCHANGED, no item row"* is a real
    shape and is exactly the one whose count stops being knowable.
    """
    connection = sqlite3.connect(tmp_path / "recovered.sqlite")
    migrate(connection)
    connection.execute(
        "INSERT INTO institution(plaid_institution_id, name, is_oauth) "
        "VALUES ('synthetic-institution', 'Synthetic institution', 0)"
    )
    connection.execute(
        "INSERT INTO link_request(flow_id, minted_at, hosted_url_expires_at, state) "
        "VALUES (?, ?, ?, 'URL_MINTED')",
        (FLOW_ID, STAMP, STAMP),
    )
    connection.execute(
        "INSERT INTO link_result(result_id, flow_id, token_digest, state, item_id) "
        "VALUES (?, ?, 'synthetic-digest', ?, ?)",
        (RESULT_ID, FLOW_ID, state, item_id),
    )
    if stored is not None:
        connection.execute(
            "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
            "status_since, created_at) VALUES (1, ?, ?, 'DEGRADED', ?, ?)",
            (stored, f"access_token.{stored}", STAMP, STAMP),
        )
    connection.commit()
    return connection


def held_by_the_other_host(directory: Path, *, item_id: str | None) -> TokenStore:
    """The credential the old VPS's own exchange left in this host's ``TokenStore``.

    Under ``access_token.<flow_id>``, which is the name
    :meth:`~networth.link_sink.ReplacementHostSink.commit` writes and therefore the
    one a restore collides with. ``item_id=None`` is the record whose identity was
    lost — the third shape.
    """
    store = TokenStore(directory)
    store.put(SecretKind.ACCESS_TOKEN, FLOW_ID, OTHER_TOKEN, item_id=item_id)
    return store


def both_credentials_survive(directory: Path, artifact: Path, key_file: Path) -> None:
    """Neither copy was moved, overwritten or emptied. Asserted, never assumed.

    The store's material is compared by *value*: a refusal that had written first and
    failed second would leave a file that still exists and still parses, so
    "the record is there" is not the assertion criterion 8 asks for.
    """
    from networth.link_sink import read_artifact

    reference = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)
    assert TokenStore(directory).get(reference).reveal() == OTHER_TOKEN
    payload = read_artifact(artifact, key_bytes=crypto.load_backup_key(key_file))
    assert payload["access_token"] == RECOVERED_TOKEN


class TestSameItem:
    """Shape 1: *"same Item — one entry, one slot"*."""

    def test_the_collision_is_refused_and_says_the_items_were_compared(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        held_by_the_other_host(tokens, item_id=RECOVERED_ITEM)
        connection = database(
            tmp_path, state="EXCHANGED", item_id=RECOVERED_ITEM, stored=RECOVERED_ITEM
        )

        with pytest.raises(DuplicateExchange) as raised:
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tokens,
                connection=connection,
            )

        verdict = raised.value.verdict
        assert verdict.outcome is DuplicateOutcome.SAME_ITEM
        assert verdict.identities == (RECOVERED_ITEM,)
        assert verdict.slots == 1
        # Nothing has to survive: two credentials for one Item means neither is the
        # only copy of anything, which is what makes deduplicating safe *here* and
        # nowhere else. An empty tuple is the assertion, not an absent one.
        assert verdict.keep == ()
        both_credentials_survive(tokens, artifact, key_file)
        connection.close()

    def test_the_budget_counts_one_slot_for_the_two_exchanges(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The number follows the identity: one Item returned twice costs one slot."""
        connection = database(
            tmp_path, state="EXCHANGED", item_id=RECOVERED_ITEM, stored=RECOVERED_ITEM
        )

        budget = read_item_budget(connection)

        assert budget.spent_count == 1
        assert [slot.plaid_item_id for slot in budget.spent] == [RECOVERED_ITEM]
        assert budget.spent[0].evidence is SlotEvidence.ITEM
        connection.close()


class TestDistinctItems:
    """Shape 2: *"distinct Items — keep and account for both"*.

    *"This is the case that costs a slot, and the design must be able to say so
    rather than being unable to represent it."*
    """

    def test_two_identities_mean_two_slots_and_two_things_to_keep(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        held_by_the_other_host(tokens, item_id=OTHER_ITEM)
        connection = database(tmp_path, state="EXCHANGED", item_id=OTHER_ITEM, stored=OTHER_ITEM)

        with pytest.raises(DuplicateExchange) as raised:
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tokens,
                connection=connection,
            )

        verdict = raised.value.verdict
        assert verdict.outcome is DuplicateOutcome.DISTINCT_ITEMS
        assert verdict.identities == tuple(sorted((RECOVERED_ITEM, OTHER_ITEM)))
        # Two, derived from the identities rather than written down: this is the
        # "not a fixed number" clause, and the same code path returns 1 above.
        assert verdict.slots == 2
        # Both, and each for its own Item. The artifact is in here because deleting
        # it loses the only credential this host has for RECOVERED_ITEM — the exact
        # thing "the exchange is already done" used to invite.
        assert verdict.keep == tuple(
            sorted((f"the recovery artifact at {artifact}", f"the TokenStore at {tokens}"))
        )
        both_credentials_survive(tokens, artifact, key_file)
        connection.close()

    def test_the_budget_reaches_two_only_once_the_second_item_has_a_row(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The gap between what is spent and what is countable, measured.

        The recovered Item's credential is in the artifact and nowhere else, so this
        host's database has no row for it and ``read_item_budget`` counts **one** —
        it understates by exactly the slot this shape exists to notice. That is not a
        defect in the budget: *"a recovery that writes the item row and leaves its
        flow row nameless is indistinguishable from an ordinary stranded flow"*, and
        here there is not even a nameless row to see. It is why the verdict carries
        its own count and why ``keep`` names the artifact — and once the second Item
        is recorded the arithmetic agrees, which is what makes this a gap in the
        evidence rather than in the module.
        """
        connection = database(tmp_path, state="EXCHANGED", item_id=OTHER_ITEM, stored=OTHER_ITEM)

        assert read_item_budget(connection).spent_count == 1

        connection.execute(
            "INSERT INTO item(institution_id, plaid_item_id, secret_ref, status, "
            "status_since, created_at) VALUES (1, ?, ?, 'DEGRADED', ?, ?)",
            (RECOVERED_ITEM, f"access_token.{RECOVERED_ITEM}", STAMP, STAMP),
        )
        connection.commit()

        budget = read_item_budget(connection)
        assert budget.spent_count == 2
        assert sorted(slot.plaid_item_id or "" for slot in budget.spent) == sorted(
            (RECOVERED_ITEM, OTHER_ITEM)
        )
        connection.close()

    def test_distinct_items_without_a_collision_still_report_the_second_slot(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The shape with nothing for the ``TokenStore`` to refuse.

        On `07a`'s path a result's credential is filed under
        ``access_token.<result_id>``, so a second exchange leaves ``access_token.<flow_id>``
        free and the restore *succeeds*. The pairing then refuses — its message names
        criterion 7 — but a refusal about one row says nothing about how many Items
        there are, so without :attr:`RestoreOutcome.duplicate` nothing on this path
        would say a second lifetime slot is gone.
        """
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        connection = database(tmp_path, state="EXCHANGED", item_id=OTHER_ITEM, stored=OTHER_ITEM)

        outcome = restore(
            artifact, key_file=key_file, token_store_directory=tokens, connection=connection
        )

        assert outcome.duplicate.outcome is DuplicateOutcome.DISTINCT_ITEMS
        assert outcome.duplicate.slots == 2
        assert outcome.pairing is not None
        assert outcome.pairing.result is PairingResult.REFUSED
        assert not outcome.complete
        # The credential half still landed, which is the half that must never be in
        # doubt once the token is spent.
        assert (
            TokenStore(tokens).get(secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)).reveal()
            == RECOVERED_TOKEN
        )
        connection.close()


class TestUnknownIdentity:
    """Shape 3: *"unknown identity — explicit unresolved, nothing discarded"*.

    *"Not a default to either branch above; the honest third answer, in the same
    spirit as `06a` (iii)'s irreducible window."*
    """

    def test_a_stored_credential_naming_no_item_is_unresolved(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        held_by_the_other_host(tokens, item_id=None)
        connection = database(tmp_path, state="EXCHANGED", item_id=None)

        with pytest.raises(DuplicateExchange) as raised:
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tokens,
                connection=connection,
            )

        verdict = raised.value.verdict
        assert verdict.outcome is DuplicateOutcome.UNRESOLVED
        # No number, rather than a number with a caveat beside it: a caller cannot
        # tell a guessed integer from a measured one.
        assert verdict.slots is None
        assert verdict.keep == tuple(
            sorted((f"the recovery artifact at {artifact}", f"the TokenStore at {tokens}"))
        )
        both_credentials_survive(tokens, artifact, key_file)
        connection.close()

    def test_a_nameless_exchanged_row_is_unresolved_and_the_budget_refuses(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """Both layers decline to produce a number for the same database.

        ``EXCHANGED`` means an exchange was committed on the host that died and its
        Item identity was lost. :func:`~networth.item_budget.read_item_budget` already
        refuses this shape — *"it cannot be told apart from the stored item row(s) it
        may already be counted by"* — and the verdict has to reach the same answer, or
        one of the two would be guessing.
        """
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        connection = database(tmp_path, state="EXCHANGED", item_id=None, stored=OTHER_ITEM)

        outcome = restore(
            artifact, key_file=key_file, token_store_directory=tokens, connection=connection
        )

        assert outcome.duplicate.outcome is DuplicateOutcome.UNRESOLVED
        assert outcome.duplicate.slots is None
        with pytest.raises(ItemBudgetError, match="EXCHANGED but names no Item"):
            read_item_budget(connection)
        connection.close()


class TestTheOrdinaryRecoveryIsNotADuplicate:
    """The control every refusal above is worth only as much as.

    Three of the four shapes are negative outcomes, and a classifier that answered
    UNRESOLVED to everything would satisfy them all. This is the shape `07b` is
    actually *for* — the VPS died **before** exchanging, so its row is
    ``SUCCESS_PENDING_EXCHANGE`` and nothing else exchanged anything.
    """

    def test_a_clean_recovery_reports_no_duplicate_and_one_slot(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        connection = database(tmp_path, state="SUCCESS_PENDING_EXCHANGE", item_id=None)

        outcome = restore(
            artifact, key_file=key_file, token_store_directory=tokens, connection=connection
        )

        assert not outcome.duplicate.duplicate
        assert outcome.duplicate.outcome is DuplicateOutcome.SAME_ITEM
        assert outcome.duplicate.slots == 1
        assert outcome.pairing is not None
        assert outcome.pairing.result is PairingResult.WRITTEN
        assert outcome.complete
        assert read_item_budget(connection).spent_count == 1
        connection.close()

    def test_a_token_expired_row_is_not_read_as_a_second_exchange(self, tmp_path: Path) -> None:
        """A host that gave up on the token did not exchange it.

        ``apply_pairing`` writes onto this row deliberately and says why — *"a row the
        VPS gave up on is still a spent slot, and this recovery just proved the token
        was live after all"*. Reading it as duplicate evidence would make that write
        unreachable behind an UNRESOLVED verdict.
        """
        connection = database(tmp_path, state="TOKEN_EXPIRED", item_id=None)

        evidence = gather_duplicate_evidence(
            flow_id=FLOW_ID,
            recovered_token=Secret(RECOVERED_TOKEN),
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        assert evidence == ()
        assert "TOKEN_EXPIRED" not in ATTEMPTED_EXCHANGE_STATES
        connection.close()

    @pytest.mark.parametrize("state", ATTEMPTED_EXCHANGE_STATES)
    def test_every_attempted_exchange_state_is_evidence_when_it_names_no_item(
        self, tmp_path: Path, state: str
    ) -> None:
        """The other side of the same boundary, so the tuple is pinned both ways."""
        connection = database(tmp_path, state=state, item_id=None)

        evidence = gather_duplicate_evidence(
            flow_id=FLOW_ID,
            recovered_token=Secret(RECOVERED_TOKEN),
            token_store_directory=tmp_path / "tokens",
            connection=connection,
        )

        assert [record.item_id for record in evidence] == [None]
        assert evidence[0].source is DuplicateSource.FLOW_ROW
        connection.close()


class TestThisRecoveryRestoredTwiceIsNotADuplicate:
    """The case Item identity **cannot** tell apart from shape 1, and it is not rare.

    A restore that already ran leaves its own credential under exactly the name a
    second run collides with, and that record names exactly the Item this recovery
    returned. Read on identity alone it is indistinguishable from the old VPS having
    exchanged and got the same Item — but it is not a second exchange at all, no
    second slot is in question, and the refusal it deserves is ``prepare_for``'s
    existing one, whose closing clause *"if that material is the recovery you are
    repeating, the exchange is already done"* is exactly right **here**. That clause
    was never wrong about this case; it was wrong about being said without checking.

    The discriminator is the material, not the identity: Plaid issues a new
    ``access_token`` per exchange, which is how `06a` (ii) could see the first one
    still HEALTHY beside an accepted duplicate.

    Found by this change breaking
    ``test_restore_link_artifact_command.py::test_pairing_only_finishes_what_the_credential_only_run_left_owed``,
    which runs the sequence the ``--pairing-only`` guidance tells the owner to run
    and asserts the full restore refuses first. Pinned here too, because that test
    is about the *guidance* and would report this regression as a broken exit code.
    """

    def test_a_second_full_restore_refuses_without_calling_it_a_duplicate(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        connection = database(tmp_path, state="SUCCESS_PENDING_EXCHANGE", item_id=None)
        restore(artifact, key_file=key_file, token_store_directory=tokens, connection=connection)

        with pytest.raises(SinkNotWritable, match="the exchange is already done") as raised:
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tokens,
                connection=connection,
            )

        # Not the subclass: a `DuplicateExchange` here would report a second exchange
        # that never happened, and its DISTINCT/UNRESOLVED siblings would tell the
        # owner a lifetime slot may be gone when none is.
        assert not isinstance(raised.value, DuplicateExchange)
        connection.close()

    def test_the_same_item_with_different_material_is_a_duplicate(
        self, tmp_path: Path, key_file: Path
    ) -> None:
        """The control for the test above: identity equal, material different.

        Without this pair, "not a duplicate" is equally satisfied by a gatherer that
        never reports a ``TokenStore`` record at all — and that gatherer would make
        every collision fall through to ``prepare_for``, which is the behaviour this
        whole module replaces.
        """
        artifact = sealed(tmp_path, key_file)
        tokens = tmp_path / "tokens"
        held_by_the_other_host(tokens, item_id=RECOVERED_ITEM)
        connection = database(tmp_path, state="SUCCESS_PENDING_EXCHANGE", item_id=None)

        with pytest.raises(DuplicateExchange) as raised:
            restore(
                artifact,
                key_file=key_file,
                token_store_directory=tokens,
                connection=connection,
            )

        assert raised.value.verdict.outcome is DuplicateOutcome.SAME_ITEM
        assert raised.value.verdict.duplicate
        connection.close()


class TestTokenStoreNeverPrefersOneCredential:
    """Criterion 8's second clause, read as a claim about the store itself.

    It holds *structurally* rather than by policy:
    :meth:`~networth.tokenstore.TokenStore.put` publishes with :func:`os.link` and so
    cannot overwrite even by accident. Worth pinning here anyway, because every
    refusal above is built on it — if the store preferred the newer write, the
    distinct-Items verdict would be a correct sentence printed over a lost
    credential.
    """

    def test_a_second_put_under_the_same_name_is_refused(self, tmp_path: Path) -> None:
        tokens = tmp_path / "tokens"
        store = held_by_the_other_host(tokens, item_id=OTHER_ITEM)

        with pytest.raises(SecretRefExists):
            store.put(SecretKind.ACCESS_TOKEN, FLOW_ID, RECOVERED_TOKEN, item_id=RECOVERED_ITEM)

        reference = secret_ref_for(SecretKind.ACCESS_TOKEN, FLOW_ID)
        assert store.get(reference).reveal() == OTHER_TOKEN
        assert store.record(reference).item_id == OTHER_ITEM


class TestTheBranchIsDecidedFromIdentitiesAlone:
    """:func:`classify_duplicate` on synthetic identities — no disk at all.

    This is the layer criterion 8's *"synthetic responses"* names most directly, and
    it is separate from the shapes above because those also exercise the restore. A
    bug that made the *gatherer* miss a row would leave these green, which is why
    both exist.
    """

    def store_record(self, item_id: str | None) -> DuplicateEvidence:
        return DuplicateEvidence(
            source=DuplicateSource.TOKEN_STORE,
            where="the other host's TokenStore",
            item_id=item_id,
            detail="synthetic",
        )

    def test_unresolved_wins_over_a_distinct_identity(self) -> None:
        """An unestablished identity is not outvoted by an established one.

        With both present the flow may have spent two slots or three, so the answer
        is neither DISTINCT_ITEMS' number nor SAME_ITEM's. Order matters here only in
        the sense that it must *not*: the unnamed record is checked first.
        """
        verdict = classify_duplicate(
            flow_id=FLOW_ID,
            recovered_item_id=RECOVERED_ITEM,
            recovered_where="the artifact",
            evidence=(self.store_record(OTHER_ITEM), self.store_record(None)),
        )

        assert verdict.outcome is DuplicateOutcome.UNRESOLVED
        assert verdict.slots is None
        # Every holder, including the two whose Items are known: while one identity
        # is open, a second copy cannot be shown to be a copy of the *same* Item.
        assert verdict.keep == ("the artifact", "the other host's TokenStore")

    def test_three_distinct_identities_cost_three_slots(self) -> None:
        """The count is ``len(identities)`` and is asserted somewhere it is not 2."""
        verdict = classify_duplicate(
            flow_id=FLOW_ID,
            recovered_item_id=RECOVERED_ITEM,
            recovered_where="the artifact",
            evidence=(
                DuplicateEvidence(
                    source=DuplicateSource.FLOW_ROW,
                    where="link_result one",
                    item_id=OTHER_ITEM,
                    detail="synthetic",
                ),
                DuplicateEvidence(
                    source=DuplicateSource.FLOW_ROW,
                    where="link_result two",
                    item_id="item-third-000000000",
                    detail="synthetic",
                ),
            ),
        )

        assert verdict.outcome is DuplicateOutcome.DISTINCT_ITEMS
        assert verdict.slots == 3
        # Neither `link_result` row holds a credential, so neither can be lost by
        # deleting it -- only the artifact is irreplaceable here.
        assert verdict.keep == ("the artifact",)

    def test_an_empty_identity_is_refused_rather_than_compared(self) -> None:
        """An empty string compares unequal to every real Item, so it would read as
        DISTINCT_ITEMS and fabricate a second spent slot — the expensive direction."""
        with pytest.raises(ValueError, match="needs a flow id"):
            classify_duplicate(
                flow_id=FLOW_ID,
                recovered_item_id="",
                recovered_where="the artifact",
                evidence=(),
            )
