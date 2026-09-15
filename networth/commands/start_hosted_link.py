"""``networth start-hosted-link`` — mint a Hosted Link session the owner can open.

This is the verb the owner was told existed and that did not
(``~/agents/inbox/claude/wip/20260911-1400-owner-06a-harness-first.md``). Task
``06a``'s criterion 1 and measurements (i), (ii) and (iv) all begin the same way: a
Sandbox hosted URL on the owner's screen, which he opens and completes with
``user_good``/``pass_good``. :mod:`networth.commands.probe_hosted_link` deliberately
refuses to produce one — it drops its only handle on exit, so a URL it printed could
be completed with no way left to poll for the result. **This verb is the other half
of that sentence**: it prints a URL, and it pays for the right to by making the
handle durable first.

**The ordering is the whole design, and it is load-bearing.**

1. mint a ``flow_id`` (§7 has it minted *before* the ``link_token``, so material
   written before any database row exists is still attributable after a restart);
2. call ``/link/token/create`` for a hosted session;
3. write the ``link_token`` into the :class:`~networth.tokenstore.TokenStore` under
   ``secret_ref_for(LINK_TOKEN, flow_id)``, ``fsync``ed;
4. **and only then** print the flow id and the URL.

By **F2a** the lifetime Item slot is spent when Link *completes*, not when a token is
minted — so a crash anywhere before step 4 costs nothing at all, and a failure at
step 3 is required to print nothing. A URL on screen whose ``link_token`` was never
stored is the one genuinely expensive outcome: the owner can open it, Plaid can spend
the slot, and **F7** leaves ``/link/token/get`` as the only way to retrieve the
resulting ``public_token`` — a call this process can no longer make. That is a
stranded Item produced by a convenience. The refusal is free; the print is not.

**Non-Sandbox is refused before the credential is read**, and the wording is
:mod:`~networth.commands.probe_hosted_link`'s verbatim, because it is the same
refusal for the same reason and two phrasings of one rule drift apart.

**What this verb deliberately does not do, each with its reason.**

*It does not write the* ``link_flow`` *row.* §7's table exists in migration ``0001``
and **no repository reads or writes it** — that state machine is task ``07a``, which
is blocked on this very measurement. Writing rows by hand here would put a second,
untested writer in front of the machine that is supposed to own them, and ``06a``
needs a handle, not a state machine. The ``TokenStore`` entry is the durable handle;
``07a`` adopts the row.

*It does not make §4's second copy a precondition.* That mechanism — pull the
recovery record to ``zelengs-macbook-air-2``, ``fsync``, read back, and refuse to
print the URL if any of it fails — is implemented in :mod:`networth.link_recovery`
and belongs to ``08``/``link.sh``. **It cannot be satisfied by this verb**, and the
reason is topology rather than effort: this process runs on the VPS, because
``/etc/networth`` is where the credential lives, so a record it wrote would land on
*the machine whose loss §4 exists to survive*. A "second copy" beside the first is
not one, and a guard that refuses on its absence would be guarding nothing. Making
the Mac-side handshake mandatory here would also contradict the owner's own plan
table, which gives sessions 1 and 2 no Mac command at all — and it would spend that
ceremony protecting a **Sandbox** slot, which is free and unlimited. The real copy
arrives with ``08``, where the slot is one of ten and the driver runs on the Mac.
"""

from __future__ import annotations

import argparse
import sys

from networth.config import ConfigError
from networth.plaid.client import PlaidCallError, PlaidClient
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.rehearsal import COUNTRY_CODES, REQUIRED_PRODUCTS
from networth.tokenstore import SecretKind, TokenStore, TokenStoreError, new_flow_id

SUMMARY = "Mint a Hosted Link session and print its URL for the owner (task 06a)."

#: Distinct from both the rehearsal's and the probe's, so a Sandbox account showing
#: sessions from all three can tell which verb produced which. It identifies a *run
#: of this harness*, never a person — ``AGENTS.md`` rule 0.
CLIENT_USER_ID = "networth-06a-session"
CLIENT_NAME = "networth"
LANGUAGE = "en"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--print-paths-only",
        action="store_true",
        help=(
            "resolve the environment and print the paths it selects, then stop — "
            "makes no Plaid call and mints nothing"
        ),
    )


def run(args: argparse.Namespace) -> int:
    try:
        environment = selected_environment()
        paths = paths_for(environment)
        print(f"environment   {environment.value}")
        print(f"credentials   {paths.credentials}")
        print(f"token store   {paths.items}")
        print("persistence   link_token only, through TokenStore, before any URL is printed")
        if environment is not PlaidEnvironment.SANDBOX:
            # Before `load_credentials`, so a Production secret is not read into
            # this process at all. Verbatim from `probe-hosted-link`: this verb
            # hands out something spendable, which is the sharper of the two
            # reasons that refusal exists.
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
        store = TokenStore(paths.items)
        # Minted before the call, per §7. If the mint fails there is a flow id that
        # names nothing, which costs nothing; the reverse — material that arrives
        # before it has a name — is what issue #15 is about.
        flow_id = new_flow_id()
        client = PlaidClient(credentials)
        minted = client.link_token_create_hosted(
            client_user_id=CLIENT_USER_ID,
            client_name=CLIENT_NAME,
            products=REQUIRED_PRODUCTS,
            country_codes=COUNTRY_CODES,
            language=LANGUAGE,
        )
        secret_ref = store.put(SecretKind.LINK_TOKEN, flow_id, minted.link_token)
    except (ConfigError, PlaidCallError, TokenStoreError) as exc:
        # All three redact themselves: no response body, no credential, no token
        # material. Printing `exc` is safe because of that property, not because
        # this handler checked anything.
        #
        # Reaching here means no URL has been printed, which is the obligation on
        # every failure above — including a mint that *succeeded* and a store that
        # then failed. That token is abandoned deliberately: it expires on Plaid's
        # clock, nobody was given its URL, and no slot can be spent through it.
        print(f"start-hosted-link failed: {exc}", file=sys.stderr)
        return 2

    # Past this line the handle is durable, so the URL may be shown.
    print()
    print(f"flow id       {flow_id}")
    print(f"secret ref    {secret_ref}")
    print(f"link token    stored, expires_at={minted.expires_at!r} (Plaid's clock)")
    print(f"url lifetime  {minted.url_lifetime_seconds!r} (ours; None = not asked for)")
    print()
    # The two clocks are never merged (issue #3), so neither is presented as "the"
    # deadline. Measurement (i) exists to put a number on the second, and until it
    # has run, 30 minutes is operative everywhere (06a acceptance (i)).
    print("open this URL, finish with user_good / pass_good, then come back:")
    print(f"  {minted.hosted_link_url}")
    print()
    print(f"then: networth complete-hosted-link --flow {flow_id}")
    return 0
