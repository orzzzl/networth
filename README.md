# networth

A personal net-worth aggregator that refuses to lie about how old its number is.

One figure — total net worth — rebuilt at least once a day from linked brokerage
and retirement accounts, plus a small number of manually-valued assets. (v0 is
**assets only**; credit cards are deferred by owner decision, not rejected.)

**There is exactly one deliverable: an Android app.** Behind it, a headless
daemon on a small always-on server does the syncing and keeps every credential;
the app is a read-only display of an encrypted snapshot and never talks to Plaid.
Single user, zero marginal cost.

## Why this exists

Commercial aggregators were tried and discarded. The failure was not a wrong
number; it was a **silently frozen** one: after linking a brokerage the balance
stopped updating and nothing in the product said so. That is the classic
aggregator failure mode — the connection dies, and the UI keeps rendering the
last successfully fetched figure as if it were live.

So the product requirement here is not the number. It is the **honesty** of the
number:

- every account carries a visible `last_successful_sync`;
- an account carries **two clocks**, and the one that matters is when the
  institution's data was actually current — not when we last called. A
  successful API call is never, by itself, evidence of freshness;
- a total whose age cannot be established honestly is shown **with no date at
  all**, rather than borrowing one from the inputs that happen to have it;
- a connection that needs re-authentication raises an alert immediately, not at
  the next time someone happens to look.

## Status

Implementation has started, from the scaffold outwards. See [`DESIGN.md`](DESIGN.md)
for the architecture and [`tasks/README.md`](tasks/README.md) for the task
breakdown, which says who owns what and what "done" means for each piece.

## Getting set up

One prerequisite — [uv](https://docs.astral.sh/uv/), which installs the Python
this project targets, so a clean machine needs one tool rather than a Python of
the right version plus a package manager:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then, one command:

```sh
./scripts/dev-setup.sh
```

That syncs the environment (fetching Python 3.12 if you do not have it),
installs the git hooks, and checks the secret scanner runs. From there:

| Command | What it does |
|---|---|
| `./scripts/check.sh` | everything CI runs, in the order CI runs it |
| `uv run pytest` | tests |
| `uv run ruff format` / `uv run ruff check --fix` | format / lint |
| `uv run mypy` | types |
| `uv run networth demo` | the CLI |

**`scripts/check-no-secrets.sh` runs as a pre-commit hook and as a CI job**, and
it has no allowlist and no escape comment on purpose: the repository is public
and its history is permanent, so the only correct response to a finding is to
remove the value. Where credentials actually belong is [`DESIGN.md`](DESIGN.md)
§15.

### Adding a CLI verb

Drop one file in `networth/commands/`. It is discovered — there is no registry
to edit, which is what keeps concurrent tasks from colliding on a shared file.
`networth/commands/demo.py` is the worked example and the contract is documented
in `networth/cli.py`.

## Constraints that shape everything

- **Zero marginal cost.** No metered spend on top of subscriptions already paid
  for. If a capability costs money, the capability is dropped.
- **No new infrastructure and no new accounts.** The daemon runs on hardware the
  owner already pays for, reachable only over his own tailnet; the design has no
  third party holding the data and creates no account anywhere.
- **Secrets and real figures never enter this repository.** It is public and
  holds code and schema only; long-lived credentials live outside it, and account
  data lives in a database outside the working tree.

See [`AGENTS.md`](AGENTS.md) for the working agreement.
