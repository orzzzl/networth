# DESIGN — networth

Status: **proposed** (design phase; nothing implemented).
Author: Claude. Reviewer: Codex.

Revision 3 — reworked after Codex's second review requested changes on
`da53ea7`. Its six findings were all cross-section contradictions: places where
two sections were individually defensible and jointly impossible. Answered in
§8.1/§10 (aggregate age is now a tagged state), §14a (the backup gate must
survive losing the Mac), §6.3 (the Worker learns about a pairing), §9.1/§9.3/§11
(what the phone can and cannot cause), §8.4 (the webhook queue's actual
mechanics), and §9.2/§18 (two statements that disagreed with their consumers).
One further contradiction was found while fixing the first and is fixed with it:
a fixed-value asset's purchase-date clock would have dominated the headline age
forever (§8.1, R3).

Revision 2 — reworked after Codex requested changes on `02c9126`. Its seven
findings are answered in §1 (I5/I6), §2, §4 (F5/F6), §6, §7, §8.1/§8.4/§8.5, §9,
§13, §14a and the task graph.

In both rounds, each place the earlier draft was wrong is called out inline
rather than quietly corrected.

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

- **I1.** Every account carries both of its clocks — when we last *called*
  (`fetched_at`) and when the institution's data was actually current
  (`source_as_of`) — always reachable in the UI without drilling down.
- **I2.** No total is rendered without its **age state** and its staleness
  annotation. A code path that can emit a bare total is a bug. The age is read
  off the **source** clock (I5), never the call clock — and it is a *tagged*
  value, not a timestamp: if any contributing account's age is unknown, the
  total's age is `UNKNOWN` (§8.1 R3). **A total never borrows a date from the
  inputs that happen to have one.** That would describe the age of part of the
  number as though it were the age of the number.
- **I3.** Every Item error state visible to `/item/get` raises an alert within
  one hour, not at the next time someone happens to look. *(Deliberately
  narrowed to what polling can prove — advance-warning events that Item state
  cannot express are handled in §8.4.)*
- **I4.** **The two staleness dimensions are never collapsed.** On a phone the
  number can be old for two independent reasons — the *connection* to an
  institution died, or the *phone's copy* of the snapshot is old — and the UI
  must always distinguish them (§9). Showing one indicator for both would
  reintroduce exactly the lie this project exists to eliminate.
- **I5.** **A successful API call is never, by itself, evidence of freshness.**
  Plaid's `/accounts/get` "retrieves cached information, rather than extracting
  fresh information from the institution," and holdings carry their own
  `institution_price_as_of`. So an HTTP 200 can return the very same frozen
  number the owner abandoned a commercial product over. Freshness must be read
  off a **source clock** defined per product (§8.1); where no source clock is
  available the answer is `UNKNOWN`, and `UNKNOWN` is never rendered as fresh.
- **I6.** **The phone never replaces a newer cached snapshot with an older
  payload.** Authenticated encryption proves a payload is genuine, not that it
  is current; a replayed old ciphertext is a valid ciphertext. Publications
  therefore carry a monotonic sequence inside the authenticated envelope, and
  a rollback is refused *and* surfaced (§9.3).

Staleness is therefore a first-class state machine (§8), not a badge computed at
render time. I5 is the reason the machine is driven by source clocks rather than
by call outcomes: the original failure mode has a quieter twin one layer down,
where every call succeeds and the data behind it is months old.

---

## 2. Scope: single-user now

Built for one person today; the owner may share it later. Both halves are meant
literally, and the failure mode to avoid is over-building.

**Build now:** no backend, no accounts, no login, no multi-tenancy. The app links
the owner's own institutions through Plaid Link; results are stored locally.

**Do not build now, not even scaffolding:** user registration, authentication, a
server, server-side token storage, per-user encryption schemes, sharing or
export-to-another-user. Each costs real work and buys nothing today.

**Reserve cheaply — only these three.** They are nearly free now and painful to
retrofit:

1. **No institution-specific knowledge is hardcoded anywhere** — not in the sync
   engine, not in config, not in UI copy or defaults. Everything comes from the
   runtime link flow. **A hardcoded institution is a bug.** (This is also why §3
   describes account *categories* rather than a list of the owner's banks.)
2. **The sync engine operates over "whatever items exist,"** never over an
   assumed fixed set of accounts.
3. **Token storage sits behind a narrow interface** (`TokenStore`), not file
   reads scattered through the code, so the storage location can change without
   touching sync logic.

Anything beyond that list is speculative generality. Push back on it in review.

**Dropped in review: a reserved `profile_id` column.** An earlier draft carried
one on every table "in case" of multi-user. It failed its own test. The other
three reservations are good design for one user *today* — they keep the sync
engine honest and the token path narrow regardless of how many people ever use
this. A tenant key is different: it earns nothing today while making every
table, uniqueness constraint, query and test carry a column that is always the
same value, and the thing it prepares for is explicitly ruled out below as a
different project. Adding one constant-backed column to a small local SQLite
database later is a cheap migration; the redesign that multi-user actually needs
is not, and a reserved column does not shorten it by a day.

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
docs rather than assumed. **Six findings, two of which corrected the brief and
two of which came out of review:**

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

**F5 — Real-time balance is bundled on Trial, and it is the only way a cash or
card balance's age can be known at all.** *(From review.)* Plaid's accounts docs
are explicit that `/accounts/get` "retrieves cached information, rather than
extracting fresh information from the institution," and point to
`/accounts/balance/get` — which they label a **paid** endpoint — for real-time
balances. Trial bundles Balance, so it costs nothing today. Two consequences,
both recorded rather than buried:

- It is used **deliberately, not incidentally**: without it, a balance-driven
  account has no source clock whatsoever (the balance object's
  `last_updated_datetime` "appears only when the institution is `ins_128026`
  (Capital One)"), so every such account would sit permanently at `UNKNOWN`
  under **I5**. Paying a real call to make freshness knowable is the whole point
  of the product.
- Like **F3**, it is a **cost cliff off Trial**, not a cost today. It therefore
  lives behind one config flag (`balance_mode: realtime | cached`), so leaving
  Trial is a config change plus a visible downgrade to `UNKNOWN` freshness — not
  a rewrite, and never a surprise bill.

**F6 — Plaid tells Trial users in writing to persist their access tokens.**
*(From review.)* "When using a Trial plan, be sure to persist your access tokens
and do not lose track of them. All access tokens created in Production will
count against your Trial plan Item limit." Combined with **F2**, losing the
token file does not cost a re-link — it **strands a permanent slot**. Durable,
tested backup is therefore a *precondition* of the first Production Link (task
03a gates task 08), not an operations chore filed under Phase 5.

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
   ┌────────────── the Mac (already runs 24/7) ──────────────────┐
   │  launchd KeepAlive loop                                     │
   │    SyncEngine → StalenessMachine → Snapshotter              │
   │    PlaidClient (holds client_id/secret + access_tokens)      │
   │    WebhookDrain (pulls + verifies queued events)             │
   │    SQLite: full history, append-only  →  encrypted backup    │
   │                        │            (never leaves the Mac)   │
   │            Publisher: encrypt (seq, AAD) + PUT               │
   └────────────────────────┼─────────────────────────────────────┘
              write token   │  ciphertext only
                    ┌───────▼──────────────┐        ┌──────────┐
                    │  transport (§6)      │◄───────┤  Plaid   │
                    │  current value only  │ webhook│ webhooks │
                    └───────┬──────────────┘  (§8.4)└──────────┘
              read token    │
   ┌────────────────────────▼─────────────────────────────────────┐
   │  Flutter app: fetch → verify seq → decrypt → cache → display │
   │  secrets provisioned by one-time pairing, in the OS keystore │
   │  shows BOTH connection staleness and copy staleness (§9)     │
   └──────────────────────────────────────────────────────────────┘
```

The Mac keeps every credential and the full history. The phone is a **read-only
display of an authenticated, encrypted snapshot** — it never holds a Plaid
token, never calls Plaid, and cannot mutate anything. That asymmetry is what
makes the phone safe to lose.

Three properties of that picture are load-bearing and each is defended below:
the transport holds **only the current value** (§6.2), the phone's credentials
arrive by **runtime pairing** rather than being compiled in (§6.3), and the two
directions use **different credentials** — the Mac can write, the phone can only
read (§6.2).

Seams (interfaces the rest of the code depends on, never concrete classes):

- `PlaidClient` — link tokens, exchange, item status, holdings, liabilities. The
  only place Plaid's error taxonomy becomes our states.
- `QuoteClient` — `get_quote(symbol) -> (price, as_of)`; the quote must carry its
  own timestamp, because a stale price is precisely the failure being hunted.
- `TokenStore` — narrow interface over secret storage (§2 reservation 3).
- `Store` — repositories over SQLite; append-only observations/snapshots.
- `Publisher` — serialize + encrypt + upload the snapshot, then **read it back**
  and assert the transport is serving what was just published (§9.3). Swappable
  transport.
- `WebhookDrain` — fetch queued webhook events from the transport, **verify
  Plaid's signature locally**, convert to item state changes (§8.4). Advisory
  *for the number* — a dropped event can never make the total wrong, because the
  poll floor is what I3 rests on. It is **not** redundant with polling: an
  earlier draft claimed here that "everything it detects, polling eventually
  detects too", which §8.4 then disproves in the same document.
  `PENDING_DISCONNECT`'s `reason` and `disconnect_time` are the counterexample —
  advance warning that no poll can derive.
- `Notifier` — alert delivery (§11).
- `BackupStore` — encrypted snapshot of the database + token material, local
  only (§14a).

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

#### 6.1 Encrypt the payload regardless of transport

The Mac encrypts the snapshot with a symmetric key (AES-256-GCM); the phone
holds the key. The transport provider then stores opaque bytes and never sees a
balance. This decouples "is the transport's auth perfect and free forever?" from
"does a third party learn the owner's net worth?" — the second answer becomes
*no* by construction.

A fresh random 96-bit nonce per publication, and the authenticated-data field
binds `schema_version`, `pairing_id`, `seq` and `published_at` so none of them
can be swapped between valid ciphertexts. One publication a day is nowhere near
any nonce-reuse boundary.

**What encryption does not buy: currency.** A valid ciphertext stays valid
forever, so an old payload replayed by anyone able to write to the transport
decrypts perfectly — an authentic, stale number, which is precisely this
product's cardinal sin wearing a signature. That gap is closed by `seq` and
**I6** in §9.3, not by the cipher.

#### 6.2 Choosing the transport: the deciding property is what it *retains*

An earlier draft recommended a private GitHub repo. Review rejected it, and the
reason generalises into the criterion this section is now organised around: for
a payload published every day under one long-lived key, **what matters most is
not who can read the current value, but how many past values the transport keeps
around to be read later.**

A Git-backed transport is the worst case on exactly that axis. Writes through
the Contents API create commits, and replacing or deleting a file does not
remove the earlier blobs from history. A year of daily publications is a year of
retrievable ciphertexts, all under the same key, so a single leak of the phone's
key plus its read credential is not a disclosure of *today's* net worth — it is
the entire history, retroactively.

**This project already has first-hand proof.** During this design phase a
superseded commit was rewritten out of this repository's branch, and it remains
fetchable by direct SHA on GitHub (recorded as a residual exposure for the
owner). The transport would be that same property, on purpose, every day.
Rotating the key later would be cosmetic: old blobs stay readable under the old
key.

| Transport | Auth | Free? | Mac asleep? | **What it retains** | Verdict |
|---|---|---|---|---|---|
| **Cloudflare Workers + KV** — Mac `PUT`s ciphertext to one key; phone `GET`s it | Two distinct bearer tokens (write / read), checked in the Worker | 100k Worker requests/day; KV 100k reads + 1k writes/day, 1 GB. We need ~1 write and a handful of reads a day | **Yes** | **Current value only**, by design — an overwrite replaces it | **Recommended** |
| Tailscale — phone reaches the Mac directly over WireGuard | Device identity; **no bearer secret anywhere** | Personal tier, long-standing | **No** — Mac must be awake | Nothing; there is no third party | Best on pure security; loses on availability |
| Private GitHub repo (`…-data`) | Fine-grained read-only PAT | Free private repos are mature | Yes | **Every payload ever published**, permanently, by design | **Rejected** — see above |
| Public static host (Pages, etc.) | None | Free | Yes | — | **Rejected** — publishes net worth |
| ntfy / public pubsub free tiers | None or weak | Varies | Yes | — | **Rejected** — no real auth |

**Recommendation: Cloudflare Workers + KV.** It is the only candidate that is
simultaneously available while the Mac sleeps and free of an accumulating
corpus, and its free limits sit three orders of magnitude above one user's
traffic. It costs one new free account (owner-only, §19) and a ~30-line Worker —
which, not incidentally, is also the zero-cost webhook receiver §8.4 needs, so
the second use pays for the setup a second time.

Two credentials, not one: the Mac's **write** token and the phone's **read**
token are different secrets, checked on different routes. A compromised phone
cannot publish, which is what makes the replay defence in §9.3 meaningful rather
than decorative.

**Blast radius, stated plainly.** If the phone is compromised, the attacker gets
the current payload and the phone's local cache — which do contain the history
window the curve renders, because the curve has to come from somewhere. What
they do *not* get is every payload ever published, the ability to publish, any
Plaid token, or the full history, all of which stay on the Mac. Rotation is then
real rather than theoretical: re-pairing (§6.3) mints a new key, the next
publication overwrites the only stored copy, and the old key decrypts nothing
that still exists.

Tailscale remains the stronger choice on pure security and stays a documented
swap behind the `Publisher` seam. Its cost is that opening the app away from
home shows a cached copy — which §9 already renders honestly, so it degrades
rather than breaks. The choice is the owner's (**O5**).

#### 6.3 Provisioning the phone's secrets: pairing, not compilation

An earlier draft injected the payload key and the transport credential into the
APK at build time. Review rejected that too, and correctly: it makes the APK
itself a bearer artifact for the owner's net worth, makes rotation require a
rebuild-and-reinstall, and bypasses the platform's protected secret storage.

Instead, **the app ships with no secrets at all** and is provisioned once at
runtime:

1. On the Mac, `networth pair` mints a fresh payload key, a read-only transport
   token and a `pairing_id`, and renders them as a QR code in the terminal (with
   a typed fallback string).
2. The phone scans it once, on-screen, on the owner's own desk — the material
   never crosses a network during pairing.
3. The app stores it via `flutter_secure_storage`, backed by the **Android
   Keystore**, so the OS protects it rather than a string constant in a DEX file.
4. Rotation, revocation and re-pairing are runtime operations. No rebuild, no
   reinstall, no version bump.

The release APK is therefore not a bearer artifact: losing it leaks nothing.

#### 6.3.1 The Worker has to be told — the control path

*(From review. The draft above minted a read token on the Mac and then claimed
re-pairing "invalidates the read token", with no mechanism by which the Worker
could ever learn either fact. As written the Worker knew exactly one read token,
forever, configured by hand — so rotation was not slow, it was **not
implementable**. A revocation story that cannot run is worse than none, because
it gets believed.)*

A fourth route, authenticated by the credential only the Mac holds:

| Route | Auth | Body | Effect |
|---|---|---|---|
| `POST /pairing/rotate` | **write** token | `{pairing_id, read_token_verifier}` | atomically replaces the active pairing **and deletes the stored snapshot** |
| `POST /pairing/revoke` | **write** token | — | clears the active pairing and deletes the stored snapshot |

**The Worker stores a verifier, never a token.** `read_token_verifier =
SHA-256(read_token)`; `GET /snapshot` hashes the presented bearer and compares
in constant time. Read tokens are 256-bit random strings, so a plain hash is
sufficient and a slow KDF would buy nothing — there is no guessable password
here. A leak of the Worker's KV therefore does not yield a working read
credential, and the Mac keeps the only copy of the token itself (in
`~/agents/secrets/`) until the phone scans it.

**Rotation order, and what each failure leaves behind.** The sequence is chosen
so that no step can leave the *old* token working:

1. Mint locally; write the `pairing` row as `PENDING`. Nothing has changed
   anywhere else, so a crash here is a no-op.
2. `POST /pairing/rotate`. One request, so the Worker cannot be left with a new
   verifier and an old snapshot readable by the old key. **On failure, abort and
   print why** — the old pairing is still active, the QR is *not* rendered, and
   the owner re-runs the command. Never render the QR before this returns 2xx:
   a phone holding material the Worker does not know is indistinguishable to the
   owner from a broken transport.
3. On success, mark the new row `ACTIVE` and the old one `revoked_at = now`.
   **From this instant the old phone is locked out** even though the new phone
   has not scanned anything yet. That ordering is deliberate: the case that
   matters is a *stolen* phone, and it must not stay readable while the owner
   walks to their desk.
4. Publish immediately under the new key with the next `seq`. If this fails, the
   pairing is still correct — the phone pairs and shows "no data yet" until the
   publish job retries on the next tick (§13), and `doctor` reports the
   publication as overdue (§11). Degraded, visible, self-healing.
5. Render the QR.

**Deleting the snapshot on rotate is not housekeeping.** The scenario is a
stolen phone, and it holds the old payload key. Revoking its token stops it
fetching *new* ciphertext; deleting the stored object removes the one thing its
key could still decrypt if it kept fetching. Both halves are needed, which is
why they are one route.

`networth revoke` is the same path without minting: it is the lost-phone
command, and it deliberately does not require a replacement phone to be present.
The Mac keeps publishing; nobody can read.

**`seq` never resets across pairings** (§7). A fresh pairing does not restart the
counter, so a payload from an earlier pairing can never present a higher `seq`
than the current one; `pairing_id` is in the AAD besides, and the phone refuses
foreign pairings outright (§9.3). The phone resets its own `last_seq` only when
*it* scans a new QR.

The write token is a Worker secret set by `wrangler` and held on the Mac;
rotating it is an owner operation (§19), not something the system does to
itself. There is no route that can change it, by design — a Worker that can
re-key its own write credential is a Worker that can be re-keyed by whoever
reaches that route.

**The APK is still secret-free.** Everything above happens between the Mac and
the Worker, or between the Mac and the phone across a QR code on a desk.

#### 6.4 Rotation and expiry monitoring

A transport credential that dies silently would freeze the phone's number — this
product's cardinal sin arriving through the back door — so expiry is monitored
rather than assumed.

The mechanism is **evidence, not arithmetic**: the `publication` audit table
(§7) records every attempt, and `doctor` plus the alert path fire on *"the last
successful publication is older than expected"*. That catches a revoked token, a
deleted namespace, a network fault and an expiry with one check, instead of
parsing an expiry date and trusting it.

*(Correcting the earlier draft: it claimed fine-grained GitHub PATs expire after
at most one year. They accept 1–366 days **or no expiry at all**; "one year" is
not a platform maximum. The claim is moot now that the Git transport is
rejected, but the correction belongs on the record.)*

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
institution(id, plaid_institution_id, name, is_oauth)

item(                                  -- one per institution LOGIN
  id, institution_id,
  plaid_item_id,
  secret_ref,                          -- KEY NAME resolved by TokenStore, never the token
  status, status_since,                -- §8.2 connection state machine
  last_successful_sync, last_attempted_sync,
  last_error_code, last_error_message,
  consent_expiration_time,
  replaces_item_id,                    -- set when this Item replaced a REVOKED one (§8.5)
  created_at)

account(
  id, item_id,                         -- item_id NULL for manual assets
  plaid_account_id, name, official_name, mask,
  type, subtype, currency,
  sign,                                -- +1 asset, -1 liability
  freshness_policy,                    -- SYNCED_HOLDINGS | SYNCED_BALANCE
                                       --   | MANUAL_STATIC | MANUAL_QTY_LIVE_PRICE
  include_in_net_worth,                -- exclude without deleting
  lineage_id,                          -- stable across re-links; defaults to own id (§8.5)
  reconciliation_state,                -- NEW | CONFIRMED | ARCHIVED
  superseded_by_account_id, superseded_at,
  last_fetch_at,                       -- when we last called successfully
  last_source_as_of,                   -- when the DATA was last actually current (I5)
  created_at, archived_at)

manual_asset(
  account_id, kind,                    -- REAL_PROPERTY | EQUITY_SHARES
  static_value_minor,                  -- REAL_PROPERTY
  symbol, share_count,                 -- EQUITY_SHARES
  valued_as_of, note)

observation(                           -- append-only, one row per account per run
  id, sync_run_id, account_id, observed_at,
  value_minor, currency,
  source,                              -- PLAID_HOLDINGS | PLAID_BALANCE | MANUAL | QUOTE
  fetched_at,                          -- when WE called
  source_as_of,                        -- when the INSTITUTION's data was current; NULL = unknown
  source_clock,                        -- which evidence produced it (§8.1); UNKNOWN if none
  is_carried_forward)                  -- TRUE = reused a prior value, not fetched

snapshot(
  id, sync_run_id UNIQUE,              -- one snapshot per successful run; the idempotency key
  taken_at,
  total_net_worth_minor, total_assets_minor, total_liabilities_minor,
  account_count, stale_account_count, unknown_freshness_account_count,
  static_account_count,                -- MANUAL_STATIC; outside the age basis (§8.1 R3)
  reauth_account_count, unreconciled_account_count,
  is_complete,                         -- FALSE if anything was carried forward or unreconciled
  -- the total's age, as a TAGGED value, never a bare timestamp (I2, §8.1 R3):
  age_state,                           -- KNOWN | UNKNOWN | STATIC_ONLY
  as_of,                               -- the total's age; NOT NULL iff age_state = KNOWN
  oldest_known_source_as_of,           -- diagnostic floor; NULL if nothing is known.
                                       --   NEVER rendered as the total's age
  CHECK ((age_state = 'KNOWN') = (as_of IS NOT NULL)))

sync_run(id, started_at, finished_at, trigger, ok, error_summary)

alert(id, created_at, kind, item_id, account_id, message,
      notified_at, acknowledged_at, resolved_at)

pairing(id, created_at, key_ref, read_token_ref,   -- refs only, never the material
        state,                                     -- PENDING | ACTIVE | REVOKED (§6.3.1)
        registered_at,                             -- when the Worker accepted the verifier
        revoked_at)                                -- §6.3.1

publication(                                       -- §6 audit trail
  id, snapshot_id, pairing_id, seq UNIQUE,         -- monotonic, NEVER reset (§6.3.1); replay defence (I6)
  schema_version, published_at, transport, ok, error,
  readback_ok, readback_seq)                       -- what the transport served straight back (§9.3)

webhook_event(                                     -- §8.4; advisory input, never a dependency
  id,
  queue_key,                                       -- the transport-side key; the ack target
  received_at,                                     -- the WORKER's clock at receipt — what `iat`
                                                   --   is checked against, never the drain time
  drained_at, processed_at,
  verified,                                        -- Plaid JWT checked ON THE MAC
  body_sha256, jwt_iat,                            -- UNIQUE(body_sha256, jwt_iat) — at-least-once
                                                   --   delivery means duplicates are normal
  webhook_type, webhook_code, plaid_item_id,
  reason, disconnect_time,                         -- PENDING_DISCONNECT carries both
  raw_ref)
```

Modelling choices worth defending:

**Two clocks per observation, never one.** `fetched_at` and `source_as_of` are
separate columns because they answer different questions and only the second one
matters to the owner. Collapsing them is not a shortcut, it is **I5** violated in
storage, and no amount of careful UI work downstream can recover a distinction
the schema threw away. `source_clock` records *which* evidence was used, so a
freshness claim can always be traced to the field that justified it — and
`UNKNOWN` is a first-class value, not a `NULL` to be quietly coalesced.

**The total's age is a tagged value, not a timestamp column.** *(From review.)*
The previous schema had a single scalar, `oldest_contributing_source_as_of`, and
that column could not be filled honestly: with one `UNKNOWN` contributor there
is no timestamp to put in it, and putting the oldest *known* one there describes
part of the number as though it described the number. So the age is
`(age_state, as_of)` with a `CHECK` binding them, `oldest_known_source_as_of` is
kept beside it as a diagnostic that is explicitly not the answer, and the
impossible row — `age_state = 'UNKNOWN'` carrying a date — cannot be written at
all. Enforcing it in the schema rather than in the writer matters because the
lie this project exists to prevent *is* a stale timestamp presented confidently.

**`lineage_id` outlives Items.** Plaid documents that a re-linked Item can return
different `account_id`s for the same real account. Keying history on
`plaid_account_id` would therefore sever a curve on re-link, or double it, both
silently. History joins on `lineage_id`, which survives re-linking because a
human confirms the mapping (§8.5).

**`freshness_policy` is per-account, not a global rule.** Real property must
never be flagged stale — it is fixed *by design*, so flagging it would train the
owner to ignore the staleness signal, destroying the one feature that matters.
It reads "manual, set on <date>" instead. Manual equity has two clocks: the share
count (manual, no expiry) and the price (must be fresh). Holdings and balances
read their freshness from different Plaid fields (§8.1). No single global rule
expresses that.

**`observation.is_carried_forward` is explicit.** When a sync fails the account
still contributes its last known value (§10), but the row records that it was
carried forward rather than fetched. Its `source_as_of` is inherited from the
last real observation and **never advances** — a carried-forward row ages, which
is the entire point. Without that flag, history retroactively looks healthy and
the product starts lying about its own past.

**`snapshot.sync_run_id` is UNIQUE, and snapshots are appended.** Every
successful run appends one row; re-running the same day appends another rather
than overwriting. The unique key makes a retried run idempotent without making
the audit trail mutable. "Today's number" is a *view* (latest row, or latest per
day for the curve), not a row that gets edited in place — an append-only table
that is sometimes updated in place is not an audit trail.

**`publication.seq` is UNIQUE and monotonic** so the phone can refuse a rollback
(**I6**, §9.3), and `publication` exists so the Mac knows whether the phone
*could* have seen the latest snapshot. Sync succeeding and publish failing is a
distinct failure, and §9 depends on telling them apart.

---

## 8. The staleness machine — two axes, never one

The first draft of this section had a single per-Item machine in which an
account went `STALE` when *our last successful call* aged past a threshold.
Review caught the flaw, and it is the same flaw the product exists to fight:
**our call succeeding says nothing about whether the institution's data moved.**
Plaid serves `/accounts/get` from cache and updates investments on its own
cadence, so a perfectly healthy Item can return a frozen number indefinitely —
green everywhere, wrong on screen.

So freshness is now two independent axes:

| Axis | Scope | Answers | Driven by |
|---|---|---|---|
| **A — connection health** (§8.2) | Item | "Can we still talk to this institution?" | Plaid error taxonomy + call outcomes |
| **B — data freshness** (§8.1) | account | "How old is the data itself?" | the **source clock** |

They are genuinely independent, and the interesting cell is the one the old
model could not express: **Axis A `HEALTHY` + Axis B `STALE`** — every call
succeeds, nothing errors, and the number has not moved in three weeks. That is
the exact failure that started this project, and it now has a name and a state.

### 8.1 Axis B: the source clock, per product

**Rule R1 — `fetched_at` never becomes `source_as_of`.** The only exception is
an endpoint documented to extract live from the institution, and the exception
is named per product below, never assumed from a 200.

**Rule R2 — no evidence means `UNKNOWN`, and `UNKNOWN` is not fresh.** It is
displayed as "can't verify how old this is", never as a healthy value.

**Rule R3 — the headline's age is a tagged state over the oldest *source* clock,
never the run time.** *(Rewritten in review; the one-line version it replaces was
wrong in two independent ways.)*

The **age basis** is the set of accounts contributing to the total *whose clocks
are expected to advance* — `SYNCED_HOLDINGS`, `SYNCED_BALANCE`,
`MANUAL_QTY_LIVE_PRICE`. Then:

| Age basis | `age_state` | `as_of` |
|---|---|---|
| every member has a source clock | `KNOWN` | the **oldest** of them |
| **any** member is `UNKNOWN` | **`UNKNOWN`** | none — there is no honest date |
| empty (only fixed-value assets) | `STATIC_ONLY` | none |

`oldest_known_source_as_of` is recorded alongside as a diagnostic and is never
presented as the total's age.

**Why `UNKNOWN` has to poison the whole total.** An account in
`balance_mode: cached` contributes a real number with no knowable age (R2). If
the headline then showed the oldest *known* clock, it would state a date that is
true of some of the money and unknown for the rest — which is exactly the
commercial-aggregator move that started this project, performed with more
arithmetic. `unknown_freshness_account_count` keeps the badge from going green,
but a count next to a confident date does not stop the date being read. So the
date goes away: **"can't date this total — N of M accounts can't be dated"**,
with the per-account breakdown one tap down. A number the owner has to inspect
is a better outcome than a number he wrongly trusts.

**Why fixed-value assets are outside the basis** *(a contradiction found while
fixing the above, not raised in review — recording it because the fix was one
line away from being wrong in a way that would have looked like working
software).* Real property is `MANUAL_STATIC` with `source_as_of = valued_as_of`,
i.e. the purchase date. Under the old rule the oldest contributing clock was
**always the house**, so the headline would have read "as of 3 years ago" every
day forever — a permanently alarming, permanently useless signal, and precisely
the way to train the owner to ignore the one indicator that matters. Excluding
policy-static accounts is not hiding them: they are counted in
`static_account_count`, the total is annotated *"includes N fixed manual
valuations"*, and each still shows its own `valued_as_of` beside it (§12). The
distinction is that a fixed value is not *stale* — it is doing exactly what it
was configured to do.

`STATIC_ONLY` is a real state, not a theoretical one: before the first Item is
linked, the only assets in the database are manual, and the app must be able to
say so rather than compute an age over an empty set.

| Account kind | Call | `source_as_of` comes from | Fresh while |
|---|---|---|---|
| Investments / holdings | `/investments/holdings/get` + `/item/get` | the **older** of: the minimum `institution_price_datetime` (else `institution_price_as_of`) across contributing holdings, and `status.investments.last_successful_update` | at or after the most recent market close (+12h posting grace) |
| Cash / card balance, `balance_mode: realtime` | `/accounts/balance/get` | `balance.last_updated_datetime` when the institution provides it, else `fetched_at` — justified **only** because this endpoint extracts live (F5) | within 36h |
| Cash / card balance, `balance_mode: cached` | `/accounts/get` | nothing — `UNKNOWN` (R2) | never; permanently labelled unverifiable |
| Liabilities detail | `/liabilities/get` | the balance clock above. Statement dates are *not* freshness evidence | as above |
| Manual, static | — | `valued_as_of` | always, by policy (§12) |
| Manual, qty × quote | `QuoteClient` | the quote's own `as_of` | quote at or after the last market close |
| Carried forward | — | inherited, **never advanced** | ages continuously |

Two details that will otherwise be discovered as bugs:
`institution_price_datetime` is documented to be returned only by select
institutions and "may contain default time values (such as 00:00:00)", so an
exactly-midnight value is treated as date-granular rather than as a precise
instant; and holdings freshness is taken as the **oldest** contributing holding,
because one stale position is enough to make a portfolio total wrong.

Market-close awareness stays where it belongs — on this axis. Plaid updates
investments after close on market days, so a Friday-close value is legitimately
~63h old on Monday morning and must **not** alarm. A naive 24h rule would scream
every weekend, and an alarm that cries wolf every Saturday is worse than no
alarm at all. Cash and card accounts, which have no market calendar, use plain
wall-clock 36h (the owner's threshold).

### 8.2 Axis A: connection state, per Item

```
                 ┌──────────┐
   link exchange │ HEALTHY  │  last call succeeded, no error on the Item
        ────────►└────┬─────┘  (says NOTHING about data age — that is Axis B)
                      │
   INSTITUTION_DOWN / _NOT_RESPONDING /    ┌────────────┐
   RATE_LIMIT / transport-level errors     │ DEGRADED   │ retry w/ backoff,
                      ├────────────────────►            │ NO owner action,
                      │◄───────────────────┤            │ no alert
                      │  successful call   └────────────┘
                      │
   ITEM_LOGIN_REQUIRED / PENDING_EXPIRATION ┌─────────────┐
   / PENDING_DISCONNECT / consent expired   │ NEEDS_REAUTH│ OWNER ACTION,
                      ├─────────────────────►             │ alert immediately
                      │◄────────────────────┤             │
                      │ Link *update mode*  └─────────────┘
                      │
   ITEM_NOT_FOUND / revocation that cannot  ┌────────────┐
   enter update mode                        │ REVOKED    │ OWNER DECISION:
                      └─────────────────────►            │ costs a permanent
                        replace + reconcile └────────────┘ slot (§8.5)
```

Rules:

- **`STALE` is not on this axis.** Data age is Axis B (§8.1) and is computed from
  source clocks, per account. An Item is not "stale"; its *data* is.
- `DEGRADED` is **not** owner-actionable and must not alert like `NEEDS_REAUTH`.
  Confusing "the internet was down" with "your connection is dead" is how alert
  fatigue starts. It is still visible in the UI — silently swallowing it would be
  its own small lie — just never as a demand for action.
- Transitions record `status_since`, so the UI says "needs re-auth since
  Tuesday" rather than just "broken".
- The state change is written **before** any notification is sent, so a crash
  between the two re-notifies rather than silently dropping the alert.

### 8.3 Re-authentication must use Link update mode — the critical constraint

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
never taken automatically, and it does not end at the new Link — see §8.5.

### 8.4 What polling proves, and what it cannot

The brief asked for Plaid error webhooks; the first draft replaced them with
hourly `/item/get` polling and claimed "the same signal". Review disproved the
claim, and it was worth disproving:

- **`PENDING_DISCONNECT` is an advance warning with content.** It carries a
  `reason` (e.g. `INSTITUTION_MIGRATION`) and a `disconnect_time` — "the date and
  time at which the Item is scheduled to disconnect". Neither is derivable from
  the Item's `consent_expiration_time`. Polling learns about a migration when it
  breaks; the webhook says it is coming, and when.
- **`USER_PERMISSION_REVOKED`** fires when the end user revokes permission, and
  Plaid notes it "may not be possible to launch update mode" — i.e. this is the
  event that lands in `REVOKED`, and it can arrive without a poll ever seeing a
  usable error.

So the honest position is a floor and an accelerator, not an equivalence:

**The floor — polling, always on, no infrastructure.** `/item/get` per Item,
hourly, returns the Item's current `error`. This is what **I3** promises, and I3
is deliberately worded as *"every Item error state visible to `/item/get`"* —
what polling can actually prove.

**The accelerator — a webhook drain, zero marginal cost.** §6 already stands up
a Cloudflare Worker for the transport, so the receiver is one extra route on
infrastructure the project has anyway. Verification happens **on the Mac**: the
verification endpoint needs `client_id` + `secret`, and putting those in a
Worker would scatter the Plaid credential to a second place to save nothing.

That split is what makes the queue between them load-bearing, and the first
draft specified it in one clause ("appends … to a KV queue") that KV cannot
honour. Review was right to stop there, so the mechanics are now spelled out.

#### 8.4.1 Why "append to a KV queue" was not implementable

Cloudflare's own docs, checked rather than assumed:

- **KV has no append and no transaction.** "Due to the eventually consistent
  nature of KV, concurrent writes to the same key can end up overwriting one
  another," and "the last write will take precedence." A queue held in one key
  silently drops an event whenever two arrive together — and institution
  migrations are exactly when several arrive together.
- **KV rate-limits one write per second per key.** "Workers KV has a maximum of
  1 write to the same key per second. Writes made to the same key within 1
  second will cause rate limiting (`429`) errors."
- **KV is eventually consistent**: "changes may take up to 60 seconds or more to
  be visible in other global network locations."

#### 8.4.2 The queue: one key per event, ack by delete

**One unique KV key per event** removes every problem above by never writing the
same key twice.

1. Plaid `POST`s to a long unguessable path on the Worker.
2. The Worker holds no Plaid credential and does **no** verification. It caps
   body size, drops anything oversized, and writes exactly one key:
   `hook:<received_at_ms>:<crypto.randomUUID()>`, with `expirationTtl` of 7 days.
   Key collision is impossible, so no event can overwrite another.
3. The value is a JSON envelope: `{received_at, verification_header,
   body_b64}`. **The raw request bytes are base64'd, never re-serialized.**
   Plaid's `request_body_sha256` "is sensitive to the whitespace in the webhook
   body and uses a tab-spacing of 2" — a Worker that parsed the JSON and wrote it
   back would break every signature it touched, and the failure would look like
   an attack rather than a bug. The `Plaid-Verification` JWT goes in the value
   for the same reason it cannot go in KV metadata: metadata is capped at 1024
   bytes and the JWT can exceed it.
4. `received_at` is the **Worker's** clock at receipt, and it is the whole reason
   the envelope exists — see §8.4.3.
5. The Mac drains every tick **through the Worker, never against the KV API
   directly** — `GET /hook/queue` returns the pending envelopes, and
   `DELETE /hook/queue/:key` acks one (§16), both authorised by the same write
   token the Mac already holds. It verifies, inserts, and **deletes last**, so a
   crash mid-drain re-delivers rather than loses. Going through our own Worker is
   not indirection for its own sake: reaching KV directly requires a Cloudflare
   **API token**, which is account-scoped, and putting one on the Mac would throw
   away the exact property that decides §8.4.3 against Cloudflare Queues.
6. Delivery is therefore at-least-once and duplicates are *normal*, not
   exceptional: `UNIQUE(body_sha256, jwt_iat)` on `webhook_event` makes a
   re-delivery a no-op insert. An idempotent consumer is cheaper than an
   exactly-once queue and is the only thing that can be relied on anyway.

Verification, on the Mac: `ES256` JWT against `/webhook_verification_key/get`,
`request_body_sha256` compared to the base64-decoded body with a constant-time
comparison, and the freshness check below. Unverified events are counted and
discarded, never acted on.

**Lifecycle, stated so nothing accumulates or disappears quietly.** Undrained
keys expire after 7 days (bounded storage, and §8.4's floor covers a lost
event). The drain is single-consumer by construction — one launchd loop, one
instance — so there is no consumer contention to reason about. If any key's
`received_at` is more than an hour old at drain time the drain itself is broken:
that raises an alert (§11) rather than being noticed a week later when the TTL
eats the evidence. KV `list` being eventually consistent (up to ~60s) is
harmless here precisely because of the next subsection.

#### 8.4.3 The five-minute rule, checked against the right clock

Plaid: "Use the issued at time denoted by the `iat` field to verify that the
webhook is not more than 5 minutes old." The Mac drains every five minutes. Two
five-minute windows in series means a genuine webhook can arrive at the drain
already "expired" — the drain would then reject real events, and only sometimes,
which is the worst kind of bug to find in production.

**So `iat` is compared against `received_at` — the Worker's receipt time —
never against the drain time.** The Worker is the process that actually received
the request, so its clock is the one the rule is about; the drain delay is an
artefact of our architecture and must not consume Plaid's window.

That this stays sound is worth showing rather than asserting, since the receive
route is unauthenticated by necessity (Plaid cannot present our credential):

- **A forged event** fails JWT verification — the signing key is Plaid's.
- **A replayed genuine event** gets a *fresh* `received_at` from the Worker and
  carries its *original* `iat`, so it fails the five-minute check exactly as
  Plaid intends. The receipt-time fix does not weaken replay protection.
- **A replay inside five minutes** passes, and is inert: it collides on
  `UNIQUE(body_sha256, jwt_iat)`.
- **`received_at` itself cannot be forged** by the sender; it is written by the
  Worker. Values in the future, or older than the retention window, are
  discarded as corrupt.
- **Flooding the route** costs storage, not correctness: bodies are size-capped,
  keys expire, and unverified events never reach the state machine. If the free
  tier were ever exhausted the drain stops and the poll floor carries on — the
  number stays honest, which is the property that has to hold.

**Cloudflare Queues was the alternative and is the documented upgrade.** It is
now on the Workers Free plan (10,000 operations/day, far above a handful of
webhooks) and offers real at-least-once delivery with acks and a dead-letter
queue. It is not chosen today for one reason: HTTP pull consumers authenticate
with a **Cloudflare API token**, which would put an account-scoped credential on
the Mac. The KV path keeps the Mac's entire transport credential surface at one
bearer token that our own Worker checks — the same argument that kept Plaid's
credentials out of the Worker (§8.4). If webhook volume or ordering ever
justifies it, Queues is a swap behind `WebhookDrain`, not a redesign.

**What we accept when the drain is off or a webhook is lost:** advance warning.
A migration or revocation is then detected after the fact, on the next poll,
when the Item's error surfaces — `PENDING_DISCONNECT`'s deadline having already
passed, or the Item having landed in `REVOKED`. The number stays honest either
way, because the account's data ages visibly on Axis B regardless. Because the
floor holds on its own, **a dropped webhook can never cause a wrong number, only
a later warning** — which is why the drain is allowed to be best-effort.

### 8.5 Replacing an Item: reconcile before contributing

When an Item reaches `REVOKED` and update mode cannot recover it, the owner
spends one of ten permanent slots (**F2**) on a fresh Link. The new Item is
*not* a drop-in replacement: Plaid documents that when an `access_token` is
deleted and new credentials are used later, "the new `account_id` will be
different from the old `account_id`."

Left alone, that produces exactly two silent disasters — the old and new
accounts both counted (net worth inflates overnight) or the old curve orphaned
and the new account starting from nothing (history severed). Both look like real
financial events. So replacement is a flow, not an event:

1. **New accounts start at `reconciliation_state = NEW` and contribute
   nothing.** A visibly incomplete total is a bug report; a silently doubled one
   is a lie. The snapshot records `unreconciled_account_count` and is
   `is_complete = FALSE` while any exist, so the phone shows it as action needed.
2. `scripts/reconcile.sh` proposes a mapping from archived to new accounts on
   `mask`, `type`, `subtype`, `currency` and name similarity, and **prints it for
   the owner to confirm**. It never auto-applies — a wrong automatic match
   corrupts history in a way no later run can detect.
3. On confirmation the new account inherits the old account's `lineage_id`; the
   old one is archived with `superseded_by_account_id` and `superseded_at`. The
   curve joins on `lineage_id`, so it stays continuous across the seam.
4. Unmatched old accounts are archived with a reason (closed, or moved); unmatched
   new accounts keep their own `lineage_id` and start their own history.
5. The new Item records `replaces_item_id`, so the slot ledger (§14) can show
   where the permanent slots went.

---

## 9. Copy staleness — the second dimension the phone adds

**I4** exists because a phone can be wrong in a second, independent way. The
Mac's view has one question ("how old is each institution's data?"). The phone's
view has two, and they have different fixes. The first draft asserted the
distinction but never said *when* a copy becomes stale, which made I4
unimplementable — a test could not have been written against it. This section
now defines it in terms a test can assert.

### 9.1 When a copy is stale — the actual definition

The payload carries `published_at`, `publish_interval_seconds` (86400) and
`grace_seconds` (default 21600 — six hours), so the deadline travels *with* the
data and the phone never hardcodes the Mac's cadence:

```
stale_after = published_at + publish_interval_seconds + grace_seconds
```

The phone evaluates, in order:

1. **Clock disagreement → `COPY_UNKNOWN`.** If `published_at > device_now + 5min`
   (payload from the future), or the device clock has moved backwards since the
   last successful fetch (detected by pairing each stored timestamp with a
   monotonic reading), the phone **cannot** compute an age. It says so — "this
   device's clock disagrees with the Mac's" — and never renders the number as
   fresh. Silently trusting a skewed clock is how a six-day-old figure gets shown
   as current.
2. **`device_now <= stale_after` → `COPY_FRESH`.**
3. **Otherwise → `COPY_STALE`**, with a *reason*, because two very different
   faults land here and they have different fixes. The reason is
   `MAC_NOT_PUBLISHING` **iff all three hold**, and `CANNOT_CHECK` otherwise:

   ```
   last_fetch_success_at >= stale_after          -- we reached the source AFTER the
                                                 --   copy was already due, not merely
                                                 --   "recently"
   last_fetch_attempt_at == last_fetch_success_at -- and nothing has failed since
   last_fetch_seq        == last_seq              -- and it had nothing newer than we hold
   ```

   - `MAC_NOT_PUBLISHING` → *"reached the source; the Mac has not published
     since <time>."* The phone and the network are fine; the Mac or its sync is
     not.
   - `CANNOT_CHECK` → *"couldn't check since <last_fetch_success_at>"*, plus the
     error class (offline, credential rejected, transport error, never fetched).
     The Mac may be perfectly healthy and unreachable.

   *(Rewritten in review. The draft said "`last_fetch_at` is recent", which is
   not a condition — "recent" was undefined, and `last_fetch_at` conflated an
   attempt with a success. A state whose predicate cannot be written down cannot
   be tested, and this state exists specifically to assign blame correctly.)*

The phone therefore persists `last_fetch_attempt_at`,
**`last_fetch_success_at`**, `last_fetch_error`, `last_fetch_seq` (the `seq` the
last *successful* fetch returned) and `last_seq` (the `seq` of the payload
actually held) — not merely an attempt timestamp. "We tried", "we succeeded and
there was nothing new" and "we hold this version" are three different facts, and
the predicate above needs all three to name the right culprit.

**Offline** is not a special case: fetches fail, the cached payload keeps aging,
and it crosses `stale_after` on schedule with the reason "couldn't check". The
app is fully usable offline — it just never claims the number is current.

### 9.2 The display matrix

The connection axis arrives from the Mac already evaluated (the phone never
re-implements policy) as one of three **display** states — split this way
specifically so that the "no owner action" states in §8.2 can never be rendered
as a demand to re-link, which the earlier matrix did:

- **`OK`** — no Item error, and every contributing account fresh on Axis B.
- **`WAITING`** — data behind expectation (Axis B `STALE`/`UNKNOWN`) and/or an
  Item `DEGRADED`. Informational. **Never asks the owner to do anything.**
- **`ACTION_NEEDED`** — an Item is `NEEDS_REAUTH` or `REVOKED`, unreconciled
  accounts exist (§8.5), **or an account's data is `FROZEN`** (below). Only this
  state invites a re-link.

**Axis-B staleness escalates.** *(From review, which caught this section saying
Axis B never asks for action while §11 and the runbook told the owner to
re-link.)* The two are not both wrong — they were describing different points on
the same timeline, and the timeline was missing:

| Axis B condition | Display | Why |
|---|---|---|
| behind its expectation window | `WAITING` | Institutions post late constantly. Asking the owner to fix Friday-afternoon holdings would burn the alert on noise. |
| Item `HEALTHY`, source clock not advanced for **5 consecutive market days** → `FROZEN` | **`ACTION_NEEDED`** | This is no longer "late". It is the failure the project was built to catch, and a re-link in update mode usually clears it. |

`FROZEN` is a named Axis-B state, carried in the payload with the account it
belongs to, so the phone renders the specific claim — *"this account has
answered for 5 market days without its data moving"* — rather than a generic
re-link prompt. **The five-day threshold is defined once** (§11) and both the
screen and the alert read it from the same place; the contradiction existed
because they each had their own answer.

**`UNKNOWN` stays in `WAITING` and never escalates**, because under
`balance_mode: cached` it is permanent and there is nothing to act on: no
re-link makes an endpoint start reporting a clock it does not have. Its copy is
written as a standing caveat — *"this account's age can't be verified"* — not as
a wait, since nothing is going to arrive.

| | **Connection `OK`** | **Connection `WAITING`** | **Connection `ACTION_NEEDED`** |
|---|---|---|---|
| **`COPY_FRESH`** | Show the number plainly. | "N accounts haven't updated since Friday — waiting on the institution." No button. | "N connections need re-linking." Owner acts. |
| **`COPY_STALE`** | "Showing a copy from 2 days ago — <reason>." The accounts may be perfectly fine. | Both, stated separately: an old copy *and* data known to be behind as of that copy. | Both, stated separately. The re-link prompt is qualified: this is what was true 2 days ago. |
| **`COPY_UNKNOWN`** | "Can't tell how current this is — clock mismatch." Number shown, freshness explicitly not claimed. | As left, plus what the copy said. | As left, plus the re-link prompt, qualified. |

Rules the implementation must hold:

- Both indicators are visible on the main screen, visually distinguishable, and
  **never merged into one "⚠ stale" icon**.
- **A stale or unknown copy must not present per-account "fresh" badges.** They
  are suppressed or explicitly qualified as "as of the copy" — the phone does not
  know the current state and saying so is the honest answer.
- The connection state shown is always the one *inside the payload*, so a stale
  copy shows a stale connection state — correctly labelled as historical rather
  than silently presented as now.
- The Mac's `publication` table (§7) lets `doctor` distinguish "sync failed" from
  "sync fine, publish failed" — the latter is invisible from the phone alone.

### 9.3 Replay and rollback (**I6**)

AES-GCM proves a payload is authentic. It does not prove it is *current*: an old
ciphertext is authentic forever. Anyone able to write to the transport — or a
transport that silently serves a cached older object — could roll the phone back
to a comfortable old number that verifies perfectly.

- Every publication carries a **monotonic `seq`** (§7), inside the authenticated
  data along with `pairing_id`, `schema_version` and `published_at`, so none of
  them can be lifted from one payload into another.
- The phone persists `last_seq` and **refuses any payload with `seq < last_seq`**,
  keeping the newer cached snapshot. `seq == last_seq` is not an error — it is
  the normal "nothing new yet" signal that §9.1 uses to blame the Mac rather than
  the network.
- A rejected rollback is **surfaced, not swallowed** — but **on the phone**, and
  only there: a persistent transport-integrity warning that survives restarts
  until the phone sees a `seq` greater than `last_seq`, plus a local log of the
  rejection. A silent rejection would hide the one event that indicates someone
  is tampering with the transport.
- `pairing_id` mismatch is likewise refused: after a re-pair (§6.3.1) the phone
  ignores payloads addressed to the old pairing.

**The Mac is not told, and that is a decision.** *(From review, which caught the
draft promising a macOS alert for an event only the phone can observe.)* §5 makes
the phone read-only — no Plaid token, no write credential, "cannot mutate
anything" — and that asymmetry is the entire reason a lost phone is a
non-event. A phone→Mac diagnostics channel would trade it away: an authenticated
write route reachable from the device most likely to be lost, existing to carry
a rare signal, and itself a thing to threat-model and abuse. The claim is
therefore withdrawn rather than the channel built. **The warning lives where the
evidence is.**

What the Mac *can* observe, it now does. `Publisher` performs a **read-back**
after every publication: it re-fetches the object it just wrote and asserts the
returned `seq` matches. A mismatch means the transport is serving something
other than the current value, and that raises a Mac-side alert (§11), recorded
in `publication.readback_ok` / `readback_seq`. It costs one extra request per
day.

Stated precisely, because a partial defence described as a whole one is its own
kind of lie: the read-back catches a transport that is *globally* serving stale
or wrong content. It cannot catch an edge serving only the phone an older
object — different edge, different cache. That case is caught by the phone,
warned about on the phone, and reaches the owner when he looks at the phone,
which is where he was already looking at the number.

---

## 10. Net-worth computation

```
net_worth = Σ(asset accounts) − Σ(liability accounts)
```

1. **A stale account still contributes its last known value**, flagged
   `is_carried_forward`, and the snapshot is marked `is_complete = FALSE`.
   Rejected alternative: excluding stale accounts, which makes the total silently
   *drop* — a different lie, and a scarier one.
2. The headline's age is the **tagged state** of R3 — `(age_state, as_of)` over
   the age basis — not the run time and not the last successful call. A run that
   succeeded everywhere against data that is all a week old is a week-old
   number, and says so; a run in which even one contributor cannot be dated
   produces a total that **has no date**, and says that instead.
3. The presentation contract (**I2**) is enforced in the query layer and in the
   payload schema, so no UI can bypass it: every total ships with `age_state`,
   `as_of` (present only when `age_state = KNOWN`), `stale_account_count`,
   `unknown_freshness_account_count`, `static_account_count`,
   `reauth_account_count`, `unreconciled_account_count` and `is_complete`.
   Accounts with `UNKNOWN` freshness are counted **separately** from stale ones —
   "we know this is old" and "we cannot tell how old this is" are different
   admissions and merging them would launder the second into the first.
3b. **The type makes the undated total unrepresentable.** `as_of` is not a
   nullable timestamp that callers are trusted to check; the total is a sum type
   (`Dated(as_of) | Undated(reason)`), so rendering code cannot reach a date
   that does not exist and cannot forget to ask. This is the one contract worth
   spending a type on: it is the exact line commercial aggregators cross.
3a. **Accounts pending reconciliation (§8.5) contribute nothing** and are counted
   in `unreconciled_account_count`, which forces `is_complete = FALSE`. This is
   the one place the total is knowingly understated, and it is loud about it —
   the alternative is double-counting a replaced account, which looks like the
   owner suddenly got richer.
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
| macOS notification | `NEEDS_REAUTH`, `REVOKED`, **frozen data**, **publication overdue**, **read-back mismatch**, **accounts pending reconciliation**, **drain stalled** | `osascript -e 'display notification'` |
| `alert` table + in-app banner | everything above, persistent | DB row, travels in the payload; cleared on resolve |
| **Phone-local warning** | **rejected rollback / foreign `pairing_id`** (§9.3) | in-app, persistent until a newer `seq` arrives — **never reaches the Mac** |
| Agent mailbox | any Mac-side alert unresolved >24h | write to `~/agents/inbox/claude/new/` |

The split in that table is the point. Five alerts deserve a note, because they
are the ones a naive build would not have:

- **Frozen data** — an account whose `source_as_of` has not advanced across
  **five consecutive market days** *while its Item is `HEALTHY`*. This is Axis A
  green, Axis B dead: the original failure, caught by the only check that can
  see it. It is owner-actionable (usually a re-link fixes it), which is why the
  same condition is `ACTION_NEEDED` on screen (§9.2) — **this paragraph is the
  single definition of the threshold**; the display state and the alert both
  derive from it rather than each carrying their own number.
- **Publication overdue** — the last successful `publication` is older than the
  publish interval plus grace. The Mac is the only place this is visible; from
  the phone it is indistinguishable from being offline (§9.1).
- **Read-back mismatch** — the transport served back something other than what
  was just published (§9.3). This is the Mac-observable half of transport
  integrity.
- **Drain stalled** — a queued webhook older than an hour is still undrained
  (§8.4.2). Without it a broken drain is invisible until the TTL destroys the
  evidence.
- **Pending reconciliation** — accounts are sitting at `NEW` and contributing
  nothing (§8.5), so the total is knowingly understated until the owner confirms
  a mapping.

**Rejected rollback is deliberately not in the macOS row.** An earlier draft put
it there, promising the Mac an alert about an event only the phone can see, over
a channel the architecture does not have and should not grow (§9.3). It is a
phone-local warning, and the Mac's read-back covers the part the Mac can
actually observe.

The mailbox hop reuses infrastructure that already exists: the ticker wakes a
session on new mail, which can escalate and record it. It costs nothing to build.

Anti-fatigue: **one notification per item per state entry**, re-notified at most
once per 24h while unresolved, never for `DEGRADED` or for routine Axis-B
staleness inside its expectation window (UI only). Alerts auto-resolve on the
transition back to `HEALTHY` — and a frozen-data alert resolves only when
`source_as_of` actually advances, not when a call merely succeeds.

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
| webhook drain | every tick (cheap: one prefix `list`, usually empty) — §8.4.2 |
| health poll | >60 min since the last poll |
| full sync | no successful full sync since the most recent market close + 1h |
| quote refresh | any `MANUAL_QTY_LIVE_PRICE` price older than the last close |
| publish | a snapshot exists newer than the last successful `publication`; every publish is followed by a read-back (§9.3) |
| backup | >24h since the last verified backup — §14a. Refuses to run if the destination is on the database's own device (§14a.1) |

**Due-ness is computed from stored state, never from cron semantics.** A Mac
asleep for two days simply finds work due on wake and catches up; there is no
missed-fire concept to handle.

**Idempotency without mutation.** An earlier draft said a second sync the same
day "updates that day's snapshot", which contradicted the append-only claim in
§7 — an audit trail with an in-place update path is not an audit trail. The
resolution: **every successful run appends a snapshot**, and idempotency is keyed
on `sync_run_id` (UNIQUE), so a retried or crashed-and-resumed run cannot produce
a second row for the same run. Multiple snapshots per day are normal and true;
the daily curve is a view (last snapshot per day), and "current" is simply the
latest row. Nothing is ever edited in place.

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

## 14a. Durability: the tokens are as scarce as the slots

Two things in this system cannot be regenerated, and they fail differently:

- **The access tokens.** Plaid's own Trial guidance is explicit — "be sure to
  persist your access tokens and do not lose track of them" — because with
  **F2** a lost token cannot be replaced by re-linking for free. It strands a
  **permanent slot**. Losing `plaid-items.json` with four Items linked destroys
  40% of the project's lifetime budget, silently, at rest.
- **The history.** Snapshots are the one asset that is impossible to backfill.
  A year of curve cannot be reconstructed from anywhere.

An earlier draft filed backups under Phase 5 operations, scheduled *after*
Production linking. That ordering is backwards: the window in which a laptop
failure costs permanent slots opens the moment task 08 runs.

- **Backup:** one encrypted archive of the SQLite database plus the `TokenStore`
  contents, written outside the working tree, keyed from `~/agents/secrets/`.
- **It never goes to a third party.** Handing a provider a bundle of
  access-token ciphertext to hold indefinitely is a different risk class from a
  daily overwritten net-worth blob (§6.2), and it buys convenience only. The
  destination is hardware the owner controls.

### 14a.1 The gate has to survive the failure it exists for

*(From review, and the finding was exact: the draft above let the database, the
archive and `networth-backup.key` all sit on one disk, then called a restore
into a temp directory "verified". That drill proves the archive parses. It
proves nothing about the scenario the section is named after — the Mac dies —
because in that scenario all three copies died together. A backup that only
survives `rm` is not a backup; it is a copy.)*

Three acceptance criteria, all owner-controlled and all free:

1. **A destination in a separate failure domain.** `backup_destination` must
   resolve to storage that is not the volume holding `~/networth-data/` — an
   external disk, a Time Machine target, or another machine over Tailscale. The
   check is mechanical and runs on every backup, not once at setup: compare the
   device id of the destination against the database's (`stat -f %d`), or
   confirm the destination is remote. Same device → **the backup fails loudly**
   and the gate stays shut. A misconfiguration that silently degrades to a
   same-disk copy is precisely how people discover they had no backups.
2. **A recoverable copy of the backup key that is not only on that disk.**
   `networth-backup.key` decrypts the archive; the two must not share a fate.
   The owner puts it in a password manager or on paper (it is one line), and
   confirms with `networth backup attest-key`, which records
   `key_escrow_confirmed_at` and *nothing else*. This is an **attestation, not a
   proof** — no agent can verify a password manager, and pretending otherwise
   would be its own dishonesty. `doctor` shows it, with its date, as the
   owner's own claim.
3. **The drill restores from the destination.** `scripts/restore-drill.sh` pulls
   the archive **back from `backup_destination`** — over the same path a real
   recovery would use, which is the part that actually gets tested — decrypts it
   with the key resolved from `~/agents/secrets/`, restores into a temp
   directory, checks row counts and schema version, and verifies the
   `TokenStore` yields the same **token fingerprints** (salted hashes — never
   the tokens, never in a log). It records `last_verified_restore_at`, runs
   weekly, and `doctor` reports its age.

**Gate:** all three must hold before task 08 links the first Production Item. A
hard dependency in the task graph, not a recommendation.

**If the owner has no second device**, the gate cannot be met and the honest
consequence is not a workaround — it is a decision: linking Production Items
while a single disk failure can strand permanent Item slots (**F2** + **F6**).
That is the owner's call to make explicitly (**O8**), not ours to route around
by weakening the check.

---

## 15. Secrets and what may never be committed

The repository is **public**. Repo visibility was never the real control — the
separation between code and credentials is. These rules are also in `AGENTS.md`,
which binds both agents.

- `~/agents/secrets/plaid.env` — `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`.
- `~/agents/secrets/plaid-items.json` — `{item_id: access_token}`, mode 600.
- `~/agents/secrets/networth-transport.env` — the transport **write** token, the
  transport **read** token issued to a pairing, and the payload key. Two tokens,
  not one (§6.2); the phone never receives the write token.
- `~/agents/secrets/networth-backup.key` — the backup archive key (§14a).
- Quotes key: already present, reused.
- Android signing keystore + `key.properties`: outside the repo (§17).

**Not in this list, deliberately: anything on the phone.** Since §6.3 the app
holds its key and read token in the Android Keystore, provisioned by pairing —
so there is no build-time secret to manage, no `--dart-define` to leak into CI
logs, and the release APK is not a bearer artifact.

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
5. **No institution-specific detail in the repo** (§2 reservation 1) — this
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

**Transport side: one Cloudflare Worker** (~120 lines of JavaScript, deployed
with `wrangler`):

| Route | Auth | Purpose |
|---|---|---|
| `PUT /snapshot` | write token | publish ciphertext |
| `GET /snapshot` | read token (verifier-checked, §6.3.1) **or** write token | the phone reads; the Mac reads back (§9.3) |
| `POST /pairing/rotate` | write token | install a new pairing verifier + drop the stored snapshot |
| `POST /pairing/revoke` | write token | lock out the current pairing + drop the stored snapshot |
| `POST /hook/<unguessable>` | none (Plaid cannot present ours) | size-capped; writes one unique KV key per event (§8.4.2) |
| `GET /hook/queue`, `DELETE /hook/queue/:key` | write token | the Mac drains and acks |

It holds **no Plaid credential**, no read token (only a hash of one) and no
payload key, and performs no verification of Plaid's signature (§8.4) — it is a
dumb, replaceable relay, which is what keeps the `Publisher` swap to Tailscale
cheap. It grew from three routes to six in review; every addition is a control
path that was previously *assumed to exist*, which is why the count went up
while the trust placed in the Worker went down.

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
- **No secrets are injected at build time.** The payload key and the read token
  arrive by runtime pairing (§6.3) and live in the Android Keystore, so the APK
  carries nothing sensitive, rotation needs no rebuild, and a `--dart-define`
  can never end up in a CI log. The signing keystore is the only secret the
  *build* touches, and it stays outside the repo.

---

## 18. Open questions

| # | Question | Owner of the answer | Blocks |
|---|---|---|---|
| O2 | Does the Trial plan actually reach the in-scope brokerages via OAuth? (**F4** — go/no-go) | owner, via dashboard | **the Production-Link path: tasks 07, 07a, 08 and everything downstream of a real Item** (09, 12b, 26) — see below |
| O3 | How many distinct card-issuer logins? | owner | Item budget sizing |
| O4 | Real property: purchase price only, or a revision log? (recommend: revision log — nearly free) | owner | task 13 |
| O5 | Transport: **Cloudflare Workers + KV** (recommended — current value only, works while the Mac sleeps) or **Tailscale** (no third party at all, but the Mac must be awake)? | owner | tasks 20, 24 |
| O6 | Android only, or iOS too? iOS has no sideloading story, which changes delivery entirely | owner | tasks 21, 24 |
| O7 | Create a free Cloudflare account? It is the one new account this design adds, and it disappears if O5 picks Tailscale | owner | tasks 20, 12a |
| O8 | **Where do backups land?** A destination in a separate failure domain is a gate on the first Production Link (§14a.1). External disk / Time Machine volume / another machine over Tailscale — or an explicit decision to link Items without one | owner | tasks 03a, and through it 08 |

*(O1 — phone vs Mac/browser — was answered by the owner: **Flutter phone app**.)*

**O2 blocks the Plaid path, not the project.** *(Narrowed in review, which
caught this table saying "all implementation" while the task graph and the
recommended plan both started six tasks before O2 could possibly be answered.
Two answers in one merged document is worse than either answer.)* The
foundation — schema, `Store`, `TokenStore`, the Plaid client wrapper, the
backup gate, Sandbox rehearsal — touches no Production Item and no institution,
and survives a `NO` intact: a `NO` changes *how accounts get linked*, not the
staleness machine, the snapshot model, the manual-asset path or the transport.
That is the actual reason it is safe to start, and it is why the graph and this
table now agree. Note that Sandbox work still needs the dashboard **account**
(task 00), which is a different dependency from O2's answer.

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

**Step 1a — Say where backups land** (~5 min, once; **before** Step 2, and the
ordering is the whole point — §14a.1)
1. Pick storage that will not die with the Mac: an external disk, the Time
   Machine volume, or another machine over Tailscale. Set `backup_destination`.
   The backup refuses to run if it resolves to the same device as the database.
2. Copy `~/agents/secrets/networth-backup.key` into a password manager or write
   it down, then run `networth backup attest-key`. It records only the date of
   your confirmation. Without this, the archive and its key die together.
3. Run `scripts/restore-drill.sh` and see it pass. It pulls the archive back
   **from the destination**, so this is the first moment a real recovery has
   actually been exercised.
4. **Do not proceed to Step 2 until it passes.** After the first Production Link,
   losing the tokens does not cost a re-link — it strands permanent Item slots
   (**F2**, **F6**).

**Step 2 — Link each institution** (~1 min each, once per institution)
1. Run `scripts/link.sh` (built by agents, run by the owner).
2. It opens Link in the browser. **Enter credentials and MFA there** — that page
   is Plaid's; nothing on this machine sees them.
3. Paste the returned `public_token` into the waiting prompt. The script
   exchanges it, writes the `access_token` via `TokenStore` (mode 600), and
   records the item.
4. Link the **highest-value institutions first** — slots are permanent (**F2**).

**Step 3 — Stand up the transport and pair the phone** (~10 min, once; skip
entirely if **O5** chooses Tailscale)
1. Create a free Cloudflare account (**O7**) and run the provided `wrangler`
   deploy. Agents can write the Worker; only the owner creates the account.
2. Run `networth pair`. It registers the new pairing with the Worker **first**
   and prints the QR code only once that succeeds (§6.3.1) — so a QR on screen
   always means a phone that will work.
3. Open the app and scan it. That is the whole provisioning step — nothing
   secret was ever compiled into the APK (§6.3).
4. Re-run `networth pair` any time to rotate: the previous phone stops reading
   **immediately**, before the new one has scanned anything, and the stored
   snapshot is deleted so the old key has nothing left to decrypt. No rebuild.
5. **Phone lost or stolen:** run `networth revoke`. Same lockout, no replacement
   phone needed. The Mac keeps publishing; nobody can read.

**Ongoing — when an alert fires:**

- `NEEDS_REAUTH` → run `scripts/relink.sh <item>`: Link in **update mode** for
  that Item. ~30 seconds, consumes no slot.
- `REVOKED` → the only case that spends a permanent slot, and only with the
  owner's explicit go-ahead. After the new Link, run `scripts/reconcile.sh`,
  check the proposed old→new account mapping and confirm it. Until that
  confirmation the new accounts contribute nothing and the total is openly
  marked incomplete (§8.5).
- **Frozen data** → an institution is answering but has not updated for five
  market days (§11), which is also when the app promotes it to `ACTION_NEEDED`
  (§9.2) — the screen and this page are describing the same threshold. Usually a
  re-link in update mode clears it; if it does not, the account is a candidate
  for the manual path (§12) rather than a permanent lie.
- **Read-back mismatch / drain stalled** → transport faults, not account faults.
  Neither changes the number; both mean a signal you rely on is degraded. Check
  `networth doctor` first.

---

## 20. Task breakdown

See [`tasks/README.md`](tasks/README.md). Tasks are drafted but **deliberately
unassigned** — assignment is itself subject to review.
