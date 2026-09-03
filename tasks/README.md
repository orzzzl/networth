# Task breakdown and assignment

Every entry below is written to be executed by a session with **no other context** —
concrete scope, the design sections that are normative for it, testable acceptance
criteria, and what it must not do. If you find yourself needing to read all 4,684 lines of
`DESIGN.md` to start a task, that task entry is defective; say so rather than guessing.

**Design history lives in `DESIGN.md`'s revision log and in git, not here.** Earlier
versions of this file carried 234 lines of revision archaeology ahead of the first
instruction. That is gone. If you need to know *why* a decision was made, `DESIGN.md` §18
and the git log of PR #1 have it.

## Status vocabulary

- `READY` — dependencies met; the assignee may start.
- `BLOCKED (x)` — waiting on `x`.
- `WIP` / `DONE` — claimed / merged.

**A task with an open PR is `WIP` from the moment the PR exists, not from the moment it
merges.** The row is the only thing a fresh session of an agent reads before deciding what
to start, and this project's founding failure is a registry that keeps serving an entry
after the thing it describes has changed. `05` sat at `READY` on `main` through two rounds
of review on PR #29 (caught 2026-09-01, after a board PR that edited the same table twice
without noticing).

## The rules that apply to every task

1. **No task may create a Production Plaid Item unless its entry says so explicitly.**
   Ten Items exist for the lifetime of the account, `/item/remove` does not return one
   (**F2**), and the slot is spent the moment Link *succeeds* — not at the exchange
   (**F2a**). Only tasks `08` and `09` may touch Production Link, and `09` consumes no
   slot. Everything else uses Sandbox, which is free and unlimited.
2. **Secrets never enter this repository** (`AGENTS.md` rule 1). Credentials live in
   `/etc/networth/` on the VPS and `~/agents/secrets/` on the Mac. Committed code reads
   them from those paths; example files carry key names only.
3. **Never present a number without its age** (`AGENTS.md` rule 4). Any code path
   producing a total also produces its `as_of` and staleness annotation.
4. **Use your own git worktree.** Two agents share this repo. `git add -A` is forbidden in
   a shared checkout — stage explicit paths.
5. **Open a PR; do not self-merge.** Your PR is reviewed by the other agent (see the
   Reviewer column). Prefix the title `[claude]` or `[codex]`.

## Assignment

Reviewer is always the other agent — that is what makes the column mechanical rather than
a judgement call. **No agent reviews a task it was assigned.**

### Phase 0 — owner gates

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 00 | Plaid account + Trial plan + O2 verification | — | **owner** | — | **DONE** (2026-08-30) |
| 00b | Install the constrained backup key on the VPS; escrow the backup key | 00a, 28 | **owner** | — | BLOCKED (00a, 28) |
| 00c | Install the Plaid **Sandbox** secret at `/etc/networth/plaid-sandbox.env` | 00 | **owner** | — | **READY (owner)** |
| 01 | UI target | — | — | — | **ANSWERED** — Flutter, Android only |

**Every row in this table names something the owner can do the day it says
`READY (owner)`, with the artifact already in his hands.** A row that needs an
agent to produce something first belongs in a phase below, blocked on the agent —
`BLOCKED (owner)` on work he cannot start parks a task where nobody looks and
spends his attention on a thing that does not exist yet. (2026-09-01: `00a` was
that row. He caught it, not us.)

### Phase 1 — foundation (no Production Item is reachable from any of these)

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 02 | Project scaffold + secret scanner | — | **claude** | codex | **DONE** (#19, 2026-09-01) |
| 03 | SQLite schema + migration runner | 02 | **codex** | claude | **DONE** (#22, 2026-09-01) |
| 04 | Domain model + `Store` repositories | 03 | **codex** | claude | **DONE** (#36, 2026-09-02) |
| 05 | `PlaidClient` wrapper + error taxonomy | 02 | **claude** | codex | **DONE** (#29, 2026-09-01) |
| 05a | `TokenStore` | 02 | **claude** | codex | **WIP** (PR #21) |
| 03a | Encrypted archive + Mac-initiated pull + restore drill — **built and tested without the installed key** | 03, 05a | **codex** | claude | BLOCKED (05a) |
| 00a | Generate the constrained backup keypair; pin its `command=` | 03a | **codex** | claude | BLOCKED (03a) |
| 03a-live | `03a`'s acceptance **over the installed restricted key**: negative SSH, battery pull, offline drill, escrow attestation | 03a, 00b | **codex** (the wire and the records) / **owner** (runs §19 step 1c) | claude | BLOCKED (03a, 00b) |
| 06 | Sandbox end-to-end rehearsal of the Link flow | 05, 05a, 00c | **claude** | codex | BLOCKED (05a, 00c) |
| 06a | Prove F7 in Sandbox + measure the four unknowns | 06 | **claude** (builds all; runs i–iii) / **owner** (runs iv's Mac half) | codex | BLOCKED (06) |

### Phase 2 — linking (the only phase that spends the scarce resource)

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 07a | Automatic `public_token` retrieval + `link_flow` state machine | 03, 05, 05a, 06a | **codex** | claude | BLOCKED (06a) |
| 07b | `scripts/link-recover.sh` — lost-VPS exchange with a durable sink | 05a, 07a, 03a, 00b | **claude** | codex | BLOCKED (07a, 00b) |
| 26a | Item budget **core** — the remaining-slot count | 04 | **claude** | codex | **READY** |
| 08 | `scripts/link.sh` — owner-run Production Link | 04, 06, 06a, 07a, 07b, 03a-live, 16, 26a | **claude** (script) / **owner** (runs it) | codex | BLOCKED |
| 09 | `scripts/relink.sh` — Link update mode | 08 | **claude** | codex | BLOCKED (08) |
| 12b | Replacement-Item reconcile flow | 04, 09 | **claude** | codex | BLOCKED (09) |

### Phase 3 — sync and the honesty machinery

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 10 | Item health poller | 04, 05 | **codex** | claude | **DONE** (#38, 2026-09-02) |
| 11 | `StalenessMachine` — two axes | 04, 10 | **codex** | claude | **READY** |
| 12 | Full sync: holdings + balances → observations | 04, 05, 11 | **codex** | claude | BLOCKED (11) |
| 13 | Manual assets: property revision log + share counts | 04 | **claude** | codex | **READY** |
| 14 | Snapshotter + net-worth computation | 12, 13 | **codex** | claude | BLOCKED |
| 15 | Alerts: payload-carried delivery | 11 | **codex** | claude | BLOCKED (11) |
| 16 | systemd units + timer + due-ness engine + catch-up + **live install** | 10, 12, 14, 15, 07a, 28 | **codex** | claude | BLOCKED |
| 27 | Vest-date nudge to re-confirm a share count | 13, 15 | **claude** | codex | BLOCKED |

### Phase 4 — getting the number onto the phone

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 17 | `NetWorthQuery` read layer | 14 | **codex** | claude | BLOCKED (14) |
| 18 | CLI: `show` / `history` / `doctor` | 17 | **codex** | claude | BLOCKED (17) |
| 19 | Payload schema + `Publisher` (encrypt) | 17 | **codex** | claude | BLOCKED (17) |
| 20 | The daemon's one HTTP route + freshness monitoring | 19, 28 | **codex** | claude | BLOCKED (28) |
| 19a | Pairing: `networth pair` / `revoke` + app secure storage | 19, 20 | **codex** | claude | BLOCKED (20) |
| 21 | Flutter app skeleton | 19 | **claude** | codex | BLOCKED (19) |
| 22 | Dual-staleness UI + alert surface + downgrade handling | 21, 19a | **claude** | codex | BLOCKED |
| 23 | History curve, incomplete snapshots visually distinct | 21 | **claude** | codex | BLOCKED (21) |
| 24 | Release signing + APK delivery | 20, 21, 22 | **claude** | codex | BLOCKED |
| 26 | Remaining-slot **surfacing** — `doctor` and the app agree | 26a, 18, 22 | **claude** | codex | BLOCKED |

### Phase 5 — operations

| # | Task | Deps | Assignee | Reviewer | Status |
|---|---|---|---|---|---|
| 28 | VPS provisioning + hardening (**base host only**) | — | **claude** (the script, criteria 1+3, and the records) / **owner** (runs §19 step 3.1 — criteria 2+4) | codex | **WIP — owner** (claude's half merged, #34; criteria 1+3 met, 2+4 wait on his two runs) |
| 25 | ~~DB backup/restore~~ | — | — | — | **SUPERSEDED by 03a** |

**Totals:** claude 18, codex 17, owner 3 (+1 answered, 1 superseded) — counted off the rows
above at this revision, which is the only way this line has ever been wrong. **Four agent
rows are shared with the owner** because he is the one who runs part of them — three of
claude's (`06a`'s measurement (iv), `08`, and `28`'s live runs, which are §19 step 3.1) and
one of codex's (`03a-live`, whose §19 step 1c half is his). Agents write those commands; the
owner executes them.

**A row is shared the moment any of its acceptance criteria is a `DESIGN.md` §19 step**,
and the entry must say which criteria those are. §19's preamble — *"agents must never
perform these"* — has no exception for a step an agent could technically run, so an
assignee cell naming only an agent is a defect whatever the agent is capable of. That is
how `03a-live` shipped with three owner-run steps inside a codex-only row (caught in review,
2026-09-01).

## Why the split is shaped this way

**The first fork is real, not nominal.** `02` is the single root and one agent must land it
before anything else starts. The moment it merges, `03` (codex) and `05` + `05a` (claude)
proceed in parallel — different agents, disjoint directories, no handoff. That is the
owner's stated requirement that the root fork not become one agent's serial queue.

**The author of a contested design decision is not its sole implementer.** Design rev 18
merged unreviewed, and **fifteen open issues (#3–#17)** track what the review would
otherwise have gated on — including five (**#13–#17**) that codex found in the rev-18 range
*after* the review was cancelled and filed rather than dropped. Where I (claude) authored
the fix, codex implements it, so the first person to walk through the mechanism is not the
person who invented it:

- `03a` implements the `probe_generation` canary — my rev-18 replacement for a
  cross-machine clock comparison (**issue #9**).
- `19` implements the payload/`seq` scheme after I **deleted** the epoch rather than
  re-scoping it, which is the largest unreviewed structural change in rev 18 (**issue #8**).
- `07a` implements the ten-state `link_flow` machine I wrote (**issue #7**), which is
  pre-production-critical.

`07a` is the uncomfortable one and I want it argued rather than assumed: it puts a
cross-agent handoff (`06a` claude → `07a` codex → `07b` claude ∥ `16` codex → `08` claude)
directly in the pre-production critical path, where a quota stall costs the most. I judged
the second-pair-of-eyes benefit worth it because this is the code path that burns lifetime
slots. **If codex disagrees, this is the row to push back on.**

**`07b` is new in this revision and exists because issue #13 found a script with no owner.**
`DESIGN.md` §19 step 2a tells the owner to run `scripts/link-recover.sh` after losing the
VPS mid-flow, and no task built it. It goes to claude rather than codex for two reasons
that are about the machine, not the agent: it runs on `zelengs-macbook-air-2`, which is the
side `08`/`09` already live on, and it must **not** be serialised behind `07a` in codex's
queue when it can run beside `16` instead. That gives the last pre-production wave two
agents working in parallel on disjoint files, both gating `08`.

**Specialisation where it is real.** Codex takes the backend spine — schema, Store, sync,
scheduling, the read/publish layer. Claude takes the Plaid client surface, the Sandbox
measurement chain, and all of Flutter (`21`–`24`), where it has recent app experience on
this machine.

`28` (host provisioning) is the one row that does not follow from that split: it moved to
claude on 2026-09-01 for load balancing, and it is named here so the exception is visible
rather than looking like drift. It is the cheapest row to move because **nothing else of
codex's reads its files** — its dependents (`16`, `20`, and the owner's `00b`) consume a
provisioned host, not its code. One side effect worth recording without overselling it:
`16` now installs and verifies units on a host a *different* agent provisioned, where
before both were codex's. That is not the "not its sole implementer" rule above being
satisfied — that rule is about the author of a contested design decision, and `28` invents
no design — it is just a second pair of eyes on the host, for free.

**File collisions.** Concurrent pairs and what they touch:

| Wave | Concurrent | Files | Collide? |
|---|---|---|---|
| 1 | `02` alone | repo root | — |
| 2 | `03` ∥ `05` ∥ `05a` | `networth/db/` + `migrations/` ∥ `networth/plaid/` ∥ `networth/secrets/` | No |
| 3 | `04` ∥ `06` | `networth/model/` + `networth/store/` ∥ `scripts/` + `tests/sandbox/` | No |
| 4 | `03a` ∥ `06a` | `networth/backup/` + `scripts/restore-drill.sh` ∥ `tests/sandbox/` + `scripts/link-recover.sh` | No |
| 5 | `07b` ∥ `16` | `scripts/link-recover.sh` ∥ `networth/scheduler/` + `deploy/systemd/` | No — but see below |

`06a` now creates `scripts/link-recover.sh` in its first form and `07b` extends it, so that
one file is written in two waves by the same agent — sequential, not concurrent, and never
open in two worktrees at once.

`26a` is not in this table because it is not a wave: it needs only `04`, it touches its own
`networth/budget/`, and it collides with nothing. It is claude's filler work whenever
claude is otherwise waiting — which is most of Phase 2, since `07a` sits in codex's queue
between `06a` and `07b`. It has to land before `08` regardless.

Wave 5's files are disjoint but its *subject* is not: `07b` recovers a flow that `16`'s
worker may still be driving. They do not collide in git; they can contradict each other in
behaviour.

**And the shared contract cannot be what an earlier draft of this file claimed it was.**
That draft named the conditional `EXCHANGING` claim as the thing keeping both paths from
exchanging the same `public_token`. That claim is a conditional `UPDATE` against **one
SQLite file on the VPS**, and the entire scenario `07b` exists for is the one where that
host and that file are unreachable. A restored copy on a replacement host is a *different*
database. Two files can both win a claim they do not share, so the mechanism named as the
safety property does not span the two processes it was supposed to serialise.

The honest contract is two things, and both tasks are written against it:

- **The fence is that the old VPS is provably not running** — a precondition the owner
  establishes in the provider control plane before recovery exchanges, not a lock. See
  `07b`. Unreachable is not off.
- **At-most-once on the wire is Plaid's, not ours.** The `public_token` is single-use, so
  if both hosts do reach the API, one exchange fails. What that failure *means* is measured
  in `06a` (ii) — including whether the first `access_token` survives it — and both `07b`
  and `16` must classify the losing branch from that measurement rather than from a guess.
  Losing that race is **not** a lost slot; it means the other host holds the credential.

Whichever of the two lands second proves with a test that the loser's branch is classified
honestly. Neither may weaken the local claim to make its own path simpler: the claim is
still what serialises two runs *on the same host*, which is a real failure mode (two
terminal windows, a re-run after a crash) even though it is not the cross-host one.

**Two known shared files, called out because they are the only ones:**

- `pyproject.toml` — any task adding a dependency touches it. Add your dependency in its
  own commit, rebase rather than merge, and never reformat the file.
- **CLI subcommand registration.** Many tasks add a `networth <verb>`. To keep this from
  becoming a shared registry every task edits, `02` must establish
  `networth/commands/<verb>.py` with **auto-discovery** — one file per command, no central
  list to edit. A task that needs to edit a shared registry file to add a command means
  `02` got this wrong.

**Quota fragility.** Both agents have exhausted daily limits during this project, twice
stalling it for hours. If an assignee is out of quota and a `READY` task is waiting,
**reassign it rather than letting it queue** — update the Assignee cell, note the swap in
the PR, and the reviewer becomes whoever did not write it. This does not require the
owner's approval; letting a root task idle behind a stalled agent is the failure mode.

**Give each agent a branch of the graph that does not stall on the other's review turn.**
With two agents and no third reviewer, a chain where every agent task depends on a task the
other agent must review first converts one quota outage into an outage for both: on
2026-09-01 Codex idled for hours across two Claude quota resets because `04` waited on `03`
and `03` waited on a Claude review. When sequencing, check that each agent holds at least
one `READY` task whose dependencies are all merged — and **say so explicitly** if the graph
makes that impossible, rather than discovering it as idle time.

**2026-09-01, second occurrence, this time on the claude side — and the fix is a
reassignment.** Merging `05` left claude with no `READY` row at all: `05a` is with the
owner (issue #28) and every other claude row sits behind `04` (`26a`, `13`), behind
`05a`+`00c` (`06`), or further down the chain. Codex held **two** `READY` roots, `04` and
`28`, neither depending on the other. `28` therefore moved to claude and `04` stayed with
codex: both agents hold one `READY` root, and nobody is idle.

`28` is a **shared** row — the owner runs `DESIGN.md` §19 step 3.1 — so what moved to claude
is its script half plus criteria (1) and (3). That is real, startable-today work and it does
what this paragraph needs it to do; it does not mean claude can *close* the row alone. See
`28`'s entry for the split.

**Count the other agent's independent `READY` roots before concluding that a swap only
relocates the problem.** The first version of this paragraph reassigned nothing and argued
that taking one of codex's tasks "would leave *him* idle instead" — true when an agent has
one root, false here, and codex rejected it on exactly that ground in review of PR #32.
Note also what that argument leaned on: the quota-stall rule above is a *mandatory trigger*
for reassignment, not a licence to reassign only when quota is the cause. Reading a trigger
as a prohibition is how an agent talks itself into idling.

**2026-09-02, third occurrence — and this time no reassignment fixes it, which is the thing
to say out loud rather than discover.** `28`'s claude half merged, so both claude rows are
now waiting on the owner (`05a` on issue **#28**, `28` on his two provisioning runs) and
claude holds **no** `READY` root. Codex holds exactly one — `04` — so by the rule two
paragraphs up, taking it would move the idleness rather than remove it. The graph itself is
the constraint here: every remaining claude row sits behind `04` (`26a`, `13`), behind
`05a` + `00c` (`06`), or further down. Whoever reads this next should expect claude idle
until one of four things happens, and none of them is an agent's to do: `04` merges, the
owner answers `#28`, he installs `00c`, or he runs step 3.1.

`04` landed the same day (#36), which freed `26a` and `13` and ended that idle window; the
paragraph above is kept as the record of it, not as current state.

Still outstanding, in the order they would land: the owner answers `#28` (frees `05a`), the
owner installs `00c` (with `05a`, frees `06`), and the owner runs
`scripts/provision-host.sh` twice on `tokyo-exit` — `28`'s criteria (2) and (4), which free
`16`, `20` and `00b`.

That last one is new, and it is here because it just became true rather than because it was
forgotten: `28`'s entry promised the owner would be asked *only* once the script existed —
"an owner row is a row he can act on today" — and the PR that adds `scripts/provision-host.sh`
is the handover. Before it merged, there was nothing for him to run.

**Owner-only work stays with the owner.** `00` (done), `00b` (installing the constrained
backup key, and escrowing it) and `00c` (installing the Sandbox secret) are not assignable
to an agent, and `08` is a script an agent writes and **the owner runs** — no agent ever
performs a Production Link or sees the Production secret.

**An owner row is a row he can act on today.** Everything that has to be built before he
can act belongs to an agent, in the phase where that work lives, blocked on the task that
defines it — `00a` is the worked example. Marking the owner's half `BLOCKED (owner)` while
the agent half does not exist yet parks the task where nobody looks and asks him for
something impossible; it cost this project a task sitting in Phase 0 until he checked the
machines himself and told us (2026-09-01).

---

# Task detail

## Phase 0 — owner gates

### 00 — Plaid account, Trial plan, O2 verification — **DONE** (owner, 2026-08-30)

**Outcome:** Trial active at `0/10`. Production `client_id`/`secret` issued. Plaid states
bank access is **automatic on the Trial** — no per-institution request (**F4**). O2 = GO.

**Two things it leaves behind, both still live:**

- **The runbook trap.** "Get production access" in the Plaid dashboard is the **paid**
  funnel; the Trial is a separate plan not offered inside it. The owner walked into it and
  stopped at the plan picker. No document may send anyone back there (`DESIGN.md` §19
  step 1).
- **The Production secret must never reach an agent.** Agents write the command; the owner
  runs it on the VPS (§15).

### 00b — Install the constrained backup key; escrow the backup key — **owner only**

**Blocked on `00a` and `28`, both ours.** `00a` produces the line; `28` creates the
dedicated service user the design requires the key to be installed under, so a paste that
happens before `28` puts the key on the wrong account. This entry used to be `00a` and used to read
`BLOCKED (owner)`, which was false: it asked the owner to install a key that does not
exist. Verified on the machines 2026-09-01 — `~/agents/secrets/` holds only
`networth-vps.key(.pub)`, and `authorized_keys` on `tokyo-exit` holds exactly two entries,
`tokyo-exit-tailscale` and `networth-daemon@claude-agents`. The interactive key is
installed and working; the backup key is not, because there was never anything to install.

**What the owner does, once `00a` hands him the finished line and `28` has made the service
account:** `DESIGN.md` **§19 step 1a, item 3** — *not* step 1c, which is a different
sitting and a later one (corrected 2026-09-01; the wrong pointer sent him to the step that
confirms the backup works to do the step that installs the key it pulls over). Paste one
`authorized_keys` entry for `networth-backup-ssh.key`, already carrying its
`restrict,command="/usr/local/lib/networth/backup-ssh-dispatch"` prefix, under the service
user.

Then, at the same sitting, **escrow** `networth-backup.key` — the archive key, a different
key from the SSH one above — by copying it into a password manager or writing it down. That
is the first half of §19 step 1c item 3, brought forward to here because the key already
exists by now and a second trip serves nothing. **The `networth backup attest-key` run that
*records* the escrow stays in step 1c**, where the rest of that step is, and is
`03a-live`'s owner half; `03a`'s criterion 2 is satisfied by that run, not by this one.

**Already done and verified:** the tailnet half, and the interactive key.
`zelengs-macbook-air-2` is Connected at `100.96.163.67`; the VPS host key matches across
its public and tailnet addresses; `tailscale ping` is direct; SSH Mac→VPS works with the
peer observing `100.96.163.67`.

**Must not:**

- **Install an unrestricted backup key now and add the restriction later.** That inverts
  the security property the two-key split exists for: the whole point is that a compromised
  laptop cannot open a shell on the host holding the Plaid master credential. The key is
  generated when `03a` defines the command, and the owner receives the finished constrained
  line in **one** step. (Owner instruction, 2026-09-01.)
- **No agent may ever ask the owner for a password** (§15.1).
- **Never write a bare hostname prefix into a config, unit, script or runbook step.** There
  are **four** MacBook Airs on this tailnet and they are four different computers,
  differing only by suffix. `zelengs-macbook-air` is a *different machine* that a prefix
  match silently selects. Write the full name or the IP (§19 step 1b).
- Block foundation work on this. What waits on it is `03a-live` (the live acceptance,
  including `03a`'s criteria 2 and 3) and `07b`'s emergency artifact, which is encrypted
  under the escrowed key. Everything through `03a` itself proceeds without it.

### 00c — Install the Plaid Sandbox secret — **owner only**, actionable today

**Why it is its own row.** It was buried inside `06`'s status as
`BLOCKED (05, 05a, Sandbox secret)`, which reads as blocked on two agent tasks and hides
the one part the owner could have done weeks ago. That is the same defect as a false owner
block, pointing the other way: it does not waste his attention, it wastes the parallelism.
Doing it now means `06` is gated only on `05` and `05a` when they land.

**What the owner does:** install the Sandbox `client_id`/`secret` at
`/etc/networth/plaid-sandbox.env`, mode `0600`, same owner-installs-it rule as every other
credential (§15, §19 step 3.3). Plaid issues one `client_id` per team with a **separate
secret per environment**, so this is a different value from the Production one and both
files exist side by side — which is exactly why `NETWORTH_ENV` selects the credential file,
the items file and the database together, with no default (`AGENTS.md` rule 1).

**The artifact is already in his hands:** task `00` is DONE, so the Plaid dashboard account
exists, and `/etc/networth/` exists on `tokyo-exit` (verified 2026-09-01: `drwx------`,
root-owned).

**Must not:** put the Sandbox secret anywhere an agent reads it from the repo, or let a
Sandbox credential sit in a file labelled production — `06`'s fourth criterion makes that a
**startup failure**, not a run nobody questions.

---

## Phase 1 — foundation

**Nothing in Phase 1 can reach a Production Item.** These are the tasks the owner
authorised to start immediately.

### 02 — Project scaffold + secret scanner — **claude** — **DONE** (#19, 2026-09-01)

**What to build.** The repo skeleton every other task builds inside:

- Python package layout `networth/`, virtualenv, pinned dependencies in `pyproject.toml`.
- Format / lint / test toolchain, wired into CI (GitHub Actions) and runnable locally by
  one command.
- `scripts/check-no-secrets.sh` — a scanner for credential-shaped strings — installed
  **both** as a pre-commit hook and as a CI job.
- `networth/commands/` with **auto-discovery**, one module per CLI verb and **no central
  registry file**. See the collision note above: this is the single design decision in `02`
  that other tasks depend on to avoid editing a shared file.

**Normative:** `DESIGN.md` §16 (stack), §15 (what may never be committed). `AGENTS.md`
rule 1.

**Acceptance:**

- [ ] `git clone` → one documented command → tests pass on a clean machine.
- [ ] CI runs format, lint, tests and the secret scanner on every PR.
- [ ] The scanner **fails** on a planted fixture containing a Plaid-shaped
      `access_token`, a `client_id`/`secret` pair, and a private key header — one test per
      shape, each asserting a non-zero exit.
- [ ] The pre-commit hook blocks a commit containing a planted secret.
- [ ] A new file `networth/commands/demo.py` is discovered and invocable as
      `networth demo` with no other file edited. This is the collision-avoidance
      criterion; test it explicitly.

**Must not:** touch any credential, contact any network service, or add a dependency on
Plaid. This task exists before the first commit that could leak, because the repo is
**public**.

### 03 — SQLite schema + migration runner — **codex**

**What to build.** `DESIGN.md` **§7 verbatim**, plus a forward-only migration runner.

Core disciplines the schema must encode:

- Integer minor units; UTC timestamps.
- **Two clocks per observation** — `fetched_at` and `source_as_of` + `source_clock`. This
  is the project's founding discipline and it applies everywhere, including backups
  (`built_at` vs `pulled_verified_at`).
- `lineage_id` on accounts; `snapshot.sync_run_id` UNIQUE.
- **No `profile_id`** — dropped as speculative generality (§2).

**Tables §7 requires** (do not invent storage; if a section claims to store something with
nowhere to put it, that is a defect to report, not to improvise around):

- `published_envelope` — nonce, ciphertext‖tag and the four AAD fields **as served**, with
  a **partial unique index** (`WHERE is_active = 1`) so "which envelope is current?"
  cannot have two answers (§6.3.1).
- `backup_archive` — `built_at` and `pulled_verified_at` as **separate** columns. An
  archive on the VPS is not a backup of the VPS; only the second column may answer "is
  there a second copy?" Also `archive_sha256`, `byte_size`, `archive_id`.
- `backup_state` — singleton: `key_escrow_confirmed_at`, `last_verified_restore_at`.
- `daemon_state` — singleton: `publish_epoch`, a **diagnostic only** (see issue #8; it is
  no longer packed into `seq`). Written by the restore procedure, read by `doctor`,
  touched by nothing else.
- `link_flow` — the pending-Link recovery row; `secret_ref` resolves the `link_token`
  through `TokenStore`. Columns its state machine needs: `started_at`/`finished_at`
  **observed** from `/link/token/get`; `token_exchange_expires_at` (`finished_at` + 30 min
  — **the** deadline) and `session_retention_expires_at` (`finished_at` + 6 h,
  diagnostics) as **separate derived** values, `NULL` meaning *unknown*, never *passed*;
  `exchange_claimed_at` / `exchange_claim_owner` / `exchange_attempts`; and a ten-value
  `state`.
- `link_flow`, **the identifiers a support ticket needs when the exchange response is the
  thing that was lost** (issue #14): `link_session_id`, persisted the moment
  `/link/token/get` first exposes it; `item_id`, persisted the moment any exchange response
  reaches the process; and Plaid's `request_id` **per attempt**, which means a child table
  (`link_exchange_attempt`) rather than a column, since `exchange_attempts` can exceed one.
  `/link/token/get` does **not** return `item_id` — that is the whole reason these must be
  captured when seen rather than re-derived later.
- `link_flow`, **the columns the VPS reaper reads** (issue #16): `material_reaped_at` and
  `secret_ref_cleared_at` as **separate** nullable columns. One column cannot express the
  crash between the two deletions, and expressing it is the entire point of the ordering.

**Normative:** §7, §6.3.1, §14a.1, §9.3a. Issues #7, #8, **#14**, **#16**.

**Acceptance:**

- [ ] **Both singletons are declared `id INTEGER PRIMARY KEY CHECK (id = 1)`** and
      **seeded by the migration.** `CHECK` alone constrains the *value*, never the
      multiplicity — measured in `sqlite3` 3.51.0, two rows with `id = 1` both insert.
- [ ] **A test attempts the second insert into each singleton and sees it rejected.** This
      is a one-word difference that reads correct and is not; the test is the only thing
      that distinguishes them.
- [ ] Every singleton write is an `ON CONFLICT(id) DO UPDATE` upsert, never an `INSERT`
      that hopes to be first.
- [ ] The partial unique index on `published_envelope` is tested by attempting two active
      envelopes.
- [ ] Migrations run forward from empty and are idempotent on re-run.
- [ ] A `link_flow` row in `URL_MINTED` whose URL expired is **not** counted as a stranded
      Item (issue #7, **F2a** — no slot was spent).
- [ ] **A test writes a `link_flow` row in every state and asserts `link_session_id` and
      `item_id` are storable independently** (issue #14) — `item_id` arrives only with an
      exchange response, `link_session_id` arrives before one, and a schema that requires
      them together cannot record the crash case where only the first exists.
- [ ] `link_exchange_attempt` holds `request_id` per attempt and has **no column that could
      hold a token**. Grep the schema for it in the same test.

**Must not:** add `webhook_event` or any other table with no writer — dead schema is not a
reservation (§2). Do not store `db_row_counts_json`, `item_count` or the token digest in
`backup_archive`; those belong in the manifest sealed **inside** the archive (§14a.1),
because the drill must not validate an archive against a row on the machine whose loss is
the entire scenario.

### 04 — Domain model + `Store` repositories — **codex**

**What to build.** The seam every other component reads through. Append-only observations
and snapshots over the `03` schema.

**Normative:** §7, §2 (reservation 2), §10.

**Acceptance:**

- [ ] Snapshots append per successful run and are **idempotent on `sync_run_id`**.
- [ ] Nothing is edited in place; a correction is a new row.
- [ ] Queries never assume a fixed set of accounts (§2 reservation 2) — a test adds an
      account mid-history and the existing queries still answer.
- [ ] Every read that returns a figure also returns its `as_of` and `source_clock`. A
      `Store` method that can return a bare number is a defect.

**Must not:** perform any I/O beyond SQLite. No Plaid calls, no filesystem beyond the DB,
no network.

### 05 — `PlaidClient` wrapper + error taxonomy — **claude**

**What to build.** The **one** place Plaid errors become our states (§8.2):
`DEGRADED` / `NEEDS_REAUTH` / `REVOKED`.

Must cover at minimum: `ITEM_LOGIN_REQUIRED`, `PENDING_EXPIRATION`,
`USER_PERMISSION_REVOKED`, `ITEM_NOT_FOUND`.

**Normative:** §8.2, §8.4, §16.

**Acceptance:**

- [ ] Unit-tested against synthetic fixtures — no live calls in the test suite.
- [ ] Every listed error code maps to exactly one state, and an unknown code maps to a
      loud "unrecognised", never silently to healthy.
- [ ] `NETWORTH_ENV` is **required with no default** and selects the credential file, the
      items file and the database path **together**; the process asserts the selected
      file's `PLAID_ENV` matches and **refuses to start** otherwise.

**Must not:** build a path that waits for `PENDING_DISCONNECT`. It is **deliberately not a
transition** — it is webhook-only and invisible to `/item/get` (**F8**, §8.4). Handle the
code if Plaid ever surfaces it; do not poll for it. (See issue #12 — if the owner reverses
O9, §8.4a is the costed plan.)

### 05a — `TokenStore` — **claude**

**What to build.** A narrow interface over secret storage with a mode-600 file backend.
Small, but it must exist **before anything reads a token**, or file reads scatter across
the codebase (§2 reservation 3).

**Normative:** §2 (reservation 3), §15, §14a. Issues #11, **#15**.

**Acceptance:**

- [ ] Files are created mode 600; a test asserts the mode on disk.
- [ ] **A token written but not yet committed to the DB is discoverable after a restart and
      attributable to its `flow_id`, and it carries the `item_id` that came back with it**
      (issue #15). Concretely: the `secret_ref` naming scheme encodes `flow_id`, and the
      stored record holds `item_id`. Without this, a crash between `fsync` and `COMMIT`
      leaves a perfectly good credential on disk that nothing can match to the flow that
      earned it — and `07a` would then send a recoverable case to Plaid support as lost.
- [ ] A `reconcile(flow_id)` read exists that answers "is there already durable material for
      this flow?" **without** returning every token (see Must not).
- [ ] The database stores a **`secret_ref` key name, never a secret value** — a test
      asserts no token material appears in any table.
- [ ] Tokens are `fsync`ed **before** the `item` row that references them. An orphan token
      is harmless and recoverable; an `item` row whose `access_token` is missing strands a
      lifetime slot.
- [ ] Deletion order is **material first, then `secret_ref`** — a crash between them
      leaves a *visible dangling ref*, not an invisible orphan (issue #11). Tested with an
      injected crash.
- [ ] No token value is ever logged, echoed, or included in an exception message. Test the
      exception paths specifically.

**Must not:** expose an interface that returns all tokens at once, or accept a path
outside the configured directory.

### 03a — Encrypted archive + Mac-initiated pull + passing restore drill — **codex**

**What to build.** The VPS *builds* an encrypted archive into a local directory and
**never initiates anything toward any Mac**. `zelengs-macbook-air-2` (`100.96.163.67`)
**pulls** it. macOS runs no `sshd` by default and the Mac sleeps, so a push would mean
opening an inbound service on the machine holding the second copy and retrying into a
sleeping laptop forever. A pull leaves the internet-facing host with **zero** new outbound
trust relationships.

**Normative:** `DESIGN.md` §14a and **§14a.1**, §9.3a, §15. Issues #8, #9, #11.

**This task stops at the last thing provable without the installed key; `03a-live` is the
rest.** The split is not tidiness — it is what makes the graph executable. Several criteria
below are live properties of an `authorized_keys` line that `00a` has not generated and the
owner has not installed: a real `ssh` refused a shell, a pull observed on battery, a drill
against an archive that was actually transferred, an attestation of a key that is actually
escrowed. Requiring those *here* while `00a` waits on this task is a cycle — the previous
revision moved it rather than removed it, which is what the review caught.

The rule for deciding where a criterion belongs: **if it can only be observed after the
owner pastes the line, it is `03a-live`'s.** Everything else — the builder, the dispatcher
and its allow-list, the puller, the drill logic, the manifest, the canary and its rate
limit — is built and tested *here*, against local paths and a directly-invoked dispatcher
with `SSH_ORIGINAL_COMMAND` set. The dispatcher is an ordinary program; nothing about
testing it requires arriving over SSH. Concretely, these move to `03a-live` and are struck
from this task's DONE gate: criteria **(2)** and **(3)** below, the drill's observed
offline run, the over-the-wire negative tests, and the battery pull. Their build-side
counterparts stay: the `attest-key` command exists and is tested against a synthetic
escrow, the drill runs against a locally built archive, the dispatcher refuses every
non-allow-listed verb when invoked directly, and the puller's write-back behaviour is
proven against a fake transport.

**Build-side requirements a naive `rsync` gets wrong:**

- The database is WAL-mode: produce the archive with **`VACUUM INTO`** (or the online
  backup API). Copying the `.db` while writes continue can omit committed data sitting in
  the `-wal`.
- The DB snapshot and the `TokenStore` copy happen under **one `flock` shared with token
  writes**.
- Publish atomically at **both** ends — the builder renames into place (a puller arriving
  mid-build otherwise fetches a truncated file); on receipt it is temp name → `fsync` →
  verify → `rename`.
- **Build order** (the obvious order cannot work): mint `archive_id` → snapshot under the
  `flock` → compute counts/fingerprints **from the copies** → seal to `.tmp-<archive_id>`
  → `fsync` → hash the sealed file → `INSERT` the row → `rename()`. The archive never
  contains its own `backup_archive` row, and that is correct.

**The authenticated manifest** (§14a.1) lives **inside** the sealed bundle: `archive_id`,
schema version, row counts, item count, `probe_generation`, and token fingerprints.
Fingerprints are `HMAC-SHA256(K_fp, …)` truncated to 128 bits, with
`K_fp = HKDF-SHA256(backup_key, salt=archive_id, info="networth/token-fingerprint/v1")` —
so no salt is stored anywhere and an attacker without the key cannot compute them. The
fingerprint binds **Item identity**:
`fp(item) = HMAC(K_fp, LP(item_id) ‖ LP(secret_ref) ‖ LP(access_token))` with
**length-prefixed** fields — a delimiter would let `"ab"|"c"` and `"a"|"bc"` collide.
`item_token_binding_sha256` hashes the canonical `item_id`-sorted **mapping**, not a set.

**The forced command is a dispatcher.** `restrict,command="networth backup serve-archive"`
would make the pull's own write-back impossible — OpenSSH runs *that* and discards the
client's command (`sshd(8)`), so `record-pull` could never execute and would fail silently
in the under-reporting direction forever. Install
`restrict,command="/usr/local/lib/networth/backup-ssh-dispatch"`; it reads
`SSH_ORIGINAL_COMMAND`, allow-lists exactly **four** verbs — `build-probe`,
`serve-archive <current|probe>`, `record-pull`, `record-drill` — never passes any of it to
a shell, validates every argument by pattern, and logs rejections.

**Acceptance — the three criteria:**

- [ ] **(1) A destination in a separate failure domain** — satisfied by construction
      (another machine). The check runs on **every pull** and **fails closed**:
      `pulled_verified_at` stays `NULL` unless the Mac holds the archive, decrypted it,
      and reconciled it.
- [ ] **(2)** — **`03a-live`'s** (needs a key that is actually escrowed, which is `00b`).
      `networth backup attest-key` records `key_escrow_confirmed_at` — an **attestation,
      not a proof**. The runtime key is `/etc/networth/networth-backup.key`; the owner's
      escrow copy is not a second runtime location. *Here:* the command exists and is
      tested against a synthetic escrow.
- [ ] **(3)** — **`03a-live`'s** (needs an archive that was actually pulled).
      `scripts/restore-drill.sh` runs **on `zelengs-macbook-air-2`**, against the archive in
      its own destination directory — the copy a real recovery would reach for, on the
      machine that would still exist. *Here:* the drill runs against a locally built
      archive, which proves the logic and not the transfer.

**Acceptance — the drill checks the invariant, not the volume:**

- [ ] Schema version and row counts against the **manifest sealed inside that archive**.
- [ ] **Every `item` row resolves to *its own* token in the same archive**, by salted
      fingerprint — never tokens, never in a log. Matching row counts prove the database
      arrived and say nothing about whether it arrived paired with the right token
      generation.
- [ ] **Required negative test: build an archive, swap two `access_token`s between two
      Items, and the drill must FAIL.** Orphan tokens are reported and are not a failure.
- [ ] **The drill runs on a machine with no network path to the VPS at all** — the
      *observed* run is **`03a-live`'s**; the structure it demands is built and tested here.
      If it needs the VPS, this criterion is not implemented whatever the code says. That
      splits the drill: *verify* (decrypt, restore, manifest, token reconcile, the §9.3a `seq`
      replay) completes **offline** and produces a verdict; *report* (`record-drill`)
      needs the VPS and, when it fails, the verdict is **kept locally and re-sent next
      run**.
- [ ] **A drill that treats "could not reach the VPS" as a drill failure is wrong** — it
      would go red every time the laptop is offline, and an alarm that cries wolf on a
      sleeping laptop is one the owner learns to ignore. Under-reporting is the correct
      failure direction.

**Acceptance — the canary and its rate limit (issue #9):**

- [ ] `build-probe` takes **no arguments**, writes **one fixed path** overwritten in
      place, is **single-flight** via non-blocking `flock`, and **no-ops within 60 s** of
      the last probe.
- [ ] It returns `(probe_generation, outcome: built | reused)` — a **VPS-local counter**.
      The canary proceeds **only on `built`**, otherwise waits out the cooldown and
      retries. The pulled archive carries `probe_generation` in its manifest so the canary
      asserts it verified the generation **its own build** produced.
- [ ] **Set the Mac's clock an hour fast and an hour slow; assert the verdict is unchanged
      in both directions.** A clock dependency that no test skews is how the previous rule
      passed review.
- [ ] **Burst test:** fifty sequential and ten concurrent `build-probe` → at most one build
      per minute, exactly one file, **zero** `backup_archive` rows, non-zero refusal count.
- [ ] Refusals are **counted and surfaced by `doctor`**, never silently absorbed.
- [ ] **Negative tests, dispatcher-level:** invoked directly with `SSH_ORIGINAL_COMMAND`
      set to `bash`, `networth show`, `build-archive current` and a mangled `archive_id`,
      the dispatcher must refuse **each**. A narrowing is only real if the removed verb is
      tested absent. The same list **over the wire**, plus the bare `ssh` that must not
      yield a shell, is **`03a-live`'s** — the bare-`ssh` case is a property of the
      `authorized_keys` line, not of this program, and cannot be tested here at all.

**Acceptance — the restore must resume publication** (§9.3a, issue #8). Assert these
**five cases separately**; cases 2–4 carry the weight, because a change making acceptance
unconditional passes case 1 and fails them:

- [ ] (1) restore + re-pair → first payload of the new pairing **accepted**
- [ ] (2) within that pairing, a lower `seq` **refused**
- [ ] (3) a pre-restore envelope (old `pairing_id`) **refused** — it does not decrypt
- [ ] (4) a **rollback** with no re-pair **refused**
- [ ] (5) the **same archive restored twice**, paired separately, works both times

**Acceptance — three ordinary-timeline behaviours that are criteria, not detail:**

- [ ] A verified pull whose write-back fails leaves `pulled_verified_at` `NULL` — it
      under-reports, which is the correct direction for this fact to fail.
- [ ] **The puller re-records whenever it holds a verified archive whose row is still
      `NULL`, transfer or no transfer.** A puller that skips because it already has the
      newest file would never retry that write-back and `doctor` would under-report
      forever.
- [ ] The puller is a **`KeepAlive` LaunchAgent, never `StartInterval`** — launchd defers
      interval timers on battery, and the owner's standing rule is that things work on
      battery with no plug-in prompt and no battery guard. The unit shape is checked here;
      **the pull observed while on battery is `03a-live`'s acceptance test**, because it
      pulls over the restricted key.
- [ ] **Every pull run records the power source it ran under**, read from the machine at
      run time, not inferred. Without it, `03a-live`'s battery criterion can only be met by
      a person happening to watch the right run, which is not a criterion anybody can
      execute — the same defect this board keeps finding in acceptance text.
- [ ] **The LaunchAgent installs in one command** the owner can run and re-run — it writes
      the plist, loads it, and prints what it did. §19 step 1c item 2 is *his* step
      (`03a-live`), so the thing this task hands him is a command, never a plist to
      hand-write into `~/Library/LaunchAgents/` and a `launchctl` incantation to get right.
      Re-running it after a failed load must converge, not stack a second copy.

**Must not:**

- The probe must never write a `backup_archive` row, and `link.sh`'s canary probe is built
  to a **distinct path** and deleted after it decrypts. A rehearsal that leaves a record
  indistinguishable from a real backup is this project's own failure mode, self-inflicted.
- **`build-archive current` is not on the restricted key.** The timer builds the real
  archive; `link.sh`'s post-exchange build uses the interactive key, where a human is
  present.
- **The backup is opportunistic, never daily**, and no acceptance criterion may imply
  otherwise. `doctor` and the app report `last_successful_backup` = **when a verified copy
  actually landed** — never `built_at`, never "the schedule fired".
- Bound the probe's **rate**, never its **fidelity**. It stays a real `VACUUM INTO` of the
  real database, or the canary stops testing the backup.
- Stamp `pulled_by` with the Mac's **full** tailnet name. Four Airs here differ only by
  suffix.

**Hard gate on task `08`:** a lost `access_token` cannot be recovered and strands a
permanent Item slot (**F2**, **F2a**, **F6**). This cannot wait for Phase 5. **The gate is
`03a-live`, not this task** — `08` spends the irreversible resource, so what has to exist
before it runs is a backup that was observed to work, not one that passes its own tests.

### 00a — Generate the constrained backup keypair; pin its `command=` — **codex**

**What to build.** The thing `00b` is waiting for: one `networth-backup-ssh` keypair, and
one finished `authorized_keys` line the owner pastes without editing.

**It sits here, after `03a`, because the ordering runs the other way from how the board
used to read it.** `00a` was numbered as a Phase 0 owner gate, so `03a` depended on it —
but the restriction it installs is `command="/usr/local/lib/networth/backup-ssh-dispatch"`,
and the dispatcher, its four allow-listed verbs and its argument patterns are all defined
by `03a`. `00a` could not be written until `03a` existed, and `03a` was marked blocked on
`00a`: a cycle, sitting on the board labelled as waiting for the owner.

**Acceptance:**

- [ ] The private key is generated **on `zelengs-macbook-air-2`** and written to
      `~/agents/secrets/networth-backup-ssh.key`, mode `0600`. It is the puller's key and
      it belongs to the machine that pulls; it never exists on the VPS, and never in this
      repository (`AGENTS.md` rule 1).
- [ ] The output handed to the owner is **one line**, complete with its
      `restrict,command="/usr/local/lib/networth/backup-ssh-dispatch"` prefix. Not a
      procedure, not a key plus instructions to prepend something.
- [ ] The `command=` string matches the dispatcher path `03a` actually installs, checked
      against `03a`'s implementation rather than against this sentence.
- [ ] The line is checked **as text**, here: it begins with `restrict`, it carries the
      `command=`, and the command is the dispatcher path — asserted against `03a`'s
      installed path, not against this sentence. That the key **cannot obtain a shell** is
      the property the two-key split exists for and it is asserted rather than assumed —
      but it is asserted in **`03a-live`**, by a real `ssh` against the installed line.
      This task cannot make that check: nothing is installed yet when it runs, and a
      generator that tested its own output by pasting it would be doing `00b`'s job on the
      owner's host.

**Must not:**

- **Emit an unrestricted key "for now".** The owner's instruction, 2026-09-01: an
  unrestricted key installed today and restricted later inverts the security property
  outright, because a compromised laptop would hold shell access to the host with the Plaid
  master credential for the whole window. One step, already constrained.
- Ask the owner for a password, or install anything on the VPS on his behalf (§15.1).

### 03a-live — `03a`'s acceptance over the installed restricted key — **codex** (the wire and the records) / **owner** (runs §19 step 1c)

**What to do.** No new component. This is the half of `03a` that is a fact about the
running system rather than about our code, and it can only be executed after the owner has
pasted `00a`'s line (`00b`) onto a host that has the service account (`28`). It exists as
its own row because the alternative — leaving these criteria inside `03a` — is the cycle
this board has now had twice: `03a` blocked on a key that `00a` cannot generate until `03a`
is done.

**Deps:** `03a` (the code), `00b` (the installed key and the escrowed backup key), and
through `00b`, `28` (the service user the key is installed under).

**Who executes what, because three of these criteria are `DESIGN.md` §19 steps** *(added
2026-09-01; this row previously named codex alone, which told an agent to perform step 1c
while §19's preamble says agents never perform it)*:

| Act | Who | Why |
|---|---|---|
| Install the puller LaunchAgent; confirm a pull on battery | **owner** | §19 step 1c item 2 |
| `networth backup attest-key` | **owner** | §19 step 1c item 3 — it records *his* confirmation that he holds an escrow copy. An agent running it writes down a fact that did not happen |
| The restore drill with the VPS unreachable | **owner** | §19 step 1c items 4/4a |
| Everything over the wire, and every check that a fact was **recorded** | **codex** | not in §19; it is `ssh` and `sqlite`, and it touches neither the host's config nor a key |

**His half is two visits, not one, and the gap between them is a wait nobody can shorten.**
Installing the puller comes first; the offline drill restores the archive **as pulled**, so
it cannot run until a pull has actually landed, and the battery run has to wait for the
laptop to be on battery — which happens on its own. `attest-key` is the only one of the
three that can be done at either visit. Say this to him when handing the task over; a
runbook step that silently contains a wait reads as a stall.

**The owner installs the puller even though agents administer this machine.** Agents
installed the ticker LaunchAgents on `zelengs-macbook-air-2` themselves, so this one is
technically ours to install too — and it is still his, because §19 is normative and the
owner closed `DESIGN.md` to revision on 2026-08-31. A board edit is not the instrument for
moving a runbook step. If it should move, it moves as a design issue: filed as
**issue #30**, `during-implementation`, so the disagreement is tracked rather than
resolved by whoever is editing this file. **What `03a` owes him for this is that it is one
command** — see `03a`'s installer criterion. He must never be asked to hand-write a plist.

**Acceptance — each one is an observation on the live host, not a test double:**

- [ ] **codex — The constrained key cannot obtain a shell.** A bare `ssh` with that key
      returns no shell, and `ssh … bash`, `ssh … 'networth show'`,
      `ssh … 'build-archive current'` and a mangled `archive_id` are each refused over the
      wire. The dispatcher-level versions already passed in `03a`; this proves the
      `authorized_keys` line, which is a different artifact and the one that actually
      protects the host.
- [ ] **owner, then codex — A pull observed while on battery**, by the `KeepAlive`
      LaunchAgent, over this key — not a manual run, and not on power. This is the
      criterion the owner's standing battery rule turns into a real check. **The owner's
      part ends when the puller is installed and has run**; codex's part is reading it off
      the record, because `03a`'s puller stamps the power source on every run. Nobody has
      to be watching at the moment it happens, and nobody is asked to unplug on cue — a
      criterion that needs a person present at 3 a.m. is not a criterion.
- [ ] **owner — `03a` criterion (3): the offline drill**, on `zelengs-macbook-air-2`
      (`100.96.163.67`), against the archive **as pulled** into its own destination
      directory, with no network path to the VPS. Verdict produced offline; `record-drill`
      re-sent on a later run — **codex** verifies that re-send landed. It is his and not
      automated because it needs this Mac's real path to the VPS severed: an agent that
      runs `tailscale down`, then dies before bringing it back, leaves the machine off the
      tailnet, which silently breaks the backup this drill exists to prove *and* both
      agents' route to the host.
- [ ] **owner, then codex — `03a` criterion (2): `networth backup attest-key`** records
      `key_escrow_confirmed_at` against the key the owner actually escrowed in `00b`. He
      runs it; codex checks the column is non-`NULL` before this row is called done.
- [ ] **codex — A verified pull writes back**, and a verified pull whose write-back fails
      leaves `pulled_verified_at` `NULL` and re-records on the next run — observed here
      over the real transport, having been proven against a fake one in `03a`.

**Must not:**

- **Fix code here.** A failure in this task is a defect in `03a` (or in `00a`'s line); the
  repair lands there and this task re-runs. A task whose acceptance is "observe the system"
  must not become the place where the system quietly changes.
- Ask the owner to re-paste anything to make a check pass. If the installed line is wrong,
  `00a` produced a wrong line — say so, regenerate, and hand him one corrected line.
- **Perform his half for him** — install the LaunchAgent, run `attest-key`, or take this
  Mac off the tailnet to produce the offline verdict. Blocked waiting on the owner is the
  correct state for this row to sit in; an agent-produced `key_escrow_confirmed_at` is
  worse than a `NULL` one, because `08`'s gate then reads as satisfied by a backup nobody
  can decrypt.

### 06 — Sandbox end-to-end rehearsal of the Link flow — **claude**

**What to build.** A complete Link → exchange → fetch cycle against Plaid **Sandbox**.

**Must pass before any Production Link.** Sandbox is free and unlimited; Production slots
are permanent and are spent by a successful **Link**, not by the exchange (**F2a**) —
which is exactly why the rehearsal is worth doing properly, since Sandbox is the only place
that boundary can be walked into without cost.

**Normative:** §16, §8, §19 step 3.3.

**Depends on `00` for the dashboard credentials** — a *different* dependency from O2's
answer. The rehearsal is worth doing whatever O2 says; it needs only that the account
exists.

**Acceptance:**

- [ ] A Sandbox Link completes with `user_good`/`pass_good`, the `public_token` is
      exchanged, and holdings and balances are fetched.
- [ ] **The fetched response is inspected for the fields net worth actually needs**, and
      what is present is recorded in `DESIGN.md` as an observation. This is one of the
      empirical questions no document could answer.
- [ ] `NETWORTH_ENV` selects the Sandbox credential file, items file and **database**
      together; a rehearsal **physically cannot** write into the Production history.
- [ ] Starting with a Sandbox credential in a file labelled production is a **startup
      failure**, not a run nobody questions.

**Blocked on the owner for one thing, and it is now its own row (`00c`) so he can do it
without waiting for `05`/`05a`:** the Sandbox secret installed at
`/etc/networth/plaid-sandbox.env` (§15, runbook step 3.3), same mode and same
owner-installs-it rule as every other credential. Plaid issues **one `client_id` per team
and a separate secret per environment** — the two files differ in their **secret**, not in
both values.

**Must not:** touch Production. No agent sees either secret.

### 06a — Prove F7 in Sandbox and measure the four unknowns — **claude** builds; **owner** runs the Mac half of (iv)

**A hard gate on `08` and on `07a`. If `06a` does not pass, `08` does not run.** There is
no degraded mode and no fallback: Plaid states that in Hosted Link *"there is no frontend
integration required (or possible)"*, and `completion_redirect_uri` carries no token, so
**nothing ever reaches the owner's browser to paste**. With webhooks out of v0,
`/link/token/get` is the only path in existence. A mode chosen *after* Link completes
cannot help — by **F2a** the slot is already spent.

**What is already measured** (2026-08-31, live): Hosted Link mints on this Trial account
(HTTP 200 with a `hosted_link_url`), `/link/token/get` is callable, and the hosted token's
30-minute lifetime was observed exactly. **What could not be measured is the one thing that
matters — a *completed* session's `public_token`** — because observing that in Production
means completing a real Link and spending a lifetime slot. So it is proven in Sandbox.

**Normative:** `DESIGN.md` **F7**, §16 ("The Link flow needs no hosted page at all"), §8.
Issues **#3, #4, #5**, **#13**, **#15**.

**Acceptance — prove F7:**

- [ ] Complete a Sandbox Hosted Link session with `user_good`/`pass_good`, poll
      `/link/token/get`, assert `link_sessions[].results.item_add_results[].public_token`
      is present, exchange it, and assert the resulting `access_token` works.
- [ ] Assert the **negative shape**: **before** completion the response contains **no
      `link_sessions` key at all**, so the poller's "not ready" branch is exercised on the
      real API rather than on a fixture someone guessed.

**Acceptance — the four measurements. Record each as a measurement whatever the result:**

- [ ] **(i) The two clocks (issue #3).** Complete a session, **wait past 30 minutes**, then
      retrieve and attempt the exchange. If the exchange fails, the 30-minute deadline is
      confirmed and nothing changes. If it succeeds, that is the **only** evidence that may
      widen the window, and `DESIGN.md` must be updated with the observation rather than
      with Plaid's phrasing. **Until this runs, 30 minutes is operative everywhere.**
- [ ] **(ii) Duplicate exchange (issue #4).** Exchange one `public_token` twice. Record the
      error code and **whether the original `access_token` is still usable**. `07a`'s
      `EXCHANGE_UNCERTAIN` handling is written against this behaviour, so a guess here
      becomes a wrong recovery procedure at the moment a slot is burning.
- [ ] **(iii) The four crash boundaries, not one (issues #5 and #15).** Inject a failure at
      each of **before send**, **after send / before response**, **after response / before
      `fsync`**, and **after `fsync` / before the DB commit**, restart, and attempt
      recovery. Assert a **distinct, honest** outcome for each. The fourth is the one rev 18
      got wrong: the credential is already durable there, so the correct outcome is that
      recovery **completes the local transaction without a second exchange** — classifying
      it `EXCHANGE_UNCERTAIN` is a false report of a lost slot. Only the third boundary is
      genuinely irreducible, and issue #5's window stays explicit.
- [ ] **(iv) Can a *different host* finish the flow at all? (issue #13).** Mint the
      `link_token` and complete a Sandbox session **on the VPS**, then call
      `/link/token/get` and exchange the `public_token` **from
      `zelengs-macbook-air-2`** — a different machine, same Plaid credentials, the VPS
      taking no part. Record whether retrieval and exchange both succeed.
      **`DESIGN.md` §19 step 2a and all of `07b` assume this works and nothing has tested
      it.** If it fails, the lost-VPS recovery does not exist and `07b` must be redesigned
      before `08` — which is why this is measured here, in the task that precedes both,
      rather than discovered inside the emergency it was written for.

**How (iv) is actually executed, because until this revision it could not be.** The Mac
half needs `client_id` and the Sandbox secret to call Plaid at all, and this project has
exactly one sanctioned Sandbox credential: `/etc/networth/plaid-sandbox.env` **on the
VPS**, which no agent may read. A Claude session on `zelengs-macbook-air-2` therefore had
no way to run this measurement without either involving the VPS — which destroys what is
being measured — or inventing a second credential location, which §15 forbids. The task
asserted a measurement with no executable path.

The resolution is not a new secret location. It is that **(iv)'s Mac half is run by the
owner, through the same TTY prompt `07b` will use in the real emergency** (`DESIGN.md`
§19 step 2a: *"It will prompt you for `client_id` and the production secret… that prompt
is the only manual part, and it exists because this Mac must not store the client
secret"*). That makes the measurement a rehearsal of the actual recovery mechanism, prompt
included, instead of a synthetic stand-in for it.

Concretely, and this is part of the deliverable:

- [ ] Claude builds and runs the **VPS half** (mint + complete the Sandbox session) — an
      agent may do this because the process on the VPS reads the file; the agent never sees
      its contents.
- [ ] Claude writes the **Mac half as one pre-staged command** that reads `client_id` and
      the **Sandbox** secret from a TTY with `read -rs`, never echoes them, never writes
      them to disk, never puts them in `argv` (so they stay out of `ps`), and leaves no
      shell-history entry. Run with `NETWORTH_ENV=sandbox`.
      **This command is the seed of `scripts/link-recover.sh`, not a copy of it** — `07b`
      depends on this task and extends *this* prompt and call path with the durable sink,
      the fencing precondition and the crash injection. Writing a throwaway here and a
      second prompt there would mean the emergency runs code this measurement never
      touched, which is the whole thing (iv) exists to prevent.
- [ ] The **owner runs that one command** and reports only the outcome — succeeded or
      failed, and the error code if it failed. **He is never asked to paste a secret
      anywhere an agent can read** (`AGENTS.md` rule 1, `DESIGN.md` §19 step 1 item 4).
- [ ] The command **refuses to run with `NETWORTH_ENV=production`**. A prompt that accepts
      the Production secret on this machine, during a measurement, is the one way this
      criterion could cost something real.

**Sequencing note:** (i)–(iii) are fully agent-run and must not wait on the owner. Only
(iv) has an owner step, and it is one command at the end. If the owner is unavailable,
(i)–(iii) still land; `07a` is gated on (ii) and (iii), and only `07b` and `08` are gated
on (iv).

**Must not:**

- Do not run any of this in Production. That is the whole point of the task.
- **Do not copy the Sandbox secret to the Mac** to make (iv) agent-runnable. A second
  credential location is not sanctioned by §15, and the copy would rehearse a path that
  does not exist in the emergency this measurement exists to validate.
- Do not ask the owner to paste a secret into a chat, a file, a log or a PR — not even a
  Sandbox one. The habit is the control.

---

## Phase 2 — linking (the only phase that spends the scarce resource)

### 07a — Automatic `public_token` retrieval + the `link_flow` state machine — **codex**

**What to build.** Mint the link token with `hosted_link`; poll `/link/token/get`
**starting immediately, without waiting for the owner to report anything** — the owner is
not a step in this loop; read `results.item_add_results[].public_token`; exchange it.

Retrieval needs **no inbound route**. `/link/token/get` is an **outbound** call from the
VPS, authenticated by the `link_token` the VPS itself minted — no Funnel, no public
surface, no browser cooperation.

**Normative:** `DESIGN.md` **F7**, **F2a**, §16, §8. Issues **#7**, #3, #4, #5, **#14**,
**#15**, **#16**.

**Builds on `03` and `05a`, and both are now declared.** Every SQL criterion below
transitions rows in the `link_flow` table `03` creates, and the `access_token` is written
through `05a`'s `TokenStore`. The first revision of this file listed only `05` and `06a`,
so the schema this task manipulates was created by nothing upstream of it — a graph that
would have sent codex to write migrations inside a task that does not own them.

**Three properties measured on the live account 2026-08-31 that the implementation must
respect:**

- `link_sessions` is **absent from the response entirely** — not an empty array — until a
  session completes. A poller treating a missing key as an error breaks on every poll
  before completion.
- The hosted token's lifetime is 30 minutes, observed exactly.
- The pre-completion response is a **documented shape, not an error**.

**Acceptance:**

- [ ] Ten `link_flow` states, response-driven: `started_at`/`finished_at` are **observed
      from the API**, never stamped at mint time. Both deadlines derive from `finished_at`
      and are `NULL` — *unknown*, never *passed* — until the session finishes.
- [ ] **Only `TOKEN_EXPIRED` and `EXCHANGE_UNCERTAIN` count against the Item budget.** A
      URL never opened, or a session exited, spends **no slot** (**F2a**) and must not be
      reported as one. `URL_EXPIRED` is a distinct, no-slot-spent fact.
- [ ] `ABANDONED` has a reachable definition, and a test reaches it.
- [ ] Entry to `EXCHANGING` is a **conditional** `UPDATE … WHERE
      state='SUCCESS_PENDING_EXCHANGE'`; a worker changing **0 rows does not call Plaid**.
      **Scope it honestly in the code comment and in any doc this task writes: this
      serialises workers sharing *this* database file.** It does not reach `07b`, which runs
      on another host against another file precisely because this one is gone. Cross-host
      at-most-once is Plaid's single-use `public_token` plus `07b`'s power-off precondition,
      not this `UPDATE`.
- [ ] `EXCHANGE_UNCERTAIN` is a real terminal outcome, not a retry loop against a
      single-use token.
- [ ] The `access_token` is written through `TokenStore` **before** the `item` row.
- [ ] **A stale `EXCHANGING` claim reconciles `TokenStore` before it is classified** (issue
      #15). Order: look up durable material for this `flow_id` first; if it exists, finish
      the `item` + `link_flow` transaction from it and **do not call Plaid again**; only if
      it does not exist may the row become `EXCHANGE_UNCERTAIN`. Rev 18 collapsed every
      stale claim into the terminal state, which reports a slot as lost while its
      credential is sitting on disk. A test restarts the worker at that exact boundary and
      asserts the flow reaches `EXCHANGED` with **zero** additional Plaid calls.
- [ ] **Identifiers are captured when they are visible, not re-derived later** (issue #14):
      `link_session_id` on the first `/link/token/get` response that carries it, `item_id`
      and `request_id` the moment any exchange response reaches the process, each written
      before any later step can lose them. **`/link/token/get` does not return `item_id`** —
      a test asserts the support path never claims to have one it was not given.
- [ ] **The VPS-side `link_token` reaper exists and is this task's** (issue #16).
      `SESSION_EXITED`, `URL_EXPIRED` and `ABANDONED` spend no slot and are reaped
      **immediately**; `TOKEN_EXPIRED` and `EXCHANGE_UNCERTAIN` retain only through the
      diagnostics window and are then reaped. Deletion order stays **material first, then
      `secret_ref`**. The reaper is **idempotent across all three partial states**
      (material+ref, no-material+ref, material+no-ref) and a crash injected between the two
      deletions converges on the next run. Without this, every terminal flow leaves
      credential material on the VPS forever — §15 defines only the Mac-side reaper.
- [ ] Behaviour matches what `06a` **measured** for duplicate exchange and the crash
      window. Where the measurement contradicts this design, the design changes.

**Must not:**

- Do not run against Production. This task is built and tested entirely in Sandbox; `08` is
  where it first meets a real institution.
- Do not clear `secret_ref` before deleting the material — that is the invisible orphan
  issue #11 exists to prevent, and the reaper is the second place it can happen.
- Do not retry Plaid before the `TokenStore` reconcile, and do not send a post-`fsync` case
  to support as though the credential were lost (issue #15).

**Note on the assignment:** this implements a state machine claude authored in design rev
18 and which no one reviewed (issue #7). It is assigned to codex deliberately, so the
first person to walk the mechanism is not its author. See the split rationale.

### 07b — `scripts/link-recover.sh`, the lost-VPS exchange — **claude**

**What to build.** The script `DESIGN.md` §19 step 2a already tells the owner to run and
that no task built (issue #13). The scenario: a Production Link has **succeeded** — the
lifetime slot is spent (**F2a**) — and the VPS is lost before the exchange. The owner runs
this on `zelengs-macbook-air-2` (`100.96.163.67`) within the **30-minute** `public_token`
lifetime. It calls `/link/token/get`, exchanges the one-time `public_token`, and stores the
result.

**The defect this task exists to close is the last step.** The VPS and its `TokenStore` are
gone, and §15's Mac inventory holds the pending `link_token` record and defines **no
access-token store**. As written, the procedure consumes the one-time token, receives a
long-lived `access_token`, and leaves it in a process with nowhere durable to put it — one
laptop crash from recreating exactly the permanent slot loss it exists to prevent.

**Normative:** §19 step 2a, §15 (Mac inventory), §14a.1, **F2a**. Issue **#13**; identifier
capture is issue **#14**.

**Acceptance:**

- [ ] **The durable destination is chosen and verified *before* the exchange is attempted,
      and the script refuses to exchange if it is not writable.** Either a replacement host
      with a ready `TokenStore`, or a Mac-side emergency artifact encrypted under the
      **already-escrowed** backup key (`00b`) with a restore path into a real `TokenStore`.
      Verifying the sink after spending the token repeats the rev-17 mistake: a fallback
      chosen after the irreversible step is not a fallback.
- [ ] `access_token` **and** `item_id` are written, `fsync`ed, **read back**, and only then
      is recovery reported successful. Also persist `link_session_id` and the exchange
      `request_id` (issue #14) — in this scenario the support ticket is the fallback.
- [ ] Crash injection **after the exchange response and before, during, and after** the
      emergency write; each leaves a state the next run can classify correctly.
- [ ] The full path is rehearsed end-to-end in Sandbox **with the VPS `TokenStore`
      unavailable**, and the recovered token is proven restorable and usable. `06a`
      measurement (iv) has already established that a different host *can* retrieve and
      exchange; this criterion is the rehearsal of the whole script, crash injection
      included. **A recovery procedure that has never been executed is a paragraph** — and
      this one would otherwise execute for the first time during the emergency.
- [ ] **A fencing precondition that is real, because the shared claim is not.** An earlier
      revision of this entry required the exchange to happen "under the same conditional
      claim `07a` uses." It cannot. That claim is a conditional `UPDATE` against the VPS's
      `link_flow` row, and this script runs precisely when that host and that database are
      unreachable; a restored copy on a replacement host is a **different** SQLite file, and
      two files can both win. What the local claim genuinely buys is serialisation of two
      runs *on this host* — keep it for that, and stop calling it cross-host safety.
      The fence is: **before the exchange, the script requires evidence that the old VPS
      worker is not running** — the owner powers off or destroys the instance in the
      provider control plane and the script takes an explicit typed confirmation naming
      that instance, recorded with the recovery artifact. **Unreachable is not off**: a host
      that fails a ping can still be mid-`/link/token/get`. State plainly in the
      owner-facing text that this host is also his exit node (§15.1), so powering it off is
      a real decision and not a formality.
- [ ] **The losing branch is classified from a measurement, not a guess.** The
      `public_token` is single-use, so Plaid — not our database — is what actually enforces
      at-most-once on the wire. If both hosts reach the API, one exchange fails. `06a` (ii)
      measured that error code **and whether the first `access_token` survives**; this
      script's duplicate-exchange branch is written against that measurement. Losing the
      race is **not** a stranded slot — it means the other host holds the credential — and
      reporting it as a loss would send the owner to Plaid support over a working Item.
- [ ] **The uncertain case is tested: the old VPS comes back after recovery exchanged.**
      Assert the outcome is classified honestly, that `TokenStore` ends with one usable
      credential rather than a silently preferred one, and that the budget (`26a`) counts
      **one** Item and not two.
- [ ] The owner-facing text states **30 minutes**, and the script is pre-staged as one
      command — this is a minutes procedure, not a six-hour one.
- [ ] **The Plaid client credential comes from the owner at a TTY, never from this Mac's
      disk.** §19 step 2a already specifies the prompt and §15 already says why: this
      machine must not store the client secret. **Extend the command `06a` (iv) built and
      the owner already ran once; do not write a second prompt.** That is the reason `06a`
      is upstream of this task rather than merely adjacent to it.

**Must not:**

- Do not print the `access_token`, pass it in `argv`, write it as plaintext anywhere on the
  Mac, or ask the owner to paste it somewhere an agent can read it (`AGENTS.md` rule 1).
- Do not invent a new key. The emergency artifact uses the escrowed backup key, which is
  why this task depends on `03a` and on the key reaching escrow in `00b`.
- **Do not present the local conditional claim as protection against the old VPS.** A lock
  in a database the other host cannot open is a comment, and one that reads like a
  guarantee is worse than none — it is the failure mode where the owner skips the power-off
  because the script implied it was covered.
- Do not touch Production while building it. It is written and tested against Sandbox; the
  first Production run is the owner's, in an emergency.

### 26a — Item budget core: the remaining-slot count — **claude**

**What to build.** One function, in one place, that answers *how many of the ten lifetime
Item slots are left* — and the counting rules behind it. No surfacing: no `doctor` output,
no app screen. Those are `26`.

**Why this is split out and why it sits before `08`.** `08` must report remaining slots
before it asks the owner to confirm, and `26` was declared the single source of that count
while itself depending on `08`. That is a cycle in meaning even where it is not one in the
graph: the first Production Link either duplicates the arithmetic `26` forbids duplicating,
or it waits for a task that waits for it. Splitting the count from its display resolves it
in the direction that keeps the single-source rule intact — **the core lands first, `08`
consumes it, and the surfaces consume it later, when they exist.**

**Normative:** §14, **F2**, **F2a**. Issue **#7**.

**Acceptance:**

- [ ] A single callable that returns the remaining count **and the facts behind it**, so a
      caller can explain the number rather than just print it (`AGENTS.md` rule 4 in
      spirit: no number without its provenance).
- [ ] Reads `link_flow` as the single source for spent-but-unusable slots — a `URL_MINTED`
      row whose URL expired is **not** counted. Only `TOKEN_EXPIRED` and
      `EXCHANGE_UNCERTAIN` are spent-and-unusable; `URL_EXPIRED`, `SESSION_EXITED` and
      `ABANDONED` spend nothing (**F2a**), and `07a` is where those states are produced.
- [ ] Reads `replaces_item_id` so a replacement's cost is visible.
- [ ] Counts an Item recovered by `07b` **once**, whichever host ended up holding the
      credential.
- [ ] Unit-tested against a fixture database. It has no dependency on a Production Item and
      must not acquire one.

**Must not:**

- Do not derive the count from a second source. Two sources means two answers — that rule
  is the reason for this split, not a casualty of it.
- Do not print anything or add a CLI verb. If you are formatting for a human, you are in
  `26`.

### 08 — `scripts/link.sh`, the owner-run Production Link — **claude** writes it, **owner** runs it

**This is the only task that spends a lifetime Item slot.** Every gate above it exists for
this moment.

**Where it runs:** on `zelengs-macbook-air-2` (`100.96.163.67`), because the backup is a
**pull** and only that machine can perform one. It reaches the VPS over SSH for every step
needing the client secret, which never leaves that host. The browser step is on the same
Mac. **Agents never run it.**

**Normative:** §19 (runbook), **F2a**, §14a.1, §16. Issues #3, #5, #7, **#13**, **#17**.

**Acceptance:**

- [ ] **Confirm institution and login *before* Link completes.** The slot is spent when
      Link succeeds (**F2a**), so a confirmation prompt before the *exchange* asks a
      question whose answer can no longer change anything.
- [ ] **A canary through the real backup path first** — build a probe archive on the VPS,
      pull it, decrypt it, delete it — and **refuse to mint a link token if any of it
      fails**. A full disk, an unreadable key or a missing destination directory all answer
      a reachability ping and all fail a real backup. The canary runs over the
      **restricted key and its dispatcher**, or it proves a route no backup takes.
- [ ] The owner-facing text states the **30-minute** deadline (issue #3). Six hours is
      session-data retention and is diagnostics only. Until `06a` measures otherwise, 30
      minutes is operative.
- [ ] **The completion worker is triggered through task `16`'s durable wake-up, never with
      a bare `systemctl start` on the shared sync unit** (issue #17). A plain `start`
      against an already-active service is silently discarded, and the service it targets
      also does health polling, full sync, quote refresh, publication and archive building —
      any one of which can be mid-flight when Link completes. The trigger this script uses
      must be one `16` guarantees survives an active run, and the script asserts the wake-up
      was **recorded**, not merely issued.
- [ ] **`07b` is on disk, rehearsed, and its durable sink is verified before the link token
      is minted** (issue #13). The lost-VPS window opens the instant Link succeeds; a
      recovery script that first runs during the emergency is not a recovery path.
- [ ] **Reports remaining slots before asking for confirmation, by calling `26a`** — not by
      counting Items itself. The single-source rule (`26a`) applies to its first consumer
      most of all: a second derivation written here is the one that runs while a lifetime
      slot is about to be spent.

**Must not:**

- Do not implement remove-and-relink. `/item/remove` does **not** free a slot (**F2**).
- Do not run before `06a` passes. There is no fallback path and no degraded mode.
- Do not print the 6-hour number as a deadline anywhere.
- Do not use `systemctl restart` on the general sync unit as the wake-up. Killing an
  in-flight sync to deliver a message is not a queue (issue #17).

### 09 — `scripts/relink.sh`, Link update mode — **claude**

**What to build.** The recovery path for `NEEDS_REAUTH`. **Consumes no slot.**

By **F8** this is also the fix for an Item disconnected by a missed
`INSTITUTION_MIGRATION` deadline: it lands in `ITEM_LOGIN_REQUIRED`, and sending **that
same Item** through update mode moves it to the new API and restores it healthy — no new
Item, no new slot. This script is what makes the webhook decision (issue #12) affordable:
without webhooks the migration is caught after the fact, and this is the repair.

**Normative:** §8.3, §8.4, **F8**, **F2**.

**Acceptance:**

- [ ] Works from `ITEM_LOGIN_REQUIRED` **arrived at any way**, not only from a credential
      change.
- [ ] A test asserts the Item ID is unchanged across a successful relink.
- [ ] Remaining-slot count is unchanged afterwards.

**Must not:** **the naive remove-and-relink must never be implemented** (**F2**). It looks
like the obvious fix and it permanently destroys a slot.

### 12b — Replacement-Item reconcile flow — **claude**

**What to build.** `scripts/reconcile.sh` per §8.5. New accounts start `NEW` and
**contribute nothing**; the owner confirms an old→new mapping; `lineage_id` carries history
across the seam.

**Normative:** §8.5, §7 (`lineage_id`).

**Acceptance:** tests must cover **the two silent disasters** — double-counting (old and
new both contributing) and a severed curve (history not carried across `lineage_id`).

**Must not:** auto-confirm a mapping. The owner confirms.

---

## Phase 3 — sync and the honesty machinery

### 10 — Item health poller — **claude reviews; codex builds**

**What to build.** Hourly `/item/get`. **This is the whole of Axis A's input** (§8.4), so
it carries I3 alone.

**Normative:** §8.2, §8.4, §8.1.

**Acceptance:**

- [ ] Every Item error state visible to `/item/get` is observed within an hour.
- [ ] Records `status.investments.last_successful_update`, which feeds the holdings source
      clock (§8.1).

**Must not:** wait for `PENDING_DISCONNECT` — it is invisible to `/item/get` (**F8**).

### 11 — `StalenessMachine`, two axes — **codex**

**This is the product.** Heavily unit-tested.

**Normative:** §8.1–§8.2, §9.2, §11.

**Acceptance — required cases:**

- [ ] A call that **succeeds while `source_as_of` never advances** — the frozen-number
      failure, and the reason this task exists.
- [ ] `UNKNOWN` freshness **never renders as fresh**.
- [ ] Weekends, holidays and half-days on the market-close clock.
- [ ] A carried-forward observation continues to age.
- [ ] **Axis B escalation:** merely behind its expectation window is `WAITING`
      (institutions post late constantly — alerting there burns the signal on noise). Item
      `HEALTHY` + source clock frozen for **five consecutive market days** becomes
      `FROZEN`, which is `ACTION_NEEDED` on screen *and* alerts.
- [ ] **`UNKNOWN` never escalates.** Under `balance_mode: cached` it is permanent — no
      re-link can conjure a clock — so it is a standing caveat, not a wait.

**Must not:** re-declare the five-day threshold. It is **read from the single definition in
§11**. Task 11 and task 15 each carrying their own copy is what made the display and the
runbook contradict each other in rev 2.

### 12 — Full sync: holdings + balances → observations — **codex**

**Normative:** §8.1, §7, **F5**.

**Acceptance:**

- [ ] Records `fetched_at` and `source_as_of` **separately**, plus which evidence produced
      the latter.
- [ ] Holdings take the **oldest** contributing `institution_price_*`.
- [ ] Balances use `/accounts/balance/get` under `balance_mode: realtime` (**F5**) and are
      `UNKNOWN` under `cached`.
- [ ] Sets `is_carried_forward` honestly, and **a carried-forward row never advances its
      source clock**.

**Must not:** infer a source clock when none is available. `UNKNOWN` is a correct answer.

### 13 — Manual assets: property revision log + share counts — **claude**

**What to build.** O4 is answered: **a revision log** (§12), defaulting to the purchase
price. `QuoteClient` reuses the quotes integration from the sibling project.

**Normative:** §12, §7.

**Acceptance:**

- [ ] **A revision applies from its own date forward and the curve behind it does not
      move.** The value used for a given day is the latest revision *as of that day*, never
      the latest revision outright.
- [ ] Mechanically a new append-only observation with its own `source_as_of` — **never an
      `UPDATE`**.
- [ ] **A test asserts that revaluing the property in 2026 leaves the 2024 points on the
      curve unchanged.**
- [ ] The quote carries its own `as_of`, and that `as_of` **is** the source clock.
- [ ] Ticker and quantities are configured at runtime, never hardcoded.

**Why that test passes** (and this constrains task `23`): `snapshot.total_net_worth_minor`
is a **stored** number, not a query re-evaluated at render time. "Recompute the curve from
observations" would reintroduce exactly the retroactive deformation §12 rules out.

**Must not:** hardcode a ticker, a quantity or a price.

### 14 — Snapshotter + net-worth computation — **codex**

**Normative:** §10, **I2**.

**Acceptance:**

- [ ] **A total cannot be constructed without its age state and staleness counts (I2) —
      enforced in the type, not by convention.** The total is a sum type carrying
      `(age_state, as_of)`.
- [ ] A caller cannot obtain a bare integer total. If it can, this task is not done.

**Must not:** provide a convenience accessor that strips the age. That accessor is the bug
this project exists to prevent.

### 15 — Alerts: payload-carried delivery — **codex**

**What to build.** §11. **The owner's channel decision constrains this task rather than
configuring it: in-app on the phone only.** He declined email and the agent-mailbox route,
and there is no Mac in the path. So alert state is **serialized into the payload**; the app
renders it; a cached payload carries the alerts that were true when published, **labelled
as of that time**.

**Four kinds:** `NEEDS_REAUTH`, `REVOKED`, **frozen data**, **pending reconciliation**.

**Normative:** §11, §9.

**Acceptance:**

- [ ] State is written **before** it travels, so a crash re-raises rather than losing the
      alert.
- [ ] The frozen-data threshold is **read from §11**, shared with task 11, **not
      re-declared here**.
- [ ] **The criterion this task must not fudge:** *publication overdue* **cannot reach the
      phone**, because the failure it reports is the failure of the channel it would
      travel over. It is task `22`'s `HOST_NOT_PUBLISHING`, detected phone-side. Do not
      add it here and call it delivered.

**Must not:** add an alert kind that watches a third party or a queue. Those were deleted
because there is nothing left for them to observe.

### 16 — systemd units + timer + due-ness engine + catch-up + live install — **codex**

**Normative:** §13, **F7**, **F2a**. Issues #3, **#10**, **#16**, **#17**.

**This task owns the units end to end: it defines them, installs them on the host `28`
provisioned, and verifies them running.** `28` prepares the base host and stops there.
Before this revision, `28` promised to install "whatever unit set `16` settles on" without
depending on `16`, and `16` required itself to be "installed and running" before `08`
without depending on `28` — so the install existed in two entries and belonged to neither,
and the verification that gates the only slot-spending task in the project had no owner.
**Depends on `28`.**

**Acceptance:**

- [ ] **The units are installed and observed running on the provisioned host, and that
      observation is what gates `08`** — `systemctl is-active` on each unit and the timer's
      next elapse, captured, not assumed. A unit file merged into the repo is not a unit
      that runs.

- [ ] Due-ness is computed from **stored state**, not from "did the timer fire" — the
      catch-up predicate survives downtime.
- [ ] Due on non-market days too.
- [ ] **The *complete pending Link* job:** due while a `link_flow` row is in a
      **non-terminal** state. It polls `/link/token/get`, advances the state **from the
      response** rather than from elapsed time, exchanges under a claim, and writes the
      `access_token` through `TokenStore` **before** the `item` row.
- [ ] **Boot-time catch-up is an acceptance criterion on this job specifically** (issue
      #10). The deadline is 30 minutes; a 40-minute reboot spans the entire token
      lifetime.
- [ ] Terminal transitions are **distinct facts**, not one `EXPIRED`: `URL_EXPIRED` (no
      slot spent), `TOKEN_EXPIRED` (slot stranded), `EXCHANGE_UNCERTAIN` (slot at risk).
- [ ] **The pending-Link wake-up cannot be lost behind an active sync run** (issue #17).
      `systemctl start` on an already-active service is discarded, and a timer elapsing
      while its unit is active does not queue a second run — so the design as merged can
      swallow the wake-up inside a long Plaid call, full sync or archive build, against a
      **30-minute** token. Pick one and state which: a **separate unit** for pending Link,
      guarded by the same conditional DB claim so it cannot double-exchange; or a **durable
      wake-up** in the existing service (a persisted request the run drains, plus a
      "re-check Link before exit" contract and a bounded max runtime).
- [ ] **A stated, tested upper bound from observed Link success to the first exchange
      attempt, comfortably inside 30 minutes.** The test starts a deliberately long
      sync/archive job, completes a Link while it runs, and asserts the exchange begins
      within the bound. A bound nobody measured is the merged design's position.
- [ ] **Catch-up after downtime is specified, not assumed.** `Persistent=true` resumes
      missed runs **only** for `OnCalendar=` timers — so either use `OnCalendar=`, or define
      an explicit boot service that scans stored state. A `Persistent=` line on a monotonic
      timer looks like catch-up and is not.
- [ ] **The VPS `link_token` reaper from `07a` is scheduled here** (issue #16), including
      the diagnostics-window expiry for `TOKEN_EXPIRED` / `EXCHANGE_UNCERTAIN`. `07a` owns
      the reconcile logic; this task owns the fact that it runs regularly and after a crash.
- [ ] **Two** units, not three — *unless* the first criterion is answered with a dedicated
      Link unit, in which case say so and **update `DESIGN.md` §13 in the same PR**. That
      count is a normative claim in the design; it exists to keep the deleted webhook
      receiver from returning, not to forbid a correct wake-up. Changing it deliberately is
      fine; leaving the document asserting a number the deployment contradicts is not.

**Why the Link job lives here and not in `link.sh`:** retrieval is the only path to a
`public_token`, and that deadline must not sit on a laptop whose lid closes. Catch-up after
downtime therefore matters to a **lifetime slot** on this job, not just to a curve.

**Must not:**

- Do not let task `08` be runnable before this task is installed and running (issue #10).
- Do not use `systemctl restart` on the general sync unit as the wake-up — it kills
  unrelated in-flight work and is still not a queue (issue #17).

### 27 — Vest-date nudge to re-confirm a share count — **claude**

**What to build.** A prompt to re-confirm a manual share count after a vest date. Manual
quantities drift silently — the same failure this project exists to prevent, arriving from
the manual side.

**Normative:** §12, §11.

**Acceptance:** the nudge is an alert of the existing four-kind taxonomy or a documented
fifth; it never silently changes a quantity.

**Must not:** auto-update a share count. The owner confirms.

---

## Phase 4 — getting the number onto the phone

### 17 — `NetWorthQuery` read layer — **codex**

**What to build.** Pure reads. The only surface a UI or a payload builder may touch.

**Normative:** §10, §7.

**Acceptance:** history joins on `lineage_id` so a re-link does not break the curve.

**Must not:** write anything. No side effects.

### 18 — CLI: `show` / `history` / `doctor` — **codex**

**What to build.** Pure reads, run over SSH — there is no other interface on this host.
First consumer; proves the seam before a phone exists.

**Normative:** §7, §14a.1, §11.

**Acceptance — `show`:**

- [ ] Must be able to print a total with **no date** without reaching for one. This is the
      first place task `14`'s tagged age is rendered.

**Acceptance — `doctor` prints:**

- [ ] Both clocks per account; Item states; remaining Item slots.
- [ ] Days since the last verified restore drill.
- [ ] **`last_successful_backup` — read from `backup_archive.pulled_verified_at`, never
      from `built_at` and never from the schedule.** The Mac is not always on and is the
      side that initiates, so this number growing silently is the **only** signal that it
      has stopped pulling. The VPS has no way to notice and must not pretend to.
- [ ] **`key_escrow_confirmed_at` labelled as the owner's own attestation**, not a verified
      fact (§14a.1).
- [ ] The age of the last successful publication.
- [ ] Probe-refusal counts (issue #9).
- [ ] `publish_epoch` as a restore-lineage **diagnostic** (issue #8).
- [ ] **The support-ready identifiers for any non-terminal or uncertain `link_flow`** (issue
      #14): `flow_id`, `link_session_id`, `item_id` **if the system was ever given one**,
      and the per-attempt `request_id`s. Each prints as *present* or *never observed* — a
      test loses an exchange response and asserts `doctor` still prints a usable ticket
      without claiming an `item_id` it does not have.
- [ ] **Overdue `link_token` material and dangling `secret_ref`s** (issue #16): material
      past its reap deadline, and references whose material is gone. Both are the visible
      output of the reaper's partial states; if `doctor` cannot show them, the ordering
      guarantee is untestable in production.

**Must not:** count the manual-paste fallback. It does not exist (**F7**). Do not report a
fact this host cannot observe — the two `doctor`s are **split by host** (issue #11).

### 19 — Payload schema + `Publisher` — **codex**

**What to build.** The host↔phone contract. Publishing is a **local SQLite transaction**.

**Normative:** §6.1, §6.3.1, §6.4, §9.3, **§9.3a**. Issue **#8**.

**Acceptance:**

- [ ] The payload carries `published_at`, `publish_interval_seconds`, `grace_seconds` and a
      monotonic `seq`.
- [ ] `schema_version`, `pairing_id`, `seq` and `published_at` go in the **AAD**, in the
      **envelope and canonical length-delimited AAD encoding of §6.1** — both ends must
      build those bytes identically or nothing decrypts.
- [ ] The payload carries the total's age as task `14`'s tagged `(age_state, as_of)`.
- [ ] `last_seq` is **pairing-scoped**. The epoch is **not** part of `seq` (issue #8);
      `publish_epoch` is a diagnostic only.
- [ ] The five §9.3a restore cases pass separately — see `03a`.

**Must not:** re-read the row you just wrote to confirm SQLite wrote it. That tests
SQLite. And do not re-add the epoch to `seq` without removing the pairing scope — two
mechanisms for one invariant is what produced the original contradiction.

### 20 — The daemon's one HTTP route + freshness monitoring — **codex**

**What to build.** `GET /snapshot` in `networth-serve`, **bound to the tailnet interface
only**, database **read-only**. Serves the active `published_envelope` row when its pairing
is `ACTIVE`, `404` otherwise, reassembling the §6.1 header from stored columns **without
re-encrypting**.

**Normative:** §16, §6.4, §6.3.1, §15.1.

**Acceptance — the bind test, which is the one mistake here that silently publishes a
private endpoint:**

- [ ] **Assert our listener's address**, not the absence of a string. "Not `0.0.0.0`" is
      passed by the public IPv4, passed by `[::]` while still serving the world, and
      passed by loopback-only while making the phone unable to connect at all.
- [ ] **The test is split: "our socket" + "a baseline".** Measured read-only on the live
      VPS, `sshd` listens on `0.0.0.0:22` **and** `[::]:22`, so a whole-table criterion
      fails before networth exists.
- [ ] **The node has two tailnet addresses, not one** — `TailscaleIPs` is an array and
      includes an IPv6 (`fd7a:115c:a1e0::1d37:f526`). "*The* node's Tailscale address" is
      ambiguous.
- [ ] `systemd-resolve` binds `127.0.0.53%lo`, an **interface-scoped** loopback a naive
      string match mishandles.

**Must not:** open any second port. Since rev 15 there is no public inbound service of any
kind.

### 19a — Pairing: `networth pair` / `revoke` — **codex**

**Normative:** §6.3, **§6.3.1**.

**What to build.** Rotation and revocation are **one local SQLite transaction** — new
pairing `ACTIVE`, old `revoked_at`, stored envelope dropped. No `PENDING` state, no
registration round-trip, no `UNCERTAIN` outcome, no suspend-publishing rule.

The QR carries the payload key, the `pairing_id` and the VPS's tailnet name — **no read
token**, because tailnet membership plus the payload key *are* the read credential.

**Acceptance:**

- [ ] `revoke` drops the served envelope **in the same transaction** that marks the pairing
      revoked, so a stolen phone cannot fetch again.
- [ ] `pair` prints the QR **only after** the transaction commits.
- [ ] **The CLI says plainly that ciphertext already cached on a stolen phone is beyond
      recall.** Revocation stops future fetches; it does not reach backwards.

**Must not:** promise atomicity you do not have, or add state to survive a network call
that no longer happens.

### 21 — Flutter app skeleton — **claude**

**What to build.** **Android only** (O6 answered) — no iOS branch, no second delivery
story. Read-only display; **holds no Plaid token and never calls Plaid**. Fetches through a
**fixture-backed seam** so it does not wait on `20` or `28`.

**Normative:** §8.1 (R3), §17, **I4**.

**Acceptance:**

- [ ] **I4: there is no intermediate state in which this app renders a bare headline.**
      That would be shippable and would be the exact lie the project refuses.
- [ ] **All three age states render:** `KNOWN` shows the date; `UNKNOWN` shows *"can't date
      this total — N of M accounts can't be dated"* and **no date anywhere near the
      headline**; `STATIC_ONLY` says so — which is the real state before the first Item is
      ever linked, not a theoretical one.
- [ ] **Fixtures include the mixed known/unknown case**, because that is the one where a
      plausible implementation quietly prints the oldest known date.

**Must not:** render a total without its age, at any point, even temporarily. Task `22`
deepens the treatment; it does not introduce it.

### 22 — Dual-staleness UI + alert surface + downgrade handling — **claude**

**With alerts in-app only, this is where the product either works or quietly fails.**

**Normative:** §9, §9.1, §9.2, §9.3, §11.

**Two things it owns outright:**

1. **An unhealthy state must be impossible to miss on open, and a stale total must never
   render as a normal number.** There is no email, no push and no Mac — an alert is seen
   when the owner opens the app and not before.
2. **`HOST_NOT_PUBLISHING` is how a host-side failure reaches him at all**, since the
   *publication overdue* alert cannot travel over the channel whose failure it reports
   (task `15`).

**Acceptance:**

- [ ] The two `COPY_STALE` reasons are an **exact predicate, not a vibe** (§9.1):
      `HOST_NOT_PUBLISHING` iff `last_fetch_success_at >= stale_after` **and**
      `last_fetch_attempt_at == last_fetch_success_at` **and** `last_fetch_seq ==
      last_seq`; `CANNOT_CHECK` otherwise.
- [ ] The app therefore persists **five distinct facts**: `last_fetch_attempt_at`,
      `last_fetch_success_at`, `last_fetch_error`, `last_fetch_seq`, `last_seq`.
- [ ] Clock-skew `COPY_UNKNOWN` is handled.
- [ ] Per-account "fresh" badges are **suppressed under a stale copy**.
- [ ] **I6 downgrade refusal** — the phone never accepts a lower `seq` within a pairing
      (§9.3).

**Must not:** collapse the two staleness dimensions into one indicator. They answer
different questions and a single badge answers neither.

### 23 — History curve — **claude**

**Normative:** §12, §10.

**Acceptance:**

- [ ] Dashed/hollow for `is_complete = FALSE`. **A gap in the record must look like a gap.**
- [ ] **Reads stored snapshot totals; never recomputes a past point from current
      observations.** With task `13`'s revision log that would silently redraw history
      every time the owner revalues the property — the deformation §12 exists to rule out.

**Must not:** recompute the curve. It is the obvious implementation and it is wrong.

### 24 — Release signing + APK delivery — **claude**

**Normative:** §17, §6.3, `AGENTS.md`.

**Acceptance:**

- [ ] Keystore **outside** the repo; **no secrets in the build**.
- [ ] **Signed from the first delivered build** — a debug→release signature change later
      forces an uninstall.
- [ ] Version bumped per `AGENTS.md` before each delivery; the desktop file name carries
      the new version.

**Depends on `22`** so a build that can collapse the two staleness dimensions — or bury an
alert on a design with no second channel — cannot be delivered, and on `20` so the
delivered app has a real transport.

**Must not:** deliver a debug-signed build.

### 26 — Remaining-slot surfacing: `doctor` and the app agree — **claude**

**What to build.** Display of `26a`'s count everywhere the owner looks: the `doctor`
subcommand (`18`) and the app (`22`). Running out of slots is invisible until it isn't
(**F2**), and a number nobody sees is not surfacing.

**Why it is here and not in Phase 2.** Its consumers are here. This task used to hold both
the arithmetic and the display and to depend on `08`, which needs the arithmetic — so it
was upstream and downstream of the same task. The count moved to `26a`, before `08`; what
is left is the presentation, and presentation lands with the surfaces that present it.

**Normative:** §14, **F2**, **F2a**. Issue **#7**.

**Acceptance:**

- [ ] `doctor` and the app **agree on the number**, because both call `26a` — verified by a
      test that changes the underlying state and asserts both surfaces move together, not
      by two implementations that happen to match on the day they were written.
- [ ] The number is shown with what it means: a remaining count of zero says the account is
      at its lifetime ceiling and that `/item/remove` will not free one (**F2**), rather
      than showing a bare `0`.
- [ ] A slot spent-but-unusable (`TOKEN_EXPIRED`, `EXCHANGE_UNCERTAIN`) is **visible as
      that**, not silently folded into "used".

**Must not:**

- Do not compute anything. If this task needs a rule about what counts, the rule belongs in
  `26a` and this task calls it. Two sources means two answers.
- Do not present the count without its provenance (`AGENTS.md` rule 4).

---

## Phase 5 — operations

### 28 — VPS provisioning + hardening — **claude** writes and checks it, **owner** runs §19 step 3.1

**Reassigned codex → claude on 2026-09-01**, in review of PR #32, to restore the
one-`READY`-root-per-agent rule in *Why the split is shaped this way*. This is a
load-balancing move, not a specialisation one; the work below is unchanged, but **which
half of it an agent may execute is not** — see the next paragraph, which was missing from
the first version of this reassignment and is the reason it was rejected in review.

**Who executes what, because two of the four acceptance criteria are `DESIGN.md` §19
step 3.1** *(added 2026-09-01; the reassignment first named claude alone, on the argument
that step 3.1.3 already prints the `sshd` change for the owner to apply. That covers one
`Must not`, not the owner half of the step — §19 step 3's preamble is "agents prepare
everything, the owner runs it", and criteria (2) and (4) are observations of that run)*:

| Act | Who | Why |
|---|---|---|
| Write the idempotent provisioning script; keep it off `PermitRootLogin`; keep `PLAID_ENV` out of the source — criteria **(1)** and **(3)** | **claude** | static facts about our code, checkable in the repo and in CI without touching the host |
| Execute the script on `tokyo-exit`, twice — §19 step 3.1 | **owner** | §19 preamble: agents never perform these. It changes SSH config, the firewall and account ownership on his exit node |
| Criteria **(2)** and **(4)** — the reported `chown`, and the `S1..S2` host-state diff | **owner runs, claude inspects and records** | the observation is of *his* run. He brings back two transcripts and three captures; claude takes the diffs and writes the result into this entry |

The same rule as `03a-live`: an agent may prepare, read back and record; the run itself is
his. **The line is host *state*, not the wire.** Read-only checks over SSH stay claude's —
the `id` re-check two paragraphs down is one, and so is running `scripts/host-state.sh`
against the host at any time — because they change no config, create no account and touch
no key. What no agent does is *run the provisioning script*, and that includes a
"rehearsal" pass: criterion (4) has no dry mode, it is two real runs, and the first one is
the one that edits `sshd`, the firewall and `/etc/networth/`.

**The three captures are inside his sequence, and that is a correction** *(2026-09-01, from
codex's review of PR #34)*. The version above split them "claude captures before and after,
the owner runs the script in between", which cannot produce `S1` at all: that capture has to
happen **between** his two runs, so an agent taking it would mean stopping the owner
mid-procedure and waiting for a session to wake up. §19 step 3.1 is therefore one chained
sequence he pastes once, with the read-only captures interleaved — which is only sound
because they are read-only, and `test_host_state_capture_changes_nothing` is what keeps
them that way. Claude's half is the diffing and the recording, on artifacts he brings back.

**Consequence to state rather than let someone discover: this row does not close without
him.** Claude's half is startable today and is real work — the script plus criteria (1) and
(3) — so `28` is a genuine `READY` root for the load-balancing purpose it was moved for. But
`16`, `20` and the owner's `00b` all consume a *provisioned host*, so they wait on the owner
executing step 3.1. It is deliberately **not** added to the "still outstanding" list of
owner actions above, by that section's own rule — *an owner row is a row he can act on
today* — because the script he would run does not exist yet. It becomes his the moment
claude's half merges, and the agent handing it over says so then.

**Handed over 2026-09-02.** Claude's half merged as `6e35ef3` (PR #34, approved by codex at
`b4810a7`), so criteria **(1)** and **(3)** are met and checked below; **(2)** and **(4)**
are now the owner's two runs. What he brings back is fixed and small: `provision-run-1.log`,
`provision-run-2.log`, and `host-state-{0,1,2}.txt`. Claude takes the two diffs, checks run
2 for `changed: 0`, compares the transcripts' `sha256` against
`~/networth-run/reviewed-commit.txt`, and writes the result into this entry.

**One defect was found in the handover itself, between the merge and the hand-off**, and it
is recorded here because it is the second time this row's *procedure* — not its script —
was the broken part: §19 step 3.1's six remote commands named no key, and
`zelengs-macbook-air-2` has no `~/.ssh/config`, no default identity file and an empty
`ssh-agent`, so the paste would have failed at its first `scp`. Rev 22 passes the step-1a
key explicitly and `tests/test_owner_runbook.py` fails a PR that drops it from even one of
the six commands. Nothing was handed to him before that landed.

That guard then took three review rounds of its own, and every finding was the same shape as
the defect it was written for: it checked that the *text* said the right thing rather than
what `ssh` would *do*. Round 2 kept every token it looked for and unpinned the sequence
(`IdentitiesOnly` is first-value-wins; identities accumulate); probing that fix found three
more routes it did not read at all; round 3 kept the tokens **and** their order, and moved
them past the position where each program stops reading options at all. Rev 23 stops
enumerating the bad spellings, fails closed on any option it does not model, and models that
boundary per program — `ssh` resumes after its destination, `scp` does not. Recorded here
because the row's lesson is now four-for-four: **on this task, every defect has been in the
procedure or its checks, and none in `provision-host.sh`.**

**Unblocked 2026-09-01, verified on the host rather than inferred from the board.** This
entry read `BLOCKED (00a)` because it needed the agent SSH key installed. It is installed,
and it is enough: over `~/agents/secrets/networth-vps.key` to `100.102.245.37`, `id`
returns `uid=0(root)`, `/etc/networth/` already exists as `drwx------` root-owned, and
`authorized_keys` holds two entries. The *backup* key `00a`/`00b` are about is a different
key that nothing here needs. Re-check with `id` before provisioning rather than trusting
this paragraph — it is a fact about a live host on a date, not a property of the design.

Note that the existing `/etc/networth/` is what acceptance criterion (2) below is for: the
`chown` has something to report on, so it must not be silent.

**What to build.** Idempotent provisioning of the **base host** — the owner's **existing**
Vultr host (`tokyo-exit`, `100.102.245.37`): key-only SSH (`PasswordAuthentication no`), a
firewall opening **only SSH**, unattended security upgrades, a **dedicated unprivileged
service user**, `/etc/networth/` with the right ownership and modes, and the Python
runtime.

**This task installs no application unit.** Earlier it said it would install "whatever unit
set task `16` settles on," which made it wait on `16` while `16` did not declare it — an
ordering nobody could execute, and the live verification that gates `08` belonged to
neither. **The boundary is now: `28` prepares the host; `16` owns the unit files, installs
them, and verifies them running.** `16` therefore depends on `28`, and because `08` already
depends on `16`, the Production Link is gated on a host that was provisioned *and* on units
someone watched start. Nothing here needs to know how many units there are.

**Normative:** §15.1, §13, §19 step 3. Issues **#6**, #17.

**What claude's half delivered, and what it deliberately did not.** Two files, both
standalone so the host never needs a checkout of this repository:

| File | Who may run it | What it does |
|---|---|---|
| `scripts/provision-host.sh` | **the owner only** | the whole of *What to build* above, comparing before it acts and ending in a `changed:` count |
| `scripts/host-state.sh` | anyone, including an agent | reads and prints exactly what the other script can change — no writes, no clock, no pids, so successive captures diff cleanly |

`tests/test_provision_script.py` pins criteria (1) and (3) as facts about the repository:
the only sshd setting the script can write is `PasswordAuthentication no`; every
`PermitRootLogin` mention is a comment, a read, or the proposal it prints; the set of files
it writes is two, neither under `/etc/networth/`; no `PLAID_ENV` value is assigned anywhere
in `networth/` or `scripts/`; no second port, no rule deletion, no unit, no password
prompt. These are shape checks on shell source and cannot prove runtime behaviour — what
they buy is that the edit which would break the owner's exit node fails a PR instead.

**Verified read-only on the host on 2026-09-01, before the script was written**, because
half of what this task was specified to change is already the way it should be, and a
script that "hardens" what is already hardened reports work it did not do:

| Fact on `tokyo-exit` | State found | What the script does about it |
|---|---|---|
| `PasswordAuthentication` | already `no` (a drop-in the owner installed) | verifies via `sshd -T`, writes nothing |
| `PermitRootLogin` | `prohibit-password` — already key-only | reports it; **proposes nothing**, because the only value §15.1 argues against is `yes` |
| `ufw` | active, deny incoming, `22/tcp` + `41641/udp` (Tailscale) | verifies `22/tcp`; leaves the Tailscale rule alone |
| unattended upgrades | installed, enabled, both periodic jobs on | verifies through `apt-config`, writes nothing |
| service user | **absent** | creates it, home `/var/lib/networth`, `nologin` |
| `/etc/networth/` | `root:root 700`, one credential file `600` | `chown`s both to the service user and **reports each one** — criterion (2) |
| `python3` | **3.14.4**; `python3-venv` **absent**, so `python3 -m venv` fails today | installs `python3-venv` and proves a venv can be built |
| public listeners | `sshd` on `0.0.0.0:22` and `[::]:22` — exactly §19 step 3.4's baseline | records them |

Two of those are findings rather than confirmations. **The one sudo account on the host has
zero authorized keys**, so §19 step 3.1's precondition for restricting root login does not
hold today — the script prints the key counts next to the proposal so that is visible at
the moment it matters. And **`python3` is 3.14 while CI runs 3.12** (issue **#33**); this
task installs the distribution interpreter on purpose, because one outside
`unattended-upgrades` on the host holding the master credential is the worse trade.

**How criterion (4) is measured, so it can fail.** Capture `scripts/host-state.sh` **three
times** — `S0` before the owner's first run, `S1` between the two runs, `S2` after the
second — and keep all three alongside both transcripts. The two diffs answer two different
questions:

| Diff | Expected | What it establishes |
|---|---|---|
| `S0..S1` | **non-empty**: the service user appears, `/etc/networth/` changes owner, `python3-venv` becomes installed — and nothing else | what provisioning did |
| `S1..S2` | **empty** | criterion (4): re-running changes nothing |

*(Corrected 2026-09-01, from codex's review of PR #34. The first version captured before run
1 and after run 2 — one diff, expected to be non-empty, which can establish the outcome of
the two runs combined and cannot establish the criterion written directly beneath it. An
acceptance test whose evidence cannot come out clean is not a test.)* Run 2's transcript
must also print `changed: 0`; the two are independent readings of the same claim, one from
the script and one from the host.

The one known benign line is `tailscaled`'s ephemeral source port, which changes if it
restarts between captures. In `S1..S2` that is the only kind of entry that may be waved
through, and only written down as the environmental event it is. Anything else there is a
defect in the script.

**Four acceptance criteria that are about not breaking the owner's machine — they matter
more than the hardening itself:**

- [x] **(1) claude — The provisioning script must not modify `PermitRootLogin` at all.** This host
      is **not fresh** — the owner administers it as `root`, and this design does not get
      to lock him out of his own exit node. **The fix must land in `DESIGN.md` §19 step 3,
      the procedure he actually executes**, not only in §15.1's rationale. (Rev 12
      identified this and corrected only the rationale; rev 14 had to re-fix it. A
      correction that lands where nobody reads it during the procedure is not a
      correction.)
- [ ] **(2) owner runs, claude records** — `/etc/networth/` is root-owned today, so the
      `chown` is **reported**, never silent. Claude's half is that the script *can* report
      it; the criterion is met by what his run actually printed.
- [x] **(3) claude** — Config is read from `/etc/networth/plaid.env`, with `PLAID_ENV`
      **never hardcoded**.
- [ ] **(4) owner runs, claude records** — Re-running the script changes nothing.
      Idempotence is testable: run twice, capture the host state three times, and the
      capture between the runs must equal the one after the second (`S1..S2` empty, above).
      Both runs are his, and so is the machine the diffs are taken on.

**Must not:**

- **No agent runs an unattended `sshd` config change on this host. Ever.** Getting this
  wrong does not degrade the product — it takes away the owner's exit node and his access
  to every credential.
- Do not open a second port. There is no public inbound service of any kind (§8.4).
- **Do not install any application unit — that is `16`'s, and specifically the listening
  one does not exist at all.** The webhook receiver was removed in rev 15 and does not come
  back through provisioning. (This rule used to read "no third unit" — a count, which issue
  #17 may legitimately change. The thing being forbidden was always an inbound listener,
  not arithmetic.)
- Do not wait on `16`. Nothing here reads what `16` decided; if you find yourself needing
  to know the unit count, the boundary above has been crossed.
- No agent asks the owner for a password (§15.1).

### 25 — ~~DB backup/restore~~ — **SUPERSEDED by `03a`**

Number retained so review references stay valid. The work moved into Phase 1 and became a
gate on `08`: **backups scheduled *after* Production linking protect nothing during the
window that matters.**
