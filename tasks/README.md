# Task breakdown

Drafted during the design phase. **Nothing here is assigned and nothing is
started** — task assignment is itself subject to cross-review, per the owner's
instruction. Status vocabulary:

- `BLOCKED` — a dependency or an owner decision is outstanding.
- `READY` — dependencies met; may be assigned.
- `WIP` / `DONE` — claimed / merged.

Everything is `BLOCKED` until this design PR merges. Numbers are stable from the
merge of this PR onward; dependencies are by number. (Revision 2 added `03a`,
`12a`, `12b` and `19a` and tightened several dependency edges after review.
Existing numbers were kept stable and new work given letter suffixes so review
references stay valid; nothing had been assigned or started, so no work was
disturbed. **Revision 3** added one owner gate, `00a` — the backup destination is
a decision only the owner can make and it gates 08 through 03a. Its other six
findings were contradictions *between* sections, so they land as sharper
acceptance criteria on 03a, 11, 12a, 14, 15, 18, 19, 19a, 20, 21 and 22, plus
**three corrected dependencies** — `03a` now needs `00a`; `19a` now needs `20`,
because the pairing control path it promises lives in the Worker that 20 builds;
and `06` now names `00`, since Sandbox needs the dashboard account even though it
does not need O2's answer — and **O2's block scope narrowed to match this graph**
instead of contradicting it. The sequencing section below was re-ordered to
match; a graph and a plan that disagree is the same class of defect the review
was catching.)

## Phase 0 — gates (owner-only, nothing can ship before these)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 00 | Create the Plaid account, get the Trial plan approved, **verify the in-scope brokerages are reachable on Trial (O2)** | — | BLOCKED (owner) | Runbook `DESIGN.md` §19 step 1. **Go/no-go for the Plaid link path** (07/07a/08 and everything downstream of a real Item), not for the foundation — a `NO` changes how accounts get linked, not the schema, the staleness machine, the manual-asset path or the transport (`DESIGN.md` §18). Task 00 also produces the dashboard credentials that Sandbox work needs, which is a *separate* dependency from O2's answer. |
| 00a | Choose the backup destination and escrow the backup key (**O8**) | — | BLOCKED (owner) | Runbook `DESIGN.md` §19 step 1a. Owner-only because only the owner knows what storage exists. Feeds task 03a, which gates 08. |
| 01 | UI target | — | **ANSWERED** | The owner chose a **Flutter phone app**. Number retained so later references stay valid. Its consequence is Phase 4, not a task of its own. |

## Phase 1 — foundation (Mac side; UI- and transport-agnostic, and the bulk of the work)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 02 | Project scaffold: package layout, venv, format/lint/test, CI, `scripts/check-no-secrets.sh` as a pre-commit hook and a CI job | — | BLOCKED (design PR) | Can start before 00 lands; touches no credential. The secret/figure scanner exists from the first commit because the repo is public. |
| 03 | SQLite schema + migration runner | 02 | BLOCKED | `DESIGN.md` §7 verbatim. Integer minor units, UTC timestamps, **two clocks per observation** (`fetched_at` / `source_as_of` + `source_clock`), `lineage_id` on accounts, `snapshot.sync_run_id` UNIQUE. **No `profile_id`** — dropped in review as speculative generality. |
| 03a | **Encrypted backup + restore + a passing restore drill** | 03, 05a, **00a** | BLOCKED (O8) | `DESIGN.md` §14a **and §14a.1**. Backs up the DB **and** the `TokenStore`; never goes to a *third party*, but it **must leave the disk** — the rev-2 version let the DB, the archive and the key share one device and still called a temp-directory restore "verified", which proves the archive parses and nothing about the Mac dying. Three acceptance criteria, all owner-controlled and free: (1) `backup_destination` resolves to a **separate failure domain**, checked mechanically on **every** backup (`stat -f %d` against the DB's volume, or confirmed remote) and **failing loudly** on the same device; (2) `networth backup attest-key` records `key_escrow_confirmed_at` — an **attestation, not a proof**, since no agent can verify a password manager; (3) `scripts/restore-drill.sh` pulls the archive **back from `backup_destination`**, over the path a real recovery would use, then verifies row counts and token *fingerprints* (never tokens) and records `last_verified_restore_at`. **Hard gate on task 08:** after the first Production Link, a lost token strands a permanent Item slot (F2 + F6), so this cannot wait for Phase 5. If the owner has no second device the gate cannot be met and **O8** is the explicit decision — not a check to weaken. |
| 04 | Domain model + `Store` repositories; append-only observations/snapshots | 03 | BLOCKED | The seam everything else reads through. No I/O beyond SQLite. Snapshots append per successful run, idempotent on `sync_run_id`; nothing is edited in place. Queries never assume a fixed set of accounts (§2 reservation 2). |
| 05 | `PlaidClient` wrapper + **error taxonomy mapping** to our states | 02 | BLOCKED | The one place Plaid errors become `DEGRADED` / `NEEDS_REAUTH` / `REVOKED` (§8.2). Must cover `ITEM_LOGIN_REQUIRED`, `PENDING_EXPIRATION`, `PENDING_DISCONNECT`, `USER_PERMISSION_REVOKED`, `ITEM_NOT_FOUND`. Unit-tested against synthetic fixtures. |
| 05a | `TokenStore`: narrow interface over secret storage, mode-600 file backend | 02 | BLOCKED | §2 reservation 3. Small, but it must exist before anything reads a token, or file reads will scatter. |
| 06 | Sandbox end-to-end rehearsal of the Link flow | 05, 05a, **00** | BLOCKED | **Must pass before any Production Link.** Sandbox is free and unlimited; Production slots are permanent (F2). Depends on 00 for the **dashboard credentials** — which is a *different* dependency from O2's answer (`DESIGN.md` §18): Sandbox rehearsal is worth doing whatever O2 says, and needs only that the account exists. |

## Phase 2 — linking (spends the scarce resource)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 07 | Static OAuth redirect page + free hosting + dashboard registration | 00 | BLOCKED | Holds no data and no secrets. HTTPS required; `localhost` is Sandbox-only. |
| 07a | *Spike (optional):* automatic `public_token` handoff to the waiting local process | 07 | BLOCKED | Nice-to-have. Copy-paste is the shipping path and is never a blocker. |
| 08 | `scripts/link.sh` — owner-run Link, token exchange, `TokenStore` write, item record | 04, 06, 07, **03a** | BLOCKED | The owner runs it; agents never do. Must confirm the exact institution *and login* before exchanging. **Cannot start until the restore drill passes** — this is the task after which data loss costs permanent slots. |
| 09 | `scripts/relink.sh` — Link **update mode** for an existing item | 08 | BLOCKED | Consumes no slot. The recovery path for `NEEDS_REAUTH`; the naive remove-and-relink must never be implemented (F2). |

## Phase 3 — sync and the honesty machinery

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 10 | Item health poller (`/item/get`, hourly) | 04, 05 | BLOCKED | The **floor** for I3 — every Item error state visible to `/item/get`, within an hour, with zero infrastructure. Also records `status.investments.last_successful_update`, which feeds the holdings source clock (§8.1). |
| 11 | `StalenessMachine`: **two axes** — connection state per Item, data freshness per account from source clocks | 04, 10 | BLOCKED | `DESIGN.md` §8.1–§8.2, §9.2. Heavily unit-tested — **this is the product**. Required cases: a call that succeeds while `source_as_of` never advances (the frozen-number failure, and the reason this task exists); `UNKNOWN` freshness never rendering as fresh; weekends, holidays and half-days on the market-close clock; a carried-forward observation continuing to age. **Axis B escalates:** merely behind its expectation window is `WAITING` (institutions post late constantly — alerting there burns the signal on noise), but Item `HEALTHY` + source clock frozen for **five consecutive market days** becomes the named `FROZEN` state, which is `ACTION_NEEDED` on screen *and* alerts. The threshold is **read from the single definition in §11**, not re-declared here or in 15 — the two carrying their own copies is what made the display and the runbook contradict each other in rev 2. **`UNKNOWN` never escalates**: under `balance_mode: cached` it is permanent, no re-link can conjure a clock, so it stays a standing caveat rather than a wait. |
| 12 | Full sync: holdings + balances → observations; carry-forward on failure | 04, 05, 11 | BLOCKED | Records `fetched_at` and `source_as_of` **separately**, plus which evidence produced the latter. Holdings take the oldest contributing `institution_price_*`; balances use `/accounts/balance/get` under `balance_mode: realtime` (F5) and are `UNKNOWN` under `cached`. Sets `is_carried_forward` honestly; a carried-forward row never advances its source clock. |
| 12a | Webhook drain: Worker receive route + queue + **Mac-side** Plaid JWT verification | 05, 10, 20 | BLOCKED (O5/O7) | `DESIGN.md` §8.4.1–§8.4.3. **The queue is one unique KV key per event** (`hook:<received_at_ms>:<uuid>`, `expirationTtl` 7d) — KV has no append and no transaction, and rev 2's "appends to a KV queue" would have silently dropped events exactly when several arrive together. The Worker stores the **raw request bytes base64'd, never re-serialized** (`request_body_sha256` is whitespace-sensitive; a Worker that round-tripped the JSON would break every signature and look like an attack). The Mac drains **through the Worker's `GET`/`DELETE /hook/queue` routes, never the KV API** (a Cloudflare API token on the Mac is the thing §8.4.3 refuses), verifies, inserts, and **deletes last as the ack** — at-least-once, so duplicates are normal and `UNIQUE(body_sha256, jwt_iat)` makes a redelivery a no-op. Verifies ES256 via `/webhook_verification_key/get` and matches `request_body_sha256` in constant time. **`iat` is checked against the Worker's `received_at`, never the drain time** — two five-minute windows in series would otherwise reject genuine events, intermittently. Captures `PENDING_DISCONNECT`'s `reason`/`disconnect_time` and `USER_PERMISSION_REVOKED`, which polling cannot derive. **Advisory only** — a dropped webhook must never change the number, only delay a warning. |
| 12b | Replacement-Item reconcile flow (`scripts/reconcile.sh`) | 04, 09 | BLOCKED | `DESIGN.md` §8.5. New accounts start `NEW` and contribute nothing; the owner confirms an old→new mapping; `lineage_id` carries history across the seam. Tests must cover the two silent disasters: double-counting and a severed curve. |
| 13 | Manual assets: static property value + share count × live quote | 04 | BLOCKED (O4) | `QuoteClient` reuses the quotes integration from the sibling project; the quote must carry its own `as_of` and that `as_of` is the source clock. Ticker and quantities are configured at runtime, never hardcoded. |
| 14 | Snapshotter + net-worth computation + presentation contract | 12, 13 | BLOCKED | A total cannot be constructed without its **age state** + staleness counts (**I2**) — enforce it in the type, not by convention: the total is a sum type (`Dated(as_of) | Undated(reason)`), so rendering code cannot reach a date that does not exist (`DESIGN.md` §10.3b). The age is the tagged `(age_state, as_of)` of §8.1 **R3** over the *age basis* — never `oldest_contributing_source_as_of`, which was the rev-2 scalar and could not be filled honestly. Required tests: **all contributors known** → `KNOWN` with the oldest source clock; **mixed known + unknown** → `UNKNOWN` with **no date** (the case that must never borrow the oldest known timestamp); **all unknown** → `UNKNOWN`; **fixed-value assets only** → `STATIC_ONLY`; a `MANUAL_STATIC` property **never** dragging the headline age to its purchase date. Stale and `UNKNOWN` counts stay separate; `static_account_count` is its own count; unreconciled accounts contribute nothing and force `is_complete = FALSE`. |
| 15 | Alerts: `alert` table, macOS notification, mailbox escalation, anti-fatigue policy | 11 | BLOCKED | `DESIGN.md` §11. State written before notifying, so a crash re-notifies rather than losing the alert. macOS row: `NEEDS_REAUTH`, `REVOKED`, **frozen data** (Item healthy, source clock stuck — threshold owned by §11 and shared with task 11, not re-declared), **publication overdue**, **read-back mismatch** (§9.3 — the transport served back something other than what was just published), **pending reconciliation**, and **drain stalled** (a queued webhook still undrained after an hour, without which a broken drain stays invisible until the TTL destroys the evidence). **A rejected rollback is deliberately NOT here** — it is phone-local (task 22); rev 2 promised the Mac an alert about an event only the phone can observe, over a channel the architecture does not have. |
| 16 | launchd `KeepAlive` loop + due-ness engine + catch-up after sleep | 10, 12, 14, **15** | BLOCKED | Mirrors `~/agents/bin/tick-loop.sh`. **No `StartInterval`, no battery guard.** Depends on 15 because I3 promises an *alert* within an hour, not a recorded state. |

## Phase 4 — getting the number onto the phone

The half of the project the UI decision created. 17–20 are Mac-side; 21–24 are
the app.

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 17 | `NetWorthQuery` read layer (totals, history, per-account staleness) | 14 | BLOCKED | Pure reads. The only surface a UI or a payload builder may touch. History joins on `lineage_id` so a re-link does not break the curve. |
| 18 | CLI: `networth show` / `history` / `doctor` | 17 | BLOCKED | First consumer; proves the seam before a phone exists — and the first place the tagged age of task 14 is rendered, so `show` must be able to print a total with **no date** without reaching for one. `doctor` prints both clocks per account, item states, remaining Item slots, days since the last verified restore drill, **the backup destination and whether it is in a separate failure domain**, **`key_escrow_confirmed_at` labelled as the owner's own attestation** rather than a verified fact (§14a.1), the age of the last successful publication, and the last read-back result. |
| 19 | Payload schema (versioned) + `Publisher`: serialize → AES-GCM encrypt → publish → **read back**, with a `publication` audit row | 17 | BLOCKED | The Mac↔phone contract. Carries `published_at`, `publish_interval_seconds`, `grace_seconds` and a monotonic `seq`; `schema_version`, `pairing_id`, `seq` and `published_at` go in the AAD. **The payload contract carries the total's age as the tagged `(age_state, as_of)` of task 14** — `as_of` present *only* when `age_state = KNOWN` — plus `static_account_count`; a schema that lets a caller read a bare date is the bug this contract exists to prevent. **`seq` is never reset across pairings** (§6.3.1), so a payload from an earlier pairing can never present a higher `seq` than the current one. Every publish is followed by a **read-back** asserting the transport serves the `seq` just written, recorded in `publication.readback_ok`/`readback_seq` and alerting on mismatch (§9.3); it costs one request a day and is the Mac-observable half of transport integrity. Ciphertext only ever leaves the machine, whatever transport 20 picks. |
| 19a | Pairing: `networth pair` / `networth revoke`, the **Worker-side control path**, and the app-side secure-storage sink | 19, **20** | BLOCKED (O5/O7 via 20) | `DESIGN.md` §6.3 **and §6.3.1**. Rev 2 minted a read token on the Mac and claimed re-pairing "invalidates" it with **no mechanism by which the Worker could ever learn either fact** — not slow rotation, *unimplementable* rotation. So: `POST /pairing/rotate` and `POST /pairing/revoke`, both **write-token** authorised, each atomically replacing/clearing the active pairing **and deleting the stored snapshot** (revoking the token stops the stolen phone fetching new ciphertext; deleting the object removes what its key could still decrypt — both halves, one route). **The Worker stores `SHA-256(read_token)`, never a token**, compared in constant time, so leaking KV yields no working credential. Rotation order is the acceptance criterion: mint `PENDING` → rotate → **only then** mark `ACTIVE`/`revoked_at` → publish → **render the QR last**, so a QR on screen always means a phone that will work, and a failure never leaves the *old* token working. Depends on 20 because the routes live in the Worker. **No secret is ever compiled into the APK**; rotation is a re-pair, not a rebuild. App side stores via `flutter_secure_storage` (Android Keystore). |
| 20 | Transport backend (**six Worker routes**) + publication-freshness monitoring | 19 | BLOCKED (O5/O7) | Implements whichever of §6.2's candidates the owner picks. On the Cloudflare path the Worker carries **all six** routes of §16: `PUT /snapshot` (write), `GET /snapshot` (read-verifier **or** write, so the Mac can read back), `POST /pairing/rotate` + `POST /pairing/revoke` (write — task 19a's control path), `POST /hook/<unguessable>` (unauthenticated by necessity; Plaid cannot present ours), and `GET`/`DELETE /hook/queue` (write — 12a's drain and ack). It grew from three routes to six in review; **every addition is a control path rev 2 assumed already existed**, which is why the count went up while the trust placed in the Worker went down — it holds no Plaid credential, no read token (only a hash), and no payload key. Behind the `Publisher` seam, so the choice stays reversible. Monitoring is evidence-based — "the last successful publication is older than expected" — not expiry arithmetic, and it is not optional: a transport credential dying silently freezes the phone's number, this product's cardinal sin arriving through the back door. |
| 21 | Flutter app skeleton: fetch → verify `seq` → decrypt → local cache → headline **with both staleness dimensions** | 19 (+ fixture transport) | BLOCKED (O6) | Read-only display; holds no Plaid token and never calls Plaid. **Fetches through a fixture-backed seam so it does not wait on 20 or O5.** Its acceptance criterion includes **I4**: there is no intermediate state in which this app renders a bare headline — that would be shippable and would be the exact lie the project refuses. **It must render all three age states** (§8.1 R3): `KNOWN` shows the date; `UNKNOWN` shows **"can't date this total — N of M accounts can't be dated"** and *no date anywhere near the headline*; `STATIC_ONLY` says so — which is the real state before the first Item is ever linked, not a theoretical one. Fixtures must include the mixed known/unknown case, because that is the one where a plausible implementation quietly prints the oldest known date. 22 deepens the treatment; it does not introduce it. |
| 22 | Full dual-staleness UI + replay handling | 21, 19a | BLOCKED | `DESIGN.md` §9. Clock-skew `COPY_UNKNOWN`, suppression of per-account "fresh" badges under a stale copy, and the rejected-rollback warning (**I6**). The two `COPY_STALE` reasons are an **exact predicate, not a vibe** (§9.1): `MAC_NOT_PUBLISHING` iff `last_fetch_success_at >= stale_after` **and** `last_fetch_attempt_at == last_fetch_success_at` **and** `last_fetch_seq == last_seq`; `CANNOT_CHECK` otherwise. Rev 2 said "`last_fetch_at` is recent", which is untestable and conflated an attempt with a success — so the app persists `last_fetch_attempt_at`, `last_fetch_success_at`, `last_fetch_error`, `last_fetch_seq` **and** `last_seq` as five distinct facts. The rejected-rollback warning is **phone-local and persistent** — it survives restarts until a `seq` greater than `last_seq` arrives, and it **never reaches the Mac**: adding a phone→Mac report channel would trade away the read-only asymmetry that makes a lost phone a non-event (§9.3). |
| 23 | History curve, with incomplete snapshots visually distinct | 21 | BLOCKED | Dashed/hollow for `is_complete = FALSE`. A gap in the record must look like a gap. |
| 24 | Release signing + delivery discipline | 20, 21, **22** | BLOCKED | Depends on 22 so a build that can collapse the two staleness dimensions cannot be delivered, and on 20 so the delivered app has a real transport. Keystore outside the repo; **no secrets in the build** (§6.3). Signed from the first delivered build — a debug→release signature change later forces an uninstall. Version bump per `AGENTS.md`. |

## Phase 5 — operations

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 25 | ~~DB backup/restore~~ | — | **SUPERSEDED by 03a** | Number retained so review references stay valid. Moved into Phase 1 and made a gate on 08: backups scheduled *after* Production linking protect nothing during the window that matters. |
| 26 | Item budget tracking + surfacing remaining slots | 04, 08 | BLOCKED | Running out is invisible until it isn't (F2). Reads `replaces_item_id` so a replacement's cost is visible. |
| 27 | Vest-date nudge to re-confirm a manual share count | 13, 15 | BLOCKED | Manual quantities drift silently — the same failure this project exists to prevent, arriving from the manual side. |

## Suggested sequencing (not an assignment)

1. **Now, in parallel with the owner's Phase 0:** 02, 03, 04, 05, 05a. None of
   them touch a credential or a Plaid Item, so none are blocked on the go/no-go —
   this is the foundation that survives a `NO` intact.
   **03a can be built alongside them but cannot *pass* until the owner answers
   O8** (task 00a), because its gate is a destination in another failure domain
   and only the owner knows what storage exists. Building it early is right;
   calling it green before 00a would be the rev-2 mistake again.
2. **Once 00 exists:** 06 (Sandbox needs the dashboard account, not O2's answer)
   → then, **once O2 says go**, 07 → 08 — and 08 only after 03a's drill passes
   from the real destination. Rehearse in Sandbox before spending a single
   Production slot.
3. **The core value:** 10 → 11 → 12 → 14 → 15 → 16, with 12b close behind 09.
   Task 11 deserves more review attention than anything else in this list — it
   is where a successful API call is prevented from masquerading as fresh data.
4. **Then the number travels:** 17 → 18 → 19 → **20 → 19a** (+12a on the same
   Worker), and 21 → 22 in the app. **20 precedes 19a**: pairing is a
   conversation with the Worker (§6.3.1), so the Worker has to exist first — rev
   2 ordered these the other way because it believed pairing was a purely local
   act. 21 runs against fixtures in parallel and does not wait for any of it.
   22 is not polish — it is invariant I4, and 21 already ships the minimum of it.
5. **Last:** 23, 24, and Phase 5.
