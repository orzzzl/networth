# Task 16: Link scheduling and archive capture decision

**2026-10-03 runtime update:** `RUNTIME.md` and DESIGN section 13 describe the
implemented service roles. Publication and archive jobs have independent timers;
the Link supervisor scans every 30 seconds and gives each flow its own process.
The historical three-role proposal below is superseded by those five roles.
Production mint/release remains task08-gated; the five-minute wall-clock acceptance
is not claimed by this deployment. No review gate remains under the owner directive.

Status: A approved and merged in PR #115; capture-boundary implementation
merged in PR #118; socket timeout/no-retry policy merged in PR #120.
Stored-state full-sync planning merged in PR #121 and worker collection/persistence
merged in PR #122. Full-sync dispatch and per-Item retry admission merged in PR #124; health
dispatch and worker transaction guards merged in PR #125. Manual equity quote
collection/persistence merged in PR #128; stored cycle alert assembly/dispatch
merged in PR #130; full-cycle completion merged in PR #131. The source-price
quote-due planner merged in PR #133. Quote-only dispatch is in review; executable wiring,
remaining scheduler implementation and live acceptance
remain owed.
Task 16 remains WIP. Tasks 08 and 03a-live remain blocked on its live acceptance.

## Decision: restore the specified capture boundary (A)

Claude selected **A: narrow the shared TokenStore lock to coherent capture**:
https://github.com/orzzzl/networth/pull/115#issuecomment-5823630580.
Implementation proceeds on that approved boundary. No owner decision is needed.

A is already normative: DESIGN.md §14a.1 build ordering step 2 releases the lock
before manifest creation and sealing in steps 3–4. The wide implementation since
#46 is a code/design divergence, despite `archive.py` claiming to follow §14a.1
literally and calling the lock the capture boundary. A restores those claims;
its control regression must check the previously untested release in step 2.

The dedicated Link service is already an allowed task-16 choice and is selected.
A second service alone cannot remove the shared lock. The rejected alternative B
is retained below with its full cost, including the design amendment it required.

## Measured obstacle on main

At `d882fe1`, `BackupBuilder.build_current()` holds `TokenStore.lock_path` around
`_build_bytes()`. That method performs database/token capture, manifest creation,
bundling and encryption. `build_probe()` uses the same method under that lock.
`TokenStore.put()` needs the same lock to durably save an exchanged credential.
`poll-link` processes all eligible requests serially.

A synthetic probe used the real migrated SQLite database, TokenStore, lifecycle
worker and archive builder. Two synthetic requests were minted before starting
an archive; its real `seal` call was held at an event gate. While that gate was
held:

- A nonblocking acquisition independently proved the shared lock was held.
- The first exchange returned and entered its access-credential write.
- Only one exchange had begun; the serial worker could not reach request two.
- After releasing the archive gate, both requests completed their exchange paths.

Evidence is private machine-local tooling, outside the public repository:
`~/agents/review-evidence/task16-scheduling/archive_lock_probe.py` and its JSON
result. All fixtures are synthetic. This demonstrates an unbounded-by-code wait,
not a measured thirty-minute production stall. The probe does not claim the
first exchange attempt itself was blocked; its credential write blocked the
**next** request's first attempt. That distinction is the reason to test two
requests instead of merely checking that a separate worker starts.

A second inspection found `PlaidClient._call()` calls `method(request)` with no
explicit request timeout; `_build_api()` sets no finite timeout policy. Transport
bounds also need implementation and SDK-specific verification before any
exchange-latency claim is accepted.

## Transport policy implementation

The SDK wrapper supplies a 10-second connect timeout and a 30-second read
inactivity timeout for Link create/get/exchange. Other calls, including health
and metadata, use 10/120 seconds so slower data fetches have a separate budget.
These are explicit operating limits, not measured provider latency guarantees;
an exchange that reaches one remains uncertain and is never retried by the
worker. The real SDK transport disables retries **and redirects**, including
connection-error retries; one wrapper invocation cannot hide another send.
The cost is that idempotent data fetches lose their former transport retries too:
a transient failure now reaches the scheduler, whose explicit backoff and the
20-hour catch-up rule must provide recovery. No per-call resend policy is added.

Verified against the locked plaid-python 43.0.0 and urllib3 2.7.0 path, with
synthetic loopback responses and a failing connection factory. The tests observe
what reaches urllib3 through SDK serialization, count actual redirect requests,
and exercise a silent exchange response followed by worker restart with no replay.
The earlier capture regression also now counts live reads from before the build,
covering PR118 review's previously unguarded interval before the encryption gate.

**These limits do not establish the five-minute wall-clock target.** urllib3's
connect/read timeouts do not bound blocking name resolution or a response that
keeps supplying bytes inside the inactivity interval. Multiple resolved addresses
can also spend multiple connect waits. See the upstream
[Timeout notes](https://urllib3.readthedocs.io/en/2.7.0/reference/urllib3.util.html#urllib3.util.Timeout).
The scheduler still owes capture admission, request/result fairness, recovery
metadata accounting, and an explicit latency envelope. Do not add the two socket
numbers and call the sum a whole-operation deadline. No process kill is added:
a returned credential must still reach durability, and a timed-out exchange
remains subject to the existing uncertain-send fence.

## A: capture under lock; seal the immutable copy after release

For **both** current and probe archives:

1. Hold the existing builder lock throughout temporary-file ownership and final
   publication. Do not weaken the single-builder/probe refusal controls.
2. Hold the shared TokenStore lock only while `_capture()` copies the database
   and token records. Preserve their existing order and coherence protocol.
3. Release only the TokenStore lock; compute the manifest, bundle and encrypt
   from the captured bytes. These steps must never reopen the live database or
   live token files. The manifest and binding digest describe the captured copy.
4. Keep existing archive ledger, probe-generation, rename/fsync and recovery
   semantics. No access credential is reaped, replaced or consolidated here.

This makes dependency-free, pure-Python RFC 8439 encryption independent of
credential durability without dropping the coherent-copy boundary. It does
**not** make capture, filesystem I/O or arbitrarily many provider calls bounded;
those remain explicit work and limitations below, not facts licensed by moving
one context manager.

Encryption cost is linear in database plus token bytes, not a tunable work
factor. Claude measured `seal` at 1.41 MiB/s on `zelengs-macbook-air-2`
(1 MiB in 0.71 s); an empty migrated snapshot was 0.219 MiB. These are that
review's measurements, not a sync-host bound. Capture itself still copies and
reads the entire database and every token file: also O(database + token bytes).
DESIGN.md §14a's "well under a second" is unproven and must be corrected with
implementation; moving encryption does not establish it.

**Cost:** a small archive refactor plus concurrent integrity and Link tests.
The archive may describe an earlier capture while Link finishes after capture;
that is already normal snapshot behavior, and all bindings must still validate.

## B: retain archive scope; exclude overlap durably

Leave encryption under the TokenStore lock. Introduce a reviewed durable gate
covering archive/probe admission and automatic URL release. An archive may not
start while a released or release-unknown request still needs its exchange path;
a URL may not be released while an archive owns the gate. Crash recovery must
reconcile gate ownership and retain ambiguous state. The probe command must
report refusal rather than silently bypassing the gate.

**Cost:** another persisted protocol touching release, backup and crash recovery.
It would also amend DESIGN.md §14a.1 step 2 and remove §14a's "well under a
second" claim. An unresolved request may delay backups indefinitely; because
sealing cost grows with archive size, that can become a steady state when the
admission budget is exceeded, not merely a tail risk. A is selected. Neither
option changes the 30-minute token deadline or adjudicates an uncertain exchange
as safe to retry.

## Scheduling shape to implement

- Three service roles: `networth-sync.service`, `networth-link.service`, and
  read-only `networth-serve.service`. Update DESIGN.md section 13's table,
  single-writer claim, trigger wording and count in the implementation PR.
  The third service is an outbound Link worker, not an inbound receiver.
- The Link timer uses `OnCalendar=` with `Persistent=true`, a one-minute cadence,
  `AccuracySec=1s`, and no randomized delay. Enable the Link service at boot too,
  so a first installation or boot with no missed timer stamp still scans stored
  requests. Do not restart or depend on completion of the general sync service.
- An immediate trigger targets the Link service. Coalescing while that service
  is active remains possible: bound each pass, re-scan before exit, and prove the
  next activation delay. Never cite systemd coalescing as the exchange fence;
  the per-request file lock and conditional SQL claim remain that fence.
- Schedule `run_lifecycle`, not a timer-based state rewrite. Its request selection
  includes unreaped material and EXCHANGING recovery even on otherwise closed
  requests. Closure coverage holds and diagnostics retention remain authoritative;
  there is no scheduler-owned credential deletion or automatic adjudication.
- Target at most **five minutes** from an eligible success becoming observable to
  first exchange attempt on an awake host with functioning storage and responsive
  provider calls. This is a target, not yet a proven bound. The implementation
  must account for multiple requests/results, recovery metadata, request-lock
  contention, capture duration and bounded transport waits. `TokenStore.deleting()`
  also holds this lock across its caller's database update; it currently has no
  production caller, but scheduling a caller must account for that distinct hold.
  If those cannot fit, revise this contract rather than silently weakening the
  acceptance claim.
- A blanket process kill during exchange/credential persistence is not the normal
  deadline mechanism: it can strand a returned credential before durability.
  Transport timeout follows existing uncertain-send handling; it never authorizes
  retry. Any service-level emergency timeout must document that residual cost.
- Sandbox-only executable guards remain until task 08 supplies its separately
  reviewed Production path. No new Link run or owner rerun is needed for this PR.

Timer behavior source: upstream `systemd.timer` documentation (in particular
active-unit coalescing and the OnCalendar-only Persistent behavior):
https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html
(source verified at https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml).

## Acceptance before claiming the bound or installing

1. Hold actual archive encryption at an event gate, run multiple Link requests,
   and observe **both** exchange attempts and durable credential writes before
   releasing encryption. Name §14a.1 step 2 in the regression and add a control
   proving the old lock scope fails: this checks the previously untested doc claim.
2. Verify archived DB/token bindings after a concurrent Link write. Assert that
   processing captured bytes performs no live-store re-read; test current and
   probe paths. Keep crash cleanup and builder-lock refusal behavior. Check cooldown
   before taking the token lock: reuse an existing recent probe even during a token
   write, without reading tokens. Retain the existing cooldown refusal counter
   increment (the current REUSED branch already calls `count_probe_refusal()`);
   reuse is counted once, not also as a token-lock refusal. A real new build still
   refuses and counts once when the nonblocking token lock is unavailable. Test
   both paths and unchanged generation on reuse/refusal.
3. Delay database/token capture separately: `VACUUM INTO`, snapshot `read_bytes()`
   and every token file are O(database + token bytes). Measure its contribution
   and implement the bounded wait/admission policy before making the five-minute
   claim. A slow encryption test alone must not stand in for this case.
4. Exercise a slow first request, a second ready request, lock contention, multiple
   results, transport timeout, and uncertain exchange with no replay. Verify any
   SDK retry policy cannot repeat an exchange behind the claim.
5. Restart with pending state and with an already-attempted exchange; scan at boot
   without relying on a previous timer stamp. Retain the distinct terminal facts.
6. Prove Link wakes while real sync/archive work remains active. Verify the unit
   wiring and timer on Linux, not just through source-text assertions on macOS.

## Stored full-sync due planner

Migration 0010 distinguishes `FULL_SYNC` from `OTHER` in `sync_run`, because
manual revisions and quote-only work already use this ledger. Legacy successes
remain `OTHER`; the first scheduled activation therefore conservatively runs a
full sync. The runtime caller must explicitly mark full-sync runs at creation.
`FullSyncSchedule` reads only committed, completed successes of that kind.
The two-value vocabulary is deliberate: manual revisions and quote-only work
both remain `OTHER` because this planner only needs to distinguish full syncs.
Distinguishing those other jobs later requires a migration that rebuilds
`sync_run` to widen its `CHECK`; it is not just another value a caller can write.

The latest successful start satisfies market close + 1h; the latest successful
finish controls the strict >20h clock. Separate maxima preserve both facts when
runs finish out of order. A run starting before market readiness cannot satisfy
it merely by finishing afterward. The existing local market calendar supplies
holidays, DST and early closes. Planning is read-only, consumes no due work and
is identical after reopening the database; failed and interrupted runs do not
advance the clock. Malformed, non-UTC, reversed or future success clocks raise
`ScheduleStateError` with a fixed diagnostic. Per DESIGN §13, this means due:
the dispatcher must report the diagnostic and schedule a full sync subject to
per-Item retry backoff, never skip it as "not due". Caller misuse (an active
transaction or non-UTC check time) raises `ValueError` separately and must not
enter that fallback.

All historical successes are validated. One bad row can keep this fallback
active across subsequent healthy syncs: future clocks require time to catch up,
and malformed or reversed clocks require explicit repair. The cost is repeated
diagnostics and potentially a full sync on every activation; new successes do
not heal old evidence, and no automatic ledger rewrite or deletion is added.
Runtime dispatch acceptance must demonstrate this fallback with a bad historical
row plus newer healthy rows, and show that caller misuse escapes it.

The full-sync dispatcher below adopts this planner and writes `FULL_SYNC`; no
command invokes it yet. Other job predicates, full cycle assembly, units and
live installation remain owed. The regression
suite uses migrated databases, a real WAL reader and a reopen after interruption.

## Worker transaction boundary

`FullSync.collect()` and `ItemHealthPoller.collect_due()` / `collect_all()`
return immutable in-memory plans without writing. The scheduler must call them
without an open transaction, then open `BEGIN IMMEDIATE` and call `persist()`.
Persistence calls no provider or token resolver and never commits for its caller.
If the transaction fails, roll it back before retrying persistence with the same
plan; replaying collection would repeat network work unnecessarily. A committed
full-sync plan must not be replayed: duplicate run/account observations are
refused, even for identical values. Health persistence retains the repository's
rule that an older observation cannot replace a newer one.

This separation is needed because wrapping the old combined methods in
`BEGIN IMMEDIATE` holds a write lock across their provider calls; waiting until
a combined call returns starts the explicit transaction after its writes.
The `run()` / `poll_due()` / `poll_all()` convenience methods retain caller-owned
persistence, but now reject an active transaction before collection, as does
`poll_item()`. Callers must commit their preceding work before invoking them and
commit or roll back their resulting writes themselves. They are not the scheduled
runtime entry points. The class guards use the actual Item repository connection
and run before token resolution; caller misuse is raised, not caught as a provider
failure or converted to carried-forward observations.

The plans contain sensitive account/health facts, not credentials, and must not
be logged or serialized as recovery files. A process death before persistence
loses the plan; the unfinished run cannot count as a success. The future runner
owns run creation/completion, single-run admission, three-attempt jittered SQLite
busy retries, and the commit containing observations plus the run result.
Those policies are not implemented by these worker methods; the full-sync
dispatcher below owns them for full sync.

Synthetic WAL tests use two connections: an independent writer commits during
each provider call, then a failure on the second persisted record leaves the
first invisible to the other connection. Rollback removes it; retry succeeds
without another provider call or a hidden commit. A newer health poll arriving
between collection and retry is preserved. These test the worker seam, not the
remaining live `record-pull` / `pair` / `revoke` contention acceptance.

## Full-sync dispatch and retry admission

`FullSyncDispatcher` constructs its worker on its own connection and enforces
no active transaction before planning and immediately before collection. This
is PR122 review finding 1's caller-side enforcement; both workers now also guard
their collection entry points on the actual repository connection. PR122 finding
2's stale health-loop comment is also corrected; simplifying the `_Plan` wrapper is deferred cleanup.

Admission holds the selected database's canonical sync file lock, nonblocking,
through the whole run; nested dispatch is refused even in the same thread.
The future executable must supply the same lock path to every dispatcher of that
database. A second invocation raises `LockUnavailable` without creating a run.
An unfinished row is not a lock: restart may begin a new run after the OS releases
the dead process's file lock. Old unfinished rows remain unsuccessful history.

Run creation commits `kind='FULL_SYNC'` before provider I/O. Collection runs
without a transaction. Observations, per-Item retry outcomes and the finished
run's `ok` commit in one `BEGIN IMMEDIATE`. Every write transaction retries
`SQLITE_BUSY` (including extended BUSY codes) at most three attempts, rolling
back before replay, with 50–150 ms then 100–300 ms jitter after SQLite's 5000 ms
busy timeout. It never recollects inside a database retry. Non-BUSY errors and
exhaustion propagate; their run remains unfinished and cannot satisfy due-ness.

Migration 0011 stores `full_sync_retry(item_id, failures, next_attempt_at)`.
Any account failure makes that Item's attempt fail once, irrespective of account
count; delay is 1h/2h/4h/8h from completed collection, capped at 8h. Success deletes
its retry row. Deferred Items do not resolve tokens or call providers, and do not
advance the counter; their older observations are carried forward with both
clocks intact, or omitted when none exist. A partial/deferred run is unsuccessful
and does not advance the full-sync clock. If every current target Item is
backing off, no run is created. A corrupt full-sync success clock still reports
the fixed diagnostic, but cannot bypass this retry admission.

Costs and limits: healthy Items in a mixed partial run are fetched again on each
activation while the full-sync predicate remains due. This is conservative,
not a per-Item successful-fetch cache. A crash before completion leaves no new
retry outcome; the next activation may repeat those idempotent data fetches.
This policy must never be applied to Link exchanges. Invalid retry timestamps
fail closed with a fixed error and require repair; they are not success-clock
corruption and cannot authorize skipping a retry delay. No runtime installation,
provider calls or credential reads are needed to review this slice.

Synthetic WAL regressions prove an independent writer can commit during provider
calls; finishing writes stay invisible together until commit; a write retry
reuses collected data and increments a failed Item once. Reopen tests pin
backoff boundaries, cap/reset and recovery from an unfinished run. The full
cycle still must value manual accounts, evaluate alerts, snapshot and publish;
this component does not claim those acceptance criteria or add a CLI command.

## Health dispatch

`HealthDispatcher` shares full sync's canonical file lock and three-attempt
BUSY write helper. It collects due health observations with no active transaction,
then persists the whole plan under `BEGIN IMMEDIATE`. A write retry reuses that
plan; a newer poll already on disk retains precedence. Nothing writes `sync_run`
or the full-sync retry table: health completion must not satisfy full-sync due-ness.

Due work comes from each Item's committed `last_health_poll_at`, including never
polled Items and polls exactly one hour old (the existing task-10 inclusive
boundary). Restart catches up without a timer stamp, on non-market days too.
Unclassified exceptions leave that target's clock unchanged and return a degraded
batch; other collected observations still commit. Classified provider outcomes,
including transport failure, are health observations and advance the poll clock
without inventing successful source data. There is no additional exponential
health retry delay: unobserved failures retry next activation, which can cost a
call each tick until repaired. A crash before commit discards the in-memory plan
and repeats idempotent health reads on restart. No Link exchange uses this policy.

The combined worker APIs now refuse active transactions, including read-only
`BEGIN`, before resolving tokens. Existing health fixtures explicitly commit
setup and successive polls. No production caller uses those convenience health
APIs yet. Persistence still never commits behind the caller. Synthetic WAL tests
pin writer availability, atomic rollback/replay, newer-poll precedence, restart,
exact hourly due boundary, and mutual exclusion with full sync. No executable or
runtime installation is introduced here.

## Remaining task-16 work

The subsequent implementation still owes the other job predicates,
integration of the stored alert dispatcher,
manual quote observations before snapshot, publication and archive ordering,
writer-contention tests, and reaper scheduling. Live acceptance still owes the
reviewed runtime install, forced backup dispatcher ownership/mode/byte equality,
active services and timer next elapse, exact tailnet listener/public baseline and
no-Funnel checks. Task 16 cannot be DONE from this proposal or unit files alone.

## Manual equity quote collection (merged #128)

`ManualQuoteWorker` reads every active included `MANUAL_QTY_LIVE_PRICE` account
and its stored `EQUITY_SHARES` holding in one read transaction, closes that
transaction, then fetches a deduplicated batch of symbols. It values each holding
through task 13's `EquityHolding.value_with()` and produces immutable in-memory
`QUOTE` drafts for the named run. The source clock stays the quote timestamp;
fetch/observation time is measured after the response. A quote may legitimately
advance during the call. Future quotes or confirmation dates are refused.

Persistence requires the caller's short `BEGIN IMMEDIATE` transaction and checks
that the selected accounts and their normalized holding values still match the
capture. A concurrent quantity/confirmation/account-selection edit refuses the
whole plan before any writes. Notes and account display names do not affect
valuation. It appends observations and updates fetch summaries without committing;
rollback and persistence retry reuse the plan without fetching again. A committed
plan cannot be appended twice, through the existing observation uniqueness rule.
Plans contain sensitive values and must never be logged or saved to recovery files.

Missing, malformed, mismatched or unavailable prices refuse collection rather
than producing zero or silently reusing an old quote. This strict slice adds no
carry-forward policy: its future caller must leave an incomplete cycle unfinished
and retry collection. This can delay a snapshot during a quote-provider outage.
The existing full-sync dispatcher still marks only its Plaid work complete; cycle
integration must ensure manual failure cannot become a completed-cycle claim.
This is a prerequisite, not a command or completed cycle: alert assembly,
quote-due planning, full cycle/run-success composition, independent publication
retry, archive scheduling, Link bounds, units and installation remain owed.

Synthetic migrated-WAL tests observe a rival writer commit during quote I/O,
uncommitted observations staying invisible to that rival, rollback after the
second append fails, and retry with no second quote call. The successful path
feeds the real Snapshotter and checks its total and source age. This is local
component evidence, not Linux unit or live-host acceptance.

## Cycle alert assembly and dispatch (merged #130)

`CycleAlertEvaluator` reads every stored Item and every active account in its
caller's transaction, derives freshness through `StalenessMachine`, explicitly
reads each manual side, then calls `AlertEvaluator.evaluate()`. `AlertDispatcher`
owns the canonical sync lock and the short `BEGIN IMMEDIATE`/three-attempt BUSY
retry policy for that operation. There is no provider I/O in the transaction.
Retries reread the facts after rollback: a rival writer may have changed them.

Account selection includes accounts excluded from the headline: that choice does
not confirm a replacement or a share count. All archive/supersession markers
exclude a subject; its omitted signals deliberately do not resolve old alerts.
Non-static accounts use their latest stored observation, preserving source and
fetch clocks. No new successful sync is required: wall time alone can make an
old price frozen or a share count overdue, including on non-market days. A missing
observation produces no freshness assessment, preserving any standing frozen
alert while still evaluating reconciliation and manual facts. A property uses
the effective lineage revision, as Snapshotter does, not latest insertion order.

A read missing manual row or a property row supplies `ShareCountObservation(None)`;
a share row supplies its validated confirmation clock. Malformed/non-UTC manual
clocks and future Item/observation/confirmation clocks refuse the batch. They do
not become absence or healthy evidence. All input assembly precedes alert writes;
a later write failure rolls back the entire evaluation. No alert prompt is marked
here: Publisher owns marking only the alerts in a committed envelope.

Cycle callers must evaluate alerts after worker persistence and before
publication, even when no new snapshot was produced. The synthetic test
assembles all five alert kinds, snapshots, encrypts with the real Publisher and
decodes the resulting bulletin. This proves component compatibility and delivery
when called in that order; it is not executable scheduling evidence. No command
or live runtime is added in this slice. Full/manual cycle completion, quote due
planning, independent publication retry, archive scheduling, Link latency, units
and installation remain owed before task 16 can close.

## Full-cycle completion (merged #131)

`FullCycleDispatcher` extends the reviewed full-sync admission, stored due clocks
and per-Item retry policy. It collects Plaid first; when that plan has no failures,
it collects the manual quotes for the same run without a SQLite transaction.
The canonical sync file lock remains held across both providers and completion.
The completion clock is sampled after quote collection. One short write
transaction persists Plaid observations/retry outcomes, manual observations,
`sync_run` success, the Snapshotter result and cycle alerts. SnapshotRepository
requires `ok=1`, so that assignment precedes snapshot construction **inside the
same uncommitted transaction**; an observer cannot see success without its snapshot.

Missing quotes, changed manual inputs, snapshot refusal or alert failure roll
back the entire completion, leaving only the previously committed unfinished run.
BUSY retry replays persistence with the same collected plans and rereads alert
facts; it never recollects either provider. Process death discards the plans,
and the next activation starts a new run. This conservative policy can repeat
successful idempotent Plaid calls during a prolonged quote outage. It does not
reuse a prior price, manufacture a successful cycle, or replay a Link exchange.

A failed or deferred Plaid plan cannot produce a successful snapshot. It persists
its available/carry-forward observations and per-Item retry result, evaluates
alerts and records `ok=0`, without requesting manual prices. An unrelated quote
outage therefore cannot erase the failed Item's backoff. An alert/input error can
still roll back that completion and its retry outcome, just like another final
transaction failure; no partial completion is committed. All-deferred or not-due
activations create no run or snapshot, but still reassess stored alerts under the
same lock. Quote-only refresh is the separate dispatcher described below.

`FullSyncDispatcher` remains the Plaid-only component seam; the future scheduled
full-cycle executable must choose `FullCycleDispatcher`. Neither is currently
wired to a command. Publication follows a completed transaction and retains its
independent retry obligation: a failed Publisher call cannot undo the snapshot
or consume another provider fetch. This slice does not schedule Publisher or
archives, install units, or establish the Link latency target. The health job
still runs separately and does not become full-sync success evidence.

Synthetic WAL tests measure independent writes during both provider calls,
refusal of a competing dispatcher, atomic visibility through a last-stage BUSY
retry, restart after quote failure, manual edit/snapshot/alert refusals, the
manual-only full-cycle path, and completion clocks after quote I/O. A real
Publisher encrypt/decrypt check confirms the committed manual-inclusive total,
its source age and the share-count alert. These are component integration checks,
not executable scheduling or live acceptance. Task16/08/03a-live remain open.

## Quote refresh due planner (merged #133)

`QuoteRefreshSchedule` reads the same active, included manual-equity accounts as
`ManualQuoteWorker`, including NEW accounts. An absent observation, unknown source
clock, or source price older than the latest completed market close makes the
batch due. Equality at close satisfies it. Unlike full sync, there is no one-hour
posting grace or 20-hour fallback: the local calendar supplies the actual latest
close across weekends, holidays, DST and early closes.

Selection and source clocks are read in one short read transaction, released
before returning. Planning never writes, fetches or consumes work. Both FULL_SYNC
and OTHER observations can supply prices; neither a successful run nor a recent
fetch/summary timestamp can make an old price current. Reopening the database
preserves the answer. A successful provider response with a still-old source
price therefore remains due, even if that means another quote call each tick.

Malformed or future source/fetch/observation evidence raises a fixed
`QuoteScheduleStateError`; it cannot return not-due. The future dispatcher must
report this refusal and leave the work pending. This differs from the separately
reviewed full-sync corrupt-ledger fallback: it never interprets an invalid price
as valid evidence. Caller misuse (an active transaction or non-UTC check time)
raises ValueError without rolling back the caller's transaction.

This is the read-only prerequisite for quote-only dispatch, not a scheduled
quote cycle. Run creation/completion, preserving other accounts' dated values in
a quote-only snapshot, publication/archive scheduling, executable wiring, Link
latency, units and live acceptance remain owed. PR131's two review notes are also
pinned: completion-clock refusal names its own error, and a rival writer makes
idle-path validation before writer admission observable.

## Quote-only dispatch (in review)

`QuoteCycleDispatcher` admits work through `QuoteRefreshSchedule` under the
canonical sync lock. It creates a durable unfinished `OTHER` run before quote
collection, which holds no SQLite transaction. Completion commits manual
observations, carried linked observations, success, snapshot and alerts in one
short write transaction. Three BUSY attempts reuse the collected quote plan;
restart recollects. Invalid stored price clocks propagate their fixed refusal
before run creation, leaving work pending.

Linked values come from the latest committed observations read inside the final
write transaction. Their figures, source clocks and fetch times remain unchanged;
the new observation time records the carry, and `is_carried_forward=True` makes
the snapshot incomplete. Account fetch summaries and Item retry state stay intact.
Missing or future linked evidence refuses completion. NEW linked accounts need
no carried value because the snapshot excludes them; manual-static revisions
continue to be selected by Snapshotter. Manual edits during collection still
refuse the entire plan through the reviewed ManualQuoteWorker boundary.

A quote-only success never satisfies the full-sync clock. A successful response
with an old price remains due, potentially calling again each tick. Idle quote
admission does not take a writer lock or evaluate alerts; the runtime must retain
its independent AlertDispatcher activation before publication, including when
this job is idle or fails. Publication follows the completed transaction and
cannot undo it. No executable or live installation is introduced by this slice.
