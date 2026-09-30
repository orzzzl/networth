"""``networth complete-hosted-link`` — retrieve, and measure, after the owner finishes.

The other end of :mod:`networth.commands.start_hosted_link`. Once the owner has opened
the hosted URL and finished with ``user_good``/``pass_good``, **F7** leaves
``/link/token/get`` as the only path in existence to the resulting ``public_token``:
Hosted Link has no frontend integration, and ``completion_redirect_uri`` carries no
token, so nothing ever reaches his browser to paste. This verb is that call, plus the
three things task ``06a`` has to measure around it.

**One of three modes, and the mode is required rather than defaulted.** A default that
exchanges is a default that spends, and the whole of ``06a``'s value is that each run
does exactly the one thing its session was for:

``--retrieve-only``
    Poll and stop. The first half of session 2, which then waits out the 30 minutes
    that measurement **(i)** is about. **Deliberately does not exchange**, because the
    point of (i) is to attempt the exchange *late* and find out whether Plaid's
    30-minute window is real.

``--exchange``
    Poll, then exchange every ``public_token`` in the reply. Every one of them is a
    slot already spent (**F2a**); exchanging the first and dropping the rest would
    silently abandon Items the owner has already paid for.

``--exchange-twice``
    Measurement **(ii)**, and it has two halves. Exchange, then exchange the *same*
    ``public_token`` again and record the error code — and then call ``/item/get``
    with the **first** ``access_token`` to record whether it still works. That second
    half is the half ``07a``'s ``EXCHANGE_UNCERTAIN`` recovery is written against: a
    duplicate exchange that invalidates the original credential and one that does not
    are different recoveries, and guessing becomes a wrong procedure at the moment a
    slot is burning.

**Everything is reported as a measurement, never as a verdict.** The shape of the
reply is printed as the shape (:class:`~networth.plaid.client.LinkSessionShape` keeps
seven apart); a duplicate exchange that *succeeds* is recorded as that rather than
treated as an error, because ``06a``'s acceptance says "record each as a measurement
whatever the result" and the design is what changes if Plaid disagrees with it.

**Two credential sources, one call path** — which is what makes measurement **(iv)**
the same code rather than a second implementation of it.

*Default (the VPS).* The ``link_token`` comes from this host's
:class:`~networth.tokenstore.TokenStore` under the ``flow_id``
``start-hosted-link`` printed, and ``client_id``/``secret`` from ``/etc/networth``.

*``--from-tty --flow <id>`` (``zelengs-macbook-air-2``).* ``client_id`` and the
secret are prompted for on **the controlling terminal**, never echoed, never written
and never in shell history; the ``link_token`` comes from **this Mac's recovery
record**, which ``scripts/link-start.sh`` wrote and verified at mint time. This is
(iv): the same Plaid credentials, a different machine, **the VPS taking no part in
either API call**.

**Two prompts rather than three, and that is the shape ``07b`` inherits.** §19 step
2a's procedure is already written — *"it already holds the recovery record and the
``link_token``; it will prompt you for ``client_id`` and the secret"* — and ``06a``'s
job is to be the first form of that command rather than a rehearsal of a different
one. A third prompt would also have meant the owner pasting token material by hand,
which is the manual copy step F7's design removes.

**``--from-tty`` used to persist nothing, and ``07b`` is why it no longer does.**
That rule was right for a measurement and wrong for the emergency the same command
is the procedure for. §19 step 2a's scenario: a Link has succeeded, so **F2a** has
already spent one of ten lifetime Item slots; the VPS is lost before the exchange;
the owner runs this on ``zelengs-macbook-air-2`` inside the ``public_token``'s 30
minutes. Printing the answer and dropping the credential there means the procedure
spends the one-time token, receives a long-lived credential, and holds it in a
process with nowhere durable to put it — one laptop crash from recreating the
permanent slot loss it exists to prevent.

So ``--sink`` is **required** whenever ``--from-tty`` will exchange, and it names the
destination explicitly rather than having one inferred. §15 is satisfied by the
encryption plus the escrowed key, not by declining to write: a credential lost
because nothing was written breaks the task this command exists to perform.

**One of `07b` criterion 1's two kinds is reachable from here, and the other is
refused — measured, not assumed.** ``--sink emergency-artifact`` seals the credential
under the **already-escrowed** ``03a`` backup key and is this command's answer.
``--sink replacement-host`` is refused, because
:class:`~networth.link_sink.ReplacementHostSink` carries no transport: it builds a
``TokenStore`` from whatever directory it is handed, on whatever machine is running,
and ``--from-tty`` is by construction ``zelengs-macbook-air-2`` — it reads this Mac's
recovery record, and ``scripts/link-recover.sh`` verifies the tailnet address before
it runs. So that option would have written an unsealed ``access_token`` onto the one
computer §15 keeps them off, under a name that says it went somewhere else. The class
is still criterion 1's first branch and :func:`~networth.link_sink.restore` is its
real caller — on the replacement host, where the name is true. Until a verified
destination on the far side exists, refusing here is the honest half of the choice,
and ``AGENTS.md`` rule 1 is the general form of why: a path that quietly resolves to
the local machine "is how a path bug becomes 'it worked on my machine' for a file
holding access tokens".

The destination is proven **before the prompts**, and so necessarily before the
exchange — ``link_sink`` writes and reads back a real probe rather than asking
``os.access``. A fallback chosen after the irreversible step is not a fallback.
``--retrieve-only`` exchanges nothing and so needs no sink, and that is the one path
on which this command still stores nothing.

**And the fence is asked for between those two** (`07b` criterion 6). Before an
exchange, the owner confirms — by typing the host's name, not by pressing a key — that
the host running the old Link worker is powered off or destroyed in his provider's
control plane, and the confirmation is sealed inside the artifact with the credential.
No probe stands in for it: *"unreachable is not off"*, and the emergency this command
exists for is precisely a host that does not answer. The order is sink, then fence,
then credentials, and each step is placed where its refusal costs the least — the sink
proof asks nothing of him, the fence asks him to give up his exit node (§15.1), and the
secret is asked for last so that neither refusal above can happen after he has typed
it. ``--retrieve-only`` is exempt from the fence for the same reason it is exempt from
the sink: it exchanges nothing, so there is nothing for a second worker to race.
"""

from __future__ import annotations

import argparse
import os
import sys
import termios
from datetime import UTC, datetime
from pathlib import Path

from networth import link_fence, link_recovery, link_sink, mac_identity
from networth.config import ConfigError
from networth.link_fence import FenceError
from networth.link_recovery import LinkRecoveryError
from networth.link_sink import (
    DurableSink,
    EmergencyArtifactSink,
    RecoveredItem,
    SinkError,
    SinkKind,
    SinkNotWritable,
)
from networth.plaid.client import (
    ExchangedItem,
    LinkSessionPoll,
    LinkSessionShape,
    PlaidCallError,
    PlaidClient,
)
from networth.plaid.environment import (
    PlaidCredentials,
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.tokenstore import (
    Secret,
    SecretKind,
    TokenStore,
    TokenStoreError,
    new_flow_id,
    secret_ref_for,
)

SUMMARY = "Retrieve a finished Hosted Link session and measure 06a's (ii) and (iv)."

#: Re-exported rather than defined here. It moved into :mod:`networth.link_sink` when
#: ``restore-link-artifact`` began reporting the same status, because the same exit code
#: meaning two things in two verbs is worse than an import.
INCOMPLETE_RECOVERY = link_sink.INCOMPLETE_RECOVERY


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flow",
        metavar="ID",
        help=(
            "the flow id the mint printed; names the link_token to use — from this "
            "host's TokenStore, or from this Mac's recovery record with --from-tty"
        ),
    )
    parser.add_argument(
        "--from-tty",
        action="store_true",
        help=(
            "read the link token from this Mac's recovery record and prompt on the "
            "terminal for client_id and secret — measurement (iv). Persists nothing"
        ),
    )
    # `07b`: where a credential recovered with --from-tty lands. Required on that
    # path rather than defaulted, and never inferred from which other argument
    # happens to be present: the two kinds put the credential on **different
    # computers**, and `AGENTS.md` rule 1 is explicit that a lookup which falls
    # back from one host's secrets directory to the other "is how a path bug
    # becomes 'it worked on my machine' for a file holding access tokens".
    parser.add_argument(
        "--sink",
        choices=[kind.value for kind in SinkKind],
        help=(
            "07b: where --from-tty puts the recovered access_token. "
            f"{SinkKind.EMERGENCY_ARTIFACT.value} needs --artifact and --backup-key. "
            f"{SinkKind.REPLACEMENT_HOST.value} is refused from this command — it has "
            "no transport, so it would write plaintext on this laptop"
        ),
    )
    # Kept as an accepted argument although no sink here uses it, so that supplying
    # it produces `_refuse_unused`'s explanation rather than argparse's "unrecognized
    # arguments". The refusal an operator meets mid-emergency should say which
    # destination he actually asked for.
    parser.add_argument(
        "--token-store",
        metavar="DIR",
        type=Path,
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--artifact",
        metavar="PATH",
        type=Path,
        help=(
            f"--sink {SinkKind.EMERGENCY_ARTIFACT.value}: the sealed file to create; must not exist"
        ),
    )
    parser.add_argument(
        "--backup-key",
        metavar="PATH",
        type=Path,
        help=(
            f"--sink {SinkKind.EMERGENCY_ARTIFACT.value}: the already-escrowed 03a backup key. "
            "No new key is ever minted here"
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--retrieve-only",
        action="store_true",
        help="poll and stop; do not exchange (session 2's first half, measurement (i))",
    )
    mode.add_argument(
        "--exchange",
        action="store_true",
        help="poll, then exchange every public_token in the reply",
    )
    mode.add_argument(
        "--exchange-twice",
        action="store_true",
        help="measurement (ii): exchange, exchange again, then item_get the first token",
    )


def _read_from_tty(prompt: str, *, echo: bool = False) -> str:
    """One line from the **controlling terminal**, echo off by default, or a refusal.

    ``getpass.getpass`` is what this used to call and it is the wrong tool here,
    measured rather than assumed: fed three synthetic lines on stdin with no terminal
    available, it *accepted all three* and returned normally, warning
    ``GetPassWarning: Can not control echo on the terminal``. That fallback is the
    whole hazard — ``06a``'s Mac command reads a Plaid secret, and a prompt that
    silently reads a pipe is one ``<<<`` away from putting that secret in a shell
    history, a CI log, or a process's argv. **A warning is not a refusal.**

    So the terminal is opened by name. ``/dev/tty`` *is* the controlling terminal by
    definition, and a process that has none cannot open it (``ENXIO``) — which makes
    the presence of a terminal the thing being tested, rather than whether stdin
    happens to be a tty right now. Echo is turned off on that descriptor and restored
    in a ``finally``, so a refusal partway through does not leave the owner's shell
    silent.

    **``echo=True`` silences nothing and is for the one prompt that reads no secret.**
    `07b`'s fence asks the owner to *type a host name* (criterion 6), and a name typed
    blind is a name typed wrong — on a 30-minute clock, in a procedure whose refusal
    message deliberately does not quote what it read back. Everything else here is
    unchanged on that path: the terminal is still opened by name, a process without one
    is still refused rather than falling back to stdin, and the attributes are still
    restored in a ``finally``. The difference is exactly the ``ECHO`` bit, which is why
    it is one flag on this function rather than a second reader with its own idea of
    what a terminal is.
    """
    try:
        descriptor = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError as exc:
        raise ConfigError(
            "--from-tty needs a controlling terminal and this process has none. "
            "Run it from a terminal; piped or redirected input is refused rather "
            "than read, because a Plaid secret read off a pipe has already been "
            "written down somewhere"
        ) from exc
    # Buffered text update mode ("r+") requires a seekable stream; a real TTY
    # cannot seek. Separate readers/writers support it without a stdin fallback.
    # Keep descriptor ownership here: wrapper construction can fail, and an
    # implicit close followed by another close would mask that failure with EBADF.
    try:
        with (
            os.fdopen(
                descriptor, "r", encoding="utf-8", errors="replace", closefd=False
            ) as terminal_input,
            os.fdopen(
                descriptor, "w", buffering=1, encoding="utf-8", errors="replace", closefd=False
            ) as terminal_output,
        ):
            try:
                original = termios.tcgetattr(descriptor)
            except termios.error as exc:
                raise ConfigError("/dev/tty opened but is not a terminal we can silence") from exc
            wanted = list(original)
            if not echo:
                wanted[3] = int(wanted[3]) & ~termios.ECHO
            try:
                termios.tcsetattr(descriptor, termios.TCSAFLUSH, wanted)
                terminal_output.write(prompt)
                terminal_output.flush()
                line = terminal_input.readline()
            finally:
                termios.tcsetattr(descriptor, termios.TCSAFLUSH, original)
                terminal_output.write("\n")
                terminal_output.flush()
    finally:
        os.close(descriptor)
    if not line:
        raise ConfigError("the terminal closed before the prompt was answered")
    return line.rstrip("\r\n")


def _prompt_credentials(environment: PlaidEnvironment) -> PlaidCredentials:
    """Two prompts, neither echoed and neither reaching ``argv`` or shell history.

    **Two, not three.** The ``link_token`` is no longer asked for: ``link-start.sh``
    puts it in this Mac's recovery record at mint time, and this verb reads it from
    there. A third prompt would have rehearsed a call path ``07b`` does not have —
    §19 step 2a's procedure is "it already holds the recovery record and the
    ``link_token``, it will prompt you for ``client_id`` and the secret" — and
    ``06a``'s job is to be the first form of that command, not a throwaway.
    """
    client_id = _read_from_tty("Plaid client_id (not echoed): ")
    secret = _read_from_tty(f"Plaid {environment.value} secret (not echoed): ")
    if not (client_id and secret):
        raise ConfigError("both prompts are required; nothing was read")
    return PlaidCredentials(environment=environment, client_id=client_id, secret=secret)


def _confirm_fence() -> link_fence.FenceAttestation:
    """Criterion 6's typed confirmation, read on the terminal and returned to be sealed.

    The prompt echoes — it reads a host name, not a credential — and it is the only
    prompt in this verb that does. :mod:`networth.link_fence` owns the wording, the
    comparison and the refusal; this function owns nothing but the terminal, which is
    the same split :func:`_prompt_credentials` uses.
    """
    return link_fence.confirm(
        lambda prompt: _read_from_tty(prompt, echo=True),
        now=datetime.now(UTC),
    )


def _link_token_from_this_host(environment: PlaidEnvironment, flow_id: str) -> str:
    store = TokenStore(paths_for(environment).items)
    return store.get(secret_ref_for(SecretKind.LINK_TOKEN, flow_id)).reveal()


def _session_that_supplied(poll: LinkSessionPoll, public_token: str) -> str | None:
    """Which Link attempt handed us *this* token, or ``None`` if the reply does not say.

    PR #129 finding 3. This used to take the first non-empty ``session_id`` in the whole
    poll, and one reply can describe several sessions: an attempt the owner abandoned,
    then the one that worked. That reply has exactly one public token — so it passes the
    cardinality refusal above — and the artifact was sealed with the *abandoned*
    session's id beside the successful session's ``item_id``. A pairing that names the
    wrong attempt is worse than an absent one, because nothing downstream can tell.

    ``LinkSessionRecord`` exists to prevent precisely this: *"the first cut returned
    parallel tuples of session ids and public tokens, which threw away **which token
    belongs to which session**"*. The association was preserved one layer down and then
    discarded by its only consumer.

    ``None`` when no record claims the token, rather than the nearest id available.
    ``link_session_id`` is optional in the artifact schema and an honest absence is
    recoverable; a confident wrong answer is not.
    """
    for record in poll.sessions:
        if public_token in record.public_tokens:
            return record.session_id
    return None


def _report_poll_shape(shape: LinkSessionShape, sessions: int, tokens: int) -> None:
    print()
    print(f"link_sessions {shape.value}")
    print(f"sessions      {sessions}")
    print(f"public tokens {tokens}")


def _sink_from(args: argparse.Namespace) -> DurableSink:
    """Build the destination named by ``--sink``, or refuse to guess one.

    Every branch that fails here raises :class:`SinkNotWritable`, which the caller
    treats exactly like an unwritable directory — because it is the same fact at a
    different layer: **there is no destination**. Nothing has been exchanged when
    any of these fire, which is the only property that matters about them.
    """
    if args.sink is None:
        raise SinkNotWritable(
            "--from-tty now persists the recovered credential, so --sink is required "
            f"({SinkKind.REPLACEMENT_HOST.value} or {SinkKind.EMERGENCY_ARTIFACT.value}). "
            "It is not inferred: the two put the credential on different computers"
        )
    kind = SinkKind(args.sink)
    if kind is SinkKind.REPLACEMENT_HOST:
        # **Refused from this CLI, and the refusal is the fix rather than a gap.**
        # `ReplacementHostSink` holds no transport: it builds a `TokenStore` from
        # whatever `Path` it is handed, on whatever machine the process is running.
        # `--from-tty` is by construction *this* Mac — it reads this Mac's recovery
        # record, and `link-recover.sh` verifies the tailnet address before it runs
        # at all — so the directory named here is a local one, and the option
        # advertised as "replacement host" would drop an unsealed `access_token` on
        # the one computer §15 keeps them off. Naming an option after a destination
        # does not make the credential travel there.
        #
        # The class stays: it is the right object for code running **on** the
        # replacement host, and `link_sink.restore()` is its real caller. What does
        # not exist yet is a verified destination on the far side, and until it does
        # the honest answer here is this refusal rather than a local write.
        raise SinkNotWritable(
            f"--sink {kind.value} is refused from this command: it writes a plain "
            "TokenStore on whatever machine it runs on, and --from-tty runs on "
            f"{mac_identity.REQUIRED_HOLDER}. There is no transport here, so this "
            "would leave an unsealed access_token on this laptop. Use --sink "
            f"{SinkKind.EMERGENCY_ARTIFACT.value}, then run the restore on the "
            "replacement host once it is standing"
        )
    if args.artifact is None or args.backup_key is None:
        raise SinkNotWritable(f"--sink {kind.value} needs --artifact PATH and --backup-key PATH")
    _refuse_unused(args, allowed={"artifact", "backup_key"})
    return EmergencyArtifactSink(args.artifact, key_file=args.backup_key)


def _refuse_unused(args: argparse.Namespace, *, allowed: set[str]) -> None:
    """Refuse arguments belonging to the kind that was **not** chosen.

    Ignoring them would let ``--sink replacement-host --artifact /tmp/x`` run and
    say nothing, leaving the operator believing a sealed file was written. In this
    scenario the operator is the owner, mid-emergency, reading one line of output
    to decide whether his credential survived.
    """
    supplied = {name for name in ("token_store", "artifact", "backup_key") if getattr(args, name)}
    stray = sorted(supplied - allowed)
    if stray:
        raise SinkNotWritable(
            "these belong to the other --sink kind and would be silently ignored: "
            + ", ".join(f"--{name.replace('_', '-')}" for name in stray)
        )


def run(args: argparse.Namespace) -> int:  # noqa: PLR0911 — each return is a distinct outcome
    try:
        environment = selected_environment()
        print(f"environment   {environment.value}")
        if environment is not PlaidEnvironment.SANDBOX:
            # Same placement as the mint verbs: before any credential is read. A
            # Production public_token belongs to task 08's flow, and exchanging one
            # from a measurement verb writes a lifetime Item's credential somewhere
            # nobody planned for it.
            print(
                f"refusing to run against {environment.value!r}: this verb retrieves and "
                "exchanges real Link results, and a Production Item is one of ten "
                "lifetime slots. Production Link is task 08, run by the owner",
                file=sys.stderr,
            )
            return 2

        if args.from_tty:
            if not args.flow:
                print(
                    "--from-tty reads the link token from this Mac's recovery record, "
                    "so --flow <id> is required; it is the id link-start.sh printed",
                    file=sys.stderr,
                )
                return 2
            directory = link_recovery.mac_recovery_directory()
            print(f"source        {directory} + two prompts (measurement (iv))")
            # The record is read *before* the prompts on purpose. A missing record is
            # the likeliest failure on this path, and discovering it after the owner
            # has typed his Plaid secret would have spent the one thing this command
            # asks of him for nothing.
            link_token = link_recovery.load(directory, args.flow).link_token.reveal()
            # **The sink is proven here — before the prompts, and so necessarily
            # before the exchange.** Criterion 1: *"a fallback chosen after the
            # irreversible step is not a fallback"*. Placed against the prompts for
            # the same reason the record read above is: discovering there is
            # nowhere to put the credential after the owner has typed his Plaid
            # secret would have spent the one thing this command asks of him, and
            # discovering it after the exchange would strand a lifetime Item slot.
            if not args.retrieve_only:
                sink = _sink_from(args)
                sink.prepare_for(args.flow)
                print(f"sink          {sink.destination}")
                print("              proven writable before anything was spent")
                # **The fence comes after the sink proof, and that order is the
                # expensive part of it.** Both refuse before the exchange, so either
                # order satisfies criterion 6 on paper — but this prompt is what makes
                # the owner power off the host that is also his Tailscale exit node
                # (§15.1). Asking first and *then* discovering the destination is
                # unwritable would have spent his VPN to learn something free. The
                # sink proof needs nothing from him; this needs a decision.
                fence = _confirm_fence()
                print(f"fence         {fence.instance} — {fence.statement}")
                print(f"              confirmed at {fence.confirmed_at.isoformat()}")
            credentials = _prompt_credentials(environment)
        else:
            if not args.flow:
                print("--flow is required unless --from-tty is given", file=sys.stderr)
                return 2
            paths = paths_for(environment)
            print(f"credentials   {paths.credentials}")
            print(f"token store   {paths.items}")
            credentials = load_credentials(environment)
            link_token = _link_token_from_this_host(environment, args.flow)

        client = PlaidClient(credentials)
        poll = client.link_token_get(link_token)
    except (
        ConfigError,
        PlaidCallError,
        TokenStoreError,
        LinkRecoveryError,
        SinkError,
        FenceError,
    ) as exc:
        # Every one of these redacts itself; printing `exc` is safe because of that
        # property, not because this handler checked anything. `SinkError` joins them
        # under the same promise: `link_sink` raises paths and field names only.
        # `FenceError` under a stricter one — its refusals quote nothing that was typed,
        # because the prompt one line below this reads a Plaid secret.
        print(f"complete-hosted-link failed: {exc}", file=sys.stderr)
        return 2

    _report_poll_shape(poll.shape, len(poll.sessions), len(poll.public_tokens))

    public_tokens = poll.public_tokens
    if not public_tokens:
        # Not a crash. "Nothing yet" is a real answer to a real question, and it is
        # the answer criterion 2b is about — someone has opened the URL and has not
        # finished. Exit 1 says "measured, and there is nothing to exchange".
        print(
            "no public_token in the reply — the session is not finished, or this is "
            "the pre-start state. Nothing was exchanged and nothing was spent"
        )
        return 1

    if args.retrieve_only:
        print()
        print("retrieve-only — the public_token was NOT exchanged, on purpose")
        print(
            "measurement (i): wait past 30 minutes, then re-run this flow with "
            "--exchange and record whether Plaid still accepts it"
        )
        return 0

    if args.from_tty and len(public_tokens) > 1:
        # **Refused before the exchange, not discovered during it.** One sink is one
        # destination: the artifact's `O_EXCL` create would refuse the second item
        # and the TokenStore refuses a second put for one flow — and both of those
        # refusals would land *after* two tokens had been spent, with the second
        # credential live in a process that has nowhere to put it. The recovery this
        # path exists for is one lost Item.
        print(
            f"the reply carries {len(public_tokens)} public_tokens and --from-tty has one "
            "sink to put a credential in. Nothing was exchanged. Recover them one flow "
            "at a time, each with its own --sink destination",
            file=sys.stderr,
        )
        return 2

    try:
        exchanged = [client.item_public_token_exchange(token) for token in public_tokens]
    except PlaidCallError as exc:
        print(f"exchange failed: {exc}", file=sys.stderr)
        return 2

    print()
    print(f"exchanged     {len(exchanged)} public_token(s)")
    for item in exchanged:
        # The request_id is the one field lifted out of a Plaid body that this
        # project prints (issue #14): its whole purpose is to be quoted into a
        # support ticket. The access_token and item_id are not printed — the first
        # is the credential, the second names one of the owner's institutions
        # (AGENTS.md rule 0).
        print(f"  request_id  {item.request_id!r}")

    owed: tuple[str, ...] = ()
    if not args.from_tty:
        try:
            _persist(environment, exchanged)
        except TokenStoreError as exc:
            print(f"exchange succeeded but the credential was not stored: {exc}", file=sys.stderr)
            return 2
        print("stored        access_token(s) through TokenStore")
    else:
        # **This is the line `07b` exists to delete.** It used to read *"stored
        # nothing — --from-tty persists no credential (§15)"*, which was correct for
        # a measurement and catastrophic for the emergency: the procedure spent the
        # one-time token, received a long-lived credential, and held it in a process
        # with nowhere durable to put it — one laptop crash from recreating the
        # permanent slot loss it exists to prevent. §15 is satisfied by the
        # encryption plus the escrowed key, not by declining to write.
        session_id = _session_that_supplied(poll, public_tokens[0])
        try:
            receipt = sink.commit(
                RecoveredItem(
                    access_token=Secret(exchanged[0].access_token),
                    item_id=exchanged[0].item_id,
                    flow_id=args.flow,
                    link_session_id=session_id,
                    request_id=exchanged[0].request_id,
                    fence=fence,
                ),
                now=datetime.now(UTC),
            )
        except SinkError as exc:
            # The expensive side. `prepare` already excluded everything it could,
            # so reaching here means the credential exists and is not on disk — and
            # the caller must not read it as "try again", because trying again means
            # exchanging a token that has already been consumed.
            print(
                f"THE EXCHANGE SUCCEEDED AND THE CREDENTIAL WAS NOT STORED: {exc}", file=sys.stderr
            )
            return 2
        owed = receipt.owed
        print(f"stored        {', '.join(receipt.durable)}")
        print(f"              at {receipt.destination}")
        if owed:
            # A receipt with anything owed describes an *incomplete* recovery, and
            # saying so is what stops it being reported as a finished one.
            print(f"still owed    {', '.join(owed)} — {receipt.pairing.value}")

    # The end of a flow's life, announced before it is acted on — because the
    # process that knows the exchange happened is usually not the process that
    # holds the record. Both readers are downstream of this one line:
    # `_retire_recovery_record` below, when this is already running on the Mac,
    # and `networth retire-hosted-link` on the Mac when this is running on the VPS.
    #
    # Deliberately not on the `--retrieve-only` path above, which returns earlier:
    # that flow is still live on purpose, and measurement (i) comes back to it
    # after 30 minutes with the record it needs. A marker there would authorise
    # deleting the copy that path exists to preserve.
    #
    # **And deliberately not on an incomplete receipt either** (PR #129 finding 4).
    # `SinkReceipt.owed` was built so an incomplete recovery could not be reported as
    # a finished one, and then this block ignored it: the marker is *terminal* — the
    # only outcome `CompletionOutcome` accepts is `EXCHANGED`, and §4 has `link.sh`
    # delete the record on it — so emitting it here retired the second copy of a
    # recovery that still owes fields, deleting the evidence needed to finish the
    # remaining work without spending another one-time token. No new outcome value is
    # invented for this: an incomplete recovery is not terminal, so the honest wire
    # output is none at all, and the record staying put *is* the resumption evidence.
    if args.flow:
        if owed:
            print(
                f"RECOVERY INCOMPLETE — {', '.join(owed)} did not land, "
                f"pairing is {receipt.pairing.value}. The access_token IS durable at "
                f"{receipt.destination}, so do NOT exchange again: the token is spent. "
                f"The recovery record for {args.flow} is kept on purpose so the rest "
                "can be finished from it",
                file=sys.stderr,
            )
        else:
            print(link_recovery.CompletionOutcome(args.flow, link_recovery.EXCHANGED).to_wire())
            _retire_recovery_record(args.flow)

    if args.exchange_twice:
        measured = _measure_duplicate_exchange(client, public_tokens[0], exchanged[0])
        if measured != 0:
            return measured

    return INCOMPLETE_RECOVERY if owed else 0


def _persist(environment: PlaidEnvironment, exchanged: list[ExchangedItem]) -> None:
    store = TokenStore(paths_for(environment).items)
    for item in exchanged:
        store.put(
            SecretKind.ACCESS_TOKEN, _flow_id_for(item), item.access_token, item_id=item.item_id
        )


def _flow_id_for(item: ExchangedItem) -> str:
    """A fresh handle per exchanged Item.

    The ``secret_ref`` scheme encodes a ``flow_id`` (issue #15) and one Hosted Link
    session can return several Items, so the minted flow id cannot name all of them.
    ``07a`` owns the real association through the ``link_flow`` row; this verb is a
    measurement and needs only a name that does not collide.
    """
    return new_flow_id()


def _measure_duplicate_exchange(
    client: PlaidClient, public_token: str, first: ExchangedItem
) -> int:
    """Measurement (ii), both halves. Records; never judges."""
    print()
    print("measurement (ii) — exchanging the SAME public_token a second time")
    second: ExchangedItem | None = None
    try:
        second = client.item_public_token_exchange(public_token)
    except PlaidCallError as exc:
        # The expected branch, and still recorded as an observation rather than as a
        # pass: the error *code* is what 07a branches on, so it is printed as its own
        # line rather than left inside a message that deliberately carries no body.
        # `error_code` is validated against the taxonomy's grammar by the client, so
        # this line cannot become the place a Plaid error body reaches a transcript;
        # `None` means Plaid sent no code we can name, which is itself the
        # measurement and is never rendered as a code.
        print(f"  second      REFUSED — {exc}")
        print(f"  error_code  {exc.error_code!r}")
    else:
        print(f"  second      ACCEPTED — request_id {second.request_id!r}")
        print(
            "  NOTE        Plaid accepted a duplicate exchange. DESIGN.md records "
            "this observation, not Plaid's phrasing, and 07a's EXCHANGE_UNCERTAIN "
            "recovery is written against it"
        )

    # The half that actually decides the recovery procedure. `item_get` never raises
    # for a Plaid-level failure, so this is a classification, not an exception test.
    # Health is read off `state`, never off `error_code is None` — a transport
    # failure is DEGRADED with no code, because Plaid never answered.
    status = client.item_get(first.access_token)
    classification = status.classification
    print(
        f"  first token state={classification.state!r} "
        f"observed={classification.item_state_observed} "
        f"error_code={classification.error_code!r} "
        f"(item_id present: {status.item_id is not None})"
    )
    print(
        "  (ii) is a measurement: whichever way these two lines came out is what "
        "07a is written against"
    )
    return 0


def _retire_recovery_record(flow_id: str) -> None:
    """Remove the second copy **if this process is on the machine that holds it**.

    Ordering matters here in a way it does not elsewhere in this file: the
    exchange has already happened and any credential is already stored, so a
    cleanup fault is a note rather than a failure. Returning non-zero at this
    point would tell the owner the exchange did not land, which would be false
    and would send him to a recovery procedure for a flow that is finished.

    **Which machine this runs on is now measured, and the previous version's
    account of it was wrong.** It said "on the VPS this finds nothing and says
    nothing", and treated that as the VPS case being handled. It is not handled;
    it is silent. `mac_recovery_directory()` resolves `~/agents/secrets/...` on
    whatever host is running — on the VPS that is the *VPS's* `~`, which
    `AGENTS.md` forbids this code from reading at all, and finding it empty was
    being read as "nothing to clean" when the truth is "the file is on another
    computer". The normal rehearsal path runs this verb over ssh
    (`sandbox-rehearsal-remote.sh`), so the *normal* path was the silent one, and
    every completed Sandbox flow left its record on the Mac until the seven-hour
    sweep — with `DESIGN.md` §4 requiring the interactive driver to delete on
    `EXCHANGED` and expiry to cover only the crash gap.

    So the host is established by the same measurement everything else on this
    path uses, and the two cases are now distinguishable to a reader of the
    transcript: on the Mac (`--from-tty`, through `scripts/link-recover.sh`) the
    record is retired in this process; anywhere else the verb says so, and the
    marker printed by the caller is what lets the Mac-side driver finish the job.
    """
    try:
        mac_identity.verify()
    except mac_identity.WrongHost:
        print(
            f"record        not this machine's to remove; "
            f"{mac_identity.REQUIRED_HOLDER} retires {flow_id} on the marker above"
        )
        return
    directory = link_recovery.mac_recovery_directory()
    try:
        removed = link_recovery.delete(directory, flow_id)
    except (LinkRecoveryError, OSError) as exc:
        print(
            f"note          this Mac's recovery record was not removed: {exc}",
            file=sys.stderr,
        )
        return
    if removed:
        print(f"cleaned       removed this Mac's recovery record for {flow_id}")
