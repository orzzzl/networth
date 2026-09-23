# 07a storage implementation and Item finalization decision

The request/session/result contract is approved in PR #88. This first
implementation change adds migration `0006`, backfills legacy evidence, replaces
the stranded view, and switches the budget reader to successful results and
explicit success observations. It does not implement or run the exchange worker.
`07a` remains WIP and `07b` remains blocked.

## Storage details for review

- Legacy `link_flow` and attempt rows remain as an audit/recovery source. Each
  migrated request records its unique old row id. Each legacy success gets one
  durable result UUID and a unique old-row mapping, including when there is no
  provider session id. No session id or public-token digest is fabricated.
- The unified success view excludes legacy evidence only when a result carries
  its legacy row mapping; a request mapping alone never suppresses a spent slot.
  Unmigrated legacy evidence remains visible, so importing legacy evidence cannot silently make it free.
  New writers must use the new tables. Older binaries reject schema version 6
  through their existing migration guard.
- Each result has its own access-token reference field. Migration does not read,
  rename or remove any credential. The worker must still reconcile both the
  original flow name and result name before exchange, as the approved contract
  requires; this change does not claim that runtime boundary is implemented.
- A success observation exists independently of result rows. An unresolved one
  raises an actionable `ItemBudgetError` naming the observation and adjudication
  needed. Its explicit resolution retains the observation, resolution timestamp,
  audit note and `additional_slots`: the adjudicated cost **beyond** Items and
  results already counted. An adjudicated duplicate adds zero. An established
  additional lost slot appears as stranded observation evidence, without a
  fabricated result UUID or exchange state. Another unresolved observation still
  refuses the whole count. The payload wire contract is unchanged.
- This schema makes recorded adjudication representable; an authorized
  adjudication command and the worker's observation deduplication remain follow-up
  implementation. The first observation writer must land with that command in
  the same PR, as required by task 07a acceptance. Direct SQL in tests is synthetic
  setup, not an operator runbook.

## Approved Item-finalization boundary (PR #91 review)

The existing `item.institution_id` is a non-null foreign key. `ExchangedItem`
contains only access token, Item id and request id. `TokenStore.reconcile`
returns credential metadata and Item identity, also with no institution.
The existing `PlaidClient.item_get` drops institution metadata, and the approved
contract explicitly excludes `institution` from stored Link poll results.
Consequently the required post-fsync local Item transaction has an input that
neither exchange nor current crash reconciliation can supply.

**A — selected by Claude in the [PR #91 review](https://github.com/orzzzl/networth/pull/91#issuecomment-5786449256):
resolve institution metadata through an authenticated read after credential
durability, retaining the existing Item schema.** Extend the client with the
read needed to establish the returned Item's institution and persist verified
metadata before finalizing the Item. A read failure preserves the credential and
pending finalization; subsequent passes reconcile material and retry only
metadata reads, never exchange. This introduces a network dependency for local
Item finalization, but never for saving the credential. Keeping the required
institution reference avoids weakening every Item consumer for a transient outage.

The worker must capture Item/request identifiers before either step and retain
`EXCHANGING` while metadata is pending. Set `EXCHANGED` only in the transaction
that commits the `item` row: an exchanged result without its Item is an orphan
fault, whereas this pending finalization is correctly reported as in-flight.
The existing five result states suffice; no new state or migration is needed.

Tests must restart through a metadata outage and prove reconciliation found the
durable material, retried metadata, and completed the Item without another
exchange. Merely asserting a zero exchange-call count does not prove recovery.
Metadata is runtime-learned and may be persisted in the institution table, but
sentinel institution values must appear in neither logs nor exception text.

This boundary does not ask the owner to repeat Link, permit a guessed institution,
or store the forbidden Link poll fields. Credential-handling implementation
follows the storage review; this PR does not implement that worker.
