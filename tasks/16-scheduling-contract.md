# Task 16: Link scheduling and archive capture decision

Status: A approved and merged in PR #115; capture-boundary implementation
is in review. Scheduler implementation and live acceptance remain owed.
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
credential durability without dropping the coherent-copy boundary. It does **not** make capture,
filesystem I/O or arbitrarily many provider calls bounded; those remain explicit
work and limitations below, not facts licensed by moving one context manager.

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
   claim. A slow
   encryption test alone must not stand in for this case.
4. Exercise a slow first request, a second ready request, lock contention, multiple
   results, transport timeout, and uncertain exchange with no replay. Verify any
   SDK retry policy cannot repeat an exchange behind the claim.
5. Restart with pending state and with an already-attempted exchange; scan at boot
   without relying on a previous timer stamp. Retain the distinct terminal facts.
6. Prove Link wakes while real sync/archive work remains active. Verify the unit
   wiring and timer on Linux, not just through source-text assertions on macOS.

## Remaining task-16 work

The subsequent implementation still owes the stored-state due engine (including
weekends and successful-sync clocks), per-cycle health/account/manual alert facts,
manual quote observations before snapshot, publication and archive ordering,
writer-contention tests, and reaper scheduling. Live acceptance still owes the
reviewed runtime install, forced backup dispatcher ownership/mode/byte equality,
active services and timer next elapse, exact tailnet listener/public baseline and
no-Funnel checks. Task 16 cannot be DONE from this proposal or unit files alone.
