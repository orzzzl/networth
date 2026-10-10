# VPS runtime

Task 16 composes the merged workers and installs them on the provisioned host.
The installer requires a full commit that has passed CI and merged:

```sh
sudo bash scripts/install-runtime.sh <full-merged-commit>
```

Run it on `tokyo-exit` over the existing agent SSH key. It installs hash-pinned
runtime/build dependencies, root-owned code in `/opt/networth/releases/<commit>`,
`/opt/networth/current`, executable wrappers in `/usr/local/bin`, and the exact
backup forced-command source at `/usr/local/lib/networth/backup-ssh-dispatch`.
It preserves existing credentials and configuration. To upgrade, pass
`--upgrade-if-idle` after the commit. This holds
`/var/lib/networth/.runtime-deploy.lock` (also required of task08's mint), refuses
pending Link requests, stages the pinned release, stops workers, rechecks pending
requests, then switches code and starts services. An active exchange has no forced
kill timeout. A failed preflight leaves the old deployment running.

`/etc/networth/networth.env` selects Production and the archive directory.
Every job validates `/etc/networth/plaid.env` agrees with `NETWORTH_ENV` before
mutation. No secret is copied into a unit or printed. The database lives under
the service user's home, `/var/lib/networth/networth-data/networth.db`.

- Sync runs at boot and every five minutes: health, full cycles and quote cycles.
  Full cycles use cached balances; no paid realtime-balance product is enabled.
  Quotes credentials load only when a manual holding needs a quote.
- Publication runs every minute independently of sync. It waits for pairing and
  a snapshot, then publishes changed snapshots/pairings or the daily heartbeat.
  Alert evaluation precedes publication. Failure never rolls back a snapshot or
  consumes another provider fetch.
- Archives run independently every five minutes from the last build clock.
  Missing archive files rebuild even if the ledger is recent. Existing builder
  and coherent-capture locks remain authoritative.
- Link scans every 30 seconds and immediately at boot. Each pending flow has its
  own child process; a slow flow does not delay a second flow's admission.
  Children receive the running release's source directory in `PYTHONPATH`; the
  installer's preflight tests this import outside the checkout before downtime.
  Failed child exits are reported without identifiers and excluded from the
  active count once observed. An active unit alone does not prove polling works.
  The lifecycle worker handles response-based transitions, uncertain exchanges,
  recovery and the diagnostics-window reaper. No blind exchange retries.
- Serving binds only the node's current Tailscale address and retries startup
  while that address is unavailable. It reads the encrypted envelope only.

Inspect status without disclosing figures or credentials:

```sh
systemctl status 'networth-*' --no-pager
systemctl list-timers 'networth-*' --no-pager
sudo /usr/local/bin/networth verify-listeners
```

The oneshot services normally show `inactive (dead)` after successful completion;
check `Result=success` and `ExecMainStatus=0`, alongside the active timers. The
Link and serving services must be active. Do not infer daemon health from units
merely being installed. Also inspect `tailscale funnel status` after installation.

## Close a Production Link request after expiry

An operator performs this after each owner Link, using private evidence. This is
request/slot evidence adjudication, not code review. Never infer zero additional
slots from empty polls, or repeat Link to repair a request. Abandon only records
intent; it does not revoke the URL, close polling or reclaim a lifetime Item.

1. Wait until the recorded `hosted_url_expires_at` has passed and the service has
   completed a successful poll at or after that instant. Retain the private flow
   ID, `COVERAGE_UNPROVEN` observation ID and evidence audit UUID. Resolve any
   unfinished session, pending/uncertain exchange, polling error or material hold
   through its existing recovery procedure before proceeding.
2. Stop admission and let existing children finish with
   `sudo systemctl stop networth-link.service`. The unit's `KillMode=mixed` and
   infinite stop timeout let children finish without forced termination. Confirm
   the unit is inactive and its cgroup is empty; do not adjudicate while a child
   or another operator is still polling. If stop waits, investigate the worker;
   never kill a possibly returned credential to force closure.
3. On `tokyo-exit`, record the evidence-derived count of **additional** spent
   slots beyond known Items/results and other adjudications. With polling still
   stopped, run these commands as the service user (substitute private values;
   never paste their output or evidence into git):

   ```sh
   sudo runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production \
     /usr/local/bin/networth adjudicate-link-observation \
     --observation <observation-id> --additional-slots <evidenced-count> \
     --audit-id <audit-uuid> --confirm-reviewed
   sudo runuser -u networth -- env HOME=/var/lib/networth NETWORTH_ENV=production \
     /usr/local/bin/networth close-link-request --flow <flow-id> \
     --audit-id <audit-uuid> --confirm-reviewed
   ```

   Do not guess the count. Every unresolved observation must be adjudicated;
   closure still refuses incomplete child/poll evidence and material holds.
   Optional abandonment uses the same environment and
   `networth abandon-link-request --flow <flow-id>`; it does not bypass these gates.
4. Confirm closure reports material reaped. If it reports false, retain the
   request until every diagnostic retention deadline has passed; the lifecycle
   reaper retries later. Access-token material is preserved. Verify the private
   request row has `polling_closed_at` and `material_reaped_at`, and no result is
   `EXCHANGING`, before expecting an idle upgrade to pass.
5. Restart `sudo systemctl start networth-link.service` after completion or any
   refusal, so other pending requests continue polling. Check the worker journal
   and stored poll history; new evidence can reopen a budget hold and requires
   fresh adjudication. A refused closure is not permission to remint.

## Remaining acceptance boundaries

Production starts with zero Items and without pairing. This proves deployment,
not a real account balance on the phone. Completion is enabled in the explicitly selected environment, including Production:
once a request exists its short-lived token must be captured. Task08 owns the
Production mint/release gate. The owner alone opens Link and enters bank credentials/MFA. Task 24 owns signed APK delivery
and installed-phone pairing verification.

The 30-second scan is an admission cadence, **not a proven five-minute exchange
bound**. Existing synthetic archive tests establish that encryption releases the
TokenStore lock. Slow capture, DNS, trickling responses and a stuck same-flow
worker can still delay exchange. No blanket timeout kills a returned credential
before durability. Completing that end-to-end latency acceptance remains WIP;
this deployment does not claim full task16/03a-live/08 acceptance.

Tests exercise real migrated WAL storage, empty Production cycles without any
provider call, publication failure/retry without another sync, pairing rotation,
archive verification/restart/missing-file repair, and independent Link admission.
