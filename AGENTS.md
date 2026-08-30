# AGENTS.md — Working agreement for this repo

`networth` is a personal net-worth aggregator. **Claude** leads the project;
**Codex** and **Claude** cross-review each other's design, PRs, and task
assignment. Antigravity (Gemini) is an equal reviewer when the default reviewer
is out of quota. The owner is pulled in only for genuine decisions.

Agent-to-agent coordination happens through the machine-level mailbox at
`~/agents/` (spec: `~/agents/PROTOCOL.md`). Nothing from that mailbox is ever
committed here.

## Hard rules (do not violate)

### 0. This repository is PUBLIC

Everything here is world-readable, and **anything committed once stays in the
history even after it is deleted** — on a public repo that is unrecoverable.
Visibility was never the control that protects this project; the separation
between code and data is. So: the repo holds *code and schema only*.

- Field names, table shapes and state machines are **not** sensitive. Balances,
  holdings, account identifiers and the owner's institutions **are**.
- **No institution-specific detail anywhere** — not in the sync engine, not in
  config, not in tests, docs, UI copy or default values. Everything about which
  institutions exist comes from the runtime link flow. A hardcoded institution
  is a bug (`DESIGN.md` §2). `DESIGN.md` deliberately describes account
  *categories*; keep it that way.

### 1. Secrets and real figures never enter this repository

This is the rule that matters most, because the credentials involved are
long-lived and grant read access to real financial accounts.

- Plaid `access_token`s, the Plaid `client_id`/`secret`, the transport
  credential, the payload encryption key, the Android keystore and the quotes
  key live in `~/agents/secrets/` — **never** in git, never in a PR, never in a
  PR comment, never echoed into a log or a test fixture.
- The database stores a *reference* to a secret (a key name), never the secret.
- Committed code reads secrets from files under `~/agents/secrets/` or from the
  environment. Example files (`*.env.example`) carry key names only, no values.
- **Never commit real figures.** No real balances, account numbers, institution
  item ids, or screenshots showing actual holdings — in code, tests, fixtures,
  docs, or issue/PR text. **All test fixtures are synthetic.** This binds CI
  too: no test or script may print a real balance.
- **PR descriptions and commit messages carry no real numbers.** Report a
  verified sync as "3 accounts reconciled", never as amounts.
- Before every commit, check the diff for anything credential- or
  balance-shaped. `.gitignore` is a safety net, not permission to be careless;
  the ignore rules were written in the first commit for that reason.

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
  UI-specific or transport-specific; the first consumer is a CLI, the second is
  a published payload. Depend on the interfaces described in `DESIGN.md`, not on
  concrete implementations across layers.
- The Mac and the phone share **one contract: the payload schema**, versioned
  explicitly. Changing it means bumping `schema_version`; an older app must
  refuse to render a newer payload rather than misread it.
- **Bump `pubspec.yaml` before every APK handed to the owner** — a feature batch
  bumps the minor version, the `+N` build number always increments, and the
  delivered file name carries the new version and overwrites the previous file.
  Two different APKs sharing a version has already cost one real debugging
  incident on a sibling project.
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
