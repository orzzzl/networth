# DESIGN — networth

Status: **proposed** (design phase; nothing implemented).
Author: Claude. Reviewer: Codex.

Revision 10 — **not review-driven, and mostly a deletion.** The owner answered
five open questions in one sitting, and one of the answers moved the host. Every
finding open against rev 7 is **void by removal**, not by fix — the distinction
matters, and §6.2 and §9.3 state it where the mechanisms used to be.

- **O5 answered, and it changed more than the transport.** The owner has an
  **always-on VPS he already pays for** (Ubuntu, public IPv4, already his
  Tailscale exit node), so the daemon moves off the Mac and onto it. Both
  drawbacks the Tailscale option carried through rev 9 were **Mac** drawbacks and
  both are void at once: "the Mac must be awake" (a VPS is), and "Plaid webhooks
  are impossible" (it has a public IP, so §8.4 gets its accelerator back, and the
  JWT is verified on the machine that already holds the Plaid secret).
- **The third-party transport branch is deleted, not resolved.** With it go the
  write/read token pair, the publish/read-back cycle, the pre-write compare and
  its `ROLLBACK`/`FOREIGN`/`ABSENT` classifier, the publication outcome state
  machine, `payload_fingerprint`, the provider-retention analysis, the
  control-plane credential lifecycle and the longest procedure in the runbook.
  **Roughly a third of this document was about a risk the owner's hardware does
  not have.** What survives from all of it is `seq` and **I6** — the phone
  refusing a downgrade — which was never about the transport (§9.3).
- **Alerts are in-app on the phone only** (the owner declined email and the
  mailbox route). On a headless host that is a real design consequence, not a
  configuration: alert state has to travel **inside the payload**, and §11 now
  states the thing that is easy to leave implicit — *there is no way to reach the
  owner; an alert is seen when he opens the app.* One listed alert has a hole
  because of it, and §11 names the hole rather than letting a reader assume the
  alert arrives.
- **O4 answered: a revision log for real property**, and the load-bearing half is
  that **a revision applies from its own date forward** — the curve behind it must
  not deform when an estimate changes (§12).
- **O8 answered: VPS → Mac over the tailnet, and framed around the tokens.** The
  owner asked whether history could be back-filled instead. It cannot (Plaid has
  no historical endpoint; reconstruction has no cash history and needs mutual-fund
  prices the quotes integration does not cover) — but the more useful answer is
  that the curve is not what is at risk. **A lost `access_token` cannot be
  recovered at all** and strands a lifetime Item slot (§14a).
- **No Schwab OAuth request** (§18): the manual path needs no Item and no
  six-week wait, so the request branch is removed rather than deferred.
- **Co-location is accepted, and recorded.** The VPS is now his VPN exit node
  *and* the holder of the Plaid master credential. He was told and accepted;
  §15.1 states the concentration, the hardening that is part of the deploy, and
  the residual — **this design does not defend against compromise of the host**,
  and no check running on the host could honestly claim otherwise.

*Revisions 4–9 were largely a post-mortem of the third-party transport branch:
its retention window, its atomicity, its credential lifecycle, and three
consecutive rounds of defects in the mechanisms built to police it. Those
mechanisms no longer exist, so the entries are compressed here rather than
preserved in full — a detailed account of a deleted subsystem at the top of a
document that no longer contains it sends every reader looking for sections that
are gone. **The findings themselves are not disowned; they were right, and they
are why the branch was expensive enough to be worth deleting when a cheaper host
appeared.** They remain in full in git history and on PR #1.*

- **Rev 9** (not review-driven): O6 answered — **Android only**; **credit cards
  deferred**, so v0 is assets-only and the Item reserve doubles from 2 to 4; and
  §1 gained its opening line that **there is exactly one deliverable**, after the
  owner's reading of the progress reporting revealed a document a reader could
  come away from believing two apps were being built.
- **Revs 5–8** (review-driven, one round each): the transport's retention window
  was 30 days and had been described as erasure; a "restore is not silent" claim
  that was false as written; a "this machine never holds an account credential"
  claim the runbook contradicted; a pre-write check that classified ordinary
  operation as an attack; a credential bracket that closed one of two doors; an
  audit model with no state for an attempt proven not to have landed; and an
  object fingerprint that did not cover what the cipher authenticates. The
  pattern across them is the one worth carrying forward and is **not** deleted
  with the branch: *each round's defect was in what the previous round added
  while fixing the round before it.*
- **Rev 4**: five mechanisms that could not be built as specified — including a
  backup gate that compared filesystems rather than physical stores, and a sync
  predicate that was market-driven only, so a Friday success satisfied it all
  weekend. Both of those findings are still live in §14a.1 and §13.

Revision 3 — reworked after Codex's second review requested changes on
`da53ea7`. Its six findings were all cross-section contradictions: places where
two sections were individually defensible and jointly impossible. Answered in
§8.1/§10 (aggregate age is now a tagged state), §14a (the backup gate must
survive losing the host), §6.3 (the transport learns about a pairing), §9.1/§9.3/
§11 (what the phone can and cannot cause), §8.4 (the webhook queue's actual
mechanics), and §9.2/§18 (two statements that disagreed with their consumers).
One further contradiction was found while fixing the first and is fixed with it:
a fixed-value asset's purchase-date clock would have dominated the headline age
forever (§8.1, R3). **Kept in full because five of the six are still live** —
only the queue mechanics went with the transport.

Revision 2 — reworked after Codex requested changes on `02c9126`. Its seven
findings are answered in §1 (I5/I6), §2, §4 (F5/F6), §6, §7, §8.1/§8.4/§8.5, §9,
§13, §14a and the task graph.

In every round, each place the earlier draft was wrong is called out inline
rather than quietly corrected.

---

## 1. What this is

**There is exactly one deliverable: an Android app.** It is the only thing the
owner installs, opens or looks at. The **VPS** this document then talks about on
nearly every page is **not** a second app — it is a headless daemon on a server
he already owns, with no window, no icon and no interface, which he never opens
and never touches again after the one-time setup in §19.

*(Stated first, before any architecture, because the progress reporting on this
project left the owner believing two apps were being built. That was a reporting
failure, but a design a reader can come away from with the same impression has
the same defect — so this line is load-bearing, not a preamble. **One app.**
Rev 10 moved the daemon from the Mac to the VPS, which does not change this
sentence at all — and that is the test it was written to pass.)*

One number — total net worth — rebuilt at least once a day from linked financial
accounts plus a few manually-valued assets, and displayed on that phone. Single
user, one host, zero marginal cost.

**v0 is assets only.** Credit cards are **deferred** by owner decision
(2026-08-30), so the headline is a sum rather than a difference; §10 keeps the
one place a liability would re-enter, and §14 counts the Item slots that frees.

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
- **Credit cards are deferred, not rejected** *(owner, 2026-08-30 —
  "信用卡先不做了")*. They were a row in this table through rev 8, subtracted as
  debt. v0 drops the Plaid **Liabilities** product and every credit-account path
  from the model, the sync engine and the UI. What is deliberately *kept* is the
  one thing that would be expensive to retrofit: `account.sign` stays in the
  schema and nothing anywhere assumes an account's value is positive (§7, §10).
  Adding cards back is then a link flow and a UI row, not a migration of history.
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
Balance, Identity, Assets, Liabilities, **Investments**, Statements. Every
product this project needs is included — since rev 9 that is **Investments plus
account balances**; Liabilities is bundled too and simply goes unused while
cards are deferred, so bringing them back costs no plan change.

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
an OAuth institution, so this is a **go/no-go for the Plaid Production-Link
path** — tasks 07 and 08 and everything downstream of a real Item — and only the
owner's dashboard can settle it. It is **not** a gate on the project: the
foundation (schema, `Store`, `TokenStore`, the Plaid client wrapper, the backup
gate, Sandbox rehearsal) survives a `NO` intact, because a `NO` changes how
accounts get linked, not the staleness machine, the snapshot model, the
manual-asset path or the transport. Recorded as a gate (§18, O2), not asserted.

**F5 — Real-time balance is bundled on Trial, and it is the only way a cash
balance's age can be known at all.** *(From review.)* Plaid's accounts docs
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

Everything else is free by construction: SQLite on disk, a systemd timer on a
server the owner already pays for and already runs 24/7, a quotes key the owner already holds, and a transport
chosen in §6 specifically for having no bill attached.

---

## 5. Architecture

The owner has decided the UI is a **Flutter Android app**, and — as of rev 10 —
that the sync runs on a **VPS he already pays for** rather than on this Mac.
Those two decisions between them fix the shape of the system: sync and display
live in different places, so the data has to *travel*, and the host that syncs
is a machine with no screen.

```
   ┌──── the VPS (Ubuntu, always on, public IPv4, on the tailnet) ────┐
   │  systemd timer (§13)                                            │
   │    SyncEngine → StalenessMachine → Snapshotter                  │
   │    PlaidClient (holds client_id/secret + access_tokens)          │
   │    WebhookReceiver: public HTTPS endpoint, JWT verified HERE     │
   │    SQLite: full history, append-only                            │
   │    Snapshot server: GET /snapshot, bound to the TAILNET only    │
   └───────┬──────────────────────────────────────────┬───────────────┘
           │ tailnet (WireGuard)                      │  nightly rsync
           │ + payload encrypted anyway (§6.1)        │  over the tailnet
   ┌───────▼──────────────────────────────────────┐   │   (§14a — the
   │  Flutter app: fetch → decrypt → cache →      │   │    tokens, not
   │  display, secrets from one-time pairing      │   │    the curve)
   │  shows BOTH staleness dimensions (§9)        │   ▼
   │  AND is the only place alerts appear (§11)   │  the Mac: backup target
   └──────────────────────────────────────────────┘  + where Link is run once
```

The VPS keeps every credential and the full history. The phone is a
**read-only display of an authenticated, encrypted snapshot** — it never holds a
Plaid token, never calls Plaid, and cannot mutate anything. That asymmetry is
what makes the phone safe to lose. The Mac is no longer a component: it is where
the owner runs Plaid Link once per institution (§19) and where the backup lands
(§14a). Nothing runs there on a schedule.

**What moving the host bought, and what it cost.** *(Rev 10. The VPS was not on
the table until the owner mentioned he already had one, and it collapses most of
this document's hardest section.)*

| Was a problem | Now |
|---|---|
| The Mac sleeps, so a daily guarantee needed a third-party transport that serves while it is asleep | The VPS is always awake. The phone fetches straight from it |
| Plaid webhooks need a public endpoint the Mac does not have | The VPS has a public IPv4. Plaid POSTs directly, and the JWT is verified on the machine that already holds the Plaid secret |
| A third party stored the payload — bounded retention, provider restores, a control plane, two credential tiers, a whole rollback-detection apparatus | **No third party at all.** The daemon serves its own SQLite over the tailnet |
| **New:** co-location risk | The VPS is also the owner's VPN exit node, and now also holds the Plaid master credential. **The owner was told and accepted it** (§15) |

Seams (interfaces the rest of the code depends on, never concrete classes):

- `PlaidClient` — link tokens, exchange, item status, holdings, balances. The
  only place Plaid's error taxonomy becomes our states. *(Liabilities dropped in
  rev 9 with credit cards; the seam is where it would return.)*
- `QuoteClient` — `get_quote(symbol) -> (price, as_of)`; the quote must carry its
  own timestamp, because a stale price is precisely the failure being hunted.
- `TokenStore` — narrow interface over secret storage (§2 reservation 3).
- `Store` — repositories over SQLite; append-only observations/snapshots.
- `Publisher` — serialize + encrypt the snapshot and make it the object the
  snapshot server hands out. On this host that is a **local transaction**, not an
  upload: there is no remote object, no write credential, and nothing to read
  back. *(Rev 10 deleted the upload, the read-back, the pre-write compare and
  their audit states along with the third party they existed to police. The seam
  survives so a future transport is a swap rather than a rewrite.)*
- `WebhookReceiver` — the public endpoint Plaid POSTs to; **verifies Plaid's JWT
  locally** and converts events to item state changes (§8.4). Advisory *for the
  number* — a dropped event can never make the total wrong, because the poll
  floor is what I3 rests on. It is **not** redundant with polling:
  `PENDING_DISCONNECT`'s `reason` and `disconnect_time` are advance warning no
  poll can derive.
- `Notifier` — alert delivery. On a headless host that means **into the payload**
  and nowhere else (§11).
- `BackupStore` — encrypted archive of the database + token material, pushed to
  **the Mac over the tailnet** (§14a): a different machine, a different provider,
  a different physical failure domain.

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

### Option 2 — one always-on host syncs; the phone displays. **Chosen.**

All credentials stay on the syncing host; the phone gets an authenticated,
encrypted snapshot. Through rev 9 that host was the Mac, and because a Mac
sleeps, this option dragged a third-party transport in behind it — something had
to serve the phone while the Mac was shut. **Rev 10 removes that constraint
instead of solving it:** the host is the owner's existing always-on VPS, so the
host *is* the transport, reachable over his tailnet.

The requirement that killed the simple answers is unchanged: a public static
host is not acceptable, because a plaintext net-worth JSON at an unguessable URL
is still a leak, and unguessable URLs leak through caches, logs and history.

#### 6.1 Encrypt the payload regardless of transport

The daemon encrypts the snapshot with a symmetric key (AES-256-GCM); the phone
holds the key. **This is kept even though rev 10 removed the third party it was
introduced to defend against**, and the reason is worth stating rather than
leaving as inertia: the snapshot leaves the host over a network to a device that
can be lost, the ciphertext is what makes `networth revoke` mean anything, and
the cost is one key and one nonce per day. A design that encrypts only when it
can name the eavesdropper has to be re-argued every time the topology changes —
this one has changed three times already.

A fresh random 96-bit nonce per publication, and the authenticated-data field
binds `schema_version`, `pairing_id`, `seq` and `published_at` so none of them
can be swapped between valid ciphertexts. One publication a day is nowhere near
any nonce-reuse boundary.

**The envelope, and the exact bytes of the AAD.** *(Specified in rev 8. The
phone has to rebuild the AAD to decrypt at all, so "binds these four fields" is
not enough — the encoding has to be one both ends compute the same way. Rev 8
had a second consumer, a fingerprint that identified the object at the third
party; that is gone with the third party, and the encoding still has to be
pinned for the first reason.)* The published object is:

```json
{ "schema_version": "1", "pairing_id": "<uuid>", "seq": "137",
  "published_at": "2026-08-30T09:00:00Z",
  "nonce": "<base64url>", "payload": "<base64url of ciphertext ‖ GCM tag>" }
```

with the four authenticated fields carried **in the clear** — they are inputs to
decryption, not secrets — and `seq` as a **decimal string**, so no JSON number
coercion sits between the two ends. `published_at` appears twice, once here and
once inside the plaintext, and **the phone reads the plaintext copy** (§9.1):
the header copy exists only so decryption is possible at all, and a party
without the key cannot change it without breaking the tag. Sealing the two apart
requires the key, i.e. the host itself — the boundary stated below. The AAD is
the length-delimited tuple

```
LP(x) = uint64_be(byte_length(x)) ‖ x        -- length-delimited: no field boundary
                                             --   is ambiguous, no field can absorb
                                             --   another by containing a separator
aad   = LP(schema_version) ‖ LP(pairing_id) ‖ LP(seq) ‖ LP(published_at)
```

over the UTF-8 bytes of each field **as they appear in the envelope**. Nothing
re-serializes the object between the two ends — the daemon builds it and the
phone parses it — but nothing above depends on that either: the AAD is rebuilt
from parsed values, so a re-encoded envelope would still decrypt.

**What encryption does not buy: currency.** A valid ciphertext stays valid
forever, so an old payload replayed to the phone decrypts perfectly — an
authentic, stale number, which is precisely this product's cardinal sin wearing
the cipher's own authenticity tag. That gap is closed by `seq` and **I6** in
§9.3, not by the cipher. *(Rev 10 note: through rev 9 this was a much larger
worry, because a third party held the object and anyone able to write there
could replay it. The snapshot is now served by the process that produced it, out
of the database that holds the history, so the replay position an attacker would
need is **inside the host that holds every Plaid token** — at which point the
payload is the least of the losses. §9.3 keeps the phone-side check anyway: it
costs one comparison and it is the only thing standing between a cached copy and
a downgrade.)*

**And what the tag does not buy: authorship.** GCM is **symmetric** — one key
seals and opens — so a valid tag proves the writer held the payload key, nothing
more. Since the only two holders of that key are the daemon and the paired
phone, and the phone has no route to write anywhere, "the writer held the key"
and "the daemon wrote it" now differ only if the host itself is compromised.
That is the boundary, stated once: **every freshness claim in this document is
scoped to an uncompromised host.** The alternative — an asymmetric signing key
whose private half never leaves the daemon, the phone holding only the public
half — is named and not built, because on a single-host design it defends
against nothing the host's own compromise does not already give away.

#### 6.2 The transport: the tailnet, and why the alternatives lost

**O5 is answered: Tailscale, with the daemon on the owner's VPS.** The phone
fetches `GET /snapshot` from a server bound to the VPS's **tailnet interface**;
nothing about this system is reachable from the public internet except the
webhook endpoint Plaid needs (§8.4). This section keeps the comparison that led
here, because the criterion it produced is the reusable part.

**The criterion: what does the transport *retain*?** *(From review, which
rejected an earlier draft's private GitHub repo.)* For a payload published every
day under one long-lived key, what matters most is not who can read the current
value, but **how many past values the transport keeps around to be read later**.

A Git-backed transport is the worst case on exactly that axis. Writes through the
Contents API create commits, and replacing or deleting a file does not remove the
earlier blobs. A year of daily publications is a year of retrievable ciphertexts
under one key, so a single leak of the phone's key plus its read credential is
not a disclosure of *today's* net worth — it is the entire history,
retroactively. **This project has first-hand proof:** during the design phase a
superseded commit was rewritten out of this repository's branch and remains
fetchable by direct SHA. Rotating the key later would be cosmetic.

| Transport | Auth | Free? | Serves while the *Mac* sleeps? | **What it retains** | Verdict |
|---|---|---|---|---|---|
| **Tailnet-bound endpoint on the owner's VPS** | Tailnet device identity, **plus** the payload key, which *is* the read credential (§6.3) | The VPS is an existing subscription; the tailnet is the personal tier | **Yes** — the VPS never sleeps, and the Mac is not in the path at all | **Nothing at any third party.** The daemon serves its own current snapshot out of its own SQLite | **Chosen (O5)** |
| Cloudflare Worker + Durable Object | Two bearer tokens (write / read) | Free tier, comfortably | Yes | Application state: the current value only — but a **30-day provider-side point-in-time recovery window**, on by default with no opt-out | **Deleted in rev 10.** It existed to cover a sleeping Mac; with an always-on host it buys nothing and costs a third party, an account, a control plane and a rollback-detection apparatus |
| Private GitHub repo (`…-data`) | Fine-grained read-only PAT | Yes | Yes | **Every payload ever published**, permanently — readable with the same credential the phone carries | **Rejected** — see above |
| Public static host (Pages, etc.) | None | Yes | Yes | — | **Rejected** — publishes net worth |
| ntfy / public pubsub free tiers | None or weak | Varies | Yes | — | **Rejected** — no real auth |

**What the VPS answer removes, and it is a lot.** Every drawback the tailnet
option carried through rev 9 was a *Mac* drawback:

- "the Mac must be awake" — the VPS is always awake;
- "Plaid webhooks become impossible, because there is no public endpoint" — the
  VPS has a public IPv4, so §8.4 gets its accelerator back, verified on the
  machine that already holds the Plaid secret.

With the third party gone, so is everything that existed to police it: the write
token and read token pair, the publish/read-back cycle, the pre-write compare and
its `ROLLBACK`/`FOREIGN`/`ABSENT` classifier, the publication audit states, the
provider-retention analysis, and the control-plane credential lifecycle around
`wrangler`. **Roughly a third of this document was about a risk the owner's
hardware simply does not have.** The three blockers open against rev 7 were all
in that third; they are void by deletion rather than by fix, which is worth
saying plainly because "we removed the mechanism" and "we fixed the mechanism"
are very different claims.

**Blast radius, stated plainly.** If the phone is compromised, the attacker gets
the current payload and the phone's local cache — which does contain the history
window the curve renders, because the curve has to come from somewhere. What
they do *not* get: the ability to publish anything (there is no write route),
any Plaid token, the full history, or a way back into the tailnet beyond that
device. Recovery is `networth revoke` plus removing the device in the Tailscale
admin console (§6.3), and unlike the Cloudflare branch **nothing retained
anywhere else survives it** — there is no third party holding a copy.

**The one thing this costs, stated rather than buried:** the whole system now
sits on one VPS that is also the owner's VPN exit node. Co-location was put to
him explicitly and accepted (§15); the mitigations are ordinary host hardening
and the fact that a lost VPS is a *recoverable* event only because §14a backs
the token set up to the Mac.

#### 6.3 Provisioning the phone's secrets: pairing, not compilation

An earlier draft injected the payload key and the transport credential into the
APK at build time. Review rejected that too, and correctly: it makes the APK
itself a bearer artifact for the owner's net worth, makes rotation require a
rebuild-and-reinstall, and bypasses the platform's protected secret storage.

Instead, **the app ships with no secrets at all** and is provisioned once at
runtime:

1. `networth pair`, run on the VPS over SSH, mints a fresh payload key and a
   `pairing_id` and renders them as a QR code **in the terminal** (with a typed
   fallback string). The QR carries the key, the `pairing_id` and the VPS's
   tailnet name — and nothing else. *(Rev 10: through rev 9 it also carried a
   read-only transport token, because a third party had to be told who was
   allowed to read. There is no third party now, so there is no token to issue:
   see "authentication is two layers" below.)*
2. The phone scans it once, on-screen, on the owner's own desk — the material
   never crosses a network during pairing.
3. The app stores it via `flutter_secure_storage`, backed by the **Android
   Keystore**, so the OS protects it rather than a string constant in a DEX file.
4. Rotation, revocation and re-pairing are runtime operations. No rebuild, no
   reinstall, no version bump.

The release APK is therefore not a bearer artifact: losing it leaks nothing.

#### 6.3.1 Serving, rotation and revocation — one process, one database

*(Rev 10 replaced the section that stood here. Through rev 9 this was the
hardest mechanism in the document: a pairing verifier and a snapshot living at a
third party, which had to be replaced atomically by an HTTP call whose outcome
the Mac could fail to learn — hence a `PENDING`/`ACTIVE`/`UNCERTAIN` state
machine, a suspend-publishing rule and a whole class of alerts. **None of that
survives the host move, because none of it was about pairing.** It was about
doing a transaction across a network. On one host it is a local transaction.)*

- **Serving.** `Publisher` encrypts exactly as in §6.1 and stores the envelope in
  the daemon's own SQLite. A small HTTP server bound to the VPS's **tailnet
  interface** serves `GET /snapshot` — the current envelope for the `ACTIVE`
  pairing, or `404` when there is none. The phone fetches it over WireGuard.
  Nothing leaves the host except that one response, to that one tailnet.
- **No TLS requirement, and therefore no certificate to expire.** The tailnet
  link is already end-to-end encrypted and the payload is encrypted underneath
  it. `tailscale serve` can front the port with a tailnet certificate and
  identity headers if the owner has that enabled — a strict improvement, never a
  dependency, so nothing here rests on which Tailscale features are on.
- **Authentication is two layers, neither of them a bearer token.** Tailnet
  membership decides who can reach the port; **the payload key is the read
  credential** — a tailnet device that never scanned the QR receives ciphertext
  it cannot decrypt. So there is no read token, no stored verifier and no hash
  comparison anywhere: there is nothing to present and nothing to leak.
- **Rotation and revocation are one SQLite transaction** — new pairing `ACTIVE`,
  old `revoked_at`, stored envelope dropped. One process, one database, one
  writer. **`networth revoke` is immediate in the literal sense**, and there is
  no `UNCERTAIN` state to design around because there is no network call whose
  outcome the daemon can fail to learn.
- **Lost phone.** `networth revoke`, then — recommended, owner-only — remove the
  device in the Tailscale admin console, which is the stronger control because it
  revokes *reachability* rather than content. The ciphertext already cached **on**
  the stolen phone is beyond recall either way: revocation stops the next fetch,
  never the last one. What is genuinely gone is everything else — no third party
  is holding a copy, and the dropped envelope is dropped.
- **`seq` and replay defence** are the phone's, and are all that is left of the
  old apparatus (§9.3): same AAD, same monotonic counter, same phone-side
  refusal.

#### 6.4 Publication-freshness monitoring

A publish path that dies silently would freeze the phone's number — this
product's cardinal sin arriving through the back door — so it is monitored
rather than assumed.

The mechanism is **evidence, not arithmetic**: the `publication` table (§7)
records every attempt, and `doctor` plus the alert path fire on *"the last
successful publication is older than expected"*. One check catches a crashed
daemon, a full disk, a database that will not open and a serving process that is
not listening — instead of enumerating the failures in advance.

*(Rev 10: this section used to be about a **transport credential** expiring, and
that reading is gone with the credential. What remains is the check, which was
always the load-bearing part — it never parsed an expiry date, it looked at
whether a publication had actually happened. On the new host it is doing more
work than before, because it is now the **only** publication-side check: the
read-back and pre-write compare that used to sit either side of the upload were
policing a third party, and there is no longer a party to police. A local write
that returns is a local write that happened; re-reading the row you just wrote
to confirm SQLite wrote it tests nothing but SQLite.)*

### Option 3 — considered and dismissed

Manual transfer (AirDrop/Files/iCloud) breaks the automatic daily guarantee. A
self-hosted server contradicts "no 24/7 server" and costs money. Push
notification as transport (rather than as an alert) has payload limits and no
delivery guarantee.

**Running the whole sync as serverless functions on a cron trigger — dismissed,
and recorded so it is not proposed again.** *(Checked in rev 9, when the question
was whether the Mac could be eliminated entirely. Rev 10 answered that question a
different way — the owner already had a machine — but the finding is worth
keeping, because "just run it serverless" is the answer someone will propose
again the first time the VPS has a bad day.)* The concrete free tier checked was
Cloudflare Workers, and it fails for two independent reasons: each invocation is
capped at **10 ms of CPU** — network wait does not count, but parsing a holdings
response and doing AES-GCM over the payload is real CPU — and **failed scheduled
invocations are not retried**, which breaks the once-a-day guarantee (§1)
outright rather than degrading it. The paid tier costs money, so the zero-spend
rule (§4) ends the discussion before the technical one starts. And it would put
the Plaid `client_id`/`secret` — the master credential, not a per-Item token —
at a third party. The daily guarantee needs a host that is awake, allowed to be
slow, and holding its own credentials; that is what §13 assumes.

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
  sign,                                -- +1 asset, -1 liability. v0 writes only +1
                                       --   (cards deferred, rev 9), and the column stays
                                       --   so nothing downstream may assume "positive"
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
  total_net_worth_minor, total_assets_minor,
  total_liabilities_minor,             -- always 0 in v0: no liability account can exist
                                       --   while cards are deferred. Kept so the curve
                                       --   does not need rewriting when they return
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

pairing(id, created_at, key_ref,                   -- a ref, never the material
        state,                                     -- ACTIVE | REVOKED. Two states, because
                                                   --   rotation is one local transaction
                                                   --   (§6.3.1). Rev 9 also carried PENDING
                                                   --   and UNCERTAIN, which existed only to
                                                   --   survive a network call to a third
                                                   --   party mid-rotation
        revoked_at)

publication(                                       -- §6.4 evidence: did the phone's copy get built?
  id, snapshot_id, pairing_id, seq UNIQUE,         -- monotonic, NEVER reset across pairings;
                                                   --   replay defence (I6, §9.3)
  schema_version, published_at,
  ok, error)                                       -- a local transaction either committed or
                                                   --   raised. Rev 9 carried an outcome state
                                                   --   machine, a payload fingerprint, and
                                                   --   pre-write/read-back columns; all four
                                                   --   existed to detect a third party serving
                                                   --   something other than what was uploaded.
                                                   --   The daemon now serves its own rows (§6.4)

webhook_event(                                     -- §8.4; advisory input, never a dependency
  id,
  received_at,                                     -- this host's clock when the HTTPS request
                                                   --   arrived — and, because the receiver
                                                   --   verifies inline, also when `iat` is
                                                   --   checked. Rev 9 needed a separate drain
                                                   --   time; there is no queue now (§8.4)
  processed_at,
  verified,                                        -- Plaid JWT checked HERE: this host already
                                                   --   holds client_id/secret
  body_sha256, jwt_iat,                            -- UNIQUE(body_sha256, jwt_iat) — Plaid
                                                   --   retries, so duplicates are normal
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
(**I6**, §9.3), and `publication` exists so the daemon knows whether the phone
*could* have seen the latest snapshot. Sync succeeding and publish failing is a
distinct failure, and §9 depends on telling them apart.

**`seq` is never reset when a pairing rotates**, which is easy to get wrong now
that rotation is a local transaction and *feels* like a fresh start. It is not: a
phone that still holds an old payload must never be handed a lower `seq` than it
has seen, or **I6** rejects the genuine new one. The counter belongs to the
daemon, not to a pairing.

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
| Cash balance, `balance_mode: realtime` | `/accounts/balance/get` | `balance.last_updated_datetime` when the institution provides it, else `fetched_at` — justified **only** because this endpoint extracts live (F5) | within 36h |
| Cash balance, `balance_mode: cached` | `/accounts/get` | nothing — `UNKNOWN` (R2) | never; permanently labelled unverifiable |
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
alarm at all. Cash accounts, which have no market calendar, use plain wall-clock 36h (the
owner's threshold).

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

**The accelerator — one route on the host that already holds the credential.**
*(Rewritten in rev 10, and this is the section the host move improved most.)*
Plaid POSTs to a public HTTPS endpoint on the VPS; the receiver **verifies the
JWT inline and writes the event straight to the local database** in the same
request. That is the whole mechanism.

It is worth being explicit about what disappeared, because it was a third of
this section and every part of it was a workaround for a host that could not be
reached from the internet: a receive route at a third party, a queue to hold
events until the Mac woke up, a drain loop, an ack protocol, at-least-once
semantics with an idempotent consumer, a `drain stalled` alert for when the loop
broke, a TTL racing the drain, and a rule about which of two clocks the
five-minute check should use. **The receiver is now the verifier is now the
consumer** — one process, one hop, no queue between them.

Two properties that used to need argument are now structural:

- **Plaid's five-minute `iat` rule is checked against the clock that received the
  request**, because there is only one clock in the path. Rev 9 needed a whole
  subsection to establish this, since a five-minute drain interval stacked on
  Plaid's five-minute window would have rejected genuine events intermittently.
  The stacking is gone.
- **Verification happens where the credential already is.** Plaid's
  `/webhook_verification_key/get` needs `client_id` + `secret`; this host holds
  them for every other call it makes. Rev 9 had to argue against putting them at
  the third party; there is no second place to put them now.

What still has to be built carefully, because it is a public unauthenticated
route (Plaid cannot present our credential):

- **A forged event** fails JWT verification — the signing key is Plaid's, fetched
  from `/webhook_verification_key/get` and cached by `kid`. `request_body_sha256`
  is compared in **constant time**, against the **raw received bytes**: the hash
  is whitespace-sensitive, so a receiver that parses and re-serializes the JSON
  before hashing breaks every signature and makes healthy traffic look like an
  attack.
- **A replayed genuine event** carries its original `iat` and fails the
  five-minute check exactly as Plaid intends.
- **A replay inside five minutes** passes, and is inert: it collides on
  `UNIQUE(body_sha256, jwt_iat)`. Plaid retries, so this is ordinary traffic,
  not an attack signature.
- **Flooding the route** costs CPU and disk, not correctness: the body is
  size-capped and rejected before parsing, the route is rate-limited at the
  reverse proxy, and an unverified event never reaches the state machine. If the
  route is knocked over entirely the poll floor carries on — **the number stays
  honest**, which is the property that has to hold.
- **The path is unguessable and it is not a secret.** `POST /hook/<random>` keeps
  ordinary internet scanning out of the logs; it is a nuisance filter, and the
  JWT is the actual control. Nothing anywhere may treat knowledge of the path as
  authorisation.

**What we accept when the receiver is down or a webhook is lost:** advance warning.
A migration or revocation is then detected after the fact, on the next poll,
when the Item's error surfaces — `PENDING_DISCONNECT`'s deadline having already
passed, or the Item having landed in `REVOKED`. The number stays honest either
way, because the account's data ages visibly on Axis B regardless. Because the
floor holds on its own, **a dropped webhook can never cause a wrong number, only
a later warning** — which is why the receiver is allowed to be best-effort, and
why it is deliberately *not* on the critical path of anything.

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
data and the phone never hardcodes the daemon's cadence:

*(86400 is the **promise** — "rebuilt at least once a day" (§1) — not the
schedule. §13 runs the sync on a tighter 20h rule precisely so that one failed
run does not break the promise; the gap between the two is deliberate margin,
not two numbers disagreeing. The phone is told the promise, because that is what
the owner is owed and what a broken pipeline violates.)*

```
stale_after = published_at + publish_interval_seconds + grace_seconds
```

The phone evaluates, in order:

1. **Clock disagreement → `COPY_UNKNOWN`.** If `published_at > device_now + 5min`
   (payload from the future), or the device clock has moved backwards since the
   last successful fetch (detected by pairing each stored timestamp with a
   monotonic reading), the phone **cannot** compute an age. It says so — "this
   device's clock disagrees with the server's" — and never renders the number as
   fresh. Silently trusting a skewed clock is how a six-day-old figure gets shown
   as current.
2. **`device_now <= stale_after` → `COPY_FRESH`.**
3. **Otherwise → `COPY_STALE`**, with a *reason*, because two very different
   faults land here and they have different fixes. The reason is
   `HOST_NOT_PUBLISHING` **iff all three hold**, and `CANNOT_CHECK` otherwise:

   ```
   last_fetch_success_at >= stale_after          -- we reached the source AFTER the
                                                 --   copy was already due, not merely
                                                 --   "recently"
   last_fetch_attempt_at == last_fetch_success_at -- and nothing has failed since
   last_fetch_seq        == last_seq              -- and it had nothing newer than we hold
   ```

   - `HOST_NOT_PUBLISHING` → *"reached the source; nothing has been published
     since <time>."* The phone and the network are fine; the daemon or its sync
     is not. *(Renamed in rev 10 with the host. The state is unchanged — but on
     this architecture it does more work than before, because §11 has no channel
     that can carry a host-side failure to the owner: this is how he finds out.)*
   - `CANNOT_CHECK` → *"couldn't check since <last_fetch_success_at>"*, plus the
     error class (offline, credential rejected, transport error, never fetched).
     The daemon may be perfectly healthy and unreachable.

   **The error class matters more since rev 10, and it is worth saying why.** A
   dead host and a phone that has simply dropped off the tailnet both land in
   `CANNOT_CHECK` — and with alerting reduced to this one channel (§11), those
   two sit at opposite ends of how much the owner should care. They are
   distinguishable in practice and the app must distinguish them: *no network at
   all* is one class, *network fine, the host did not answer* is another. The
   first is ordinary and self-correcting; the second is the shape a lost VPS
   takes on the only screen that can report it.

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

The connection axis arrives from the daemon already evaluated (the phone never
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
- The daemon's `publication` table (§7) lets `doctor` distinguish "sync failed"
  from "sync fine, publish failed" — the latter is invisible from the phone alone.

### 9.3 The phone never accepts a downgrade (I6)

*(Rev 10 rewrote this section down to a fifth of its length, and the deletion is
the honest description of what happened. Through rev 9 this section and a
subsection beneath it carried a publication audit state machine — `UNCONFIRMED`/`LANDED`/`NOT_LANDED`/
`SUPERSEDED` — an expected-live set, a whole-object `payload_fingerprint`, a
pre-write compare classifying `ROLLBACK`/`FOREIGN`/`ABSENT`/`UNAVAILABLE`, and a
read-back. **Every one of those existed because a third party held the object.**
They were the answer to "how does the Mac find out that the thing serving our
snapshot is serving something we did not put there?" — including the provider's
own 30-day restore window. The daemon now serves rows out of the database it
wrote them to. There is no gap between writing and serving for anything to
happen in, so the machinery that watched that gap is deleted rather than ported.
The three blockers Codex raised against rev 7 all lived here; they are void by
removal, and §6.2 says so plainly.)*

**What survives is the check that was never about the transport.** A cached
payload on the phone can be *downgraded*: something serves the phone an older
envelope than the one it already holds, and every cryptographic check passes,
because an old ciphertext is a perfectly valid ciphertext (§6.1). The cipher
cannot see this. Only a counter can.

So `publication.seq` is monotonic and never reset (§7), it travels **inside the
AAD** (§6.1) so it cannot be edited without breaking the tag, and:

**I6, as a rule the app implements.** On every successful fetch, the phone
compares the fetched `seq` against `last_seq` — the `seq` of the payload it
currently holds:

| Fetched | Phone does |
|---|---|
| `seq > last_seq` | accept; replace the cache; `last_seq = seq` |
| `seq == last_seq` | accept as "nothing new"; the copy's age is unchanged, and this is the ordinary case between publications |
| `seq < last_seq` | **refuse.** Keep the newer cached payload, and raise a persistent phone-local warning |
| `pairing_id` is not the paired one | **refuse**, same warning. The phone is talking to something that is not its daemon |

Three things about the refusal case, each of which a plausible implementation
gets wrong:

1. **The warning is persistent and phone-local.** It survives restarts and clears
   only when a `seq` greater than `last_seq` actually arrives — not on the next
   successful fetch, which would let a single good response paper over an
   unexplained downgrade.
2. **It never reaches the daemon.** There is no phone→host report channel and
   adding one would trade away the read-only asymmetry that makes a lost phone a
   non-event (§5). The phone warns about what the phone sees; §11 alerts on what
   the host sees; neither stands in for the other.
3. **It is measured against what *this* phone has seen**, which is the honest
   limit and is stated rather than hidden: a phone that has never fetched has no
   `last_seq` to compare against, so its *first* payload is accepted on trust —
   as it must be, since pairing is the trust anchor. I6 defends a copy, not a
   first impression.

**The realistic trigger is not an attacker.** On this architecture the ways to
serve the phone an old `seq` are: a database restored from backup (§14a) onto a
running daemon, a rollback of the daemon to an older build with an older
database, or two daemons on the tailnet answering to the same name. All three are
operator error, all three are *exactly* the case where a silently-accepted old
number would be most convincing — and all three are why this check stays in a
design that otherwise deleted its whole integrity apparatus.

## 10. Net-worth computation

```
net_worth = Σ(account value × account.sign)     -- v0: every sign is +1, so this is Σ(assets)
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
4. **v0 has no liability accounts at all** — cards are deferred (§1, §3), so the
   sum has no negative term and the UI has no debt row. The formula is written
   over `sign` rather than as "assets minus debts" for one reason: a subtraction
   term left lying around for a category that cannot exist is exactly the
   cross-section contradiction the last six reviews kept finding. When cards
   return, `sign` is set once at link time from the Plaid account type and
   stored, never inferred at render time — and nothing else in §10 changes.
5. History renders incomplete snapshots visually distinct (dashed/hollow) — a gap
   in the record must look like a gap.
6. Single currency (USD). The schema carries currency so mixed units fail loudly
   rather than silently summing unlike things.

---

## 11. Alerting

**The owner decided the channel, and it constrains the design rather than just
configuring it** *(2026-08-30: in-app on the phone only; email and the
agent-mailbox route were both put to him and both declined)*. On a headless host
that decision has a consequence which must be stated plainly rather than
discovered:

> **There is no way to reach the owner. An alert is seen when he opens the app,
> and not before.**

Rev 9 could lean on a macOS notification appearing on a Mac he uses daily. There
is no Mac in the path now, no mail, and no push (which would need FCM and a
sender — infrastructure, and §4 forbids the spend). So the phone is not *a*
surface for alerts, it is the **only** one, and two rules follow directly:

1. **Alert state travels inside the payload.** It is not something the app
   derives, and not something it asks for. The daemon evaluates, the payload
   carries the result, the app renders it. A cached payload therefore carries
   the alerts that were true when it was published — labelled as of that time,
   like everything else on a stale copy (§9.2).
2. **An unhealthy state must be impossible to miss on open, and a stale total
   must never render as a normal number.** This is where the whole design pays
   out or fails: the product exists because an aggregator rendered a frozen
   number as a live one, and a design whose only alerting channel is a screen the
   owner opens irregularly has *less* margin for a quiet indicator, not more.

| Channel | Used for | Mechanism |
|---|---|---|
| **In-app, in the payload** | `NEEDS_REAUTH`, `REVOKED`, **frozen data**, **publication overdue**, **accounts pending reconciliation** | `alert` rows on the host, serialized into the payload; persistent until resolved |
| **In-app, phone-local** | **rejected downgrade / foreign `pairing_id`** (§9.3) | the phone's own observation; persistent until a newer `seq` arrives — **never reaches the host** |
| **Local notification on the phone** | the same in-app alerts, when the app evaluates a newly-fetched payload in the background | Android local notification. Best-effort: it is a *prompt to open the app*, never the alert itself |

*(The local notification is deliberately described as best-effort. Android
background execution is not dependable — the sibling project spent a whole
revision on exactly this — so nothing may be designed as though the notification
will arrive. It shortens the average time-to-notice; the in-app surface is what
the guarantee rests on.)*

**Seven alerts became four, and the deletion is the point.** Rev 9 also carried
*read-back mismatch*, *read-back unavailable*, *pre-write rollback*, *foreign
write*, *snapshot missing*, *pairing uncertain* and *drain stalled*. Every one
of them was an alert about a third party misbehaving or a queue stalling, and
§6.2/§6.4/§8.4 deleted the mechanisms they watched. They are not "unimplemented";
there is nothing left for them to observe.

The four that remain deserve a note, because they are the ones a naive build
would not have:

- **Frozen data** — an account whose `source_as_of` has not advanced across
  **five consecutive market days** *while its Item is `HEALTHY`*. This is Axis A
  green, Axis B dead: the original failure, caught by the only check that can
  see it. It is owner-actionable (usually a re-link fixes it), which is why the
  same condition is `ACTION_NEEDED` on screen (§9.2) — **this paragraph is the
  single definition of the threshold**; the display state and the alert both
  derive from it rather than each carrying their own number.
- **Publication overdue** — the last successful `publication` is older than the
  publish interval plus grace (§6.4). **This one has a hole in it that the
  channel decision opens, and it must not be papered over:** the alert is
  evaluated on the host and delivered *in the payload*, so the failure of the
  publish path is the very thing that prevents its own alert from being
  delivered. The phone cannot receive "I have not published." What the phone
  *can* do is notice that its copy has aged past `stale_after`, which it does
  independently and without the host's help (§9.1) — and `HOST_NOT_PUBLISHING` is
  precisely the reason code for "I reached the source and it had nothing newer."
  So the alert row exists for `doctor` and the record; **the phone's own copy
  staleness is what actually surfaces this failure to the owner.** Stated here
  because a reader who sees the alert listed will otherwise assume it arrives.
- **Pending reconciliation** — accounts are sitting at `NEW` and contributing
  nothing (§8.5), so the total is knowingly understated until the owner confirms
  a mapping.

**The rejected downgrade is deliberately not in the payload row.** The
distinction is the *observer*, and collapsing it would recreate a promise review
already rejected once: the host cannot alert on something only the phone can
see, over a channel the architecture does not have and should not grow (§9.3).
The host alerts on what the host observes; the phone warns about what the phone
observes; neither stands in for the other.

Anti-fatigue: **one alert per item per state entry**, re-raised at most once per
24h while unresolved, never for `DEGRADED` or for routine Axis-B staleness inside
its expectation window (UI only). Alerts auto-resolve on the transition back to
`HEALTHY` — and a frozen-data alert resolves only when `source_as_of` actually
advances, not when a call merely succeeds. The anti-fatigue rule matters more,
not less, on a single-channel design: the app's unhealthy state has to stay
credible, because there is no second channel to fall back on when the owner
learns to swipe past it.

**Push notifications remain out of scope** — a server-sent push would need FCM
and a sender, i.e. infrastructure and an account (§4). The local notification in
the table above is the app notifying *itself* after a background fetch, which
needs neither.

---

## 12. Manual assets

- **Real property** — `MANUAL_STATIC`: never refreshed, never marked stale,
  always labelled with `valued_as_of`. **O4 is answered: a revision log**, with
  the purchase price as the default first entry *(owner, 2026-08-30)*. The owner
  may set a new value at any time; every revision is kept with its own date.

  **The rule that makes this worth building rather than a settings field: a
  revision applies from its own date forward, and the curve behind it does not
  move.** If the house is entered at its purchase price in 2023 and revalued in
  2026, the 2024 points on the net-worth curve keep showing what the owner
  believed in 2024. The alternative — one mutable value, applied retroactively —
  would silently redraw history every time an estimate changed, which is the same
  class of lie as a frozen balance rendered as live, just running the other way.

  Mechanically this needs nothing new: observations are already append-only (§7)
  and the curve is already built from them, so a revision is **a new observation
  with its own `source_as_of`**, not an `UPDATE`. The one thing to get right is
  that the value used for a given day is the latest revision **as of that day**,
  never the latest revision outright.

  *(And snapshots already written are not revisited — `snapshot.total_net_worth_minor`
  is a stored number, not a query re-evaluated at render time (§7), so past points
  on the curve are immune by construction. Worth checking rather than assuming,
  because "recompute the curve from observations" is the obvious implementation
  and it would reintroduce exactly the retroactive deformation this decision
  rules out.)*
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

**A systemd service on the VPS**, not launchd. *(Rev 10. The section this
replaces was three paragraphs about macOS deferring `StartInterval` timers on
battery and why no battery guard may ever be added — hard-won knowledge on the
sibling project, and **entirely Mac-specific**. A VPS has no battery and never
sleeps. Recorded here only so nobody reintroduces a workaround for a problem
this host does not have.)*

Two units, `networth-sync.service` (the periodic worker, started by
`networth-sync.timer`) and `networth-serve.service` (the always-on snapshot
server and webhook receiver, `Restart=always`). Both run as a **dedicated
unprivileged user** that owns the database and nothing else (§15), and both are
**enabled at boot** — "the VPS is always awake" is a claim about the *host*, and
it buys nothing if a reboot leaves the daemon stopped.

**Three ordering and isolation details that a straightforward implementation gets
wrong, all of them consequences of rev 10's host move rather than of systemd:**

1. **The tailnet interface does not exist at boot time.** `networth-serve` binds
   the tailnet address (§6.3.1), which `tailscaled` has not yet brought up when
   the unit first starts. The unit therefore orders `After=tailscaled.service`
   and **retries the bind**; what it must never do is fall back to `0.0.0.0`
   because the intended address was unavailable. That fallback is the single
   configuration mistake in this design that silently converts a private endpoint
   into a public one, and "the address was not ready yet" is exactly the
   plausible-looking reason someone would add it.
2. **The webhook receiver must not be able to take the snapshot server down with
   it.** Both live in `networth-serve` today, which means an unhandled exception
   in a parser reachable from the public internet stops the route the *phone*
   uses. Since the whole alerting design now rests on the phone being able to
   fetch (§11), that is a public input with a path to silencing the owner's only
   channel. The receiver runs in its own worker with a catch-all boundary, and
   the acceptance criterion is stated as a property: **no request to `/hook` can
   terminate the process.** Splitting them into two units is the alternative and
   is a reasonable implementation choice; what is not optional is that one cannot
   kill the other.
3. **The sync worker and the serving process share one SQLite file.** WAL mode,
   one writer, and the serving process opens the database **read-only** — it has
   no reason to write and every reason not to be able to.

The timer fires every 5 minutes and the worker asks the database what is due:

| Job | Due when |
|---|---|
| health poll | >60 min since the last poll |
| full sync | **either** no successful full sync since the most recent market close + 1h, **or** >20h since the last successful full sync — whichever comes first (see below) |
| quote refresh | any `MANUAL_QTY_LIVE_PRICE` price older than the last close |
| publish | a snapshot exists newer than the last successful `publication` (§6.4) |
| backup | >24h since the last verified backup — §14a |

*(No webhook-drain row: events are verified and stored by the receiver as they
arrive, §8.4. And no pre-write compare or read-back around the publish, §9.3.)*

**Due-ness is computed from stored state, never from timer semantics** —
`Persistent=true` on the timer plus a stored-state predicate, so a host that was
down for two days finds work due on boot and catches up. There is no missed-fire
concept to handle. This is the one piece of the launchd design worth carrying
over: it was never about launchd, it was about refusing to trust the scheduler
to have fired.

**Why the full sync has two predicates and not one.** *(From review. Rev 3 made
a sync due only after a new market close, which quietly redefined the product:
after a successful Friday run the predicate stayed false all weekend, so a Monday
morning number was built from Friday's balances — and at ~36h the copy would
have crossed its own staleness window (§9.1, §1) by **construction**, on the one
promise the brief states outright: rebuilt at least once a day. A schedule that
cannot satisfy the requirement it exists for is a bug in the schedule, not a
caveat for the UI.)*

The two clocks in §8.1 are why the two predicates are not redundant:

- **Holdings move on market time.** Institutions post after close, so
  *market close + 1h* is when new data can actually exist. Nothing else fetches
  it earlier.
- **Cash balances move on wall-clock time.** Money moves without waiting for a
  market day. `/accounts/balance/get` returns a real-time balance whenever it
  is called (**F5**), so a weekend call returns genuinely newer data.

`min(market-close rule, 20h)` covers both with one job and one code path, which
is worth more than a second job here: the sync already fetches holdings and
balances together, and splitting them would double the state to reason about for
no additional freshness.

**20 hours, not 24.** A 24h threshold makes "at least once a day" *exactly*
achievable and therefore fragile — one failed run and the day is missed. At 20h
a failure has a full backoff cycle (1h/2h/4h/8h) to recover before the 36h stale
threshold is anywhere near, so the daily guarantee survives a bad afternoon
rather than depending on nothing going wrong.

The visible consequence is that weekend runs fetch holdings that have not moved.
That is correct and costs nothing: `source_as_of` does not advance, so Axis B
keeps ageing the holdings honestly (§8.1) while the balances genuinely refresh,
and the `FROZEN` escalation counts **market** days, so a weekend of unchanged
holdings can never be mistaken for a frozen feed.

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
| Card issuers | **0** | deferred with credit cards (§1, rev 9) — they were one slot per issuer *login* |
| **Reserve** | **4** | permanent headroom for a mislink or a future institution |

Working budget: **6**, and the change is worth stating rather than just
recalculating. Card issuers were the only category with an unbounded count in
it, so deferring cards does not merely free their slots — it removes the part of
the budget nobody could size in advance. Every slot not spent on an issuer login
becomes headroom, and against **F2** headroom is the only currency that matters:
a slot is never recycled, so reserve is *literally* the number of link mistakes
this project can survive. Rev 9 doubles it, from two to four.

Spending rules:

1. **Rehearse every Link flow in Sandbox first.** Sandbox is unlimited and free;
   Production mistakes are permanent.
2. Confirm the exact institution *and login* before each Production exchange.
3. Never spend a slot on an account whose balance could be typed in once a
   month. A small, rarely-moving account is a manual entry, not an Item.
4. Re-auth via update mode costs nothing (§8). Only `REVOKED` can require a new
   slot, and that is an owner decision.
5. Track remaining slots in the DB and surface them in `doctor` and the app.
   Running out is invisible until it isn't.

---

## 14a. Durability: the tokens are as scarce as the slots

Two things in this system cannot be regenerated, and they fail differently:

- **The access tokens.** Plaid's own Trial guidance is explicit — "be sure to
  persist your access tokens and do not lose track of them" — because with
  **F2** a lost token cannot be replaced by re-linking for free. **There is no
  recovery API**: Plaid's guidance for a lost `access_token` is to run Link
  again, which creates a **new Item** and burns another of the ten *lifetime*
  slots. Losing `plaid-items.json` with four Items linked destroys 40% of the
  project's lifetime budget, silently, at rest.
- **The history.** Snapshots are the one asset that is impossible to backfill.
  A year of curve cannot be reconstructed from anywhere.

**O8 is answered: the backup goes from the VPS to the Mac, over the tailnet**
*(owner, 2026-08-30; he reserved the right to veto and has not)*. A scheduled
`rsync` over SSH to a machine he physically owns, in a different country from
the VPS, on a different provider, on different hardware.

**It is framed around the tokens, not the curve, and that reframing came from a
question the owner asked** — could the history simply be back-filled instead, and
the backup skipped? The answer is no, and the more useful half of the answer is
that history is not the thing at risk:

- **The curve cannot be reconstructed.** Plaid exposes **current** holdings and
  balances; there is no historical-balance or historical-holdings endpoint.
  Reverse-reconstructing from investment transactions is bounded by each
  institution's lookback, is not supported everywhere, has **no cash-balance
  history at all**, and — decisively — would need historical **mutual fund**
  prices for the retirement plan, which the quotes integration (§12) does not
  cover. The result would be an approximate curve with a hole in its largest
  component, which contradicts this project's entire thesis. A missing curve is
  honest; an invented one is the failure mode the product exists to prevent.
- **But the curve is not what must not be lost — the token set is.** A lost curve
  costs history. A lost `access_token` costs a **permanent slot per Item**, and
  no amount of later effort buys it back. That is the asymmetry the backup is
  sized against.

Which is why this is deliberately **a small scheduled job, not a subsystem**: the
payload is one SQLite file and one token file. It is measured in megabytes.

- **Backup:** one encrypted archive of the SQLite database plus the `TokenStore`
  contents, written outside the working tree, keyed from `~/agents/secrets/`.
- **It never goes to a third party, and it must leave this host.** Two different
  requirements, and earlier revisions stated only the first in some places while
  §14a.1 required the second. Handing a provider a bundle of access-token
  ciphertext to hold indefinitely is its own risk class — so, no third party. But
  an archive on the disk it is meant to survive is not a backup — so, **not this
  host**. The Mac satisfies both.

### 14a.1 The gate has to survive the failure it exists for

*(From review, and the finding was exact: an earlier draft let the database, the
archive and `networth-backup.key` all sit on one disk, then called a restore into
a temp directory "verified". That drill proves the archive parses. It proves
nothing about the scenario the section is named after — the host dies — because
in that scenario all three copies died together. A backup that only survives `rm`
is not a backup; it is a copy.)*

Three acceptance criteria, all owner-controlled and all free:

1. **A destination in a separate failure domain.** Satisfied by construction now
   that O8 is answered: the destination is **a different machine**, reached over
   the tailnet. *(Rev 10 deleted a page of APFS `diskutil` resolution here —
   `APFSPhysicalStores`, `ParentWholeDisk`, external-media detection. It existed
   because the destination might have been a second volume on the Mac's own disk,
   where a naive `stat -f %d` filesystem comparison would have passed two volumes
   sharing one physical store. That whole branch is moot when the destination is
   another computer, and it was macOS-only code that would not have run on Ubuntu
   anyway.)*

   What survives is the part that was never about disks: the check runs on
   **every** backup, not once at setup, and **failure to verify fails closed**.
   Concretely — the destination host answers over the tailnet, the transfer
   completes, and the archive is readable back. An unreachable Mac means the
   backup **did not happen** and `doctor` says so; it must never be recorded as
   a success because the schedule ran. *(A check that passes when it cannot see
   is worse than no check: it reports a green gate.)*

   The nightly window matters and is a known limitation rather than a bug: the
   Mac is not always on, so backups land when it is. `doctor` and the app both
   surface **days since the last verified backup**, because that number silently
   growing is exactly how the token set gets lost.

   **But "daily" is the wrong criterion, and running the ordinary timeline
   through this schedule is what exposes it.** Suppose the Mac has been off for
   two weeks and the VPS dies. Two weeks of *curve* are lost — regrettable. Two
   weeks of *token set* are lost only if an Item was linked in those two weeks —
   and if one was, the loss is a **permanent slot**, which is the thing this
   whole section exists to prevent. The token set changes at exactly one moment:
   **a successful Link.** So the binding rule is not a daily schedule at all:

   > **`scripts/link.sh` runs a backup immediately after a successful token
   > exchange, and refuses to report the Link as complete until it succeeds.**

   The daily job stays, for the history. But the gate that protects the
   irreplaceable thing is tied to the event that creates it, not to a clock —
   the same reasoning that put this section in Phase 1 instead of Phase 5.
2. **A recoverable copy of the backup key that is not only on the VPS.**
   `networth-backup.key` decrypts the archive; the two must not share a fate.
   The owner puts it in a password manager or on paper (it is one line), and
   confirms with `networth backup attest-key`, which records
   `key_escrow_confirmed_at` and *nothing else*. This is an **attestation, not a
   proof** — no agent can verify a password manager, and pretending otherwise
   would be its own dishonesty. `doctor` shows it, with its date, as the
   owner's own claim.
3. **The drill restores from the destination.** `scripts/restore-drill.sh` pulls
   the archive **back from the Mac** — over the same path a real recovery would
   use, which is the part that actually gets tested — decrypts it with the key
   resolved from `~/agents/secrets/`, restores into a temp directory, checks row
   counts and schema version, and verifies the `TokenStore` yields the same
   **token fingerprints** (salted hashes — never the tokens, never in a log). It
   records `last_verified_restore_at`, runs weekly, and `doctor` reports its age.

**Gate:** all three must hold before task 08 links the first Production Item. A
hard dependency in the task graph, not a recommendation — the window in which a
host failure costs permanent slots opens the moment task 08 runs, which is why
this is a Phase 1 gate and not a Phase 5 operations chore.

---

## 15. Secrets and what may never be committed

The repository is **public**. Repo visibility was never the real control — the
separation between code and credentials is. These rules are also in `AGENTS.md`,
which binds both agents.

**On the VPS**, under the dedicated service user's home, mode 600:

- `plaid.env` — `PLAID_CLIENT_ID`, `PLAID_SECRET`, `PLAID_ENV`.
- `plaid-items.json` — `{item_id: access_token}`.
- `networth-payload.key` — the payload key (§6.1). **One key, no tokens**:
  tailnet membership replaces the bearer credential and the payload key *is* the
  read credential (§6.3.1).
- `networth-backup.key` — the backup archive key (§14a).
- Quotes key for `QuoteClient` (§12).

**On the Mac:** `~/agents/secrets/networth-vps.key` — the agents' SSH key to the
VPS, and the backup archives as they land. Nothing else; the Mac is a backup
target and a Link workstation, not a component (§5).

**Not in this list, deliberately: anything on the phone.** Since §6.3 the app
holds the payload key in the Android Keystore, provisioned by pairing — so there
is no build-time secret to manage, no `--dart-define` to leak into CI logs, and
the release APK is not a bearer artifact.

### 15.1 The VPS is a shared host, and the owner accepted that knowingly

The VPS already serves as the owner's **Tailscale exit node** for his personal
VPN. Rev 10 adds the Plaid master credential and the full token set to the same
machine. That is real concentration and it is recorded here rather than left
implicit: **compromise of this host yields the Plaid `client_id`/`secret`, every
`access_token`, the whole balance history and the payload key at once** — and,
separately, the ability to observe his VPN traffic.

**The owner was told this explicitly and accepted it** (2026-08-30). It is
recorded, not silently dropped, because a future reader weighing a change to this
host needs to know the concentration was a decision. The mitigations are ordinary
and are part of the deploy task, not aspirations:

- **Key-only SSH; `PasswordAuthentication no`, `PermitRootLogin no`.** Agents use
  their own dedicated key (`networth-vps.key`); the owner's password is never
  requested by, shown to, or stored by any agent — a standing rule, not a
  preference.
- **A firewall that opens exactly two things to the public internet:** SSH and
  the webhook route (§8.4). The snapshot server binds the **tailnet interface
  only** and must never be published; a bind-address regression is the one
  configuration mistake here that quietly turns a private endpoint into a public
  one, so it is an acceptance criterion with a test, not a config comment.
- **Unattended security upgrades** enabled.
- **A dedicated unprivileged service user** owning the database and the secrets,
  so the daemon is not root and a bug in the webhook parser is not a root bug.
- **The backup is what makes a lost VPS recoverable rather than terminal**
  (§14a) — without it, losing this host strands every Item slot permanently.

The residual, stated plainly: this design **does not** defend against compromise
of the host. Every freshness and integrity claim in it is scoped to an
uncompromised host (§6.1), and no check that runs *on* the host could honestly
claim otherwise.

Non-negotiable:

1. **`.gitignore` from the first commit** for the database, snapshots, any
   `.env`, any token cache, any export. A file committed once stays in history
   even after deletion, and on a public repo that is unrecoverable.
2. **Never commit real figures** — no real balances, account numbers, or
   institution item ids, in code, tests, fixtures, docs, or PR text. **All test
   fixtures are synthetic.** No test or script may print a real balance, in CI
   or locally.
3. **Credentials live only in the two locations named above** — the VPS service
   user's secrets directory, and `~/agents/secrets/` on the Mac. Never in git, a
   PR body, a review comment, or a log line. The DB stores `secret_ref` (a key
   name) resolved through `TokenStore`, never a token. **`TokenStore` is what
   makes this a one-line change rather than a refactor** (§2 reservation 3): the
   host moved between rev 9 and rev 10 and the storage path moved with it, which
   is exactly the churn that reservation was written for.
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

**Host side: Python 3.12 + SQLite + the official `plaid-python` SDK**, on Ubuntu
26.04. **Phone side: Flutter** (decided by the owner), Android only (O6).

| Option for the host side | For | Against | Verdict |
|---|---|---|---|
| **Python + SQLite** | Official Plaid SDK; SQLite in stdlib; trivial systemd integration; no build step; ideal for a daemon | Not the UI language | **Chosen** |
| TypeScript / Node | Official SDK too | Adds a toolchain for no daemon-side gain; shares nothing with a Flutter UI | Second |
| Dart end-to-end | One language across both halves | **No server-side Plaid SDK** — would mean hand-rolling a financial API client and its error taxonomy, on the side of the system that holds the credentials | Rejected |

**There is no transport component any more** *(rev 10)*. Rev 9 specified a
Cloudflare Worker here — six routes, two storage bindings, and a rule about which
route bound which store because atomicity depended on it. It is deleted. What
takes its place is **two routes in the same Python process that already holds
everything**:

| Route | Bound to | Auth | Purpose |
|---|---|---|---|
| `GET /snapshot` | **tailnet interface only** | tailnet membership; the payload key is the read credential (§6.3.1) | the phone reads the current envelope |
| `POST /hook/<random>` | public interface | none — Plaid cannot present our credential; the **JWT** is the control (§8.4) | receive, verify inline, store |

Four routes disappeared with the Worker, and it is worth naming them so a reader
of an old review comment can see where they went: `PUT /snapshot` (there is no
upload — the writer and the server are one process), and
`POST /pairing/rotate` + `POST /pairing/revoke` + the queue's `GET`/`DELETE`
(rotation and revocation are a local SQLite transaction, §6.3.1; there is no
queue, §8.4).

**The `Publisher` seam survives the deletion on purpose** (§5). Publishing is now
a local transaction, which is barely a seam at all — but it is the joint the
design has already been swung on twice (GitHub repo → Worker → local), and the
cost of keeping it is one interface.

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

**The host move splits this step across two machines, and the split is a
simplification rather than a complication.** Link runs in a **browser on the
Mac** — that is where the owner is sitting and where his password manager is —
while the `public_token` must be exchanged where the client secret lives, which
is now the **VPS**. So:

1. `scripts/link.sh` runs on the VPS, over SSH from the Mac. It mints a
   `link_token` and prints the URL.
2. The owner opens it in the Mac's browser and completes Link there. **Credentials
   and MFA go into Plaid's page**; nothing on either machine sees them.
3. The redirect page displays the `public_token`; the owner pastes it back into
   the waiting SSH session, which exchanges it immediately and writes the
   `access_token` through `TokenStore`.

**Primary path: copy-paste**, and rev 10 makes it *more* clearly the right
answer rather than less. Rev 9 kept an optional spike (task 07a) for handing the
token back automatically, which was awkward because a fetch from an HTTPS page to
`http://localhost` sits in browser mixed-content grey area. With the exchanging
process on a different machine from the browser, "automatic handoff" would mean
opening a route on the VPS for it — new public surface, for a step that happens
at most ten times in this project's life and takes ten seconds. **Task 07a is
therefore dropped, not deferred.** The `public_token` is short-lived and useless
without the client secret, so copy-paste is not a credential-handling risk.

Note that Link never runs **in the phone app**. Adding Plaid's Flutter Link SDK
would require a server-side `/link/token/create` anyway (§6), so it buys nothing
for a single-user tool.

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
- **No secrets are injected at build time.** The payload key arrives by runtime
  pairing (§6.3) and lives in the Android Keystore, so the APK carries nothing
  sensitive, rotation needs no rebuild, and a `--dart-define` can never end up in
  a CI log. The signing keystore is the only secret the *build* touches, and it
  stays outside the repo.

---

## 18. Open questions

**One question is open. It was seven.** *(Rev 10: the owner answered O4, O5, O6,
O7 and O8 in one sitting, and O3 was voided by the credit-card deferral. Answered
questions are kept, struck through, with the answer in place — so a reader of an
old review comment can still find what O5 was, and so the reason each branch
disappeared is on the record rather than inferable only from a diff.)*

| # | Question | Owner of the answer | Blocks |
|---|---|---|---|
| **O2** | **Does the Trial plan actually reach the in-scope brokerages via OAuth?** (**F4** — go/no-go) | **owner, via dashboard — the account does not exist yet, and creating it is owner-only** | **the Production-Link path: tasks 07, 08 and everything downstream of a real Item** (09, 12b, 26) — see below |
| ~~O3~~ | ~~How many distinct card-issuer logins?~~ **VOID** — it existed only to size the card share of the Item budget, and cards are deferred (§1, rev 9). Nothing waits on it | — | — |
| ~~O4~~ | ~~Real property: purchase price only, or a revision log?~~ **ANSWERED: a revision log**, defaulting to purchase price, every revision kept with its date — **and a revision applies from its own date forward, so the curve behind it never deforms** (§12) | — | — |
| ~~O5~~ | ~~Transport: a third-party relay, or Tailscale?~~ **ANSWERED: Tailscale — and the host moved with it.** The owner has an always-on Vultr VPS (already paid for, already his tailnet exit node), so the daemon runs there instead of on the Mac. Both drawbacks the Tailscale branch carried were *Mac* drawbacks and both are void: the VPS never sleeps, and it has a public IPv4 so the webhook accelerator survives (§8.4). The entire third-party branch is **deleted** (§6.2), not parked | — | — |
| ~~O6~~ | ~~iOS as well as Android?~~ **ANSWERED: Android only.** *Decided* rather than postponed — the iOS branch and its sideloading problem are gone from this design rather than parked. Tasks 21 and 24 are Android-only | — | — |
| ~~O7~~ | ~~Create a free third-party account for the transport?~~ **VOID** — it existed only on the branch O5 deleted. No new account is created by this design | — | — |
| ~~O8~~ | ~~Where do backups land?~~ **ANSWERED: VPS → Mac over the tailnet** — a different machine, provider and country. Framed around the **access-token set**, not the curve: history cannot be back-filled, but a lost token cannot be recovered *at all* and strands a lifetime Item slot (§14a) | — | — |

*(O1 — phone vs Mac/browser — was answered earlier: **Flutter phone app**, which
O6 narrows to **Android only**.)*

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

**And one non-question that would otherwise look like one.** The employer-equity
brokerage can require an explicit access request on the Plaid dashboard, granted
in **up to six weeks** (§3). Rev 9 carried that as a branch to plan around.
**The owner decided not to request it at all** (2026-08-30): the manual path
(§12) needs no Item, no OAuth approval and no wait, and requesting access would
have spent a lifetime slot on something that may not surface the award-center
account anyway. So it is not an open question, not a gate, and not a task — it
is a path this design does not take.

---

## 19. Owner runbook — the only manual steps

Agents must never perform these. Everything before and after is automated.

**Step 1 — Create the Plaid account** (~10 min, once)
1. Sign up at `dashboard.plaid.com/signup`, verify email.
2. Apply for the **Trial plan** at `dashboard.plaid.com/trial-plan`. Most apply
   automatically; a manual review takes 2–3 business days.
3. After approval, confirm the plan reads **Trial, 10 Items** and that the
   in-scope brokerages appear available. This **answers O2**. If they are *not*
   available, report it — that blocks the **Production-Link path** (tasks
   07 and 08, and anything downstream of a real Item) and nothing else. The
   foundation continues either way (§18); do not stop it. *(Rev 3 narrowed this
   in §18 and the task graph but left "stop before implementation proceeds"
   here, which is the sentence the owner would actually have been reading.)*
4. Copy `client_id` and the **production** secret into
   `~/agents/secrets/plaid.env`. Never paste them into a chat or a PR.
5. Register the redirect URI (§16) under *Allowed redirect URIs*, and the
   webhook URL (§8.4) under the webhook setting.
6. **Do not request special access for the equity-comp brokerage.** Rev 9 listed
   this as an optional step; the owner decided against it (§18). The manual path
   (§12) is the plan, it needs no request and no Item, and the request would cost
   up to six weeks for something that may not surface the award account anyway.

**Step 1a — Give the agents a key to the VPS** (~5 min, once; **this is the one
step everything else on the host waits for**)
1. An `ed25519` keypair already exists on the Mac:
   `~/agents/secrets/networth-vps.key` (private, mode 600, never leaves the Mac,
   never in git, a PR or a log) and `…​.key.pub`.
2. Append the **public** half to `~/.ssh/authorized_keys` on the VPS.
3. Tell the agents it is done. **No agent will ever ask you for a password**, for
   this host or any other — that is a standing rule, not a preference for this
   step (§15.1).

**Step 1b — Put the Mac on the tailnet** (~2 min, once)

The phone is already on it. The Mac needs to be, for the backup in step 1c to
have anywhere to land.

**Step 1c — Confirm where backups land** (~5 min, once; **before** Step 2, and
the ordering is the whole point — §14a.1)
1. **O8 is decided: VPS → this Mac, over the tailnet.** Nothing to choose; this
   step is confirming it works.
2. Copy `networth-backup.key` into a password manager or write it down, then run
   `networth backup attest-key`. It records only the date of your confirmation.
   Without this, the archive and its key die together.
3. Run `scripts/restore-drill.sh` and see it pass. It pulls the archive back
   **from the Mac**, so this is the first moment a real recovery has actually
   been exercised.
4. **Do not proceed to Step 2 until it passes.** After the first Production Link,
   losing the tokens does not cost a re-link — a lost `access_token` cannot be
   recovered at all and strands permanent Item slots (**F2**, **F6**, §14a).

**Step 2 — Link each institution** (~2 min each, once per institution)
1. SSH to the VPS and run `scripts/link.sh` (built by agents, run by the owner).
   It runs **there** because that is where the client secret lives.
2. It prints a Link URL. Open it **in this Mac's browser**. **Enter credentials
   and MFA there** — that page is Plaid's; neither machine sees them.
3. Paste the returned `public_token` back into the waiting SSH session. The
   script exchanges it, writes the `access_token` via `TokenStore` (mode 600),
   and records the item.
4. Link the **highest-value institutions first** — slots are permanent (**F2**).

**Step 3 — Stand up the daemon on the VPS** (~20 min, once; agents prepare
everything, the owner runs it)

*(Rev 10 replaced two mutually-exclusive step 3s — one per O5 branch — with this
one. The Cloudflare branch's step 3a was the longest procedure in this document:
an account to create, a Worker to deploy, and a login/logout bracket around every
`wrangler` command with a browser session to close and verify from a second
device. All of it is gone with the third party it protected.)*

1. **Harden the host** (§15.1), from the provided script: key-only SSH
   (`PasswordAuthentication no`, `PermitRootLogin no`), a firewall opening
   **only** SSH and the webhook port, unattended security upgrades, and a
   dedicated unprivileged service user that owns the database and the secrets.
2. **Install the two units** (§13): `networth-sync.timer`/`.service` and
   `networth-serve.service`. Confirm `networth-serve` is listening on the
   **tailnet address only** — `ss -ltnp` must not show the snapshot port on
   `0.0.0.0`. This is the one misconfiguration that silently publishes the
   endpoint, so it is checked by hand once here and by a test forever after.
3. **Put the secrets in place** under the service user (§15), mode 600.
4. **Register the webhook URL** in the Plaid dashboard (§8.4) if step 1 did not.

**Step 3a — Pair the phone** (~2 min, once, and again whenever you want to
rotate)
1. `networth pair` over SSH. It prints a QR code in the terminal immediately —
   there is no registration round-trip to wait for and nothing to fail
   halfway (§6.3.1).
2. Open the app and scan it. That is the whole provisioning step; nothing secret
   was ever compiled into the APK (§6.3).
3. Away from the tailnet the app shows its cached copy, labelled with its age
   (§9). That is the availability cost of this design, and it is the honest one:
   the number is not claimed to be current when it cannot be checked.
4. **Re-run `networth pair` any time to rotate.** The previous phone stops
   reading immediately, the stored envelope is dropped, and the new QR is
   printed — one local transaction, so "immediately" is literal (§6.3.1). No
   rebuild, no reinstall.
5. **Phone lost or stolen:** `networth revoke`. Same lockout, no replacement
   phone needed; the daemon keeps publishing and nobody can read. Then — worth
   doing and owner-only — **remove the device in the Tailscale admin console**,
   which is stronger because it revokes reachability rather than content. Know
   the one limit: the ciphertext already **on** the stolen phone is beyond
   recall. Revocation stops the next fetch, never the last one.

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
- **The app says its copy is old, with the reason "reached the source; nothing
  published since &lt;time&gt;"** → this is `HOST_NOT_PUBLISHING` (§9.1), and on this
  architecture it is the **main way a host-side failure reaches you at all**, so
  it is worth recognising. The phone and the network are fine; the daemon is not
  publishing. SSH in and run `networth doctor`, which will say which of the
  jobs (§13) is failing and when it last succeeded. Common causes: the units are
  stopped, the disk is full, or the sync has been failing against one
  institution for long enough to block the run.
- **The app cannot reach the daemon at all** ("couldn't check since &lt;time&gt;") →
  usually the phone is off the tailnet, which is ordinary and self-correcting.
  If it persists, check the tailnet first and the units second.
- **The app shows a persistent "refused an older payload" warning** (§9.3) → the
  phone was served a `seq` below one it already holds. On this architecture the
  realistic causes are operator error, not attack: a database restored from
  backup onto a running daemon, a rollback to an older build, or two daemons
  answering on the tailnet. Find out which before clearing it — the warning
  clears itself when a genuinely newer `seq` arrives, and forcing it away with a
  re-pair would hide the cause.
- **A `pairing_id` the phone does not recognise** → it is talking to something
  that is not its daemon. Re-pair (step 3a.1) only after you know why.

*(Rev 10 deleted five entries here — read-back mismatch, read-back unavailable,
drain stalled, pre-write rollback, foreign write and snapshot missing — with the
third-party transport that produced them, §9.3.)*

---

## 20. Task breakdown

See [`tasks/README.md`](tasks/README.md). Tasks are drafted but **deliberately
unassigned** — assignment is itself subject to review.
