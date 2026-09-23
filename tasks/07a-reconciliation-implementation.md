# 07a credential candidate reconciliation

This component implements all-candidate credential reconciliation and persistent
material holds from the approved flow contract. Task 07a remains WIP and 07b
remains blocked. It installs no worker and makes no live calls.

## Reconciliation and persistence

`reconcile_request` inspects every result UUID, the original request UUID, and
recorded legacy/result access-token references. Every candidate goes through
`TokenStore.reconcile`, retaining its final/pending lookup and durability barrier.
A failure does not stop inspection of sibling candidates. Deterministic result
names belong to that result; original-flow material belongs only to the uniquely
mapped legacy result. A recorded foreign reference is inspected but cannot grant
attribution. Equal returned Item identities preserve both credential names;
conflicting identities or missing/corrupt/unverified evidence cannot authorize
finalization or exchange.

Migration 0008 records material holds separately from success-observation slot
adjudications. An open hold blocks the entire request and makes the Item budget
unavailable with an actionable hold identifier. It persists across restart, even
if a later automatic pass could read the material or find it absent. No previous
result state, Item identity, credential or observation is rewritten to clear it.
A transaction failure refuses reconciliation rather than returning partial facts.

The local `adjudicate-link-material` command requires explicit `NETWORTH_ENV`,
`--hold`, `--audit-id` and `--confirm-reviewed`. The confirmation attests that
workers are quiesced and credential attribution and every possible spent slot
have been reviewed. The audit UUID identifies private evidence outside git.
Host filesystem access is the authorization boundary. Resolution records an
immutable decision on that hold and permits **reinspection only**, not a found
or absent verdict, budget adjustment, exchange retry or credential deletion.
Still-unsafe material creates a new hold with a new identifier. Success-observation
adjudication remains separate, and other unresolved holds continue to block.

The budget refusal applies even if all currently identified Items are already
counted: ambiguous material cannot be presumed to represent no additional slot.
This is the approved unavailable-count boundary, not a new numeric slot estimate.

## Worker obligations

The returned reconciliation is a snapshot. The worker must retain a per-request
lock across reconciliation, conditional exchange claim, send and storage. SQLite's
transaction serializes this inspection with ingestion/adjudication but cannot
protect a later network operation after the transaction commits. Adjudication
requires workers to be quiesced. All material must be reinspected before acting.

Local absence never proves an earlier exchange failed remotely. A stale claim
with no material must become uncertain and must not retry. Found material goes
to the approved durable finalizer; unresolved material/observation holds forbid
exchange. These transitions remain worker integration work.

Remaining 07a work: request mint/second-copy attestation, automatic polling and
request locking, conditional claims, identifier capture before storage, crash
classification/finalization orchestration, retention/reaping and entry points.
The reconciler and durable hold boundary require review before that integration.
No completed 06a flow is rerun; its heartbeat remains PAUSED.

## Validation

Tests use real SQLite files and synthetic TokenStore material, restart through
both deterministic names and pending-file recovery, force a real pending-file
fsync failure, preserve equal/distinct Item credentials, and check persistence,
redaction, audit refusal, all-candidate inspection and independent budget holds.
The two non-blocking PR94 coverage requests also land here: tokenless success in
a later session reopens earlier adjudication, and direct observation adjudication
requires literal `True`. Targeted mutations verify those assertions and the
critical reconciliation/hold guards, with an unchanged-source control first.
