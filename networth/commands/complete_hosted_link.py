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

*``--from-tty`` (``zelengs-macbook-air-2``).* All three are prompted for and never
echoed, never written and never in shell history. This is (iv): the same Plaid
credentials, a different machine, **the VPS taking no part in either API call**. The
``link_token`` is prompted for too rather than passed in ``argv``, because the thing
that has to cross VPS→Mac is exactly one string and `argv` is the one place it would
persist.

**``--from-tty`` persists nothing, and that is a rule rather than a simplification.**
§15 keeps ``access_token``s off this laptop; the Mac holds the backup key and the
``link_token`` second copy and nothing else that can read an account. (iv) asks only
whether retrieval and exchange *succeed* from another host, so the answer is printed
and the credential is dropped when the process exits. A run that stored one would
answer the question and widen the machine while doing it.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from networth.config import ConfigError
from networth.plaid.client import (
    ExchangedItem,
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
    SecretKind,
    TokenStore,
    TokenStoreError,
    new_flow_id,
    secret_ref_for,
)

SUMMARY = "Retrieve a finished Hosted Link session and measure 06a's (ii) and (iv)."


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--flow",
        metavar="ID",
        help="the flow id start-hosted-link printed; reads its link_token from this host",
    )
    parser.add_argument(
        "--from-tty",
        action="store_true",
        help=(
            "prompt for client_id, secret and link token instead of reading this "
            "host's files — measurement (iv), run from the Mac. Persists nothing"
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


def _prompt_credentials(environment: PlaidEnvironment) -> tuple[PlaidCredentials, str]:
    """Three prompts, none echoed and none reaching ``argv`` or the shell's history.

    ``getpass`` reads from ``/dev/tty`` where it can, so this still refuses to be fed
    from a pipe on a terminal-less host rather than silently accepting whatever was
    on stdin — which would put the secret in a shell history one redirect later.
    """
    client_id = getpass.getpass("Plaid client_id (not echoed): ")
    secret = getpass.getpass(f"Plaid {environment.value} secret (not echoed): ")
    link_token = getpass.getpass("link token from the VPS (not echoed): ")
    if not (client_id and secret and link_token):
        raise ConfigError("all three prompts are required; nothing was read")
    return (
        PlaidCredentials(environment=environment, client_id=client_id, secret=secret),
        link_token,
    )


def _link_token_from_this_host(environment: PlaidEnvironment, flow_id: str) -> str:
    store = TokenStore(paths_for(environment).items)
    return store.get(secret_ref_for(SecretKind.LINK_TOKEN, flow_id)).reveal()


def _report_poll_shape(shape: LinkSessionShape, sessions: int, tokens: int) -> None:
    print()
    print(f"link_sessions {shape.value}")
    print(f"sessions      {sessions}")
    print(f"public tokens {tokens}")


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
            if args.flow:
                print(
                    "--flow and --from-tty are different credential sources; --from-tty "
                    "prompts for the link token because this host has no token store "
                    "for it",
                    file=sys.stderr,
                )
                return 2
            print("source        prompts (measurement (iv)); this host stores nothing")
            credentials, link_token = _prompt_credentials(environment)
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
    except (ConfigError, PlaidCallError, TokenStoreError) as exc:
        # Every one of these redacts itself; printing `exc` is safe because of that
        # property, not because this handler checked anything.
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

    if not args.from_tty:
        try:
            _persist(environment, exchanged)
        except TokenStoreError as exc:
            print(f"exchange succeeded but the credential was not stored: {exc}", file=sys.stderr)
            return 2
        print("stored        access_token(s) through TokenStore")
    else:
        print("stored        nothing — --from-tty persists no credential (§15)")

    if not args.exchange_twice:
        return 0

    return _measure_duplicate_exchange(client, public_tokens[0], exchanged[0])


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
        # pass: the error *code* is what 07a branches on.
        print(f"  second      REFUSED — {exc}")
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
