# 07a lifecycle closure — reviewed conservative boundary

The polling/exchange worker is merged in PR #104 (`95123ee`). Task 07a remains
WIP. Claude approved option A on the merits in [PR #106](https://github.com/orzzzl/networth/pull/106#issuecomment-5814064075).
This contract resolves the destructive cleanup boundary before implementing
mint/attestation, terminal classification, and the VPS reaper together. No runtime
material was read and no Plaid call was made to investigate it.

## Why the scalar cleanup rules cannot be copied

The approved request/session/result split permits several sessions per URL. The
request owns the link token, which is also the HMAC key identifying result tokens.
Deleting it because one child exits or exchanges can strand another child and
remove the only way to identify a later result. A request remains reopenable
until its URL expires; marking a database row ABANDONED does not revoke that URL.

There is a second absence problem. The current [Plaid Link reference](https://plaid.com/docs/api/link/)
(checked 2026-09-24) limits session data availability to up to six hours after a
session ends. It does not give an empty late response the meaning "this URL was
never used". For example, a worker can poll an unused URL, stop for eight hours,
and return to no session records after a successful session has disappeared.
Classifying that as URL_EXPIRED with zero cost silently asserts an unmeasured
Item budget. The same problem occurs when the last known session was unfinished.
The six-hour statement is a retention limit, not a completeness guarantee.

Current code makes neither destructive decision: `ingest_poll` records evidence
without closing requests, and `run_request` does not reap. The missing decision
must be resolved before adding those paths, per AGENTS.md's credential/budget rule.

## Selected A: conservative closure with explicit coverage holds

1. Preserve the existing 06a measurement verbs. Add a distinct automatic lifecycle
   driver so a measurement flow can never silently opt into exchange. Production
   remains refused before credentials are loaded.
2. Mint flow identity first, store the link token through TokenStore, commit the
   request with the provider's Hosted Link expiration, and trigger the first
   worker pass immediately, without an owner completion signal. A poll failure
   leaves a recorded request for the next pass; it never remints automatically.
3. The driver on zelengs-macbook-air-2 writes, fsyncs and reads back its recovery
   record using the existing store. An authenticated return leg then records the
   attestation and a durable URL-release authorization on the VPS **before** the
   driver displays the URL. A lost acknowledgement withholds the URL and retains
   the request; it must be resumable without minting a replacement. This record is
   an attestation by the verified driver, not proof of future file availability.
   Neither link token nor hosted URL is stored in SQLite or passed in argv.
4. The release marker is monotonic. NULL means this new driver has never authorized
   display, provided the row is native to this protocol. Migrated rows have unknown
   release history; absence of a new marker must never certify them as unreleased.
5. A child exit/exchange is never sufficient to close an unexpired request. An
   abandon request records intent, but does not delete the token, assert zero cost,
   or prevent polling while the URL or any observed session can still succeed.
6. At URL expiry, poll before classification. Known unfinished sessions, unknown
   finish times, missing identities, poll failures and unresolved material/success
   holds prevent closure. Pending results must exchange or reach their own observed
   deadline; EXCHANGING material must reconcile before any cleanup decision.
7. **Coverage is not inferred from the current empty response.** An exposed request
   or migrated request whose history crosses the provider's retention limit without
   a conclusive final observation gets a durable coverage hold. It has no invented
   successful result or invented number of spent slots. The budget reader refuses
   an available count until explicit audited adjudication resolves the possible
   missing evidence. Reuse the merged `link_observation_adjudication`,
   `_adjudicated_slots`, and `adjudicate-link-observation` path; this does not
   rewrite task 26a. A later empty poll cannot clear this hold. Record the
   actual poll intervals over the URL lifetime, including restart/outage gaps.
   Any proposed automatic exposed-request closure must require a maximum gap
   strictly under six hours with margin, and separately justify completeness;
   frequent polling alone does not turn the retention limit into a guarantee.
   The implementation must include a named regression that skips a poll past
   the retention window and asserts a durable hold rather than clean closure.
8. **Never-released native requests have a local no-success proof independent
   of provider retention:** their URLs were never displayed by the driver.
   A native request whose URL was never authorized for release and has no success
   evidence can become ABANDONED (explicit intent) or URL_EXPIRED (expiry) and be
   cleaned up. For exposed requests, automatic zero-success closure requires a
   reviewed, explicit criterion for the final observation. **No maximum gap or
   completeness assumption is approved by this proposal alone.** Until such a
   criterion is agreed, exposed absence remains a coverage hold with an operator
   exit; it is not promoted to a proof of zero spent slots.
9. Once closure is authorized, retain link material through the latest known
   diagnostics deadline of any TOKEN_EXPIRED or EXCHANGE_UNCERTAIN child. Unknown
   deadlines and unresolved holds retain it. Successful child material is never
   deleted by link-token cleanup. Keep polling closure distinct from deletion.
10. Reaping holds the same request file lock as the worker and the same SQLite
    write transaction as ingestion. Delete only the deterministic request's
    LINK_TOKEN material, then clear its reference and stamp completion. Inspect
    legacy references through the existing reconciliation rules; never repurpose
    an access-token reference as a link-token deletion target. The three partial
    states and a crash between deletion and reference clearing must converge.

A requires a forward migration for release/abandon/coverage evidence and an
explicit audited closure command in the same implementation PR. The cost is that
ambiguous exposed requests can require operator adjudication and keep the budget
unavailable; the benefit is that loss of historical evidence cannot manufacture
headroom. An agent can perform mechanical adjudication only from established
private evidence; nothing in this proposal requests another owner Link or 06a run.

## Alternative B: establish a stronger provider closure contract first

Specify and substantiate a provider observation that closes the entire request,
including all sessions, and proves no successful result was omitted. Then use
that observation for automatic terminal classification. A successful HTTP reply,
empty list, child exit, URL expiry, or local abandon flag alone is insufficient.

B could remove routine closure adjudication, but it blocks automatic exposed-flow
cleanup until the stronger guarantee is obtained. Existing material must remain
intact in the meantime. Do not use a repeat of a consumed 06a flow as evidence.

## Review decision and binding acceptance

Claude selected A; B is not the implementation path. The task 07a acceptance
list and issue #16 now narrow cleanup to the request boundary above:
`SESSION_EXITED` still spends no slot, but never authorizes deletion of the
parent request's token. This is a cross-agent implementation/design decision;
no owner action is needed.

After approval, implement the minter, return attestation, poll/abandon/coverage
classification and reaper together, with named crash and multi-session regressions.
07a is not DONE and 07b remains blocked until that integrated path is reviewed.
