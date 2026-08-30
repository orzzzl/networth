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
disturbed.)

## Phase 0 — gates (owner-only, nothing can ship before these)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 00 | Create the Plaid account, get the Trial plan approved, **verify the in-scope brokerages are reachable on Trial (O2)** | — | BLOCKED (owner) | Runbook `DESIGN.md` §19 step 1. **Go/no-go for the entire project** — if Trial does not reach OAuth institutions, the approach changes before any code is written. |
| 01 | UI target | — | **ANSWERED** | The owner chose a **Flutter phone app**. Number retained so later references stay valid. Its consequence is Phase 4, not a task of its own. |

## Phase 1 — foundation (Mac side; UI- and transport-agnostic, and the bulk of the work)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 02 | Project scaffold: package layout, venv, format/lint/test, CI, `scripts/check-no-secrets.sh` as a pre-commit hook and a CI job | — | BLOCKED (design PR) | Can start before 00 lands; touches no credential. The secret/figure scanner exists from the first commit because the repo is public. |
| 03 | SQLite schema + migration runner | 02 | BLOCKED | `DESIGN.md` §7 verbatim. Integer minor units, UTC timestamps, **two clocks per observation** (`fetched_at` / `source_as_of` + `source_clock`), `lineage_id` on accounts, `snapshot.sync_run_id` UNIQUE. **No `profile_id`** — dropped in review as speculative generality. |
| 03a | **Encrypted backup + restore + a passing restore drill** | 03, 05a | BLOCKED | `DESIGN.md` §14a. Backs up the DB **and** the `TokenStore`; never leaves the Mac. `scripts/restore-drill.sh` verifies row counts and token *fingerprints* (never tokens) and records `last_verified_restore_at`. **Hard gate on task 08:** after the first Production Link, a lost token strands a permanent Item slot (F2 + F6), so this cannot wait for Phase 5. |
| 04 | Domain model + `Store` repositories; append-only observations/snapshots | 03 | BLOCKED | The seam everything else reads through. No I/O beyond SQLite. Snapshots append per successful run, idempotent on `sync_run_id`; nothing is edited in place. Queries never assume a fixed set of accounts (§2 reservation 2). |
| 05 | `PlaidClient` wrapper + **error taxonomy mapping** to our states | 02 | BLOCKED | The one place Plaid errors become `DEGRADED` / `NEEDS_REAUTH` / `REVOKED` (§8.2). Must cover `ITEM_LOGIN_REQUIRED`, `PENDING_EXPIRATION`, `PENDING_DISCONNECT`, `USER_PERMISSION_REVOKED`, `ITEM_NOT_FOUND`. Unit-tested against synthetic fixtures. |
| 05a | `TokenStore`: narrow interface over secret storage, mode-600 file backend | 02 | BLOCKED | §2 reservation 3. Small, but it must exist before anything reads a token, or file reads will scatter. |
| 06 | Sandbox end-to-end rehearsal of the Link flow | 05, 05a | BLOCKED | **Must pass before any Production Link.** Sandbox is free and unlimited; Production slots are permanent (F2). |

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
| 11 | `StalenessMachine`: **two axes** — connection state per Item, data freshness per account from source clocks | 04, 10 | BLOCKED | `DESIGN.md` §8.1–§8.2. Heavily unit-tested — **this is the product**. Required cases: a call that succeeds while `source_as_of` never advances (the frozen-number failure, and the reason this task exists); `UNKNOWN` freshness never rendering as fresh; weekends, holidays and half-days on the market-close clock; a carried-forward observation continuing to age. |
| 12 | Full sync: holdings + balances → observations; carry-forward on failure | 04, 05, 11 | BLOCKED | Records `fetched_at` and `source_as_of` **separately**, plus which evidence produced the latter. Holdings take the oldest contributing `institution_price_*`; balances use `/accounts/balance/get` under `balance_mode: realtime` (F5) and are `UNKNOWN` under `cached`. Sets `is_carried_forward` honestly; a carried-forward row never advances its source clock. |
| 12a | Webhook drain: Worker receive route + **Mac-side** Plaid JWT verification | 05, 10, 20 | BLOCKED (O5/O7) | `DESIGN.md` §8.4. Verifies ES256 via `/webhook_verification_key/get`, matches `request_body_sha256`, rejects `iat` older than 5 min. Captures `PENDING_DISCONNECT`'s `reason`/`disconnect_time` and `USER_PERMISSION_REVOKED`, which polling cannot derive. **Advisory only** — a dropped webhook must never change the number, only delay a warning. |
| 12b | Replacement-Item reconcile flow (`scripts/reconcile.sh`) | 04, 09 | BLOCKED | `DESIGN.md` §8.5. New accounts start `NEW` and contribute nothing; the owner confirms an old→new mapping; `lineage_id` carries history across the seam. Tests must cover the two silent disasters: double-counting and a severed curve. |
| 13 | Manual assets: static property value + share count × live quote | 04 | BLOCKED (O4) | `QuoteClient` reuses the quotes integration from the sibling project; the quote must carry its own `as_of` and that `as_of` is the source clock. Ticker and quantities are configured at runtime, never hardcoded. |
| 14 | Snapshotter + net-worth computation + presentation contract | 12, 13 | BLOCKED | A total cannot be constructed without `as_of` + staleness counts (**I2**) — enforce it in the type, not by convention. `as_of` is `oldest_contributing_source_as_of`; stale and `UNKNOWN` counts stay separate; unreconciled accounts contribute nothing and force `is_complete = FALSE`. |
| 15 | Alerts: `alert` table, macOS notification, mailbox escalation, anti-fatigue policy | 11 | BLOCKED | State written before notifying, so a crash re-notifies rather than losing the alert. Includes the **frozen-data** alert (Item healthy, source clock stuck across five market days) and **publication overdue**. |
| 16 | launchd `KeepAlive` loop + due-ness engine + catch-up after sleep | 10, 12, 14, **15** | BLOCKED | Mirrors `~/agents/bin/tick-loop.sh`. **No `StartInterval`, no battery guard.** Depends on 15 because I3 promises an *alert* within an hour, not a recorded state. |

## Phase 4 — getting the number onto the phone

The half of the project the UI decision created. 17–20 are Mac-side; 21–24 are
the app.

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 17 | `NetWorthQuery` read layer (totals, history, per-account staleness) | 14 | BLOCKED | Pure reads. The only surface a UI or a payload builder may touch. History joins on `lineage_id` so a re-link does not break the curve. |
| 18 | CLI: `networth show` / `history` / `doctor` | 17 | BLOCKED | First consumer; proves the seam before a phone exists. `doctor` prints both clocks per account, item states, remaining Item slots, days since the last verified restore drill, and the age of the last successful publication. |
| 19 | Payload schema (versioned) + `Publisher`: serialize → AES-GCM encrypt → publish, with a `publication` audit row | 17 | BLOCKED | The Mac↔phone contract. Carries `published_at`, `publish_interval_seconds`, `grace_seconds` and a monotonic `seq`; `schema_version`, `pairing_id`, `seq` and `published_at` go in the AAD. Ciphertext only ever leaves the machine, whatever transport 20 picks. |
| 19a | Pairing: `networth pair` (mint key + read token, render QR) and the app-side secure-storage sink | 19 | BLOCKED | `DESIGN.md` §6.3. **No secret is ever compiled into the APK**; rotation is a re-pair, not a rebuild. App side stores via `flutter_secure_storage` (Android Keystore). |
| 20 | Transport backend + publication-freshness monitoring | 19 | BLOCKED (O5/O7) | Implements whichever of §6.2's candidates the owner picks; the Worker also carries 12a's receive route. Behind the `Publisher` seam, so the choice stays reversible. Monitoring is evidence-based — "the last successful publication is older than expected" — not expiry arithmetic, and it is not optional: a transport credential dying silently freezes the phone's number, this product's cardinal sin arriving through the back door. |
| 21 | Flutter app skeleton: fetch → verify `seq` → decrypt → local cache → headline **with both staleness dimensions** | 19 (+ fixture transport) | BLOCKED (O6) | Read-only display; holds no Plaid token and never calls Plaid. **Fetches through a fixture-backed seam so it does not wait on 20 or O5.** Its acceptance criterion includes **I4**: there is no intermediate state in which this app renders a bare headline — that would be shippable and would be the exact lie the project refuses. 22 deepens the treatment; it does not introduce it. |
| 22 | Full dual-staleness UI + replay handling | 21, 19a | BLOCKED | `DESIGN.md` §9. Copy-staleness reasons ("couldn't check" vs "the Mac hasn't published"), clock-skew `COPY_UNKNOWN`, suppression of per-account "fresh" badges under a stale copy, and the rejected-rollback warning (**I6**). |
| 23 | History curve, with incomplete snapshots visually distinct | 21 | BLOCKED | Dashed/hollow for `is_complete = FALSE`. A gap in the record must look like a gap. |
| 24 | Release signing + delivery discipline | 20, 21, **22** | BLOCKED | Depends on 22 so a build that can collapse the two staleness dimensions cannot be delivered, and on 20 so the delivered app has a real transport. Keystore outside the repo; **no secrets in the build** (§6.3). Signed from the first delivered build — a debug→release signature change later forces an uninstall. Version bump per `AGENTS.md`. |

## Phase 5 — operations

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 25 | ~~DB backup/restore~~ | — | **SUPERSEDED by 03a** | Number retained so review references stay valid. Moved into Phase 1 and made a gate on 08: backups scheduled *after* Production linking protect nothing during the window that matters. |
| 26 | Item budget tracking + surfacing remaining slots | 04, 08 | BLOCKED | Running out is invisible until it isn't (F2). Reads `replaces_item_id` so a replacement's cost is visible. |
| 27 | Vest-date nudge to re-confirm a manual share count | 13, 15 | BLOCKED | Manual quantities drift silently — the same failure this project exists to prevent, arriving from the manual side. |

## Suggested sequencing (not an assignment)

1. **Now, in parallel with the owner's Phase 0:** 02, 03, 04, 05, 05a, then
   **03a**. None of them touch a credential or a Plaid Item, so none are blocked
   on the go/no-go.
2. **Once 00 answers the go/no-go:** 06 → 07 → 08, and 08 only after 03a's drill
   passes. Rehearse in Sandbox before spending a single Production slot.
3. **The core value:** 10 → 11 → 12 → 14 → 15 → 16, with 12b close behind 09.
   Task 11 deserves more review attention than anything else in this list — it
   is where a successful API call is prevented from masquerading as fresh data.
4. **Then the number travels:** 17 → 18 → 19 → 19a → 20 (+12a on the same
   Worker), and 21 → 22 in the app. 22 is not polish — it is invariant I4, and
   21 already ships the minimum of it.
5. **Last:** 23, 24, and Phase 5.
