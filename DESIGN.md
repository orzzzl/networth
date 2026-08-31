# DESIGN — networth

Status: **proposed** (design phase; nothing implemented).
Author: Claude. Reviewer: Codex.

Revision 15 — **the owner asked whether Plaid webhooks are worth having at all,
and the answer is no. Two of rev 14's mechanisms are deleted rather than
reviewed.**

- **v0 polls and does not receive webhooks** (§8.4). This is a **scope
  reduction**, stated plainly rather than buried: the design gives up
  `PENDING_DISCONNECT`'s advance warning, which no poll can derive.
- **What made the case was this design's own delivery chain, not a preference.**
  An alert travels *inside the payload* (§11) and is seen when the owner opens
  the app. So the path from "Plaid knows" to "the owner knows" is bounded below
  by the next publication and above by nothing at all. Webhooks shorten the
  **first** hop from ≤1h to seconds, on a chain measured in hours to days.
  **The improvement lands inside the noise**, and no stated invariant moves: I3
  is already scoped to what `/item/get` proves, and Axis B ages the data
  honestly whether or not anyone was warned.
- **What it costs is structural and permanent:** a publicly reachable,
  unauthenticated write path on the machine holding the Plaid master credential,
  every `access_token`, the payload key and the full history — for the life of
  the project, in exchange for a rare, days-scale heads-up. Deleting it means
  **the VPS exposes no inbound service at all beyond SSH.**
- **Deleted with it:** the `POST /hook` route, `networth-hook.service` (so §13 is
  back to two units and **one writer**, and rev 14's WAL-contention rules are
  moot), the `webhook_event` table, the `WebhookReceiver` seam, the Funnel
  dependency and its two unverified assumptions, the JWT/`kid`/constant-time
  verification surface, and task 20's backfill obligation. **Blockers 3 and 4 are
  void by removal, not by fix** — the same distinction rev 10 had to make.
- **The owner's premise is corrected in his favour, and it does not change the
  answer.** A domain is *not* required and there is no recurring cost: Tailscale
  Funnel would have supplied a domain-form HTTPS URL with a managed certificate
  for free. Webhooks are being dropped because the benefit is small, not because
  the bill was large.
- **One argument could reverse this, and it is named rather than dismissed
  (§8.4).** If an institution migration that passes its deadline unattended
  requires a *fresh* Link rather than update mode, then advance warning protects
  a **permanent Item slot** — the scarcest thing in this project — and the
  calculus changes completely. **That is unverified**, it is answerable from
  Plaid's documentation without spending anything, and §8.4 records it as the
  trigger to revisit.
- §8.4a is kept as a **costed plan that is deliberately not built**, so re-adding
  the accelerator is a decision rather than a redesign.

Revision 14 — **Codex's seven blockers against rev 13, plus two owner
corrections that arrived while the review was being written.** The blockers
cluster at the seams rev 10's host move created, which is the honest summary:
deleting a subsystem is not the same as re-checking everything that referenced
it.

- **The Plaid slot is spent by a successful *Link*, not by the exchange** — and
  every gate in this document was built one step too late. Link hands back a
  `public_token` only *after* the Item exists, so by the time the owner pastes
  anything the slot is gone. §14, §19 step 2 and task 08 now model the whole
  timeline, including the case that has no recovery at all: **Link succeeds and
  the `public_token` is lost, so the Item exists and its `access_token` never
  will.** The pre-Link check is now a **real write-and-read-back canary through
  the actual backup path**, not a reachability ping — because "the host answers"
  and "a backup would work" are different facts, which is this document's own
  thesis applied to its own gate.
- **The backup direction is inverted: the Mac *pulls*.** *(Owner, 2026-08-30.)*
  macOS runs no `sshd` by default, so a push design would have opened an inbound
  service on the machine holding the backup copy; and the Mac sleeps, so every
  push into a sleeping laptop is a failure to retry and alert on. A pull just
  happens on the next wake. **The VPS now needs no knowledge of the Mac at
  all** — no address, no credential, no schedule — which means the
  internet-facing box holding the Plaid master credential gains **zero** new
  outbound trust relationships. §14a, §5, §13, §16 and §19 are rewritten around
  it.
- **Address the Mac by its full tailnet name, never a prefix.** *(Owner,
  2026-08-30, retracting a "prune the stale duplicates" suggestion that was
  destructive: the entries are **different physical machines**.)* This tailnet
  carries **four** MacBook Airs whose names differ only by suffix. The one this
  project means is **`zelengs-macbook-air-2` / `100.96.163.67`** and nothing may
  be written that a prefix match could resolve to a different computer (§19
  step 1b).
- **The webhook endpoint was specified as a public IPv4 and a nameless reverse
  proxy — neither a deployable HTTPS endpoint nor attached to any Item.** Plaid
  wants a domain-form URL with a valid certificate, and Item webhooks are set by
  `/link/token/create` or `/item/webhook/update`, not by a dashboard field. Rev
  14 answered this with Tailscale Funnel and a live-Sandbox acceptance test;
  **rev 15 deleted the endpoint instead** (§8.4), and the analysis survives as
  §8.4a's costed plan.
- **`networth-serve` was simultaneously the webhook writer and a read-only
  database process.** Both could not hold. Rev 14 split the receiver into its own
  unit; **rev 15 removed the receiver**, so §13 is back to two units and the
  "one writer" claim is true rather than merely repeated.
- **The schema had nowhere to put the object `GET /snapshot` serves.** §6.3.1
  said the envelope is stored; `publication` held only metadata. §7 adds
  `published_envelope` with a one-active-row index, and rotation drops it in the
  same transaction that revokes the pairing — the atomicity §6.3.1 claimed now
  has a table to be atomic over.
- **The backup was not a coherent snapshot and its key had two homes.** Copying
  a live WAL database file can miss committed data, and copying `TokenStore`
  separately can pair an `item` row with the wrong token generation — the
  unrecoverable direction. §14a now specifies `VACUUM INTO` under a lock shared
  with token writes, a durability *ordering* (token before the row that
  references it), and a restore drill that checks **that invariant** rather than
  row counts. `/etc/networth/` is authoritative for runtime secrets; the escrow
  copy is described separately; **Sandbox gets its own credential file and its
  own database**, so a rehearsal cannot reach Production by editing a constant.
- **The deletion left live instructions for the architecture it removed.** §2
  still told implementers to build "no backend" and no server-side token
  storage, while the chosen design is exactly a single-user server holding
  tokens; the task graph still labelled VPS work "Mac-side". Rewritten, and the
  current-tense host claims swept again.
- **One overclaim narrowed:** a successful `/institutions/get` proves the
  credential, Trial Production access and VPS egress. It does **not** prove any
  particular institution is reachable, and §4 no longer lets "end to end" imply
  that it does.

Revision 13 — **not review-driven. A correction the owner had to make to this
project's own reporting**, which is worth recording as such rather than folding
in quietly.

- **The Mac is not on the tailnet, and was never on it.** A status summary from
  this project said it was done. It was not: this Mac has no Tailscale installed
  at all. §19 step 1b is now marked **an open owner action** rather than
  satisfied. Nothing else waits on it, but **O8's destination does not exist
  until it happens**, so task 03a cannot pass and 03a gates the first Production
  Link.
- **The trap that caused it is recorded, because it is this project's own thesis
  turned on itself.** `tailscale status` run *from the VPS* lists two entries for
  this Mac — both **stale registrations from previous installs**, both offline —
  and one's "last seen" was misread as a fresh join. **A registry that keeps
  serving an entry after the thing it describes is gone, read as current because
  it was there**, is precisely the failure this product exists to prevent. The
  check is now specified as positive and *local*: this machine has a `100.x`
  address and says so itself.
- **The backup is opportunistic and no revision may imply otherwise.** The Mac
  sleeps, so it runs when the Mac is awake. That is sufficient — the purpose is
  that the token set is **not single-copy**, not that it is continuously
  mirrored. And the project's own discipline applies to its own backup: the
  system reports **when the last successful backup actually happened**, read from
  a record of a completed transfer, never inferred from the schedule having
  fired. An unreachable Mac means the backup **did not happen** and is recorded
  as such (§13, §14a.1).

Revision 12 — **not review-driven, and it found a defect in rev 11.** The owner
installed the Plaid credential on the VPS and it was verified live. Folded into
the same round again; Codex had still not claimed the review.

- **O2 goes from "read off a dashboard" to "proven end to end."** An
  authenticated call from the VPS to `/institutions/get` — **Item-free**, so it
  spent none of the ten slots — returned **200 with 10,033 institutions**. That
  settles the secret's correctness, Trial Production access being genuinely
  active, *and* the new host's egress to Plaid, which nothing had tested (**F4**).
- **The credential's real path is now in the design** (`/etc/networth/plaid.env`,
  §15) rather than a description of one, and **`PLAID_ENV` is read from the file**
  — never hardcoded, which is what keeps a Sandbox run from being one edited
  constant away from a Production one.
- **`PermitRootLogin no` was wrong as rev 11 wrote it, and this is the finding.**
  §15.1 stated it as a flat setting. **This is not a fresh host** — it was the
  owner's Tailscale exit node before this project existed and he administers it
  as `root`. An unattended deploy task applying that setting could **lock the
  owner out of infrastructure that is not ours.** The requirement is now the
  *ordering*: a non-root sudo account must exist and be verified from a second
  session first, and the change is proposed rather than applied. **Hardening that
  can strand the owner is not hardening.**
- **`/etc/networth/` is root-owned today**, so the deploy `chown`s it to the
  service user, keeps `600`/`700`, and **says that it did** — a step that quietly
  adjusts permissions on the file holding the master credential is
  indistinguishable from one that quietly widens them.

Revision 11 — **not review-driven, and small.** The owner created the Plaid
account while rev 10 was in flight, which closes the project's last open
question. Folded into the same review round rather than a round of its own, as
the owner has asked for twice now.

- **O2 answered: GO.** OAuth on Trial is **confirmed** rather than probable —
  from the dashboard itself: a `0/10` trial meter, Production credentials issued,
  and Plaid stating that bank access is automatic on the trial with **no
  per-institution request**. **F4** is rewritten around the evidence; §18 has no
  open questions left. Both feared blockers evaporated rather than being worked
  around, including the one about an individual with no company.
- **A correction to this document's own runbook, which the owner walked into.**
  §19 step 1 sent him through "Get production access" — that is the **paid**
  funnel, ending at a plan picker where every option is billed. The Trial is a
  separate fourth plan, granted on signup, and is not offered anywhere in that
  flow. He stopped and asked; nothing was purchased. The step now says what to
  do (confirm the `0/10` meter) and warns explicitly about the button that looks
  like the answer. **The most likely way to break the zero-spend rule was never
  a design decision — it was following a plausible dashboard button.**
- **F2 gains its citation and its other half:** past ten Items Plaid **stops**
  rather than silently billing. The budget can be exhausted; the cost rule
  cannot be broken by accident.
- **The production secret joins the "no agent ever sees this" list** (§15),
  alongside the standing rule that no agent asks the owner for a password.
- **GO is not permission to link.** The ten slots are lifetime, so task 03a's
  backup gate still stands ahead of 08 — stated in §18 and **F4** because "the
  go/no-go cleared" is exactly the sentence someone would read as clearance.

Revision 10 — **not review-driven, and mostly a deletion.** The owner answered
five open questions in one sitting, and one of the answers moved the host. Every
finding open against rev 7 is **void by removal**, not by fix — the distinction
matters, and §6.2 and §9.3 state it where the mechanisms used to be.

- **O5 answered, and it changed more than the transport.** The owner has an
  **always-on VPS he already pays for** (Ubuntu, public IPv4, already his
  Tailscale exit node), so the daemon moves off the Mac and onto it. Both
  drawbacks the Tailscale option carried through rev 9 were **Mac** drawbacks and
  both are void at once: "the Mac must be awake" (a VPS is), and "Plaid webhooks
  are impossible" (it has a public IP). *(Rev 15 note: only the first of those
  two ever mattered — the design later declined webhooks on their merits, §8.4.
  The host move stands on the sleep argument alone.)*
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
  narrowed to what polling can prove. Rev 13 pointed here at a webhook
  accelerator for advance-warning events that Item state cannot express; **rev 15
  drops it, and this invariant is unchanged** — it never rested on the webhook,
  which is precisely why the webhook was droppable. §8.4 states what is given up
  instead of implying nothing was.)*
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

**Build now:** **no multi-user backend** — no accounts, no login, no
multi-tenancy, no tenant boundary anywhere. The owner links his own institutions
through Plaid Link and the results are stored on one host that is his.

**Do not build now, not even scaffolding:** user registration, authentication,
per-user encryption schemes, sharing, or export-to-another-user. Each costs real
work and buys nothing today.

*(Rewritten in rev 14, and the correction matters more than the wording.
Through rev 13 this section said "no backend" and "no server-side token
storage" — written when the design ran entirely on the Mac, and left standing
when rev 10 moved the daemon to a VPS. Since rev 10 the design **is** a server
holding tokens, so a reader following §2 literally would have been building
against §5. What was always meant is the multi-user ceiling below; the daemon is
a single-user headless service, and "backend" here means the thing a second user
would need, not "a process on a computer that is not the phone".)*

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

**F2 — `/item/remove` does NOT free a slot.** Plaid's billing docs, quoted
rather than paraphrased: removing Items created on a Trial plan "will **not**
allow you to create more Items." The 10 are therefore **10 lifetime Items**, not
10 concurrent connections. A mislinked institution burns a slot permanently.
This is the binding constraint of the project and drives §8 and §14 — in
particular it is the whole reason re-authentication must go through Link
**update mode** (§8.3) rather than the obvious remove-and-relink.

**F2a — and the slot is spent when Link succeeds, not when we exchange.** *(Rev
14, from review, and it moved every gate in this document one step earlier.)*
Through rev 13 this said "10 lifetime Link **exchanges**", which quietly placed
the irreversible moment at `/item/public_token/exchange` — a call *we* make, and
therefore one we could gate. That is not where it is. Plaid's Link flow returns
a `public_token` **only after the user has successfully created the Item**;
exchange is what obtains that Item's `access_token` afterwards. So the Item —
the thing the ten counts — exists *before* the owner has pasted anything, and
nothing after that point can un-spend it. §14a.1, §19 step 2 and task 08 are
built on this boundary rather than the old one.

*The residual uncertainty is stated rather than resolved,* because the two
plausible readings differ and only one is safe: **F6**'s quote counts *access
tokens*, while the Item is created earlier. This design assumes the **earlier**
event spends the slot, since being wrong that way costs a little caution and
being wrong the other way costs a permanent slot. No agent may "verify" this by
running a Production Link to see what the meter does — that experiment costs the
thing it measures.

*(Rev 11 adds the other half, which matters for a different reason: past ten
Items, Plaid **stops** rather than silently billing. That is the failure mode
this project would want — the zero-spend rule (§4) cannot be violated by
accident here, only the slot budget can be exhausted. Confirmed on the dashboard,
which shows a `0/10` trial meter.)*

**F3 — Investments Refresh *is* bundled on Trial.** *(Corrects the brief, which
had it as a paid add-on.)* It is a paid add-on **off** Trial. It stays unused
regardless: the natural cadence already satisfies the requirement (holdings
update "at least once per day during market days (up to 2-4 times per day,
depending on institution)" after close), and depending on it would create a
silent cost cliff the moment the account leaves Trial.

**F4 — OAuth access on Trial is CONFIRMED.** *(Rev 11. Through rev 10 this read
"probable but unconfirmed" and was the project's one go/no-go, **O2**. The owner
created the account on 2026-08-30 and it is settled — from the product surface
itself, not from an inference.)* The dashboard shows a **`0/10` free-trial
meter** (Trial active, no lifetime slot consumed), the credential panel now
issues **Production** `client_id` and `secret`, and Plaid states on the overview
page: *"Automatic bank access — No action is needed to access banks through
Plaid's free trial."* Plaid's OAuth guide independently says Production access
"via either a paid plan or a trial" satisfies the prerequisite.

Both feared blockers evaporated rather than being worked around:

- **"OAuth institutions may be unreachable on the free tier"** — no. Bank access
  is automatic on Trial and **no per-institution approval request is required.**
  This independently confirms §18's decision not to file an access request for
  the equity-comp brokerage: there was nothing to request.
- **"Plaid may not approve an individual with no company"** — did not arise.
  Plaid accepted an **Individual** business type, and the Trial needs only
  account creation plus email verification. No MSA, no security questionnaire,
  no underwriting review.

**Proven live, and here is exactly what that proves** *(rev 12; scope narrowed
in rev 14)*. From the VPS, an authenticated call to `/institutions/get` — an
**Item-free** endpoint, so it consumed none of the ten lifetime slots —
returned **HTTP 200 with 10,033 institutions**. Three things the dashboard could
only suggest are now facts: the secret is exactly right (a wrong one returns
`INVALID_API_KEYS`), Trial Production access is genuinely active rather than
merely displayed, and **the VPS has working egress to Plaid** — a property of
the new host that nothing had tested before.

**It proves nothing about any particular institution.** *(Rev 14, from review.)*
Rev 12 called this "end to end", and next to an O2 whose question was
*"do the in-scope brokerages work on Trial?"* that phrasing implies a check that
was never run. A directory listing is not a link attempt. What actually retires
the institution-specific half of O2 is Plaid's own statement that bank access is
automatic on Trial with no per-institution request (**F4**) — a general claim
about the plan, not evidence about the owner's banks — plus the Sandbox
rehearsal in task 06. The first genuine per-institution evidence arrives with
the first Production Link, and by **F2a** that evidence costs a lifetime slot to
obtain, which is precisely why it is not treated as a check to run early.

**O2 is closed, and this design now has no owner-side unknowns left** (§18).
Note what that does *not* license: the ten slots are still lifetime, so **no
institution is linked until the backup is actually running** (§14a; task 03a
still gates 08). GO removed one gate; it did not remove the reason for the other.

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
   ┌──── the VPS (Ubuntu, always on, on the tailnet) ──────────────────┐
   │  networth-sync.timer  (§13)                                      │
   │    SyncEngine → StalenessMachine → Snapshotter → Publisher       │
   │    PlaidClient (holds client_id/secret + access_tokens)          │
   │    ItemHealthPoller: /item/get hourly — the only Plaid signal    │
   │    BackupBuilder: writes the encrypted archive to a local dir    │
   │  networth-serve.service   GET /snapshot — TAILNET only, DB r/o   │
   │  SQLite (WAL): full history, append-only                        │
   │  NOTHING is reachable from the public internet (§8.4)           │
   └───────┬────────────────────────────────────────────▲─────────────┘
           │ tailnet (WireGuard)                        │ the Mac PULLS,
           │ + payload encrypted anyway (§6.1)          │ whenever it is
   ┌───────▼──────────────────────────────────────┐     │ awake (§14a):
   │  Flutter app: fetch → decrypt → cache →      │     │ OPPORTUNISTIC,
   │  display, secrets from one-time pairing      │     │ never daily.
   │  shows BOTH staleness dimensions (§9)        │     │ The VPS knows
   │  AND is the only place alerts appear (§11)   │     │ nothing of it
   └──────────────────────────────────────────────┘     │
                        zelengs-macbook-air-2 / 100.96.163.67 ──┘
                        holds the pulled archives + runs Link in its browser
```

**The arrow into the Mac points the other way from every revision before 14, and
that is a deliberate inversion** *(owner, 2026-08-30)*. A VPS→Mac push would
need `sshd` running on the laptop that holds the backup copy — macOS does not
run one by default, so the design would have been asking the owner to open an
inbound service on the machine whose whole job is to be the second copy. And the
Mac sleeps, so most pushes would fail and need retry and alert machinery. **A
pull needs none of that:** it happens on the next wake, and the VPS gains no
address, no credential and no schedule pointing at anything. The internet-facing
box holding the Plaid master credential ends up with **zero** new outbound trust
relationships, which is a security simplification and not just a plumbing
preference.

**The Mac is named in full, everywhere, on purpose** — `zelengs-macbook-air-2`
(`100.96.163.67`). This tailnet has four MacBook Airs whose names differ only by
suffix and they are four different computers; a bare "the Mac" or a prefix match
selects the wrong one silently (§19 step 1b).

The VPS keeps every credential and the full history. The phone is a
**read-only display of an authenticated, encrypted snapshot** — it never holds a
Plaid token, never calls Plaid, and cannot mutate anything. That asymmetry is
what makes the phone safe to lose. The Mac is where the owner runs Plaid Link
once per institution (§19) and where the backup archives land (§14a) — and,
since rev 14, it is also what *initiates* the transfer, so one small scheduled
job does run there. It is still not a component of the number: nothing on the
Mac contributes to, computes, or serves the total.

**What moving the host bought, and what it cost.** *(Rev 10. The VPS was not on
the table until the owner mentioned he already had one, and it collapses most of
this document's hardest section.)*

| Was a problem | Now |
|---|---|
| The Mac sleeps, so a daily guarantee needed a third-party transport that serves while it is asleep | The VPS is always awake. The phone fetches straight from it |
| Plaid webhooks need a public endpoint the Mac does not have | The VPS could have one — but **rev 15 dropped webhooks entirely** (§8.4), so this row records a benefit the design chose not to take. The other row above is the one that carried the host move |
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
- *(No `WebhookReceiver`. Rev 15 dropped it with the route — §8.4. The seam is
  not kept "for later" either: an interface with no implementation is the
  speculative generality §2 rules out, and §8.4a already holds everything a
  future implementation would need.)*
- `Notifier` — alert delivery. On a headless host that means **into the payload**
  and nowhere else (§11).
- `BackupBuilder` — produces the encrypted archive of a **consistent** database
  snapshot plus the token material, into a local directory on the VPS (§14a).
  It has no network side and no destination: *building* the archive and *moving*
  it are now different jobs on different machines, and the seam is where that
  split lands. **`BackupStore` — the push-side seam — is deleted**, not renamed;
  the code that would have used it does not exist on this host any more.
- `BackupPuller` — the Mac-side counterpart (`zelengs-macbook-air-2`): fetches
  the archive over the tailnet, verifies it decrypts and that its token
  fingerprints match its `item` rows, then reports the verified pull back to the
  VPS. It is the only piece of this system that runs off the VPS and is not the
  phone.

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
**nothing about this system is reachable from the public internet at all** — rev
13 carved out an exception for the webhook endpoint and rev 15 removed the
exception with the endpoint (§8.4). This section keeps the comparison that led
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
  VPS *could* publish one. *(Rev 14 found that a routable address was never the
  same thing as a domain-form HTTPS URL with a valid certificate, and specified
  Funnel. **Rev 15 then declined the capability altogether** — §8.4 — so this
  drawback of the Mac turns out not to have mattered either way. Recorded because
  it was one of the two reasons the host moved, and only the other one held.)*

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
  the daemon's own SQLite — concretely in `published_envelope` (§7), which holds
  the nonce, the ciphertext‖tag and the four AAD fields **exactly as they will be
  served**, under an index that permits at most one active row. A small HTTP
  server (`networth-serve`) bound to the VPS's **tailnet interface** serves
  `GET /snapshot`: the active envelope, provided its pairing is `ACTIVE`, or
  `404`. It holds the database **read-only** and reassembles the JSON envelope of
  §6.1 from those columns; it never re-encrypts and never re-serializes the
  plaintext, so the bytes the phone authenticates are the bytes the publisher
  sealed. The phone fetches it over WireGuard. Nothing leaves the host except
  that one response, to that one tailnet.

  *(Rev 14, from review: through rev 13 this bullet and §7 disagreed about
  whether the envelope existed anywhere. The section said "stores the envelope";
  the schema's `publication` table held only metadata — `snapshot_id`, pairing,
  `seq`, schema version, time, outcome — with no nonce, no ciphertext and no
  pointer to a store. Since task 03 builds §7 verbatim, task 19 would have had
  to invent its own storage contract to have anything to serve. A section that
  says "we store X" over a schema with no X is a specification that cannot be
  implemented as written, which is why the table is now the normative part and
  this prose points at it.)*

- **Publishing deletes the previous envelope before inserting the new one, in
  that order, in one transaction.** *(Stated because the obvious implementation
  fails on the second publication and only on the second: inserting a new
  `is_active = 1` row while the old one is still active violates the partial
  unique index, so a mechanism added for safety would break every publish after
  the first.)* Only one envelope is ever stored — the previous ciphertext is not
  kept, which also means the host holds exactly one day's payload at rest rather
  than an accumulating pile of them under one key. The `publication` rows remain;
  they are the audit trail, and they carry no ciphertext.
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
  old `revoked_at`, and the row in `published_envelope` **deleted**, all inside
  one `BEGIN IMMEDIATE`. **`networth revoke` is immediate in the literal sense**,
  and there is no `UNCERTAIN` state to design around because there is no network
  call whose outcome the daemon can fail to learn. The serving process holds the
  database read-only, so it cannot observe a half-applied rotation: it either
  reads the old envelope or gets a `404`, never a new pairing pointing at an old
  ciphertext.
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

published_envelope(                                -- §6.3.1: the object GET /snapshot hands out.
                                                   --   Rev 14 adds it: §6.3.1 said the envelope
                                                   --   was stored and this schema had nowhere to
                                                   --   put it, so the serving contract could not
                                                   --   be built from §7 as task 03 requires
  publication_id PRIMARY KEY REFERENCES publication(id) ON DELETE CASCADE,
  pairing_id REFERENCES pairing(id),
  schema_version, seq, published_at,               -- the AAD inputs, stored exactly as served:
                                                   --   the header is reproduced, never recomputed
  nonce BLOB NOT NULL,                             -- 96-bit, fresh per publication (§6.1)
  ciphertext BLOB NOT NULL,                        -- ciphertext ‖ GCM tag, opaque here
  is_active)                                       -- 1 = the row /snapshot serves; NULL otherwise

CREATE UNIQUE INDEX one_active_envelope            -- at most one servable envelope, enforced by
  ON published_envelope(is_active)                 --   the database rather than by the writer:
  WHERE is_active = 1;                             --   "which one is current?" must not have two
                                                   --   answers, and a partial unique index makes
                                                   --   the second one unwritable

-- No `webhook_event` table. Rev 15 dropped the receiver (§8.4), and an empty
--   table with no writer is dead schema, not a reservation: §2 rules out
--   scaffolding for capabilities this version does not build. If webhooks are
--   ever adopted, §8.4a carries the shape and this is a migration, not a
--   redesign.

backup_archive(                                    -- §14a. Two clocks again, and for the same
                                                   --   reason as everywhere else: "an archive
                                                   --   was built" and "a second machine holds a
                                                   --   readable copy" are different facts, and
                                                   --   only the second one is a backup
  id, built_at,                                    -- when the VPS produced it
  archive_sha256, byte_size,
  db_row_counts_json, item_count,                  -- what the drill re-checks against
  token_fingerprint_set_sha256,                    -- salted fingerprints only, NEVER tokens
  pulled_verified_at,                              -- NULL until zelengs-macbook-air-2 has pulled
                                                   --   it AND proved it decrypts and reconciles.
                                                   --   Written by the Mac over SSH; the VPS never
                                                   --   reaches out to learn it (§14a)
  pulled_by,                                       -- the full tailnet name that claimed the pull:
                                                   --   four Airs differ only by suffix (§19 1b)
  verify_error)                                    -- why a pull failed verification, if it did
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

**The envelope is stored, not rebuilt.** *(Rev 14.)* `published_envelope` keeps
the nonce, the ciphertext‖tag **and** the four header fields as literal columns,
so serving is a read rather than a re-derivation. The alternative — store the
ciphertext and recompute the header from `publication` at request time — looks
equivalent and is not: the header fields are the AAD, so any drift between how
they were encoded at seal time and how they are rendered at serve time (a
different `seq` formatting, a re-normalised timestamp) makes the tag fail and the
phone report a corrupt payload for a payload that is fine. **Two ends must
compute the same bytes; the cheapest way to guarantee that is for only one end to
compute them at all.** `is_active` is a partial unique index rather than a
`current_publication_id` pointer on some other table because the invariant is
"at most one", and an index enforces it against every writer including a future
one that has not read this paragraph.

**`backup_archive.pulled_verified_at` is the only field allowed to answer "is
there a second copy?"** — `built_at` may not, and the distinction is the same one
as `fetched_at` vs `source_as_of` (**I5**) in a different domain. An archive
sitting on the VPS is not a backup of the VPS. Nothing infers the pull from the
schedule; the row stays `NULL` until `zelengs-macbook-air-2` has fetched that
exact archive, decrypted it, and reconciled its token fingerprints — and if that
never happens, the age of the last non-`NULL` row grows visibly, which is
precisely what should happen.

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
   / consent expired                        │ NEEDS_REAUTH│ OWNER ACTION,
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
- **`PENDING_DISCONNECT` is not a transition on this diagram, and rev 15 removed
  it from one.** It was listed as an entry into `NEEDS_REAUTH` through rev 14,
  which contradicted §8.4's own argument two sections later: an Item scheduled
  for migration is **not in an error state**, which is exactly why `/item/get`
  cannot see it and why the webhook was the only way to learn of it. Listing it
  as a poll-driven transition would have had the implementation looking for a
  condition that never appears — and, worse, would have made the design read as
  though dropping webhooks cost nothing. It costs this transition. The Item
  arrives at `NEEDS_REAUTH` or `REVOKED` later, by the door it actually uses:
  the error that surfaces once the disconnect happens.
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

### 8.4 What polling proves, what it cannot, and why v0 stops there

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

So the honest position through rev 14 was a floor and an accelerator, not an
equivalence. **Rev 15 keeps the floor and drops the accelerator.**

**The floor — polling, always on, no infrastructure.** `/item/get` per Item,
hourly, returns the Item's current `error`. This is what **I3** promises, and I3
is deliberately worded as *"every Item error state visible to `/item/get`"* —
what polling can actually prove. **In v0 it is the whole mechanism**, and no
invariant in §1 moves as a result: I3 was already scoped to it, and Axis B (§8.1)
ages the data honestly whether or not anyone was warned in advance.

#### The decision, because it is a scope reduction and not a detail

*(Owner question, 2026-08-30: "webhooks are optional — weigh dropping them
rather than fixing the two blockers they caused." Rev 14 had just answered those
two blockers by specifying a Funnel endpoint and splitting the receiver into its
own unit. Both are deleted here.)*

**What decided it was this design's own delivery chain.** An alert does not go
anywhere when it is raised: alerts are in-app only, they travel **inside the
payload** (§11), and the owner sees one when he opens the app. So the path from
*Plaid knows* to *the owner knows* has four hops:

| Hop | Latency |
|---|---|
| Plaid knows → we know | **≤ 1h polling, or seconds by webhook** |
| we know → it is in a payload | next publication (§13) |
| payload → the phone | next fetch; background execution is explicitly not dependable (task 22) |
| the phone → the owner | **when he opens the app** — unbounded |

**Webhooks improve the first hop only, by under an hour, on a chain whose total
is measured in hours to days and whose last hop has no bound at all.** The
improvement lands inside the noise. This is the same reasoning that made the
receiver "deliberately not on the critical path of anything" in rev 10 — followed
one step further, to the conclusion that a component off every critical path,
whose latency gain is swallowed downstream, is a component this version does not
need.

**What is genuinely given up, stated as a loss and not minimised:** advance
warning of a scheduled disconnect. Polling cannot derive it — an Item heading for
`INSTITUTION_MIGRATION` is **not** in an error state yet, so `/item/get` shows
nothing. Without it, a migration is detected when it breaks: the Item errors,
polling catches it within the hour, the alert reaches the phone at the next
publication, and the owner re-links in update mode (no slot). The cost is **a few
days of one account being visibly stale on a rare event** — not a wrong number,
not a broken invariant, and not silence, because Axis B keeps ageing that account
in plain sight.

**What keeping it would have cost**, now that the price is known precisely:

- **A permanently internet-reachable, unauthenticated write path on the machine
  holding the Plaid master credential, every `access_token`, the payload key and
  the entire history.** That is the whole cost, and Funnel does not reduce it —
  Funnel is *how* the route becomes publicly reachable. Dropping it means **the
  VPS exposes no inbound service at all beyond SSH.**
- A verification surface that is silent when wrong: constant-time comparison over
  raw bytes, a `kid` cache, the `iat` window, size caps, in-process rate
  limiting, a catch-all boundary, a second writing process and the WAL contention
  that comes with it. None of it is exercised by the number being right, so a
  defect there is invisible until it matters.
- Two unverified dependencies (Funnel on the personal tier; Plaid accepting a
  `.ts.net` URL), a live-Sandbox delivery test, and a standing obligation to run
  `/item/webhook/update` for every Item that predates the endpoint.

**And the owner's premise is corrected in his favour without changing the
answer.** He asked that a free, durable route be named rather than assumed, and
warned against quietly introducing a recurring cost. There is one: **Tailscale
Funnel** supplies a domain-form HTTPS URL with a certificate it provisions and
renews, on the personal tier, with no domain to buy and no new open port. So this
is **not** a decision forced by §4 — webhooks are affordable. They are dropped
because what they buy is small, which is a better reason and a more durable one.

#### The one argument that would reverse this, named rather than dismissed

If an `INSTITUTION_MIGRATION` whose `disconnect_time` passes unattended leaves an
Item that **update mode cannot recover** — requiring a fresh Link — then advance
warning is not buying latency at all. It is protecting a **permanent Item slot**
(**F2**), which is the scarcest resource in this project, and against that the
public-route cost is obviously worth paying.

**This is unverified.** It is not written as a risk that was weighed and
discounted, because it was not weighed — it is a fact nobody here has checked.
Two things follow: it is **answerable from Plaid's documentation or support at
zero cost and without touching an Item**, and until it is answered this decision
rests on the assumption that a post-deadline migration is recoverable through
update mode like any other broken Item. **If that assumption is false, revisit
this section immediately**; §8.4a exists so that doing so is a decision rather
than a redesign.

#### 8.4a The plan if the accelerator is ever added — specified, deliberately not built

*(Rev 14 worked this out in full while answering two review blockers, and rev 15
keeps the conclusions rather than the machinery. It is documentation, not dead
code: no route, no table, no seam and no unit exist for it. The point is that
adding webhooks later costs a decision and an implementation, not another round
of design.)*

- **The endpoint would be Tailscale Funnel**, publishing one path on the VPS at
  `https://<node>.<tailnet>.ts.net/hook/<random>`: a real domain, a certificate
  Tailscale provisions and renews, and **no new inbound port** — ingress arrives
  over `tailscaled`'s existing outbound connection. That removes the domain
  purchase, the ACME client, the renewal timer and the reverse proxy that a
  public-IPv4 design needs. Rate limiting would live in the receiver process,
  since there is no proxy. **Both of its load-bearing assumptions are
  unverified**: that the personal tier permits Funnel, and that Plaid accepts a
  `.ts.net` URL. Fallback: `nginx` on the public IPv4 with Let's Encrypt over
  `<ip-with-dashes>.sslip.io`, which costs an open port and a renewal lifecycle.
- **A public IPv4 is not a public HTTPS endpoint.** Rev 10 wrote it as though it
  were, and then leaned on a "rate-limiting reverse proxy" that no section, task
  or runbook step ever specified. Whatever route is chosen, Plaid requires a
  domain-form URL with a valid certificate.
- **Item webhooks are set through the API, not the dashboard.** The `webhook`
  field of `/link/token/create` wires an Item at birth; `/item/webhook/update`
  fixes one that already exists. The dashboard webhook setting belongs to other
  product families. Any adoption must therefore **backfill every Item created
  before the endpoint existed**, and surface the remaining count until it is zero.
- **The receiver must be its own process.** It cannot share one with the snapshot
  server, both because a public input would then have a path to killing the route
  the phone uses — the only channel any alert reaches the owner through (§11) —
  and because that server holds the database **read-only** while a receiver must
  write. Rev 13 specified both of one process, which is impossible; that is the
  defect this bullet exists to prevent recurring.
- **Correctness details that are easy to get subtly wrong:** verify ES256 against
  `/webhook_verification_key/get` cached by `kid`; compare `request_body_sha256`
  in **constant time against the raw received bytes**, because the hash is
  whitespace-sensitive and a handler that parses and re-serializes before hashing
  breaks every signature while making healthy traffic look like an attack; check
  `iat` against receipt time; size-cap and reject before parsing; and make
  duplicates inert with `UNIQUE(body_sha256, jwt_iat)`, since Plaid retries and
  a replay inside five minutes is ordinary traffic rather than an attack.
- **The unguessable path is a nuisance filter, never authorisation.** The JWT is
  the control, and nothing may treat knowledge of `<random>` as permission.
- **It would remain advisory.** A dropped event must never change the number,
  only delay a warning — the poll floor is what I3 rests on, then and now.

**What we accept in v0, restated because it is now the only case:** a migration
or revocation is detected after the fact, on the next poll, when the Item's error
surfaces. The number stays honest either way, because the account's data ages
visibly on Axis B regardless. **A missing webhook can never cause a wrong number,
only a later warning** — which was rev 10's argument for letting the receiver be
best-effort, and is rev 15's argument for not having one.


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
  sibling project. *(The key exists in `~/agents/secrets/` on the Mac, where that
  project runs; this daemon reads its own copy from
  `/etc/networth/quotes.env` — §15. The owner installs it there, the same way as
  every other runtime secret.)* The *price* obeys normal
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

**Two units**, both running as a **dedicated unprivileged user** that owns the
database and nothing else (§15), both **enabled at boot** — "the VPS is always
awake" is a claim about the *host*, and it buys nothing if a reboot leaves the
daemon stopped:

| Unit | What it is | Database handle |
|---|---|---|
| `networth-sync.service` (+ `.timer`) | the periodic worker: poll, sync, snapshot, publish, build the backup archive | read-write — **the only writer** |
| `networth-serve.service` | `GET /snapshot`, bound to the **tailnet interface only**, `Restart=always` | **read-only** |

**One writer, and this time it is true.** *(Rev 13 claimed it while §8.4 had a
webhook receiver writing events inline in the read-only serving process — a
contradiction review caught, and which rev 14 resolved by adding a third unit.
Rev 15 removes the receiver entirely (§8.4), so the simplest version is the
correct one again.)* No `busy_timeout` contention to reason about, no second
process holding a write lock, and the read-only handle on `networth-serve` is
what keeps it from ever observing a half-applied rotation (§6.3.1).

**Two ordering and isolation details that a straightforward implementation gets
wrong, both consequences of rev 10's host move rather than of systemd:**

1. **The tailnet interface does not exist at boot time.** `networth-serve` binds
   the tailnet address (§6.3.1), which `tailscaled` has not yet brought up when
   the unit first starts. The unit therefore orders `After=tailscaled.service`
   and **retries the bind**; what it must never do is fall back to `0.0.0.0`
   because the intended address was unavailable. That fallback is the single
   configuration mistake in this design that silently converts a private endpoint
   into a public one, and "the address was not ready yet" is exactly the
   plausible-looking reason someone would add it.
2. **Both processes share one SQLite file**, `networth-serve` opening it
   **read-only** — it has no reason to write and every reason not to be able to.

   **"Read-only" here means the open mode, not filesystem permissions, and the
   difference is a deployment trap worth naming.** A WAL reader needs to write
   the `-shm` index, so a well-meant hardening step that gives `networth-serve`
   its own unix user with no write access to the database directory does not
   produce a safer service — it produces one that cannot read at all, and the
   failure arrives as an unhelpful `SQLITE_CANTOPEN` at the moment the phone
   asks. Both units therefore run as the **same** service user, and the
   read-only guarantee is enforced by opening `file:…?mode=ro`.

*(There is no third unit and no isolation requirement between a public input and
the phone's route, because since rev 15 there is no public input. The property
rev 13 and rev 14 were both trying to secure — "nothing reachable from the
internet can silence the owner's only channel" — is now satisfied by there being
nothing reachable from the internet, §8.4.)*

The timer fires every 5 minutes and the worker asks the database what is due:

| Job | Due when |
|---|---|
| health poll | >60 min since the last poll |
| full sync | **either** no successful full sync since the most recent market close + 1h, **or** >20h since the last successful full sync — whichever comes first (see below) |
| quote refresh | any `MANUAL_QTY_LIVE_PRICE` price older than the last close |
| publish | a snapshot exists newer than the last successful `publication` (§6.4) |
| build archive | >24h since the last archive was **built** — §14a. This job is entirely local: it snapshots the database, packs the token material and encrypts, into a directory on this host. It has no destination and cannot fail for a reason involving another machine |

*(No webhook-drain row and no receiver at all: v0 polls and does not receive,
§8.4. And no pre-write compare or read-back around the publish, §9.3.)*

**There is no "backup" row in that table any more, and its absence is the
inverted direction showing up in the schedule.** *(Rev 14, owner.)* Through rev
13 the VPS was supposed to notice the Mac was awake and push — a job whose
due-ness predicate had to include another machine's liveness, and which therefore
spent most of its life failing for a reason that was not a failure. Now the VPS
only *builds*; **`zelengs-macbook-air-2` decides when to pull** (§14a). The
scheduler on the always-on host is no longer modelling the sleep schedule of a
laptop it cannot see.

**The pull side runs on the Mac, and the one thing it must not be is a
`StartInterval` LaunchAgent.** A sibling project on this same machine established
the hard way that launchd defers `StartInterval` timers while on battery — and
the owner's standing requirement there was that things work **on battery**, with
no prompt to plug in and no battery guard. A backup that only runs on AC power,
on a laptop, is a backup that quietly does not run. So the puller is a
`KeepAlive` LaunchAgent running its own sleep loop, and its acceptance criterion
is that a pull is observed **on battery** (§14a).

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
2. Confirm the exact institution *and login* **before Link completes** — not
   before the exchange. **F2a**: the Item is created inside Link, so by the time
   a `public_token` is in the owner's hands the slot is already spent and the
   confirmation prompt rev 13 put in front of the paste was asking a question
   whose answer could no longer change anything.
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

**O8 is answered: the archive travels from the VPS to
`zelengs-macbook-air-2` (`100.96.163.67`) over the tailnet — and the Mac is what
initiates it** *(owner, 2026-08-30)*. A scheduled pull over SSH from a machine he
physically owns, in a different country from the VPS, on a different provider, on
different hardware.

**Rev 14 inverted the direction, and the reasons are concrete rather than
stylistic:**

- **macOS does not run `sshd` by default.** A push design requires enabling an
  inbound service on the owner's laptop for the sole purpose of receiving
  backups — new attack surface on the machine that holds the second copy, in
  exchange for nothing.
- **The Mac sleeps.** Every push into a sleeping laptop is a failure that has to
  be retried, alerted on, and distinguished from a real failure. A pull simply
  happens on the next wake; there is nothing to fail in between, so there is
  nothing to model.
- **The VPS gains no way to *initiate* anything toward the Mac** — no address to
  dial, no private key, no schedule, no `known_hosts` entry. The internet-facing
  machine holding the Plaid master credential ends up with **zero new outbound
  trust relationships**. That is the part worth keeping even if the other two
  ever stop applying.

  *Stated that precisely rather than as "the VPS knows nothing about the Mac",
  which is the tidier sentence and is false: the VPS holds the Mac's **public**
  keys in `authorized_keys`, and it learns which machine pulled from what that
  machine tells it (`pulled_by`). Neither is a capability — a public key does not
  let the holder connect anywhere, and a self-reported name is not an address.
  The claim worth making is about direction, and it survives; the broader one
  does not.*
- It is also the direction that was actually proven to work: SSH from
  `zelengs-macbook-air-2` to the VPS over the tailnet IP, key-based, with the
  peer observing source `100.96.163.67`.

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

- **The archive** is one encrypted bundle of the SQLite database plus the
  `TokenStore` contents, written to a local directory on the VPS, outside the
  working tree, and encrypted with **`/etc/networth/networth-backup.key`** —
  which is where every runtime secret on this host lives (§15). *(Rev 14 fixes a
  path that had drifted: rev 13 said in two places that the archive was keyed
  from `~/agents/secrets/`, which is a directory **on the Mac**. Code on the VPS
  cannot read it, and the escrow copy the owner keeps is a different thing from
  the runtime key — §14a.1 criterion 2 covers that separately.)*
- **It never goes to a third party, and it must leave this host.** Two different
  requirements, and earlier revisions stated only the first in some places while
  §14a.1 required the second. Handing a provider a bundle of access-token
  ciphertext to hold indefinitely is its own risk class — so, no third party. But
  an archive on the disk it is meant to survive is not a backup — so, **not this
  host**. `zelengs-macbook-air-2` satisfies both.

**The archive has to be a *coherent* snapshot, and "copy two files" is not one.**
*(Rev 14, from review — two distinct defects, and the second is the dangerous
one.)*

1. **The database is in WAL mode.** Copying `networth.db` while the sync worker
   writes can capture a file whose committed data is still in the `-wal`, so the
   copy is missing transactions that the source considers durable. The archive
   is therefore produced with **`VACUUM INTO`** (equivalently the SQLite online
   backup API), which yields a single consistent file without blocking readers.
   Copying the three WAL files by hand and hoping they are mutually consistent is
   the thing this replaces.
2. **The database and `TokenStore` must be captured under one boundary**, or an
   archive can pair an `item` row with a token file from a different generation.
   Both directions of that mismatch are not equally bad, and the design leans on
   the asymmetry: a token with no `item` row is an orphan — harmless, and
   recoverable by re-reading the Item from Plaid. **An `item` row whose
   `access_token` is missing is unrecoverable and strands a lifetime slot.** So
   two rules:
   - **Ordering:** `TokenStore` writes the token, `fsync`s, *then* the `item`
     row is committed. The unrecoverable direction is never the one a crash can
     produce.
   - **A shared lock:** token writes and archive builds both take an exclusive
     `flock` on `/etc/networth/.tokenstore.lock`. The builder holds it across
     `VACUUM INTO` **and** the token-file copy, so no Link can land between the
     two halves. It is held for well under a second and blocks nothing but
     another token write.

**The archive is published atomically on both sides**, and the build side is the
one that is easy to forget. On the VPS the builder writes to a temporary name in
the archive directory and `rename`s it into place, because a puller that arrives
mid-build would otherwise fetch a truncated file that decrypts to nothing. On the
Mac the fetch goes to a temporary name in the destination directory, is
`fsync`ed, is verified (below), and only then `rename`d over the previous one. A
half-written archive is never the newest archive at either end, and a rename
during a transfer is harmless: the reader's open descriptor keeps referring to
the file it started reading.

**Running the ordinary timeline through the pull, because that is where the last
four review rounds found their defects:**

- **The Mac pulls, verifies, and then fails to write the result back** (the lid
  closes, the tailnet drops). `pulled_verified_at` stays `NULL` although a good
  copy exists — an under-report, which is the correct direction for this
  particular fact to fail, since the alternative is claiming a backup nobody
  confirmed. It self-corrects, but only because of the next bullet.
- **The next wake finds nothing to transfer.** A puller that skips when it
  already holds the newest archive would never retry that write-back, and
  `doctor` would under-report forever. So the rule is not "record after a
  transfer" but: **whenever the Mac holds a verified archive whose row has a
  `NULL` `pulled_verified_at`, it records it** — transfer or no transfer.
- **The canary and the real archive must not be confused for one another.**
  `link.sh`'s probe (§14a.1) is built to a distinct path, is deleted after it
  decrypts, and **never writes a `backup_archive` row.** A rehearsal that leaves
  behind a record indistinguishable from a real backup would be this project's
  own failure mode, self-inflicted.

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
   **every** pull, not once at setup, and **failure to verify fails closed**. The
   pull is recorded as successful only when `zelengs-macbook-air-2` has the
   archive on its own disk, has **decrypted** it, and has confirmed every `item`
   row in it resolves to a token in the same archive. Anything short of that
   leaves `backup_archive.pulled_verified_at` `NULL`. *(A check that passes when
   it cannot see is worse than no check: it reports a green gate.)*

   **The backup is opportunistic, and this document must never imply otherwise.**
   *(Rev 13, at the owner's explicit instruction after a status report claimed
   something that was not true. Rev 14 changes the direction and not this rule.)*
   `zelengs-macbook-air-2` **sleeps**. So the pull runs when it happens to be
   awake — there is no daily guarantee here and no revision may quietly introduce
   one. That is sufficient for the purpose, because the purpose is that **the
   `access_token` set is not single-copy**, not that it is continuously mirrored.

   *What the inversion does change is that "the destination was unreachable" has
   stopped being an event anyone has to handle. A sleeping laptop is no longer a
   failed job on the VPS; it is simply a pull that has not happened yet. The
   error class the old design spent alerts on was an artefact of pointing the
   arrow the wrong way.*

   **And the project's own rule applies to its own backup.** This design exists
   because a product rendered a number without saying how old it was. A backup
   subsystem that assumes it ran is the same failure in a different domain — so
   `doctor` and the app both surface **`last_successful_backup`: when a verified
   copy last actually landed**, read from `backup_archive.pulled_verified_at`,
   never inferred from the schedule having fired and never from an archive merely
   having been built. **`built_at` is not a backup date** (§7). If that number is
   old, the owner sees that it is old, and its ageing is the *only* signal that
   the Mac has stopped pulling — which is correct, because on this direction the
   VPS has no way to notice and should not pretend to.

   **How the Mac's verdict reaches the VPS, without the VPS reaching out.** After
   a verified pull, the Mac writes the fact back over the same SSH connection it
   already opened (`networth backup record-pull`), stamping
   `pulled_verified_at` and `pulled_by` with its **full** tailnet name. The
   direction of trust is unchanged — the Mac initiates everything — and the VPS
   still holds no address for it. `pulled_by` records the full name because four
   Airs on this tailnet differ only by suffix (§19 step 1b), and "some Mac
   fetched it" is not the fact worth storing.

   **But "daily" is the wrong criterion, and running the ordinary timeline
   through this schedule is what exposes it.** Suppose `zelengs-macbook-air-2`
   has been closed for two weeks and the VPS dies. Two weeks of *curve* are
   lost — regrettable. Two weeks of *token set* are lost only if an Item was
   linked in those two weeks — and if one was, the loss is a **permanent slot**,
   which is the thing this whole section exists to prevent. The token set changes
   at exactly one moment: **a successful Link.** So the binding rule is not a
   daily schedule at all:

   > **`scripts/link.sh` runs on `zelengs-macbook-air-2`. It proves the whole
   > backup path works *before* it mints a link token, and it pulls a fresh
   > verified archive immediately after the exchange — refusing to report the
   > Link as complete until that archive is on the Mac's disk and decrypts.**

   The daily job stays, for the history. But the gate that protects the
   irreplaceable thing is tied to the event that creates it, not to a clock —
   the same reasoning that put this section in Phase 1 instead of Phase 5. Two
   details of that sentence are load-bearing and both are rev 14:

   - **The script runs on the Mac, not the VPS.** That follows from the pull
     direction: the Mac is the only machine that can fetch, so it has to be the
     one driving the sequence. The `public_token` exchange still happens on the
     VPS, over SSH, because that is where the client secret lives — the script
     is a wrapper around a remote step, not a relocation of the credential.
   - **"Proves the path" means a canary, not a ping.** `link.sh` builds a small
     probe archive on the VPS, pulls it, decrypts it and deletes it — the entire
     mechanism, end to end, with the real key and the real transport. Rev 13's
     check was reachability, which is exactly the substitution this project
     exists to refuse: *the host answering* is evidence about the network, and
     what needs proving is that **a backup would work**. A full disk, a wrong key
     mode, a `chown` that took away the read bit, a destination directory that
     does not exist — every one of those passes a ping and fails a backup.

   **And the whole timeline has to be stated, because the happy path is not where
   the slots go.** By **F2a** the Item exists the moment Link succeeds, before
   the owner has pasted anything. So:

   | Moment | What exists | If everything stops here |
   |---|---|---|
   | Before Link opens | nothing | Nothing lost. **This is the only point at which the design can still refuse**, which is why the canary runs here |
   | Link completed in the browser | the **Item** — the slot is spent | The `public_token` is in the browser and expires in ~30 minutes. Paste it and continue; there is no other route back to this Item |
   | `public_token` lost or expired | the Item, with no reachable `access_token` | **Unrecoverable. A permanently stranded slot** — the Item counts against the ten and can never be read. Not a hypothetical: closing the tab is enough |
   | Exchange returned, token not yet durable | the Item and an `access_token` in memory | A crash here loses the token: same stranded slot. The token is `fsync`ed to `TokenStore` **before** anything else happens, and before the `item` row that references it |
   | Token durable, archive not yet pulled | one copy of the token, on the VPS | Survivable unless the VPS is lost in this window. The window is seconds, and the pull that closes it is the next step rather than tomorrow's job |
   | Verified archive on the Mac | two copies | The state this section exists to reach |

   The residual is named rather than closed: **the row three lines up has no
   engineering answer.** Once Link has completed, nothing this project can build
   makes a lost `public_token` recoverable. What the design does instead is keep
   that window as short as possible and tell the owner plainly, in the runbook
   (§19 step 2), that the paste is not a formality — it is the only path from a
   spent slot to a usable Item.
2. **A recoverable copy of the backup key that is not only on the VPS.** The
   runtime key is `/etc/networth/networth-backup.key` and it decrypts the
   archive, so the two must not share a fate. The owner keeps a second copy in a
   password manager or on paper (it is one line) and confirms with
   `networth backup attest-key`, which records `key_escrow_confirmed_at` and
   *nothing else*. **This third copy is the offline one, and nothing reads it** —
   distinct from the operational copy on `zelengs-macbook-air-2`, which the
   puller and the drill do read (§15). It exists for the case where the VPS and
   the Mac are gone together, which is the only scenario the other two copies do
   not cover. This is an
   **attestation, not a proof** — no agent can verify a password manager, and
   pretending otherwise would be its own dishonesty. `doctor` shows it, with its
   date, as the owner's own claim.
3. **The drill restores from the copy that would actually be used.**
   `scripts/restore-drill.sh` runs on `zelengs-macbook-air-2` against the archive
   sitting in its own destination directory — the copy a real recovery would
   reach for, on the machine that would still exist — decrypts it with that
   machine's own copy of the key (§15), restores into a temp directory, and
   checks **the invariant, not
   the volume**:

   - schema version matches, and row counts match what
     `backup_archive.db_row_counts_json` recorded at build time;
   - **every `item` row resolves to a token in the same archive**, compared by
     salted **fingerprint** (never the tokens, never in a log). This is the check
     rev 13 did not have: matching row counts prove the database arrived, and say
     nothing about whether it arrived paired with the right token generation —
     which is the failure mode §14a's lock and ordering exist to prevent, so it
     is the one the drill has to be able to catch;
   - the restored `TokenStore` has no `item` row missing a token. Orphan tokens
     are reported and are **not** a failure (§14a).

   It records `last_verified_restore_at`, runs weekly, and `doctor` reports its
   age.

**Gate:** all three must hold before task 08 links the first Production Item. A
hard dependency in the task graph, not a recommendation — the window in which a
host failure costs permanent slots opens the moment task 08 runs, which is why
this is a Phase 1 gate and not a Phase 5 operations chore.

---

## 15. Secrets and what may never be committed

The repository is **public**. Repo visibility was never the real control — the
separation between code and credentials is. These rules are also in `AGENTS.md`,
which binds both agents.

**Each host has exactly one secrets directory, and neither host's code ever
reaches for the other's.** Since rev 14 there is code on both machines, so the
rule has to be stated per host rather than as one location:

| Host | Directory | Read by |
|---|---|---|
| the VPS | **`/etc/networth/`** (mode `700`/`600`) | the three daemon units, `link.sh`'s remote half |
| `zelengs-macbook-air-2` | **`~/agents/secrets/`** | the backup puller, the restore drill, `link.sh`'s local half, the APK build |

*(Rev 13 said runtime secrets live on the sync host and, two paragraphs later,
that committed code reads them from `~/agents/secrets/` — which is a directory on
a laptop that VPS code cannot open. Both halves were half-right, and the fix is
not to pick one: there really are two hosts with two directories. What must never
happen is a lookup that falls back from one to the other, because that is how a
path bug turns into "it worked on my machine" for a file holding access tokens.)*

**On the VPS**, in `/etc/networth/`:

- **`/etc/networth/plaid.env`** — `PLAID_CLIENT_ID`, `PLAID_SECRET`,
  `PLAID_ENV=production`. **Already installed by the owner** (2026-08-30).
- **`/etc/networth/plaid-sandbox.env`** — the **Sandbox** `client_id`/`secret`
  and `PLAID_ENV=sandbox`. *(Rev 14, from review: Plaid issues separate Sandbox
  and Production credentials, and rev 13 named only the Production file while
  also forbidding a second location — which left task 06's rehearsal with no
  legitimate way to authenticate. The rule was always "no **invented** second
  path", not "one file"; two environments are two credential sets, and mixing
  them into one file is how a rehearsal reaches Production.)* Same directory,
  same mode, same owner-installs-it rule — and, like the Production secret, **no
  agent may see it**: agents write the command, the owner runs it.
- `/etc/networth/plaid-items.json` — `{item_id: access_token}`, Production.
  Sandbox Items live in `plaid-items-sandbox.json`, never the same file.
- `/etc/networth/networth-payload.key` — the payload key (§6.1). **One key, no
  tokens**: tailnet membership replaces the bearer credential and the payload key
  *is* the read credential (§6.3.1).
- `/etc/networth/networth-backup.key` — the backup archive key (§14a), used to
  **encrypt**.
- `/etc/networth/quotes.env` — the quotes key for `QuoteClient` (§12).
- `/etc/networth/networth.env` — non-secret runtime config, listed here because
  it sits beside the secrets: `balance_mode` (**F5**) and the archive directory.
  *(No webhook URL — v0 has no endpoint, §8.4.)*

**Environment selection fails closed.** `NETWORTH_ENV` (`sandbox` |
`production`) is required — there is no default — and it selects the credential
file, the items file **and the database path** together. The process asserts at
startup that the selected file's `PLAID_ENV` equals `NETWORTH_ENV` and **refuses
to start** if they disagree. Two consequences worth the paragraph: a Sandbox
rehearsal physically cannot write into the Production history, so task 06 cannot
contaminate the curve; and the mismatch that would otherwise be silent — a
Sandbox credential in a file labelled production — becomes a startup failure
rather than a run whose data nobody questions.

**On `zelengs-macbook-air-2` (`100.96.163.67`)**, in `~/agents/secrets/`:

- `networth-vps.key` — the agents' general SSH key to the VPS.
- `networth-backup-ssh.key` — a **separate, forced-command** key used only by the
  unattended puller (below).
- `networth-backup.key` — the same archive key, used here to **decrypt**. The
  Mac needs it: an archive it cannot open is not a verified copy, and §14a.1
  criterion 3's drill would have nothing to check.
- the Android release keystore and `key.properties` (§17).
- the pulled archives themselves, in their own directory outside any repo.

**The archive key exists in three places, and saying so is the point.** The VPS
encrypts with it, `zelengs-macbook-air-2` decrypts with it, and the owner holds
an offline escrow copy (§14a.1 criterion 2) that no program reads. The first two
are operational necessities — a key only on the machine being backed up protects
nothing, and a copy the destination cannot read makes the destination useless.
The escrow exists for the case the other two are gone at once.

**The honest consequence, since a design that only lists benefits is not
describing a security boundary:** `zelengs-macbook-air-2` holds both the archives
and the key to open them, so compromising that laptop yields the token set —
without any need to touch the VPS. It does **not** yield a shell on the VPS (the
puller's key is forced-command), live Plaid API access, the payload key, or the
ability to publish anything. That is the trade the second copy costs, and it is
the right one: the alternative is one copy of an irreplaceable credential set.

**Why the puller gets its own SSH key rather than reusing `networth-vps.key`**
*(the owner asked for the argument, not the conclusion)*. The pull is
**unattended** and runs on a laptop that leaves the house; `networth-vps.key`
opens an interactive shell on the machine holding the Plaid master credential.
Constraining the backup key in `authorized_keys` with
`restrict,command="networth backup serve-archive"` means a compromise of
`zelengs-macbook-air-2` yields the archives — which that machine already has on
disk, so nothing new — but **not a shell on the VPS**, which would additionally
yield live Plaid API access, the payload key, and the ability to rewrite history.
The gain is real and the cost is one extra line in `authorized_keys`. The
interactive key stays for `link.sh` and ordinary administration, where a human is
present.

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

- **Key-only SSH: `PasswordAuthentication no`.** Agents use their own dedicated
  key (`networth-vps.key`); the owner's password is never requested by, shown to,
  or stored by any agent — a standing rule, not a preference.
- **`PermitRootLogin` needs care, and rev 11 stated it as a flat `no` without
  it.** *(Rev 12, and this is a defect in this document's own hardening step
  rather than a note about it.)* **This is not a fresh host.** It was the owner's
  Tailscale exit node before this project existed, he administers it as `root`,
  and he installed the Plaid credential as `root`. A deploy task that reads
  "`PermitRootLogin no`" and applies it can **lock the owner out of infrastructure
  that is not ours** — and it would do so while he is not watching, since these
  units run unattended.

  So the ordering is the requirement, not the setting: **a non-root account with
  `sudo` and the owner's key must exist and be verified working from a second
  session before root login is restricted at all**, and the change is proposed to
  the owner rather than applied. If he declines, that is his call on his own
  machine and it is recorded rather than worked around. *Hardening that can strand
  the owner is not hardening.*
- **A firewall that opens exactly one thing to the public internet: SSH.**
  *(Rev 14 removed the second opening by routing the webhook over Funnel; rev 15
  removed the webhook, so there is now **no public inbound service of any kind**
  beyond SSH — no port, no Funnel, no proxy, nothing to harden.)* The snapshot
  server binds the **tailnet interface only** and must never be published; a bind-address regression is the one configuration mistake
  here that quietly turns a private endpoint into a public one, so it is an
  acceptance criterion with a test, not a config comment.
- **No inbound anything on `zelengs-macbook-air-2`.** Rev 13's push design would
  have needed `sshd` enabled on the owner's laptop; the pull direction (§14a)
  means the Mac opens connections and accepts none. Nothing in this design may
  ask for Remote Login to be turned on.
- **Unattended security upgrades** enabled.
- **A dedicated unprivileged service user** owning the database and the secrets,
  so the daemon is not root and a bug in a Plaid response parser is not a root
  bug.
  **`/etc/networth/` is currently root-owned**, because the owner created it. The
  deploy task `chown`s it to the service user and **keeps mode `600`/`700`** —
  and **reports that it did so**. A permission change to a file holding the master
  credential is never a silent step: the failure mode of getting it wrong is
  widening access to the one secret that can burn all ten lifetime Items, and a
  step that quietly adjusts permissions on secrets is indistinguishable from a
  step that quietly widens them.
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
3. **Credentials live only in the two locations named above, and the two are not
   interchangeable** — **`/etc/networth/` on the VPS holds everything the daemon
   reads**, while `~/agents/secrets/` on `zelengs-macbook-air-2` holds only keys
   that let the *Mac* start a conversation (SSH, archive decryption, APK
   signing). No committed code reaches for `~/agents/secrets/` at runtime; it is
   not a fallback path and not a search location. Never in git, a PR body, a
   review comment, or a log line. The DB stores `secret_ref` (a key name)
   resolved through `TokenStore`, never a token. **`TokenStore` is what makes
   this a one-line change rather than a refactor** (§2 reservation 3): the host
   moved between rev 9 and rev 10 and the storage path moved with it, which is
   exactly the churn that reservation was written for.
4. **PR descriptions and commit messages carry no real numbers.** Report "3
   accounts reconciled", never amounts.
5. **No institution-specific detail in the repo** (§2 reservation 1) — this
   document deliberately describes categories, not the owner's banks.

Logging redacts by default: the logger takes a `redact=[...]` set and every Plaid
response passes through it. `scripts/check-no-secrets.sh` runs as a pre-commit
hook and in CI.

Banking credentials are never seen by any agent or written anywhere: the owner
types them into Plaid Link, which returns only a short-lived `public_token`.

**And neither is the Plaid production secret.** *(Rev 11, standing rule.)* It is
the master credential — it can burn all ten lifetime Items (**F2**) — so agents
write the *command* that installs it and the owner runs that command himself on
the VPS. **No agent may ask him to paste it into a chat, into a file an agent
reads, or into a PR.** Same rule, same reason, as the ban on asking him for a
password (§15.1): the fact that an agent needs a credential to exist somewhere is
never a reason for the agent to see it.

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
takes its place is **two routes on the host that already holds everything, in
two separate processes** *(rev 14 split them, §13)*:

| Route | Process | Bound to | Auth | DB | Purpose |
|---|---|---|---|---|---|
| `GET /snapshot` | `networth-serve` | **tailnet interface only** | tailnet membership; the payload key is the read credential (§6.3.1) | **read-only** | the phone reads the current envelope |

**One route, and nothing else listens.** *(Rev 15.)* Rev 9 had six routes at a
third party; rev 10 cut them to two in one process; rev 13's two turned out to be
impossible in one process; rev 14 split them across two units; rev 15 deletes the
second route with the webhook decision (§8.4). **The end state is that this host
answers exactly one HTTP request, from exactly one tailnet, and nothing on it is
reachable from the public internet.** Worth stating as a destination rather than
a diff, because five revisions of movement is hard to read as a shape.

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
simplification rather than a complication.** Link runs in a **browser on
`zelengs-macbook-air-2`** — that is where the owner is sitting and where his
password manager is — while the `public_token` must be exchanged where the client
secret lives, which is the **VPS**. Since rev 14 the *driver* is on the Mac too,
because the backup is a pull and only the Mac can perform one:

1. `scripts/link.sh` runs **on `zelengs-macbook-air-2`**. It first runs the
   backup canary (§14a.1) — build a probe archive on the VPS, pull it, decrypt
   it, delete it — and **refuses to go further if that fails.**
2. It then SSHes to the VPS to mint a `link_token` and prints the URL. The
   `client_id`/`secret` never leave the VPS; the Mac holds an SSH key, not a
   Plaid credential.
3. The owner opens the URL in this Mac's browser and completes Link there.
   **Credentials and MFA go into Plaid's page**; neither machine sees them.
   **The lifetime slot is spent at the end of this step** (**F2a**), before
   anything is pasted anywhere.
4. The redirect page displays the `public_token`; the owner pastes it into the
   waiting `link.sh` prompt, which pipes it to the VPS **over stdin** — never as
   an argument, which would put it in `ps` output — where it is exchanged and the
   `access_token` written durably through `TokenStore` before the `item` row is
   committed (§14a).
5. `link.sh` immediately triggers an archive build on the VPS and **pulls it**,
   verifies it decrypts and reconciles, and only then reports the Link complete.

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

**None are open.** *(Rev 11. Rev 10 got this down to one — O2 — and the owner
closed it the same day. Answered questions are kept, struck through, with the
answer in place, so a reader of an old review comment can still find what O5 was
and why each branch disappeared.)*

**What that means, stated so nobody reads it as more than it is:** every
*external* unknown is gone — nothing is waiting on Plaid, on a vendor's terms, or
on a decision only the owner can make. The critical path is now entirely
internal. It does **not** mean linking may begin: task 03a's backup gate stands
(§14a), and it is not an open question, it is a dependency.

| # | Question | Owner of the answer | Blocks |
|---|---|---|---|
| ~~O2~~ | ~~Does the Trial plan actually reach the in-scope brokerages via OAuth?~~ **ANSWERED 2026-08-30: GO — on the strength of Plaid's plan-level statement, not an institution-level test.** Trial active at `0/10`, Production credentials issued, and Plaid states bank access is **automatic on the trial** with no per-institution request. The live `/institutions/get` call proves the credential, Trial Production access and VPS egress; it is a directory listing and proves nothing about any specific bank (**F4**, narrowed in rev 14). Per-institution evidence costs a lifetime slot to obtain (**F2a**), which is why it is not a check anyone runs early. Tasks 07, 08 and the downstream Production-Link work are ungated — but see the runbook correction in §19 step 1, because the obvious path to "production access" is a **paid** funnel that this project must not enter | — | — |
| ~~O3~~ | ~~How many distinct card-issuer logins?~~ **VOID** — it existed only to size the card share of the Item budget, and cards are deferred (§1, rev 9). Nothing waits on it | — | — |
| ~~O4~~ | ~~Real property: purchase price only, or a revision log?~~ **ANSWERED: a revision log**, defaulting to purchase price, every revision kept with its date — **and a revision applies from its own date forward, so the curve behind it never deforms** (§12) | — | — |
| ~~O5~~ | ~~Transport: a third-party relay, or Tailscale?~~ **ANSWERED: Tailscale — and the host moved with it.** The owner has an always-on Vultr VPS (already paid for, already his tailnet exit node), so the daemon runs there instead of on the Mac. Both drawbacks the Tailscale branch carried were *Mac* drawbacks and both are void: the VPS never sleeps, and it has a public IPv4 so the webhook accelerator survives (§8.4). The entire third-party branch is **deleted** (§6.2), not parked | — | — |
| ~~O6~~ | ~~iOS as well as Android?~~ **ANSWERED: Android only.** *Decided* rather than postponed — the iOS branch and its sideloading problem are gone from this design rather than parked. Tasks 21 and 24 are Android-only | — | — |
| ~~O7~~ | ~~Create a free third-party account for the transport?~~ **VOID** — it existed only on the branch O5 deleted. No new account is created by this design | — | — |
| ~~O8~~ | ~~Where do backups land?~~ **ANSWERED: on `zelengs-macbook-air-2` (`100.96.163.67`), which *pulls* over the tailnet** — a different machine, provider and country. **Rev 14 inverted the direction** (owner): macOS runs no `sshd`, the Mac sleeps, and a pull leaves the VPS with no address, credential or schedule pointing at anything. Framed around the **access-token set**, not the curve: history cannot be back-filled, but a lost token cannot be recovered *at all* and strands a lifetime Item slot (§14a) | — | — |

*(O1 — phone vs Mac/browser — was answered earlier: **Flutter phone app**, which
O6 narrows to **Android only**.)*

**O2 blocked the Plaid path, not the project — and the record of why is worth
keeping now that it is answered.** *(Narrowed in review, which caught this table
saying "all implementation" while the task graph and the recommended plan both
started six tasks before O2 could possibly be answered.)* The foundation —
schema, `Store`, `TokenStore`, the Plaid client wrapper, the backup gate,
Sandbox rehearsal — touches no Production Item and no institution, and would
have survived a `NO` intact. **It went the other way, so nothing was spent on
the contingency; but the reasoning is what made it safe to start six tasks
before the answer existed**, and the same reasoning is why nothing in the graph
changes now except that a `BLOCKED (O2)` label comes off.

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

**Step 1 — Create the Plaid account** — ✅ **DONE 2026-08-30.** Kept because it
records a trap, not because there is work left.

1. Sign up at `dashboard.plaid.com/signup`, verify email.
2. **Confirm the `0/10` free-trial meter on the dashboard overview.** That is the
   entire check. The Trial is granted automatically on signup.
3. **DO NOT follow "Get production access".** *(Rev 11, and this is a correction
   to a runbook I wrote and the owner actually walked into.)* That flow is the
   **paid** path: it ends at a plan picker offering only Pay-as-you-go / Growth /
   Custom — **all billed** — followed by a billing step. **The Trial is a
   separate fourth plan that is not offered anywhere in that flow**, so
   "production access" reads like the thing you want and is not. The owner
   stopped at the plan page and asked before selecting anything; nothing was
   purchased and no payment method was entered.

   Recorded prominently because the §4 zero-spend rule is the economic basis of
   the whole project, and the most likely way to break it is not a design
   decision — it is following a plausible-sounding dashboard button. Trial
   credentials are **already Production credentials**; there is nothing to
   upgrade to.
4. **DONE 2026-08-30.** `client_id` and the **production** secret are installed
   at **`/etc/networth/plaid.env`** (mode `600`, directory `700`), written with a
   `read -rs` one-liner so the secret never entered shell history, never rendered
   on screen and **has never been seen by any agent**. Verified live from the VPS
   against `/institutions/get` — an **Item-free** endpoint, so the check cost
   none of the ten slots (**F4**).

   The rule that produced that, kept for every future secret: **no agent may see
   the production secret.** Agents write the *command*; the owner runs it. Never
   a request to paste it anywhere an agent can read — not a chat, not a file, not
   a PR. *(The `read -rs` form and the stdin-piped request body are worth reusing
   verbatim: they keep the secret out of shell history and out of `ps` output,
   which are the two places a careful person still leaks it.)*
5. Register the redirect URI (§16) under *Allowed redirect URIs*. **That is the
   only dashboard registration this project needs.** *(Rev 13 also asked for a
   webhook URL here. Rev 14 found that setting does not apply to Item-based
   products at all — the webhook is the `webhook` field of `/link/token/create` —
   and rev 15 dropped webhooks entirely (§8.4), so there is nothing to register
   under either reading.)*
6. **Do not request special access for the equity-comp brokerage.** Rev 9 listed
   this as an optional step; the owner decided against it (§18). The manual path
   (§12) is the plan, it needs no request and no Item, and the request would cost
   up to six weeks for something that may not surface the award account anyway.

**Step 1a — Give the agents a key to the VPS** (~5 min, once; **this is the one
step everything else on the host waits for**)
1. An `ed25519` keypair already exists on `zelengs-macbook-air-2`:
   `~/agents/secrets/networth-vps.key` (private, mode 600, never leaves that
   machine, never in git, a PR or a log) and `…​.key.pub`.
2. Append the **public** half to `~/.ssh/authorized_keys` on the VPS.
3. Add a **second, restricted** key for the unattended backup pull (§15):
   generate `networth-backup-ssh.key` on the same Mac and install its public half
   with `restrict,command="networth backup serve-archive"`. The unattended job
   then cannot open a shell on the host holding the Plaid master credential; the
   interactive key stays for `link.sh` and administration, where you are present.
4. Tell the agents it is done. **No agent will ever ask you for a password**, for
   this host or any other — that is a standing rule, not a preference for this
   step (§15.1).

**Step 1b — Put the backup machine on the tailnet** (~5 min, once) — ✅ **DONE
2026-08-30, verified.** Kept because it now records two corrections, both of
which were mistakes this project made about the owner's own environment.

Verified from the machine itself, not inferred:

| Check | Result |
|---|---|
| Tailnet address of `zelengs-macbook-air-2` | **`100.96.163.67`**, `Connected` |
| Host key of `207.148.102.122` vs `100.102.245.37` | **identical** — same `tokyo-exit`, not a spoofed peer answering on the tailnet address |
| `tailscale ping` | ~118 ms, **direct** (`via 207.148.102.122:41641`), not DERP-relayed |
| SSH Mac → VPS over the tailnet IP | **OK**, and the peer observed source `100.96.163.67` |

The Mac is **not** configured to use the exit node, and that needs no action: the
exit node routes the owner's browsing traffic and is irrelevant to peer-to-peer
reachability inside the tailnet. Do not add an exit-node step here.

**Correction 1 — the "stale duplicate registrations" were four different
computers, and the suggestion to prune them was wrong and destructive.** Rev 13
read two similar names in a device list as leftovers of one machine. The owner
owns **several MacBook Airs**:

| Device | macOS | Tailnet IP | Seen |
|---|---|---|---|
| `zelengs-macbook-air` | 13.4.0 | `100.68.28.38` | ~20d ago |
| `zelengs-macbook-air-1` | 13.4.0 | `100.83.37.57` | ~1h ago |
| **`zelengs-macbook-air-2`** | **26.5.1** | **`100.96.163.67`** | **Connected — this one** |
| `zelengs-macbook-air-3` | — | `100.120.179.15` | ~4m ago |

**Nothing was deleted** — the retraction arrived before any pruning was
attempted, and all four entries were confirmed still present afterwards. Note
that the fourth (`-3`) was not in the list rev 13 saw *or* in the owner's own
correction: the population of near-identical names is not stable, which is
exactly why no rule may depend on knowing it.

> **The rule this produces, and it binds every config, script, unit file and
> runbook step: address this machine by its full tailnet name
> `zelengs-macbook-air-2` or by `100.96.163.67`. Never "the Mac", never
> "the MacBook Air", never a bare prefix.** `zelengs-macbook-air` is a
> **different computer** that is still on this tailnet, and a prefix match will
> select it silently. Because the backup is a pull (§14a), the identity that
> matters operationally is the one the **VPS observes as the source** —
> `100.96.163.67` — which is the value to use in any Tailscale ACL or
> source-address constraint.

**Correction 2 — the discriminating signal.** The giveaway rev 13 missed was
sitting in the same output: **different macOS versions and different IPs.** Names
that look like a series are not evidence of anything; the OS version is. A status
claim about the owner's environment gets verified **on the thing itself**, with a
signal that can distinguish the possibilities — never from a name that merely
looks like a duplicate.

*Worth noticing what rev 13's error was underneath: **a registry that keeps
serving an entry after the thing it describes is gone**, read as current because
it was there. That is this project's founding failure mode wearing different
clothes, and it caught this project's own status reporting twice in one day — in
opposite directions, first believing an absent machine was present, then
believing three present machines were absent. Both were caught by the owner
rather than by us.*

**Step 1c — Confirm backups actually work** (~5 min, once; **before** Step 2, and
the ordering is the whole point — §14a.1)
1. **O8 is decided: `zelengs-macbook-air-2` pulls from the VPS over the
   tailnet.** Nothing to choose; this step is confirming it works.
2. Install the puller on this Mac: a **`KeepAlive` LaunchAgent**, not a
   `StartInterval` one. (launchd defers interval timers on battery — a sibling
   project on this machine proved it, and a backup that only runs plugged in is
   a backup that does not run.) Confirm one pull happens **while on battery**.
3. Copy `networth-backup.key` into a password manager or write it down, then run
   `networth backup attest-key`. It records only the date of your confirmation.
   Without this, the archive and its key die together.
4. Run `scripts/restore-drill.sh` **on this Mac** and see it pass. It restores
   from the archive sitting in this machine's own destination directory — the
   copy a real recovery would reach for — and checks that **every `item` row
   resolves to a token in the same archive**, not just that the row counts look
   right.
5. **Do not proceed to Step 2 until it passes.** After the first Production Link,
   losing the tokens does not cost a re-link — a lost `access_token` cannot be
   recovered at all and strands permanent Item slots (**F2**, **F2a**, **F6**,
   §14a).

**Step 2 — Link each institution** (~2 min each, once per institution)
1. Run `scripts/link.sh` **on this Mac** — `zelengs-macbook-air-2` (built by
   agents, run by the owner). It runs here because the backup is a pull and only
   this machine can perform one; it reaches the VPS over SSH for every step that
   needs the client secret, which never leaves that host.
2. It runs the **backup canary** first — builds a probe archive on the VPS, pulls
   it, decrypts it, deletes it — and **refuses to continue if that fails.** This
   is the last moment anything can be refused (step 4).
3. It prints a Link URL. Open it **in this Mac's browser**. **Enter credentials
   and MFA there** — that page is Plaid's; neither machine sees them.
   **Confirm the institution *and* the specific login before you finish this
   step**, because finishing it is what spends the slot.
4. **Finishing Link is the irreversible moment (F2a).** Plaid creates the Item
   when Link succeeds and only then hands back a `public_token`. Everything
   after this is recovery of something that already cost a slot.
5. The redirect page shows the `public_token`. **Paste it into the waiting
   `link.sh` prompt promptly — this is not a formality.** It expires in about
   thirty minutes, and it is the only route from the Item Plaid just created to
   an `access_token` this project can use. Close the tab without pasting and the
   slot is spent on an Item that can never be read; there is no recovery for
   that, from Plaid or from us.
6. The script exchanges it on the VPS, writes the `access_token` via `TokenStore`
   (mode 600) **before** recording the item row, then immediately builds and
   **pulls** a fresh archive and verifies it — reporting the Link complete only
   once a verified second copy is on this Mac (§14a).
7. Link the **highest-value institutions first** — slots are permanent (**F2**).

**Before you start step 2, make sure this Mac is awake and on the tailnet.**
*(Rev 13; sharpened in rev 14, because rev 13 got the boundary wrong.)* Rev 13
said the exchange was the irreversible step and put the check before it — one
step too late, since Link creates the Item first (**F2a**). And rev 13's check
was *reachability*, which proves the network and not the backup: a full disk, an
unreadable key or a missing destination directory all answer a ping and fail a
restore. **The one place this design can convert an irreversible risk into a
recoverable one is before Link opens**, and the only thing worth checking there
is the mechanism itself, end to end.

**Step 3 — Stand up the daemon on the VPS** (~20 min, once; agents prepare
everything, the owner runs it)

*(Rev 10 replaced two mutually-exclusive step 3s — one per O5 branch — with this
one. The Cloudflare branch's step 3a was the longest procedure in this document:
an account to create, a Worker to deploy, and a login/logout bracket around every
`wrangler` command with a browser session to close and verify from a second
device. All of it is gone with the third party it protected.)*

1. **Harden the host** (§15.1), from the provided script: key-only SSH
   (`PasswordAuthentication no`), a firewall opening **only SSH** — v0 has no
   public inbound service at all (§8.4) — unattended
   security upgrades, and a dedicated unprivileged service user that owns the
   database and the secrets.

   **The script does not touch `PermitRootLogin`, at all.** *(Rev 14. Rev 12
   identified this defect and fixed it in §15.1, then left this step — the part
   the owner actually executes — still saying the script applies
   `PermitRootLogin no`. A correction that lands only in the rationale and not in
   the procedure has not been made.)* **This is not a fresh host:** it was the
   owner's Tailscale exit node before this project existed and he administers it
   as `root`. Restricting root login here can lock him out of infrastructure that
   is not ours, unattended, while he is not watching.

   So the ordering *is* the requirement, and it is his call to make:
   1. A non-root account with `sudo` and his key must exist.
   2. He must **verify it works from a second, separate session** — one that is
      still open when the first one breaks — before anything changes.
   3. Only then does the script **print the proposed change** for him to apply
      himself. If he declines, that is recorded as his decision on his own
      machine, not worked around. *Hardening that can strand the owner is not
      hardening.*
2. **Install the two units** (§13): `networth-sync.timer`/`.service` and
   `networth-serve.service`. Confirm `networth-serve` is listening on the
   **tailnet address only** — `ss -ltnp` must not show the snapshot port on
   `0.0.0.0`. This is the one misconfiguration that silently publishes the
   endpoint, so it is checked by hand once here and by a test forever after.
3. **Put the secrets in place** under the service user (§15), mode 600 — both
   `plaid.env` and `plaid-sandbox.env`, since a rehearsal needs its own
   credential and its own database.
4. **Confirm the host publishes nothing.** `ss -ltnp` shows the snapshot port on
   the tailnet address and nothing on a public one, and `tailscale funnel status`
   shows no funnel configured. *(v0 has no webhook endpoint — §8.4. This step
   exists because "no public service" is a property that decays silently, and the
   only way to notice is to look.)*

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
