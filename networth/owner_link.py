"""The owner's Link, driven from ``zelengs-macbook-air-2`` (task 08).

This is the only path that spends a lifetime Item slot (**F2**), and it is run by
the owner: he types bank credentials and MFA into Plaid's page, which no agent
may do or automate (``AGENTS.md`` rule 3). Everything on either side of that one
step is here.

**What runs where.** The Plaid secret lives on the VPS and never leaves it, so
the mint, the exchange and every later fetch happen there. This Mac holds an SSH
key, the second copy of the recovery record (§4), and the browser. The driver
therefore does four things itself — check, write the copy, show the URL, watch —
and asks the host for the rest through three verbs written for it
(``link-mint``, ``link-authorize``, ``link-status``).

**The order is the design, and each step refuses for free** (**F2a**: the slot
is spent when Link *succeeds*, so nothing before the URL is shown can cost one):

1. this machine is the one the second copy belongs on;
2. the host's daemon is running — a finished Link is exchangeable for 30 minutes
   and the thing that exchanges it must already be up;
3. the host can account for its slots and has one left;
4. the backup path works, so the credential about to exist will not live on one
   disk;
5. the owner confirms, **before** the URL exists rather than before the exchange
   — after Link succeeds there is nothing left to decline;
6. mint → second copy written, ``fsync``ed, read back → **the host's service is
   seen polling the new request** → the host acknowledges the release → **only
   then** is the URL shown.

**Step 6's middle term is measured, because the proxy for it lied.** Step 2 asks
``systemctl`` whether the link service is active. On 2026-10-10 it was, on a host
whose worker could not start: every child the service spawned died at import, the
unit stayed ``active`` and its log line stayed healthy. A login finished against
that host would never have been polled, and its token would have died at thirty
minutes with the slot spent. So the driver reads the property itself — the
request's count of successful polls must move past the one the mint made — and
shows no URL until it does. That refusal is free; the alternative was not.

**The exchange is the host's job, and this process is only its understudy.** The
owner's laptop lid can close. ``networth-link.service`` polls and exchanges
whether or not this driver is still running, and while it does the driver only
reads status and reports. If the service's polls stop while a page is open, the
driver runs the same worker pass itself, behind the same per-request lock, and
says so — a token must not expire because the one process meant to catch it went
quiet while a second one sat watching.

**What it reports as failure, and what it refuses to guess.** A URL that expires
with no finished login most likely spent nothing — but the host deliberately
does not assume so (``link_lifecycle``: "provider absence never proves an
exposed request spent zero slots"), and neither does this. It says what was
observed and that the request needs closing before the next Link.
"""

from __future__ import annotations

import contextlib
import shlex
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from networth import link_recovery, mac_identity
from networth.commands.authorize_automatic_link import ACK
from networth.commands.link_status import STATUS_WIRE_MARKER, from_wire
from networth.link_recovery import MINT_WIRE_MARKER, MintResult, RecoveryRecord
from networth.plaid.environment import PlaidEnvironment

VPS = "root@100.102.245.37"
SSH_KEY = Path.home() / "agents" / "secrets" / "networth-vps.key"
RUNTIME = "/usr/local/bin/networth"
SERVICE_USER = "networth"
SERVICE_HOME = "/var/lib/networth"

#: What must be running before a URL may exist. The link service exchanges; the
#: sync timer is what turns the new Item into figures; serve and publish are how
#: they reach the phone.
REQUIRED_UNITS = (
    "networth-link.service",
    "networth-serve.service",
    "networth-sync.timer",
    "networth-publish.timer",
)

CONFIRMATION = "LINK"
POLL_SECONDS = 10.0
#: How long the host's service gets to poll a new request before the URL is
#: withheld. It scans every 30 seconds; four scans is not a slow host.
PROVE_POLLING_FOR = timedelta(minutes=2)
PROVE_POLLING_EVERY = 5.0
#: A page is open and the request has gone this long without a successful poll:
#: two missed scans and a pass. From here the driver runs the pass itself.
STALL_AFTER = timedelta(seconds=90)
#: 30 minutes of URL lifetime plus the exchange window's slack. A display bound,
#: not a deadline: the host keeps working after this driver stops watching.
WATCH_FOR = timedelta(minutes=40)
SETTLE_FOR = timedelta(minutes=5)

EXIT_OK = 0
EXIT_REFUSED = 2  # no URL was shown, and running it again is safe
EXIT_UNFINISHED = 4  # a URL was shown and no login finished while we watched
EXIT_NEEDS_ATTENTION = 5  # an agent has to look before the owner tries again


class RemoteFailure(RuntimeError):
    """A host-side step did not succeed. The message is safe to show."""


class NotPolling(RemoteFailure):
    """The host did not poll a request it holds. Running again will not help."""


class Remote(Protocol):
    def verb(
        self, name: str, *arguments: str, material: str = "", stated: bool = True
    ) -> list[str]: ...

    def inactive_units(self, units: Sequence[str]) -> list[str]: ...

    def start_sync(self) -> None: ...


class Console(Protocol):
    def say(self, line: str = "") -> None: ...

    def ask(self, prompt: str) -> str: ...

    def interactive(self) -> bool: ...


def _own_lines(stderr: str, verb: str) -> str:
    """Only the line our own verb wrote. Everything else on stderr is not ours to relay."""
    prefix = f"{verb} stopped:"
    return " ".join(line.strip() for line in stderr.splitlines() if line.startswith(prefix))


class SshRemote:
    """The installed runtime on the VPS, as the service user, over the root SSH key."""

    def __init__(
        self,
        environment: PlaidEnvironment,
        *,
        run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._environment = environment
        self._run = run

    def _ssh(self, command: str, *, material: str = "", timeout: float) -> Any:
        try:
            return self._run(
                [
                    "ssh",
                    "-i",
                    str(SSH_KEY),
                    "-o",
                    "IdentitiesOnly=yes",
                    "-o",
                    "BatchMode=yes",
                    "-o",
                    "ConnectTimeout=15",
                    VPS,
                    command,
                ],
                input=material,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemoteFailure(f"could not reach the host ({type(exc).__name__})") from None

    def verb(
        self, name: str, *arguments: str, material: str = "", stated: bool = True
    ) -> list[str]:
        argv = [
            "runuser",
            "-u",
            SERVICE_USER,
            "--",
            "env",
            f"HOME={SERVICE_HOME}",
            f"NETWORTH_ENV={self._environment.value}",
            RUNTIME,
            name,
        ]
        if stated:
            argv += ["--environment", self._environment.value]
        result = self._ssh(shlex.join([*argv, *arguments]), material=material, timeout=180)
        if result.returncode != 0:
            raise RemoteFailure(
                _own_lines(result.stderr, name)
                or f"{name} did not succeed on the host (exit {result.returncode})"
            )
        return list(result.stdout.splitlines())

    def inactive_units(self, units: Sequence[str]) -> list[str]:
        # `is-active` prints one word per unit and exits non-zero if any is not
        # active, so the words are read rather than the status.
        result = self._ssh(shlex.join(["systemctl", "is-active", *units]), timeout=30)
        words = result.stdout.split()
        if len(words) != len(units):
            raise RemoteFailure("the host did not report its units")
        return [unit for unit, word in zip(units, words, strict=True) if word != "active"]

    def start_sync(self) -> None:
        # `--no-block`: the unit is a oneshot that can run for minutes, and an
        # already-running one will fetch the new Item on its next activation.
        self._ssh(
            shlex.join(["systemctl", "start", "--no-block", "networth-sync.service"]), timeout=30
        )


def read_status(remote: Remote, flow_id: str | None = None) -> dict[str, Any]:
    arguments = () if flow_id is None else ("--flow", flow_id)
    lines = [
        line
        for line in remote.verb("link-status", *arguments)
        if line.startswith(STATUS_WIRE_MARKER)
    ]
    if len(lines) != 1:
        raise RemoteFailure("the host did not return one status line")
    try:
        return from_wire(lines[0])
    except ValueError:
        raise RemoteFailure("the host returned a status this driver cannot read") from None


@dataclass(frozen=True, slots=True)
class Verdict:
    """What one status read means for a watching driver."""

    state: str  # WAITING | EXCHANGING | LINKED | UNFINISHED | ATTENTION
    detail: str = ""


def judge(flow: dict[str, Any]) -> Verdict:
    """Read a flow's status. Bad news first: it outranks every other state."""
    if not flow.get("known"):
        return Verdict("ATTENTION", "the host has no record of this flow")
    results = flow["results"]
    lost = [state for state in ("TOKEN_EXPIRED", "EXCHANGE_UNCERTAIN") if results[state]]
    if lost:
        return Verdict("ATTENTION", f"a finished login is recorded as {' and '.join(lost)}")
    # `awaiting_closure` is deliberately not read here. Every released request
    # carries it once its page expires, a finished one included.
    if flow["holds"] or flow["unidentified"]:
        return Verdict("ATTENTION", "the host is holding material it could not attribute")
    if results["SUCCESS_PENDING_EXCHANGE"] or results["EXCHANGING"]:
        return Verdict("EXCHANGING")
    if results["EXCHANGED"]:
        return Verdict("LINKED")
    if flow["request"] != "URL_MINTED" or flow["closed"]:
        return Verdict("UNFINISHED", "closed")
    # An expired URL alone proves nothing yet: a login finished in its last
    # seconds is visible only to a poll made after it. Wait for that poll.
    if flow["url_expired"] and flow["polled_after_expiry"]:
        return Verdict("UNFINISHED")
    return Verdict("WAITING")


def _flow(remote: Remote, flow_id: str) -> dict[str, Any]:
    flow: dict[str, Any] = read_status(remote, flow_id)["flow"]
    if not flow.get("known"):
        raise RemoteFailure("the host has no record of the request it was asked about")
    return flow


def _pass(remote: Remote, flow_id: str) -> None:
    """Run the worker pass the link service runs, for this one request.

    Safe beside the service: the pass takes the request's own lock without
    waiting, so whichever arrives second does nothing. A pass that records a
    failure exits non-zero, and that is the status read's to report, not this
    call's.
    """
    with contextlib.suppress(RemoteFailure):
        remote.verb("daemon", "link-flow", "--flow", flow_id, stated=False)


def _prove_polling(
    remote: Remote,
    flow_id: str,
    production: bool,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> None:
    """Return once the host has polled this request again; raise if it does not.

    The mint polls once itself, so a count of one proves nothing about the
    service. Sandbox has no service, and there this proves the worker verb runs
    on the host, which is the half of the claim a rehearsal can reach.
    """
    seen = _flow(remote, flow_id)["polls"]
    deadline = clock() + PROVE_POLLING_FOR
    while True:
        if not production:
            _pass(remote, flow_id)
        if _flow(remote, flow_id)["polls"] > seen:
            return
        if clock() >= deadline:
            raise NotPolling("the host did not check the new request")
        sleep(PROVE_POLLING_EVERY)


def _mint(remote: Remote, directory: Path, holder: str, now: datetime) -> RecoveryRecord:
    payloads = [line for line in remote.verb("link-mint") if line.startswith(MINT_WIRE_MARKER)]
    if len(payloads) != 1:
        raise RemoteFailure("the host did not return exactly one mint payload")
    mint = MintResult.from_wire(payloads[0])
    return link_recovery.store_and_verify(
        directory, mint.as_record(now=now, automatic=True), holder=holder, now=now
    )


def _release(remote: Remote, record: RecoveryRecord) -> None:
    lines = remote.verb("link-authorize", material=record.to_json())
    if [line for line in lines if line.startswith(ACK)] != [ACK + record.flow_id]:
        raise RemoteFailure("the host did not acknowledge the release for this flow")


def _slots(budget: dict[str, Any]) -> str:
    return (
        f"{budget['spent']} of {budget['capacity']} lifetime bank connections used, "
        f"{budget['remaining']} left"
    )


def run_link(
    remote: Remote,
    console: Console,
    *,
    environment: PlaidEnvironment,
    directory: Path,
    resume: str | None = None,
    watch: bool = True,
    verify_holder: Callable[[], str] = mac_identity.verify,
    canary: Callable[[], None] | None = None,
    protect: Callable[[], None] | None = None,
    open_url: Callable[[str], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run one Link from this Mac. Returns a process exit code.

    ``canary`` and ``protect`` are the two backup steps, injected so that a
    Sandbox rehearsal (which has no production archive to pull) omits them
    rather than faking them. ``canary`` runs before the mint and a failure
    refuses it; ``protect`` runs after an exchange and a failure is reported
    loudly without un-saying the Link, which has already happened.
    """
    production = environment is PlaidEnvironment.PRODUCTION
    try:
        holder = verify_holder()
    except mac_identity.WrongHost as exc:
        console.say(f"Refused: {exc}. Nothing was minted.")
        return EXIT_REFUSED

    try:
        if production:
            down = remote.inactive_units(REQUIRED_UNITS)
            if down:
                console.say(
                    f"Refused: not running on the host: {', '.join(down)}. A finished login "
                    "must be exchanged within 30 minutes, so the daemon has to be up first. "
                    "Nothing was minted."
                )
                return EXIT_REFUSED
        else:
            # Nothing installs a Sandbox database. The daemon's own `init`
            # creates it, and checks the Sandbox credential file while it does.
            remote.verb("daemon", "init", stated=False)

        preflight = read_status(remote)
        budget = preflight["budget"]
        if budget["state"] != "AVAILABLE":
            if preflight["awaiting_closure"] and not preflight["unattributed"]:
                console.say(
                    "Refused: an earlier connection's page has expired and the agents have not "
                    "closed it out yet. Until they do the host will not count its lifetime "
                    "connections, so it will not spend one. Nothing was minted. Tell the agents; "
                    "it takes them a few minutes."
                )
            else:
                console.say(
                    "Refused: the host cannot account for its lifetime connections from its own "
                    "records, so it will not report a number or spend one. Nothing was minted. "
                    "This needs an agent to look at the stored Link evidence."
                )
            return EXIT_REFUSED
        console.say(f"Environment: {environment.value}. {_slots(budget)}.")
        if budget["remaining"] <= 0:
            console.say("Refused: no lifetime connection is left. Nothing was minted.")
            return EXIT_REFUSED

        if resume is None and canary is not None:
            console.say("Checking the backup path before creating anything...")
            try:
                canary()
            except Exception as exc:
                console.say(
                    f"Refused: the backup check failed ({type(exc).__name__}). A new bank "
                    "connection's credential would exist on one disk only. Nothing was minted."
                )
                return EXIT_REFUSED

        if production:
            console.say()
            console.say("You are about to connect ONE institution.")
            console.say("  - Have its login and your phone (for the code) ready now.")
            console.say(
                "  - The connection is spent when you finish logging in on the bank page — "
                "not before — and it cannot be undone or reused."
            )
            console.say("  - Once the page opens you have 30 minutes to finish.")
            if not console.interactive():
                console.say("Refused: this must be run by the owner at a terminal.")
                return EXIT_REFUSED
            if console.ask(f"Type {CONFIRMATION} to continue: ").strip() != CONFIRMATION:
                console.say("Stopped. Nothing was minted.")
                return EXIT_REFUSED

        now = clock()
        if resume is None:
            record = _mint(remote, directory, holder, now)
        else:
            record = link_recovery.verify_for_resume(directory, resume, holder=holder, now=now)
        console.say("Checking that the host is watching for your login (up to two minutes)...")
        _prove_polling(remote, record.flow_id, production, clock, sleep)
        _release(remote, record)
    except NotPolling as exc:
        console.say(f"Stopped before any page was shown: {exc}.")
        console.say(
            "A login finished now would not be picked up, so no page will be shown. Nothing "
            "can have been spent. Running this again will not help: tell the agents the host "
            f"is not checking flow {record.flow_id}."
        )
        return EXIT_NEEDS_ATTENTION
    except (RemoteFailure, link_recovery.LinkRecoveryError, OSError) as exc:
        # Every failure above lands before a URL is shown, which is the whole
        # obligation: with no URL on screen no slot can be spent (F2a).
        console.say(f"Stopped before any page was shown: {exc}")
        console.say("Nothing can have been spent. It is safe to run this again.")
        return EXIT_REFUSED

    assert record.hosted_url is not None
    url = record.hosted_url.reveal()
    # The page's lifetime runs from the mint, and the check above used some of it.
    closes = record.minted_at + timedelta(seconds=record.url_lifetime_seconds or 1800)
    console.say()
    console.say(f"Open this page and log in to ONE institution before {closes.astimezone():%H:%M}:")
    console.say()
    console.say(f"  {url}")
    console.say()
    if production:
        console.say(f"(If this window closes, the host keeps working. Flow: {record.flow_id})")
    else:
        console.say(
            "This is the rehearsal: choose any institution and log in as user_good / "
            "pass_good. Nothing real is connected and nothing is spent."
        )
        console.say(
            f"(Keep this window open: in the rehearsal it does the host's checking. "
            f"Flow: {record.flow_id})"
        )
    if open_url is not None:
        open_url(url)
    if not watch:
        return EXIT_OK

    return _watch(remote, console, record.flow_id, production, protect, clock, sleep)


def report_status(
    remote: Remote,
    console: Console,
    flow_id: str,
    *,
    environment: PlaidEnvironment,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Say where one flow stands, without minting, releasing or waiting on a browser."""
    try:
        status = read_status(remote, flow_id)
    except RemoteFailure as exc:
        console.say(f"Could not read the host: {exc}")
        return EXIT_REFUSED
    verdict = judge(status["flow"])
    if verdict.state == "LINKED":
        production = environment is PlaidEnvironment.PRODUCTION
        return _settle(remote, console, flow_id, production, None, clock, sleep, kick=False)
    unfinished = (
        "This request expired unused and is closed. Nothing was spent."
        if verdict.detail == "closed"
        else "The page expired and no finished login was seen. Before linking again, tell "
        "the agents to close this flow."
    )
    console.say(
        {
            "WAITING": "No finished login has been seen yet; the page may still be open.",
            "EXCHANGING": "A login finished and the host is exchanging it.",
            "UNFINISHED": unfinished,
            "ATTENTION": f"STOP: {verdict.detail}. Do not link again; tell the agents.",
        }[verdict.state]
    )
    return {
        "WAITING": EXIT_UNFINISHED,
        "EXCHANGING": EXIT_UNFINISHED,
        "UNFINISHED": EXIT_UNFINISHED,
        "ATTENTION": EXIT_NEEDS_ATTENTION,
    }[verdict.state]


def _watch(
    remote: Remote,
    console: Console,
    flow_id: str,
    production: bool,
    protect: Callable[[], None] | None,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
) -> int:
    console.say("Waiting for you to finish in the browser...")
    polled_at = clock()
    deadline = polled_at + WATCH_FOR
    polls: int | None = None
    # No service completes Sandbox flows, so the rehearsal runs the pass from
    # the start. Production runs it only once its service has gone quiet.
    understudy = not production
    said_exchanging = False
    unreachable = 0
    while True:
        try:
            if understudy:
                _pass(remote, flow_id)
            status = read_status(remote, flow_id)
            unreachable = 0
        except RemoteFailure as exc:
            # A dropped connection must not be reported as a failed Link: the
            # host is exchanging regardless of whether this Mac can see it.
            unreachable += 1
            if unreachable >= 6:
                console.say(f"Lost contact with the host ({exc}). The host keeps working;")
                console.say(f"check later with: scripts/link.sh --status {flow_id}")
                return EXIT_UNFINISHED
            sleep(POLL_SECONDS)
            continue

        verdict = judge(status["flow"])
        if verdict.state == "ATTENTION":
            console.say()
            console.say(f"STOP: {verdict.detail}.")
            console.say(
                "Do not run this again. A lifetime connection may be at stake and running "
                f"again could spend another. Tell the agents: flow {flow_id}."
            )
            return EXIT_NEEDS_ATTENTION
        if verdict.state == "LINKED":
            return _settle(remote, console, flow_id, production, protect, clock, sleep)
        if verdict.state == "EXCHANGING" and not said_exchanging:
            console.say("Login finished. The host is exchanging the result...")
            said_exchanging = True
        if verdict.state == "UNFINISHED":
            console.say()
            console.say("The page expired and no finished login was seen.")
            console.say(
                "If you did not finish logging in anywhere, nothing was spent. The host does "
                "not assume that on its own, so before linking again tell the agents to close "
                f"flow {flow_id}."
            )
            return EXIT_UNFINISHED

        now = clock()
        if polls is None or status["flow"]["polls"] > polls:
            polls, polled_at = status["flow"]["polls"], now
        elif not understudy and now - polled_at >= STALL_AFTER:
            understudy = True
            console.say()
            console.say(
                "The host's own checker has gone quiet, so this window is now doing its job. "
                "Keep this window open until it says Connected, and tell the agents afterwards."
            )
        if verdict.state == "WAITING" and now >= deadline:
            # Expired, and still no poll after expiry: nothing has looked since
            # the page died, so "no login was seen" would be a guess.
            console.say()
            console.say(
                "STOP: the page has expired and the host has not been able to check it since, "
                "so it cannot say whether a login finished."
            )
            console.say(
                "Do not run this again. If you did finish logging in, a lifetime connection "
                f"is at stake. Tell the agents now: flow {flow_id}."
            )
            return EXIT_NEEDS_ATTENTION
        sleep(POLL_SECONDS)


def _settle(
    remote: Remote,
    console: Console,
    flow_id: str,
    production: bool,
    protect: Callable[[], None] | None,
    clock: Callable[[], datetime],
    sleep: Callable[[float], None],
    *,
    kick: bool = True,
) -> int:
    """The Link happened. Get it backed up and fetched, and say plainly what exists."""
    console.say()
    console.say("Connected. The credential is stored on the host.")

    if kick:
        # A failed kick is not a failed Link: the five-minute timer fetches it.
        # Sandbox has no timer, so the rehearsal runs the same job in line.
        with contextlib.suppress(RemoteFailure):
            if production:
                remote.start_sync()
            else:
                remote.verb("daemon", "sync", stated=False)

    if protect is not None:
        console.say("Copying an encrypted backup of it to this Mac...")
        try:
            protect()
            console.say("Backup verified on this Mac.")
        except Exception as exc:
            console.say(
                f"WARNING: the backup copy did not complete ({type(exc).__name__}). The "
                "connection itself is fine, but its credential is on one disk until a backup "
                "succeeds. Tell the agents."
            )

    console.say("Reading its accounts and first figures (up to a few minutes)...")
    deadline = clock() + SETTLE_FOR
    status: dict[str, Any] | None = None
    while clock() < deadline:
        try:
            status = read_status(remote, flow_id)
        except RemoteFailure:
            sleep(POLL_SECONDS)
            continue
        flow = status["flow"]
        if flow["items_discovered"] == flow["items"] and (
            flow["accounts_in_snapshot"] == flow["accounts"]
        ):
            break
        sleep(POLL_SECONDS)

    if status is None:
        console.say("The host could not be read just now; the sync runs every five minutes.")
        return EXIT_OK
    flow = status["flow"]
    if flow["items_discovered"] < flow["items"]:
        console.say("Its account list has not been read yet; the sync retries every five minutes.")
    elif flow["accounts"] == 0:
        console.say(
            "It has no account of a kind this version counts (USD investment and cash "
            "accounts). Nothing was added to the total."
        )
    else:
        valued = flow["accounts_in_snapshot"]
        console.say(f"{flow['accounts']} account(s) recorded; {valued} already in the total.")
        if flow["accounts_pending"]:
            console.say(
                f"{flow['accounts_pending']} of them wait for reconciliation and are not "
                "counted yet."
            )
    if status["budget"]["state"] == "AVAILABLE":
        console.say(_slots(status["budget"]) + ".")
    if not production:
        return EXIT_OK
    if not status["paired"]:
        console.say("No phone is paired yet. Pair one with: scripts/pair-phone.sh")
    elif flow["accounts_in_snapshot"] < flow["accounts"]:
        # The newest snapshot predates these accounts, so "published" is true of
        # the total the phone already had. A read that failed is retried an hour
        # later at the earliest; saying "a minute or two" here would be a guess.
        console.say(
            "These accounts are not in the total yet, so the phone still shows the total "
            "from before. The host keeps retrying on its own, and the next try can be an "
            "hour away. If they are still missing tomorrow, tell the agents."
        )
    elif status["published_latest"]:
        console.say("The phone shows the new total the next time the app is opened.")
    else:
        console.say("The new total reaches the phone within a minute or two.")
    # The first released page to expire stops the host counting its slots until
    # an agent closes it, so the next mint has to land before that instant.
    until = status["mint_window_ends_at"]
    if status["budget"]["state"] != "AVAILABLE":
        if status["awaiting_closure"] and not status["unattributed"]:
            console.say(
                "Another institution can be connected once the agents have closed this one "
                "out; tell them."
            )
    elif until is not None and datetime.fromisoformat(until) > clock():
        console.say(
            "To connect another institution, run this again before "
            f"{datetime.fromisoformat(until).astimezone():%H:%M}. After that, the next one "
            "waits a few minutes for the agents to close this one out."
        )
    return EXIT_OK


__all__ = [
    "CONFIRMATION",
    "EXIT_NEEDS_ATTENTION",
    "EXIT_OK",
    "EXIT_REFUSED",
    "EXIT_UNFINISHED",
    "REQUIRED_UNITS",
    "Console",
    "Remote",
    "RemoteFailure",
    "SshRemote",
    "Verdict",
    "judge",
    "read_status",
    "report_status",
    "run_link",
]
