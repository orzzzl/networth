# 07a URL release resume — storage decision needed

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

## Recommended A — versioned automatic recovery record on the existing holder

Add `networth.link-recovery.2` for the new automatic driver only, at the existing
`<recovery-directory>/<flow_id>.json` path, mode 0600 in a mode 0700 directory.
The payload retains the version-1 recovery fields and adds the hosted URL as a
redacted `Secret`, plus a protocol discriminator binding it to automatic Link.

- Measurement verbs continue writing version 1 and retain their current behavior.
  The reader accepts both versions; only the new automatic driver displays a
  version-2 URL. A version-1 record cannot be silently opted into automatic Link.
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
- Update DESIGN.md's secrets inventory to name the additional short-lived bearer
  on `zelengs-macbook-air-2`. Logs, repr, errors, SQLite, argv and public evidence
  must remain free of it. Version-1 reads and hygiene behavior remain compatible.

This widens the existing holder's private recovery file from a retrieval token
(which also needs provider credentials) to an openable URL. That is a real
security distinction, even though both are short-lived and protected by the same
file permissions. It is the reason to obtain explicit review instead of treating
this as an incidental serialization field.

## Alternative B — keep the URL on the VPS

Introduce a separate hosted-URL secret kind in TokenStore and persist it before
emitting the mint. Resume retrieves that same request's URL over authenticated
SSH after release authorization; the holder's recovery file stays version 1.
This can also recover a crash before the first mint response reached the holder,
but expands TokenStore's kind set, backup inventory, reconciliation and cleanup
contract. Link-token cleanup must then retire two distinct kinds safely without
mistaking either for an access credential. No URL goes in SQLite in either option.

## Decision and implementation gate

Choose A or B. A is recommended because one existing holder record supplies both
restart inputs and one existing local reaper bounds both. The existing request,
worker, coverage-hold and audited-closure decisions from #106 stay approved.
No implementation or credential deletion is authorized by this proposal alone.
After the storage choice is reviewed, implement it in the integrated lifecycle
PR together with mint/attestation, coverage, terminal classification and reaping,
as #106 requires; do not claim 07a DONE or unblock 07b from this proposal.

Required synthetic regressions: process restart after committed release/lost ack
with exactly one mint; crash before attestation; failed read-back; wrong holder;
conflicting/existing recovery file; repeated return acknowledgement; expired and
closed request refusal; URL/token absence from database, logs, repr and argv;
version-1 compatibility; recovery reaper removes the combined version-2 record.
