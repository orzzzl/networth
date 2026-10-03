# VPS runtime

Task 16 composes the merged workers and installs them on the provisioned host.
The first-install script requires a full commit that has passed CI and merged:

```sh
sudo bash scripts/install-runtime.sh <full-merged-commit>
```

Run it on `tokyo-exit` over the existing agent SSH key. It installs hash-pinned
runtime/build dependencies, root-owned code in `/opt/networth/releases/<commit>`,
`/opt/networth/current`, executable wrappers in `/usr/local/bin`, and the exact
backup forced-command source at `/usr/local/lib/networth/backup-ssh-dispatch`.
It preserves existing credentials and refuses a different existing runtime or
configuration rather than stopping a possibly active exchange. Runtime upgrades
need an explicit worker drain; this is intentionally a first-install script.

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

## Remaining acceptance boundaries

Production starts with zero Items and without pairing. This proves deployment,
not a real account balance on the phone. The existing Sandbox-only Link execution
guard stays in place until task 08 supplies its Production release gate; existing
Production rows would report a fixed refusal instead of exchanging. The owner
alone opens Link and enters bank credentials/MFA. Task 24 owns signed APK delivery
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
