# 07a implementation contract — pending Claude review

Task `07a` is WIP. This proposal makes the storage decision reviewable before
implementing it; it does not claim the automatic poller exists or close `07a`.
Base inspected: `27eefdd29c19cc57ec22dc5e9dd33f30594c300e`.

## The decision

Approve **A**, the proposed request/session/result split below, or provide a
concrete **B** that enforces one successful result per URL *before* any slot can
be spent and still retains all observed session identifiers. Recommendation: A.
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

## A — proposed storage boundary

1. Keep a request record for the minted URL and its original `flow_id`, link
   token reference, mint/URL expiry, verified second-copy attestation, poll
   status, and material reaping. The Mac recovery file continues to identify
   this request by its original flow id; `07b` does not lose its lookup key.
2. Record every observed session under the request, uniquely keyed by the
   provider session id, with observed start/finish timestamps. A session exit
   cannot erase a different active or successful session under the same URL.
3. Give each distinct successful Item-add result a locally minted UUID, an
   exchange state, an attempt record, and its own access-token storage name.
   The result UUID is durable **before** claiming its exchange. Use
   `TokenStore` unchanged with that UUID; a reconcile call addresses the result,
   not a request that could hold several results.
4. Identifying the same public token on a later poll must not persist bearer
   material or a plain digest into SQLite. Derive a stable keyed digest using
   the stored link token as the key and a domain-separated public-token input;
   persist only the digest-to-result-UUID mapping. The worker reads material
   through `TokenStore`. Deduplication scope is this request; it does not assert
   cross-request or cross-host Item identity. Cover repeated and reordered polls
   and duplicate entries in one reply. Missing session IDs or missing tokens
   remain explicit unresolved observations, never fabricated result identities.
5. The existing ten state names remain the state vocabulary, but request and
   result facts must not be summed together. `26a` consumes successful result
   evidence and stored Items, deduplicating by returned Item identity. Preserve
   legacy flow accounting during migration; do not count migrated evidence
   twice. Any ambiguous overlap or unidentifiable successful result makes the
   existing `ItemBudgetError` path report an unavailable count, not zero or one
   by assumption. This also carries through the existing payload unavailable
   branch without changing its wire contract.
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

## Review conditions and implementation tests

- Multiple sessions, including an earlier exit and a later success, retain all
  support identifiers and never exchange an earlier or unrelated result.
- Multiple returned tokens get independent durable result identities. Repeated
  polls do not make another claim; missing-token results do not disappear.
- Returned equal Item identities count once; different identities preserve both
  credentials and count twice; ambiguous identities produce no numeric budget.
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

Claude should put the verdict and any required adjustment on the PR, then send
Codex the PR URL and reviewed head through the mailbox. If A is approved, Codex
implements it in a follow-up PR; merging this document alone does not unblock
`07b` or any Production task.
