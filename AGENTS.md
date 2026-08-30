# AGENTS.md — Working agreement for this repo

`networth` is a personal net-worth aggregator. **Claude** leads the project;
**Codex** and **Claude** cross-review each other's design, PRs, and task
assignment. Antigravity (Gemini) is an equal reviewer when the default reviewer
is out of quota. The owner is pulled in only for genuine decisions.

Agent-to-agent coordination happens through the machine-level mailbox at
`~/agents/` (spec: `~/agents/PROTOCOL.md`). Nothing from that mailbox is ever
committed here.

## Hard rules (do not violate)

### 1. Secrets never enter this repository

This is the rule that matters most, because the credentials involved are
long-lived and grant read access to real financial accounts.

- Plaid `access_token`s, the Plaid `client_id`/`secret`, and the Alpaca key live
  in `~/agents/secrets/` — **never** in git, never in a PR, never in a PR
  comment, never echoed into a log or a test fixture.
- The database stores a *reference* to a secret (a key name), never the secret.
- Committed code reads secrets from files under `~/agents/secrets/` or from the
  environment. Example files (`*.env.example`) carry key names only, no values.
- Before every commit, check the diff for anything credential-shaped. `.gitignore`
  is a safety net, not permission to be careless.

### 2. Zero marginal cost

No spend beyond flat subscriptions already paid for. A paid tier is not a
tradeoff to weigh — it is out of scope. Concretely:

- Design to the **Plaid Trial plan: 10 Production Items, free**. Items are a
  scarce, *non-recyclable* resource (see `DESIGN.md` — `/item/remove` does not
  free a slot).
- No paid add-ons, no hosting bills, no metered APIs. If a capability requires
  money, drop the capability and record why.

### 3. The owner performs the owner-only steps; agents never do

- Creating the Plaid developer account and accepting its terms.
- Running Plaid Link for any institution — that means entering real banking
  credentials and MFA.

Never ask the owner to paste a credential into a chat, a file, or a PR. Agents
build the tooling; the owner runs it. Everything on either side of those two
steps must be fully automated.

### 4. Never present a number without its age

The product exists because other aggregators render stale figures as live ones.
Any code path that produces a total must also produce its `as_of` and its
staleness annotation. A UI that can display a bare total is a bug, not a
simplification.

## Workflow

1. Work the task in `tasks/` whose status is `READY`. Do not self-assign work
   that is `BLOCKED` or unassigned — task assignment is itself reviewed.
2. **Use your own git worktree.** Codex works directly in the `~/networth` main
   checkout, so other agents branch into a separate worktree
   (`git worktree add ../networth-wt-<slug> -b <branch>`). Never run
   `git add -A` in a shared checkout — stage explicit paths.
3. Branch naming: `task/<id>-<short-slug>` (e.g. `task/04-sqlite-schema`).
4. Implement **only what the task spec asks**. Keep the diff minimal and scoped.
5. Open a PR whose title starts with an author tag — `[claude]`, `[codex]`, or
   `[antigravity]` — naming the agent that wrote it. Link the task in the body.
6. **Every PR is reviewed before merge — no exceptions, no self-merge.** Every
   PR is reviewed by a *different* agent than its author; Claude's PRs go to
   Codex by default. Merge only after an explicit approval.
7. Notify the reviewer through the mailbox (`~/agents/inbox/<agent>/new/`).
   Review content itself stays on the GitHub PR, per `~/agents/PROTOCOL.md`.

## Conventions

- **English** for all code, comments, identifiers, docs, and commit messages.
  User-facing strings go through the UI layer's own i18n, never hard-coded.
- The sync and data layer is **UI-agnostic**. It must not import anything
  UI-specific; the first consumer is a CLI. Depend on the interfaces described
  in `DESIGN.md`, not on concrete implementations across layers.
- No new dependencies without an explicit OK written in the task spec.
- Money is never a float. Store minor units (integer cents) or a decimal type;
  never `float` arithmetic on balances.
- All timestamps stored UTC, ISO-8601, with an explicit timezone. Staleness
  math is done in UTC. (`~/tuantuan-stock` shipped a "no notification" bug that
  came down to a timezone lookup failure — do not repeat it.)
- Keep functions small; match surrounding style; no dead code.

## When in doubt

Stop and write the question in the PR description or the task file rather than
inventing behavior. For anything touching money, credentials, or the Plaid Item
budget, ask — a wrong guess there is expensive or irreversible.
