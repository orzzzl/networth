"""Task 08: the owner's Link, from his terminal to a total that contains the accounts.

Two layers. The scripted-host tests pin each refusal and the order of the steps
that matter. The last test runs the whole path in one process against the real
verbs, the real lifecycle worker and the real sync — only Plaid and SSH are
synthetic — because every piece below had been tested on its own before and the
path from one end to the other had never been walked.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import sqlite3
import sys
from collections.abc import Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from networth import cli, link_recovery, mac_identity, runtime
from networth.commands import link_authorize, link_mint, link_status
from networth.commands.authorize_automatic_link import ACK
from networth.filelock import exclusive_file_lock
from networth.link_recovery import MintResult
from networth.mac_identity import REQUIRED_HOLDER
from networth.owner_link import (
    CONFIRMATION,
    EXIT_NEEDS_ATTENTION,
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_UNFINISHED,
    PROVE_POLLING_FOR,
    REQUIRED_UNITS,
    STALL_AFTER,
    WATCH_FOR,
    RemoteFailure,
    judge,
    report_status,
    run_link,
)
from networth.plaid import AccountDescriptor, BalanceRecord, HoldingRecord, InvestmentRecords
from networth.plaid.client import (
    ExchangedItem,
    HostedLinkToken,
    ItemInstitution,
    ItemStatus,
    LinkSessionPoll,
    LinkSessionRecord,
    LinkSessionShape,
)
from networth.plaid.environment import PlaidCredentials, PlaidEnvironment, paths_for
from networth.plaid.errors import HEALTHY
from networth.tokenstore import Secret

PRODUCTION = PlaidEnvironment.PRODUCTION
SANDBOX = PlaidEnvironment.SANDBOX
FLOW = "f" * 32
LINK_TOKEN = "synthetic-link-token-sentinel"
ACCESS_TOKEN = "synthetic-access-token-sentinel"
URL = "https://synthetic.invalid/hosted-link-sentinel"


class Console:
    def __init__(self, *answers: str, tty: bool = True) -> None:
        self.lines: list[str] = []
        self.prompts: list[str] = []
        self._answers = list(answers)
        self._tty = tty

    def say(self, line: str = "") -> None:
        self.lines.append(line)

    def ask(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._answers.pop(0) if self._answers else ""

    def interactive(self) -> bool:
        return self._tty

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class Clock:
    """A clock only the driver's own sleeps move, so a test reads elapsed time exactly."""

    def __init__(self) -> None:
        self.start = self.now = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    @property
    def elapsed(self) -> timedelta:
        return self.now - self.start


def budget(remaining: int = 10) -> dict[str, Any]:
    return {
        "state": "AVAILABLE",
        "capacity": 10,
        "spent": 10 - remaining,
        "remaining": remaining,
        "in_flight": 0,
        "stranded": 0,
    }


def flow_status(**overrides: Any) -> dict[str, Any]:
    flow: dict[str, Any] = {
        "known": True,
        "request": "URL_MINTED",
        "released": True,
        "url_expired": False,
        "polled_after_expiry": False,
        "poll_failing": False,
        "closed": False,
        "polls": 1,
        "sessions": 0,
        "results": dict.fromkeys(link_status.RESULT_STATES, 0),
        "holds": 0,
        "awaiting_closure": False,
        "unidentified": 0,
        "items": 0,
        "items_discovered": 0,
        "accounts": 0,
        "accounts_pending": 0,
        "accounts_in_snapshot": 0,
    }
    results = overrides.pop("results", {})
    flow.update(overrides)
    flow["results"].update(results)
    return flow


def status(flow: dict[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "environment": "production",
        "budget": budget(),
        "pending_requests": 0,
        "awaiting_closure": 0,
        "unattributed": 0,
        "mint_window_ends_at": None,
        "items": 0,
        "paired": False,
        "published_latest": False,
    }
    document.update(overrides)
    if flow is not None:
        document["flow"] = flow
    return document


class ScriptedHost:
    """A host that answers from a script and records what it was asked, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.inactive: list[str] = []
        self.preflight: dict[str, Any] | Exception = status()
        self.flows: list[dict[str, Any] | Exception] = []
        self.mint_lines: list[str] | Exception = [
            MintResult(
                FLOW,
                Secret(LINK_TOKEN),
                datetime.now(UTC),
                datetime.now(UTC) + timedelta(minutes=30),
                1800,
                URL,
            ).to_wire()
        ]
        self.authorize_lines: list[str] | Exception = [ACK + FLOW]
        self.material: list[str] = []
        self.pass_fails = False
        #: What makes the request's poll count move before the URL is released:
        #: "service" (it moves by itself), "pass" (only a worker pass run by the
        #: driver moves it) or None (nothing does).
        self.poller: str | None = "service"
        self._polls = 1  # the mint's own
        #: What every status read returns once ``flows`` is used up.
        self.steady: dict[str, Any] | None = None

    def _before_release(self) -> dict[str, Any]:
        """A status read made between the mint and the release: the polling proof."""
        answer = status(flow_status(released=False, polls=self._polls))
        if self.poller == "service":
            self._polls += 1
        return answer

    def verb(
        self, name: str, *arguments: str, material: str = "", stated: bool = True
    ) -> list[str]:
        self.calls.append(" ".join([name, *arguments]))
        if name == "link-status":
            answer: Any
            if not arguments:
                answer = self.preflight
            elif "link-status" in self.calls and "link-authorize" not in self.calls:
                # After a preflight and before the release: the polling proof.
                answer = self._before_release()
            elif not self.flows and self.steady is not None:
                answer = self.steady
            else:
                answer = self.flows.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return ["noise the driver must skip", link_status.to_wire(answer)]
        if name == "link-mint":
            if isinstance(self.mint_lines, Exception):
                raise self.mint_lines
            return self.mint_lines
        if name == "link-authorize":
            self.material.append(material)
            if isinstance(self.authorize_lines, Exception):
                raise self.authorize_lines
            return self.authorize_lines
        if name == "daemon":
            assert not stated, "the daemon verb takes no --environment"
            if arguments[0] == "link-flow":
                if self.poller == "pass":
                    self._polls += 1
                if self.pass_fails:
                    raise RemoteFailure("a pass that recorded a failure")
            return []
        if name == "backup":
            return []
        raise AssertionError(f"unexpected verb {name}")

    def inactive_units(self, units: Sequence[str]) -> list[str]:
        self.calls.append("units")
        assert tuple(units) == REQUIRED_UNITS
        return self.inactive

    def start_sync(self) -> None:
        self.calls.append("start-sync")


def link(
    host: ScriptedHost,
    console: Console,
    tmp_path: Path,
    *,
    environment: PlaidEnvironment = PRODUCTION,
    **overrides: Any,
) -> int:
    arguments: dict[str, Any] = {
        "environment": environment,
        "directory": tmp_path / "recovery",
        "verify_holder": lambda: REQUIRED_HOLDER,
        "sleep": lambda _: None,
    }
    arguments.update(overrides)
    return run_link(host, console, **arguments)


def record_on_disk(tmp_path: Path) -> Path:
    return tmp_path / "recovery" / f"{FLOW}.json"


# --- every refusal lands before anything is minted ---------------------------


def test_the_wrong_machine_is_refused_before_the_host_is_contacted(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)

    def wrong() -> str:
        raise mac_identity.WrongHost("this machine does not hold the address")

    assert link(host, console, tmp_path, verify_holder=wrong) == EXIT_REFUSED
    assert host.calls == []
    assert "Nothing was minted" in console.text


def test_production_refuses_while_the_daemon_is_not_running(tmp_path: Path) -> None:
    """A finished login is exchangeable for 30 minutes; the exchanger must already be up."""
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.inactive = ["networth-link.service"]

    assert link(host, console, tmp_path) == EXIT_REFUSED
    assert host.calls == ["units"]
    assert "networth-link.service" in console.text and URL not in console.text


def test_a_refused_count_is_reported_as_a_refusal_and_never_as_a_number(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.preflight = status(budget={"state": "UNAVAILABLE"})

    assert link(host, console, tmp_path) == EXIT_REFUSED
    assert "link-mint" not in host.calls
    assert "cannot account for its lifetime connections" in console.text
    assert not any(character.isdigit() for character in console.text)
    assert console.prompts == []


def test_no_remaining_slot_is_refused_before_the_confirmation(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.preflight = status(budget=budget(remaining=0))

    assert link(host, console, tmp_path) == EXIT_REFUSED
    assert "10 of 10 lifetime bank connections used, 0 left" in console.text
    assert console.prompts == [] and "link-mint" not in host.calls


def test_a_failed_backup_check_refuses_the_mint(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)

    def broken() -> None:
        raise RuntimeError("synthetic-private-backup-detail")

    assert link(host, console, tmp_path, canary=broken) == EXIT_REFUSED
    assert "link-mint" not in host.calls and console.prompts == []
    assert "synthetic-private-backup-detail" not in console.text
    assert "backup check failed (RuntimeError)" in console.text


@pytest.mark.parametrize(
    ("console", "asked"),
    [(Console(CONFIRMATION, tty=False), False), (Console("yes"), True), (Console(""), True)],
    ids=["no-terminal", "wrong-word", "empty"],
)
def test_production_needs_the_owners_typed_word_at_a_terminal(
    tmp_path: Path, console: Console, asked: bool
) -> None:
    host = ScriptedHost()

    assert link(host, console, tmp_path) == EXIT_REFUSED
    assert bool(console.prompts) is asked
    assert "link-mint" not in host.calls
    assert not record_on_disk(tmp_path).exists()


def test_the_owner_is_told_the_cost_and_the_deadline_before_he_is_asked(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console("no")
    link(host, console, tmp_path)

    assert "0 of 10 lifetime bank connections used, 10 left" in console.text
    assert "spent when you finish logging in" in console.text
    assert "30 minutes" in console.text
    assert "6 hours" not in console.text and "six hours" not in console.text


# --- the URL is the last thing to appear --------------------------------------


def test_the_url_is_shown_only_after_the_copy_is_on_disk_and_the_host_acknowledged(
    tmp_path: Path,
) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    opened: list[tuple[str, bool, list[str]]] = []
    canaried: list[list[str]] = []

    exit_code = link(
        host,
        console,
        tmp_path,
        watch=False,
        canary=lambda: canaried.append(list(host.calls)),
        open_url=lambda url: opened.append((url, record_on_disk(tmp_path).exists(), host.calls[:])),
    )

    assert exit_code == EXIT_OK
    assert canaried == [["units", "link-status"]], "the backup check precedes the mint"
    proof = f"link-status --flow {FLOW}"
    assert host.calls == ["units", "link-status", "link-mint", proof, proof, "link-authorize"]
    ((url, copy_existed, calls_when_opened),) = opened
    assert url == URL and copy_existed and calls_when_opened[-1] == "link-authorize"
    # What the host was sent is the record this Mac holds, holder and all.
    sent = link_recovery.RecoveryRecord.from_json(host.material[0])
    assert sent.second_copy_holder == REQUIRED_HOLDER and sent.second_copy_verified_at is not None
    assert record_on_disk(tmp_path).read_text() == host.material[0]
    assert console.text.count(URL) == 1 and LINK_TOKEN not in console.text
    assert FLOW in console.text


@pytest.mark.parametrize(
    "fault",
    ["mint-fails", "two-payloads", "no-payload", "no-ack", "wrong-ack", "authorize-fails"],
)
def test_a_failure_anywhere_before_the_acknowledgement_shows_no_url(
    tmp_path: Path, fault: str
) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    assert isinstance(host.mint_lines, list)
    payload = host.mint_lines
    if fault == "mint-fails":
        host.mint_lines = RemoteFailure("link-mint stopped: no Item budget remains")
    if fault == "two-payloads":
        host.mint_lines = payload * 2
    if fault == "no-payload":
        host.mint_lines = ["a transcript with no payload line"]
    if fault == "no-ack":
        host.authorize_lines = []
    if fault == "wrong-ack":
        host.authorize_lines = [ACK + "0" * 32]
    if fault == "authorize-fails":
        host.authorize_lines = RemoteFailure("link-authorize stopped: request cannot authorize")
    opened: list[str] = []

    assert link(host, console, tmp_path, open_url=opened.append) == EXIT_REFUSED
    assert opened == []
    assert URL not in console.text and LINK_TOKEN not in console.text
    assert "Nothing can have been spent" in console.text
    assert host.calls[-1] in {"link-mint", "link-authorize"}, "it must not start watching"


def test_a_copy_that_cannot_be_written_stops_before_the_host_is_told(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")

    assert link(host, console, tmp_path, directory=blocked / "recovery") == EXIT_REFUSED
    assert "link-authorize" not in host.calls
    assert URL not in console.text


def test_resume_shows_an_existing_flows_page_again_without_minting(tmp_path: Path) -> None:
    host, first = ScriptedHost(), Console(CONFIRMATION)
    assert link(host, first, tmp_path, watch=False) == EXIT_OK
    host.calls.clear()
    again = Console(CONFIRMATION)

    assert link(host, again, tmp_path, watch=False, resume=FLOW) == EXIT_OK
    proof = f"link-status --flow {FLOW}"
    assert host.calls == ["units", "link-status", proof, proof, "link-authorize"]
    assert again.text.count(URL) == 1


# --- watching: what each thing the host can say means -------------------------


def test_a_finished_login_is_followed_to_its_accounts_and_reported(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    exchanged = {"results": {"EXCHANGED": 1}, "sessions": 1, "items": 1}
    host.flows = [
        status(flow_status()),
        status(flow_status(sessions=1, results={"SUCCESS_PENDING_EXCHANGE": 1})),
        status(flow_status(sessions=1, results={"EXCHANGING": 1})),
        status(flow_status(**exchanged)),
        status(flow_status(**exchanged)),
        status(
            flow_status(**exchanged, items_discovered=1, accounts=2, accounts_in_snapshot=2),
            budget=budget(remaining=9),
        ),
    ]
    protected: list[list[str]] = []

    exit_code = link(host, console, tmp_path, protect=lambda: protected.append(host.calls[:]))

    assert exit_code == EXIT_OK
    assert host.flows == []
    assert host.calls.count("start-sync") == 1
    assert len(protected) == 1 and protected[0][-1] == "start-sync"
    assert "daemon link-flow" not in " ".join(host.calls), "production never drives the worker"
    assert console.text.count("The host is exchanging the result") == 1
    assert "2 account(s) recorded; 2 already in the total." in console.text
    assert "1 of 10 lifetime bank connections used, 9 left." in console.text
    assert "No phone is paired yet" in console.text
    assert "Backup verified on this Mac." in console.text


def test_a_failed_backup_copy_is_loud_and_does_not_unsay_the_link(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    done = flow_status(
        results={"EXCHANGED": 1}, items=1, items_discovered=1, accounts=1, accounts_in_snapshot=1
    )
    host.flows = [status(done), status(done)]

    def broken() -> None:
        raise OSError("synthetic-private-transfer-detail")

    assert link(host, console, tmp_path, protect=broken) == EXIT_OK
    assert "WARNING: the backup copy did not complete (OSError)" in console.text
    assert "synthetic-private-transfer-detail" not in console.text
    assert "Connected." in console.text


@pytest.mark.parametrize(
    ("flow", "phrase"),
    [
        (flow_status(results={"TOKEN_EXPIRED": 1}), "TOKEN_EXPIRED"),
        (flow_status(results={"EXCHANGE_UNCERTAIN": 1, "EXCHANGED": 1}), "EXCHANGE_UNCERTAIN"),
        (flow_status(holds=1), "could not attribute"),
        (flow_status(unidentified=1), "could not attribute"),
        ({"known": False}, "no record of this flow"),
    ],
    ids=["token-expired", "uncertain-beside-success", "hold", "unidentified", "unknown-flow"],
)
def test_anything_the_host_could_not_finish_stops_the_owner_from_trying_again(
    tmp_path: Path, flow: dict[str, Any], phrase: str
) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.flows = [status(flow)]

    assert link(host, console, tmp_path) == EXIT_NEEDS_ATTENTION
    assert phrase in console.text
    assert "Do not run this again" in console.text and FLOW in console.lines[-1]
    assert "start-sync" not in host.calls


def test_an_expired_page_is_not_called_unfinished_until_a_poll_after_expiry_agrees(
    tmp_path: Path,
) -> None:
    """A login finished in the URL's last seconds is visible only to a later poll."""
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.flows = [
        status(flow_status(url_expired=True)),
        status(flow_status(url_expired=True, polled_after_expiry=True)),
    ]

    assert link(host, console, tmp_path) == EXIT_UNFINISHED
    assert host.flows == []
    assert "no finished login was seen" in console.text
    assert "does not assume that on its own" in console.text and FLOW in console.text


def test_a_login_finished_at_the_last_second_is_still_linked(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    late = flow_status(
        url_expired=True, polled_after_expiry=True, results={"EXCHANGED": 1}, items=1
    )
    host.flows = [status(flow_status(url_expired=True))]
    host.steady = status(late)
    clock = Clock()

    assert link(host, console, tmp_path, clock=clock, sleep=clock.sleep) == EXIT_OK
    assert "Connected." in console.text
    assert "Its account list has not been read yet" in console.text


def test_losing_the_host_is_not_reported_as_a_failed_link(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.flows = [RemoteFailure("could not reach the host (TimeoutExpired)")] * 6

    assert link(host, console, tmp_path) == EXIT_UNFINISHED
    assert "The host keeps working" in console.text
    assert f"scripts/link.sh --status {FLOW}" in console.text
    assert "failed" not in console.text.lower()


def test_an_expired_page_nothing_has_checked_since_is_never_called_unfinished(
    tmp_path: Path,
) -> None:
    """With no poll after expiry, "no login was seen" is a guess about a spent slot."""
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    host.steady = status(flow_status(url_expired=True))

    exit_code = link(host, console, tmp_path, clock=clock, sleep=clock.sleep)

    assert exit_code == EXIT_NEEDS_ATTENTION
    assert clock.elapsed >= WATCH_FOR
    assert "cannot say whether a login finished" in console.text
    assert "no finished login was seen" not in console.text
    assert "Do not run this again" in console.text and FLOW in console.lines[-1]


# --- the host must be seen polling, before the URL and while it is open ----------


@pytest.mark.parametrize("poller", [None, "pass"], ids=["nothing-polls", "only-a-pass-would"])
def test_no_url_is_shown_until_the_hosts_service_polls_the_new_request(
    tmp_path: Path, poller: str | None
) -> None:
    """`is-active` was green on a host whose worker could not start. This is not."""
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    host.poller = poller
    opened: list[str] = []

    exit_code = link(
        host, console, tmp_path, clock=clock, sleep=clock.sleep, open_url=opened.append
    )

    assert exit_code == EXIT_NEEDS_ATTENTION
    assert opened == [] and URL not in console.text
    assert "link-authorize" not in host.calls, "an unreleased request closes itself at expiry"
    # In Production the proof has to come from the service: a pass run by the
    # driver here would move the count and prove nothing about what catches
    # a login after this window is closed.
    assert not any(call.startswith("daemon") for call in host.calls)
    assert clock.elapsed >= PROVE_POLLING_FOR
    assert "Nothing can have been spent" in console.text
    assert "Running this again will not help" in console.text and FLOW in console.lines[-1]


def test_the_mints_own_poll_does_not_count_as_the_service_polling(tmp_path: Path) -> None:
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    host.poller = None
    host._polls = 7  # however many are already on record, the count has to move

    assert link(host, console, tmp_path, clock=clock, sleep=clock.sleep) == EXIT_NEEDS_ATTENTION
    assert URL not in console.text


def test_while_the_service_keeps_polling_production_never_runs_the_pass_itself(
    tmp_path: Path,
) -> None:
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    waiting = [status(flow_status(polls=n)) for n in range(2, 40)]  # one new poll per read
    done = status(flow_status(polls=40, results={"EXCHANGED": 1}, items=1, items_discovered=1))
    host.flows = [*waiting, done, done]

    assert link(host, console, tmp_path, clock=clock, sleep=clock.sleep) == EXIT_OK
    assert clock.elapsed > STALL_AFTER
    assert not any(call.startswith("daemon") for call in host.calls)
    assert "gone quiet" not in console.text


def test_a_service_that_goes_quiet_with_a_page_open_is_covered_by_this_window(
    tmp_path: Path,
) -> None:
    """The token must not expire because the one process meant to catch it stopped."""
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    quiet = status(flow_status(polls=2))
    done = status(flow_status(polls=3, results={"EXCHANGED": 1}, items=1, items_discovered=1))
    host.flows.extend([quiet] * 12 + [done, done])

    assert link(host, console, tmp_path, clock=clock, sleep=clock.sleep) == EXIT_OK
    passes = [i for i, call in enumerate(host.calls) if call.startswith("daemon link-flow")]
    watched = [i for i, call in enumerate(host.calls) if call == "link-authorize"][0]
    reads_before_first_pass = host.calls[watched : passes[0]].count(f"link-status --flow {FLOW}")
    assert reads_before_first_pass * 10 >= STALL_AFTER.total_seconds()
    assert console.text.count("gone quiet") == 1
    assert "Connected." in console.text


# --- sandbox is the same path with the Production-only steps left out ----------


def test_the_sandbox_rehearsal_asks_for_nothing_and_drives_the_worker_itself(
    tmp_path: Path,
) -> None:
    host, console = ScriptedHost(), Console(tty=False)
    host.pass_fails = True
    host.poller = "pass"  # Sandbox has no service: only a pass moves the count
    host.preflight = status(environment="sandbox")
    done = flow_status(results={"EXCHANGED": 1}, items=1, items_discovered=1)
    host.flows = [status(flow_status()), status(done), status(done)]

    assert link(host, console, tmp_path, environment=SANDBOX) == EXIT_OK
    assert console.prompts == []
    read, run_pass = f"link-status --flow {FLOW}", f"daemon link-flow --flow {FLOW}"
    assert host.calls == [
        "daemon init",  # nothing installs a Sandbox database
        "link-status",
        "link-mint",
        *(read, run_pass, read),  # the proof, driven here because nothing else will
        "link-authorize",
        *(run_pass, read, run_pass, read),
        "daemon sync",  # and no timer fetches a Sandbox Item
        read,
    ]
    assert "no account of a kind this version counts" in console.text
    assert "pair-phone" not in console.text, "pairing is Production's; Sandbox shares its key file"
    assert "user_good / pass_good" in console.text and "Keep this window open" in console.text
    assert "the host keeps working" not in console.text, "no service completes a Sandbox flow"


# --- one-shot status ----------------------------------------------------------


@pytest.mark.parametrize(
    ("flow", "code", "phrase"),
    [
        (flow_status(), EXIT_UNFINISHED, "No finished login has been seen yet"),
        (flow_status(results={"EXCHANGING": 1}), EXIT_UNFINISHED, "is exchanging it"),
        (
            flow_status(url_expired=True, polled_after_expiry=True, awaiting_closure=True),
            EXIT_UNFINISHED,
            "tell the agents to close this flow",
        ),
        (
            flow_status(request="URL_EXPIRED", closed=True, released=False),
            EXIT_UNFINISHED,
            "expired unused and is closed. Nothing was spent",
        ),
        (flow_status(results={"TOKEN_EXPIRED": 1}), EXIT_NEEDS_ATTENTION, "STOP"),
    ],
    ids=["waiting", "exchanging", "expired-released", "expired-unreleased", "attention"],
)
def test_status_says_where_a_flow_stands_without_touching_it(
    flow: dict[str, Any], code: int, phrase: str
) -> None:
    host, console = ScriptedHost(), Console()
    host.flows = [status(flow)]

    assert report_status(host, console, FLOW, environment=PRODUCTION) == code
    assert phrase in console.text
    assert host.calls == [f"link-status --flow {FLOW}"]


def test_judge_ranks_bad_news_above_a_success_beside_it() -> None:
    both = flow_status(results={"EXCHANGED": 1, "TOKEN_EXPIRED": 1})
    assert judge(both).state == "ATTENTION"
    assert judge(flow_status(results={"EXCHANGED": 1, "EXCHANGING": 1})).state == "EXCHANGING"
    assert judge(flow_status(results={"EXCHANGED": 2})).state == "LINKED"
    assert judge(flow_status(request="URL_EXPIRED")).state == "UNFINISHED"
    assert judge(flow_status(url_expired=True)).state == "WAITING"


def test_a_request_waiting_for_closure_is_not_reported_as_stray_material() -> None:
    """Every released request carries the observation once its page expires."""
    expired = {"url_expired": True, "polled_after_expiry": True, "awaiting_closure": True}
    assert judge(flow_status(**expired, results={"EXCHANGED": 1})).state == "LINKED"
    assert judge(flow_status(**expired)) == judge(
        flow_status(url_expired=True, polled_after_expiry=True)
    )
    assert judge(flow_status(**expired, unidentified=1)).state == "ATTENTION"


def test_status_of_a_linked_flow_reports_it_without_starting_anything(tmp_path: Path) -> None:
    host, console = ScriptedHost(), Console()
    done = flow_status(
        results={"EXCHANGED": 1}, items=1, items_discovered=1, accounts=2, accounts_in_snapshot=2
    )
    host.flows = [status(done), status(done, paired=True, published_latest=True)]

    assert report_status(host, console, FLOW, environment=PRODUCTION) == EXIT_OK
    assert host.calls == [f"link-status --flow {FLOW}"] * 2
    assert "2 account(s) recorded; 2 already in the total." in console.text
    assert "the next time the app is opened" in console.text


@pytest.mark.parametrize(
    ("overrides", "phrase", "absent"),
    [
        ({"awaiting_closure": 1}, "agents have not closed it out yet", "stored Link evidence"),
        ({"awaiting_closure": 1, "unattributed": 1}, "stored Link evidence", "closed it out"),
        ({}, "stored Link evidence", "closed it out"),
    ],
    ids=["awaiting-closure", "closure-and-stray-material", "unexplained"],
)
def test_a_refused_count_says_which_kind_of_refusal_it_is(
    tmp_path: Path, overrides: dict[str, Any], phrase: str, absent: str
) -> None:
    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.preflight = status(budget={"state": "UNAVAILABLE"}, **overrides)

    assert link(host, console, tmp_path) == EXIT_REFUSED
    assert phrase in console.text and absent not in console.text
    assert "Nothing was minted" in console.text and "link-mint" not in host.calls


def test_the_owner_is_told_until_when_another_institution_can_be_connected(
    tmp_path: Path,
) -> None:
    host, console, clock = ScriptedHost(), Console(CONFIRMATION), Clock()
    done = flow_status(
        results={"EXCHANGED": 1}, items=1, items_discovered=1, accounts=1, accounts_in_snapshot=1
    )
    closes = clock() + timedelta(minutes=25)
    host.flows = [status(done), status(done, mint_window_ends_at=closes.isoformat())]

    assert link(host, console, tmp_path, clock=clock, sleep=clock.sleep) == EXIT_OK
    assert f"run this again before {closes.astimezone():%H:%M}" in console.text

    host, console = ScriptedHost(), Console(CONFIRMATION)
    host.flows = [
        status(done),
        status(done, budget={"state": "UNAVAILABLE"}, awaiting_closure=1),
    ]
    assert link(host, console, tmp_path / "second") == EXIT_OK
    assert "run this again before" not in console.text
    assert "once the agents have closed this one out" in console.text


# --- the host verbs ------------------------------------------------------------


class Provider:
    """Every provider call the path makes. Synthetic, and it counts."""

    def __init__(self) -> None:
        self.mints = 0
        self.poll = LinkSessionPoll(LinkSessionShape.SESSIONS_ABSENT, ())
        self.calls: list[str] = []

    def link_token_create_hosted(self, **kwargs: Any) -> HostedLinkToken:
        self.mints += 1
        assert kwargs["url_lifetime_seconds"] == 1800
        return HostedLinkToken(LINK_TOKEN, URL, datetime.now(UTC) + timedelta(minutes=30), 1800)

    def link_token_get(self, token: str) -> LinkSessionPoll:
        assert token == LINK_TOKEN
        return self.poll

    def item_public_token_exchange(self, token: str) -> ExchangedItem:
        self.calls.append("exchange")
        return ExchangedItem(ACCESS_TOKEN, "synthetic-item", "syntheticrequest")

    def item_institution(
        self, token: str, *, expected_item_id: str, country_codes: Sequence[str]
    ) -> ItemInstitution:
        return ItemInstitution(expected_item_id, "synthetic-institution", "Synthetic", False)

    def item_get(self, access_token: str) -> ItemStatus:
        assert access_token == ACCESS_TOKEN
        return ItemStatus("synthetic-item", HEALTHY, "syntheticrequest", True, datetime.now(UTC))

    def list_accounts(self, access_token: str) -> tuple[AccountDescriptor, ...]:
        self.calls.append("list_accounts")
        return (
            AccountDescriptor(
                "account-brokerage", "Synthetic", None, None, "investment", None, "USD"
            ),
            AccountDescriptor("account-cash", "Synthetic", None, None, "depository", None, "USD"),
            AccountDescriptor("account-card", "Synthetic", None, None, "credit", None, "USD"),
        )

    def _balances(self) -> tuple[BalanceRecord, ...]:
        return (
            BalanceRecord("account-brokerage", Decimal("1000.25"), "USD", None),
            BalanceRecord("account-cash", Decimal("234.50"), "USD", None),
        )

    def fetch_cached_balances(self, access_token: str) -> tuple[BalanceRecord, ...]:
        return self._balances()

    def fetch_holdings(self, access_token: str) -> InvestmentRecords:
        return InvestmentRecords(
            accounts=self._balances(),
            holdings=(
                HoldingRecord(
                    "account-brokerage", Decimal("1000.25"), "USD", None, date(2026, 10, 2)
                ),
            ),
        )


@pytest.fixture
def host_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An installed Production host, in a temporary directory, with a synthetic provider."""
    paths = paths_for(PRODUCTION, secrets_dir=tmp_path / "etc", data_dir=tmp_path / "home" / "data")
    provider = Provider()
    monkeypatch.setenv("NETWORTH_ENV", "production")
    credentials = PlaidCredentials("synthetic", "synthetic", PRODUCTION)
    for module in (link_mint, link_authorize, link_status, runtime):
        monkeypatch.setattr(module, "paths_for", lambda _: paths)
    for module in (link_mint, runtime):
        monkeypatch.setattr(module, "load_credentials", lambda _: credentials)
        monkeypatch.setattr(module, "PlaidClient", lambda _: provider)
    assert runtime.run(argparse.Namespace(job="init", flow=None, country_code=None)) == 0
    return paths, provider


@contextlib.contextmanager
def piped(material: str = "") -> Iterator[list[str]]:
    """Give a verb real pipes on stdin and stdout, as sshd does, and collect its output."""
    out_read, out_write = os.pipe()
    in_read, in_write = os.pipe()
    os.write(in_write, material.encode())
    os.close(in_write)
    lines: list[str] = []
    saved = sys.stdin, sys.stdout
    sys.stdin, sys.stdout = os.fdopen(in_read), os.fdopen(out_write, "w")
    try:
        yield lines
    finally:
        sys.stdout.close()
        sys.stdin.close()
        sys.stdin, sys.stdout = saved
        with os.fdopen(out_read) as reader:
            lines.extend(reader.read().splitlines())


def verb(*argv: str, material: str = "") -> tuple[int, list[str]]:
    with piped(material) as lines:
        code = cli.main(list(argv))
    return code, lines


def test_link_mint_refuses_a_caller_that_names_another_environment(
    host_paths: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rehearsal meant for Sandbox that arrives in Production must mint nothing."""
    paths, provider = host_paths

    code, lines = verb("link-mint", "--environment", "sandbox")

    assert code == 2 and lines == [] and provider.mints == 0
    assert "asked for 'sandbox'" in capsys.readouterr().err
    with sqlite3.connect(paths.database) as db:
        assert db.execute("SELECT count(*) FROM link_request").fetchone() == (0,)


def test_link_mint_refuses_to_write_its_payload_to_anything_but_a_pipe(
    host_paths: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, provider = host_paths
    with (tmp_path / "captured.txt").open("w") as not_a_pipe:
        monkeypatch.setattr(sys, "stdout", not_a_pipe)
        code = cli.main(["link-mint", "--environment", "production"])
    assert code == 2 and provider.mints == 0
    assert (tmp_path / "captured.txt").read_text() == ""


def test_link_mint_yields_to_a_deployment_holding_the_lock_and_mints_nothing(
    host_paths: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    paths, provider = host_paths
    lock = link_mint.deploy_lock_path(paths)
    assert lock == paths.database.parent.parent / ".runtime-deploy.lock"

    # Another process holds it: flock is per open file description, so a second
    # descriptor in this process contends exactly as the installer's would.
    import fcntl

    held = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(held, fcntl.LOCK_EX)
    try:
        code, lines = verb("link-mint", "--environment", "production")
    finally:
        os.close(held)

    assert code == 2 and lines == [] and provider.mints == 0
    assert "another operation holds" in capsys.readouterr().err


def test_link_mint_persists_the_request_and_releases_the_lock(host_paths: Any) -> None:
    paths, provider = host_paths

    code, lines = verb("link-mint", "--environment", "production")

    assert code == 0 and provider.mints == 1
    (wire,) = lines
    mint = MintResult.from_wire(wire)
    assert mint.hosted_link_url == URL and mint.link_token.reveal() == LINK_TOKEN
    with sqlite3.connect(paths.database) as db:
        assert db.execute(
            "SELECT flow_id, state, url_release_authorized_at FROM link_request"
        ).fetchall() == [(mint.flow_id, "URL_MINTED", None)]
        dump = "\n".join(db.iterdump())
    assert LINK_TOKEN not in dump and URL not in dump
    with exclusive_file_lock(link_mint.deploy_lock_path(paths), blocking=False, reentrant=False):
        pass  # not still held


def test_an_unexpected_failure_is_reported_by_type_and_never_by_text(
    host_paths: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def explode(*_: Any, **__: Any) -> Any:
        raise RuntimeError("synthetic-private-provider-reply")

    monkeypatch.setattr(link_mint, "mint_request", explode)

    code, lines = verb("link-mint", "--environment", "production")

    err = capsys.readouterr().err
    assert code == 2 and lines == []
    assert "RuntimeError (detail withheld)" in err
    assert "synthetic-private-provider-reply" not in err


def test_link_status_carries_states_and_counts_and_no_identity(host_paths: Any) -> None:
    code, lines = verb("link-status", "--environment", "production")
    assert code == 0
    document = link_status.from_wire(lines[0])
    assert document == status()

    _, minted = verb("link-mint", "--environment", "production")
    flow = MintResult.from_wire(minted[0]).flow_id
    code, lines = verb("link-status", "--environment", "production", "--flow", flow)

    document = link_status.from_wire(lines[0])
    assert document["pending_requests"] == 1
    assert document["flow"] == flow_status(released=False)
    assert LINK_TOKEN not in lines[0] and URL not in lines[0] and flow not in lines[0]
    assert verb("link-status", "--environment", "production", "--flow", "not-a-flow")[0] == 2
    unknown = verb("link-status", "--environment", "production", "--flow", "0" * 32)[1]
    assert link_status.from_wire(unknown[0])["flow"] == {"known": False}


# --- what the real lifecycle reports, read by the real judge --------------------
#
# The scripted statuses above are this file's own idea of what a host says. These
# run the installed verbs and the lifecycle worker and hand the driver whatever
# comes back, because the first version of `judge` was written against statuses
# the host never produces: it called every expired request stray material.


def host_status(flow: str | None = None) -> dict[str, Any]:
    arguments = () if flow is None else ("--flow", flow)
    code, lines = verb("link-status", "--environment", "production", *arguments)
    assert code == 0
    return link_status.from_wire(lines[0])


def mint_on_host(tmp_path: Path, *, release: bool) -> str:
    _, minted = verb("link-mint", "--environment", "production")
    mint = MintResult.from_wire(minted[0])
    if release:
        now = datetime.now(UTC)
        record = link_recovery.store_and_verify(
            tmp_path / "recovery",
            mint.as_record(now=now, automatic=True),
            holder=REQUIRED_HOLDER,
            now=now,
        )
        material = record.to_json()
        assert verb("link-authorize", "--environment", "production", material=material)[0] == 0
    return mint.flow_id


def service_pass(flow: str) -> int:
    """One scan of `networth daemon link`, for one pending flow."""
    return runtime.run(argparse.Namespace(job="link-flow", flow=flow, country_code=None))


def expire_page(paths: Any, flow: str) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(paths.database) as db:
        db.execute(
            "UPDATE link_request SET hosted_url_expires_at = ? WHERE flow_id = ?", (past, flow)
        )


def test_the_poll_count_tells_the_mints_own_poll_from_a_later_one(
    host_paths: Any, tmp_path: Path
) -> None:
    _, provider = host_paths
    flow = mint_on_host(tmp_path, release=False)
    assert host_status(flow)["flow"]["polls"] == 1, "the mint polls once itself"

    assert service_pass(flow) == 0
    assert host_status(flow)["flow"]["polls"] == 2

    def unreachable(_: str) -> LinkSessionPoll:
        raise OSError("synthetic outage")

    provider.link_token_get = unreachable
    service_pass(flow)
    after = host_status(flow)["flow"]
    assert after["polls"] == 2 and after["poll_failing"], "a failed poll is not a poll"


def test_an_abandoned_page_ends_unfinished_and_holds_the_next_mint_for_an_agent(
    host_paths: Any, tmp_path: Path
) -> None:
    paths, provider = host_paths
    flow = mint_on_host(tmp_path, release=True)
    with sqlite3.connect(paths.database) as db:
        (expires,) = db.execute("SELECT hosted_url_expires_at FROM link_request").fetchone()
    assert host_status()["mint_window_ends_at"] == expires

    expire_page(paths, flow)
    service_pass(flow)

    document = host_status(flow)
    assert document["flow"]["awaiting_closure"] is True
    assert document["flow"]["unidentified"] == 0 and document["flow"]["holds"] == 0
    assert judge(document["flow"]) == judge(flow_status(url_expired=True, polled_after_expiry=True))
    assert judge(document["flow"]).state == "UNFINISHED"
    assert document["budget"] == {"state": "UNAVAILABLE"}
    assert (document["awaiting_closure"], document["unattributed"]) == (1, 0)
    assert document["mint_window_ends_at"] is None, "it is refusing now, not later"

    # And the owner, running the command again, is told which refusal this is.
    console = Console(CONFIRMATION)
    exit_code = run_link(
        InstalledHost(provider),
        console,
        environment=PRODUCTION,
        directory=tmp_path / "recovery",
        verify_holder=lambda: REQUIRED_HOLDER,
    )
    assert exit_code == EXIT_REFUSED and provider.mints == 1
    assert "agents have not closed it out yet" in console.text
    assert not any(character.isdigit() for character in console.text), "a refusal, not a number"


def test_a_finished_link_is_still_linked_after_its_page_expires(
    host_paths: Any, tmp_path: Path
) -> None:
    paths, provider = host_paths
    flow = mint_on_host(tmp_path, release=True)
    now = datetime.now(UTC)
    provider.poll = LinkSessionPoll(
        LinkSessionShape.ITEM_ADDED,
        (LinkSessionRecord("synthetic-session", now, now, ("synthetic-public",), 1),),
    )
    assert service_pass(flow) == 0
    assert judge(host_status(flow)["flow"]).state == "LINKED"

    expire_page(paths, flow)
    service_pass(flow)

    document = host_status(flow)
    assert document["flow"]["awaiting_closure"] is True
    assert judge(document["flow"]).state == "LINKED"
    assert document["budget"] == {"state": "UNAVAILABLE"}


def test_a_request_that_was_never_released_closes_itself_and_costs_nothing(
    host_paths: Any, tmp_path: Path
) -> None:
    """What a refused polling proof leaves behind: nothing an agent has to clear."""
    paths, _ = host_paths
    flow = mint_on_host(tmp_path, release=False)
    assert host_status()["mint_window_ends_at"] is None

    expire_page(paths, flow)
    assert service_pass(flow) == 0

    document = host_status(flow)
    verdict = judge(document["flow"])
    assert (verdict.state, verdict.detail) == ("UNFINISHED", "closed")
    assert document["flow"]["awaiting_closure"] is False
    assert document["budget"]["state"] == "AVAILABLE" and document["pending_requests"] == 0


# --- the whole path, once ------------------------------------------------------


class InstalledHost:
    """The real verbs and the real daemon jobs, run in-process as the host runs them."""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider
        self.calls: list[str] = []
        self.service_passes = 0
        self.service_passes_at_release: int | None = None

    def verb(
        self, name: str, *arguments: str, material: str = "", stated: bool = True
    ) -> list[str]:
        self.calls.append(name)
        if name == "link-authorize":
            self.service_passes_at_release = self.service_passes
        argv = [name, *(["--environment", "production"] if stated else []), *arguments]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code, lines = verb(*argv, material=material)
        if code != 0:
            raise RemoteFailure(err.getvalue().strip() or f"{name} exited {code}")
        return lines

    def inactive_units(self, units: Sequence[str]) -> list[str]:
        return []

    def start_sync(self) -> None:
        self.calls.append("start-sync")
        assert runtime.run(argparse.Namespace(job="sync", flow=None, country_code=None)) == 0


def test_the_owner_links_one_institution_and_its_accounts_reach_the_total(
    host_paths: Any, tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    paths, provider = host_paths
    host = InstalledHost(provider)
    console = Console(CONFIRMATION)
    seen: dict[str, Any] = {}

    def the_other_two_machines(_: float) -> None:
        """What the host's link service and the owner do while the driver sleeps."""
        flows = [p.stem for p in (tmp_path / "recovery").glob("*.json")]
        if not flows:
            return
        if URL in console.text and "logged_in" not in seen:
            seen["logged_in"] = True
            now = datetime.now(UTC)
            provider.poll = LinkSessionPoll(
                LinkSessionShape.ITEM_ADDED,
                (LinkSessionRecord("synthetic-session", now, now, ("synthetic-public",), 1),),
            )
        # One scan of `networth daemon link`: a pass for the pending flow.
        job = argparse.Namespace(job="link-flow", flow=flows[0], country_code=None)
        assert runtime.run(job) == 0
        host.service_passes += 1

    def canary() -> None:
        seen["mints_at_canary"] = provider.mints

    def protect() -> None:
        seen["exchanged_at_protect"] = "exchange" in provider.calls

    exit_code = run_link(
        host,
        console,
        environment=PRODUCTION,
        directory=tmp_path / "recovery",
        verify_holder=lambda: REQUIRED_HOLDER,
        canary=canary,
        protect=protect,
        sleep=the_other_two_machines,
    )

    assert exit_code == EXIT_OK, console.text
    assert seen["mints_at_canary"] == 0 and seen["exchanged_at_protect"] is True
    assert provider.mints == 1 and provider.calls.count("exchange") == 1
    # The release waited for a poll the service made, not the one the mint made.
    released = host.calls.index("link-authorize")
    assert host.calls[:2] == ["link-status", "link-mint"]
    assert set(host.calls[2:released]) == {"link-status"}
    assert host.service_passes_at_release == 1
    assert "daemon" not in host.calls, "production leaves the exchange to the service"

    # The owner saw the URL once, the cost before it, and the result after it.
    assert console.text.count(URL) == 1
    assert console.text.index("0 of 10 lifetime") < console.text.index(URL)
    assert "2 account(s) recorded; 2 already in the total." in console.text
    assert "1 of 10 lifetime bank connections used, 9 left." in console.text
    assert "No phone is paired yet" in console.text
    for secret in (LINK_TOKEN, ACCESS_TOKEN):
        assert secret not in console.text
    assert ACCESS_TOKEN not in "".join(capfd.readouterr())

    # And the host holds what that report claims: the credential, two asset
    # accounts (not the card), and a snapshot that sums them.
    with sqlite3.connect(paths.database) as db:
        assert db.execute(
            "SELECT status, accounts_discovered_at IS NOT NULL FROM item"
        ).fetchall() == [("HEALTHY", 1)]
        assert db.execute("SELECT type FROM account ORDER BY id").fetchall() == [
            ("investment",),
            ("depository",),
        ]
        assert db.execute(
            "SELECT total_net_worth_minor, account_count FROM snapshot ORDER BY id DESC LIMIT 1"
        ).fetchone() == (123_475, 2)
        assert db.execute("SELECT state FROM link_result").fetchall() == [("EXCHANGED",)]


# --- the two scripts the owner actually types ----------------------------------

REPOSITORY = Path(__file__).resolve().parent.parent


def run_script(
    name: str, *arguments: str, tmp_path: Path, cwd: Path | None = None
) -> tuple[int, str, str]:
    """Run a script with a stub `uv` and `ssh` first on PATH, recording their argv."""
    import subprocess

    stubs = tmp_path / "stubs"
    stubs.mkdir(exist_ok=True)
    for tool in ("uv", "ssh"):
        stub = stubs / tool
        stub.write_text(f'#!/bin/sh\nprintf "{tool}:%s\\n" "$*"\n')
        stub.chmod(0o755)
    result = subprocess.run(
        [str(REPOSITORY / "scripts" / name), *arguments],
        capture_output=True,
        text=True,
        check=False,
        cwd=cwd or tmp_path,
        stdin=subprocess.DEVNULL,
        env={**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"},
    )
    return result.returncode, result.stdout, result.stderr


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ((), "owner-link --environment production"),
        (("--sandbox",), "owner-link --environment sandbox"),
        (("--status", FLOW), f"owner-link --environment production --status {FLOW}"),
        (
            ("--sandbox", "--resume", FLOW, "--no-open"),
            f"owner-link --environment sandbox --resume {FLOW} --no-open",
        ),
    ],
    ids=["default-is-production", "sandbox", "status", "sandbox-resume"],
)
def test_link_sh_selects_the_environment_and_runs_from_any_directory(
    tmp_path: Path, arguments: tuple[str, ...], expected: str
) -> None:
    code, out, err = run_script("link.sh", *arguments, tmp_path=tmp_path)

    assert (code, err) == (0, "")
    assert out == f"uv:run --quiet networth {expected}\n"


@pytest.mark.parametrize(
    "arguments",
    [("--production",), ("--resume",), ("extra",), ("--environment", "sandbox")],
    ids=["unknown-flag", "resume-without-flow", "positional", "environment-is-not-a-flag"],
)
def test_link_sh_refuses_what_it_does_not_recognise_before_running_anything(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    code, out, err = run_script("link.sh", *arguments, tmp_path=tmp_path)

    assert code == 2 and out == ""
    assert err.startswith("link: ")


def test_pair_phone_refuses_to_print_the_pairing_secret_without_a_terminal(
    tmp_path: Path,
) -> None:
    """An agent session has no terminal, and its transcript would keep the payload key."""
    code, out, err = run_script("pair-phone.sh", tmp_path=tmp_path)

    assert code == 2 and out == ""
    assert "in a terminal" in err
    assert run_script("pair-phone.sh", "--sandbox", tmp_path=tmp_path)[0] == 2
