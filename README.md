# networth

A personal net-worth aggregator that refuses to lie about how old its number is.

One figure — assets minus debts — rebuilt at least once a day from linked
brokerage, retirement and credit-card accounts, plus a small number of
manually-valued assets.

A Mac does the syncing on a resident schedule and keeps every credential; a
Flutter phone app is a read-only display of an encrypted snapshot. Single user,
zero marginal cost.

## Why this exists

Commercial aggregators were tried and discarded. The failure was not a wrong
number; it was a **silently frozen** one: after linking a brokerage the balance
stopped updating and nothing in the product said so. That is the classic
aggregator failure mode — the connection dies, and the UI keeps rendering the
last successfully fetched figure as if it were live.

So the product requirement here is not the number. It is the **honesty** of the
number:

- every account carries a visible `last_successful_sync`;
- an account with no successful sync in 36h is **stale**, and any total
  containing it says so;
- a connection that needs re-authentication raises an alert immediately, not at
  the next time someone happens to look.

## Status

Design phase. Nothing is implemented yet. See [`DESIGN.md`](DESIGN.md) for the
architecture and [`tasks/README.md`](tasks/README.md) for the task breakdown.

## Constraints that shape everything

- **Zero marginal cost.** No metered spend on top of subscriptions already paid
  for. If a capability costs money, the capability is dropped.
- **No 24/7 server.** A resident launchd loop on one Mac, which must keep
  working on battery.
- **Secrets and real figures never enter this repository.** It is public and
  holds code and schema only; long-lived credentials live in
  `~/agents/secrets/`, and account data lives in a local database outside the
  working tree.

See [`AGENTS.md`](AGENTS.md) for the working agreement.
