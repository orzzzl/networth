# 07a polling evidence and adjudication

This component implements the first success-observation writer and its required
operator adjudication command together. It does not install an automatic worker
or complete 07a; 07b remains blocked. All validation uses synthetic evidence.

`ingest_poll` consumes the existing typed `LinkSessionPoll`, retaining every
identified session and successful token. Result UUIDs commit before transient
`Secret` token handles return. A request-keyed, domain-separated HMAC associates
replayed tokens with those UUIDs; repeated, duplicate and reordered replies do
not create another result. Tokens remain memory-only. A missing, reaped,
unreadable or unverified link-token key creates an unresolved observation and
no result UUID. A migrated result without a digest also creates an attribution
hold: absence of a digest is not evidence that a polled token is new.

Session timestamps are observed, never inferred from mint time. A later observed
finish fills that session's result deadlines, even if the later reply carries
no token; a missing timestamp cannot erase an older observation. Conflicting
clocks or cross-session token attribution refuse atomically. Empty/absent/null
session arrays never close or reap a request. Exits cannot erase earlier success
or another session. The persistence surface is explicit: no response dictionaries,
accounts, institution metadata, public tokens or unkeyed token hashes enter SQL.

## Observation sets and explicit adjudication

An open observation groups unidentified successes by request, session (when
known), and reason. It is a set of evidence, **not a claim of one spent slot**.
Migration 0007 retains the highest reported result count and latest observation
time; the count is diagnostic, never a substitute for adjudication. Legacy
observations keep an unknown count until a later observation provides a bound.

The first implementation exposed a necessary consequence of the approved
`additional_slots` projection: a resolved unknown slot followed by recovered
token identity must not become both an adjudicated slot and a result slot.
Therefore **any later success evidence for a request reopens its resolved
observation sets**, including when the later evidence is fully identifiable.
The budget is unavailable until explicit review establishes the new additional
cost. Reopening retains every earlier decision in `link_observation_adjudication`;
the observation's resolution fields project only the current decision. Migration
0007 seeds the ledger from existing resolved observations. Repeated polls while
a hold is open update it rather than multiplying holds. Identical-looking
unidentified evidence after adjudication cannot prove it is already accounted
for, so it also reopens the hold.

This is deliberately conservative: an operator must quiesce polling before
adjudication, or the next successful poll reopens the reviewed sets. Claude must
review this concrete accounting boundary before worker integration; it is not a
new automatic right to infer zero slots or clear any hold.

The local command is `networth adjudicate-link-observation`, requiring explicit
`NETWORTH_ENV`, `--observation`, `--additional-slots`, `--audit-id`, and
`--confirm-reviewed`. The last flag attests that polling is quiesced and the
whole unresolved set has been reviewed against Items, results, other observations
and prior decisions. The audit UUID identifies a private review record kept
outside the public repository. The command accepts no credential or free-form
evidence, opens only the selected existing database, and never calls Plaid or
reads its credentials. Host filesystem access is its authorization boundary;
it adds no remotely callable endpoint. The caller must already be authorized
to adjudicate the slot outcome. Missing confirmation, negative counts, malformed
UUIDs, nonexistent/already-resolved observations and database errors refuse.

Zero additional slots means all this evidence is already counted elsewhere.
Positive additional slots represent independently established spent capacity
beyond all other current evidence. Other unresolved holds still refuse the
budget. Current resolution and appended audit decision commit together; a ledger
write failure leaves the observation unresolved. Neither the command nor this
component deletes credentials, changes result states, or permits an exchange.

## Integration still required

The request minter/second-copy attestation, polling loop, all-candidate legacy
credential reconciliation, durable material holds, conditional exchange claims,
identifier capture, finalization orchestration and reaper remain 07a work. A
returned token handle is not exchange permission. The worker must inspect all
persisted holds and credential candidates, respect each observed deadline, and
conditionally claim the result before sending. The reaper must share ingestion's
SQLite write lock when removing a request's digest key; a late poll after reaping
still records a hold. The worker must not poll a request whose polling is closed
or whose material has been reaped. No 06a flow is rerun and its heartbeat remains PAUSED.
