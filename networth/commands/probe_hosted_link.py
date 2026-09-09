"""``networth probe-hosted-link`` — task ``06a``'s F7 criterion 2, on the real API.

**What this verb is for, and what it deliberately cannot do.** F7's first criterion
needs a *completed* Hosted Link session, and completing one **has no API**: Plaid's
Hosted Link documentation states that a real person must finish the flow in a browser
or webview, and Plaid separately advises against scripting that UI because it changes.
The documented programmatic substitute — ``/sandbox/public_token/create`` — is the
*Link bypass* that :mod:`networth.commands.rehearse_sandbox` already runs for task
``06``, so using it here would prove nothing that task has not already proved.

The **second** criterion is what this verb serves — but only part of it, and saying
which part is the whole of its honesty. As written, criterion 2 is universal:

    *before completion the reply carries no* ``link_sessions`` *key at all*

"Before completion" is not one state. A freshly minted token nobody has opened is one
of them; a session the owner has **started and not finished** is another, and it is
the one ``07a``'s poller spends most of its ticks in. Plaid's schema permits it
explicitly — ``LinkTokenGetSessionsResponse.finished_at`` is nullable — so it is a
state the API really produces.

**This verb reaches the first and cannot reach the second**, because entering the
second means opening the hosted URL, which is the browser step no API replaces. So it
measures the **pre-start** state, reports it as that, and never claims the universal
criterion (PR #59 review, finding 1). Splitting the criterion rather than quietly
generalising this run is the point: a poller written against the pre-start shape and
deployed into the started-but-unfinished one is precisely the failure the criterion
exists to prevent, and a transcript that said "criterion 2 HOLDS" would have hidden it.

It mints one token and polls it immediately — one Plaid call each, no browser, no
owner, and **nothing spent**: a mint is not an Item, and by **F2a** the lifetime slot
is consumed when a Link *completes*, which is exactly the step this verb cannot reach.

**Why that is worth a live call rather than a fixture.** The pre-start branch is the
one that runs on the first tick of every Link flow. Written against
``link_sessions == []`` when Plaid actually omits the key, it raises instead of
waiting — at the one moment a slot is already burning and the token has 30 minutes to
live. :class:`~networth.plaid.client.LinkSessionShape` keeps seven shapes apart for
this reason; this verb is what decides which of them Plaid sends for the one state it
can create.

**It persists nothing.** No database row, no ``TokenStore`` write, no ``link_flow``.
The minted token is discarded when the process exits, which is honest for a
measurement and is also why this verb cannot be mistaken for the Link driver that
§16 describes and task ``07a`` builds.

**Neither the link token nor the hosted URL is ever printed.** The URL is openable by
whoever holds it, so it is a spendable artefact rather than a diagnostic — the same
reason :class:`~networth.plaid.client.HostedLinkToken` redacts both in its repr. There
was a ``--print-url`` option here and it is gone (PR #59 review, finding 2): this
process holds the **only** handle to the session it mints and drops it on exit, so a
URL it printed could be completed with no way left to call ``/link/token/get`` for the
result — an Item slot spent for nothing. The owner-run half of ``06a`` needs a URL on
his screen *and* a surviving handle to poll with; that is one mechanism, it does not
exist yet, and half of it shipped early would be the half that spends.
"""

from __future__ import annotations

import argparse
import sys

from networth.config import ConfigError
from networth.plaid.client import LinkSessionShape, PlaidCallError, PlaidClient
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.rehearsal import COUNTRY_CODES, REQUIRED_PRODUCTS

SUMMARY = "Mint a Hosted Link token and poll it before completion (task 06a, F7 #2)."

#: Distinct from the rehearsal's, so a Sandbox account showing both can tell which
#: verb produced which session. It identifies a *run of this measurement*, never a
#: person — ``AGENTS.md`` rule 0.
CLIENT_USER_ID = "networth-06a-probe"
CLIENT_NAME = "networth"
LANGUAGE = "en"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--print-paths-only",
        action="store_true",
        help=(
            "resolve the environment and print the paths it selects, then stop — "
            "makes no Plaid call"
        ),
    )


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        paths = paths_for(environment)
        print(f"environment   {environment.value}")
        print(f"credentials   {paths.credentials}")
        print("persistence   none — this verb writes no database row and no token store")
        if environment is not PlaidEnvironment.SANDBOX:
            # Before `load_credentials`, so a Production secret is not read into
            # this process at all. The refusal is sharper here than in
            # `rehearse-sandbox`: that verb refuses because it would *spend* a
            # slot, this one refuses because it would *hand out something that
            # can be spent* — a Production hosted URL is one completed Link away
            # from a lifetime Item, and it would be sitting in a transcript.
            print(
                f"refusing to mint a Hosted Link URL against {environment.value!r}: the "
                "URL is openable by whoever holds it and finishing Link through it "
                "spends a lifetime Item slot (F2a). Production Link is task 08, run by "
                "the owner, and not from a measurement verb",
                file=sys.stderr,
            )
            return 2
        if args.print_paths_only:
            return 0
        credentials = load_credentials(environment)
        client = PlaidClient(credentials)
        minted = client.link_token_create_hosted(
            client_user_id=CLIENT_USER_ID,
            client_name=CLIENT_NAME,
            products=REQUIRED_PRODUCTS,
            country_codes=COUNTRY_CODES,
            language=LANGUAGE,
        )
        poll = client.link_token_get(minted.link_token)
    except (ConfigError, PlaidCallError) as exc:
        # Both types redact themselves: no response body, no credential, no token
        # material. Printing `exc` is safe because of that property, not because
        # this handler checked anything.
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2

    # Two clocks, never merged (issue #3). `expires_at` is Plaid's, for the link
    # token. `url_lifetime_seconds` is what this program asked for and is never
    # echoed back, so `None` means "did not ask, Plaid's default applies, we do
    # not know it" — which is a different fact from any number and is printed as
    # one. Measurement (i) exists to put a number on the second.
    print(f"link token    minted, expires_at={minted.expires_at!r} (Plaid's clock)")
    print(f"url lifetime  {minted.url_lifetime_seconds!r} (ours; None = not asked for)")

    # The measurement. Printed as the shape rather than as a verdict: "not ready"
    # is an interpretation, and F7 criterion 2 asks what the API actually sent.
    #
    # And scoped: `state` names which of the pre-completion states this run
    # created, because that is the line between what was measured and what the
    # criterion claims. A transcript is read later by someone deciding whether a
    # branch is covered, and a line that said "criterion 2 HOLDS" would answer a
    # question this run did not ask.
    print()
    print("state         PRE-START — minted, nobody has opened the URL")
    print(f"link_sessions {poll.shape.value}")
    print(f"sessions      {len(poll.sessions)}")
    print(f"public tokens {len(poll.public_tokens)}")
    if poll.shape is LinkSessionShape.SESSIONS_ABSENT:
        print(
            "criterion 2a  HOLDS — the key is absent in the pre-start state. 2b "
            "(started, not finished) needs a browser and is NOT measured here"
        )
        return 0

    # Not a crash and not a pass. A different shape is a real measurement of a real
    # API, and the honest outcome is to report which one arrived and let `07a`'s
    # poller be written against it. Exit 1 (not 2) says "measured, and it is not
    # what the design assumed" rather than "the run failed".
    print(
        f"criterion 2a  DOES NOT HOLD — Plaid sent {poll.shape.value} for a token "
        "nobody has opened, so §16's poller must branch on this shape and DESIGN.md "
        "records the observation, not Plaid's phrasing"
    )
    return 1
