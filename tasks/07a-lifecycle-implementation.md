# 07a integrated automatic Link lifecycle

Implements the reviewed conservative lifecycle A (PR #106) and combined recovery
record A (PR #108) over the merged worker. Task 07a remains WIP pending review;
07b stays blocked. No live call, credential inspection, owner Link run, or
scheduler installation is part of this change. The 06a heartbeat stays PAUSED.

## Entry points and ordering

On zelengs-macbook-air-2, `NETWORTH_ENV=sandbox uv run networth automatic-link
--commit <reviewed-full-commit>` verifies the pinned holder identity and a clean
checkout at that commit. It invokes the existing source/hash-verified remote
runner over authenticated SSH with the distinct `mint-automatic-link` verb.
The minter checks budget, mints flow identity first, stores LINK_TOKEN, commits a
native-protocol request, then runs the first poll immediately. Poll errors leave
that request for `poll-link`; no owner completion prompt is used.

The holder captures the mint, exclusively writes/fsyncs/reads back the combined
version-2 recovery record, then sends its attestation over SSH stdin to
`authorize-automatic-link`. The VPS checks token proof, protocol provenance,
holder, request state and its own expiry clock under the request lock and SQL
write transaction. It commits the monotonic release marker before acknowledging.
Only that fresh acknowledgement permits URL display. Bearers never enter argv,
SQLite, diagnostics or repr. Public runner source in a shell argument contains
no runtime material. Captured remote stdout/stderr is never forwarded.

`--resume <flow-id>` re-establishes the existing file and directory durability
barriers and literal read-back before repeating the return leg. It never mints or
rewrites the recovery record. Missing, conflicting, expired, truncated or v1
records refuse. Measurement mint/absorb behavior stays version 1; measurement
retirement cannot delete a version-2 record after one child's exchange. The
existing holder reaper removes the entire combined record at its local seven-hour
hygiene bound. Production is refused by every new entry point.

## Closure and cleanup

Migration 0009 leaves every existing request's release provenance unknown. Only
the new minter opts into native provenance. It preserves the existing observation
ledger while adding COVERAGE_UNPROVEN and stores successful/failed poll instants.
Coverage has no invented result and no reported-result count. The existing budget
reader and audited `adjudicate-link-observation` command handle it unchanged.

`poll-link` now runs the integrated lifecycle: recover/poll/exchange, conservative
classification, then reaping, all under the worker's request lock. An exposed or
migration-unknown request takes a durable coverage hold at expiry or a six-hour
history gap. Frequent empty polls also do not prove completeness. A later empty
poll cannot clear or reopen an adjudicated coverage set; later success evidence
reopens it through the existing ingestion rule and preserves earlier decisions.

`abandon-link-request --flow <id>` records intent. It neither revokes a URL nor
stops polling a released request. A native never-released request without any
session/result/observation evidence can close on abandonment or expiry through
local no-success proof. For exposed requests, quiesce polling, establish private
evidence, record its additional-slot count with `adjudicate-link-observation`, then
run `close-link-request --flow <id> --audit-id <private-record-uuid>
--confirm-reviewed`. Closure requires an expired URL, successful post-expiry poll,
finished known sessions, resolved holds and no pending/exchanging child. Successful
parents retain URL_MINTED; polling_closed_at records closure separately. Only
proven no-success parents receive URL_EXPIRED or ABANDONED.

The reaper reconciles all credential candidates first, keeps uncertain/expired
children's link material through the latest known diagnostics deadline, and retains
unknown deadlines. It holds BEGIN IMMEDIATE across deletion and reference clearing,
deletes only the deterministic LINK_TOKEN name, and never follows an access-token
reference as a deletion target. Both missing-material and missing-reference states
converge; a crash after deletion rolls back SQL and the next pass completes it.
Successful child access credentials remain intact.

Scheduling remains task 16. Cross-host recovery remains task 07b and needs its
power-off fence; neither this lock nor a provider duplicate-exchange response
supplies that fence.

## Validation

Synthetic tests use real SQLite, TokenStore and recovery files; no runtime secret
is read. They cover mint-to-poll ordering, lost acknowledgement with one mint,
crash before return attestation, durability/read-back failures, exact schema
literals and forbidden deadlines, migrated provenance, retention gaps, multi-session
success/exit, operator closure, diagnostics retention and all reaper partial states.
A separate process verifies the worker file lock during deletion; a second SQLite
connection verifies the concurrent writer refusal. Existing measurement, worker,
observation, migration and backup tests run with the full suite. Local sandbox
restrictions on /dev/tty affect two existing TTY tests; unrestricted CI is required.
