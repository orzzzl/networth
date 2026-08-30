# Task breakdown

Drafted during the design phase. **Nothing here is assigned and nothing is
started** — task assignment is itself subject to cross-review, per the owner's
instruction. Status vocabulary:

- `BLOCKED` — a dependency or an owner decision is outstanding.
- `READY` — dependencies met; may be assigned.
- `WIP` / `DONE` — claimed / merged.

Everything is `BLOCKED` until this design PR merges. Numbers are stable from the
merge of this PR onward; dependencies are by number. (Phase 4 was re-baselined
before merge, when the owner settled the UI on a phone app — nothing had been
assigned or started, so no work was disturbed.)

## Phase 0 — gates (owner-only, nothing can ship before these)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 00 | Create the Plaid account, get the Trial plan approved, **verify the in-scope brokerages are reachable on Trial (O2)** | — | BLOCKED (owner) | Runbook `DESIGN.md` §19 step 1. **Go/no-go for the entire project** — if Trial does not reach OAuth institutions, the approach changes before any code is written. |
| 01 | UI target | — | **ANSWERED** | The owner chose a **Flutter phone app**. Number retained so later references stay valid. Its consequence is Phase 4, not a task of its own. |

## Phase 1 — foundation (Mac side; UI- and transport-agnostic, and the bulk of the work)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 02 | Project scaffold: package layout, venv, format/lint/test, CI, `scripts/check-no-secrets.sh` as a pre-commit hook and a CI job | — | BLOCKED (design PR) | Can start before 00 lands; touches no credential. The secret/figure scanner exists from the first commit because the repo is public. |
| 03 | SQLite schema + migration runner | 02 | BLOCKED | `DESIGN.md` §7 verbatim. Integer minor units, UTC timestamps, **`profile_id` present from the first migration** (§2 reservation 1). |
| 04 | Domain model + `Store` repositories; append-only observations/snapshots | 03 | BLOCKED | The seam everything else reads through. No I/O beyond SQLite. Queries take a profile, never assume a fixed set of accounts (§2 reservation 3). |
| 05 | `PlaidClient` wrapper + **error taxonomy mapping** to our states | 02 | BLOCKED | The one place Plaid errors become `NEEDS_REAUTH` / `DEGRADED` / `REVOKED`. Unit-tested against synthetic fixtures. |
| 05a | `TokenStore`: narrow interface over secret storage, mode-600 file backend | 02 | BLOCKED | §2 reservation 4. Small, but it must exist before anything reads a token, or file reads will scatter. |
| 06 | Sandbox end-to-end rehearsal of the Link flow | 05, 05a | BLOCKED | **Must pass before any Production Link.** Sandbox is free and unlimited; Production slots are permanent (F2). |

## Phase 2 — linking (spends the scarce resource)

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 07 | Static OAuth redirect page + free hosting + dashboard registration | 00 | BLOCKED | Holds no data and no secrets. HTTPS required; `localhost` is Sandbox-only. |
| 07a | *Spike (optional):* automatic `public_token` handoff to the waiting local process | 07 | BLOCKED | Nice-to-have. Copy-paste is the shipping path and is never a blocker. |
| 08 | `scripts/link.sh` — owner-run Link, token exchange, `TokenStore` write, item record | 06, 07 | BLOCKED | The owner runs it; agents never do. Must confirm the exact institution *and login* before exchanging. |
| 09 | `scripts/relink.sh` — Link **update mode** for an existing item | 08 | BLOCKED | Consumes no slot. The recovery path for `NEEDS_REAUTH`; the naive remove-and-relink must never be implemented (F2). |

## Phase 3 — sync and the honesty machinery

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 10 | Item health poller (`/item/get`, hourly) | 04, 05 | BLOCKED | Replaces webhooks; detection ≤1h with zero infrastructure. Satisfies I3. |
| 11 | `StalenessMachine`: states, transitions, 36h threshold, market-close awareness | 04, 10 | BLOCKED | `DESIGN.md` §8. Heavily unit-tested — **this is the product**. Weekends, holidays and half-days are the interesting cases. |
| 12 | Full sync: holdings + liabilities → observations; carry-forward on failure | 04, 05, 11 | BLOCKED | Sets `is_carried_forward` honestly. Idempotent per day. |
| 13 | Manual assets: static property value + share count × live quote | 04 | BLOCKED (O4) | `QuoteClient` reuses the quotes integration from the sibling project; the quote must carry its own `as_of`. Ticker and quantities are configured at runtime, never hardcoded. |
| 14 | Snapshotter + net-worth computation + presentation contract | 12, 13 | BLOCKED | A total cannot be constructed without `as_of` + staleness counts (invariant I2) — enforce it in the type, not by convention. |
| 15 | Alerts: `alert` table, macOS notification, mailbox escalation, anti-fatigue policy | 11 | BLOCKED | State written before notifying, so a crash re-notifies rather than losing the alert. |
| 16 | launchd `KeepAlive` loop + due-ness engine + catch-up after sleep | 10, 12, 14 | BLOCKED | Mirrors `~/agents/bin/tick-loop.sh`. **No `StartInterval`, no battery guard.** |

## Phase 4 — getting the number onto the phone

The half of the project the UI decision created. 17–20 are Mac-side; 21–24 are
the app.

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 17 | `NetWorthQuery` read layer (totals, history, per-account staleness) | 14 | BLOCKED | Pure reads. The only surface a UI or a payload builder may touch. |
| 18 | CLI: `networth show` / `history` / `doctor` | 17 | BLOCKED | First consumer; proves the seam before a phone exists. `doctor` prints item states, remaining Item slots, and transport-credential days-to-expiry. |
| 19 | Payload schema (versioned) + `Publisher`: serialize → AES-GCM encrypt → publish, with a `publication` audit row | 17 | BLOCKED | The Mac↔phone contract. Ciphertext only ever leaves the machine, whatever transport 20 picks. `schema_version` so an old app refuses a newer payload instead of misreading it. |
| 20 | Transport backend + credential-expiry monitoring | 19 | BLOCKED (O5) | Implements whichever of §6's candidates the owner picks. Behind the `Publisher` seam, so the choice is reversible; expiry monitoring is not optional — a transport credential dying silently freezes the phone's number, which is this product's cardinal sin arriving through the back door. |
| 21 | Flutter app skeleton: fetch → decrypt → local cache → headline number with its `as_of` | 19 | BLOCKED (O6) | Read-only display. Holds no Plaid token and never calls Plaid. Caches locally so it opens offline — honestly labelled. |
| 22 | **Dual staleness UI (invariant I4)**: connection staleness and copy staleness, always distinguishable | 21 | BLOCKED | `DESIGN.md` §9. The two dimensions must never collapse into one "⚠ stale" icon, and a stale copy must not present per-account "fresh" badges as if they were known. |
| 23 | History curve, with incomplete snapshots visually distinct | 21 | BLOCKED | Dashed/hollow for `is_complete = FALSE`. A gap in the record must look like a gap. |
| 24 | Release signing + delivery discipline | 21 | BLOCKED | Keystore outside the repo, injected at build time along with the transport credential and payload key. Signed from the first delivered build — a debug→release signature change later forces an uninstall. Version bump per `AGENTS.md`. |

## Phase 5 — operations

| # | Task | Deps | Status | Notes |
|---|---|---|---|---|
| 25 | DB backup/restore | 03 | BLOCKED | History is impossible to backfill; losing it is the worst non-security failure available. |
| 26 | Item budget tracking + surfacing remaining slots | 04, 08 | BLOCKED | Running out is invisible until it isn't (F2). |
| 27 | Vest-date nudge to re-confirm a manual share count | 13, 15 | BLOCKED | Manual quantities drift silently — the same failure this project exists to prevent, arriving from the manual side. |

## Suggested sequencing (not an assignment)

1. **Now, in parallel with the owner's Phase 0:** 02, 03, 04, 05, 05a. None of
   them touch a credential or a Plaid Item, so none of them are blocked on the
   go/no-go.
2. **Once 00 answers the go/no-go:** 06 → 07 → 08. Rehearse in Sandbox before
   spending a single Production slot.
3. **The core value:** 10 → 11 → 12 → 14 → 15 → 16. Task 11 deserves more review
   attention than anything else in this list.
4. **Then the number travels:** 17 → 18 → 19 → 20, and 21 → 22 in the app. 22 is
   not polish — it is invariant I4, and shipping 21 without it would ship the
   exact lie this project exists to refuse.
5. **Last:** 23, 24, and Phase 5.
