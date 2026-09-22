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
- The unified success view excludes migrated parents. Unmigrated legacy evidence
  remains visible, so importing legacy evidence cannot silently make it free.
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
  implementation. Direct SQL in tests is synthetic setup, not an operator runbook.

## Decision needed before the exchange worker's final transaction

The existing `item.institution_id` is a non-null foreign key. `ExchangedItem`
contains only access token, Item id and request id. `TokenStore.reconcile`
returns credential metadata and Item identity, also with no institution.
The existing `PlaidClient.item_get` drops institution metadata, and the approved
contract explicitly excludes `institution` from stored Link poll results.
Consequently the required post-fsync local Item transaction has an input that
neither exchange nor current crash reconciliation can supply.

**A — recommended: resolve institution metadata through an authenticated read
after credential durability, retaining the existing Item schema.** Extend the
client with the read needed to establish the returned Item's institution and
persist verified metadata before finalizing the Item. A read failure preserves
the credential and pending finalization; subsequent passes reconcile material
and retry only metadata reads, never exchange. This introduces a network
dependency for local Item finalization, but never for saving the credential.
The worker must distinguish pending metadata from an uncertain exchange, and
capture Item/request identifiers before either step. Tests must restart through
a metadata outage and prove zero additional exchange calls.

**B — permit an Item with unknown institution metadata.** Make the institution
reference nullable and enrich it later. This keeps credential reconciliation
entirely local, but changes the Item schema's invariant and requires auditing
every Item consumer for the unknown-institution case.

Neither option asks the owner to repeat Link, permits a guessed institution, or
stores the forbidden Link poll fields. Claude should select the boundary on the
PR before its credential-handling implementation; this is the repository's
review requirement for changes touching credentials, not an owner decision.
