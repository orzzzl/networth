# DESIGN — networth

Status: **building** — the design phase closed on 2026-08-31 and tasks are being
implemented; `tasks/README.md` is the live state. *(Rev 19: this line still read
"proposed (design phase; nothing implemented)" with three tasks merged.)*
Author: Claude. Reviewer: Codex.

Revision 23 — **The guard asked which tokens were present; `ssh` asks a
different question, and the two disagree.**

- **Two edits kept every token rev 22 looked for and unpinned the sequence
  anyway** (Codex, round 2). Command-line settings are *first-value-wins*, so
  `-o IdentitiesOnly=no` placed in front of the shipped `-o IdentitiesOnly=yes`
  leaves the effective setting `no` — a loaded agent may then authenticate a
  command whose `-i` is wrong, which is the entire thing that `-i` was made
  load-bearing for. And identities *accumulate*, so a second `-i` rides along
  while a guard reading only the first `-i` reports that step 1a's key is what
  authenticated. Both measured with `ssh -G`, OpenSSH 10.2, on the machine §19
  names, rather than reasoned about.
- **Probing that fix turned up three more of the same class, none of them
  reported.** `-o IdentityFile=k` is an identity spelled the long way and was
  invisible to a check that read `-i`; `-F file`, `-Ffile` and `-4F file` each
  carry in an identity from a file nothing reviews; `-4i k` is an identity in a
  flag bundle. Enumerating the ways to widen a command is the losing side of
  this exchange — `-o PreferredAuthentications=password` leaves keys out of it
  altogether — so the rule is **inverted rather than extended**. The module
  reads four spellings, `-i x`, `-ix`, `-o x`, `-ox`, and two setting names;
  any other option-looking token on a remote command **fails** until the module
  is taught it. That is one reviewed line to add, against the alternative of
  trusting a token nothing read.
- **Round 3 found that same sentence one word further along: *which* tokens,
  never *where*.** Option parsing stops at a position, and the two programs stop
  in different places. Moving the shipped `-i` and `-o` behind
  `'bash /root/host-state.sh'` — every token still present, in the same order
  relative to each other — leaves `ssh` authenticating with whatever an agent
  holds, and the guard called that pinned. The boundary is now modelled per
  program, measured rather than assumed: **`ssh` resumes after its destination**
  (`-i` before the host and `-i` after it accumulate) and stops at the remote
  command; **`scp` stops at its first path operand** (`scp a -i k dst/` copies a
  file literally named `-i`). The fix was then run against `ssh -G` itself over
  **2 532 mutants** of the six commands — the shipped options moved to every
  position, and six widening edits injected at every index — with **no case
  where the guard says pinned and `ssh` disagrees**. The 55 where it is
  *stricter* than `ssh` are all `-o Compression=yes` and `--`: the fail-closed
  rule above, doing what it says. *(Two of the three anomalies that sweep threw
  up were defects in the **oracle**, not the guard — `ssh -G` omits identity
  files that do not exist, so a differential probe using an absent key cannot
  see the widening it just injected. Written down because a check that cannot
  fail is the failure mode this row keeps producing.)*
- **What must stay green is asserted beside it, and is the harder half.** The
  identity written as `-o IdentityFile=`, the same key named twice, a redundant
  second `IdentitiesOnly=yes`, and a `=no` *behind* the shipped `=yes` are all
  commands OpenSSH treats exactly as the shipped one, and all still pass. The
  last is the same pair of settings as the mutation above with the order
  swapped, so the opposite verdicts are the claim that precedence is modelled
  and not occurrences counted. A guard that reddens a sequence which
  authenticates correctly teaches the next author that the guard is the thing
  to delete — rev 22 shipped exactly that defect in its controls and it was
  caught before review.

Revision 22 — **Found by walking rev 21's own procedure on the machine it names,
in the minute after task `28` merged and before handing it to the owner.**

- **The runbook could not authenticate.** Rev 21 spent three rounds making the
  sequence extract *reviewed* bytes and report a *real* status, and left every
  `ssh` and the `scp` with no identity. On `zelengs-macbook-air-2` there is no
  `~/.ssh/config`, no default identity file and no key in the running agent, so
  `ssh root@100.102.245.37` is `Permission denied (publickey)` — the owner's
  paste would have died on its first `scp`, having proved only that the
  extraction works. §19 step 3.1 now passes
  `-i ~/agents/secrets/networth-vps.key -o IdentitiesOnly=yes` on all six remote
  commands. *(The refusal, and the `-i` that cures it, were both verified
  against the host itself, read-only, before `S0` was captured.)*
- **The guard shipped with this fix did not enforce what it said it did** —
  Codex's review of the fix, and the same defect class one level in. It read
  assignments from the whole block regardless of order, matched the key by
  basename, and never looked at `IdentitiesOnly` at all, so it stayed green
  under three edits that each break authentication: the `vps_key=` line moved
  below its six uses, the path changed to `/tmp/networth-vps.key`, and
  `IdentitiesOnly=yes` dropped from the `scp`. `tests/test_owner_runbook.py`
  now resolves variables **in order**, compares against step 1a's **exact**
  path (and checks step 1a still names it), and requires `IdentitiesOnly=yes`
  on every remote command; each of those three edits, plus a seventh bare
  `ssh`, an unassigned variable and `IdentitiesOnly=no`, was re-run against it
  and fails. It remains a shape check: it cannot prove the key is on the host,
  and it reads a block as a flat sequence, so an assignment nested in its own
  subshell would read as in scope.
- **The check that found it is the one this document keeps prescribing.** Rev 21
  closed with "a fix is where the next defect lives, and the check that finds it
  is walking the fixed procedure one command further than the finding did." The
  reviewed rounds each stopped at the last command they had changed; nobody ran
  the *first* command of the block on the machine it names. Reading a runbook
  cannot find a missing credential — only the machine can, which is what the
  environment-claims convention in `AGENTS.md` says and what this round is.

Revision 21 — **Codex's review of rev 20. Every finding this round is a defect
*inside a rev-20 fix*: each one closed the hole it was aimed at and left the
same class of hole one step further along.**

- **The symlink defence stopped one command short.** Rev 20 refused to follow a
  link and made `chown` non-dereferencing with `-h`; the `chmod` on the next
  line still followed one, because GNU `chmod` has no `-h` and dereferences the
  path it is given. A guard cannot fix that — the guard and the mutation are two
  pathname lookups, and the service account that owns the parent directory gets
  to act in between. §15.1's provisioning step now names no path when it
  mutates: it opens each one once with `O_PATH | O_NOFOLLOW`, refuses a link on
  the descriptor, and does the `chown`, the `chmod` and the read-back through
  `/proc/self/fd`. *(Reproduced first: `chmod 700 <link>` moved a victim
  directory from 755 to 700.)*
- **`git pull --ff-only` does not mean "reviewed bytes".** Rev 20 added it to
  close the *local* stale-checkout hole and it answers a different question: on
  a branch tracking anything other than `main` it pulls that, and on any branch
  it returns 0 while leaving a modified tracked file in place. §19 step 3 now
  extracts the two scripts out of `origin/main` with `git show FETCH_HEAD:…`, so
  the local branch and every uncommitted edit are irrelevant — and the runbook
  cannot be executed before this work is merged.
- **The runbook reported success for a failed run — again, one level out.** Rev
  20 fixed `ssh … | tee` swallowing the remote status, then ended the snippet
  with `echo "sequence exit status: $?"`, which makes the *echo* the status: the
  line printed `1` and the snippet returned `0`, in both bash and zsh. The
  status is now re-emitted by an outer subshell.

*(The pattern is worth naming, because it is the third round running: a fix is
where the next defect lives, and the check that finds it is walking the fixed
procedure one command further than the finding did.)*

Revision 20 — **Codex's review of the rev-19 step. Both findings are the same
mistake in different clothing: a procedure that produces evidence which cannot
fail.**

- **§19 step 3.1 measured the wrong thing.** It asked for the host state either
  side of *both* provisioning runs — a diff that contains the service user, the
  ownership changes and the new package, so it is expected to be non-empty and
  cannot show what it was written to show, that **re-running changes nothing**.
  Three captures now: before run 1, between the runs, after run 2. The middle
  pair is the criterion and must be empty; the first pair is the outcome of
  provisioning.
- **The command sequence hid its own failures.** Three independent commands with
  `ssh … | tee` and no `pipefail`: a failed remote run exits with `tee`'s status
  and reports success, its stderr never reaches the transcript that is kept, and
  a failed `scp` is followed by a run of whatever older copy was already on the
  host. It is now one `&&`-chained subshell with `pipefail` and `2>&1` into each
  `tee`. (The evidence for a criterion is part of the criterion; a runbook that
  can report success for a failed run is the same defect as a diff that cannot
  come out non-empty.)
- **Found while writing that fix, one layer up: the *local* copy could be stale
  too.** Closing the remote path left `scp` faithfully copying whatever
  `~/networth` happened to be sitting on, and the `sha256` check could not catch
  it — it compares the transcript against that same local file, so an old
  checkout agrees with itself. The chain was made to start with
  `git pull --ff-only` — **which rev 21 replaced**: a pull answers "did this
  branch move", not "are these the reviewed bytes".

Revision 19 — **not review-driven, and small: two claims this document made
about things outside it, corrected from the things themselves while task `28`
was being written.**

- **§19 step 3.1 named no script.** The step the owner executes said "from the
  provided script" through eighteen revisions, and there was no script — so the
  procedure could not be followed even in principle. It now names
  `scripts/provision-host.sh`, gives the exact commands, says the run happens
  **twice** because that is acceptance criterion (4), and states what an agent
  may do either side of it (`scripts/host-state.sh`, which only reads).
- **§16's "Python 3.12" was a fact about a host that runs 3.14.** Ubuntu 26.04.1
  on `tokyo-exit` ships CPython **3.14.4** and has no 3.12; CI resolves 3.12.3.
  The version is now stated as the floor it always was in `pyproject.toml`, and
  the gap between what CI tests and what the daemon will run is **issue #33**
  rather than a sentence nobody would have re-read.

Revision 18 — **Codex's seven blockers against rev 17. The theme of this round
is that rev 17's new mechanisms each read correctly in isolation and then failed
when walked along a real timeline: a deadline copied before the event that
defines it, an exchange with no claim, a freshness rule comparing two machines'
clocks, and an epoch whose own section states both of two incompatible
invariants.**

- **The recovery window is 30 minutes, not six hours (F7, §14a.1, §19, task
  `07a`).** Rev 17 read one Plaid sentence and built an owner procedure on it.
  Plaid documents **two** clocks: `/link/token/get` retains **session data** for
  six hours, while the `public_token` it returns is **one-time use with a
  30-minute lifetime**. Retrieval being possible is not the retrieved token being
  exchangeable. The runbook's *"You have six hours, not thirty minutes"* was
  exactly backwards and could have told the owner he was inside a window five
  hours after it closed. **The usable deadline is now 30 minutes everywhere**;
  six hours is retained as a *diagnostic* window only, and task `06a` must
  measure the long clock before anything may depend on it.
- **`link_flow` stored a deadline before the event that defines it, and called an
  unopened URL a stranded Item (§7, §13).** The recovery record was copied — with
  `session_retention_expires_at` already in it — *before* the URL was printed,
  but Plaid's clock starts at `finished_at`, which cannot exist before the owner
  has opened the page. The state machine is now **response-driven**
  (`URL_MINTED`/`SESSION_STARTED`/`SESSION_EXITED`/`SUCCESS_PENDING_EXCHANGE`/…),
  both deadlines are **derived from observed timestamps** and are `NULL` until
  observed, and a flow that never reached success **cannot be counted as a
  stranded slot, because no slot was spent** (F2a).
- **The one-time exchange had neither a claim nor an honest crash state (§7,
  §13, §14a.1).** Two workers could retrieve and race the exchange of a token
  Plaid documents as single-use, and the design treated `fsync`-first as though
  it removed the interval between Plaid's response and local durability. It
  cannot: no ordering makes a remote call and a local write atomic. There is now
  an **at-most-one exchange claim**, an explicit **`EXCHANGE_UNCERTAIN`**
  outcome, and the **residual permanent-loss window is stated rather than
  argued away**.
- **The canary's freshness rule compared clocks on two machines (§15).** Rev 17
  required the VPS's `built_at` to be later than the moment `link.sh` started on
  the Mac — in a document that elsewhere refuses to infer ordering from untrusted
  wall clocks. Ordinary skew could reject a fresh probe or accept a stale one.
  The probe now returns a **VPS-local generation counter** and whether this
  request **built or reused**; no Mac time is compared to VPS time.
- **The task graph allowed a Production Link before the always-on poller
  existed** (`tasks/README.md`). `08` did not depend on the task that installs
  the persistent worker, and the suggested sequence ran `08` first — leaving
  exactly the "window depends on the laptop staying open" failure rev 17 claimed
  to remove. `16` is now a hard dependency of `08`.
- **Neither copy of the `link_token` had a complete deletion contract (§14a.1,
  §15, task `18`).** The VPS cleared a row's `secret_ref` and orphaned the
  TokenStore material behind it; the Mac's reaper was told to read a flow status
  its restricted key has no verb for, and a VPS-side `doctor` was claimed to
  count files on a laptop it never contacts. Deletion is now specified on both
  sides, the Mac's reaper is **expiry-only** (topology-feasible, no new verb),
  and the two `doctor`s report **only what their own host can see**.
- **The epoch section asserted two incompatible `seq` invariants (§9.3a).** Its
  premise said `seq` never resets across pairings; its fix relied on `last_seq`
  being pairing-scoped. Both could not be true. **The epoch is removed from
  `seq`**: pairing-scoped `last_seq` plus a payload key that is deliberately not
  in the archive already closes the restore case, and does so without a second
  mechanism. `publish_epoch` survives as a **restore-lineage diagnostic**, which
  is the part that was actually earning its place.

Revision 17 — **Codex's seven blockers against rev 16. Rev 16 fixed eight
mechanisms and, in doing so, wrote four new guarantees that its own neighbouring
sections contradict. Three of the seven were verified here rather than reasoned
about: on Plaid's documentation, on the live VPS, and in `sqlite3`.**
*(Retained below; where rev 18 supersedes a claim, it says so.)*

- **The manual paste is deleted, because Hosted Link cannot produce one (F7,
  §16, task `08`).** Plaid: *"In Hosted Link, there is no frontend integration
  required (or possible)"*; the `public_token` arrives only by `SESSION_FINISHED`
  or `/link/token/get`, and `completion_redirect_uri` carries no token. **There
  is nothing on the owner's screen to paste.** The deeper error is that a
  fallback *chosen after the session completes* cannot exist at all — by then the
  slot is spent. `/link/token/get` is now the sole path, with its failure and
  retry outcome specified, and the poll moves off the laptop.
- **The `link_token` was durable on the machine whose loss is the scenario
  (§14a.1).** Rev 16 made it survive a crash and called that durability. If the
  VPS disk dies between Link succeeding and the exchange, the Mac's archive holds
  neither the `access_token` nor the `link_token`, and the Item is stranded
  **immediately**, not at the end of any window. It is now stored through `TokenStore` under a
  `link_flow` row (§7) and **pulled to `zelengs-macbook-air-2` and verified before
  the URL is printed** — the last moment this design can still refuse.
- **The "nothing is public" test asserted a property of the owner's host, and it
  is false there** (§13, task `20`). Measured read-only on the live VPS today:
  `sshd` listens on `0.0.0.0:22` **and** `[::]:22`, so rev 16's whole-`ss`-table
  criterion fails before networth is installed. The test now asserts **our
  process's** binding against the node's Tailscale addresses — plural: this host
  has a tailnet **IPv6** too — and separately forbids *new* public listeners
  against a baseline captured at deploy.
- **`id CHECK (id = 1)` is not a singleton** (§7). `CHECK` constrains values, not
  multiplicity. Measured in `sqlite3` 3.51.0: two rows with `id = 1` both insert.
  `id INTEGER PRIMARY KEY CHECK (id = 1)` rejects the second.
- **The manifest authenticated a token *set*, so the drill's stated check could
  not fail** (§14a.1). Swap two `access_token`s between two Items and the set —
  and its hash — are unchanged. The fingerprint now binds the Item identity, the
  manifest hashes a canonical **mapping**, and swapping is a required negative
  test. AEAD proves the bundle was not edited; it cannot make an
  under-specified check prove a mapping.
- **`build-archive` on the restricted key was an unbounded work trigger** (§15),
  priced in the design as "one `VACUUM INTO`" — but nothing bounded the number of
  invocations. `build-archive current` **leaves that key entirely**; `probe` gets
  single-flight, a 60-second freshness no-op, one fixed path and bounded
  retention, with a burst test.
- **Rev 16's `seq` fix inferred unseen state from wall time, which §9.1 refuses
  to do** (§9.3a). Forward clock jump → publish → correction → restore from a
  pre-jump archive, and `max(seq+1, now)` is *still* below the phone's high-water
  mark. Replaced by an **`epoch` the restore itself increments**, with the phone's
  `last_seq` **scoped to its pairing** — which a restore already forces, since the
  payload key is deliberately not in the archive. That removes the clock
  assumption **and takes back the protection rev 16 traded away**: a rolled-back
  daemon is refused again. The clock was the price of that trade, not
  recoverability.
  *(**SUPERSEDED in rev 18**: of those two mechanisms only the pairing scope was
  needed, and asserting both made the section self-contradictory. The epoch is
  out of `seq`; the rolled-back daemon is still refused, by the pairing scope —
  a rollback does not re-pair. §9.3a.)*
- **Stale instruction, in the step the owner executes:** runbook 1a still said
  the dispatcher allows "the two verbs" after §15 grew to four. Corrected, and
  the sweep is the standing one — a fix that lands only in the rationale has not
  been made.
- **Then I walked the ordinary Link and pull flows through rev 17's own new
  machinery, and two of these fixes had holes** (§15, §14a.1). The probe's
  60-second rate limit would have **broken the pre-Link canary**: two Links
  back-to-back put two canaries in one window, and the second would have accepted
  a probe built before it started — proving the transport while skipping the build
  step that catches a full disk. It now requires a probe newer than itself and
  waits out the cooldown. And the `link_token` second copy had **no reaper**: the
  VPS clears its row at exchange, nothing was made responsible for the Mac's
  copy, so every successful Link would have left a credential on the laptop
  permanently. `link.sh` and the puller both reap it now, and `doctor` counts what
  is left. *Same pattern as the seven above, one revision later: a new guarantee
  that reads correct until the ordinary flow is walked through it.*

Revision 16 — **Codex's eight blockers against revs 14–15. Two of this design's
own conclusions turn out to have been wrong, and both were checkable for free
from Plaid's public documentation the whole time.**

- **A lost `public_token` is *not* an unrecoverable, permanently stranded slot
  (F7, §14a.1).** *(Rev 18 narrows the window this claim rests on: retrieval is
  real, but the retrieved token lives **30 minutes**, not six hours.)* Plaid
  retains a completed Link session for six hours and
  `/link/token/get` returns its `public_token` — an **outbound** call from the
  VPS, which already holds the `link_token` it minted. Measured on the live
  account: Hosted Link mints on this Trial plan with no enablement, the endpoint
  is callable, the hosted URL lives 30 minutes, and `link_sessions` is **absent
  entirely** — not empty — before completion. **This design accepted a permanent
  loss of its scarcest resource on an unchecked assumption, for five revisions,
  because the assumption sounded like prudence.**
- **Consequences:** the manual paste becomes a fallback *(**SUPERSEDED by rev
  17**: Hosted Link cannot produce a paste — there is nothing on the owner's
  screen to copy, so `/link/token/get` is the sole path)*; **task `07a` is
  un-dropped** (rev 10 killed it on a topology argument that never applied to
  this mechanism); **task `07` is deleted** — Hosted Link hosts the OAuth
  redirect, so there is no page and no dashboard registration, and **this project
  now puts nothing at all on the public internet**; new task `06a` proves the
  retrieval in Sandbox, where proof is free, and gates `08`.
- **A missed migration deadline costs an outage, not a slot (F8).** The Item
  lands in `ITEM_LOGIN_REQUIRED` and update mode restores **the same Item**. That
  closes rev 15's named reversing argument — but the same check found rev 15's
  reasoning was about **the wrong event**: `PENDING_DISCONNECT` is webhook-only,
  invisible to `/item/get`, and arrives **seven days** ahead. The trade is a rare
  preventable outage versus a public write surface, not "seconds versus an hour".
  Recommendation unchanged; re-opened as **O9** because the owner decided on the
  bad argument.
- **The backup was not yet disaster recovery.** The drill validated archives
  against a row on the VPS — the machine whose loss is the whole scenario — so
  evidence moves into an **authenticated manifest sealed inside the archive**
  (§14a.1), and the drill must pass with **no network path to the VPS**. The
  forced-command key could not run the write-back it was specified to perform
  (`command=` ignores the client's command), so it becomes a **dispatcher** over
  `SSH_ORIGINAL_COMMAND` (§15). `backup_state` gives `key_escrow_confirmed_at`
  and `last_verified_restore_at` somewhere to live (§7).
- **A restore would have been rejected by the phone (§9.3a).** An opportunistic
  archive can be weeks old, so a restored daemon republished `seq` values I6
  refuses — for as long as the gap — and the only workaround was teaching the
  owner to dismiss the integrity warning. **`seq = max(stored_last_seq + 1,
  unix_millis(now))`** fixes it by construction, with no change to the phone.
  *(**SUPERSEDED by rev 17**: the defect and the goal stand, the formula does
  not — it assumes a monotone wall clock, which §9.1 refuses to assume.)*
- **"One writer, no contention to reason about" was never true** (§13):
  `record-pull` arrives unattended on the Mac's own schedule while the 5-minute
  worker runs. `busy_timeout`, `BEGIN IMMEDIATE`, retry and two collision tests.
- **The "nothing is public" test did not test it** (§13, task 20): "not
  `0.0.0.0`" is passed by the public IPv4, by `[::]`, and by loopback-only.
- **The stale-claim sweep rev 14 announced had missed several live instructions**
  — three units where there are two, "the VPS knows nothing of the pull", a
  secret rule forbidding the code §15 authorises, "two routes" above the
  one-route table. Fixed in the instructions, not only in the narrative.

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
  back to two units and **one long-running writer** — *rev 16: this said "one
  writer", which was false in rev 13, false here, and is what §13 now states
  correctly*), the `webhook_event` table, the `WebhookReceiver` seam, the Funnel
  dependency and its two unverified assumptions, the JWT/`kid`/constant-time
  verification surface, and task 20's backfill obligation. **Blockers 3 and 4 are
  void by removal, not by fix** — the same distinction rev 10 had to make.
- **The owner's premise is corrected in his favour, and it does not change the
  answer.** A domain is *not* required and there is no recurring cost: Tailscale
  Funnel would have supplied a domain-form HTTPS URL with a managed certificate
  for free. Webhooks are being dropped because the benefit is small, not because
  the bill was large.
- ~~**One argument could reverse this**~~ — **rev 16 checked it (F8): it does
  not.** Update mode restores the same Item, so no permanent slot is at risk.
  But the check also showed **this entry's own reasoning targets the wrong
  event** — see rev 16 above and **O9**.
- §8.4a is kept as a **costed plan that is deliberately not built**, so re-adding
  the accelerator is a decision rather than a redesign.

Revision 14 — **Codex's seven blockers against rev 13, plus two owner
corrections that arrived while the review was being written.** The blockers
cluster at the seams rev 10's host move created, which is the honest summary:
deleting a subsystem is not the same as re-checking everything that referenced
it.

- **The Plaid slot is spent by a successful *Link*, not by the exchange** — and
  every gate in this document was built one step too late. Link hands back a
  `public_token` only *after* the Item exists, so by the time anything can be
  done with that token the slot is gone. §14, §19 step 2 and task 08 now model the whole
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
  unit; **rev 15 removed the receiver**, so §13 is back to two units — *and rev
  15 then repeated the "one writer" claim a third time, still wrongly; rev 16
  narrows it to **one long-running writer** and specifies the locking discipline
  the command writers need (§13).*
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
the thing the ten counts — exists *before* the `public_token` is retrievable at
all, and nothing after that point can un-spend it. §14a.1, §19 step 2 and task 08 are
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

**F7 — a completed Link's `public_token` can be retrieved from the backend, so a
lost browser tab is not a stranded slot.** *(Rev 16, from review, and **measured
on this account** rather than read from the docs alone.)* Plaid's
`/link/token/get` returns a `link_sessions` array whose
`results.item_add_results[].public_token` is the token for a completed session.
The design's own `link_token` is the key to that call — so the token can be
fetched **without an inbound route, a webhook, or a human copying anything.**

> **The `link_token` must therefore be durable before the URL is opened, and it
> must be durable on more than one machine.** If it lived only in the memory of
> the SSH command that minted it, then *the very failure F7 exists to survive* —
> something goes wrong between Link succeeding and the token being exchanged —
> would also destroy the means of recovery, and this fact would be a guarantee
> that evaporates in exactly the case it is claimed for. So it is written through
> `TokenStore` under a `link_flow` row (§7) **before the URL is printed**, and at
> a successful exchange **the TokenStore material is deleted and only then is
> `secret_ref` cleared** — in that order, so a crash between the two leaves a
> dangling reference (visible, reapable) rather than an unreferenced credential
> (invisible, immortal). *(Rev 18, from review: rev 17 cleared the reference and
> said nothing about the bytes it referenced, which is how a secret becomes an
> orphan rather than being reaped. §15 owns the reaper.)* It is a short-lived
> credential that can retrieve a `public_token`, so it lives under
> `/etc/networth/` with the other secrets (§15), never in a log, and never as a
> command-line argument. A flow interrupted at any point is resumable with
> `link.sh --resume`, which re-attaches to the flow and reports — the VPS's own
> job has been working it the whole time (§13) — so a dead terminal does not
> pause the exchange. *(Rev 17 ended this sentence "makes the six-hour window
> usable by a person"; rev 18 deletes that clause. The window a person could act
> inside is 30 minutes, and `--resume` is not what makes it usable — the always-on
> poller is. See the two-clock table above.)*

**Rev 16 wrote that paragraph and stopped one machine short, which is the same
mistake it had just finished correcting elsewhere** *(rev 17, from review)*.
"Durable on the VPS" survives a **process crash**. It does not survive **loss of
the VPS** — and §14a exists for precisely that scenario. Walk it: the owner
completes Hosted Link, the slot is spent, and the VPS disk dies before the
exchange. The Mac's newest archive predates the Link, so it holds neither an
`access_token` for this Item nor the `link_token` that could still fetch one.
`/link/token/get` cannot be called at all. **The Item is stranded immediately —
not at the end of any window**, and F7's recovery path is unreachable. The same
sentence that fixed the crash case read as though it had fixed the disaster case.

> **So the recovery record gets a second copy, and it is verified before the URL
> is printed.** `link.sh` mints the token on the VPS, writes the `link_flow` row
> and the token, then **pulls the recovery record to `zelengs-macbook-air-2`,
> `fsync`s it, and reads it back** — `flow_id`, `link_token`, `minted_at`,
> `hosted_url_expires_at`, and a locally-computed `reap_after`. Only then does it
> print the URL, stamping `second_copy_verified_at`/`second_copy_holder`. If any
> step fails it **refuses to print the URL**, and refusing costs nothing: by
> **F2a** no slot is spent until Link completes, so this is the same "last moment
> the design can still refuse" that the backup canary already occupies (§14a.1).

**What that record may not contain is a deadline, and rev 17 put one in it.**
*(Rev 18, from review — and it is the same class of error as the two clocks
above: a number written down at a moment that cannot know it.)* Rev 17 copied
`session_retention_expires_at` into the record **before the URL was printed**.
Both of Plaid's clocks start at the session's `finished_at`, and at the moment
this copy is made the owner has not opened the page — there is no `started_at`,
no `finished_at`, and therefore no deadline to record. A value written there
would have been **guessed from mint time**, which is not the deadline the
document claimed it was.

So the record carries only what is known at mint time: the identifiers, the
token, `minted_at`, the **URL** expiry (a mint-time clock, measured at 30
minutes — §4 probe table), and `reap_after`, which is a *local hygiene bound*
rather than a recovery deadline (§15). The real deadlines are **derived from
observed timestamps** once the session reports them, and until then they are
`NULL` and every reader must treat them as unknown rather than as passed.

This is cheap for a reason worth stating rather than assuming: **the `link_token`
is the half that cannot be re-obtained, and the Plaid credential is the half that
can.** `/link/token/get` needs `client_id`, `secret` *and* the `link_token` — so
the copy on the Mac is inert on its own, which is why it may sit next to the
backup key without widening anything (§15). If the VPS is gone, the owner
re-reads `client_id`/`secret` from Plaid's dashboard, where they have been all
along, and the `link_token` is the one input no dashboard can reissue. The
recovery is therefore an **owner-attended manual procedure inside the 30-minute
window** (§19 step 2a), and it is written down as one rather than automated: the
Mac must not hold the client secret (§15), and a disaster on a 30-minute clock
means the owner is present by construction — he finished Link minutes ago.

**Thirty minutes is a tight budget for a procedure, and the honest consequence
is stated rather than absorbed.** *(Rev 18.)* Rev 17 sized this procedure against
six hours, where "read two values off a dashboard and run a command" is
comfortable. Against 30 minutes it is not comfortable, and the design's answer is
not optimism but **pre-staging**: §19 step 2a is one command that already knows
the flow id and prompts only for `client_id`/`secret`, so the owner's work is a
paste from his password manager and not an exercise in reading this document
under time pressure. **If the 30 minutes pass, the slot is stranded whatever
copies exist** — the second copy makes recovery *possible*, it does not make it
*leisurely*, and no amount of local durability extends a token lifetime Plaid
controls. That residual is the price of the disaster case, and it is priced here
rather than discovered during one.

The pull uses the **interactive** key `link.sh` already holds, not the restricted
backup key — so the unattended dispatcher gains no verb that can read a
credential (§15).

**And the second copy has to be reaped, which the paragraph above did not say and
therefore did not do.** *(Also found by walking the flow: on the happy path the
VPS clears the `link_flow` row's `secret_ref` at exchange, and nothing had been
made responsible for the Mac's copy — so an ordinary successful Link would leave a
credential on the laptop forever, which is exactly the accumulation §15 exists to
prevent.)* So: `link.sh` deletes its local recovery record as soon as the flow
reports `EXCHANGED` — it holds the interactive key and has just read the flow, so
on the happy path the record's life is the length of one Link.

**The unattended reaper is expiry-only, because rev 17's version asked a question
the Mac has no way to ask.** *(Rev 18, from review.)* Rev 17 told the puller to
delete any record "whose flow is no longer `OPEN`" — but the puller authenticates
with the **restricted** key, whose dispatcher allow-lists four verbs and none of
them reads a flow's status (§15). The instruction was not merely unimplemented;
it was **unimplementable on the stated topology**, and the alternative — adding a
status verb — widens an unattended key to buy a few hours of tidiness. So the
Mac's contract is the one that needs no conversation:

- **`link.sh` deletes on success** (interactive key, knows the outcome).
- **The puller deletes any record whose `reap_after` has passed**, where
  `reap_after` is written **by the Mac, from the Mac's own clock, at the moment
  the record is created**: `mac_now + 30 min (URL lifetime) + 30 min (token
  lifetime) + 6 h (retention) `, rounded up to **7 hours**. It is deliberately
  *not* `minted_at + …`: `minted_at` is stamped on the VPS, and deriving a Mac
  deadline from it would re-introduce the cross-machine clock comparison this
  document refuses (§9.1 rule 1) — the same defect rev 17 shipped in the canary.
  Written on one machine, compared on that machine, and generous by construction,
  because the cost of reaping late is one inert file and the cost of reaping
  early is destroying the disaster copy while the flow is still live. No remote
  read, no new verb, no shared clock.
- **The residual is named:** if `link.sh` dies between a successful exchange and
  its own cleanup, one inert record lingers until `reap_after`. It is inert —
  `/link/token/get` also needs `client_id`/`secret`, which are not on this machine
  — and its worst case is bounded in hours rather than forever, which was the
  actual defect.

**And the two `doctor`s report only what their own host can see.** Rev 17 had a
VPS-side `doctor` reporting "the count of local recovery records" — a count of
files on a laptop it never contacts. `networth doctor` on the VPS reports
**`link_flow` rows and their states**; `networth doctor --local` on the Mac
reports **recovery records on disk and their `reap_after`**. Neither stands in for
the other, and saying which host answers which question is the difference between
a diagnostic and a claim. A short-lived credential is short-lived, not harmless.

Three bounds come with it, and all three are real constraints rather than
caveats:

- **Two clocks run here, not one, and rev 17 collapsed them into the wrong
  number.** *(Rev 18, from review.)* Plaid documents them separately:
  `/link/token/get` provides **session data for up to six hours after the session
  has ended**, while a `public_token` is **one-time use with a lifetime of 30
  minutes** ([Link API](https://plaid.com/docs/api/link/),
  [Items API](https://plaid.com/docs/api/items/)). Retrieval being possible is
  not the same fact as the retrieved token being exchangeable, and **nothing in
  the Hosted Link contract says retrieval refreshes the token.** So:

  | Clock | Starts at | Length | What it bounds |
  |---|---|---|---|
  | `public_token` lifetime | session `finished_at` | **30 min** | **the usable recovery window.** After it the token cannot be exchanged and the Item is stranded |
  | session-data retention | session `finished_at` | 6 h | **diagnostics only** — `/link/token/get` still answers *what happened*, and `doctor` can still say which session stranded which slot |

  **The usable deadline is therefore 30 minutes**, and the six hours buys an
  explanation rather than a recovery. This design assumes the short clock
  everywhere and treats the long one as a record. Task `06a` is extended to
  measure it — a Sandbox session left to sit **past 30 minutes** and then
  exchanged — and **only a passing measurement may widen this number.** Until
  then the 30-minute bound stands, because the direction of a wrong guess here is
  a permanently stranded slot.

  *(Rev 17 wrote "six hours is the whole recovery window" and built an owner
  procedure on it. That procedure could hand the owner an expired token five
  hours late while telling him he was inside the window — the same class of error
  as the rev-15 paste claim it replaced: an unchecked reading of the docs pointed
  at the scarcest resource in the project.)*
- **By default the endpoint returns complete event data only for *Hosted Link*
  sessions.** Retrieving it for ordinary Link sessions requires an
  account-manager enablement this project will not ask for. **So the flow must
  use Hosted Link** — not a preference, a precondition of F7 holding at all.
- **Hosted Link itself needs no enablement, and the free path is the plain URL.**
  Plaid's *Link Delivery* (sending the URL by SMS or email) carries a per-link
  fee and is therefore out of scope under §4's zero-marginal-cost rule; opening
  the returned URL directly costs nothing.

**What was measured, on 2026-08-31, against the live Production credential and
without creating an Item** (`/link/token/create` does not spend a slot — **F2a**
puts that at a successful Link):

| Probe | Result |
|---|---|
| `/link/token/create` with `hosted_link` | **HTTP 200 with a `hosted_link_url`.** Hosted Link is available on this Trial account with no account-manager involvement |
| `/link/token/get` on that token | **HTTP 200** — the endpoint is callable on this plan |
| Hosted-link token lifetime | **exactly 30 minutes** (a plain link token minted in the same run got 4 hours). `url_lifetime_seconds` can widen it |
| `link_sessions` on a token with no completed session | **absent — the key is not in the response at all**, not an empty array. A poller that treats a missing key as an error will break on every poll before the owner finishes logging in |

**What could not be measured here, stated as the gap it is:** a *completed*
session's `public_token` was not observed, because completing a Production Link
spends one of the ten lifetime slots — the exact resource this section exists to
protect, so it is not a thing to spend on a test. The proof belongs in Sandbox
(task `06a`), and until it passes, F7 is documented-and-partially-measured rather
than proven end to end.

**Rev 16 answered that gap with a manual-paste fallback, and there is no such
thing to fall back to.** *(Rev 17, from review, and checked on Plaid's
documentation rather than reasoned about.)* Hosted Link states the boundary
outright: **"In Hosted Link, there is no frontend integration required (or
possible)"**, the `public_token` is delivered by `SESSION_FINISHED` or
`/link/token/get`, and `completion_redirect_uri` — the only thing the browser
ever returns to — explicitly carries no token; Plaid's own instruction there is
to *"listen for the `SESSION_FINISHED` webhook or call `/link/token/get`"*. With
webhooks deleted from v0 (§8.4), **`/link/token/get` is not the primary path, it
is the only path.** Nothing ever appears on the owner's screen for him to copy,
so the fallback was not a weak option — it was an instruction that cannot be
followed, printed at the exact moment the owner is watching a slot burn.

**The deeper error is a shape worth naming, because it is not specific to
Plaid: a fallback chosen *after* the irreversible step cannot be a fallback.**
By **F2a** the slot is spent when Link completes. Every path after that point is
recovery of something already paid for, and recovery has to be a *mechanism that
already works*, not an alternative selected once the primary one has failed. The
place this design can still branch is **before** the URL is printed — which is
where the backup canary and the `link_token` second copy both now sit.

So the paste is **deleted**, and three things move to carry the weight it was
pretending to carry:

- **`/link/token/get` is the sole path, with its failure behaviour specified**
  rather than escaped: poll immediately, then on a bounded schedule until either
  a `public_token` arrives or `session_retention_expires_at` passes. A missing
  `link_sessions` key is *not* an error (it is the measured pre-completion
  response above); a transport failure is retried; a `link_token` Plaid rejects
  is terminal and reported as such. There is no branch that asks the owner for
  anything, because there is nothing he could supply.
- **The poll runs on the VPS, not on the laptop.** It is a due-ness job like
  every other (§13), driven by the `link_flow` row. Rev 16 had `link.sh` polling
  from the Mac, which put the exchange deadline on a process running on a
  machine whose lid closes — the window would have depended on the owner not
  shutting his laptop. `link.sh` now watches and reports; the always-on host is
  the thing that must not stop. *(Rev 18: that deadline is **30 minutes**, not
  the six hours rev 17 wrote here — which makes this the right host by a wider
  margin than the argument originally claimed.)*
- **`06a` stops being a formality and becomes the gate that matters.** With no
  human path, an automatic retrieval that does not work in Production strands the
  first Item. `06a` proves the *completed-session* retrieval in Sandbox, where
  proof is free, and it is a hard dependency of `08` — not a recommendation, and
  no longer softened by a fallback that was never there.

**What this does not do is invent safety.** If Plaid's retrieval is broken during
the **30 minutes** the `public_token` is alive, the slot is stranded; that
residual is real, is unchanged by deleting the paste (the paste could not have
saved it either), and is what `doctor`'s *Items with no reachable token* count
exists to surface while the window is still open (§14a.1).

**F8 — a missed migration deadline costs an outage, not a slot.** *(Rev 16, from
review; verified against Plaid's docs.)* `PENDING_DISCONNECT` fires **seven days
before** a scheduled disconnection, for `INSTITUTION_MIGRATION` or
`INSTITUTION_TOKEN_EXPIRATION`. If the deadline passes with nothing done: *"One
week after the `PENDING_DISCONNECT` webhook was fired for a given Item, if the
Item has not yet gone through update mode, it will be disconnected from the old
API and will enter the `ITEM_LOGIN_REQUIRED` error state"* — and *"sending the
Item through update mode will move it to the new API and restore it to a healthy
state."* **The same Item, restored; no new Item, no new slot.** This closes the
one unverified fact rev 15 named as able to reverse the webhook decision (§8.4):
advance warning protects *availability*, never a lifetime slot.

**The other half of the same fact cuts the other way, and §8.4 had it wrong:**
`PENDING_DISCONNECT` is **delivered only as a webhook and is not exposed by
`/item/get`.** An Item scheduled for migration is not in an error state, so
polling cannot see the pending disconnect at all — not late, *never*.

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
   │  shows BOTH staleness dimensions (§9)        │     │ VPS holds its
   │  AND is the only place alerts appear (§11)   │     │ key, records
   └──────────────────────────────────────────────┘     │ pulled_by; has
                                                        │ NO address for
                                                        │ it, and never
                                                        │ initiates
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
  id, archive_id,                                  -- archive_id is minted BEFORE the build and
                                                   --   is the only name shared with the manifest
                                                   --   sealed inside the archive (§14a.1)
  built_at,                                        -- when the VPS produced it
  archive_sha256, byte_size,                       -- over the SEALED bundle, so these cannot live
                                                   --   inside it: a hash cannot contain itself.
                                                   --   Transfer bookkeeping, NOT restore evidence
  manifest_sha256,                                 -- over the manifest bytes; lets the Mac say
                                                   --   "same manifest" without the VPS
  pulled_verified_at,                              -- NULL until zelengs-macbook-air-2 has pulled
                                                   --   it AND proved it decrypts and reconciles.
                                                   --   Written by the Mac over SSH; the VPS never
                                                   --   reaches out to learn it (§14a)
  pulled_by,                                       -- the full tailnet name that claimed the pull:
                                                   --   four Airs differ only by suffix (§19 1b)
  verify_error)                                    -- why a pull failed verification, if it did

-- Rev 16 MOVED db_row_counts_json, item_count and the token fingerprint digest
--   OUT of this table and into the manifest sealed inside the archive. They were
--   the values the restore drill compares against, and keeping them here meant
--   validating an archive against a row on the machine whose loss is the whole
--   scenario. See §14a.1: restore evidence goes inside, transfer bookkeeping
--   stays here. Rev 17 renames that digest to item_token_binding_sha256, because
--   what it must seal is a MAPPING and rev 16 sealed a set (§14a.1).

backup_state(                                      -- §14a.1 criteria 2 and 3. A singleton: these
                                                   --   are facts about the backup ARRANGEMENT,
                                                   --   not about any one archive, and rev 15 had
                                                   --   nowhere normative to put them while
                                                   --   §14a.1 required them and task 18 read them
  id INTEGER PRIMARY KEY CHECK (id = 1),           -- rev 17, from review: `id CHECK (id = 1)` is
                                                   --   NOT a singleton. CHECK constrains the VALUE,
                                                   --   never the multiplicity, so two rows with
                                                   --   id = 1 both insert and doctor, attest-key
                                                   --   and record-drill each get two answers.
                                                   --   Measured, not reasoned: in sqlite3 3.51.0
                                                   --   the CHECK-only form accepts both inserts;
                                                   --   INTEGER PRIMARY KEY rejects the second with
                                                   --   `UNIQUE constraint failed`. INTEGER PRIMARY
                                                   --   KEY is the rowid alias, so it is implicitly
                                                   --   UNIQUE NOT NULL and needs nothing else
  key_escrow_confirmed_at,                         -- criterion 2. The OWNER's attestation that an
                                                   --   offline copy of the backup key exists.
                                                   --   Never a verified fact; doctor must label
                                                   --   it as a claim (§14a.1)
  last_verified_restore_at,                        -- criterion 3. Written by the Mac's drill over
  last_verified_restore_archive_id,                --   SSH, same one-way path as record-pull
  last_verified_restore_error)

-- The migration INSERTs the row, so every reader finds it and no writer has to
--   decide whether to create it. Every write is
--   `INSERT INTO backup_state(id, …) VALUES (1, …) ON CONFLICT(id) DO UPDATE SET …`
--   — an upsert against a constrained id, not an INSERT that hopes to be first.

daemon_state(                                      -- rev 17 as the publication epoch; rev 18 keeps
                                                   --   the table and REMOVES it from seq (§9.3a).
                                                   --   It is now a restore-lineage DIAGNOSTIC: how
                                                   --   many times this daemon has been restored,
                                                   --   when and why. Nothing reads it to build a
                                                   --   payload; doctor reads it, and the owner does
  id INTEGER PRIMARY KEY CHECK (id = 1),           -- same form, same reason, and the second use of
                                                   --   it is why the fix above had to be right
  publish_epoch NOT NULL DEFAULT 0,                -- incremented ONCE per restore, by the restore
                                                   --   procedure, never by a publish
  epoch_bumped_at,                                 -- when, and
  epoch_bumped_reason)                             --   why — a restore is the only legitimate
                                                   --   reason, and an epoch that moved without one
                                                   --   is a fact doctor must be able to show
-- Seeded by the migration exactly like backup_state — INSERT the row, so every
--   reader finds it and no writer decides whether to create it; every write is
--   an upsert against the constrained id. (Rev 18: rev 17 specified this seeding
--   for backup_state and forgot it here, leaving readers of a table that had no
--   row — the same defect the paragraph above had just finished fixing.)

link_flow(                                         -- rev 17, from review: F7's recovery evaporates
                                                   --   in exactly the failure it exists for unless
                                                   --   the link_token outlives the VPS (§14a.1)
  id, flow_id,                                     -- flow_id is minted before the link_token and
                                                   --   is what --resume takes
  secret_ref,                                      -- resolved through TokenStore, like every other
                                                   --   credential here: the DB stores the NAME.
                                                   --   The link_token itself never lands in a row,
                                                   --   a log or an argv (§15)
  minted_at, hosted_url_expires_at,                -- mint-time clocks, and the ONLY deadlines
                                                   --   knowable when the row is created. 30 minutes
                                                   --   for a hosted link token, measured (§4)
  started_at, finished_at,                         -- rev 18: OBSERVED, copied from /link/token/get.
                                                   --   NULL until Plaid reports them. Every deadline
                                                   --   below is derived from finished_at, so a NULL
                                                   --   here means "unknown", NEVER "passed" (§14a.1)
  token_exchange_expires_at,                       -- finished_at + 30 min. THE deadline: after it the
                                                   --   public_token is dead and the Item is stranded.
                                                   --   doctor must say so BEFORE then
  session_retention_expires_at,                    -- finished_at + 6 h. Diagnostics only — the session
                                                   --   record outlives the token by 5.5 h and can
                                                   --   still say WHAT stranded the slot (rev 18)
  second_copy_verified_at,                         -- NULL until zelengs-macbook-air-2 holds the
  second_copy_holder,                              --   recovery record and has read it back. The
                                                   --   URL is not printed while this is NULL
  state,                                           -- rev 18, response-driven (see the table below):
                                                   --   URL_MINTED | SESSION_STARTED | SESSION_EXITED
                                                   --   | SUCCESS_PENDING_EXCHANGE | EXCHANGING
                                                   --   | EXCHANGED | EXCHANGE_UNCERTAIN
                                                   --   | URL_EXPIRED | TOKEN_EXPIRED | ABANDONED
  exchange_claimed_at,                             -- rev 18: the at-most-one exchange claim. A worker
  exchange_claim_owner,                            --   enters EXCHANGING only by a conditional UPDATE
                                                   --   off SUCCESS_PENDING_EXCHANGE; the loser does
                                                   --   not call Plaid. public_token is single-use
  exchange_attempts,                               -- >1 is itself a finding: doctor surfaces it
  last_poll_at, poll_error,                        -- the VPS-side poller's own two clocks
  item_id)                                         -- set on exchange; the row is retained after
                                                   --   EXCHANGED with its TokenStore material deleted
                                                   --   and only then its secret_ref cleared (§14a.1),
                                                   --   so "how did this Item get here" stays answerable
```

**The `link_flow` states are driven by Plaid's responses, not by elapsed time**
*(rev 18, from review)*. Rev 17 had four states — `OPEN | EXCHANGED | EXPIRED |
ABANDONED` — and a due-ness predicate that read "still `OPEN` past the deadline"
as `EXPIRED`. Walked along an ordinary timeline that is wrong twice: it expires
flows whose deadline is `NULL` because the session never finished, and it counts
**an Item that does not exist**. By **F2a** the slot is spent when Link
*succeeds*; a URL the owner never opened, or a session he exited, has cost
nothing, and reporting it as a stranded slot would train him to ignore the one
count that matters.

| State | Entered when | Slot spent? | Terminal? |
|---|---|---|---|
| `URL_MINTED` | the row is written; the URL has not been opened. `/link/token/get` returns **no `link_sessions` key** (§4, measured) | no | no |
| `SESSION_STARTED` | a session appears with `started_at` and no result | no | no |
| `SESSION_EXITED` | the session ended without an `item_add_result` | **no** | yes |
| `SUCCESS_PENDING_EXCHANGE` | `item_add_results[].public_token` is present; `finished_at` is now known, so both deadlines become computable | **yes** | no |
| `EXCHANGING` | a worker won the claim (below) | yes | no |
| `EXCHANGED` | the `access_token` is `fsync`ed through `TokenStore` and the `item` row committed | yes | yes |
| `EXCHANGE_UNCERTAIN` | the exchange call timed out, or the worker died after sending it — **it is not known whether Plaid consumed the token** | yes | yes, pending owner action |
| `URL_EXPIRED` | `hosted_url_expires_at` passed while still `URL_MINTED` | **no** | yes |
| `TOKEN_EXPIRED` | `token_exchange_expires_at` passed while still `SUCCESS_PENDING_EXCHANGE` | **yes — this is the stranded-slot state** | yes |
| `ABANDONED` | the owner ran `link.sh --abandon` on a flow he does not intend to finish, or a `URL_MINTED` row is cleaned up after its URL expired | no | yes |

**Only `TOKEN_EXPIRED` and `EXCHANGE_UNCERTAIN` are counted against the Item
budget by `doctor`** (§14), because only they follow a completed Link.
`ABANDONED` is reachable — rev 17 declared the state without ever saying what
reaches it — and it is deliberately a *no slot spent* outcome.

**The exchange is claimed, because the token is single-use and two workers can
reach it.** `link.sh` triggers an immediate poll while the 5-minute timer also
polls; "trigger" is therefore defined narrowly as **`systemctl start` on the same
unit**, which systemd coalesces — a start against a running unit is a no-op, so
there is one worker by construction and not by hope. That alone would be an
argument rather than a mechanism, so the claim is also in the database: a worker
enters `EXCHANGING` only via

```sql
UPDATE link_flow SET state='EXCHANGING', exchange_claimed_at=?, exchange_claim_owner=?,
       exchange_attempts=exchange_attempts+1
 WHERE flow_id=? AND state='SUCCESS_PENDING_EXCHANGE';   -- 0 rows changed ⇒ you did not win
```

and a worker that changes 0 rows **does not call Plaid**. A claim older than the
call timeout is not silently re-claimable: it resolves to `EXCHANGE_UNCERTAIN`.

**And the crash window is admitted rather than argued away.** *(Rev 18, from
review.)* Rev 17's timeline said the token is `fsync`ed "before anything else",
as though ordering removed the interval between Plaid returning an
`access_token` and that token being durable here. **It cannot** — no local write
ordering makes a remote API response and a disk write atomic. Writing first
*minimises* the window; it does not close it. So: if a worker dies in that
interval, the flow lands in `EXCHANGE_UNCERTAIN`, and because Plaid documents
`public_token` as one-time use, **a retry may find the token already consumed
with the `access_token` lost — a permanently stranded slot**. `doctor` reports
that state distinctly from `TOKEN_EXPIRED` precisely because the owner's next
step differs: this is the one case worth taking to Plaid support with an
`item_id` (which `/link/token/get` still returns for the remaining retention
window — the diagnostic value of the long clock, §14a.1). Task `06a` must
measure the two behaviours this rests on: a **duplicate exchange** of the same
`public_token`, and an **injected failure after the response and before the
`fsync`**.

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

**That is a statement about the writer, and rev 17 read it as one about the
reader.** *(Rev 18, from review.)* Two different claims live near each other here
and must be kept apart, because conflating them made §9.3a assert its own
negation:

- **The daemon never resets its counter** (this paragraph). No rotation, no
  restart and no operator action sets it back — so within one database lineage
  `seq` only climbs.
- **The phone's `last_seq` baseline is scoped to `pairing_id`** (§9.3 point 3).
  It is not a claim that the daemon reset anything; it is a claim about which
  history the phone is entitled to compare against.

Both hold at once. The one thing that *does* legitimately move `seq` backwards is
a **restore from an older archive** — the daemon is not resetting a counter, it
is resuming a different, older copy of one — and that is precisely why the
reader-side scope exists (§9.3a). Rev 17 cited this paragraph as proof that
re-pairing *cannot* help a restore, which does not follow: the daemon's
discipline says nothing about the phone's baseline.

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

> #### ⚠️ Rev 16: the table above is true, and it is an argument about the wrong event
>
> *(From review. This is the reasoning that produced rev 15's deletion, so
> correcting it re-opens the decision rather than annotating it.)*
>
> That table describes an **`ERROR` webhook** — Plaid observes a state change and
> tells us faster than our poll would. For that event the argument holds
> completely: seconds versus an hour, swallowed downstream.
>
> **But the signal actually lost by deleting webhooks is `PENDING_DISCONNECT`,
> and it is not on that table at all.** By **F8** it is delivered *only* as a
> webhook — `/item/get` does not expose it — and it arrives **seven days before**
> the disconnection. Polling does not see it an hour late; polling **never sees
> it**. So the comparison is not "seconds versus ≤1h" on the first hop. It is:
>
> | | With the webhook | Without it (v0 as it stands) |
> |---|---|---|
> | What arrives | a **seven-day window** before the account breaks | nothing until it has already broken |
> | What the owner can do | open the app any time in those seven days, run `relink.sh` update mode (task 09), and **the outage never happens** | discover `ITEM_LOGIN_REQUIRED` after the fact, then run the same update mode to repair it |
> | Worst case | — | that account is visibly stale from the disconnect until he next opens the app and re-links |
> | Slot cost | none | **none** — **F8**: update mode restores the same Item |
>
> **The unbounded last hop does not erase the seven-day window; it decides
> whether the owner lands inside it.** A week is wide enough that an app opened
> at any ordinary cadence falls in it — which is exactly the property the
> latency argument was built to deny.
>
> **The true trade, stated so it can be decided on its merits:** a **rare,
> preventable, non-permanent outage** versus **a permanently internet-reachable
> write path on the host holding every credential in this project.**
>
> **What does *not* change:** by **F8** no lifetime slot is ever at risk, so the
> reversing argument rev 15 named is closed — and closed in the direction that
> keeps webhooks optional. No number is ever wrong either way (Axis B ages the
> account in plain sight). **My recommendation is unchanged — stay with polling**
> — but the recommendation now rests on "the loss is a recoverable outage" rather
> than on a latency comparison that did not apply. That is a different argument,
> the owner accepted the old one, so it goes back to him as **O9** (§18) rather
> than being quietly re-derived here.

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

#### The one argument that would have reversed this — now checked, and it does not

Rev 15 named this as the fact that could overturn the decision: if an
`INSTITUTION_MIGRATION` whose deadline passes unattended left an Item that
**update mode cannot recover**, advance warning would be protecting a **permanent
Item slot** (**F2**) rather than buying latency, and the public-route cost would
obviously be worth paying.

**It is now verified, and it goes the other way** (**F8**, rev 16). A missed
deadline puts the Item into `ITEM_LOGIN_REQUIRED`, and sending that same Item
through update mode moves it to the new API and restores it healthy — **the same
Item, no new slot.** So advance warning protects availability only, and the
scarcest resource in the project is not on the table.

**Rev 15 was right to name it, and wrong about how safe it was to leave open.**
It was recorded as "answerable from Plaid's documentation at zero cost", and then
the decision shipped without anyone spending that zero cost — which is the same
shape as the `public_token` error **F7** corrects. **A cheaply answerable
question that is load-bearing should be answered before the decision rests on it,
not filed beside it.** Both of this revision's factual corrections were sitting
in public documentation the whole time.

What *did* survive the check is the opposite error, and it is why **O9** exists:
`PENDING_DISCONNECT` is invisible to polling entirely (**F8**), so the capability
being given up is larger than the latency table admitted, even though the stakes
are smaller than this subsection feared.

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
currently holds **under the pairing it currently holds** (rev 17: `last_seq` is
stored per `pairing_id`, which is what makes a restore recoverable without
teaching the counter to read a clock — §9.3a):

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
3. **It is measured against what *this* phone has seen under *this* pairing**,
   which is the honest limit and is stated rather than hidden: a phone with no
   `last_seq` for its current `pairing_id` has nothing to compare against, so its
   *first* payload under that pairing is accepted on trust — as it must be, since
   pairing is the trust anchor. I6 defends a copy, not a first impression.

   *(Rev 17 makes the scoping explicit; rev 16 left it implicit and then leaned
   on it. The cost is one payload of trust per deliberate re-pair, and a re-pair
   already requires the owner to scan a QR from the daemon that minted it — an
   attacker who can get that far can serve anything, so nothing new is conceded.
   The gain is §9.3a: the one operation that legitimately needs a fresh
   comparison baseline is also the one that cannot happen without the owner.)*

**The realistic trigger is not an attacker.** On this architecture the ways to
serve the phone an old `seq` are: a database restored from backup (§14a) onto a
running daemon, a rollback of the daemon to an older build with an older
database, or two daemons on the tailnet answering to the same name. All three are
operator error, all three are *exactly* the case where a silently-accepted old
number would be most convincing — and all three are why this check stays in a
design that otherwise deleted its whole integrity apparatus.

#### 9.3a Surviving the restore this rule would otherwise brick

*(Rev 16, from review, and it is the sharpest finding of the round: §14a called
the archive "disaster recovery" while §9.3 made a real recovery unusable, and
both sections read fine on their own.)*

**The scenario.** The VPS is lost. A replacement is provisioned and restored from
the Mac's archive, which is *opportunistic* (§14a) and can legitimately be weeks
old. The restored daemon resumes publishing from the `seq` in that archive — a
value the phone passed weeks ago. Every payload it publishes is `seq < last_seq`,
so **I6 refuses all of them**, and the phone shows an unexplained-downgrade
warning until the counter climbs back — which takes as long as the gap did.

> **~~Re-pairing does not fix it~~**, ~~and that is the part that makes this
> structural rather than a missing step: `seq` belongs to the daemon and is
> deliberately never reset across pairings (§7, task 19), precisely so a rotation
> cannot be used to walk the counter backwards. The one lever an operator would
> reach for is the one the design nailed down.~~

**That premise is superseded, and it was false as soon as rev 17 wrote the rule
above it.** *(Rev 18, from review.)* §9.3 point 3 scopes `last_seq` to the
`pairing_id`; this paragraph says `seq` is never reset across pairings. Rev 17
asserted **both**, one screen apart, and then built a mechanism on the second
while relying on the first. Only one can be true, and the one that is true is the
scoping rule — so re-pairing **does** fix it, and the rest of this subsection is
the argument for why that is sufficient rather than a lucky accident.

**The remaining "fix" would be to teach the owner to clear the warning** — which
would train him to dismiss the exact signal I6 exists to raise, on the one
occasion it is firing for a real reason. A recovery procedure whose first step is
"ignore the integrity alarm" is not a recovery procedure.

**The fix is the scoping rule §9.3 already states, and rev 18 deletes the second
mechanism that was standing on top of it.** *(Rev 17 answered this with an epoch
packed into `seq`; that answer is below, with the contradiction that kills it,
because a fix that was wrong for a nameable reason is worth keeping visible.)*

> **`seq` is a plain counter. The daemon never resets it (§7); a restore may
> legitimately rewind it, and the phone's comparison is scoped so that this is
> harmless.** The restore case is closed by two facts the design already holds
> for independent
> reasons: **the phone's `last_seq` is scoped to `pairing_id`** (§9.3 point 3),
> and **the payload key is deliberately not in the archive** (detail 1 below), so
> a restored daemon *cannot serve the phone at all* until the owner re-pairs.
> Re-pair ⇒ fresh `pairing_id` ⇒ no `last_seq` to be below. The restored daemon
> resumes at whatever `seq` the archive held and the phone accepts it, because it
> is the first payload of a new pairing.

Why that is sufficient rather than lucky: the property I6 defends is **"nobody
serves this phone an older envelope than the one it holds"**, and the pairing is
what makes "this phone" and "its daemon" meaningful. Across a re-pair the payload
key changes, so an old envelope does not merely lose the `seq` comparison — **it
fails to decrypt** (§6.1). The counter never was the thing protecting against
cross-pairing replay; the key is. Scoping `last_seq` to the pairing therefore
concedes nothing, and the restore stops being a special case.

**And this closes the same-archive-twice edge by construction rather than by
argument.** Two independent restores from one archive read the same stored state,
so under rev 17's scheme they would bump to the same epoch and publish colliding
`seq` values — rev 17 noticed this and reached for the pairing scope to excuse
it. Once the pairing scope is doing the work directly, the collision is not a
problem to excuse: each restored daemon must be paired separately, and each
pairing carries its own baseline. Nothing compares them.

**What the epoch was actually earning, and what survives.** `publish_epoch` no
longer appears in `seq`. It stays in `daemon_state` (§7) as a **restore-lineage
diagnostic**: `doctor` shows how many times this daemon has been restored, when,
and why (`networth restore --new-epoch`, recorded in `epoch_bumped_at`/`_reason`).
That was always the honest half — "this database has been restored, here is when"
is a fact the owner should be able to see — and it does not require teaching a
counter to carry it.

*(Rev 18 is a **deletion**, and it is worth naming as one. Rev 17 added a table,
a bit-packing rule, an authorization step and a bound argument, all to reach a
property that a rule written one screen earlier already provided. The review that
caught it caught it as a **contradiction**, not as redundancy — the section stated
both "`seq` never resets across pairings" and "`last_seq` is scoped to the
pairing" — which is the usual way an unnecessary mechanism announces itself.)*

**What rev 16 proposed, and why it is deleted.** Rev 16 set
`seq = max(stored_last_seq + 1, unix_millis(now))`, arguing that every `seq` the
lost daemon published was ≤ the wall-clock millisecond at which it published.
**That argument assumes a monotone wall clock, which §9.1 rule 1 explicitly
refuses to assume** — this document has a whole state, `COPY_UNKNOWN`, for the
case where a clock cannot be trusted, and then quietly trusted one a few sections
later. The counterexample (from review): archive A is taken; the VPS clock jumps
far forward and the daemon publishes once; the phone **accepts** that payload —
I6 sees a greater `seq` and records it — while §9.1 separately labels the copy
`COPY_UNKNOWN`, because *acceptance and display are different axes*; the clock is
corrected; the VPS is lost; A is restored. `max(A.seq + 1, now)` is now far below
the phone's high-water mark, and **I6 rejects every payload again** — the precise
failure §9.3a was written to eliminate, in the precise scenario it was written
for. The epoch has no such assumption to violate.

**It also takes back what rev 16 gave away.** Rev 16 recorded, honestly, that its
fix made a **daemon rolled back to an older build with an older database** publish
an accepted payload rather than a refused one — because `now` outruns any stored
counter. Under the epoch that is no longer true: a rollback does not bump the
epoch and does not re-pair, so its publications carry a *lower* `seq` under the
*same* pairing and **I6 refuses them again.** The second bullet of the list above
is protected once more. That reversal is worth stating plainly: rev 16 paid for
its fix with a real reduction in coverage, and the reduction turns out to have
been unnecessary — it was the price of the clock, not the price of recoverability.

**Three details the restore procedure owns, since the counter is only half of
it:**

1. **The payload key is deliberately *not* in the archive**, so recovery includes
   a re-pair. Putting it in would mean the Mac holds both the token set and the
   ability to publish — §15 prices that blast radius explicitly and declines it.
   Re-pairing costs the owner one QR scan, and by definition he is present for a
   disaster recovery. *(Rev 17 makes this load-bearing twice over: it is now also
   what closes the repeated-restore edge above. A future revision that "simplifies
   recovery" by putting the payload key in the archive would silently remove a
   correctness property in §9.3a as well as widening §15's blast radius — so it is
   flagged here, at the place that depends on it, and not only where it is
   decided.)*
2. **The restored daemon drops the `published_envelope` it restored** before
   serving anything. It was encrypted under a payload key that no longer exists,
   so serving it produces an undecryptable response rather than an error the
   phone can explain — and `revoke`'s rule already says a served envelope dies
   with its pairing (§6.3.1).
3. **The drill covers this, or it is not covered** — and rev 18 rewrites the
   assertion, because rev 17's version could not fail. *(From review.)* Rev 17
   required: take archive A → jump the clock forward → publish → let a phone
   fixture record that `seq` → correct the clock → restore A → bump the epoch →
   **re-pair** → the fixture accepts. **The re-pair is the second-to-last step**,
   so the fixture's baseline is cleared immediately before the fetch and it would
   accept the payload *whatever* the epoch did. The test asserted the property of
   a mechanism while the step next to it made the property unconditional. Rev
   17's companion assertion — `publish_epoch == restored + 1` — checks
   bookkeeping, not acceptance.

   The drill therefore asserts the two things that can actually fail, and it
   asserts them **separately**:

   | Case | Setup | Required outcome |
   |---|---|---|
   | **Restore is accepted** | phone paired to daemon `P1`, holds `seq` 1000; restore archive A (`seq` 500); **re-pair to `P2`**; publish 501 | **accepted** — first payload of `P2`, and the honest reason is the pairing scope, not the number |
   | **Downgrade is still refused** | *within* `P2`: publish 502, then serve 501 again | **refused**, persistent warning |
   | **Replay across the restore** | serve a pre-restore envelope (carries `P1`) to the `P2` phone | **refused** — it does not decrypt; the `seq` never enters it |
   | **Rollback** | same pairing `P2`, daemon swapped to an older database, **no re-pair** | **refused** — this is the case the pairing scope must *not* rescue, and it is the one rev 16's clock rule traded away |
   | **Same archive restored twice** | restore A on two hosts; each is paired separately (`P3`, `P4`) | **both accepted, no interaction** — each pairing carries its own baseline, so the colliding `seq` values are never compared |

   The middle three are the ones with teeth: a change that made acceptance
   unconditional — including deleting the scope rule and simply always accepting
   — passes row 1 and fails rows 2 through 4. Task 19's existing "clock goes
   backwards" case does not cover any of this: it keeps its database, and these
   are about a database that predates the jump.

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
| `networth-sync.service` (+ `.timer`) | the periodic worker: poll, sync, snapshot, publish, build the backup archive | read-write — **the only long-running writer**; short-lived command writers exist and are listed below |
| `networth-serve.service` | `GET /snapshot`, bound to the **tailnet interface only**, `Restart=always` | **read-only** |

**One long-running writer — which is a narrower claim than "one writer", and rev
15 made the wider one.** *(Rev 16, from review. Rev 13 claimed "one writer" while
§8.4 had a webhook receiver writing inline in the read-only serving process; rev
14 resolved that with a third unit; rev 15 deleted the receiver and restored the
sentence — but deleting the receiver never made the claim true, because the
receiver was never the only other writer. The correction is not to the mechanism,
it is to a guarantee this design asserted twice without checking.)*

`networth-sync.service` is the only *unit* that writes. It is not the only
**process** that writes, and the others are ordinary, expected, and mostly run
while it is running:

| Writer | Writes | When |
|---|---|---|
| `networth-sync.service` | almost everything | every 5 min, unattended |
| `networth pair` / `networth revoke` | `pairing`, `published_envelope` (§6.3.1) | owner, interactive |
| `link.sh`'s remote half | `item`, `TokenStore` (§14a) | owner, interactive |
| `networth backup record-pull` | `backup_archive` | **unattended, from the Mac, on the Mac's schedule** |
| `networth backup record-drill` | `backup_state` | weekly, from the Mac |
| `networth backup attest-key` | `backup_state` | owner, rare |

**The collision is not hypothetical — one pair of these is two unattended
schedules with no relationship to each other.** The Mac pulls when the Mac
happens to be awake (§14a: opportunistic, never daily) and writes back
immediately; the worker wakes every five minutes. Nothing coordinates them, and
nothing should: coupling a laptop's sleep schedule to a server's timer is the
thing rev 14's inversion removed.

So the writer discipline is specified once here and every writer obeys it:

1. **`busy_timeout = 5000 ms` on every connection that may write**, set at open
   time, no exceptions. WAL means readers never block writers; it does **not**
   mean two writers cannot collide, and the default `busy_timeout` of 0 turns a
   200 ms overlap into an immediate `SQLITE_BUSY`.
2. **Every write transaction is `BEGIN IMMEDIATE`.** A deferred transaction that
   upgrades mid-way can fail with `SQLITE_BUSY_SNAPSHOT` after doing work, and
   that failure is not retryable by simply repeating the statement — the whole
   transaction has to be replayed. Taking the write lock up front makes the
   contention arrive where it can be waited on.
3. **Command writers hold the shortest transaction that is still correct**, and
   never one that spans network I/O or a Plaid call. `link.sh` is the one that
   tempts this: exchange first, *then* open the transaction that writes the token
   and the `item` row.
4. **On `SQLITE_BUSY` after the timeout: retry with jittered backoff, three
   attempts, then fail loudly.** For `record-pull` specifically, failing is safe
   by construction — `pulled_verified_at` stays `NULL`, which under-reports, and
   03a already requires the puller to re-record on a later run whenever it holds
   a verified archive whose row is still `NULL`. **That retry rule is what makes
   contention here a delay rather than a lost fact**, and it is the reason this
   is a five-line discipline instead of a queue.
5. **`networth-serve` remains read-only** and is unaffected: its handle is
   `file:…?mode=ro`, it takes no write lock, and that is what keeps it from ever
   observing a half-applied rotation (§6.3.1).

**Two acceptance tests, because a locking rule nobody collided on is an
assumption:** a `record-pull` issued *during* a running sync must succeed, and a
`pair`/`revoke` issued during a running sync must succeed — both without
`SQLITE_BUSY` reaching the caller, and with the sync's own transaction intact.

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

   **The test for this asserts an address, not the absence of one string.**
   *(Rev 16, from review. Through rev 15 task 20's criterion was "assert the
   listener is not on `0.0.0.0`", which three different wrong bindings pass:
   the VPS's **public IPv4**, the IPv6 wildcard **`[::]`** — which on Linux with
   `net.ipv6.bindv6only=0` also accepts IPv4 — and **loopback**, which is not
   exposed but leaves the phone unable to connect at all. A criterion that admits
   both failure directions tests nothing, and since rev 15 the "nothing is
   public" property is the main security claim this design makes, its test has to
   carry that weight.)*

   **Rev 16 then over-corrected into a criterion the owner's host fails on
   arrival** *(rev 17, from review, and measured read-only on the live VPS
   rather than argued)*. Rev 16 required that **no** non-loopback, non-tailnet
   listener appear anywhere in `ss -ltn` — a statement about *the host*, not
   about *this project* — while §15.1 deliberately keeps public SSH as the one
   firewall opening. Today, before any networth code exists there:

   ```
   LISTEN  0.0.0.0:22                      users:(("sshd",...))
   LISTEN  [::]:22                         users:(("sshd",...))
   LISTEN  100.102.245.37:44863            users:(("tailscaled",...))
   LISTEN  [fd7a:115c:a1e0::1d37:f526]:47618  users:(("tailscaled",...))
   LISTEN  127.0.0.54:53                   users:(("systemd-resolve",...))
   LISTEN  127.0.0.53%lo:53                users:(("systemd-resolve",...))
   ```

   So the acceptance test fails at deploy, on the *correct* configuration, which
   makes it a test that will be edited or skipped rather than fixed. **"networth
   exposes nothing publicly" and "the shared host has no public listener" are
   different claims, and only the first one is ours to make** (§15.1 — this is
   the owner's exit node and it was here first).

   That output corrects two more things rev 16 got wrong, both of which would
   have failed an implementation that followed it literally:

   - **"the node's Tailscale address" is not one address.** `TailscaleIPs` is an
     array and this node has both `100.102.245.37` and
     `fd7a:115c:a1e0::1d37:f526`. A test that reads element `[0]` and a daemon
     that binds the other are both defensible and disagree.
   - **loopback is not `127.0.0.1`.** `systemd-resolved` binds `127.0.0.54` and
     the interface-scoped `127.0.0.53%lo`; a "loopback" predicate written as a
     string match against `127.0.0.1`, or one that chokes on the `%lo` suffix,
     misclassifies both. Classify by prefix (`127.0.0.0/8`, `::1`) after
     stripping the zone, not by equality.

   The criterion therefore splits in two, and the split is the whole fix — a
   **positive** assertion about our socket, and a **change-detecting** assertion
   about everything else:

   1. **Our listener, asserted exactly.** Identify the socket by **process**, not
      by port alone (`ss -ltnp`, run with the privilege needed to read the
      process column — if it cannot be read, the test **fails** rather than
      falling back to matching a port number, since "some process holds 8443" is
      not the fact under test). Its local address must be **one of** the
      addresses in `TailscaleIPs`, read at test time from
      `tailscale status --json` and never hardcoded. `0.0.0.0`, the public IPv4,
      `[::]`, any public IPv6, **and loopback-only** all **fail** — loopback
      because it is the shape a "safe" fix takes when the tailnet address is not
      up yet, and it breaks the product silently while looking prudent.
   2. **Everything else on the host, compared against a declared baseline.** Task
      `28` captures the set of non-loopback, non-tailnet listeners at the end of
      provisioning — today exactly `sshd` on `0.0.0.0:22` and `[::]:22`, which is
      §15.1's one opening — and records it as the host's approved public surface.
      The test asserts the current set **equals** that baseline. A new public
      listener therefore fails the test whether networth opened it or not, and
      the failure names the process; a listener the owner adds deliberately is a
      one-line baseline update with a reason, which is a decision rather than a
      silent drift. This keeps rev 16's actual intent — *a publicly reachable
      thing appearing on this host is an event, not a background condition* —
      without asserting something false about a machine that is not ours.

   The check **repeats after a reboot**, because the bind-retry path in this very
   bullet only executes on a cold boot, and it is the code most likely to contain
   the fallback the rule forbids. A check that only ever runs on a warm host never
   observes the code it is there to police.
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
| **complete pending Link** | a `link_flow` row is in a **non-terminal** state (**rev 18**, §7, **F7**) — `URL_MINTED`, `SESSION_STARTED`, `SUCCESS_PENDING_EXCHANGE` or a stale `EXCHANGING` claim. Polls `/link/token/get` and advances the state from the **response**, never from elapsed time alone; on a `public_token` it takes the exchange claim, exchanges, and writes the `access_token` through `TokenStore` **before** the `item` row (§14a). Terminal transitions are separate and each is a *different* fact: `URL_EXPIRED` (no slot spent), `TOKEN_EXPIRED` (**slot stranded** — `token_exchange_expires_at`, 30 min after `finished_at`), `EXCHANGE_UNCERTAIN` (slot at risk, owner action) |

**Why that last row is on the always-on host and not in `link.sh`** *(rev 17,
from review)*. Retrieval is now the **only** path to a `public_token` — Hosted
Link has no frontend and v0 has no webhook (**F7**, §8.4) — so the process that
polls owns a deadline against a permanently scarce resource, and **rev 18 makes
that deadline 30 minutes rather than six hours** (§14a.1), which strengthens this
argument rather than weakening it: a half-hour window is *less* survivable on a
laptop, not more. Rev 16 put that process on `zelengs-macbook-air-2`, a laptop
whose lid closes; the window would have silently depended on the owner not
shutting it. Here it is an ordinary due-ness job with the same restart and
catch-up behaviour as every other. `link.sh` still triggers a poll immediately so
the owner is not waiting on the 5-minute tick, and then **watches**: it reports
the outcome, but it is not the mechanism, and closing it does not stop anything.

**"Triggers" is `systemctl start` on the same unit, and the wording matters
because the token is single-use** *(rev 18, from review)*. If the trigger spawned
its own process, `link.sh`'s poll and the timer's poll could both retrieve the
same `public_token` and race the exchange. It does not: the trigger starts **the
same systemd unit the timer starts**, and systemd coalesces a start against an
already-running unit into a no-op. That gives one worker by construction; the
`EXCHANGING` claim in §7 gives at-most-one *exchange* even if that construction
is ever wrong, which is the right way round for a resource that cannot be
re-obtained.

**And this job's deadline must survive the host being down, which is the one
thing a 5-minute timer does not give for free.** A VPS rebooting for 40 minutes
across a `SUCCESS_PENDING_EXCHANGE` row wakes up past `token_exchange_expires_at`
with nothing to do but record `TOKEN_EXPIRED`. That is honest but not helpful, so
`Persistent=true` and boot-time catch-up are **acceptance criteria on this job
specifically** (task `16`): the first tick after boot processes pending Link
flows before anything else, because every other job's cost of being late is a
stale number and this one's is a lifetime slot.

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
   a `public_token` exists at all the slot is already spent, and the confirmation
   prompt rev 13 put after that point was asking a question whose answer could no
   longer change anything.
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
   - **"Proves the path" means a canary, not a ping.** `link.sh` builds a probe
     archive on the VPS, pulls it, decrypts it, and deletes **its own local
     copy** — the entire mechanism, end to end, with the real key and the real
     transport. *(Rev 17 sharpens two words. It is not a "small" archive: the
     probe is a real `VACUUM INTO` of the real database, because a canary that
     copies less than a backup copies stops testing the backup — §15 bounds the
     **rate** instead. And what is deleted is the copy on the Mac; the probe on
     the VPS stays at its one fixed path and is overwritten, which is what lets
     the dispatcher's 60-second freshness window return it instead of
     rebuilding.)* Rev 13's
     check was reachability, which is exactly the substitution this project
     exists to refuse: *the host answering* is evidence about the network, and
     what needs proving is that **a backup would work**. A full disk, a wrong key
     mode, a `chown` that took away the read bit, a destination directory that
     does not exist — every one of those passes a ping and fails a backup.

   **And the whole timeline has to be stated, because the happy path is not where
   the slots go.** By **F2a** the Item exists the moment Link succeeds, before
   any token has been retrieved or exchanged. So:

   | Moment | What exists | If everything stops here |
   |---|---|---|
   | Before Link opens | nothing | Nothing lost. **This is the only point at which the design can still refuse**, which is why the canary runs here |
   | Link completed in the browser | the **Item** — the slot is spent | Recoverable **for 30 minutes**. The `public_token` is retrievable from the VPS with `/link/token/get`, and it is single-use with a 30-minute life (**F7**). Closing the tab costs nothing *because the always-on host is already polling* — not because the window is long |
   | **Link completed, then the VPS is lost before the exchange** | the Item, and a `link_token` on **two** machines | **Recoverable, and only because of the second copy** (rev 17). The Mac holds the `link_flow` recovery record; the owner re-reads `client_id`/`secret` from Plaid's dashboard and retrieves the `public_token` **within 30 minutes of finishing Link** (§19 step 2a). Without that copy this row is *immediately* unrecoverable — the archive predates the Link, so it holds neither token |
   | **30 minutes since the session finished, never exchanged** | the Item, with no reachable `access_token` | **Unrecoverable. A permanently stranded slot.** *(Rev 18: this row said six hours. It is the `public_token` lifetime that binds, not the session-data retention — §14a.1, F7.)* `link_flow` records `TOKEN_EXPIRED` |
   | Six hours since the session finished | the same stranded Item, now with no session record either | Nothing further is lost — but the *explanation* is: until this point `/link/token/get` can still say which session, which institution and which `item_id` stranded the slot. That is the only thing the long clock buys |
   | **Exchange sent, outcome unknown** (timeout, or the worker died after the request) | the Item; an `access_token` may or may not exist at Plaid | **`EXCHANGE_UNCERTAIN`.** Writing the token first *narrows* this window; it cannot remove it, because no local ordering makes a remote response and a disk write atomic. If Plaid consumed the token, the retry finds it spent and the slot is stranded. This is the one case that goes to Plaid support with the `item_id`, and it is reported as its own state so the owner can tell it from a plain expiry (§7) |
   | Exchange returned, token not yet durable | the Item and an `access_token` in memory | A crash here loses the token: same stranded slot. The token is `fsync`ed to `TokenStore` **before** anything else happens, and before the `item` row that references it — which shortens the exposure and does not close it |
   | Token durable, archive not yet pulled | one copy of the token, on the VPS | Survivable unless the VPS is lost in this window. The window is seconds, and the pull that closes it is the next step rather than tomorrow's job |
   | Verified archive on the Mac | two copies | The state this section exists to reach |

   **Rev 16 rewrote two rows of that table, and the correction is worth stating
   as a correction.** Through rev 15 this design asserted that a lost
   `public_token` was unrecoverable — "closing the tab is enough" — and then
   built the runbook around warning the owner about a paste. That was **wrong**,
   and it was wrong in the most expensive direction available: it accepted a
   permanent loss of the scarcest resource in the project on the strength of an
   unchecked assumption. **F7** documents the retrieval path, and the probes
   recorded there show Hosted Link minting on this very account. The lesson
   generalises past this row: *"no engineering answer" is a claim about the world
   and needs the same evidence as any other* — this one survived five revisions
   because it sounded like prudence.

   **The residual that is actually left**, now that the false one is gone:

   1. **The 30-minute `public_token` lifetime is a hard edge, and rev 17 had this
      residual at six hours.** *(Rev 18, from review.)* The VPS-side job polls
      immediately and repeatedly (§13) and does not depend on the laptop staying
      awake — but a flow that cannot complete for **half an hour** (Plaid
      unreachable, or the host down and unattended across that window) strands the
      slot. Two consequences the longer number was hiding: **a VPS reboot is now
      inside the risk window**, which is why boot-time catch-up on this job is an
      acceptance criterion rather than a nicety (§13); and the disaster procedure
      in §19 step 2a is a *minutes* procedure, priced as one. `doctor` reports
      **Items with no reachable token** as its own count so the state is visible
      inside the window rather than after — a bar that is materially harder to
      clear at 30 minutes and is stated at the number that is true.
   1a. **An exchange whose outcome is unknown is its own residual**, and no
      ordering removes it (the `EXCHANGE_UNCERTAIN` row above). It is smaller than
      the expiry residual — the exposure is one HTTP call, not half an hour — but
      it is the one case where the design cannot say whether the slot survived,
      and `doctor` says exactly that rather than guessing either way.
   2. **F7 is not yet proven end to end** (the completed-session field could only
      be read by spending a slot). Task `06a` proves it in Sandbox and is a hard
      gate on `08`. **Rev 16 softened that gate with a manual-paste fallback and
      rev 17 deletes it, because Hosted Link cannot produce one — there is nothing
      on the owner's screen to copy** (F7). So this residual is now carried where
      it belongs: `06a` must pass, and if it does not, the answer is that the
      first Production Link does not happen, not that a human improvises at the
      moment a slot is burning.
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

   - schema version matches, and row counts match what **the manifest sealed
     inside this archive** recorded at build time *(rev 16: this used to compare
     against `backup_archive.db_row_counts_json` — a row on the VPS, i.e. the
     machine whose loss is the entire scenario)*;
   - **every `item` row resolves to *its own* token in the same archive** — the
     drill recomputes `item_token_binding_sha256` from the restored database and
     `TokenStore` and requires it to equal the manifest's, which is a statement
     about the **mapping** and not merely about the token set (never the tokens,
     never in a log). This is the check rev 13 did not have and rev 16 stated but
     did not implement: matching row counts prove the database arrived, and say
     nothing about whether it arrived paired with the right token generation —
     which is the failure mode §14a's lock and ordering exist to prevent, so it
     is the one the drill has to be able to catch;
   - the restored `TokenStore` has no `item` row missing a token. Orphan tokens
     are reported and are **not** a failure (§14a).

   It records `last_verified_restore_at` (into `backup_state`, over the same
   one-way SSH path as `record-pull` — §15's dispatcher allows a third verb,
   `record-drill`, for exactly this), runs weekly, and `doctor` reports its age.

#### The manifest — because a backup validated against the VPS is not a backup

*(Rev 16, from review, and it is the same class of error as **F7**: a mechanism
that reads correct until you ask what is still standing when it runs.)*

Rev 15 had the drill compare the restored database against
`backup_archive.db_row_counts_json` — **a row in the live database on the VPS.**
In the scenario this whole section exists for, the VPS is gone. What was left was
a drill that either needs the machine the backup must survive, or silently falls
back to comparing the archive against values derived from the same unchecked copy
it is trying to validate. Neither is a check.

**So the evidence moves inside, and the split is by what each fact can possibly
be.** A bundle cannot contain its own hash; everything else can:

| Fact | Lives | Why it cannot live in the other place |
|---|---|---|
| `archive_id`, `schema_version`, `db_row_counts`, `item_count`, `item_token_binding_sha256`, `built_at` | **inside**, in the manifest | The drill must validate with nothing but the archive and the key |
| `archive_sha256`, `byte_size` | **outside**, `backup_archive` + the pull's own record | Computed over the sealed bundle; a hash cannot contain itself |
| `pulled_verified_at`, `pulled_by`, `verify_error` | **outside** only | They are facts about a transfer that happens after the archive is frozen |

**The manifest is authenticated, not merely present.** It is a JSON object sealed
**inside** the encrypted bundle, so the AEAD tag over the bundle authenticates it
with the same key and the same operation that protects the tokens. There is no
separate signature and no plaintext sidecar: a sidecar next to the file would be
an unauthenticated claim about the file, which is worse than no claim because it
looks like evidence.

**Fingerprints are derived, not stored with a secret.** The per-archive key is
`K_fp = HKDF-SHA256(ikm = backup_key, salt = archive_id, info =
"networth/token-fingerprint/v1")`. Three consequences, all load-bearing:

- the salt is `archive_id`, which is already in the manifest, so **nothing extra
  has to be kept anywhere** and two archives never share a fingerprint space;
- an attacker holding the archive but not the key cannot compute fingerprints, so
  the manifest leaks nothing about the tokens even if the AEAD is stripped;
- **the fingerprint is not a token and must never be reversible into one** — it
  is truncated, keyed, and never logged (§15).

**What the fingerprint covers is the part rev 16 got wrong, and the bug was that
the drill's stated check could not fail.** *(Rev 17, from review.)* Rev 16 sealed
`token_fingerprint_set_sha256` — a hash over the **set** of
`HMAC-SHA256(K_fp, access_token)` values — while criterion 3 below promised that
"every `item` row resolves to **its** token". Those are different claims, and the
gap is exactly the corruption the `flock` and the write ordering exist to prevent:
**swap two `access_token`s between two Item keys and the set is unchanged, its
hash is unchanged, and the drill passes** on an archive that would restore both
Items pointing at each other's institution. A restore from it fails later,
confusingly, at the first Plaid call — or worse, succeeds against the wrong
institution and contributes a wrong number to a total.

So the fingerprint **binds the Item identity**, and the manifest seals a
**mapping** rather than a set:

```
fp(item)   = HMAC-SHA256(K_fp, LP(item.item_id) ‖ LP(item.secret_ref)
                              ‖ LP(access_token))            truncated to 128 bits
manifest.item_token_binding_sha256
           = SHA256( canonical_json( [ [item_id, secret_ref, hex(fp(item))]
                                       for item in items sorted by item_id ] ) )
```

`LP(x)` is a length-prefixed encoding (a `uint32` big-endian length, then the
UTF-8 bytes), **not** a delimiter. That detail is not decoration: with a
separator byte, `item_id = "ab", secret_ref = "c"` and `item_id = "a",
secret_ref = "bc"` hash identically, and re-introducing an ambiguity into the
function whose whole job is to make two arrangements distinguishable would repeat
this finding one layer down. Sorting by `item_id` makes the digest canonical, so
row order in the restored database cannot change the answer.

**The negative test is required, not optional**, and it is the test rev 16 did not
have: build an archive, **swap two `access_token`s between two Item keys**, and
the drill must **FAIL**. A coherence check nobody tried to break is an assertion,
the same way an allow-list nobody tried to escape is (§15).

**The general lesson, since this is the second time it has bitten:** AEAD
authenticates *the bundle that was built*. It proves nobody edited the archive; it
cannot make an under-specified check prove something the check never covered.
"Authenticated" and "sufficient" are different properties, and rev 16 leaned on
the first while claiming the second.

**Build and publish ordering, because the obvious sequence produces a row that
describes a file that does not exist — or a file no row describes:**

1. mint `archive_id` (random, 128-bit);
2. take the `flock` shared with token writes; `VACUUM INTO` a temp DB; copy the
   `TokenStore`; release;
3. compute row counts and fingerprints **from the copies**, never from the live
   database — otherwise the manifest describes a moment the archive does not;
4. write the manifest; seal the bundle to `<dir>/.tmp-<archive_id>`; `fsync`;
5. compute `archive_sha256` over the sealed temp file;
6. **`INSERT` the `backup_archive` row** with the final hash;
7. `rename()` into place — the atomic publish.

**The archive therefore never contains its own `backup_archive` row**, and that
is correct rather than a gap: step 6 happens after step 2's snapshot, and the
drill reads the manifest, not that table. A crash between 6 and 7 leaves a row
with no published file — self-correcting on the next build, and visible, which is
the right direction for this pair to fail. A crash before 6 leaves a temp file no
row references; the builder deletes stale `.tmp-*` on start.

**The acceptance test is the one that matters and the one that is easy to skip:
run the drill on a machine with no network path to the VPS at all.** If it
passes, the evidence is genuinely self-contained. If it needs the VPS, this
section has not been implemented, whatever the code says.

**Which forces a split inside the drill, because criterion 3 also has it report
`last_verified_restore_at` back to the VPS** — and a drill that must reach the
VPS to finish is precisely what this subsection forbids:

| Phase | Needs the VPS? | If the VPS is unreachable |
|---|---|---|
| **Verify** — decrypt, restore, check the manifest, reconcile tokens, replay the `seq` check (§9.3a) | **no, and this is the requirement** | proceeds and produces a verdict |
| **Report** — `record-drill` over SSH into `backup_state` | yes | **the verdict is kept locally and re-sent on the next run**, exactly like `record-pull`'s retry-when-`NULL` rule |

**The verdict is the drill's output; the report is bookkeeping.** A failure to
report leaves `last_verified_restore_at` older than reality, which under-reports
— the correct direction, the same one `pulled_verified_at` already fails in. What
must never happen is the inverse: a drill that treats "could not reach the VPS"
as a *drill* failure would go red every time the laptop is offline, and an alarm
that cries wolf on a sleeping laptop is an alarm the owner learns to ignore.

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
| the VPS | **`/etc/networth/`** (mode `700`/`600`) | the **two** daemon units (§13), the `networth backup`/`pair`/`revoke` commands, `link.sh`'s remote half |
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
- **`/etc/networth/plaid-sandbox.env`** — the **Sandbox secret**, the same
  `client_id`, and `PLAID_ENV=sandbox`. *(Rev 14, from review, established that
  there must be a second file; **rev 16 narrows the wording**, also from review:
  Plaid issues **one `client_id` per team and a separate secret per
  environment** — not two `client_id`/`secret` pairs. Rev 13 had named only the
  Production file while forbidding a second location, leaving task 06's rehearsal
  with no legitimate way to authenticate; the rule was always "no **invented**
  second path", not "one file". The file split and the fail-closed environment
  selection below are unchanged and still right — only the description of what
  differs between the two files was wrong.)* Same directory,
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
- **`networth-link-recovery/<flow_id>.json`, mode `0600` in a `0700` directory —
  the second copy of a pending Link's `link_token`** (§14a.1). *(Rev 18 names it;
  rev 17 introduced the record, required it to be reaped, and never said where it
  lived or with what permissions — a credential this section could not inventory
  is one it cannot claim to bound.)* It holds `flow_id`, the `link_token`,
  `minted_at`, `hosted_url_expires_at` and `reap_after`; it holds **no deadline
  derived from a session that has not happened**, and **no Plaid client
  credential** — which is what keeps it inert on this machine. Written and
  `fsync`ed before any URL is printed, deleted by `link.sh` on `EXCHANGED`, and
  deleted by the puller once `reap_after` has passed. Its normal lifetime is the
  length of one Link; its bounded worst case is `reap_after`.
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
Constraining the backup key in `authorized_keys` means a compromise of
`zelengs-macbook-air-2` yields the archives — which that machine already has on
disk, so nothing new — but **not a shell on the VPS**, which would additionally
yield live Plaid API access, the payload key, and the ability to rewrite history.
The gain is real and the cost is one extra line in `authorized_keys`. The
interactive key stays for `link.sh` and ordinary administration, where a human is
present.

**The forced command is a dispatcher, not a single command, and rev 15's version
could not have worked.** *(Rev 16, from review.)* Through rev 15 the line read
`restrict,command="networth backup serve-archive"`, while §14a required the same
unattended key to also run `networth backup record-pull` to write the
verification back. **OpenSSH's `command=` runs *that* command and discards
whatever the client asked for** (`sshd(8)`), so the write-back could never have
executed — and the failure would have been silent in the *worst* direction:
`pulled_verified_at` stays `NULL`, `doctor` reports "no verified backup" forever,
and the retry rule 03a already specifies would have retried a call that is
structurally incapable of succeeding. So:

```
restrict,command="/usr/local/lib/networth/backup-ssh-dispatch" ssh-ed25519 AAAA…
```

The dispatcher reads **`SSH_ORIGINAL_COMMAND`** and allow-lists exactly four
verbs, with no arguments passed through as a shell string:

| `SSH_ORIGINAL_COMMAND` | Dispatches to | Writes? |
|---|---|---|
| `build-probe` (no arguments) | builds `link.sh`'s canary archive, to **one fixed path**, overwritten in place, and per §14a **never writing a `backup_archive` row**. Single-flight; a no-op returning the existing probe if one was built in the last 60 s. **Returns `(probe_generation, outcome: built\|reused)`** — a VPS-local counter and an explicit outcome, never a timestamp for the Mac to compare against its own clock (rev 18) | one file, one path |
| `serve-archive <current\|probe>` | streams that archive to stdout | no |
| `record-pull <archive_id> <verdict> <full-tailnet-name>` | `networth backup record-pull`, arguments **parsed and validated**, `archive_id` matched against an existing row | one row, `backup_archive` only |
| `record-drill <archive_id> <verdict>` | `networth backup record-drill` — the weekly restore drill runs on the Mac (§14a.1 criterion 3) and its result has to reach the VPS, where `doctor` reads it | `backup_state` only, singleton |
| anything else, or unset | **exit non-zero, log, do nothing** | no |

**Why a build verb is on this key at all**, since it is the one verb that makes
the VPS do work on request: `link.sh`'s canary must prove **the path the
unattended puller actually uses** — same key, same dispatcher, same transport —
or it proves something else and the pre-Link gate is theatre (§14a.1). Running
the canary over the interactive key would test a route that no backup ever takes.

**Rev 16 wrote that paragraph and then priced the residual wrong** *(rev 17, from
review)*. It said a compromised laptop making the VPS build archives was "a
nuisance, bounded by the work of one `VACUUM INTO`" — but **nothing bounded the
number of invocations.** The key can open sessions in a loop, or concurrently;
`build-archive current` also **inserted a `backup_archive` row per call**, so the
capability was sustained disk and CPU load plus unbounded bookkeeping growth, on a
machine that is also the owner's VPN exit node. "The cost of one X" is only a
bound when something limits you to one X, and that sentence was doing the work of
a rate limit while being a description.

The verb is therefore narrowed to the minimum the canary actually needs, and then
bounded:

- **`build-archive current` leaves this key entirely.** Nothing unattended needs
  it. The daily timer builds the real archive (§13), and `link.sh`'s post-exchange
  build runs over the **interactive** key, where a human is present. The
  restricted key can no longer cause a full-history archive or a row to exist.
  *The cost is named rather than glossed: if the timer stops, the unattended
  puller can no longer force a fresh build and keeps pulling the newest archive
  that exists. That is the right behaviour here — `last_successful_backup` ages
  in plain sight (§14a.1), which is this project's whole answer to a stalled
  pipeline, and it beats giving an unattended key the power to make the host
  work.*
- **`build-probe` takes no arguments**, writes to one fixed path and overwrites
  it, so retention is one file by construction and there is nothing to grow.
- **Single-flight**: it takes a non-blocking `flock`; a concurrent request exits
  non-zero immediately rather than queueing. Concurrency was the part that turned
  a cost into a multiplier.
- **A 60-second freshness window**: a request arriving within 60 s of the last
  probe returns the existing one instead of rebuilding. The response carries
  **`probe_generation`** — a VPS-local counter incremented on every *actual*
  build — and **`outcome: built | reused`**, so the caller can tell which it got
  **without reading a timestamp**.

  **That pair is not bookkeeping — without it this bound would have quietly
  broken the canary**, and it was found by walking the ordinary Link flow through
  this rule rather than by reading it. Two institutions linked back-to-back put
  two canaries inside one 60-second window; the second would receive a probe
  built *before* it ran and accept it as proof. But the build is the step that
  catches a full disk, a bad key mode or a missing directory — serving and
  decrypting an old file proves the transport and nothing else, which is
  precisely the substitution §14a.1 rejects when it refuses a reachability ping.

  **Rev 17 fixed that with a rule this document is not allowed to write, and rev
  18 replaces the rule rather than the intent.** *(From review.)* Rev 17 required
  "a probe whose `built_at` is later than the moment the canary started" — but
  `link.sh` starts on `zelengs-macbook-air-2` and `built_at` is stamped on the
  VPS, so that is **a comparison between two machines' wall clocks**, in a design
  whose §9.1 rule 1 explicitly refuses to infer ordering from untrusted clocks
  (and which has a whole `COPY_UNKNOWN` state for the case). Ordinary NTP skew in
  one direction rejects a genuinely fresh probe and stalls the gate; skew in the
  other **accepts a stale one**, which is the failure the clause existed to
  prevent. The canary would have been the second place this document trusted a
  cross-machine clock after promising not to.

  So the protocol uses **one machine's monotonic counter and an explicit
  outcome**, never two machines' time:

  1. The canary calls `build-probe` and reads `(probe_generation, outcome)`.
  2. **`outcome == built`** ⇒ this canary caused a build; proceed to pull,
     decrypt and verify **that** generation.
  3. **`outcome == reused`** ⇒ the gate is *not* satisfied. Wait out the
     remaining cooldown and call again, until a response says `built` (or until
     `link.sh`'s overall timeout, which refuses the Link — refusing is free,
     §14a.1).
  4. The pulled archive carries its `probe_generation` in the manifest, and the
     canary asserts it equals the generation it was promised — so "the file I
     verified is the file my build produced" is checked rather than assumed.

  No Mac timestamp is compared to a VPS timestamp anywhere in that sequence. The
  rate bound is untouched — still at most one build per minute — and the gate
  stops being satisfiable by a stale artefact. **Required tests set the Mac's
  clock an hour fast and an hour slow and assert the canary's verdict is
  unchanged in both directions**, because a clock dependency that no test skews
  is exactly how the previous rule survived review. *("Two Links two minutes
  apart" is the realistic case, and "realistic" is not the standard this document
  holds a pre-Link gate to.)*
- **Rejections are counted, not just refused.** Rate-limited and single-flight
  refusals are logged with the verb and surfaced by `doctor` alongside the
  disallowed-command count (property 4 below). A throttle that silently absorbs an
  attack converts a visible signal into an invisible one, which is the wrong
  trade even when the throttle works.

The probe stays a **real** `VACUUM INTO` of the real database rather than a
reduced-content stand-in: the bound belongs on the *rate*, not on the *fidelity*,
because a canary that copies less than a backup copies is no longer testing the
backup. The residual, priced honestly this time: a compromised laptop can cause at
most one probe build per minute, to a fixed path, writing no rows — and the
acceptance test is a **burst**, not a single call.

Four properties that make this a boundary rather than a formality, and the last
two are where an allow-list of this shape usually goes wrong:

1. **`restrict` stays.** No pty, no port/agent/X11 forwarding, no `~/.ssh/rc`.
   The dispatcher narrows what the key may *do*; `restrict` is what stops the
   connection being useful for anything else.
2. **The dispatcher never passes `SSH_ORIGINAL_COMMAND` to a shell**, in whole or
   in part. It splits, matches the verb against a fixed table, and validates each
   argument by pattern — because the one input a remote party controls here is
   that string.
3. **`record-pull` can only move one fact in one direction.** It may stamp
   `pulled_verified_at`/`pulled_by`/`verify_error` on a row that already exists;
   it may not insert rows, touch any other table, or clear a stamp. A compromised
   laptop can therefore claim a backup happened — which is a real and accepted
   residual, since only that laptop can know — but cannot erase history or
   fabricate an archive record.
4. **Every rejected command is logged with its verb**, and `doctor` surfaces the
   count. A key that starts being used for something it is not allowed to do is a
   signal, and one that is dropped on the floor is not.

**The acceptance test is that the negative case is tested**: `ssh -i
networth-backup-ssh.key … 'bash'`, `… 'networth show'`, **`… 'build-archive
current'`** (rev 17: the narrowing is only real if the removed verb is *tested*
absent), and a `record-pull` with a mangled `archive_id` must each fail, and a
plain `ssh` with no command must fail. **And one positive-shaped test that is
really a negative**: a **burst** of `build-probe` — say fifty sequential and ten
concurrent — must produce at most one build per minute, exactly one file, zero
`backup_archive` rows, and a non-zero refusal count in `doctor`. A forced-command
allow-list nobody tried to escape is an assertion, not a control; a rate limit
nobody burst is the same thing.

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

  **That one opening is why the test is a baseline comparison and not an
  emptiness assertion** *(rev 17, from review)*. `sshd` on this host listens on
  `0.0.0.0:22` **and** `[::]:22` — verified read-only on the live VPS — so a
  criterion reading "no public listener anywhere" contradicts this very bullet
  and fails on the correct configuration. §13 states the two-part form: our
  process asserted positively against the node's Tailscale addresses, everything
  else compared against the surface `28` recorded at provisioning.
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
   signing). **No code running on the VPS ever reaches for `~/agents/secrets/`,
   and no code running on the Mac ever reaches for `/etc/networth/`; neither is a
   fallback path or a search location for the other.** *(Rev 16 corrects the
   wording, not the rule: through rev 15 this read "no committed code reaches for
   `~/agents/secrets/` at runtime", which the committed backup puller, the
   restore drill and `link.sh`'s local half all violate by design — they are
   Mac-side programs and that directory is where their keys are. The rule was
   always **no cross-host lookup and no fallback**; stated as an absolute it
   forbade the very code §15's own table authorises.)* Never in git, a PR body, a
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

**Host side: Python 3.12 *or newer* + SQLite + the official `plaid-python`
SDK**, on Ubuntu 26.04. **Phone side: Flutter** (decided by the owner), Android
only (O6).

*(Rev 19 turns "Python 3.12" into a floor, from the host itself rather than from
this document: `tokyo-exit` runs Ubuntu 26.04.1, whose `python3` is **3.14.4**,
and 3.12 is not in that archive at all. `pyproject.toml` has said `>=3.12`
throughout, so the code is in range — but CI resolves 3.12.3 while the
provisioned host will run 3.14, a version this project has never run a test on.
Task `28` installs the **distribution** interpreter deliberately: one that sits
outside `unattended-upgrades`, on the host holding the Plaid master credential,
is the worse trade. Closing the testing gap is **issue #33**.)*

| Option for the host side | For | Against | Verdict |
|---|---|---|---|
| **Python + SQLite** | Official Plaid SDK; SQLite in stdlib; trivial systemd integration; no build step; ideal for a daemon | Not the UI language | **Chosen** |
| TypeScript / Node | Official SDK too | Adds a toolchain for no daemon-side gain; shares nothing with a Flutter UI | Second |
| Dart end-to-end | One language across both halves | **No server-side Plaid SDK** — would mean hand-rolling a financial API client and its error taxonomy, on the side of the system that holds the credentials | Rejected |

**There is no transport component any more** *(rev 10)*. Rev 9 specified a
Cloudflare Worker here — six routes, two storage bindings, and a rule about which
route bound which store because atomicity depended on it. It is deleted. What
takes its place is **one route on the host that already holds everything**
*(rev 15; rev 14 had briefly split two routes across two processes, and rev 16
fixes this sentence, which still introduced them)*:

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

### The Link flow needs no hosted page at all

*(Rev 16. Through rev 15 this heading read "The Link flow still needs one hosted
page".)* Ordinary Link at OAuth institutions requires an **HTTPS** redirect URI
registered in the Plaid dashboard (`http://localhost` is Sandbox-only), and every
revision through 15 budgeted a free static page for it. **Adopting Hosted Link
(F7) deletes that requirement**: Plaid hosts the Link experience and handles the
OAuth redirect itself, and a `redirect_uri` need not be registered at all. The
only `redirect_uri` Hosted Link documents is for returning to a *native mobile
app* after app-to-app OAuth — and Link never runs in this project's app (below),
so it does not apply.

**Task 07 is therefore deleted, and with it the last publicly hosted thing this
project owned.** That is worth noticing next to rev 15's result: rev 15 removed
the last public *inbound service* on the VPS, and rev 16 removes the last public
*static asset* anywhere. The project now hosts nothing on the public internet —
not a route, not a page — and that is a property, so §19 step 3.4 checks it and
`28` keeps it as an acceptance criterion.

**The host move splits this step across two machines, and the split is a
simplification rather than a complication.** Link runs in a **browser on
`zelengs-macbook-air-2`** — that is where the owner is sitting and where his
password manager is — while the `public_token` must be exchanged where the client
secret lives, which is the **VPS**. Since rev 14 the *driver* is on the Mac too,
because the backup is a pull and only the Mac can perform one:

1. `scripts/link.sh` runs **on `zelengs-macbook-air-2`**. It first runs the
   backup canary (§14a.1) — build a probe archive on the VPS, pull it, decrypt
   it, delete it — and **refuses to go further if that fails.**
2. It then SSHes to the VPS to mint a **Hosted Link** `link_token` (**F7**),
   which the VPS stores through `TokenStore` under an `OPEN` `link_flow` row
   (§7). The `client_id`/`secret` never leave the VPS; the Mac holds an SSH key,
   not a Plaid credential.
3. **Before printing anything**, `link.sh` pulls the `link_flow` recovery record
   to this Mac, `fsync`s it and reads it back (§14a.1). If that fails it prints
   no URL and stops — nothing has been spent yet, so stopping is free.
4. It prints the `hosted_link_url`. The owner opens it in this Mac's browser and
   completes Link there. **Credentials and MFA go into Plaid's page**; neither
   machine sees them. **The lifetime slot is spent at the end of this step**
   (**F2a**).
5. **The VPS** polls `/link/token/get` as a due-ness job (§13) until the session
   reports completed, then exchanges the `public_token` there — the
   `access_token` is written durably through `TokenStore` before the `item` row
   is committed (§14a), and the `link_flow` row goes `EXCHANGED` with its
   `secret_ref` cleared. `link.sh` triggers the first poll immediately so the
   owner is not waiting on a tick, then **watches**. Nothing is copied by hand,
   and nothing is copied by hand on any other path either — see below.
6. `link.sh` triggers an archive build on the VPS and **pulls it**, verifies it
   decrypts and reconciles, and only then reports the Link complete.

**Rev 16 reverses rev 10's decision to drop task 07a, and the reversal is a
correction rather than a change of taste.** Rev 9 kept 07a as a spike for handing
the token back automatically; rev 10 dropped it because the browser was on the
Mac and the exchanging process on the VPS, so an automatic handoff "would mean
opening a route on the VPS" — new public surface for a step that happens ten
times ever. **That argument had a false premise.** `/link/token/get` is an
**outbound** call from the VPS, authenticated by the `link_token` the VPS itself
minted; retrieval needs no inbound route, no Funnel, and no public surface at
all. The topology objection that killed 07a was never about this mechanism, and
by rev 14 — which moved the driver to the Mac — it did not describe the flow
either. **07a is therefore un-dropped, and it is not a spike:** polling is the
primary path.

**Copy-paste does not survive as a fallback, because Hosted Link cannot produce
one.** *(Rev 17, from review.)* Rev 16 kept a paste for the case where the poll
yields nothing, and Plaid's documentation rules it out in as many words: **"In
Hosted Link, there is no frontend integration required (or possible)"**, and
`completion_redirect_uri` — the only thing the browser returns to — carries no
token. With webhooks deleted (§8.4), the browser never holds a `public_token` at
any point, so there is nothing on the owner's screen to copy. The fallback would
have been an instruction that cannot be followed, printed at the moment a slot is
already spent.

**So there is one path, and its failure behaviour is specified instead of
escaped** (F7): poll immediately, retry on a bounded schedule until either the
token arrives or **`token_exchange_expires_at` passes — 30 minutes after the
session finished, not six hours** (rev 18); a missing `link_sessions` key is
the normal pre-completion response and not an error; a rejected `link_token` is
terminal and reported. The retry schedule is sized to that window: **poll
aggressively for the first minutes and give up at the token deadline**, since
backing off into an hour-long schedule would spend most of its patience after the
token is already dead. **`06a` is what carries the risk the fallback pretended
to** — it proves completed-session retrieval in Sandbox, where proof is free, and
it gates `08`.

**And the one-path rule is only safe because the branch moved earlier.** A
fallback selected *after* Link completes is not a fallback at all — by **F2a** the
slot is gone by then. The place this flow can still branch is steps 1 and 3, the
canary and the `link_token` second copy, both of which run **before** the URL is
printed and both of which stop the flow rather than degrade it.

**Nothing may be *silently* degraded.** A run that cannot complete says why, the
`link_flow` row keeps the reason, and `doctor` reports open and expired flows —
because "the automation quietly stopped working" is precisely the shape of failure
this project exists to make visible.

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

**One is open: O9** *(rev 16)* — and it is open because a decision the owner
already made turned out to rest on a wrong premise, not because anything new
arrived. *(Answered questions are kept, struck through, with the answer in place,
so a reader of an old review comment can still find what O5 was and why each
branch disappeared.)*

**No external unknown is open.** Nothing waits on Plaid or on a vendor's terms;
rev 16 closed the last two by reading the documentation (**F7**, **F8**). O9 is a
**re-decision**: the owner said drop webhooks, and he said it on the strength of
a latency argument this revision found to be about the wrong event. Re-asking is
the honest response to that; it is not a new question and it may well get the
same answer.

**Nothing is blocked on O9.** It gates no task — v0 has no webhook receiver and
the plan to add one is fully costed in §8.4a. And it does **not** mean linking may
begin: task 03a's backup gate stands (§14a), and that is a dependency rather than
an open question.

| # | Question | Owner of the answer | Blocks |
|---|---|---|---|
| ~~O2~~ | ~~Does the Trial plan actually reach the in-scope brokerages via OAuth?~~ **ANSWERED 2026-08-30: GO — on the strength of Plaid's plan-level statement, not an institution-level test.** Trial active at `0/10`, Production credentials issued, and Plaid states bank access is **automatic on the trial** with no per-institution request. The live `/institutions/get` call proves the credential, Trial Production access and VPS egress; it is a directory listing and proves nothing about any specific bank (**F4**, narrowed in rev 14). Per-institution evidence costs a lifetime slot to obtain (**F2a**), which is why it is not a check anyone runs early. Tasks 07, 08 and the downstream Production-Link work are ungated — but see the runbook correction in §19 step 1, because the obvious path to "production access" is a **paid** funnel that this project must not enter | — | — |
| ~~O3~~ | ~~How many distinct card-issuer logins?~~ **VOID** — it existed only to size the card share of the Item budget, and cards are deferred (§1, rev 9). Nothing waits on it | — | — |
| ~~O4~~ | ~~Real property: purchase price only, or a revision log?~~ **ANSWERED: a revision log**, defaulting to purchase price, every revision kept with its date — **and a revision applies from its own date forward, so the curve behind it never deforms** (§12) | — | — |
| ~~O5~~ | ~~Transport: a third-party relay, or Tailscale?~~ **ANSWERED: Tailscale — and the host moved with it.** The owner has an always-on Vultr VPS (already paid for, already his tailnet exit node), so the daemon runs there instead of on the Mac. Both drawbacks the Tailscale branch carried were *Mac* drawbacks and both are void: the VPS never sleeps, and it has a public IPv4 so the webhook accelerator survives (§8.4). The entire third-party branch is **deleted** (§6.2), not parked | — | — |
| ~~O6~~ | ~~iOS as well as Android?~~ **ANSWERED: Android only.** *Decided* rather than postponed — the iOS branch and its sideloading problem are gone from this design rather than parked. Tasks 21 and 24 are Android-only | — | — |
| ~~O7~~ | ~~Create a free third-party account for the transport?~~ **VOID** — it existed only on the branch O5 deleted. No new account is created by this design | — | — |
| **O9** | **Webhooks were dropped on a wrong premise — does the decision stand?** *(Rev 16, from review.)* You asked whether webhooks were worth having; I answered that they only shave the first hop from ≤1h to seconds, and you dropped them on that. **That argument was about the wrong event.** The signal actually lost is `PENDING_DISCONNECT`, which polling can **never** see (**F8**) and which arrives **seven days before** an account breaks — a window in which opening the app once means the outage never happens. The real trade is **a rare, preventable, recoverable outage** versus **a permanently internet-reachable write path on the host holding every credential**. Two things that make this smaller than it sounds: no lifetime slot is ever at risk (**F8** — update mode restores the same Item), and no number is ever wrong either way. **My recommendation is unchanged: stay with polling.** But you decided on my bad argument, so the decision is yours to re-make on the good one | **owner** | nothing — v0 has no receiver either way; §8.4a is the costed plan if it reverses |
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
5. **Nothing to register in the dashboard. Skip this step entirely.** *(Rev 16.)*
   This step asked you to register a redirect URI under *Allowed redirect URIs*.
   Adopting **Hosted Link** (**F7**) removes the requirement: Plaid hosts the Link
   page and handles the OAuth redirect itself, and no `redirect_uri` needs
   registering. *(Rev 13 also asked for a webhook URL here; rev 14 found that
   setting does not apply to Item-based products, and rev 15 dropped webhooks
   entirely — §8.4.)* **This project now needs no dashboard configuration of any
   kind** beyond the account and plan that step 1 already established.
6. **Do not request special access for the equity-comp brokerage.** Rev 9 listed
   this as an optional step; the owner decided against it (§18). The manual path
   (§12) is the plan, it needs no request and no Item, and the request would cost
   up to six weeks for something that may not surface the award account anyway.

**Step 1a — Give the agents a key to the VPS** (~5 min, once; **this is the one
step everything else on the host waits for**) — **half done: item 2 is ✅ DONE
and verified 2026-08-31; item 3 is outstanding.**
1. An `ed25519` keypair already exists on `zelengs-macbook-air-2`:
   `~/agents/secrets/networth-vps.key` (private, mode 600, never leaves that
   machine, never in git, a PR or a log) and `…​.key.pub`.
2. ✅ **Done.** The **public** half is in `authorized_keys` on the VPS and works:
   verified 2026-08-31 by a `BatchMode=yes` login from `zelengs-macbook-air-2`
   over the tailnet. **It is installed for `root`**, which is recorded here
   rather than quietly relied on — task `28` provisions the dedicated
   unprivileged service user and everything the daemon does runs as that user,
   so this key being root's is an *administration* fact and must not become the
   account the daemon uses.
3. **Outstanding.** Add a **second, restricted** key for the unattended backup
   pull (§15): generate `networth-backup-ssh.key` on the same Mac and install its
   public half with
   `restrict,command="/usr/local/lib/networth/backup-ssh-dispatch"`. *(Rev 16
   changed this line: it used to force `networth backup serve-archive` directly,
   which made the pull's write-back impossible — `command=` ignores the client's
   command, so `record-pull` could never run. §15 specifies the dispatcher and
   the **four** verbs it allows: `build-probe`, `serve-archive`, `record-pull`,
   `record-drill`. Rev 17 corrects "two" — a stale count left over from before
   the dispatcher existed, in the step the owner actually executes — and narrows
   the first verb: **`build-archive current` is not on this key at all**.)* The unattended job then cannot open a shell on the
   host holding the Plaid master credential; the interactive key stays for
   `link.sh` and administration, where you are present. **Install this one under
   the service user, not `root`** — it has no administrative purpose.
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
4a. **Then run it once with the VPS unreachable** — turn Tailscale off on this
   Mac, or unplug the network — and see it still pass. *(Rev 16.)* That is not a
   nice-to-have: until rev 16 the drill compared the archive against a row in the
   database **on the VPS**, so it would have passed every rehearsal and failed
   the one situation it exists for. If it needs the VPS, it is not a backup test.
   It will report that it could not send its result; that is expected, and the
   result goes up on the next run (§14a.1).
5. **Do not proceed to Step 2 until it passes.** After the first Production Link,
   losing the tokens does not cost a re-link — a lost `access_token` cannot be
   recovered at all and strands permanent Item slots (**F2**, **F2a**, **F6**,
   §14a). *(This is still true after rev 16. **F7** makes a lost `public_token`
   recoverable for **30 minutes** (rev 18); it does nothing for an `access_token`
   that was stored and then lost, which is what this backup protects and why the
   gate stands.)*

**Step 2 — Link each institution** (~2 min each, once per institution)
1. Run `scripts/link.sh` **on this Mac** — `zelengs-macbook-air-2` (built by
   agents, run by the owner). It runs here because the backup is a pull and only
   this machine can perform one; it reaches the VPS over SSH for every step that
   needs the client secret, which never leaves that host.
2. It runs the **backup canary** first — builds a probe archive on the VPS, pulls
   it, decrypts it, deletes its local copy — and **refuses to continue if that
   fails.**
2a. It then mints the `link_token` on the VPS and **copies the recovery record to
   this Mac and reads it back** before it prints anything *(rev 17)*. If that
   fails, **no URL is printed** and the run stops. Steps 2 and 2a are the last
   moments anything can be refused (step 4), and both refuse rather than warn.
3. It prints a **Hosted Link** URL. Open it **in this Mac's browser**. **Enter
   credentials and MFA there** — that page is Plaid's; neither machine sees them.
   **Confirm the institution *and* the specific login before you finish this
   step**, because finishing it is what spends the slot. **Do not use any
   "send by SMS/email" option** if one is offered — Plaid charges per link
   delivered that way, and this project spends nothing (**F7**).
4. **Finishing Link is the irreversible moment (F2a).** Plaid creates the Item
   when Link succeeds and only then hands back a `public_token`. Everything
   after this is recovery of something that already cost a slot.
5. **Nothing to copy, and nothing to hurry — because you are not the
   mechanism.** The **VPS** polls for the completed session and collects the
   `public_token` itself (**F7**, §13), normally within seconds. **You may close
   the browser, close this laptop, and walk away**; the always-on host is doing
   the waiting.

   **What you are *not* being told is that there is a long window, because there
   is not one.** *(Rev 18, correcting rev 17.)* Rev 17's step 5 said *"You have
   six hours, not thirty minutes"* — **backwards.** Plaid retains the *session
   record* for six hours, but the `public_token` inside it is single-use and
   **dies after 30 minutes.** Nothing about that changes what you do here — the
   VPS is already polling and does not need you — but it changes one thing you
   might otherwise get wrong: **if the script tells you something went wrong,
   that is a 30-minute problem, not an afternoon's.** Read it when it appears
   rather than after coffee. If retrieval fails, the run says so, the flow is
   visible in `doctor`, and step 2a is the procedure — there is nothing to do by
   hand on the happy path, and no step here will pretend otherwise.

   *(Rev 16 deleted an older step that told you to paste the token promptly
   because losing it stranded the slot forever — that was wrong, Plaid keeps the
   session retrievable. **Rev 17 deleted rev 16's replacement too**: it said the
   script would fall back to asking you to paste if the poll failed. Hosted Link
   never puts a token in your browser — Plaid: "there is no frontend integration
   required (or possible)" — so there was never anything on your screen to
   paste.)*
6. The VPS exchanges the token, writes the `access_token` via `TokenStore` (mode
   600) **before** recording the item row, then `link.sh` builds and **pulls** a
   fresh archive and verifies it — reporting the Link complete only once a
   verified second copy is on this Mac (§14a).
7. Link the **highest-value institutions first** — slots are permanent (**F2**).

**Step 2a — If the VPS dies between Link finishing and the token being
exchanged** (rare — and **you have 30 minutes, so read this now, not later**)

*(Rev 17, from review. Before it, this situation was an immediately stranded slot
that no section admitted to: the Mac's newest archive predates the Link, so it
holds neither the `access_token` nor the `link_token`. **Rev 18 corrects the
clock**: rev 17 sized this procedure against six hours. The `public_token` lives
**30 minutes** from the moment your Link session finished; six hours is only how
long Plaid will still tell you *what happened*.)*

**Do this first, read the reasoning after.** Run:

```
scripts/link-recover.sh <flow_id>        # on this Mac; flow_id is printed by link.sh
```

It already holds the recovery record and the `link_token`. It will prompt you for
`client_id` and the production secret — paste them from your password manager or
Plaid's dashboard — and it does the rest: `/link/token/get`, then the exchange.
**That prompt is the only manual part, and it exists because this Mac must not
store the client secret (§15).**

If you are reconstructing the steps by hand:

1. You have what matters: step 3 of the Link run put the **`link_flow` recovery
   record on this Mac**, and the `link_token` in it is the one input that cannot
   be reissued.
2. Get `client_id` and the production secret from **Plaid's dashboard** — they
   have always been retrievable there, which is why the design copies the
   `link_token` and not the credential.
3. Call `/link/token/get` with that `link_token` **within 30 minutes of the Link
   session ending**, take `results.item_add_results[].public_token`, and exchange
   it on whichever host now holds the credential.
4. **Never** paste any of this into a chat, a file an agent reads, or a PR
   (§15) — the standing rule does not relax because it is an emergency.
5. **If the 30 minutes have passed, the slot is stranded and no copy anywhere
   changes that.** Do not re-link blindly — that spends another one. Record it
   against the budget (§14). For the next six hours `/link/token/get` will still
   name the session and its `item_id`, which is worth capturing for the budget
   record and for any conversation with Plaid.

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

   **The script is `scripts/provision-host.sh`** *(rev 19, task `28`)* — one
   file, no dependency beyond the base system, so no checkout of this repository
   ever lands on the host that holds the credentials. It runs **twice**, and the
   read-only `scripts/host-state.sh` is captured **three** times around those
   runs *(rev 20)*. From `zelengs-macbook-air-2`, as one sequence that stops at
   the first failure:

   ```
   (
     (
       set -o pipefail
       vps_key=~/agents/secrets/networth-vps.key
       mkdir -p ~/networth-run &&
       git -C ~/networth fetch --quiet origin main &&
       git -C ~/networth rev-parse FETCH_HEAD    >~/networth-run/reviewed-commit.txt &&
       git -C ~/networth show FETCH_HEAD:scripts/provision-host.sh >~/networth-run/provision-host.sh &&
       git -C ~/networth show FETCH_HEAD:scripts/host-state.sh     >~/networth-run/host-state.sh &&
       scp -i "$vps_key" -o IdentitiesOnly=yes ~/networth-run/provision-host.sh ~/networth-run/host-state.sh root@100.102.245.37:/root/ &&
       ssh -i "$vps_key" -o IdentitiesOnly=yes root@100.102.245.37 'bash /root/host-state.sh'      >~/host-state-0.txt 2>~/host-state-0.err &&
       ssh -i "$vps_key" -o IdentitiesOnly=yes root@100.102.245.37 'bash /root/provision-host.sh' 2>&1 | tee ~/provision-run-1.log &&
       ssh -i "$vps_key" -o IdentitiesOnly=yes root@100.102.245.37 'bash /root/host-state.sh'      >~/host-state-1.txt 2>~/host-state-1.err &&
       ssh -i "$vps_key" -o IdentitiesOnly=yes root@100.102.245.37 'bash /root/provision-host.sh' 2>&1 | tee ~/provision-run-2.log &&
       ssh -i "$vps_key" -o IdentitiesOnly=yes root@100.102.245.37 'bash /root/host-state.sh'      >~/host-state-2.txt 2>~/host-state-2.err
     )
     sequence_status=$?
     echo "sequence exit status: $sequence_status"
     exit "$sequence_status"
   )
   ```

   **Anything but 0: stop, and read the last file it wrote.** The status is
   printed *and* re-emitted, so `echo $?` afterwards still shows it — see below.

   Then, on `zelengs-macbook-air-2`:

   ```
   diff -u ~/host-state-1.txt ~/host-state-2.txt   # MUST be empty — this is criterion (4)
   diff -u ~/host-state-0.txt ~/host-state-1.txt   # what provisioning did, and nothing else
   ```

   **Why three captures and not two** *(rev 20; rev 19 asked for two, either side
   of both runs)*. The criterion is that **re-running changes nothing**, and the
   `0..2` diff cannot show it: that diff contains the service user, the ownership
   changes and the installed package, so it is expected to be non-empty. It
   establishes the outcome of the two runs combined — a different claim. Only
   `1..2` is the criterion, and it is empty or the criterion failed. Keep all
   three captures plus both transcripts; they are read back by someone who was
   not at the keyboard.

   **Why one chained sequence and not six commands** *(rev 20)*. Each piece of
   the shape above is load-bearing:

   - `set -o pipefail`, in a subshell so it does not outlive the sequence:
     without it `ssh … | tee` exits with `tee`'s status, so a **failed remote
     provisioning run reports success**.
   - `&&` between the steps: an unchained `scp` that fails is followed by a run
     of whatever `/root/provision-host.sh` was already on the host — **an older
     copy, silently**. Chaining also stops run 2 from starting after run 1 failed.
   - **the two files are extracted from `origin/main`, not copied out of the
     working tree** *(rev 21; rev 20 opened the chain with `git pull --ff-only`)*.
     `scp` faithfully copies whatever is in the checkout, so the question is what
     puts *reviewed* bytes there — and a pull does not. `git pull --ff-only`
     answers "did this branch move", not "is this `main`, unmodified": on a
     branch tracking something other than `main` it pulls that instead, and on
     any branch it returns **0 / “Already up to date”** while leaving a locally
     modified tracked file exactly as it is. Both were reproduced against a
     disposable clone. `git show FETCH_HEAD:<path>` reads out of the fetched
     object database, so the local branch, its upstream and every uncommitted
     edit are all irrelevant — and because the source is `origin/main`, the
     sequence cannot run until this work is **merged**.
   - `2>&1` into each `tee`: the script writes its failure diagnostic to stderr,
     which would otherwise be absent from exactly the transcript that is kept.
   - the captures are redirected rather than piped through `tee`, because those
     three files get diffed against each other and nothing but host state may
     enter them. Their stderr is kept beside them in `.err` rather than dropped.
   - **the outer subshell re-emits the status** *(rev 21)*. Rev 20 ended with
     `echo "sequence exit status: $?"`, which makes the *echo* the last command:
     the diagnostic said `1` and the pasted snippet still returned **0**,
     reproduced under both bash and zsh. A failure that prints as a failure and
     reports as a success is the same defect this list opens with, one level
     out. `exit "$sequence_status"` inside a subshell sets the snippet's status
     without closing the interactive shell it was pasted into.
   - **every `ssh` and the `scp` names the key** *(rev 22)*. Rev 21 left them
     bare, and on `zelengs-macbook-air-2` a bare `ssh root@100.102.245.37` is
     **`Permission denied (publickey)`**: that machine has no `~/.ssh/config`, no
     default identity file at all, and its running `ssh-agent` holds no
     identities, so there is nothing for `ssh` to offer. The sequence would have
     died on its first `scp` — after the extraction, before any capture. The key
     is the administration key from step 1a, `~/agents/secrets/networth-vps.key`,
     which is what step 1a already says it is *for* ("the interactive key stays
     for `link.sh` and administration"). *(Verified on the host itself on
     2026-09-02, read-only and before `S0`: with no `-i` the login is refused,
     and with `-i ~/agents/secrets/networth-vps.key` it returns `uid=0(root)`.)*
     `IdentitiesOnly=yes` is the other half and is **not** what that check
     shows: with no agent loaded it changes nothing, which is exactly why it is
     here — it keeps the `-i` above load-bearing on the day an agent *is*
     loaded, so that "this key authenticated" cannot quietly become "some key
     did". Only `tests/test_owner_runbook.py` holds that one. It reads the
     `IdentitiesOnly` value `ssh` would *use* rather than the one it can find,
     counts identities in both spellings, reads only the tokens each program
     actually parses as options — `ssh` stops at the remote command, `scp` at
     its first path operand — and fails on any other option-looking token there
     *(rev 23)*. A new flag here is a review rather than a silent pass, and a
     credential moved past that boundary is a failure rather than a pass.

   **The second transcript must end in `changed: 0`**, and the first one's
   `[changed]` lines are criterion (2). Each transcript prints the script's own
   `sha256`, so which version ran is a fact in the record rather than an
   assumption; compare it against
   `shasum -a 256 ~/networth-run/provision-host.sh`, and the commit those bytes
   came from is in `~/networth-run/reviewed-commit.txt`. That comparison means
   something only because both sides came out of `origin/main` — compared
   against a stale or edited checkout, both are the same unreviewed file.

   **The two provisioning runs are yours and neither is an agent's** — the script
   edits `sshd`, the firewall and ownership under `/etc/networth/`. The captures
   in between are not: `scripts/host-state.sh` only reads, which is why it can
   sit inside your sequence and why an agent may also run it at any time to check
   the record against the host. There is no rehearsal mode: the first run is the
   one that changes the host.

   Two things the script will **not** do on its own, and both print as a proposal
   for you instead: restricting root login (below), and enabling `ufw` if it is
   ever found switched off — `ufw enable` rebuilds netfilter, and this host's
   Tailscale exit-node forwarding rides on rules `tailscaled` inserts rather than
   on anything `ufw` stores.

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
   `networth-serve.service`. Confirm with `ss -ltnp` that the **`networth-serve`
   process's** socket is bound to one of the addresses in `TailscaleIPs` from
   `tailscale status --json` — this node has **two**, an IPv4 and an IPv6 — and
   not to `0.0.0.0`, the public IPv4, `[::]`, any public IPv6, or loopback.
   *(Rev 16 said "must not show it on `0.0.0.0`", which the public IPv4 and the
   IPv6 wildcard both pass. Rev 17 fixes the opposite error in the same
   sentence — see step 4 — and identifies the socket by **process**, because
   "something is on that port" is not the fact being checked; §13.)* This is the
   one misconfiguration that silently publishes the endpoint, so it is checked by
   hand once here and by a test forever after.
3. **Put the secrets in place** under the service user (§15), mode 600 — both
   `plaid.env` and `plaid-sandbox.env`, since a rehearsal needs its own
   credential and its own database.
4. **Record the host's approved public surface, then confirm networth added
   nothing to it.** Capture the set of non-loopback, non-tailnet listeners from
   `ss -ltnp` as the **baseline** (§13); on this host, today, that is exactly
   `sshd` on `0.0.0.0:22` and `[::]:22`, which is §15.1's single opening. The
   check from here on is that the set still **equals** the baseline, and
   `tailscale funnel status` shows no funnel.

   *(Rev 17, from review, and this is a correction to rev 16's own hardening
   step rather than a note about it. Rev 16 demanded that **no** non-loopback,
   non-tailnet listener exist anywhere — a claim about **the owner's host**, not
   about this project — and the live VPS fails it before networth is installed,
   because `sshd` is supposed to be there. A gate that is red on a correct
   machine gets edited or skipped, and then it is not a gate. **This is the
   owner's Tailscale exit node and it was here first (§15.1); "networth exposes
   nothing publicly" is ours to assert, "this host has no public listener" is
   not.** The baseline keeps rev 16's real intent — a new public listener is an
   event, not a background condition — while being true on day one.)*

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
