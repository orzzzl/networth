# 07a durable Item finalization

This implementation slice supplies approved boundary A from PR #91. Task 07a
remains WIP and 07b remains blocked. No automatic worker or live Link invocation
is installed by this change.

## Implemented boundary

`PlaidClient.item_institution` performs authenticated `/item/get` and
`/institutions/get_by_id` reads after its caller has established credential
durability. It verifies both returned identities, requires an institution name
and a real boolean OAuth flag, and returns only that metadata in a redacted
project type. Null institution metadata leaves finalization pending; it does
not cause an invented institution or a replacement exchange. The SDK request
serialization and response fields were checked against the official
[Items](https://plaid.com/docs/api/items/#itemget) and
[Institutions](https://plaid.com/docs/api/institutions/#institutionsget_by_id)
references on 2026-09-22. No optional status, logo or account data is requested.

`finalize_durable_result` takes an **explicitly attributed** access-token
reference. It verifies that the reference names this result or the uniquely
mapped legacy flow, reconciles that name through TokenStore, checks the captured
Item identity, then requests metadata. Metadata failure changes neither the
result's EXCHANGING state nor its credential. A successful call commits the
institution, Item and EXCHANGED transition in one transaction, with a fresh
attribution check after the network read. A transaction failure rolls all three
back while preserving the credential.

A newly inserted Item is conservatively DEGRADED until the existing health
poller observes its status. Metadata retrieval makes no health or freshness
claim. An existing Item keeps its credential and health. A second result naming
the same returned Item stores its own access-token reference without overwriting
the Item's original credential; distinct returned Item identities get distinct
Item rows. Retry after a committed finalization is idempotent.

## Caller obligations and remaining 07a work

This primitive deliberately receives an attribution; it is **not** the automatic
reconciler or a way to select the preferred credential from ambiguous evidence.
The automatic worker must first inspect **all** applicable result and legacy
names (including recorded references), establish unambiguous attribution and
respect persisted durability/attribution holds. Passing a selected reference
alone cannot establish that other candidates are absent. An absent or unverified
selected candidate raises here; the worker must persist the required hold before
any later pass. This helper never classifies a stale claim as absent, exchanges,
clears a hold or adjudicates slot evidence.

Remaining work includes the request minter/attestation, polling and observation
deduplication, full migration-aware candidate reconciliation, exchange claims,
identifier capture before storage, durable holds, adjudication command, reaper,
and worker entry point. The first success-observation writer must still land
with its authorized adjudication command in the same PR. This slice adds
neither an observation writer nor a command that could create a hold.

## Evidence

All fixtures are synthetic. Restart tests reopen the SQLite file and TokenStore
after a metadata outage, assert that the production finalization path called
`TokenStore.reconcile`, and verify the committed Item and result. Both final and
pending-file material are exercised. The metadata-only client interface has no
exchange method. Other tests cover rollback at the result transition,
concurrent attribution changes, legacy mapping, absent/unverified/wrong
material, duplicate and distinct Items, SDK serialization, malformed metadata,
and redaction of sentinel institution and credential values. Direct tests of the
metadata Protocol boundary also pin timestamp awareness, access-token reference
kind, conflicting captured references, absent material identity, incomplete
EXCHANGED identity, and malformed or credential-echoing metadata. Each guard is
checked by a targeted deletion mutation, with a no-op control.

These tests validate the finalization component, not the full automatic worker,
Production, or the completed 06a measurement flows.
