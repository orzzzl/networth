"""``networth start-hosted-link`` — mint a Hosted Link session the owner can open.

This is the verb the owner was told existed and that did not
(``~/agents/inbox/claude/wip/20260911-1400-owner-06a-harness-first.md``). Task
``06a``'s criterion 1 and measurements (i), (ii) and (iv) all begin the same way: a
Sandbox hosted URL on the owner's screen, which he opens and completes with
``user_good``/``pass_good``. :mod:`networth.commands.probe_hosted_link` deliberately
refuses to produce one — it drops its only handle on exit, so a URL it printed could
be completed with no way left to poll for the result. **This verb is the other half
of that sentence**: it makes the handle durable, and hands the URL to the machine
that is allowed to show it.

**It does not print the URL, and that is structural.** §4 requires the second copy
of the recovery record to be on ``zelengs-macbook-air-2`` and read back *before* any
URL is displayed. This process runs on the VPS — that is where the client secret is
— so a URL printed here is by construction a URL printed before that copy exists.
What leaves instead is one marked line carrying the mint result, which
``scripts/link-start.sh`` pipes into :mod:`networth.commands.absorb_hosted_link`;
that verb writes the copy, verifies it, and prints the URL. **The line carries a
``link_token``**, so this verb refuses to run with a terminal on stdout: the only
supported caller reads it on a pipe.

**The ordering is the whole design, and it is load-bearing.**

1. mint a ``flow_id`` (§7 has it minted *before* the ``link_token``, so material
   written before any database row exists is still attributable after a restart);
2. call ``/link/token/create`` for a hosted session;
3. write the ``link_token`` into the :class:`~networth.tokenstore.TokenStore` under
   ``secret_ref_for(LINK_TOKEN, flow_id)``, ``fsync``ed;
4. **and only then** emit the flow id and the mint payload. The URL travels inside
   that payload and is shown by the Mac, after *its* copy verifies.

By **F2a** the lifetime Item slot is spent when Link *completes*, not when a token is
minted — so a crash anywhere before step 4 costs nothing at all, and a failure at
step 3 is required to emit nothing. A URL on screen whose ``link_token`` was never
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

*It does not write §4's second copy itself, and cannot.* The reason is topology
rather than effort: this process runs on the VPS, so a record it wrote would land on
*the machine whose loss §4 exists to survive*. A "second copy" beside the first is
not one. **An earlier revision of this paragraph concluded from that the ordering
belonged to ``08`` and left this verb printing the URL** — which made every run of
it a URL displayed with no second copy anywhere, the exact state §4 forbids. The
correct conclusion is that the ordering cannot be expressed in one process at all:
it needs two, on two machines, and the one that holds the URL back is the one that
wrote the copy. That is ``absorb-hosted-link``, and ``06a`` builds it rather than
deferring it (PR #75 review, option A).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime

from networth import link_recovery
from networth.config import ConfigError
from networth.plaid.client import PlaidCallError, PlaidClient
from networth.plaid.environment import (
    PlaidEnvironment,
    load_credentials,
    paths_for,
    selected_environment,
)
from networth.plaid.rehearsal import COUNTRY_CODES, REQUIRED_PRODUCTS
from networth.tokenstore import Secret, SecretKind, TokenStore, TokenStoreError, new_flow_id

SUMMARY = "Mint a Hosted Link session and emit it for scripts/link-start.sh (task 06a)."

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

        if sys.stdout.isatty():
            # Before the mint, so nothing has been created when this refuses. The
            # payload below carries a `link_token`, and a terminal is the one
            # destination that keeps it — in scrollback, in a `script` capture, in
            # a screenshot. The supported caller is `scripts/link-start.sh`, which
            # reads this on a pipe. This is not a formatting preference: it is the
            # difference between material crossing a process boundary and material
            # being displayed.
            print(
                "refusing to mint: this verb writes a link token to stdout for "
                "scripts/link-start.sh to absorb, and stdout is a terminal. Run it "
                "through that driver, or redirect stdout into one",
                file=sys.stderr,
            )
            return 2

        credentials = load_credentials(environment)
        store = TokenStore(paths.items)
        # Minted before the call, per §7. If the mint fails there is a flow id that
        # names nothing, which costs nothing; the reverse — material that arrives
        # before it has a name — is what issue #15 is about.
        flow_id = new_flow_id()
        client = PlaidClient(credentials)
        # This host's clock, stamped by the host that made the call. The Mac does
        # not re-stamp it and does not derive its own deadline from it — see
        # `MintResult.as_record`.
        minted_at = datetime.now(UTC)
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

    # Past this line the handle is durable on *this* host, so the mint result may
    # leave the process. The URL is not printed here, and that is structural rather
    # than stylistic — see the module docstring: §4 requires the second copy to be
    # verified before any URL is shown, this process runs on the VPS, and a URL
    # printed here is by construction a URL printed before that copy exists.
    print()
    print(f"flow id       {flow_id}")
    print(f"secret ref    {secret_ref}")
    print(f"link token    stored, expires_at={minted.expires_at!r} (Plaid's clock)")
    print(f"url lifetime  {minted.url_lifetime_seconds!r} (ours; None = not asked for)")
    print("hosted url    not printed here — the Mac shows it once its copy verifies")
    print()
    result = link_recovery.MintResult(
        flow_id=flow_id,
        link_token=Secret(minted.link_token),
        minted_at=minted_at,
        link_token_expires_at=minted.expires_at,
        url_lifetime_seconds=minted.url_lifetime_seconds,
        hosted_link_url=minted.hosted_link_url,
    )
    print(result.to_wire())
    return 0
