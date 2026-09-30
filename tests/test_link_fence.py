"""`07b` criterion 6: the fence is a typed attestation, and these pin what it refuses.

The criterion's own words are what has to be true here — *"the script takes an explicit
typed confirmation naming that instance"* and *"unreachable is not off"* — so the
interesting assertions are about what does **not** satisfy the fence: a keypress, an
affirmation, a blank line, another host's name. Each is paired with the control that the
real name does satisfy it, because "confirm() raised" is equally satisfied by a fence
that refuses everything.

The other half is the owner-facing text, which criterion 6 asks for by name: it must say
plainly that the fenced host is also his exit node. That is a *product* requirement
rather than a phrasing preference — the fence's whole purpose is that powering the host
off is a decision he made knowing what it costs — so it is asserted rather than left to
review.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from networth.link_fence import (
    ATTESTATION,
    FENCED_INSTANCE,
    FenceAttestation,
    FenceNotConfirmed,
    FenceRecordInvalid,
    confirm,
    prompt_text,
)

NOW = datetime(2026, 9, 29, 21, 40, tzinfo=UTC)

#: What a paste into the wrong prompt could look like. Synthetic, and deliberately not
#: shaped like a real credential (this repo's scanner is right to refuse those): the
#: point of the test that uses it is that no refusal repeats its input back into a
#: transcript the owner may be piping to a file.
A_MISDIRECTED_PASTE = "something-that-came-out-of-the-paste-buffer"


class _Terminal:
    """An ``ask`` that answers once and remembers the prompt it was handed."""

    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.asked: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.asked.append(prompt)
        return self._answer


class TestWhatSatisfiesTheFence:
    def test_the_hosts_name_confirms_it_and_the_record_says_what_was_attested(self) -> None:
        attestation = confirm(_Terminal(FENCED_INSTANCE), now=NOW)

        assert attestation.instance == FENCED_INSTANCE
        assert attestation.statement == ATTESTATION
        assert attestation.confirmed_at == NOW

    @pytest.mark.parametrize("answer", ["", "   ", "\t"])
    def test_an_empty_answer_is_not_a_confirmation(self, answer: str) -> None:
        with pytest.raises(FenceNotConfirmed, match="nothing was typed"):
            confirm(_Terminal(answer), now=NOW)

    @pytest.mark.parametrize("answer", ["y", "yes", "Y", "ok", "done", "off", "confirm"])
    def test_an_affirmation_is_not_a_name(self, answer: str) -> None:
        """Criterion 6 asks for a typed confirmation *naming that instance*.

        A ``y`` is the formality the criterion exists to prevent — *"so powering it off
        is a real decision and not a formality"* — and it is refused by the same
        comparison that refuses another host, rather than by a list of words to reject.
        """
        with pytest.raises(FenceNotConfirmed, match="does not name the host"):
            confirm(_Terminal(answer), now=NOW)

    def test_another_host_is_refused(self) -> None:
        with pytest.raises(FenceNotConfirmed, match="does not name the host"):
            confirm(_Terminal(FENCED_INSTANCE + "-2"), now=NOW)

    @pytest.mark.parametrize(
        "answer", [FENCED_INSTANCE.upper(), f"  {FENCED_INSTANCE}  ", f"{FENCED_INSTANCE}\t"]
    )
    def test_case_and_surrounding_space_are_a_typo_not_a_different_host(self, answer: str) -> None:
        attestation = confirm(_Terminal(answer), now=NOW)

        # The canonical spelling is what lands, so no later reader has to decide whether
        # two spellings name one host.
        assert attestation.instance == FENCED_INSTANCE

    def test_no_refusal_repeats_what_was_typed(self) -> None:
        """The prompt after this one reads a Plaid secret, and this one echoes.

        A refusal that quoted its input would put whatever was in the paste buffer into
        the transcript, and this is the prompt an owner mid-emergency is most likely to
        answer with the wrong thing.
        """
        with pytest.raises(FenceNotConfirmed) as refusal:
            confirm(_Terminal(A_MISDIRECTED_PASTE), now=NOW)

        assert A_MISDIRECTED_PASTE not in str(refusal.value)
        # And it still says what he can act on.
        assert "link_fence.py" in str(refusal.value)


class TestTheOwnerFacingText:
    def test_it_states_plainly_that_this_host_is_also_his_exit_node(self) -> None:
        text = prompt_text()

        assert "exit node" in text
        assert "VPN exit" in text

    def test_it_says_unreachable_is_not_off(self) -> None:
        text = prompt_text()

        assert "Unreachable is not off" in text
        # The mechanism, not only the slogan: what a live host could still be doing.
        assert "/link/token/get" in text

    def test_it_does_not_supply_the_answer_it_is_asking_for(self) -> None:
        """A prompt that prints the name to type is friction, not evidence.

        The refusal names where the expected value is written, so a renamed or mistyped
        host is recoverable — see ``test_no_refusal_repeats_what_was_typed``. The prompt
        itself must not, or the fence degrades into pressing a longer key.
        """
        assert FENCED_INSTANCE not in prompt_text()

    def test_the_prompt_is_what_the_owner_is_actually_asked(self) -> None:
        terminal = _Terminal(FENCED_INSTANCE)

        confirm(terminal, now=NOW)

        assert terminal.asked == [prompt_text()]


class TestTheRecordThatTravelsWithTheCredential:
    def test_it_round_trips(self) -> None:
        attestation = confirm(_Terminal(FENCED_INSTANCE), now=NOW)

        assert FenceAttestation.from_record(attestation.to_record()) == attestation

    def test_an_offset_free_instant_is_refused(self) -> None:
        """Whether the fence preceded the exchange is the only thing its clock is for."""
        with pytest.raises(FenceRecordInvalid, match="UTC offset"):
            FenceAttestation(
                instance=FENCED_INSTANCE,
                statement=ATTESTATION,
                confirmed_at=NOW.replace(tzinfo=None),
            )

    def test_an_offset_other_than_utc_names_the_same_instant(self) -> None:
        local = NOW.astimezone(timezone(timedelta(hours=-7)))
        attestation = FenceAttestation(
            instance=FENCED_INSTANCE, statement=ATTESTATION, confirmed_at=local
        )

        assert FenceAttestation.from_record(attestation.to_record()).confirmed_at == NOW

    @pytest.mark.parametrize("record", [None, "tokyo-exit", 7, [], {}])
    def test_anything_that_is_not_a_statement_is_refused(self, record: object) -> None:
        with pytest.raises(FenceRecordInvalid):
            FenceAttestation.from_record(record)

    @pytest.mark.parametrize("missing", ["instance", "statement", "confirmed_at"])
    def test_each_field_is_required(self, missing: str) -> None:
        record = confirm(_Terminal(FENCED_INSTANCE), now=NOW).to_record()
        del record[missing]

        with pytest.raises(FenceRecordInvalid, match=missing):
            FenceAttestation.from_record(record)

    def test_a_confirmed_at_that_is_not_an_instant_is_refused(self) -> None:
        record = confirm(_Terminal(FENCED_INSTANCE), now=NOW).to_record()
        record["confirmed_at"] = "last Tuesday"

        with pytest.raises(FenceRecordInvalid, match="ISO 8601"):
            FenceAttestation.from_record(record)
