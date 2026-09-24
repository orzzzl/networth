# 07a URL release resume — selected storage contract A

PR #106 selected conservative lifecycle A and is merged as `fdd77ee`.
Its item 3 requires lost-ack recovery without minting a replacement. During
implementation preparation, the existing second-copy format exposed a missing
storage decision. This proposal changes no runtime material and makes no API call.

## The concrete crash

1. The VPS mints and durably records request R, then sends `MintResult` over the
   authenticated pipe. That transient result carries the hosted URL.
2. `zelengs-macbook-air-2` calls `MintResult.as_record`, then writes, fsyncs and
   reads back `RecoveryRecord`. The record intentionally omits the hosted URL;
   see `networth/link_recovery.py`'s `MintResult` contract and `as_record`.
3. The authenticated return leg commits the second-copy attestation and
   monotonic release authorization on the VPS.
4. The return acknowledgement is lost and the local driver exits. It correctly
   displayed nothing. A new process loads R's recovery file, which has the link
   token, but no hosted URL to display after retrying the authorization.

An idempotent VPS acknowledgement alone cannot recover the lost local value.
The current provider `/link/token/get` response contract does not expose the
original hosted URL; do not invent a URL from the link token or a provider path.
The reviewed contract forbids storing either bearer in SQLite or argv. Therefore
restart-and-display requires a new durable home for the URL, or a weaker resume
contract. This is credential storage under AGENTS.md's review rule, not a reason
for another owner Link run.

## Selected A — versioned automatic recovery record on the existing holder

Add `networth.link-recovery.2` for the new automatic driver only, at the existing
`<recovery-directory>/<flow_id>.json` path, mode 0600 in a mode 0700 directory.
The payload retains the version-1 recovery fields and adds the hosted URL as a
redacted `Secret`, plus a protocol discriminator binding it to automatic Link.

- Measurement verbs continue writing version 1 and retain their current behavior.
  The reader accepts exactly the literal schema strings `networth.link-recovery.1`
  and `networth.link-recovery.2`; no prefix, range or numeric-version inference.
  Unknown or malformed schemas fail closed with a fixed diagnostic that echoes
  no part of the input document, including the schema value. Only the new
  automatic driver displays a version-2 URL. A version-1 record cannot be
  silently opted into automatic Link.
- Keep `FORBIDDEN_FIELDS` enforced by `from_json` for both accepted schemas,
  before record construction. Version 2 must not bypass the guessed-deadline
  guard. `url_lifetime_seconds` remains allowed in both: a requested lifetime is
  not an observed session deadline.
- Reuse `store_and_verify`'s exclusive-create, fsync and literal read-back barrier.
  Serialize the URL into that same record, not an independently managed sidecar.
  The return attestation occurs only after both the token and URL are durable.
- `--resume <flow_id>` loads that exact record, verifies local machine identity,
  re-establishes the file/directory durability barrier and read-back, and retries
  the authenticated return leg. It never invokes mint or rewrites an existing
  record. Truncated or conflicting material withholds display and remains for
  inspection; it never falls back to minting.
- The VPS return leg binds the request identity and token proof to this driver,
  records the attestation and release marker in one transaction under the worker's
  request lock, and acknowledges only after commit. Repeating it for the same
  record is idempotent; mismatched material, a closed/abandoned/expired request,
  or unknown migration provenance cannot authorize display. The marker remains
  monotonic even when an acknowledgement or the display itself fails.
- Resume displays only following a fresh successful acknowledgement. The driver
  also refuses after its existing local recovery hygiene bound; the VPS checks
  URL expiration on its own clock. Do not compare the holder's clock to a VPS
  timestamp to invent a new deadline.
- The current recovery reaper learns version 2 and deletes the one combined
  record by its existing locally measured hygiene bound. No independent URL
  orphan remains. Do not delete this request's disaster copy just because one
  child exchanges; multiple sessions remain possible until request closure.
- In the same implementation PR that introduces version 2, update DESIGN.md §15
  and rewrite `networth/link_recovery.py`'s module docstring, including its
  "copy is inert on its own" location argument and "one inert file" cleanup
  wording. Keep the location justification, replacing its premise as specified
  below; deleting it or updating only DESIGN.md is insufficient.
- The §15 inventory must distinguish the openable hosted URL's configured
  30-minute lifetime from the combined record's locally measured seven-hour
  `REAP_AFTER` hygiene bound. The URL therefore remains at rest roughly 6.5 hours
  after its spendable lifetime; the later cleanup does not extend URL validity
  or any exchange deadline. Keep one reaper for the combined record. Logs, repr,
  errors, SQLite, argv and public evidence must remain free of both bearers.
  Version-1 reads and hygiene behavior remain compatible.

The replacement location argument in the module must state: version 1 contains a
retrieval token that needs provider credentials; version 2 additionally contains
an openable hosted URL, which is not inert on its own. The file may still live in
`~/agents/secrets/networth-link-recovery/` on `zelengs-macbook-air-2` because a
compromise of the containing secrets directory already exposes `networth-vps.key`.
That key reaches the sync host's `/etc/networth/` provider credentials, which can
mint fresh hosted URLs. At that directory-compromise boundary the new URL adds
no capability the directory did not already grant; equal file permissions alone
are not the justification. An isolated recovery-file disclosure does expose a
new bearer, so its redaction and restrictive modes remain required. Preserve
this location invariant explicitly in the module when landing version 2.

A is selected because this widening stays inside that existing directory
capability boundary, while B changes the TokenStore that holds long-lived access
credentials. Avoiding a new secret kind there also avoids expanding the cleanup
path whose mistakes could mishandle an access token.

## Alternative B — keep the URL on the VPS

Introduce a separate hosted-URL secret kind in TokenStore and persist it before
emitting the mint. Resume retrieves that same request's URL over authenticated
SSH after release authorization; the holder's recovery file stays version 1.
This can also recover a crash before the first mint response reached the holder,
but expands TokenStore's kind set, backup inventory, reconciliation and cleanup
contract. Link-token cleanup must then retire two distinct kinds safely without
mistaking either for an access credential. No URL goes in SQLite in either option.

## Decision and implementation gate

Claude selected A in the review of PR #108 at `50abf715`; this revised document
still needs exact-head re-review and merge. One existing holder record supplies
both restart inputs and one existing local reaper bounds both. B's extra crash
coverage does not justify changing the access-token store: before a mint response
reaches the holder there is no recovery record and no displayed URL; minting a
replacement does not itself spend an Item slot (F2a). This does not permit
re-minting on resume when an existing record is present or corrupt.

The existing request, worker, coverage-hold and audited-closure decisions from
#106 stay approved. No implementation or credential deletion is authorized by
this proposal alone. After this document is approved and merged, implement A in
the integrated lifecycle PR together with mint/attestation, coverage, terminal
classification and reaping,
as #106 requires; do not claim 07a DONE or unblock 07b from this proposal.

Required synthetic regressions: process restart after committed release/lost ack
with exactly one mint; crash before attestation; failed read-back; wrong holder;
conflicting/existing recovery file; repeated return acknowledgement; expired and
closed request refusal; URL/token absence from database, logs, repr and argv;
version-1 compatibility; recovery reaper removes the combined version-2 record.

Reader regressions must additionally pin both exact accepted schema literals and
refuse an unknown-but-well-formed schema (for example `networth.link-recovery.99`),
prefix/suffix variants, and malformed schema types. A synthetic arbitrary schema
value and other synthetic document contents must be absent from refusal text,
including captured diagnostic output. Removing the exact-schema guard or echoing
the rejected value must fail these named regressions. For each accepted version,
exercise every `FORBIDDEN_FIELDS` entry and verify refusal, while an otherwise
valid record with `url_lifetime_seconds` remains accepted. These regressions land
with the v2 reader in the integrated implementation PR, not as claims that a v2
reader exists in this document-only proposal.
