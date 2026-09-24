# 07a automatic polling and exchange worker

This component joins the reviewed observation, reconciliation and durable
finalization components. `networth poll-link --country-code <code>` performs one
pass over recorded requests, or `--flow <UUID>` selects one. It refuses Production
before loading credentials. It reads only the selected host's configured stores;
no owner completion signal is accepted and no inbound route is needed.

## Ordering and recovery

A nonblocking per-database/per-request file lock spans reconciliation, polling,
conditional SQL claim, exchange, credential storage and finalization. It fences
workers sharing that database only. Cross-host recovery still requires 07b's
power-off precondition; provider acceptance of duplicate exchanges is no fence.

Each pass first reconciles all candidate material. Durable material is finalized
without another exchange, including when metadata failed on the previous pass.
Only proven absence lets an orphaned EXCHANGING claim become EXCHANGE_UNCERTAIN.
A previously sent result never returns to the automatic exchange queue. Material
holds persist; observation holds permit recovery of existing credentials but
prevent fresh exchanges. Their reviewed adjudication commands remain the exits.

The poll ingester retains observed session/result identities and derives deadlines
from observed completion. Immediately before each send, the worker rechecks that
result's deadline and claims it with a conditional SQL update. Zero changed rows
mean no provider call. The claim and numbered attempt commit before sending.

Returned Item and request identifiers commit independently before credential
storage. Known bearer strings cannot be persisted as identifiers. Credential
storage goes through TokenStore; the finalizer alone commits the Item and the
EXCHANGED state together after metadata resolution. A storage/durability error
creates a persistent material hold. An exception or interrupted send is uncertain,
never automatically retried. If identifier capture itself cannot commit, the
worker stops; the durable claim remains available for uncertain classification
on restart. No credential or provider exception text is printed by this worker.

## Remaining task obligations

07a stays WIP and 07b remains blocked. This is a bounded worker pass, not the
completed mint/poll/reap lifecycle. Request minting and second-copy attestation,
immediate polling from the minter, URL/session terminal classification (including
ABANDONED), retention/reaping and their entry-point integration remain to follow
this component's review. Scheduling is task 16. No completed 06a flow is rerun and
its heartbeat remains PAUSED. No live Plaid calls were made for this component.

## Validation

Tests use synthetic material with real SQLite files, TokenStore durability and
cross-process locks. They exercise repeated polls, same/distinct Item identities,
not-ready response forms, individual deadlines, crash boundaries, zero-row claims,
metadata recovery, holds, identifier redaction and Production refusal. The healthy
legacy LINK_TOKEN reference regression requested in PR96 is included with and
without a result row.

An unchanged-source control and a must-fail outcome control precede eight guard
mutations. Each mutation fails named tests; restoration leaves the focused suite
green. Full local validation passes lint/format/mypy and 1315 tests with four skips;
two existing controlling-TTY tests fail because this sandbox denies /dev/tty.
Unrestricted CI must pass before approval. All fixtures and fault strings are
synthetic, and the changed files pass the repository secret scanner.
