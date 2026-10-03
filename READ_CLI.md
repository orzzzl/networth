# Read-only command line

Run on the sync host over SSH with `NETWORTH_ENV=sandbox` or
`NETWORTH_ENV=production` explicitly set. The selection uses the existing paired
paths (`~/networth-data/networth-sandbox.db` or `~/networth-data/networth.db`, and
the corresponding token directory under `/etc/networth/`). No provider credential
is read and no provider request is made. These verbs do not start the daemon,
create a database, migrate a schema, repair state, or reap material.

```sh
NETWORTH_ENV=sandbox networth show
NETWORTH_ENV=sandbox networth history
NETWORTH_ENV=sandbox networth history --account 1
NETWORTH_ENV=sandbox networth doctor
```

Output is indented JSON. Amounts remain integer minor units with their currency
and source age; there is no floating-point money conversion. `show` includes the
stored snapshot's completeness/counts, tagged age, and account freshness. Its
`freshness_assessed_at` is the snapshot time, not the current time. `read_at`
records when the command read it and never substitutes for the total's `as_of`.
`UNKNOWN` and `STATIC_ONLY` totals have a null `as_of`; even a known diagnostic
floor must never become their headline date. An inconsistent current population
produces no headline and exits nonzero. `doctor` still explains the mismatch and
asks for a new successful snapshot. History is immutable; `--account` follows
lineage across replacement account IDs, retaining each observation's own clocks.

`doctor` reports account fetch/source clocks, Item states and slot evidence,
verified backup pulls, restore-drill age, the owner's escrow attestation,
publication age, probe/dispatcher refusal counts, restore lineage, open alerts,
and Link support identifiers. A missing response leaves `item_id` and its
attempt's `request_id` explicitly `never observed`. Results stay distinct even
when a request contains several sessions or exchanges. Slot counts remain unknown
when existing adjudication rules refuse to count them.

Token diagnostics inspect both published and pending file names without opening
their contents or constructing a writable TokenStore. File presence is not proof
of credential validity; unreadable metadata and symlinks remain unknown. A
missing published file makes a reference dangling even when a recoverable pending
file survives. Reap due-ness requires recorded polling closure and completed
child retention bounds without unresolved holds; an expired URL alone does not
permit deletion. Cleared references cannot hide material at a deterministic name.
Filesystem observations can race a writer; they are labelled separately from the
single SQLite read transaction. Re-run diagnostics after the writer completes.

On `zelengs-macbook-air-2`, inspect only local recovery records with:

```sh
networth doctor --local
```

This mode needs no daemon environment, opens no sync-host database, and contacts
no remote host. It reports each local record's `reap_after` hygiene deadline,
which is not a provider exchange deadline. Expired and unreadable records stay
untouched. The sync-host mode does not claim to know these local files exist.
Neither mode counts a manual-paste fallback.

Exit 0 means the read completed, not that the installation is healthy. Exit 1
marks a missing `show` snapshot, a diagnostic snapshot/budget refusal, or an
unreadable local record. Exit 2 means invalid arguments/configuration or a read
failure. Schema changes must be applied by the existing writer/migration path,
never implicitly by these commands.
