# networth_app

The Android app. Read-only: it holds no Plaid token, never calls Plaid, and
renders one published snapshot.

## The one rule this app exists to keep

**No total is ever rendered without its age.** That is invariant I2/I4 in
`../DESIGN.md`, and it is kept structurally rather than by review:

- `DatedTotal` (`lib/src/domain/dated_total.dart`) is a sealed type whose every
  constructor requires an age state, so there is no value representing "an
  amount" on its own.
- `Headline` builds its annotation with an exhaustive `switch` over that type, so
  a new age state added later fails to compile here instead of rendering blank.
- `UndatableTotal` has **no date field at all**. §8.1 R3 keeps
  `oldest_known_source_as_of` as a diagnostic and forbids showing it as the
  total's age; here the date never reaches the object the UI holds, so the
  plausible wrong implementation is not available to write.

The two staleness dimensions (I4) are separate types rendered as separate rows:
how old the *institutions'* data is, and how old *this phone's copy* is. They are
never reduced to one badge.

## Where the data comes from

`SnapshotSource` (`lib/src/data/snapshot_source.dart`) is the seam. The only
implementation today reads a bundled fixture, so the screen can be built and
tested against the real payload shape without waiting on the daemon's HTTP route
(task 20) or the transport (task 28).

The fixtures in `assets/fixtures/` carry the exact key set that
`networth/publisher.py::_plaintext` emits, and `tests/test_app_fixtures.py` in
the Python suite fails if the two drift apart. All figures in them are synthetic.

## Running it

```sh
flutter test          # 49 tests, no device needed
flutter analyze
flutter run            # Android only — there is no iOS branch by design (O1)
```
