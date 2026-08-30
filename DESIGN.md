# DESIGN — networth

Status: **proposed** (design phase; nothing implemented).
Author: Claude. Reviewer: Codex.

Revision 9 — **not review-driven.** Two owner decisions arrived while rev 8 was
in flight, and folding them into the same review round is cheaper than a round of
their own (the owner said so explicitly). No finding from any review is answered
here, and no mechanism changed.

- **O6 answered: Android only.** Decided, not postponed — the iOS branch and its
  sideloading problem are gone from the document rather than parked. Tasks 21
  and 24 are Android-only, and 21 stops being blocked on an owner answer.
- **Credit cards are deferred** ("先不做了" — deferred, not rejected). v0 is
  **assets only**: the Plaid Liabilities product, the card category and every
  credit-account path leave the model, the sync engine and the UI. **O3** is
  void. What stays is the cheap half — `account.sign`, and a rule that nothing
  may assume an account's value is positive — so cards return later as a link
  flow and a UI row, never a migration of history. §14's budget changes by more
  than a row: card issuers were the only category with an **unbounded** count,
  so the reserve doubles from 2 to 4, and against **F2** the reserve *is* the
  number of link mistakes this project can survive.
- **§1 now opens by saying there is exactly one deliverable**, the Android app,
  and that the Mac component is a headless daemon with no interface. This is the
  one change here that came from a *misunderstanding* rather than a decision:
  the owner had read this project's progress reporting as building two apps. The
  reporting was at fault, but a design a reader can come away from with the same
  impression shares the defect.
- §6 Option 3 records why the Mac cannot simply be replaced by a Cloudflare
  Worker on a Cron Trigger — 10 ms CPU per invocation on the free plan, no retry
  of failed scheduled runs, and it would put the Plaid master credential at the
  third party — so the question is not re-litigated later.

Revision 8 — reworked after Codex's seventh review requested changes on
`fa7d7b9`. Two of the three findings are again **defects in what rev 7 added
while fixing rev 6**, and the third is a command in the runbook that does not do
what the step around it says. The pattern rev 7 named — check the new mechanism
against the boring timelines — held; what it missed is the **other** direction,
the older sentence the new mechanism has just made false.

- **The audit model had no state for an attempt proven not to have landed.**
  Rev 7's resolver says observing the previous object proves a later attempt did
  not land, and then had nowhere to write that: `LANDED` is false, `UNCONFIRMED`
  keeps the row in the expected-live set forever, and deleting it discards the
  audit. `publication.outcome` gains **`NOT_LANDED`** — and **`SUPERSEDED`**,
  which is the same gap seen from the other side: a row that may well have
  applied before a later write replaced it cannot honestly be called either
  landed or not. §9.3.1 now states the resolver as a table over the **whole**
  pending set, so "`E` collapses back to one element" is derived rather than
  hoped for, and a 2xx stops being described as the only confirmation.
- **The new fingerprint was not the identity of the authenticated object, and
  the read-back never used it.** GCM authenticates the AAD *and* the ciphertext,
  so `SHA-256(nonce ‖ ciphertext)` left `pairing_id`, `schema_version` and
  `published_at` outside the comparison: an object the phone would refuse to
  decrypt classified as ours. `payload_fingerprint` (§9.3.1) hashes a canonical
  length-delimited tuple over every authenticated field, the nonce and the AEAD
  output including its tag — and the read-back, which still compared `seq`
  alone three sections away, now compares the same value. §6.1 pins the envelope
  and the exact AAD bytes both ends have to agree on.
- **`wrangler login --device` still opens the browser on this Mac** — in the one
  step whose stated purpose is that it should not. Cloudflare documents
  `--browser=false` as the way to suppress it, so §19 step 3a.0 and task 20 now
  carry `wrangler login --device --browser=false --use-keyring`, all three flags,
  with what each one is for.

Two smaller things fell out of the first two rather than being asked for: the
read-back now runs **only after a 2xx** (running it after a failed `PUT` would
report the previous object — still correctly in place — as tampering, which is
rev 7's own defect arriving through the other check), and §9.3.1 exists at all
because review's diagnosis was structural: this state machine was being
explained in five distant places, and every one of the last three rounds found
drift between two of them.

Revision 7 — reworked after Codex's sixth review requested changes on
`a65a5e0`. Three findings, and the first two are again **defects in what rev 6
added while fixing rev 5** — a new check that misfires on ordinary operation, and
a credential bracket that closed one of the two doors it named. The third is
older and larger: a guarantee this document has been making since rev 1 without
saying whom it is against.

- **The pre-write check called normal operation an attack.** It compared the live
  object to the *last `publication` row*, but that table deliberately records
  failed attempts, and `rotate`/`revoke` deliberately delete the snapshot — so a
  write that failed harmlessly read as `ROLLBACK`, every rotation read as an
  unexplained deletion, and a `PUT` whose response was lost read as `FOREIGN`,
  the gravest alert here, aimed at the Mac's own write. §9.3 now compares against
  an **expected-live set** built from confirmed *and* unconfirmed attempts plus
  explicit local deletes, and identifies its own bytes by a hash of them rather
  than by `seq`, which anything able to write can forge. `publication.outcome`
  becomes a state machine rather than a boolean: the Mac cannot tell "did not
  apply" from "applied, answer lost", so it stops pretending it can. The one case
  that stays ambiguous is named and decided in §9.3 rather than left to the
  implementation. *(Rev 8 corrects two things this bullet said: the hash covered
  only `nonce ‖ ciphertext`, which is not the identity of a GCM-authenticated
  object, and `LANDED | UNCONFIRMED` was one state short of being able to record
  the resolver's own conclusion. §9.3.1 supersedes both.)*
- **`wrangler logout` closes the CLI token, not the browser session that
  authorised it.** Rev 6 bracketed (a) and treated `wrangler whoami` as proof;
  Cloudflare keeps a dashboard session on its own terms — **72 hours of
  inactivity** by default — and that session can redeploy the Worker, which is
  the access §6.2.2 turns on. §19 step 3a.0 now closes **both** layers:
  `wrangler login --device` so the browser step happens on another device, and,
  when it cannot, an explicit sign-out **verified from a second device** (the
  dashboard will not let you revoke your current session).
- **"An old number cannot look current" needed a threat boundary, and had
  none.** AES-256-GCM is symmetric: the tag proves the writer held the payload
  key, not that it was this Mac. Against §6.2.2's own conjunction — active
  payload key *plus* control plane — an old total can be resealed under today's
  `published_at`. §6.2.2 now **decides** the boundary (guaranteed against parties
  without the active payload key; that conjunction accepted as out of scope) and
  names the unbuilt remedy, and O5 carries the line to the owner.

Following from the last two together, and worth more than either: **this Mac
already holds the payload key**, so a Mac that also keeps a standing Cloudflare
session holds *both* halves of §6.2.2's conjunction at once. The bracket in step
3a.0 is load-bearing for that section, not hygiene.

Revision 6 — reworked after Codex's fifth review requested changes on
`1f554cf`. Both findings were **claims rev 5 made while mitigating rev 4's
finding** — the mitigation was right and the reassurances around it were not:

- **"A restore is not silent" was false as written.** The read-back runs *after*
  every write, so a rollback landing between publications is overwritten by the
  next `PUT` before anything reads; and I6 rejects only a `seq` below one the
  phone has **itself seen**, so a lagging phone accepts a restore as new. The
  claim is withdrawn in §6.2.2 and replaced with what actually holds. §9.3 adds
  a **pre-write compare** so the Mac can observe a rollback still live at its
  next tick, with `ROLLBACK`/`FOREIGN`/`ABSENT` kept distinct. *(Rev 7 corrects
  two things this bullet said: the comparison basis, and "`FOREIGN` means the
  write token is not exclusively ours" — a control-plane holder needs no write
  token.)* What no check can bound is stated plainly: whoever holds the control
  plane can also
  redeploy the Worker, so checks that run *through* the transport cannot police
  its owner.
- **"This machine never holds a Cloudflare credential" contradicted the
  runbook**, which has the owner run `wrangler deploy` and `wrangler secret put`.
  `wrangler login` persists an OAuth access **and refresh** token locally. §6.2.1
  now scopes the claim to **runtime** and §19 gains **step 3a.0**: every owner
  operation is bracketed `wrangler login --use-keyring` → operation →
  `wrangler logout` → `wrangler whoami`. Logout invalidates server-side, so the
  bracket is real; staying logged in is offered as an explicit trust-boundary
  change rather than an unexamined default. *(Rev 7: that bracket was half of
  one — the browser session it opens survives it.)*

The through-line of both: **freshness lives inside the encrypted payload, not in
the transport's liveness** — so an undetected rollback can cost a *current*
number, but cannot make an old one look current *to anyone who does not hold the
payload key* (the scope rev 7 supplies, §6.2.2).

Revision 5 — reworked after Codex's fourth review requested changes on
`e0f1347`. One finding, and it lands on this design's own deciding criterion:
§6.2 picked a transport by **what it retains**, then rev 4 moved the state into a
SQLite-backed Durable Object without checking that store's retention. Cloudflare
keeps a **30-day point-in-time recovery window** over the whole object, on by
default, with no opt-out — so "an overwrite leaves only the current value", "the
old key decrypts nothing that still exists" and "deleting the snapshot removes
the one thing its key could decrypt" were all true of live state and overstated
as guarantees. New **§6.2.2** states what the window holds, what reaching it
would take, what the residual actually is, and why the recommendation survives
it; §6.2, §6.3.1, §9.3, **O5** and §19 are scoped to match, and tasks 20 and 19a
carry it as acceptance criteria. The retention line is now stated in O5 as the
one thing only the owner can weigh, because Tailscale is the branch that retains
nothing at all.

Revision 4 — reworked after Codex's third review requested changes on
`93e7556`. Its five findings were about mechanisms that could not be built as
specified, not about prose: §6.2.1/§6.3.1 (the pairing path promised atomic,
immediate revocation on a store that offers neither — the snapshot and the
pairing verifier move to a **Durable Object**, and KV keeps only the webhook
queue, which is the one state that can tolerate it), §6.3.2/§18/§19 (the
Tailscale fork was offered to the owner with **no pairing path** — both branches
are now complete, and what Tailscale costs is stated where the choice is made),
§14a.1 (the backup gate compared *filesystems*, so two volumes on one dying disk
passed it — it now resolves the **physical store**), §13 (the full-sync
predicate was market-driven only, so a Friday success satisfied it all weekend
and balances crossed their own staleness window by construction), and §4/§19
(the last place the runbook still told the owner to stop *implementation* on an
O2 `NO`).

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

In every round, each place the earlier draft was wrong is called out inline
rather than quietly corrected.

---

## 1. What this is

**There is exactly one deliverable: an Android app.** It is the only thing the
owner installs, opens or looks at. The "Mac" this document then talks about on
nearly every page is **not** a second app — it is a headless launchd daemon with
no window, no icon and no interface, which the owner never opens and never
touches again after the one-time setup in §19.

*(Stated first, before any architecture, because the progress reporting on this
project left the owner believing two apps were being built. That was a reporting
failure, but a design a reader can come away from with the same impression has
the same defect — so this line is load-bearing, not a preamble. **One app.**)*

One number — total net worth — rebuilt at least once a day from linked financial
accounts plus a few manually-valued assets, and displayed on that phone. Single
user, one Mac, zero marginal cost.

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
path** — tasks 07/07a/08 and everything downstream of a real Item — and only the
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
   │                        │       (leaves this disk by design,  │
   │                        │        never to a third party §14a) │
   │            Publisher: encrypt (seq, AAD) + PUT               │
   └────────────────────────┼─────────────────────────────────────┘
              write token   │  ciphertext only
                    ┌───────▼──────────────┐        ┌──────────┐
                    │  transport (§6)      │◄───────┤  Plaid   │
                    │  live: current only  │ webhook│ webhooks │
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
the transport **serves only the current value** (§6.2) — over a bounded
provider-side recovery window that §6.2.2 states rather than hides — the phone's
credentials arrive by **runtime pairing** rather than being compiled in (§6.3),
and the two
directions use **different credentials** — the Mac can write, the phone can only
read (§6.2).

Seams (interfaces the rest of the code depends on, never concrete classes):

- `PlaidClient` — link tokens, exchange, item status, holdings, balances. The
  only place Plaid's error taxonomy becomes our states. *(Liabilities dropped in
  rev 9 with credit cards; the seam is where it would return.)*
- `QuoteClient` — `get_quote(symbol) -> (price, as_of)`; the quote must carry its
  own timestamp, because a stale price is precisely the failure being hunted.
- `TokenStore` — narrow interface over secret storage (§2 reservation 3).
- `Store` — repositories over SQLite; append-only observations/snapshots.
- `Publisher` — serialize + encrypt + upload the snapshot, then **read it back**
  and assert the transport is serving what was just published (§9.3). Swappable
  transport.
- `WebhookDrain` — fetch queued webhook events from the transport, **verify
  Plaid's signature locally**, convert to item state changes (§8.4). **Exists
  only on the Cloudflare branch** — Plaid needs a public endpoint to deliver to,
  and the Tailscale branch deliberately has none (§6.3.2). Advisory
  *for the number* — a dropped event can never make the total wrong, because the
  poll floor is what I3 rests on. It is **not** redundant with polling: an
  earlier draft claimed here that "everything it detects, polling eventually
  detects too", which §8.4 then disproves in the same document.
  `PENDING_DISCONNECT`'s `reason` and `disconnect_time` are the counterexample —
  advance warning that no poll can derive.
- `Notifier` — alert delivery (§11).
- `BackupStore` — encrypted archive of the database + token material. It goes to
  **hardware the owner controls but not the Mac's own physical disk** — never to
  a third party, and never to the disk whose death it exists to survive
  (§14a.1). *(Rev 3 called it "local only" here and "never leaves the Mac" in the
  diagram above while §14a.1 required an external or remote destination; review
  was right that one of the two had to go, and it is these two.)*

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

**The envelope, and the exact bytes of the AAD.** *(Specified in rev 8. The
phone has to rebuild the AAD to decrypt at all, and §9.3.1 has to hash it to
identify an object, so "binds these four fields" is not enough — the encoding
has to be one both ends compute the same way.)* The published object is:

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
without the key cannot change it without breaking the tag. A key holder could
seal the two apart, which is §6.2.2's conjunction and not a new exposure. The AAD is the length-delimited tuple

```
LP(x) = uint64_be(byte_length(x)) ‖ x        -- length-delimited: no field boundary
                                             --   is ambiguous, no field can absorb
                                             --   another by containing a separator
aad   = LP(schema_version) ‖ LP(pairing_id) ‖ LP(seq) ‖ LP(published_at)
```

over the UTF-8 bytes of each field **as they appear in the envelope**. The
Worker stores the request body and returns it unchanged — it is a relay (§16),
not a parser — but nothing above depends on that: the AAD is rebuilt from parsed
values, so a re-encoded envelope still decrypts and still identifies.

**What encryption does not buy: currency.** A valid ciphertext stays valid
forever, so an old payload replayed by anyone able to write to the transport
decrypts perfectly — an authentic, stale number, which is precisely this
product's cardinal sin wearing the cipher's own authenticity tag. That gap is
narrowed by `seq` and **I6** in §9.3, not by the cipher — and §9.3 states exactly
how far, since I6 measures against what *that phone* has already seen and the
Mac's own checks run through the transport they are checking.

**And what the tag does not buy: authorship.** GCM is **symmetric** — one key
seals and opens — so a valid tag proves the writer held the payload key, nothing
more. Every freshness claim in this document is therefore scoped to parties
*without* that key; **§6.2.2 decides that boundary explicitly** and says why the
alternative (a Mac-only signing key, the phone holding only its public half) is
named but not built.

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
| **Cloudflare Worker + Durable Object** — Mac `PUT`s ciphertext into one DO; phone `GET`s it (KV keeps only the webhook queue, §6.2.1) | Two distinct bearer tokens (write / read), checked in the Worker | 100k Worker requests/day; DO free tier 100k req + 100k rows written/day, 5 GB. We need ~1 write and a handful of reads a day | **Yes** | **Application state: the current value only** — an overwrite replaces it. **Provider: a 30-day point-in-time recovery window** over the whole object, on by default, with no documented opt-out — bounded, self-clearing, and reachable only by deploying code to the account (**§6.2.2**) | **Recommended** |
| Tailscale — phone reaches the Mac directly over WireGuard | Tailnet device identity, **plus** the payload key, which *is* the read credential on this branch (§6.3.2); no bearer token anywhere | Personal tier, long-standing | **No** — Mac must be awake | **Nothing at all** — there is no third party to retain anything, which after §6.2.2 is a real point of difference rather than a formality | Best on pure security; loses availability **and the webhook accelerator** (§6.3.2) |
| Private GitHub repo (`…-data`) | Fine-grained read-only PAT | Free private repos are mature | Yes | **Every payload ever published**, permanently — and readable with the **same read credential the phone itself carries** | **Rejected** — see above |
| Public static host (Pages, etc.) | None | Free | Yes | — | **Rejected** — publishes net worth |
| ntfy / public pubsub free tiers | None or weak | Varies | Yes | — | **Rejected** — no real auth |

**Recommendation: Cloudflare Workers.** It is the only candidate that is
simultaneously available while the Mac sleeps and free of an *accumulating*
corpus — its recovery window is bounded at 30 days and ages out unattended
(§6.2.2), where Git's grows for as long as the transport runs — and its free
limits sit three orders of magnitude above one user's traffic. It costs one new free account (owner-only, §19) and a small Worker —
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
real rather than theoretical: re-pairing (§6.3) mints a new key, and the rotate
replaces the verifier and deletes the snapshot in one transaction, so the old
key decrypts nothing **the transport will serve**. That is a statement about
live state, not an erasure: Cloudflare keeps a bounded recovery history that no
application route can reach and that expires on its own — **§6.2.2** states
exactly what it holds, what reaching it would take, and why the recommendation
survives it.

Tailscale remains the stronger choice on pure security and stays a documented
swap behind the `Publisher` seam. It costs two things: opening the app away from
home shows a cached copy — which §9 already renders honestly, so it degrades
rather than breaks — and, less obviously, **Plaid webhooks stop being possible
at all**, because there is no public endpoint to deliver to (§6.3.2). The choice
is the owner's (**O5**), and it is now a choice between two fully specified
branches rather than one design and one gesture.

#### 6.2.1 Two stores, because the two states have opposite requirements

*(From review, and it was the sharpest finding of the round: rev 3 put the
pairing verifier and the snapshot in **KV**, then claimed one request replaced
them "atomically", that no failure could leave the old token working, and that
the old phone was locked out "from this instant" — while §8.4.1 of the same
document correctly said KV has no transactions and takes "up to 60 seconds or
more" to converge. Both cannot be true. A security claim that the storage layer
contradicts elsewhere in the same file is not a wording problem; it is a
mechanism that cannot be built.)*

The two pieces of state this transport holds want opposite things:

| State | Access pattern | What it needs | Store |
|---|---|---|---|
| Active pairing verifier + current snapshot | one small unit, replaced together, read by the phone and read back by the Mac | **atomic replace** and **read-your-writes**, or the revocation and integrity claims are fiction | **Durable Object** (SQLite backend) |
| Webhook queue | many independent keys, written once, expired by TTL, acked by delete | cheap unique writes; **at-least-once is already the contract** | **KV** |

So the pairing and the snapshot move into a single Durable Object, and KV keeps
the queue it is actually good at. Cloudflare's own description of Durable
Objects is the property being bought: they are "single-threaded and
cooperatively multi-tasked", with "durable, transactional, and strongly
consistent storage", and each object has "a globally-unique name, which allows
you to send requests to a specific object from anywhere in the world" — one
instance, serialized requests. `rotate` becomes one transaction inside one
object: verifier replaced **and** snapshot deleted, or neither.

**The queue stays in KV deliberately, not by omission.** Eventual consistency
there is already accounted for and already harmless: a webhook is advisory
(§8.4), a duplicate is a no-op insert on `UNIQUE(body_sha256, jwt_iat)`, a lost
one costs advance warning and never the number, and `list` lagging ~60s just
means an event drains a tick later. None of that is true of a revocation.

**Cost, checked rather than assumed** (the zero-spend rule makes this
load-bearing): the Workers Free plan includes Durable Objects with the **SQLite
storage backend** — "Only Durable Objects with SQLite storage backend are
available" on Free — at 100,000 requests/day, 13,000 GB-s/day, 5 million SQLite
rows read/day, 100,000 rows written/day and 5 GB total storage. This design
writes on the order of one row a day and reads a handful. One property matters
more than the headroom: **exceeding a free-tier limit fails the operation with
an error rather than generating a bill.** There is no overage to accidentally
incur, which is the only reason a metered-looking service is admissible at all
under the zero-spend rule.

**The Mac's credential surface does not change, which is the other reason this
is admissible.** §8.4.3 rejected Cloudflare Queues because its pull consumers
authenticate with an account-scoped **Cloudflare API token**, and that argument
would be self-defeating if the Durable Object needed one. It does not: the DO is
reached through a **Worker binding**, so the Mac still presents exactly one
bearer token to our own Worker, and **at runtime holds no Cloudflare account
credential** — nothing the sync loop, the publisher or the drain touches can
reach the control plane.

**The deploy-time credential is real, though, and rev 5 wrote as if it were
not.** *(From review, which checked the runbook against the claim.)* §19 has the
owner run `wrangler deploy`, and later `wrangler secret put` to rotate the write
token. Per Cloudflare's Wrangler documentation, `wrangler login` **persists an
OAuth access token and a refresh token on this machine** — by default in
plaintext TOML under the global Wrangler config directory; `--use-keyring`
stores them AES-256-GCM-encrypted with the key in the OS keychain, which is
better but still on the machine — and `wrangler logout` **invalidates the token
at Cloudflare and deletes the local copy**. "This Mac never holds account
access" was therefore false as stated.

**And the CLI token is only one of the two doors that login opens.** *(From
review, one revision later — rev 6 bracketed the token and treated
`wrangler whoami` as proof the account was closed.)* The default flow signs a
**browser** in to the Cloudflare dashboard, and that session is a separate
object from the OAuth token: Cloudflare's logout documentation covers the token
and Wrangler's stored credentials, while dashboard sessions have their own
lifetime (**72 hours of inactivity** by default) and their own revocation UI. A
browser still signed in on this Mac can redeploy the Worker, which is exactly the
capability the tiering here is about — so `whoami`, which reports only on
Wrangler, cannot certify it.

So the lifecycle is specified rather than assumed, and it covers **both layers**
(**§19 step 3a.0**): `wrangler login --device --browser=false --use-keyring` —
the device-code grant, so the browser that signs in is on *another* machine, and
`--browser=false` because **device mode still opens the verification URL here
without it** *(from review; Cloudflare documents both halves)* — → do the one
thing → `wrangler logout` → `wrangler whoami`; and where the browser step happens
here anyway (creating the account is a dashboard action), an explicit sign-out
**verified from a second device** via My Profile → Sessions, because the
dashboard will not let you revoke the session you are using. Logout invalidates
the token server-side and a revoked session is gone server-side, so together that
is a real close rather than a tidy-up. The accurate claim, and the one the rest
of this document relies on, is: **account-level access — CLI token and dashboard
session both — is present only during an owner-run operation and is not retained
between them.**

Staying signed in is a legitimate alternative, but it is a *change to the trust
boundary*, not a convenience. This whole section separates an **application
credential** (the write token, on the Mac) from the **control plane** (owner-only,
and the only route to §6.2.2's recovery window). A permanent login of either kind
collapses that separation onto one machine: compromising the Mac would then also
yield the ability to redeploy the Worker and reach the window — and, because the
Mac holds the payload key as well (§6.1 is symmetric), **both halves of §6.2.2's
conjunction at once**. The runbook states that where the choice is made, rather
than leaving it as a default nobody decided.

*(Rejected alternative: keep KV and weaken the claims — "revocation within ~60
seconds", read-back retried through the propagation window before alerting. It
is implementable, and it was the cheaper edit. It loses on the two things this
product is about. A stolen phone would stay readable for a bounded-but-real
window with no way to shorten it, and the read-back — the Mac's only evidence
that the transport is serving what it published — would have to treat a genuine
mismatch and a propagation lag identically, which turns the one alert that means
"someone is tampering" into an alert that fires for nothing. A tamper alert
nobody trusts is worse than no tamper alert.)*

#### 6.2.2 What Cloudflare retains anyway: the 30-day recovery window

*(From review, and the catch lands on this section's own axis: §6.2 chose a
transport on **what it retains**, then §6.2.1 picked a store whose retention the
text never checked. Cloudflare documents that a SQLite-backed Durable Object can
be restored "to any point in time in the past 30 days", and that this covers the
**entire embedded database** — the SQL data and the key-value data. It is **on
by default** for every SQLite-backed object, with no documented opt-out and no
configurable window. The Free plan offers *only* the SQLite backend (§6.2.1), so
this is not avoidable by choosing a different one.)*

**Three claims in this document are scoped by that, not deleted:** §6.2's table
row, §6.2's blast-radius paragraph, and §6.3.1's "deleting the stored object
removes the one thing its key could still decrypt". Each is true of **live
application state** — what the transport will serve on any request anyone can
make — and none is true of Cloudflare's recovery history. That distinction is
now written into all three rather than implied here.

**What the window contains, precisely.** Everything the object held over 30 days:
on a once-daily publication, up to ~30 **ciphertexts**, plus the pairing rows.
The pairing rows are `SHA-256(read_token)` verifiers, never tokens (§6.3.1).
What is *not* in there, because it is never sent to the transport at all: the
**payload key** (Mac and phone only), the **read token** itself, the **write
token** (a Worker secret, not object state), and every Plaid credential. A party
who reads the entire recovery history and holds nothing else therefore holds
**bytes they cannot decrypt**.

**What reaching it would take.** The PITR methods — `getCurrentBookmark`,
`getBookmarkForTime`, `onNextSessionRestoreBookmark` — exist only on
`ctx.storage` **inside the Durable Object class**, and there is no documented CLI
or REST route that restores a Durable Object from outside it. So a restore means
**deploying code into the Worker**, which needs the owner's own Cloudflare
account login — access this machine holds **only for the duration of an owner-run
`wrangler` operation and not between them**, provided the bracket closes the
**browser session** as well as the CLI token (§6.2.1, §19 step 3a.0; rev 6's
bracket closed only the token, which left a 72-hour dashboard session standing).
It is a different and far more powerful thing than the write token the Mac does
hold.
None of the six routes in §16
calls a PITR method, and **task 20 carries "no route and no handler invokes
PITR" as an acceptance criterion**, so the application exposes no path to it. A
stolen phone, holding a revoked read token and no account access, has no path at
all.

**The residual, named.** Someone holding **both** an old phone's payload key
**and** the owner's Cloudflare account can recover up to 30 days of daily
snapshots, and can make a **revoked pairing live again** by restoring an earlier
bookmark. Three things bound it:

- It is **bounded and self-clearing.** The window is 30 days and it moves;
  nothing accumulates behind it and no action is needed for it to expire.
- It needs the **conjunction**. Account access alone yields undecryptable
  ciphertext; the old phone key alone yields nothing the transport will serve.
- For the scenario this design actually worries about — a stolen phone — the
  marginal disclosure is close to nothing, because that phone already holds the
  rendered history window in cleartext on its own disk (§6.2, blast radius). The
  genuinely new capability is **pairing resurrection**, and it belongs to whoever
  compromises the Cloudflare account, not to whoever takes the phone.

**A restore is sometimes loud, and rev 5 claimed more than that.** *(From
review.)* That revision said a rollback is caught at **both** ends and bounded by
the publish interval. Neither half survives contact with the mechanism:

- The **read-back** inspects what the Mac has just written (§9.3), so a restore
  landing between publications is overwritten by the next `PUT` before any read
  occurs — the Mac's own tick erases the evidence before looking for it. §9.3
  now adds a **pre-write compare** to close exactly that window, but it catches
  only a restore still live at the next tick.
- **I6** rejects `seq < last_seq` — a rollback below a sequence **the phone has
  itself seen**. A phone lagging behind the Mac accepts a restored older payload
  as new.
- And against a party holding the Cloudflare account, no check here binds: the
  same access that restores a bookmark can redeploy the Worker or repeat the
  restore after each publication. The publish interval bounds an accident, not
  an adversary — and the residual named above *is* an adversary.

What is true, and is what the residual actually rests on: a one-off restore is
**overwritten by the next successful publication** (§13); it is **detected, by
the Mac, if it is still live at that tick**; and — detected or not — the restored
payload carries its own age, so it ages out on the phone rather than passing for
current (§9.1). The exposure is a resurrected pairing reading **old ciphertext
it could already decrypt**, not a stale number presented as fresh.

**Where that last clause stops being true — decided here, because it is a threat
model choice and not wording to defer.** *(From review.)* The payload is sealed
with **AES-256-GCM under a key the Mac and the phone both hold** (§6.1).
Symmetric authentication proves the sealer held that key and nothing else.
Replay and restore therefore stay honest against anyone who does *not* hold it:
the only ciphertexts they can serve are ones the Mac already made, carrying the
`published_at` the Mac sealed into them. But the conjunction named just above
holds **both** halves, and against an **active** pairing that party can seal an
old total under today's `published_at` and a higher `seq`; the phone, which
verifies only the key, takes it. (Against a **revoked** pairing it still fails —
the phone rejects a foreign `pairing_id`.)

**Decided: freshness is guaranteed against any party without the active payload
key, and the active-key-plus-control-plane case is out of scope.** That scope is
not a dodge — it is the party this section exists for. The 30-day window is
reachable only through the control plane, and the control plane holds no payload
key. And a party holding **both** halves can already read every number this
product publishes — the account access serves them the object, the key opens it —
so what forging *adds* to what they have is misrepresenting one number's **age**
to the owner's own phone. Stated without softening, because it is a real harm to
a product whose premise is the honesty of the age: that attack works, and this
design declines to defend against it at the price of a second key, on the
grounds that it requires two independent compromises to reach a display.

**With one consequence that decides §19 step 3a.0 rather than merely informing
it.** The Mac holds the payload key too — §6.1 is symmetric, and `pairing.key_ref`
lives on this machine so the daily publication can be sealed at all. A Mac that
*also* keeps a standing Cloudflare credential therefore holds **both halves of
the conjunction on one machine**, and this residual stops needing two
independent compromises to reach. That is what makes the login/logout bracket of
§19 step 3a.0 load-bearing for this section instead of hygiene, and it is why
that step now closes the browser session as well as the CLI token.

The remedy, if the boundary ever stops being acceptable, is named so the choice
stays open rather than being rediscovered: a **Mac-only signing key**, with the
phone holding only the public half, makes the envelope unforgeable without the
Mac and leaves the payload key doing confidentiality alone. It is deliberately
not built — it adds a key to mint, escrow, rotate and re-pair for a threat that
needs the phone's key *and* the account, and the last three revisions of this
document have been about mechanisms described more confidently than they were
built.
**O5 carries the boundary**, so the owner weighs it beside the retention window
rather than after it.

**Against the transport this replaced**, since retention is the criterion: Git
retains every payload ever published, **forever**, readable with the **read
credential the phone itself carries**. Cloudflare retains **30 days**, reachable
only by **deploying code to the account**. Unbounded vs. bounded on one axis,
application credential vs. control plane on the other; both point the same way.
The recommendation stands — but it now stands on this paragraph instead of on
"current value only".

**The branch that retains nothing is Tailscale**, because there is no third
party to retain anything (§6.3.2). After this section that is a genuine
distinguishing property, so it is stated in **O5** where the choice is made: an
owner who wants no provider-side window anywhere in the picture has exactly one
option here, and it costs Mac-must-be-awake and the Plaid webhook accelerator.

#### 6.3 Provisioning the phone's secrets: pairing, not compilation

An earlier draft injected the payload key and the transport credential into the
APK at build time. Review rejected that too, and correctly: it makes the APK
itself a bearer artifact for the owner's net worth, makes rotation require a
rebuild-and-reinstall, and bypasses the platform's protected secret storage.

Instead, **the app ships with no secrets at all** and is provisioned once at
runtime:

1. On the Mac, `networth pair` mints a fresh payload key and a `pairing_id`,
   plus — **on the Cloudflare branch only** — a read-only transport token, and
   renders them as a QR code in the terminal (with a typed fallback string).
   *(The payload key is minted on both branches: §6.1 encrypts the payload
   regardless of transport, so "which transport" never decides whether the phone
   needs provisioning, only what else is in the QR. Rev 3 missed that and
   offered the owner a Tailscale fork with no pairing path at all; §6.3.2 now
   defines it.)*
2. The phone scans it once, on-screen, on the owner's own desk — the material
   never crosses a network during pairing.
3. The app stores it via `flutter_secure_storage`, backed by the **Android
   Keystore**, so the OS protects it rather than a string constant in a DEX file.
4. Rotation, revocation and re-pairing are runtime operations. No rebuild, no
   reinstall, no version bump.

The release APK is therefore not a bearer artifact: losing it leaks nothing.

#### 6.3.1 The Worker has to be told — the control path (Cloudflare branch)

*(From review, twice. Rev 2 minted a read token on the Mac and then claimed
re-pairing "invalidates the read token", with no mechanism by which the Worker
could ever learn either fact — rotation was not slow, it was **not
implementable**. Rev 3 built the routes but put their state in KV, so the words
"atomically" and "from this instant" described something the store cannot do
(§6.2.1). Both are the same class of error: a revocation story that cannot run
is worse than none, because it gets believed.)*

**All of the state below lives in one Durable Object** — the active pairing
verifier and the current snapshot together, which is what makes "replace the
pairing and drop the snapshot" a single transaction rather than a hopeful
sequence of two writes.

A fourth route, authenticated by the credential only the Mac holds:

| Route | Auth | Body | Effect |
|---|---|---|---|
| `POST /pairing/rotate` | **write** token | `{pairing_id, read_token_verifier}` | atomically replaces the active pairing **and deletes the stored snapshot** |
| `POST /pairing/revoke` | **write** token | — | clears the active pairing and deletes the stored snapshot |

**The Worker stores a verifier, never a token.** `read_token_verifier =
SHA-256(read_token)`; `GET /snapshot` hashes the presented bearer and compares
in constant time. Read tokens are 256-bit random strings, so a plain hash is
sufficient and a slow KDF would buy nothing — there is no guessable password
here. A leak of the Worker's stored state therefore does not yield a working
read credential, and the Mac keeps the only copy of the token itself (in
`~/agents/secrets/`) until the phone scans it.

**Rotation order, and what each failure leaves behind.** The sequence is chosen
so that no step can leave the *old* token working:

1. Mint locally; write the `pairing` row as `PENDING`. Nothing has changed
   anywhere else, so a crash here is a no-op.
2. `POST /pairing/rotate`. One request into one Durable Object, handled in **one
   transaction**: the verifier is replaced and the snapshot deleted together, or
   neither happens. There is no interleaving in which a new verifier coexists
   with a snapshot the old key can still decrypt. **On failure, abort and print
   why** — the old pairing is still active, the QR is *not* rendered, and the
   owner re-runs the command. Never render the QR before this returns 2xx: a
   phone holding material the Worker does not know is indistinguishable to the
   owner from a broken transport.
3. On success, mark the new row `ACTIVE` and the old one `revoked_at = now`.
   **From this instant the old phone is locked out** even though the new phone
   has not scanned anything yet. That ordering is deliberate: the case that
   matters is a *stolen* phone, and it must not stay readable while the owner
   walks to their desk. This claim is only true because of the store: every
   `GET /snapshot` is a request to the same single-threaded object, serialized
   after the transaction that revoked the old verifier — there is no second
   replica that could still be answering with the old one. *(Locked out of
   everything the transport will serve, which is every request any phone can
   make; Cloudflare's 30-day recovery history is a separate layer with no route
   into it — §6.2.2.)*
4. Publish immediately under the new key with the next `seq`. If this fails, the
   pairing is still correct — the phone pairs and shows "no data yet" until the
   publish job retries on the next tick (§13), and `doctor` reports the
   publication as overdue (§11). Degraded, visible, self-healing.
5. Render the QR.

**Partial failure, enumerated — including the one that has no answer at the
Worker.** The transaction removes every *server-side* split state, but the Mac
still has a network in front of it, so:

| Failure | What the Worker holds | What the Mac does |
|---|---|---|
| Crash at step 1 | old pairing, old snapshot | nothing happened; the `PENDING` row is inert. Re-run |
| `rotate` returns non-2xx | old pairing, old snapshot (transaction rolled back) | abort, print the status, **no QR**. Re-run |
| **`rotate` times out / connection dies — outcome unknown** | either state, and the Mac cannot tell which | mark the row `UNCERTAIN`, **suspend publishing**, alert, and tell the owner to re-run `networth pair` |
| Crash after 2xx, before the local row is marked `ACTIVE` | new pairing, no snapshot | same as `UNCERTAIN` on the next run — and re-running converges either way |
| Publish (step 4) fails | new pairing, no snapshot | pairing is correct; publish retries next tick; `doctor` reports it overdue |

The uncertain case is the only interesting one, and it is resolved by
**re-running rather than by asking**: a second `networth pair` mints fresh
material and rotates again, which is correct whichever way the first attempt
resolved. That is why there is no `GET /pairing/status` route — a route whose
only purpose is to tell the Mac something it can simply overwrite is a route
that can also be abused.

**Suspending publishing there is deliberate.** If an uncertain rotate did land,
the Mac's local `ACTIVE` row is stale and it would keep publishing under a key no
phone can use, while the read-back reported success — it compares the Mac's own
fingerprint (§9.3.1) against the object the transport serves, and both are the
Mac's, so nothing about the *phone's* ability to decrypt is in that comparison at
all. That is a green indicator over a broken pipe: this product's cardinal
sin, arriving through the control path. Refusing to publish makes it loud and
self-healing instead.

**Deleting the snapshot on rotate is not housekeeping.** The scenario is a
stolen phone, and it holds the old payload key. Revoking its token stops it
fetching *new* ciphertext; deleting the stored object removes the one thing its
key could still decrypt if it kept fetching. Both halves are needed, which is
why they are one route.

What that deletion does **not** do is erase Cloudflare's recovery history
(§6.2.2). The honest form of the claim: the rotate ends the stolen phone's
access to anything the transport will serve, and the 30-day window behind it
ages out unattended and is unreachable without deploying code to the owner's
account. Deletion here is a revocation, not a shredder, and the runbook (§19
step 3a) says so where the owner would otherwise assume otherwise.

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

#### 6.3.2 The same control path on the Tailscale branch

*(From review, and it was a fair catch about how an open question was left open:
**O5** offered the owner Tailscale, §6.1 requires the payload to be encrypted on
**every** transport, and yet everything above — pairing, rotation, revocation —
was written as a Worker flow, with the runbook saying to skip the whole step if
Tailscale won. That is not a fork, it is one designed branch and one gap. Either
the fork closes or both branches are real; the owner should get to keep the
choice, so both branches are real.)*

The mechanism, end to end:

- **Transport.** `Publisher` encrypts exactly as in §6.1 and writes the
  ciphertext to a local object. A small HTTP server bound to the Mac's **tailnet
  interface** serves `GET /snapshot`. The phone fetches it over WireGuard. There
  is no third party and nothing is stored off the Mac.
- **No TLS requirement, and therefore no free-tier question.** The tailnet link
  is already end-to-end encrypted and the payload is encrypted underneath it.
  `tailscale serve` can front the port with a tailnet TLS certificate and
  identity headers if the owner has certificates enabled — a strict improvement,
  never a dependency, so nothing here rests on which Tailscale features a given
  plan includes.
- **Authentication is two layers, neither of them a bearer token.** Tailnet
  membership decides who can reach the port; **the payload key is the read
  credential** — a tailnet device that never scanned the QR receives ciphertext
  it cannot decrypt. This is why the branch has no read token, no verifier and
  no `SHA-256` comparison: there is nothing to present.
- **Pairing.** `networth pair` mints the payload key and `pairing_id` as always;
  the QR carries those plus the Mac's tailnet name instead of a read token.
- **Rotation and revocation are one local SQLite transaction** — new pairing
  `ACTIVE`, old `revoked_at`, served object dropped. The atomicity problem of
  §6.2.1 simply does not exist here: one process, one database, one writer.
  There is no `UNCERTAIN` state either, because there is no network call whose
  outcome the Mac can fail to learn. **`networth revoke` is immediate in the
  literal sense** on this branch.
- **Lost phone.** `networth revoke`, then — recommended and owner-only — remove
  the device from the Tailscale admin console, which is the stronger control
  because it revokes reachability rather than content. As on the Cloudflare
  branch, the ciphertext already cached *on* the stolen phone is beyond recall;
  revocation stops the next fetch, never the last one.
- **`seq` and replay defence are unchanged** (§9.3): same AAD, same monotonic
  counter, same phone-side refusal.
- **Read-back still runs**, against the served endpoint rather than the loopback
  file, so it exercises the serving path that the phone actually uses.

**What this branch gives up, stated where the choice is made.** There is no
public endpoint, so **Plaid webhooks cannot be delivered at all** — the drain
(§8.4) does not exist on this branch and task 12a drops with it. **I3 then rests
entirely on the hourly poll floor**, which is exactly what I3 was worded to
promise, so no guarantee is broken; what is lost is the *accelerator*:
`PENDING_DISCONNECT`'s `reason` and `disconnect_time`, i.e. advance warning that
a connection is scheduled to die, which no poll can derive. Combined with the
Mac-must-be-awake availability cost, that is the honest price of the branch, and
it now appears in **O5** so the owner is choosing with it visible rather than
discovering it afterwards.

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

**Running the whole sync in a Cloudflare Worker on a Cron Trigger — dismissed,
and recorded so it is not proposed again.** *(Checked in rev 9, after the owner
asked whether the Mac could be eliminated entirely.)* It is the obvious "no
computer of my own" answer and it fails on the free plan for two independent
reasons: each invocation is capped at **10 ms of CPU** — network wait does not
count, but parsing a holdings response and doing AES-GCM over the payload is
real CPU — and **failed scheduled invocations are not retried**, which breaks
the once-a-day guarantee (§1) outright rather than degrading it. Paid Workers is
$5/month, so the zero-money rule (§4) ends the discussion before the technical
one starts. It would also put the Plaid `client_id`/`secret` — the master
credential, not a per-Item token — inside the same third party this design
deliberately keeps them away from (§6.2.2). The daily guarantee needs a host
that is awake and allowed to be slow; that is what §13 assumes.

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

pairing(id, created_at, key_ref, read_token_ref,   -- refs only, never the material
                                                   --   read_token_ref NULL on the Tailscale
                                                   --   branch: no bearer token exists (§6.3.2)
        state,                                     -- PENDING | ACTIVE | REVOKED
                                                   --   | UNCERTAIN (rotate outcome unknown —
                                                   --   suspends publishing, §6.3.1)
        registered_at,                             -- when the Worker accepted the verifier
        revoked_at)                                -- §6.3.1

publication(                                       -- §6 audit trail
  id, snapshot_id, pairing_id, seq UNIQUE,         -- monotonic, NEVER reset (§6.3.1); replay defence (I6)
  schema_version, published_at, transport,
  outcome,                                         -- UNCONFIRMED | LANDED | NOT_LANDED | SUPERSEDED.
                                                   --   States, transitions and the resolver are
                                                   --   NORMATIVE in §9.3.1 and defined ONLY there.
                                                   --   Two things a reader will otherwise assume:
                                                   --   a 2xx is not the only confirmation (a later
                                                   --   observation of this row's fingerprint is the
                                                   --   other), and "the last successful publication"
                                                   --   everywhere else in this document means LANDED
  outcome_resolved_at,                             -- when it left UNCONFIRMED; NULL while pending
  outcome_resolved_by_seq,                         --   the publication whose pre-write read or 2xx
                                                   --   settled THIS row; NULL when its own 2xx did
  error,
  payload_fingerprint,                             -- §9.3.1 — SHA-256 over the WHOLE authenticated
                                                   --   object (every AAD field, nonce, ciphertext,
                                                   --   tag), not over the JSON envelope. Identity
                                                   --   for both checks below; rev 7's
                                                   --   (seq, SHA-256(nonce ‖ ciphertext)) missed
                                                   --   every AAD field GCM authenticates
  prewrite_state,                                  -- MATCH | ROLLBACK | FOREIGN | ABSENT
                                                   --   | UNAVAILABLE (§9.3) — what the transport was
                                                   --   serving BEFORE this write; the only way a
                                                   --   rollback BETWEEN publications is ever seen
  prewrite_fingerprint, prewrite_seq,              --   what was observed; the seq is a diagnostic,
                                                   --   the fingerprint is the comparison (§9.3.1)
  readback_state,                                  -- OK | MISMATCH | UNAVAILABLE (§9.3) — a failed
                                                   --   request is not evidence of a wrong value.
                                                   --   Runs ONLY after a 2xx (§9.3.1)
  readback_attempts,
  readback_fingerprint, readback_seq)              -- what the transport served straight back

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

**The accelerator — a webhook drain, zero marginal cost.** §6 already stands up
a Cloudflare Worker for the transport, so the receiver is one extra route on
infrastructure the project has anyway. Verification happens **on the Mac**: the
verification endpoint needs `client_id` + `secret`, and putting those in a
Worker would scatter the Plaid credential to a second place to save nothing.

**The accelerator exists only on the Cloudflare branch.** Plaid delivers
webhooks to a public HTTPS endpoint, and the Tailscale branch deliberately has
none, so everything from here to §8.4.3 is conditional on **O5** (§6.3.2). The
floor is not conditional, which is the point of describing it as a floor.

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
same key twice. This is the one thing KV is kept for (§6.2.1): write-once keys,
TTL expiry, an idempotent consumer, and a payload whose loss costs a warning
rather than the number. The pairing verifier and the snapshot had none of those
properties, which is why they moved to a Durable Object and this did not.

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
transport that silently serves a cached older object, or a provider-side restore
that rolls the whole object back (§6.2.2) — could roll the phone back to a
comfortable old number that verifies perfectly.

- Every publication carries a **monotonic `seq`** (§7), inside the authenticated
  data along with `pairing_id`, `schema_version` and `published_at`, so none of
  them can be lifted from one payload into another.
- The phone persists `last_seq` and **refuses any payload with `seq < last_seq`**,
  keeping the newer cached snapshot. `seq == last_seq` is not an error — it is
  the normal "nothing new yet" signal that §9.1 uses to blame the Mac rather than
  the network. **This is a test against what this phone has already seen** — a
  rollback to a `seq` it never fetched passes it; see the bounds below.
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
after a publication the transport said it applied: it re-fetches the object and
asserts that what comes back is **the object it just wrote** — the full
`payload_fingerprint` of §9.3.1, not the `seq` alone. A mismatch means the
transport is serving something other than the current value, and that raises a
Mac-side alert (§11), recorded in `publication.readback_state` /
`readback_fingerprint`. It costs one extra request per day.

**`seq` alone was never an identity, and this check used to rest on it.** *(From
review, which caught rev 7 asserting in one place that anything able to write can
choose any `seq`, while the read-back three sections away still compared only
`seq`.)* A same-`seq`, different-ciphertext object — or the same ciphertext
re-declared under a changed `pairing_id` or `published_at`, which the phone will
refuse to decrypt — passed as `OK` under the old rule. §9.3.1 gives both checks
one identity so they cannot drift apart again.

**And it runs only after a 2xx**, which is the other half of not crying wolf. A
`PUT` that failed outright leaves the previous object correctly in place; a
read-back there would find "something other than what I wrote" on an ordinary
failed write and file it as tampering. An attempt whose answer was never seen is
not the read-back's business either — resolving *that* is the pre-write read's
job (§9.3.1), and it is the only check that can do it.

**The read-back is only meaningful because of §6.2.1.** *(From review: on a KV
snapshot an immediate post-write read could legitimately return the previous
value — up to 60 seconds or more — so a "mismatch" would have meant "tampering
**or** normal propagation", indistinguishably. An integrity alert that fires
routinely is not an integrity alert.)* Reads and writes go to the same Durable
Object, serialized, so the read-back observes the write it follows and an object
that is not the one just written has exactly one meaning.

That leaves one distinction the schema has to carry, because collapsing it would
re-introduce the same noise from the other direction:

| Outcome | Meaning | Response |
|---|---|---|
| `OK` | the object served back has the fingerprint just written (§9.3.1) | — |
| `MISMATCH` | it serves anything else — a different `seq`, the same `seq` over different ciphertext, or our ciphertext under altered authenticated fields | **integrity alert** (§11) — this is the tamper/rollback signal |
| `UNAVAILABLE` | the read-back could not be performed (timeout, 5xx) | retried up to 3 times over ~30s, then recorded and surfaced as **transport health**, not integrity |

A failed *request* is not evidence of a wrong *value*. Filing it as one would
teach the owner to ignore the row that matters.

**A check that runs after a write cannot see a change between writes.**
*(From review, which found this gap inside rev 5's own reassurance about §6.2.2.)*
The read-back inspects the object the Mac has just written, so a rollback landing
while the Mac is idle is invisible to it:

- the Mac publishes `seq = 100`, reads back 100 → `OK`;
- a provider-side restore (§6.2.2) rolls the object back to `seq = 99`;
- at the next tick the Mac writes `seq = 101` **first**, then reads back 101 →
  `OK`.

The Mac never observed 99, nothing in that sequence looks anomalous to it, and
the restored value was the one the transport served for most of a day.

So `Publisher` also **reads before it writes**. *What it compares against* is the
part rev 6 got wrong, so that is what the rest of this section is about; §9.3.1
then states it normatively, in one place, for the same reason.

**The last `publication` row is the wrong thing to compare against.** *(From
review.)* That row records an *attempt*, and `publication` keeps failed attempts
on purpose because they are audit state. Comparing the live object to it files
three ordinary timelines as attacks:

- an attempt that **failed without changing anything** leaves the last row at
  `seq = 101` while the object is still the perfectly correct `100` → read as
  `ROLLBACK`, though nothing rolled back;
- **`rotate` and `revoke` delete the snapshot deliberately** (§6.3.1), so the
  next pre-write read finds nothing → read as a deletion "the Mac did not
  perform", which it did. Every rotation would fire the alarm, by construction;
- a `PUT` whose **response was lost** may have landed. Against the last
  *successful* row, the Mac's own write comes back as an unexpected higher `seq`
  → read as `FOREIGN`, the gravest alert here, fired at itself.

A check that cries wolf on normal operation is worse than no check: it teaches
the owner to dismiss the one case that matters.

**So the Mac keeps an expected-live set, and asks two questions instead of one.**

The **expected-live set `E`** is what the transport should be serving right now,
computed from local state alone: the newest publication confirmed landed, plus
every attempt after it whose answer was never seen — the Mac cannot distinguish
"did not apply" from "applied, answer lost", so it declines to guess and treats
both as *possibly live* — or `ABSENT`, when a local delete (§6.3.1) or the
absence of any publication accounts for an empty object.

**Provenance** answers the other question — *whose bytes are these?* — and needs
an identity `seq` cannot provide, since anything able to write can write any
`seq`. That identity is the `payload_fingerprint` of §9.3.1: one hash over the
whole authenticated object, so it covers exactly what the phone's decryption
covers.

Both, and the ordering rules that make the local records complete rather than
best-effort, are defined once in **§9.3.1**; the rest of this section is what
they are for. `E` has one element in the ordinary case, grows only across a lost
response, and returns to one element at the next read that resolves it.

Classification is then mechanical:

| Observed | State | Response |
|---|---|---|
| an object whose fingerprint is in `E` — or no object where `ABSENT ∈ E` | `MATCH` | — |
| **ours, but not in `E`** — a superseded publication of this Mac is live again | `ROLLBACK` | **integrity alert** (§11); publish anyway (the number still has to move) and record the fingerprint observed |
| **no object**, and no local delete or first-publication explains it | `ABSENT` | **integrity alert** — a snapshot vanished with nothing local to account for it |
| a fingerprint **no local row carries**, or bytes that are not an envelope at all | `FOREIGN` | **integrity alert**, the gravest: something that is not this Mac wrote to our object |
| the read could not be performed | `UNAVAILABLE` | **transport health, never integrity** — the read-back's rule, for the read-back's reason |

**The pre-write read is also how an unconfirmed attempt gets resolved**, and rev
7 left that half-built. *(From review.)* It said such a row "stops being
`UNCONFIRMED`" while offering no state to move it to: `LANDED` is false,
`UNCONFIRMED` keeps it in `E` forever, and deleting the row discards the audit
this table exists to keep. §9.3.1 now carries the terminal outcomes and the rule
that settles **every** pending row from one observation — not only the row that
was observed — which is what makes the collapse of `E` a fact rather than a hope.
An observation that resolves nothing — a `ROLLBACK`, a `FOREIGN`, an
`UNAVAILABLE` — leaves them all pending: an alert is not evidence about which of
our own writes landed.

**One ambiguity, named rather than papered over.** A restore that rolls the
object back to **another member of `E`** — a publication of ours from inside a
lost-response gap — is not distinguishable from the later attempts having simply
never applied, and this design reads it as the latter. That is the mundane
explanation, and choosing the other one would raise an integrity alert on every
ordinary timeout, which is the noise failure this section keeps refusing. The
cost is stated so it is not discovered later: inside a lost-response window a
rollback to one of our own pending publications is read as a write that never
applied, and the audit row says `NOT_LANDED` (§9.3.1) for an attempt that may
have applied and been rolled back. Every rollback to a publication *outside* `E`
— which is every rollback at all in the ordinary single-element case — is still
`ROLLBACK`, and a restore that stays live is still caught at the following tick.

It costs a second request a day and needs no new route: `GET /snapshot` already
accepts the write token (§16) so the Mac can read its own writes back.

**What the pair of checks bounds, and what it does not.** Together they close the
idle window for a restore that is *left in place*: the Mac notices at its next
tick, which on a daily publication is up to one publish interval. They do not
make a rollback impossible to miss, and this document no longer implies they do.

- A rollback **undone before the next tick** — or re-applied after the pre-write
  read and before the write — is observed by neither end.
- **I6 is relative to what the phone has seen**, not to what the Mac published.
  A phone that never fetched `seq = 100` accepts a restored `99` as new, because
  to that phone it *is* new.
- Against a party holding the Cloudflare account, neither check is a bound at
  all: the access that restores a bookmark can equally redeploy the Worker to
  report whatever the Mac wants to hear, or repeat the restore after every
  publication. **A check that runs through the transport cannot police whoever
  owns the transport.** The publish interval bounds an accident, not an
  adversary.

**What holds without detecting anything** is the part the product's promise
actually rests on: freshness travels **inside** the encrypted payload, never in
the transport's liveness. A restored old snapshot carries its own `published_at`
and its own deadline (§9.1), so the phone renders it as what it is — a copy going
stale — and it crosses `COPY_STALE` on schedule with no rollback detection
involved. A rollback can cost the owner a *current* number; it cannot dress an
old one up as current.

**Against whom, though — because that claim has a boundary and rev 6 stated it
without one.** *(From review.)* AES-256-GCM is **symmetric** (§6.1): a valid tag
proves the writer held the payload key, not that the writer was this Mac. So the
paragraph above holds against every party **without the active payload key** —
which includes the provider performing a restore and includes a control-plane
holder, both of whom can serve a past ciphertext but cannot mint one carrying
today's `published_at`. Against a holder of the *active* key it does not hold at
all. §6.2.2 is where that boundary is decided and accepted, so it is not
re-argued here.

On the Tailscale branch the read-back reads back through the served endpoint
(§6.3.2), which is local and consistent by construction; the three states and
their meanings are unchanged.

Stated precisely, because a partial defence described as a whole one is its own
kind of lie: the two Mac-side checks catch a transport that is *globally*
serving stale or wrong content, at the moments the Mac looks. They cannot catch
an edge serving only the phone an older object — different edge, different
cache. That case is caught by the phone,
warned about on the phone, and reaches the owner when he looks at the phone,
which is where he was already looking at the number.

### 9.3.1 Publication identity and outcome — the normative definitions

*(New in rev 8, at review's suggestion — and the useful half of that suggestion
is its diagnosis. The publication outcome, the identity of an object, the
expected-live set, the read-back and the acceptance criteria in `tasks/` had
become **one state machine explained in five distant places**, and rev 7's
defects were drift between those explanations rather than errors inside any one
of them. This subsection is the single normative source for **the identity of an
object, the outcome states, the ordering rules, `E` and the resolver**: §7's
schema names the values it stores, the tables above classify an observation, and
tasks 19 / 19a point here — none of them defines these again. Scoped that way on
purpose: a section claiming to be the only place anything is said would be the
next sentence this document could not cash.)*

**Identity.** One hash, over the whole authenticated object:

```
payload_fingerprint = SHA-256( LP("networth/publication/v1")  -- domain separator
                             ‖ LP(aad)                         -- §6.1: the bytes GCM seals
                             ‖ LP(nonce)
                             ‖ LP(aead_output) )               -- ciphertext ‖ 16-byte GCM tag
```

`LP` and `aad` are §6.1's. Three properties rev 7's
`(seq, SHA-256(nonce ‖ ciphertext))` did not have:

- **It covers everything GCM authenticates.** *(From review.)* The tag is
  computed over the AAD **and** the ciphertext, so an object with an altered
  `pairing_id`, `schema_version` or `published_at` is a different authenticated
  object — one the phone refuses to decrypt. Under the old pair it carried our
  `seq` and our ciphertext bytes, so the Mac called it `MATCH`: the two ends
  disagreed about whether the same object was ours, and the end that could have
  raised the alert was the one saying nothing.
- **`seq` is inside it**, so identity is one value rather than a pair — and our
  own ciphertext re-declared under a different `seq` still comes out `FOREIGN`,
  which is what it is.
- **It is over values, not the envelope's JSON**, so it inherits neither
  §8.4.2's whitespace trap nor a dependency on the transport returning bytes it
  never promised to preserve.

Two rules keep it from misfiring on ordinary operation:

1. **Always recomputed from the object as observed**, never from local
   configuration. Recompute with *today's* `schema_version` and the first
   version bump files every object published before it as foreign.
2. **Bytes that do not parse as the §6.1 envelope have no fingerprint**, and are
   `FOREIGN` rather than `UNAVAILABLE`: the read succeeded, and what came back is
   not our publication.

**Outcome.** What the Mac knows about one attempt:

| `publication.outcome` | Means | Set when | Terminal |
|---|---|---|---|
| `UNCONFIRMED` | may or may not have applied | at row creation, **before** the request is sent | no — the only state that puts a row in `E` |
| `LANDED` | applied | the `PUT` returned 2xx, **or** a later read observed this attempt's fingerprint live | yes |
| `NOT_LANDED` | did not apply | a later read observed a **lower** member of `E` live, or the expected absence | yes |
| `SUPERSEDED` | never confirmed and can no longer be live: an attempt with a **higher** `seq` is `LANDED` | at that moment | yes |

Rev 7 had only the first two, and its resolver — which says observing the
previous object *proves* a later attempt did not land — had no truthful value to
write. `NOT_LANDED` is the state it was missing. `SUPERSEDED` is the other half
of the same gap: for a row that may perfectly well have applied before a later
write replaced it, `NOT_LANDED` is the same false claim in the opposite
direction and `LANDED` invents evidence. **A 2xx is therefore no longer the only
confirmation** — a later observation of an attempt's own fingerprint is the
second, and it is the one that settles a lost response. Every transition out of
`UNCONFIRMED` records `outcome_resolved_at`, plus `outcome_resolved_by_seq`: the
publication whose pre-write read or whose 2xx settled *this* row, `NULL` only
when the row's own 2xx did. So the audit says how each row was decided, and a
`SUPERSEDED` row names the later write that displaced it.

**Ordering rules** — the "state before the action" idiom of task 15. Without
them the records are best-effort and the resolver's premises are false:

1. **The attempt row is written before the request is sent** — `seq` allocated,
   `payload_fingerprint` stored, `outcome = UNCONFIRMED`. A crash in between
   would otherwise leave the Mac unable to recognise its own write.
2. **A retry is a new attempt with a new `seq`**, never a reuse of the one whose
   answer was lost. `seq` is `UNIQUE`, and — the reason that matters here — the
   resolver orders the set **by `seq`**, so two rows sharing one would have no
   order between them and neither of rule 3's readings would be available.
   (Rev 7 justified this by identity instead. That justification stopped being
   true when the fingerprint absorbed `seq`: two ciphertexts under one `seq` now
   have two distinct fingerprints. The rule survives; its reason changed.) Gaps
   are harmless — **I6** compares `seq < last_seq`, which never requires
   consecutive numbers.
3. **At most one publish request is in flight, and attempts are sent in `seq`
   order.** The `Publisher` is a single daily job (§13), so this costs nothing —
   but the resolver reads "a higher `seq` would be live instead, had it applied",
   and that is only true if a higher `seq` was never sent first.
4. **`rotate` / `revoke` record the local delete transition** on 2xx (§6.3.1) —
   what makes the absence that follows an expected state rather than an alarm.

**The expected-live set.** `E` = the newest `LANDED` publication, plus every
`UNCONFIRMED` attempt with a higher `seq`; or `ABSENT` in place of that
publication when a recorded local delete is newer than it, or when nothing has
been published yet. Terminal non-landed rows are not in `E` — which is precisely
what lets it collapse.

**The resolver.** One pre-write read that observes the fingerprint of a row
`p ∈ E` — or observes nothing where `ABSENT ∈ E` — settles the whole set:

| Row | Becomes | Because |
|---|---|---|
| `p` itself | `LANDED` (unchanged if it already was) | its bytes are live |
| every `UNCONFIRMED` row with `seq > seq(p)` | `NOT_LANDED` | sent after `p`, so it would be live *instead of* `p` had it applied (rule 3) |
| every `UNCONFIRMED` row with `seq < seq(p)` | `SUPERSEDED` | a later attempt is live; whether this one ever applied is undecidable and no longer relevant |
| — the absence case instead of the three above — every `UNCONFIRMED` row in `E` | `NOT_LANDED` | nothing is live, and had any applied, one of them would be |

The same collapse happens on a 2xx: every `UNCONFIRMED` row below the landing
`seq` becomes `SUPERSEDED`. After either, `E` holds exactly one element — that
is what "collapses back" means, and it is now derivable rather than asserted:
the widening a lost response causes lasts until the next resolving observation,
and no longer. `ROLLBACK`, `FOREIGN` and `UNAVAILABLE` resolve nothing.

The observation is recorded on the attempt it precedes (`prewrite_state`,
`prewrite_fingerprint`); the transitions it triggers are written to the rows they
settle. **A terminal row is never revisited.** If a `NOT_LANDED` object turns up
live later, that is a `ROLLBACK` — something restored a write this design decided
never applied — and it belongs in an alert, not in an audit row quietly changing
its mind.

**Where the identity is compared.** Twice, both times against the stored
`payload_fingerprint`, which is the whole reason one definition suffices:

| Check | When | `MATCH` / `OK` means |
|---|---|---|
| **pre-write read** | before every `PUT` | the live fingerprint is in `E`, or the expected absence |
| **read-back** | after a `PUT` that returned **2xx**, and only then | the live fingerprint is the one just written |

The read-back is deliberately not run after a failed or unanswered `PUT`. The
previous object is legitimately still there, so comparing it against what the Mac
*meant* to write would report an ordinary failed write as tampering — the rev-7
defect, arriving through the other check. That case belongs to the pre-write
read, which is the only one holding the state to judge it.

---

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

| Channel | Used for | Mechanism |
|---|---|---|
| macOS notification | `NEEDS_REAUTH`, `REVOKED`, **frozen data**, **publication overdue**, **read-back mismatch**, **read-back unavailable**, **pre-write rollback**, **foreign write**, **snapshot missing**, **pairing uncertain**, **accounts pending reconciliation**, **drain stalled** | `osascript -e 'display notification'` |
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
  integrity, and it is kept **separate from read-back *unavailable*** (the check
  could not be run at all, after retries): one says the value is wrong, the
  other says the evidence is missing, and merging them would make the tamper
  signal fire on ordinary network weather until nobody read it.
- **Pre-write rollback / foreign write / snapshot missing** — *before* publishing,
  the transport was serving something other than what this Mac expects to be live
  (§9.3 defines "expects": it is a **set** computed from confirmed and
  unconfirmed attempts, not the last row). The three read differently and must
  not be merged: `ROLLBACK` — the Mac's own superseded bytes are live again — is
  the provider-side-restore signal of §6.2.2; `FOREIGN` — an object no local row
  explains — is the more urgent; `ABSENT` alerts **only when no local `rotate`,
  `revoke` or first publication accounts for it**, because those deletions are
  intended and an alert that fired on them would fire on every rotation by
  construction. `UNAVAILABLE` is transport health, exactly as above.
  **`FOREIGN` does not name the credential**, and rev 6 said it did: a leaked
  write token explains it, and so does anyone holding the Cloudflare account, who
  needs no write token at all (§6.2.2). The runbook's response covers both
  because the alert cannot tell them apart.
- **Pairing uncertain** — a `rotate` whose outcome the Mac never learned
  (§6.3.1). Publishing is suspended until the owner re-runs `networth pair`,
  because the alternative is publishing under a key that may already be revoked
  while every other indicator stays green.
- **Drain stalled** — a queued webhook older than an hour is still undrained
  (§8.4.2). Without it a broken drain is invisible until the TTL destroys the
  evidence.
- **Pending reconciliation** — accounts are sitting at `NEW` and contributing
  nothing (§8.5), so the total is knowingly understated until the owner confirms
  a mapping.

**Rejected rollback is deliberately not in the macOS row — and that is a
different event from the pre-write rollback above.** The distinction is the
*observer*, and collapsing it would recreate the promise review already rejected:

| Event | Who sees it | Where it alerts |
|---|---|---|
| **Rejected rollback (I6)** | the **phone**, when the transport serves *it* a `seq` below its own `last_seq` | phone-local, persistent (§9.3) — it never reaches the Mac |
| **Pre-write `ROLLBACK`** | the **Mac**, when the object it is about to overwrite is one of its own superseded publications rather than what it expects to be live (§9.3) | macOS alert |

An earlier draft put the phone's event in the macOS row, promising an alert
about something only the phone can see, over a channel the architecture does not
have and should not grow (§9.3). The Mac alerts on what the Mac observes; the
phone warns about what the phone observes. Neither stands in for the other, and
a single restore may raise one, both, or — if it is undone before either looks —
neither (§6.2.2).

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
| full sync | **either** no successful full sync since the most recent market close + 1h, **or** >20h since the last successful full sync — whichever comes first (see below) |
| quote refresh | any `MANUAL_QTY_LIVE_PRICE` price older than the last close |
| publish | a snapshot exists newer than the last successful `publication`; every publish is **preceded by a pre-write compare and followed by a read-back** (§9.3) |
| backup | >24h since the last verified backup — §14a. Refuses to run unless the destination resolves to a **physical store disjoint from the database's** (or a remote machine), and refuses if it cannot resolve at all (§14a.1) |

**Due-ness is computed from stored state, never from cron semantics.** A Mac
asleep for two days simply finds work due on wake and catches up; there is no
missed-fire concept to handle.

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
- **It never goes to a third party, and it must leave this disk.** Those are two
  different requirements and rev 3 stated only the first in some places (§5 said
  "never leaves the Mac"; the `BackupStore` seam said "local only") while §14a.1
  required the second. Handing a provider a bundle of access-token ciphertext to
  hold indefinitely is a different risk class from a daily overwritten net-worth
  blob (§6.2) — so, no third party. But an archive on the disk it is meant to
  survive is not a backup — so, **not this physical store**. The destination is
  hardware the owner controls, elsewhere.

### 14a.1 The gate has to survive the failure it exists for

*(From review, and the finding was exact: the draft above let the database, the
archive and `networth-backup.key` all sit on one disk, then called a restore
into a temp directory "verified". That drill proves the archive parses. It
proves nothing about the scenario the section is named after — the Mac dies —
because in that scenario all three copies died together. A backup that only
survives `rm` is not a backup; it is a copy.)*

Three acceptance criteria, all owner-controlled and all free:

1. **A destination in a separate failure domain — resolved to the physical
   store, not the filesystem.** *(From review, and the check as written could
   certify the exact failure it exists to reject: `stat -f %d` compares mounted
   filesystems, and on APFS every volume in a container has its own device id
   while sharing one physical disk. Verified on this Mac: `/` is device
   `disk3s1s1` with `APFS Physical Store: disk0s2`. A second volume — or a Time
   Machine volume the owner made on the internal disk — would have passed a
   `%d` comparison and then died with the database.)*

   `backup_destination` is acceptable **iff** one of these holds:

   - it is **remote** — a different machine (rsync/ssh over Tailscale, or a
     network mount), i.e. a different computer entirely; or
   - it resolves to a **physical store disjoint from the database's**, *and*
     that store is **external media**.

   The resolution is mechanical and runs on **every** backup, not once at setup:
   `df` the path to its device node, then `diskutil info -plist` that node and
   read `APFSPhysicalStores` (an array — a Fusion or multi-store container has
   more than one), falling back to `ParentWholeDisk` for non-APFS; normalise
   each to its whole-disk identifier and require the two sets to be **disjoint**.
   External-ness comes from the same call (`Device Location: External` or
   `Removable Media: Removable`), because a second *internal* store still burns,
   drowns and gets stolen with the laptop.

   **Anything else fails loudly**, including — deliberately — the case where
   resolution does not work at all: an unrecognised path, an unmounted
   destination, a `diskutil` that returns nothing. **Unknown fails closed.** A
   check that passes when it cannot see is worse than no check, because it
   reports a green gate.
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
  not one (§6.2); the phone never receives the write token. **On the Tailscale
  branch there are no tokens at all** — only the payload key, because tailnet
  membership replaces the bearer credential (§6.3.2).
- `~/agents/secrets/networth-backup.key` — the backup archive key (§14a).
- Quotes key: already present, reused.
- Android signing keystore + `key.properties`: outside the repo (§17).

**Not in this list, and deliberately not stored anywhere: Cloudflare account
access.** `wrangler login` would put an OAuth access and refresh token on this
machine, so the runbook brackets each owner operation with login and logout
rather than leaving one behind (§6.2.1, §19 step 3a.0). It is the one credential
whose answer here is *don't keep it*, rather than *keep it safely*.

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

**Two bindings, and which state goes where is a correctness decision, not a
configuration detail** (§6.2.1): the first four routes operate on a **Durable
Object** with the SQLite backend — one object, `pairing` + `snapshot` — because
they need atomic replace and read-your-writes; the last three use **KV**,
because a write-once, TTL-expired, at-least-once queue is what KV is good at.
Rev 3 put all of it in KV and then claimed transactional behaviour it does not
have.

It holds **no Plaid credential**, no read token (only a hash of one) and no
payload key, and performs no verification of Plaid's signature (§8.4) — it is a
dumb, replaceable relay, which is what keeps the `Publisher` swap to Tailscale
cheap. It grew from three routes to six in review; every addition is a control
path that was previously *assumed to exist*, which is why the count went up
while the trust placed in the Worker went down.

**On the Tailscale branch there is no Worker at all** (§6.3.2). Its place is
taken by a small HTTP server on the Mac, bound to the tailnet interface, serving
one route — `GET /snapshot` — with pairing state in the Mac's own SQLite. The
webhook routes have no counterpart: without a public endpoint Plaid cannot
deliver, so the drain does not exist and I3 rests on the poll floor alone.

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
| ~~O3~~ | ~~How many distinct card-issuer logins?~~ **VOID** — it existed only to size the card share of the Item budget, and cards are deferred (§1, rev 9). Nothing waits on it | — | — |
| O4 | Real property: purchase price only, or a revision log? (recommend: revision log — nearly free) | owner | task 13 |
| O5 | Transport: **Cloudflare Worker** (recommended — serves the current value only, works while the Mac sleeps, **keeps the webhook accelerator**; accepts a **30-day provider-side recovery window** that cannot be turned off, that only an account compromise could reach, and whose *use* the Mac detects only if the restored value is still live at the next daily publication, §6.2.2 — and with it the stated boundary that **freshness is unforgeable only against parties without the payload key**, so an attacker holding *both* that key and the account could make an old number look current, which is why step 3a.0 keeps the account credential off this Mac) or **Tailscale** (no third party, so **nothing is retained anywhere**, and revocation is a local transaction — but the Mac must be awake **and Plaid webhooks become impossible**, §6.3.2)? Both branches are fully specified — pairing, rotation, revocation and lost-phone included. **The retention line is the one thing only the owner can weigh**, because it trades a bounded window at a third party against availability | owner | tasks 20, 24 (and 12a, which **exists only on the Cloudflare branch**) |
| ~~O6~~ | **ANSWERED (owner, 2026-08-30): Android only.** No iOS, and it is *decided* rather than postponed — the iOS branch and its sideloading problem are gone from this design rather than parked. Tasks 21 and 24 are Android-only | — | — |
| O7 | Create a free Cloudflare account? It is the one new account this design adds, and it disappears if O5 picks Tailscale. The Workers Free plan covers everything used here — Worker requests, **SQLite-backed Durable Objects**, and KV — and over-limit operations **fail rather than bill** (§6.2.1) | owner | tasks 20, 12a |
| O8 | **Where do backups land?** A destination in a separate failure domain is a gate on the first Production Link (§14a.1). External disk / Time Machine volume / another machine over Tailscale — or an explicit decision to link Items without one | owner | tasks 03a, and through it 08 |

*(O1 — phone vs Mac/browser — was answered by the owner: **Flutter phone app**.
O6 above narrows that to **Android only**, and O3 is void. Answered questions are
struck through rather than deleted so a reader of an old review comment can still
find them.)*

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
   in-scope brokerages appear available. This **answers O2**. If they are *not*
   available, report it — that blocks the **Production-Link path** (tasks
   07/07a/08 and anything downstream of a real Item) and nothing else. The
   foundation continues either way (§18); do not stop it. *(Rev 3 narrowed this
   in §18 and the task graph but left "stop before implementation proceeds"
   here, which is the sentence the owner would actually have been reading.)*
4. Copy `client_id` and the **production** secret into
   `~/agents/secrets/plaid.env`. Never paste them into a chat or a PR.
5. Register the redirect URI (§16) under *Allowed redirect URIs*.
6. Optional: request access for the equity-comp brokerage — expect up to six
   weeks, and do not wait for it (§12 is the primary path).

**Step 1a — Say where backups land** (~5 min, once; **before** Step 2, and the
ordering is the whole point — §14a.1)
1. Pick storage that will not die with the Mac: an **external** disk, a Time
   Machine volume **on external media**, or another machine over Tailscale. Set
   `backup_destination`. On every run the backup resolves that path to its
   *physical* disk and refuses unless it is a different physical store from the
   database's — or a different machine entirely. A Time Machine volume created
   on the internal disk does **not** qualify, and the check now says so instead
   of passing it (§14a.1).
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

**Step 3 — Stand up the transport and pair the phone** (~10 min, once)

*Pairing happens on **both** branches — the payload is encrypted whatever the
transport (§6.1), so the phone always needs a key. Only the setup around it
differs. Rev 3 said to skip this whole step on Tailscale, which would have left
the phone with no way to decrypt anything.*

**Step 3a — if O5 chooses Cloudflare**

0. **Bracket every `wrangler` command — and close the browser session too.** This
   step and step 7 are the only two on this machine that use account-level
   Cloudflare access, and the rest of the design depends on it not being kept
   (§6.2.1, §6.2.2). There are **two credentials to close, not one**, and rev 6
   closed only the first:

   **(a) Wrangler's OAuth token.** `wrangler login` leaves an **access and
   refresh** token on the Mac, in plaintext under the Wrangler config directory
   unless you ask otherwise.

   **(b) The Cloudflare dashboard session in whatever browser authorised it.**
   *(From review.)* The default flow opens a browser and signs it in to the
   account. Cloudflare documents `wrangler logout` as invalidating the **OAuth
   token** and deleting Wrangler's stored credentials, and says nothing about
   that browser session — which the dashboard documents separately, with its own
   lifetime (**72 hours of inactivity** by default) and its own revocation UI. A
   browser still signed in here can redeploy the Worker, which is the exact
   access §6.2.2 turns on, and `wrangler whoami` reports on (a) only. So do not
   infer (b) from (a) in either direction: close it, and check it.

   Keep the browser off this Mac where you can:

   ```
   wrangler login --device --browser=false --use-keyring
                                           # device-code grant, and NO browser
                                           #   opened here: Wrangler prints a URL
                                           #   and a user code to approve on
                                           #   ANOTHER device. Token lands in the
                                           #   macOS keychain
   #   ... run exactly the one operation you came to run ...
   wrangler logout                         # invalidates the token at Cloudflare,
                                           #   then deletes it locally
   wrangler whoami                         # confirms (a). It says nothing about (b)
   ```

   **Both flags, and the second one is the one this step is about.** *(From
   review, verified against Cloudflare's Wrangler command docs before it was
   written down.)* `--device` uses the OAuth 2.0 Device Authorization Grant
   instead of the `localhost` callback, so the browser that signs in does not
   have to be this one — but device mode **still opens the verification URL in
   this machine's default browser**, which is exactly the session (b) that
   outlives the bracket. `--browser=false` is what stops that; Cloudflare
   documents it as the way to "stop Wrangler from opening the browser for you"
   and copy the verification URL yourself. Neither flag substitutes for the
   other: without `--device`, `--browser=false` only suppresses the opening while
   a local callback server still waits on `localhost`, so the browser you paste
   into must be able to reach this Mac. (The docs make the same split explicit
   from the other side — `--callback-host` and `--callback-port` are rejected
   when combined with `--device`, because the device flow has no local callback
   at all.) If a Wrangler build rejects the trio, drop `--use-keyring`: it governs
   at-rest storage of a token this bracket deletes anyway, while `--device` moves
   the durable session and `--browser=false` keeps it off this machine.

   **If the browser step happens here regardless** — creating the account in
   step 1 is a dashboard action, and the owner may simply prefer this machine —
   then close (b) explicitly, and **verify it from another device**: sign out in
   the browser here, then open **My Profile → Sessions** on the other device and
   confirm no session for this Mac remains, revoking it there if one does. The
   dashboard will not let you revoke the session you are currently using, which
   is why a check run from the machine being checked is worth nothing.

   **If you would rather stay signed in** — either layer — that is your call to
   make, but make it knowingly. This Mac already holds the **payload key**
   (§6.1 is symmetric), so a Mac that also holds standing account access holds
   **both halves of §6.2.2's conjunction at once**: the residual there stops
   needing two independent compromises, and freshness stops being guaranteed
   against anyone who gets into this machine. Nothing breaks; the threat model
   changes, and it changes by more than the convenience is worth unless you have
   a reason.
1. Create a free Cloudflare account (**O7**) and run the provided `wrangler`
   deploy, inside the step-0 bracket. Agents can write the Worker; only the
   owner creates the account and only the owner logs in.
2. Run `networth pair`. It registers the new pairing with the Worker **first**
   and prints the QR code only once that succeeds (§6.3.1) — so a QR on screen
   always means a phone that will work.
3. Open the app and scan it. That is the whole provisioning step — nothing
   secret was ever compiled into the APK (§6.3).
4. Re-run `networth pair` any time to rotate: the previous phone stops reading
   **immediately**, before the new one has scanned anything, and the stored
   snapshot is deleted so the old key has nothing left to fetch. No rebuild.
5. **Phone lost or stolen:** run `networth revoke`. Same lockout, no replacement
   phone needed. The Mac keeps publishing; nobody can read. **Know the one
   limit** (§6.2.2): Cloudflare keeps a 30-day recovery copy of the object that
   cannot be switched off, so "deleted" means the transport will never serve it
   again — not that it is shredded. Nothing in the app or on the phone can reach
   that copy; it would take your Cloudflare account login and a code deploy, and
   it expires by itself within 30 days. If a phone is stolen *and* you think the
   Cloudflare account is compromised too, change that account's password and
   rotate the Worker's write token (step 7) — that is the case the window
   actually matters in.
6. If `networth pair` ever reports **pairing uncertain** (it lost the connection
   mid-rotation and cannot know whether it landed), publishing is suspended on
   purpose: just run it again. Re-running is correct whichever way the previous
   attempt resolved (§6.3.1).
7. **Rotating the write token** — owner-only, because no route can change it
   (§6.3.1) and that is deliberate. Generate a new random value, set it on the
   Worker with `wrangler secret put` — **inside the step-0 login/logout
   bracket**, since this is the second and last operation needing account
   access — and write the same value into
   `~/agents/secrets/networth-transport.env`. Do both, back to back: while the
   two disagree the Mac cannot publish and `doctor` reports the publication
   overdue (§6.4) — which is also how you confirm the rotation took. Not routine
   maintenance; this is the response to a suspected Cloudflare account
   compromise (step 5).

**Step 3b — if O5 chooses Tailscale** (no account to create; **O7** disappears)
1. Have the Mac and the phone on the same tailnet — the owner already runs
   Tailscale, so this is usually already true.
2. Run `networth pair`. There is no registration round-trip and no read token:
   the QR carries the payload key, the `pairing_id` and the Mac's tailnet name
   (§6.3.2). It prints immediately.
3. Open the app and scan it. Away from the tailnet the app shows its cached copy,
   labelled with its age (§9) — that is the availability cost of this branch.
4. Re-run `networth pair` to rotate; **`networth revoke`** for a lost phone. Both
   are a single local database transaction, so "immediately" is literal here.
   For a stolen phone, also remove the device in the Tailscale admin console —
   that revokes reachability, which is stronger than revoking content.
5. Know what this branch gives up: **Plaid webhooks cannot be delivered at all**,
   so connection problems surface on the hourly poll instead of on arrival, and
   `PENDING_DISCONNECT`'s advance warning is lost (§6.3.2). The number stays
   honest either way; the warning is later.

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
- **Read-back mismatch / read-back unavailable / drain stalled** → transport
  faults, not account faults. None of them changes the number; they mean a
  signal you rely on is degraded — and *mismatch* is the serious one (the
  transport served something other than what was published), while *unavailable*
  usually means the network was down when the check ran. Check `networth doctor`
  first.
- **Pre-write rollback** → between two publications the transport went *back* to
  an older snapshot (§9.3). On the Cloudflare branch the mundane explanation is
  a point-in-time restore (§6.2.2), which nothing in this system performs — so
  if you did not do it through the Cloudflare dashboard, treat the account as
  suspect: change its password and rotate the write token (step 3a.7). The
  number itself is already corrected — the publication that raised this alert
  overwrote the old value.
- **Foreign write** → before publishing, the transport was serving an object no
  local row explains (§9.3). It does **not** tell you which credential did it: a
  leaked write token explains it, and so does anyone holding the Cloudflare
  account, who needs no write token at all (§6.2.2). So cover both — rotate the
  write token (step 3a.7), re-pair the phone (step 3a.4), and check the account
  itself: password, two-factor, and **My Profile → Sessions** for a login you do
  not recognise (step 3a.0).
- **Snapshot missing** → the object was gone when the Mac went to publish and no
  local `rotate`, `revoke` or first publication accounts for it (§9.3). Expected
  deletions never raise this, so it is the same question as a foreign write —
  who else can write here — arriving as an absence. Same response.
- **Pairing uncertain** → publishing is deliberately suspended; run
  `networth pair` again (§19 step 3a.6). This is the one alert where the phone
  will visibly stop updating, which is intended: the alternative is publishing
  under a key that may already be revoked.

---

## 20. Task breakdown

See [`tasks/README.md`](tasks/README.md). Tasks are drafted but **deliberately
unassigned** — assignment is itself subject to review.
