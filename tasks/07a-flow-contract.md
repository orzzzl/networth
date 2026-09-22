# 07a implementation contract — A approved

Task `07a` is WIP. This approved contract defines the implementation boundary;
it does not claim the automatic poller exists or close `07a`.
Base inspected: `27eefdd29c19cc57ec22dc5e9dd33f30594c300e`.

## The decision

Claude selected **A**, the request/session/result split, in the
[review of 6e33553](https://github.com/orzzzl/networth/pull/88#issuecomment-5782032644).
A scalar alternative cannot enforce one successful result per URL *before* a
slot is spent. The safeguards and adjudication clause were approved in
[PR #88 at 7dbd698](https://github.com/orzzzl/networth/pull/88#issuecomment-5785657017).
The cost is a forward migration and a scoped extension of the existing `26a`
budget reader; that scope must be reviewed because it changes credential
attribution and lifetime Item accounting (`AGENTS.md`, “When in doubt”).

This needs an agent design decision, not another owner browser or TTY step.
`06a` remains DONE, its exchanged flows must not be rerun, and its heartbeat
remains PAUSED. No Production call is part of this proposal or its validation.

## Evidence and limits

- `06a` observed 3 sessions and 1 public token under one link token in both the
  delayed retrieval and the cross-host measurement. Multiple sessions are real;
  this is **not** evidence of multiple successful Items in either run.
- `LinkSessionPoll.sessions` retains all sessions, and each `LinkSessionRecord`
  retains every public token plus the Item-add count, including missing tokens.
- Migration `0001` gives `link_flow` one unique `flow_id`, one unique scalar
  `link_session_id`, one `finished_at`, and one `item_id`. Its attempt table adds
  request IDs but no independent session/result identity.
- `TokenStore.reconcile(flow_id)` addresses exactly
  `access-token.<flow_id>`. A second successful result under the same flow has
  no second storage name; overwriting is correctly refused by `put`.
- `read_item_budget` counts an unresolved successful flow as one slot. Writing
  two successful results onto one row would therefore silently undercount.

The current [Plaid Link contract](https://plaid.com/docs/api/link/), checked
2026-09-22, defines a session array and defaults `enable_multi_item_link` to
false. That option limits Items **within a session**; it does not establish a
one-session-per-token invariant. Explicitly disabling it is useful, but is not
proof that a scalar flow can represent every result returned for a URL.

The same reference resolves an apparent expiry conflict in the existing code:
for Hosted Link, `link_token` and URL expire together. The response's
`expiration` can therefore populate `hosted_url_expires_at` on a confirmed Hosted
Link mint. Keep that provenance explicit and reject a mint lacking expiration
before releasing its URL. This is documented behavior, not a new live timing
measurement; the 30-minute **post-completion exchange** policy remains separate.

## A — approved storage boundary

1. Keep a request record for the minted URL and its original `flow_id`, link
   token reference, mint/URL expiry, verified second-copy attestation, poll
   status, and material reaping. The Mac recovery file continues to identify
   this request by its original flow id; `07b` does not lose its lookup key.
2. Record every observed session under the request, uniquely keyed by the
   provider session id, with observed start/finish timestamps. A session exit
   cannot erase a different active or successful session under the same URL.
   Persist only the allowlisted identifiers, timestamps, counts and outcome
   metadata: **never persist or log `accounts` or `institution` from
   `item_add_results`**, or any raw response body. Bearer material goes only to
   `TokenStore`; error output remains redacted and no runtime data enters git.
3. Give each distinct successful Item-add result a locally minted UUID, an
   exchange state, an attempt record, and its own access-token storage name.
   The result UUID is durable **before** claiming its exchange. Use
   `TokenStore` primitives with that UUID for new material; the migration-aware
   reconciliation boundary below must also check the original request name.
   A request holding several results is never treated as one credential.
4. Identifying the same public token on a later poll must not persist bearer
   material or a plain digest into SQLite. Derive a stable keyed digest using
   the stored link token as the key and a domain-separated public-token input;
   persist only the digest-to-result-UUID mapping. The worker reads material
   through `TokenStore`. Deduplication scope is this request; it does not assert
   cross-request or cross-host Item identity. Cover repeated and reordered polls
   and duplicate entries in one reply. Missing session IDs or missing tokens
   remain explicit unresolved observations, never fabricated result identities.
   If the digest key is missing, reaped, unreadable or unverified, fail closed:
   the token cannot be identified, so record an unresolved observation, mint no
   new result UUID and make no exchange call. Do not generate a replacement key
   or infer novelty from a digest lookup miss when the key is unavailable. A
   late response after reaping follows this same rule.
5. The existing ten state names remain the state vocabulary, but request and
   result facts must not be summed together. `26a` consumes successful result
   evidence and stored Items, deduplicating by returned Item identity. Preserve
   legacy flow accounting during migration; do not count migrated evidence
   twice. A result with established token identity but no returned Item identity
   counts one slot; ambiguous overlap with stored Items or unresolved success
   observations make `ItemBudgetError` report an unavailable count. This carries
   through the existing payload unavailable branch without changing its wire
   contract.
6. Every result exchange uses a conditional pending-to-exchanging update. It
   serialises workers sharing the database only. Reconcile stale claims before
   classification; never retry an uncertain exchange. Record an
   `UnverifiedMaterial` hold that subsequent automatic passes cannot reinterpret
   as absent or found without adjudication.
7. Keep request link-token material while any session/result can still need
   retrieval. Once all relevant outcomes permit cleanup, delete material first
   and clear its reference second. Reaping must also find material by the
   deterministic original request flow id after a partial reference deletion.
   `07a` must not delete another result's access credential during link cleanup.

This is a proposed extension of §7, not permission to silently add an eleventh
state or change Item-budget policy. Exact migration/backfill SQL and all code
remain the subsequent `07a` implementation after the boundary is approved.

## State ownership and the budget projection

The ten names are partitioned between entities; they are no longer ten possible
states of each request. The migration and each table's CHECK must enforce these
sets, and tests compare the budget classification with **each** schema set in
both directions. A new or unclassified state must fail that test, never default
to zero cost.

| Entity | Allowed states | Budget meaning |
|---|---|---|
| Request (`flow_id`) | `URL_MINTED`, `URL_EXPIRED`, `ABANDONED` | No independent slot evidence. Never suppress child success evidence. |
| Session (provider session id under request) | `SESSION_STARTED`, `SESSION_EXITED` | No independent slot evidence. `SESSION_EXITED` requires an ended session with no successful Item-add result. |
| Result (durable local UUID) | `SUCCESS_PENDING_EXCHANGE`, `EXCHANGING`, `EXCHANGED`, `TOKEN_EXPIRED`, `EXCHANGE_UNCERTAIN` | Every row evidences successful Link; reconcile by Item identity before counting. |

`URL_MINTED` now means the request was minted, not that no session opened it.
`SESSION_STARTED` means the session exists; successful completion is represented
by its `finished_at` and child results, not by copying a result state onto it.
Request polling closure/reaping is separate metadata (`polling_closed_at`,
`material_reaped_at`), not an eleventh state or a success summary. A successful
request can finish polling while retaining `URL_MINTED`; its children carry the
outcomes. `URL_EXPIRED`/`ABANDONED` remain proven no-success request outcomes,
never labels applied just because a timer elapsed or a sibling session exited.
Unresolved observations prevent no-success classification and automatic cleanup.

Replace `stranded_link_flow` in the same forward migration: one row per stranded
**result**, joining its request and session. Project `result_id` as well as the
original `flow_id`, `link_session_id`, returned `item_id`, result state and that
result's deadlines. Do not leave the existing scalar view in place or group
several results back into one row. Update its consumers together; result UUID
is the evidence identity, original flow id is the request/recovery lookup key.

`26a` must read Items plus all successful-result rows (not just the stranded
view), never request/session row counts.
Its classification remains: `SUCCESS_PENDING_EXCHANGE`/`EXCHANGING` are
in-flight; `TOKEN_EXPIRED`/`EXCHANGE_UNCERTAIN` are stranded; `EXCHANGED` without
its matching Item is orphaned. An existing Item takes precedence, and all
results with the same returned Item identity count once; distinct returned
identities count separately and keep every credential. A result with established
public-token identity but no returned Item identity counts **one slot**, using
A's digest deduplication: this is normal for pending/in-flight results and may
remain true permanently for expired or uncertain results. Missing Item identity
alone does not make their budget unavailable. Ambiguous overlap with stored
Items raises `ItemBudgetError` (unavailable count), including the existing
nameless-`EXCHANGED` case; do not guess whether that evidence names a stored Item.

The reader must also inspect **unresolved success observations**, even though
they minted no result UUID. While any remains unresolved, the budget is
unavailable through `ItemBudgetError`: a returned token or Item-add evidence may
already have spent a slot, and absence of a result row is not free capacity. This
includes missing session/token identity and unavailable digest-key observations.
Each observation is a hold cleared by explicit, recorded adjudication of its
possible spent slot;
the refusal must identify the hold and the adjudication needed so the owner-run
`08` script surfaces an actionable reason, including when a reaped digest key
can never be recovered. Preserve the observation and its resolution for audit;
automatic polls or elapsed time cannot clear it. Request/session states themselves
contribute zero, but cannot suppress this evidence. No Link mint may use an
unavailable budget as headroom.

Backfill each legacy success into one durable legacy-result record, preserving
its state, identifiers, deadlines and attempts; do not invent provider session
IDs when absent. Keep an explicit unique old-row-to-result mapping. Switch the
reader and view atomically with that backfill: migrated parents are never also
counted as legacy success evidence. Preserve ambiguous legacy evidence as a hold
and an unavailable budget, not a guessed mapping. Tests must cover every state,
multiple results per request, equal/distinct/missing Item identities, and equal
budgets before/after migration for unambiguous legacy records.

## Legacy credentials: reconcile both deterministic names

Existing Sandbox material is not presumed absent. No live inventory or owner
rerun is required to make this safe: support the old name during migration.
Before classifying a migrated claim or allowing a new result exchange under a
legacy request, a migration-aware reconciler calls the existing `TokenStore`
reconciliation path for **both** the result UUID (`access-token.<result_uuid>`)
and original flow id (`access-token.<flow_id>`). Each call retains the existing
final/pending-file lookup and durability barrier; do not replace it with an
existence check. Consult recorded legacy secret references as well, if present.
A result-name `None` alone says nothing about legacy material.

- Found legacy material is bound only to its mapped legacy result using durable
  request/result metadata and returned Item identity. Complete the local Item
  transaction with no exchange, retaining the original secret reference; there
  is no requirement to rename or delete the credential to upgrade the schema.
- If legacy material cannot be attributed to exactly one result, hold automatic
  exchanges for that request. Do not attach it to every child or decide that an
  unmatched token is new. Keep all material for adjudication.
- If both names exist, preserve both; only established equal Item identity may
  deduplicate budget evidence. Conflicting or unestablished attribution is a
  hold, not permission to overwrite one credential with the other.
- `UnverifiedMaterial` or any unreadable/corrupt candidate is neither found nor
  absent. Persist the hold, make no exchange and do not finalize an Item against
  unverified bytes. A subsequent pass cannot silently downgrade that hold.
- Only absence at **all applicable names** establishes local absence. Even then,
  a previous send/stale exchange claim stays `EXCHANGE_UNCERTAIN`, with no retry;
  absence is not proof that Plaid never consumed the token.

Retain this compatibility lookup for legacy requests until an explicit audited
migration retires it. Cover final and pending legacy names, both names, absent
names, missing references, unverified material, attribution ambiguity and a
crash between credential durability and database backfill. All rehearsal data
is synthetic; no already-exchanged 06a token is exercised again.

## Review conditions and implementation tests

- Multiple sessions, including an earlier exit and a later success, retain all
  support identifiers and never exchange an earlier or unrelated result.
- Multiple returned tokens get independent durable result identities. Repeated
  polls do not make another claim; missing-token results do not disappear. A
  reaped/unavailable digest key yields an unresolved observation and zero new
  result UUIDs/exchanges, including a late poll response after cleanup.
- Responses containing sentinel `accounts` and `institution` fields leave none
  of those values in SQLite, logs or exception messages.
- Returned equal Item identities count once; different identities preserve both
  credentials and count twice; established token identity without Item identity
  counts one per deduplicated result; ambiguous overlap and unresolved success
  observations produce no numeric budget, including observations with zero
  result rows. An unavailable-key hold survives automatic passes; recorded
  adjudication resolves it and restores a numeric budget accounting for the
  adjudicated slot outcome (unless another hold remains). The refusal identifies
  the hold and required adjudication for `08`.
- Capture Item and request IDs before later failures; reject `item_id` equal to
  credential material before any persistence of that identifier or `put`.
- Inject before send, after send/before response, before durable response
  capture, before credential fsync, and after fsync/before Item commit. The last
  case completes locally with zero additional exchanges. Unknown outcomes never
  retry, even if a fake provider would accept the duplicate.
- Test absent/null/empty sessions and the measured unfinished-session shape;
  derive each result deadline only from its own observed finish time.
- A failed poll cannot prove no session exists. A request may be reaped as
  unopened/abandoned only with evidence supporting a no-slot outcome; elapsed
  URL lifetime alone must not discard an unobserved successful session.
- Reaper tests cover material+reference, no material+reference, and
  material+no reference, plus a crash between deletions. Retention must not be
  computed from another session's finish time or an invented mint-time deadline.

Codex implements this approved contract in follow-up PRs reviewed by Claude;
merging this document alone does not unblock `07b` or any Production task.
