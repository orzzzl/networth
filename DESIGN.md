# DESIGN — networth

Status: **proposed** (design phase; nothing implemented).
Author: Claude. Reviewer: Codex.

---

## 1. What this is

One number — total net worth, assets minus debts — rebuilt at least once a day
from linked financial accounts plus a few manually-valued assets, and displayed
on a phone. Single user, one Mac, zero marginal cost.

**The number is not the product. The number's honesty is the product.**

Commercial aggregators were tried and abandoned: after linking a brokerage the
balance silently froze. The connection had died and the UI kept rendering the
last good figure as if it were live. Every decision below is downstream of
refusing to do that.

**Invariants** (each one is testable, and each is a way the product could betray
its purpose):

- **I1.** Every account carries a `last_successful_sync`, always reachable in the
  UI without drilling down.
- **I2.** No total is rendered without its `as_of` and its staleness
  annotation. A code path that can emit a bare total is a bug.
- **I3.** A connection needing re-authentication raises an alert within an hour,
  not at the next time someone happens to look.
- **I4.** **The two staleness dimensions are never collapsed.** On a phone the
  number can be old for two independent reasons — the *connection* to an
  institution died, or the *phone's copy* of the snapshot is old — and the UI
  must always distinguish them (§9). Showing one indicator for both would
  reintroduce exactly the lie this project exists to eliminate.

Staleness is therefore a first-class state machine (§8), not a badge computed at
render time.

---

## 2. Scope: single-user now

Built for one person today; the owner may share it later. Both halves are meant
literally, and the failure mode to avoid is over-building.

**Build now:** no backend, no accounts, no login, no multi-tenancy. The app links
the owner's own institutions through Plaid Link; results are stored locally.

**Do not build now, not even scaffolding:** user registration, authentication, a
server, server-side token storage, per-user encryption schemes, sharing or
export-to-another-user. Each costs real work and buys nothing today.

**Reserve cheaply — only these four.** They are nearly free now and painful to
retrofit:

1. **Carry `profile_id` on accounts, holdings and snapshots from the first
   migration.** For now it is one constant local profile. Adding a column to a
   table that already holds months of net-worth history is a migration; having
   it from day one costs nothing.
2. **No institution-specific knowledge is hardcoded anywhere** — not in the sync
   engine, not in config, not in UI copy or defaults. Everything comes from the
   runtime link flow. **A hardcoded institution is a bug.** (This is also why §3
   describes account *categories* rather than a list of the owner's banks.)
3. **The sync engine operates over "whatever items exist for this profile,"**
   never over an assumed fixed set of accounts.
4. **Token storage sits behind a narrow interface** (`TokenStore`), not file
   reads scattered through the code, so the storage location can change without
   touching sync logic.

Anything beyond that list is speculative generality. Push back on it in review.

### Two ceilings, recorded so nobody rediscovers them the hard way

1. **Plaid's free tier is 10 Production Items per *developer account*, not per
   end user.** A second real user with a handful of institutions can exhaust it.
   Multi-user therefore breaks the zero-money rule outright: it is not a feature
   to switch on later, it is a different project with a different budget.
2. **Multi-user means holding other people's financial credentials** — a
   materially different security and compliance posture from a personal tool.
   Noted; not designed for.

§6 adds a third, discovered while designing the transport: Plaid forbids calling
its data APIs from a client, so any multi-user version needs a real server
anyway. That reinforces the same conclusion from a different direction.

---

## 3. What gets aggregated

Deliberately expressed as **categories, not institutions**. The concrete list of
the owner's banks and brokerages is entered at runtime through Plaid Link and
lives only in the local database and the owner's private runbook — never in this
repository, which is public.

| Category | Mechanism | Sign | Refreshed |
|---|---|---|---|
| Retirement (employer plan) | Plaid Investments | + | daily |
| Taxable brokerage | Plaid Investments | + | daily |
| Employer equity compensation | Plaid Investments if reachable, else manual (below) | + | daily |
| Credit cards | Plaid Liabilities / balances | **−** | daily |
| Equity comp fallback | manual share count × live quote for a configured ticker | + | price daily, quantity manual |
| Real property | manual fixed value at purchase price | + | **never** |

Notes that shape the design:

- **Equity compensation is expected to be the awkward one.** Employer equity
  often sits in award-center accounts that do not surface through aggregators at
  all, and at least one major brokerage additionally requires an explicit access
  request on the Plaid dashboard that can take **up to six weeks** to grant. The
  manual fallback (§12) needs no Plaid Item, no waiting, and no OAuth approval —
  so it is the **primary plan**, not a consolation prize. If the linked path
  happens to work later, it supersedes the manual entry for that account.
- **Non-goals:** transactions and budgeting, trading, tax lots, cost basis,
  multi-currency, intraday valuation.

---

## 4. The cost budget, verified

Zero spend beyond subscriptions already paid. Plaid's free tier is the entire
economic basis of the project, so its terms were checked against Plaid's own
docs rather than assumed. **Four findings, two of which corrected the brief:**

**F1 — The Trial plan is free, capped at 10 Production Items, no stated expiry.**
US/CA teams created on or after 2026-04-15. Bundled: Auth, Transactions,
Balance, Identity, Assets, **Liabilities**, **Investments**, Statements. Both
products this project needs are included.

**F2 — `/item/remove` does NOT free a slot.** *(New.)* Plaid's billing docs:
removing Items created on a Trial plan "will *not* allow you to create more
Items." The 10 are therefore **10 lifetime Link exchanges**, not 10 concurrent
connections. A mislinked institution burns a slot permanently. This is the
binding constraint of the project and drives §8 and §14.

**F3 — Investments Refresh *is* bundled on Trial.** *(Corrects the brief, which
had it as a paid add-on.)* It is a paid add-on **off** Trial. It stays unused
regardless: the natural cadence already satisfies the requirement (holdings
update "at least once per day during market days (up to 2-4 times per day,
depending on institution)" after close), and depending on it would create a
silent cost cliff the moment the account leaves Trial.

**F4 — OAuth access on Trial is probable but unconfirmed.** Plaid's OAuth guide
says access to Production "via either a paid plan or a trial" satisfies the
prerequisite, and Trial users "do not need to complete [full Production
registration] until you upgrade"; support material says Trial reaches most major
OAuth institutions, typically 6–24h after approval. Every brokerage in scope is
an OAuth institution, so this is a **go/no-go for the whole approach** and only
the owner's dashboard can settle it. Recorded as a gate (§18, O2), not asserted.

Everything else is free by construction: SQLite on disk, a launchd loop on a Mac
that already runs 24/7, a quotes key the owner already holds, and a transport
chosen in §6 specifically for having no bill attached.

---

## 5. Architecture

The owner has decided the UI is a **Flutter phone app**. That single decision
relocates the hardest problem in the system: sync and display no longer live in
the same place, so the data has to *travel*, and the transport must be free,
authenticated, and must not publish the owner's net worth to the internet.

```
   ┌────────────── the Mac (already runs 24/7) ──────────────┐
   │  launchd KeepAlive loop                                 │
   │    SyncEngine → StalenessMachine → Snapshotter          │
   │    PlaidClient (holds client_id/secret + access_tokens)  │
   │    SQLite: full history, append-only                     │
   │                        │                                 │
   │                 Publisher: encrypt + upload              │
   └────────────────────────┼─────────────────────────────────┘
                            │  ciphertext only
                    ┌───────▼────────┐
                    │   transport    │   free + authenticated (§6)
                    └───────┬────────┘
                            │
   ┌────────────────────────▼─────────────────────────────────┐
   │  Flutter app: fetch → decrypt → cache locally → display  │
   │  shows BOTH connection staleness and copy staleness (§9) │
   └──────────────────────────────────────────────────────────┘
```

The Mac keeps every credential and the full history. The phone is a **read-only
display of a signed, encrypted snapshot** — it never holds a Plaid token, never
calls Plaid, and cannot mutate anything. That asymmetry is what makes the phone
safe to lose.

Seams (interfaces the rest of the code depends on, never concrete classes):

- `PlaidClient` — link tokens, exchange, item status, holdings, liabilities. The
  only place Plaid's error taxonomy becomes our states.
- `QuoteClient` — `get_quote(symbol) -> (price, as_of)`; the quote must carry its
  own timestamp, because a stale price is precisely the failure being hunted.
- `TokenStore` — narrow interface over secret storage (§2 reservation 4).
- `Store` — repositories over SQLite; append-only observations/snapshots.
- `Publisher` — serialize + encrypt + upload the snapshot. Swappable transport.
- `Notifier` — alert delivery (§11).

The sync core must not import anything UI- or transport-specific.

---

## 6. How the data reaches the phone

The owner named this THE question of the design and asked that it not be
hand-waved. Three candidate architectures were considered.

### Option 1 — the phone talks to Plaid directly. **Rejected: Plaid forbids it.**

Superficially attractive: no transport, no Mac dependency, tokens stay on the
owner's own device, and it points toward a possible multi-user future where
nobody's credentials sit on someone else's server.

It does not survive contact with Plaid's API design. Plaid's data endpoints
authenticate with `client_id` + `secret` on **every** request, and Plaid states
these "should only be called from your server." On `/link/token/create`
specifically: "This call should never happen directly from the mobile client, as
it risks exposing your API secret."

So a phone-only build would have to embed the Plaid **client secret** in the
APK — not merely an access token. Anyone extracting it could act as this
application against the owner's Plaid account, including burning the ten
permanent Items (**F2**). The precedent of shipping an APK with an embedded
quotes key does **not** carry: that key is read-only market data of no personal
value, whereas this is the master credential to a financial data account.

This is a prohibition, not a tradeoff — which is why it is rejected even though
it was the option best aligned with a multi-user future. The secondary objection
stands too: the daily-refresh guarantee would depend on the app being opened or
on Android background fetch, which is not dependable, and dependable daily
refresh is the entire reason this project exists.

*Consequence worth recording:* since the client secret can never live on a
device, **any** multi-user version needs a real server. That is a third
independent reason multi-user is a different project (§2).

### Option 2 — the Mac syncs; the phone displays. **Recommended.**

Keeps the launchd daily guarantee, keeps all credentials on the Mac, reuses the
scheduling pattern the machine already runs. The cost is a transport that must
be free *and* authenticated. A public static host is not acceptable: a plaintext
net-worth JSON at an unguessable URL is still a leak, and unguessable URLs leak
through caches, logs, and history.

**Defence in depth: encrypt the payload regardless of transport.** The Mac
encrypts the snapshot with a symmetric key (AES-GCM); the phone holds the key.
The transport provider then stores opaque bytes and never sees a balance. This
matters because it decouples "is the transport's auth perfect and free forever?"
from "does a third party learn the owner's net worth?" — the answer to the second
becomes *no* by construction.

The key lives in `~/agents/secrets/` on the Mac and in the APK on the phone.
Embedding it is acceptable *here* precisely because of the asymmetry in §5: it
decrypts **display data**, and grants no power to move money, read raw account
credentials, or spend Plaid Items. That is the argument the quotes-key precedent
could not make on its own.

Transport candidates, judged on free-ness that will survive a year, real
authentication, and availability when the Mac is asleep:

| Transport | Auth | Free? | Mac asleep? | Verdict |
|---|---|---|---|---|
| **Private GitHub repo** (`…-data`, separate from this public code repo) — Mac commits `snapshot.json.enc`, phone reads via API with a fine-grained read-only PAT | Real (PAT, single-repo scope) | Free private repos are a stable, long-standing product, not a startup free tier | **Yes** — snapshot is in the cloud | **Recommended** |
| Cloudflare Workers + KV | Shared secret header | Long-standing generous free tier | Yes | Viable second; more moving parts, another account, deploy tooling |
| Tailscale — phone reaches the Mac directly over WireGuard | Device identity; **no secret in the APK at all** | Personal tier long-standing | **No** — Mac must be awake and online | Best on security; loses on availability |
| Public static host (Pages, etc.) | None | Free | Yes | **Rejected** — publishes net worth |
| ntfy/public pubsub free tiers | None or weak | Varies | Yes | **Rejected** — no real auth |

**Recommendation: private GitHub repo + encrypted payload.** It needs no new
account or service (the tooling is already in use here), its free tier is a
mature product rather than a venture-subsidised promise, and — decisively for an
app opened once a day — the phone gets the latest snapshot **even when the Mac
is asleep**, which the Tailscale option cannot offer.

Tailscale is the stronger choice on pure security (no embedded credential at
all, no third party in the path) and is documented as a swap the `Publisher`
seam makes cheap. If the owner prefers it, the cost is that opening the app away
from home shows a cached copy — which the UI already handles honestly (§9), so
it degrades gracefully rather than breaking.

Rotation and expiry are real chores, not footnotes: fine-grained PATs expire (max
one year), so `doctor` reports days-to-expiry and an alert fires well before it
lapses. A transport credential that dies silently would freeze the phone's number
— the exact failure this product exists to prevent, arriving through the back
door.

### Option 3 — considered and dismissed

Manual transfer (AirDrop/Files/iCloud) breaks the automatic daily guarantee. A
self-hosted server contradicts "no 24/7 server" and costs money. Push
notification as transport (rather than as an alert) has payload limits and no
delivery guarantee.

---

## 7. Data model

SQLite at `~/networth-data/networth.db` — **outside the repo**. The repo is
public; `.gitignore` blocks `data/`, `*.db`, `*.env` from the first commit as a
safety net, but the real control is that data never goes near the working tree.

Money is stored as **integer minor units** with an explicit currency. Never
floats. All timestamps UTC ISO-8601; staleness math in UTC.

```sql
profile(id, name, created_at)          -- exactly one row for now (§2 reservation 1)

institution(id, plaid_institution_id, name, is_oauth)

item(                                  -- one per institution LOGIN
  id, profile_id, institution_id,
  plaid_item_id,
  secret_ref,                          -- KEY NAME resolved by TokenStore, never the token
  status, status_since,                -- §8 state machine
  last_successful_sync, last_attempted_sync,
  last_error_code, last_error_message,
  consent_expiration_time, created_at)

account(
  id, profile_id, item_id,             -- item_id NULL for manual assets
  plaid_account_id, name, official_name, mask,
  type, subtype, currency,
  sign,                                -- +1 asset, -1 liability
  freshness_policy,                    -- SYNCED | MANUAL_STATIC | MANUAL_QTY_LIVE_PRICE
  include_in_net_worth,                -- exclude without deleting
  last_successful_sync,                -- per-account: an item can partially succeed
  created_at, archived_at)

manual_asset(
  account_id, kind,                    -- REAL_PROPERTY | EQUITY_SHARES
  static_value_minor,                  -- REAL_PROPERTY
  symbol, share_count,                 -- EQUITY_SHARES
  valued_as_of, note)

observation(                           -- append-only, one row per account per run
  id, sync_run_id, account_id, observed_at,
  value_minor, currency,
  source,                              -- PLAID | MANUAL | QUOTE
  price_as_of,                         -- for QUOTE-derived values
  is_carried_forward)                  -- TRUE = reused a prior value, not fetched

snapshot(
  id, profile_id, taken_at,
  total_net_worth_minor, total_assets_minor, total_liabilities_minor,
  account_count, stale_account_count, reauth_account_count,
  is_complete,                         -- FALSE if any account carried forward
  oldest_contributing_sync)            -- true age of the weakest input

sync_run(id, started_at, finished_at, trigger, ok, error_summary)

alert(id, created_at, kind, item_id, account_id, message,
      notified_at, acknowledged_at, resolved_at)

publication(id, snapshot_id, published_at, transport, ok, error)  -- §6 audit trail
```

Three modelling choices worth defending:

**`freshness_policy` is per-account, not a global rule.** Real property must
never be flagged stale — it is fixed *by design*, so flagging it would train the
owner to ignore the staleness signal, destroying the one feature that matters.
It reads "manual, set on <date>" instead. Manual equity has two clocks: the share
count (manual, no expiry) and the price (must be fresh). No single global rule
expresses that.

**`observation.is_carried_forward` is explicit.** When a sync fails the account
still contributes its last known value (§10), but the row records that it was
carried forward rather than fetched. Without that flag, history retroactively
looks healthy and the product starts lying about its own past.

**`publication` exists** so the Mac knows whether the phone *could* have seen the
latest snapshot. Sync succeeding and publish failing is a distinct failure, and
§9 depends on being able to tell them apart.

---

## 8. The connection staleness state machine

Per **Item**. Account state derives from its item, floored by the account's own
`last_successful_sync`.

```
                 ┌──────────┐
   link exchange │ HEALTHY  │  synced within threshold, no error
        ────────►└────┬─────┘
                      │
   threshold passed, no Plaid error       ┌────────────┐
                      ├────────────────────► STALE      │ transient: outage,
                      │◄───────────────────┤            │ Mac asleep, flaky institution
                      │  successful sync   └────────────┘
                      │
   INSTITUTION_DOWN / _NOT_RESPONDING /    ┌────────────┐
   RATE_LIMIT / transport-level errors     │ DEGRADED   │ retry w/ backoff,
                      ├────────────────────►            │ NO owner action
                      │◄───────────────────┤            │
                      │  successful sync   └────────────┘
                      │
   ITEM_LOGIN_REQUIRED / PENDING_EXPIRATION ┌─────────────┐
   / PENDING_DISCONNECT / consent expired   │ NEEDS_REAUTH│ OWNER ACTION,
                      ├─────────────────────►             │ alert immediately
                      │◄────────────────────┤             │
                      │ Link *update mode*  └─────────────┘
                      │
   ITEM_NOT_FOUND / access revoked          ┌────────────┐
                      └─────────────────────► REVOKED    │ terminal until re-link
                                            └────────────┘
```

Rules:

- **Threshold: 36h** (owner-specified). For `SYNCED` investment accounts the
  clock runs against **elapsed market closes**, not raw wall-clock: Plaid updates
  investments after close on market days, so a Friday-close value is legitimately
  ~63h old by Monday morning. A naive 24h rule would scream every weekend and the
  owner would learn to ignore it — the worst possible outcome for a product whose
  only feature is a trustworthy warning. Cash and card accounts, which update
  daily regardless, use plain wall-clock 36h.
- `STALE` and `DEGRADED` are **not** owner-actionable and must not alert like
  `NEEDS_REAUTH`. Confusing "the internet was down" with "your connection is
  dead" is how alert fatigue starts.
- Transitions record `status_since`, so the UI says "needs re-auth since
  Tuesday" rather than just "broken".
- The state change is written **before** any notification is sent, so a crash
  between the two re-notifies rather than silently dropping the alert.

### Re-authentication must use Link update mode — the critical constraint

Given **F2** (a removed Item never frees its slot), the naive recovery — remove
the Item, run Link again — would consume one of ten *lifetime* slots **every
time a connection dies**. Since dying connections are this product's entire
premise, that path exhausts the free tier within months and silently converts a
free project into a paid one. It must never be implemented.

Recovery is therefore always **Link in update mode**: a `link_token` created with
the existing `access_token`, re-authenticating the *same* Item. The
`access_token` does not change, no new Item is created, no slot is consumed.

`REVOKED` is the only state that may genuinely require a new Item; it is
surfaced to the owner as a decision ("this costs one of your N remaining slots"),
never taken automatically.

### Why polling instead of webhooks

The brief asked for Plaid error webhooks. Webhooks need a publicly reachable
HTTPS endpoint — hosting, so either cost or a new always-on dependency, both out
of scope. The same signal is available from `/item/get`, which returns the
Item's current `error`.

- **Health poll** — `/item/get` per item, hourly: detects `ITEM_LOGIN_REQUIRED`
  within an hour, satisfying **I3**.
- **Full sync** — holdings + balances after market close (§13).

Detection latency moves from ~minutes to ≤1h on a number that refreshes daily —
invisible to the owner, and it removes an entire class of infrastructure. If
webhooks are ever wanted, a Cloudflare Workers free-tier receiver drained by the
local loop is the documented upgrade path.

---

## 9. Copy staleness — the second dimension the phone adds

**I4** exists because a phone can be wrong in a second, independent way. The
Mac's view has one question ("when did each institution last answer?"). The
phone's view has two, and they have different fixes:

| | Connection fresh | Connection stale / needs re-auth |
|---|---|---|
| **Copy fresh** | Healthy. Show the number plainly. | "3 accounts haven't updated since Friday" — **the owner re-links.** |
| **Copy stale** | "Showing a copy from 2 days ago — couldn't reach the source." — **the transport is broken; the accounts may be perfectly fine.** | Both problems. Say both; do not let one mask the other. |

Consequences for the implementation:

- The phone computes **copy age** locally (`snapshot.taken_at` vs device clock,
  plus `last_fetch_attempt`) and never infers it from the payload alone.
- **Connection** staleness is computed on the Mac and travels *inside* the
  payload, already evaluated, so the phone never re-implements policy.
- **A stale copy must not be presented as evidence that accounts are healthy.**
  If the copy is old, per-account "fresh" badges are suppressed or explicitly
  qualified — the phone genuinely does not know the current state, and saying so
  is the honest answer.
- Both indicators are visible on the main screen. They must be visually
  distinguishable, never merged into one "⚠ stale" icon.
- The Mac's `publication` table (§7) lets `doctor` distinguish "sync failed" from
  "sync fine, publish failed" — the latter is invisible from the phone alone.

---

## 10. Net-worth computation

```
net_worth = Σ(asset accounts) − Σ(liability accounts)
```

1. **A stale account still contributes its last known value**, flagged
   `is_carried_forward`, and the snapshot is marked `is_complete = FALSE`.
   Rejected alternative: excluding stale accounts, which makes the total silently
   *drop* — a different lie, and a scarier one.
2. The snapshot records `oldest_contributing_sync`; the headline's honest age is
   that, not the run time.
3. The presentation contract (**I2**) is enforced in the query layer and in the
   payload schema, so no UI can bypass it: every total ships with `as_of`,
   `stale_account_count`, `reauth_account_count`, `is_complete`.
4. Credit cards enter **negative**. Sign is set once at link time from the Plaid
   account type and stored, never inferred at render time.
5. History renders incomplete snapshots visually distinct (dashed/hollow) — a gap
   in the record must look like a gap.
6. Single currency (USD). The schema carries currency so mixed units fail loudly
   rather than silently summing unlike things.

---

## 11. Alerting

| Channel | Used for | Mechanism |
|---|---|---|
| macOS notification | `NEEDS_REAUTH`, `REVOKED`, transport-credential expiry | `osascript -e 'display notification'` |
| `alert` table + in-app banner | everything, persistent | DB row, travels in the payload; cleared on resolve |
| Agent mailbox | `NEEDS_REAUTH` unresolved >24h | write to `~/agents/inbox/claude/new/` |

The mailbox hop reuses infrastructure that already exists: the ticker wakes a
session on new mail, which can escalate and record it. It costs nothing to build.

Anti-fatigue: **one notification per item per state entry**, re-notified at most
once per 24h while unresolved, never for `STALE`/`DEGRADED` (UI only). Alerts
auto-resolve on the transition back to `HEALTHY`.

Phone push notifications are **out of scope** — they would need FCM and a sender,
i.e. infrastructure. The owner is at the Mac daily; a macOS notification plus an
in-app banner covers it for free.

---

## 12. Manual assets

- **Real property** — one fixed value at purchase price, `MANUAL_STATIC`, never
  refreshed, never marked stale, always labelled with `valued_as_of`. Revisions
  are appended as new observations rather than overwritten, so history stays
  truthful about when the owner's estimate changed.
- **Employer equity fallback** — `MANUAL_QTY_LIVE_PRICE`: value =
  `share_count × quote(symbol)`, reusing the working quotes integration from the
  sibling project (key already in `~/agents/secrets/`). The *price* obeys normal
  freshness rules — a quote older than the last market close is stale and says
  so. The *quantity* does not expire, but vesting changes it, so the UI shows
  "N shares, set on <date>" and task 27 adds a periodic nudge to re-confirm.
  Silently drifting share counts are the same failure this product exists to
  prevent, arriving from the manual side.

Neither path consumes a Plaid Item.

---

## 13. Scheduling

Reuse the established pattern: a **resident process under launchd `KeepAlive`**,
not `StartInterval`. macOS defers `StartInterval` timers on battery, and battery
operation is an owner hard requirement. **No battery guards, no "please plug in"
logic.**

`~/Library/LaunchAgents/com.zeleng.networth.sync.plist` → `bin/networth-loop.sh`
→ wakes every 5 minutes and asks the database what is due:

| Job | Due when |
|---|---|
| health poll | >60 min since the last poll |
| full sync | no successful full sync since the most recent market close + 1h |
| quote refresh | any `MANUAL_QTY_LIVE_PRICE` price older than the last close |
| publish | a snapshot exists newer than the last successful `publication` |

**Due-ness is computed from stored state, never from cron semantics.** A Mac
asleep for two days simply finds work due on wake and catches up; there is no
missed-fire concept to handle. Runs are idempotent — a second full sync the same
day updates that day's snapshot rather than duplicating it.

Backoff: `DEGRADED` items retry at 1h/2h/4h/8h, capped, so an institution having
a bad week is not hammered. **Publish retries independently of sync**, because a
successful sync that never reaches the phone is a silent failure of the whole
product.

---

## 14. The Plaid Item budget

10 lifetime Items (**F2**: never recycled). One Item = one institution *login*;
all accounts behind that login are free, so consolidation is the whole game.

| Purpose | Items | Note |
|---|---|---|
| Retirement plan | 1 | may be a different login from a personal account at the same firm — verify before linking |
| Taxable brokerages | 1 each | |
| Employer equity | 0 | **prefer the §12 manual path and spend nothing** |
| Card issuers | 1 each | one per issuer login, not per card |
| **Reserve** | **2** | permanent headroom for a mislink or a future institution |

Working budget: **8**. Spending rules:

1. **Rehearse every Link flow in Sandbox first.** Sandbox is unlimited and free;
   Production mistakes are permanent.
2. Confirm the exact institution *and login* before each Production exchange.
3. Never spend a slot on an account whose balance could be typed in once a
   month. A low-balance card is a manual entry, not an Item.
4. Re-auth via update mode costs nothing (§8). Only `REVOKED` can require a new
   slot, and that is an owner decision.
5. Track remaining slots in the DB and surface them in `doctor` and the app.
   Running out is invisible until it isn't.

---

## 15. Secrets and what may never be committed

The repository is **public**. Repo visibility was never the real control — the
separation between code and credentials is. These rules are also in `AGENTS.md`,
which binds both agents.

- `~/agents/secrets/plaid.env` — `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`.
- `~/agents/secrets/plaid-items.json` — `{item_id: access_token}`, mode 600.
- `~/agents/secrets/networth-transport.env` — transport PAT + payload encryption
  key.
- Quotes key: already present, reused.
- Android signing keystore + `key.properties`: outside the repo (§17).

Non-negotiable:

1. **`.gitignore` from the first commit** for the database, snapshots, any
   `.env`, any token cache, any export. A file committed once stays in history
   even after deletion, and on a public repo that is unrecoverable.
2. **Never commit real figures** — no real balances, account numbers, or
   institution item ids, in code, tests, fixtures, docs, or PR text. **All test
   fixtures are synthetic.** No test or script may print a real balance, in CI
   or locally.
3. **Credentials live in `~/agents/secrets/` only** — never in git, a PR body, a
   review comment, or a log line. The DB stores `secret_ref` (a key name)
   resolved through `TokenStore`, never a token.
4. **PR descriptions and commit messages carry no real numbers.** Report "3
   accounts reconciled", never amounts.
5. **No institution-specific detail in the repo** (§2 reservation 2) — this
   document deliberately describes categories, not the owner's banks.

Logging redacts by default: the logger takes a `redact=[...]` set and every Plaid
response passes through it. `scripts/check-no-secrets.sh` runs as a pre-commit
hook and in CI.

Banking credentials are never seen by any agent or written anywhere: the owner
types them into Plaid Link, which returns only a short-lived `public_token`.

---

## 16. Stack

**Mac side: Python 3.12 + SQLite + the official `plaid-python` SDK.**
**Phone side: Flutter** (decided by the owner).

| Option for the Mac side | For | Against | Verdict |
|---|---|---|---|
| **Python + SQLite** | Official Plaid SDK; SQLite in stdlib; trivial launchd integration; no build step; ideal for a daemon | Not the UI language | **Chosen** |
| TypeScript / Node | Official SDK too | Adds a toolchain for no daemon-side gain; shares nothing with a Flutter UI | Second |
| Dart end-to-end | One language across both halves | **No server-side Plaid SDK** — would mean hand-rolling a financial API client and its error taxonomy, on the side of the system that holds the credentials | Rejected |

The daemon and the app do not need to share a language: they share a *payload
schema*, which is the only contract that matters and is versioned explicitly
(`schema_version` in the payload, so an old APK refuses to render a newer
payload rather than misreading it — silently misreading it would be another way
to show a wrong number confidently).

Money is integer minor units end-to-end; decimal only at presentation. No floats,
on either side.

### The Link flow still needs one hosted page

OAuth institutions require an **HTTPS** redirect URI registered in the Plaid
dashboard (`http://localhost` is Sandbox-only). A free static page satisfies it;
it holds **no data and no secrets**, existing only to catch the OAuth redirect
and hand control back to Link, so hosting it publicly is safe.

Returning the resulting `public_token` to the Mac is the one genuinely uncertain
mechanic (a fetch from an HTTPS page to `http://localhost` sits in browser
mixed-content grey area). **Primary path: copy-paste** — the page displays the
`public_token`, the owner pastes it into the waiting CLI, which exchanges it
immediately. Guaranteed to work, once per institution, ~10 seconds. An automatic
handoff is an optional spike (task 07a), never a blocker. The `public_token` is
short-lived and useless without the client secret, so this is not a
credential-handling risk.

Note that Link runs **on the Mac in a browser**, not in the phone app. Adding
Plaid's Flutter Link SDK would require a server-side `/link/token/create` anyway
(§6), so it buys nothing for a single-user tool.

---

## 17. Delivering the app

The owner already expects a specific delivery discipline from the sibling
project; it applies here.

- **Bump `pubspec.yaml` before every APK handed over.** A feature batch bumps the
  minor version; the `+N` build number **always** increments. Shipping two
  different APKs with the same version has already caused one real incident
  (Android skipped the reinstall and the owner tested a stale build believing it
  was new).
- The delivered file name carries the new version and **overwrites** the previous
  file, so there is never ambiguity about which APK is current.
- **Release signing from the start.** Keystore and `key.properties` live outside
  the repo (`~/agents/secrets/`), are never committed, and the build reads them
  by path. The sibling project shipped debug-signed for a while; starting signed
  avoids the migration (a debug→release signature change forces an uninstall).
- The transport PAT and payload key are injected at build time
  (`--dart-define`), never checked in.

---

## 18. Open questions

| # | Question | Owner of the answer | Blocks |
|---|---|---|---|
| O2 | Does the Trial plan actually reach the in-scope brokerages via OAuth? (**F4** — go/no-go) | owner, via dashboard | all implementation |
| O3 | How many distinct card-issuer logins? | owner | Item budget sizing |
| O4 | Real property: purchase price only, or a revision log? (recommend: revision log — nearly free) | owner | task 13 |
| O5 | Transport: private GitHub repo (recommended) or Tailscale (stronger security, needs the Mac awake)? | owner, on Codex's advice | task 20 |
| O6 | Android only, or iOS too? iOS has no sideloading story, which changes delivery entirely | owner | tasks 21, 24 |

*(O1 — phone vs Mac/browser — was answered by the owner: **Flutter phone app**.)*

---

## 19. Owner runbook — the only manual steps

Agents must never perform these. Everything before and after is automated.

**Step 1 — Create the Plaid account** (~10 min, once)
1. Sign up at `dashboard.plaid.com/signup`, verify email.
2. Apply for the **Trial plan** at `dashboard.plaid.com/trial-plan`. Most apply
   automatically; a manual review takes 2–3 business days.
3. After approval, confirm the plan reads **Trial, 10 Items** and that the
   in-scope brokerages appear available (**answers O2 — stop and report before
   implementation proceeds if they do not**).
4. Copy `client_id` and the **production** secret into
   `~/agents/secrets/plaid.env`. Never paste them into a chat or a PR.
5. Register the redirect URI (§16) under *Allowed redirect URIs*.
6. Optional: request access for the equity-comp brokerage — expect up to six
   weeks, and do not wait for it (§12 is the primary path).

**Step 2 — Link each institution** (~1 min each, once per institution)
1. Run `scripts/link.sh` (built by agents, run by the owner).
2. It opens Link in the browser. **Enter credentials and MFA there** — that page
   is Plaid's; nothing on this machine sees them.
3. Paste the returned `public_token` into the waiting prompt. The script
   exchanges it, writes the `access_token` via `TokenStore` (mode 600), and
   records the item.
4. Link the **highest-value institutions first** — slots are permanent (**F2**).

**Ongoing — when an alert fires:** run `scripts/relink.sh <item>`, which opens
Link in **update mode** for that Item. ~30 seconds, consumes no slot.

---

## 20. Task breakdown

See [`tasks/README.md`](tasks/README.md). Tasks are drafted but **deliberately
unassigned** — assignment is itself subject to review.
