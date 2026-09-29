import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/main.dart';
import 'package:networth_app/src/data/fetch_diagnostics_store.dart';
import 'package:networth_app/src/data/held_copy_store.dart';
import 'package:networth_app/src/data/history_source.dart';
import 'package:networth_app/src/data/history_store.dart';
import 'package:networth_app/src/data/home_load.dart';
import 'package:networth_app/src/data/recording_refresh.dart';
import 'package:networth_app/src/data/seq_baseline_store.dart';
import 'package:networth_app/src/data/snapshot_refresh.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/held_copy.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';
import 'package:networth_app/src/ui/headline.dart';
import 'package:networth_app/src/ui/home_page.dart';

import 'fixtures.dart';

/// What a phone that has recorded nothing has: the first-launch state of
/// `FileHistoryStore`, without its file.
///
/// A stub rather than the shipped store, for one mechanical reason: the store
/// does real file I/O and `pumpAndSettle` runs on fake time, so a widget test
/// pointed at a real file hangs rather than failing. `history_store_test.dart`
/// is where the store itself is tested, on real bytes; these tests are about
/// the screen and need the series only to be absent.
class _NoHistory implements HistorySource {
  const _NoHistory();

  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;
}

/// Reads fine, writes never — a full disk. The two halves of the record fail
/// independently, and this is the half nothing on screen used to report.
class _UnwritableStore implements HistoryStore {
  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;

  @override
  Future<void> record(PhonePayload payload) async =>
      throw const FileSystemException('no space left on device');
}

/// The three stores, in memory, each answering whatever the test put in it.
///
/// In memory for `_NoHistory`'s reason — a widget test pointed at a real file
/// hangs on fake time — and *stores* rather than a stubbed [HomeLoader] because
/// the loader is half of what these tests are about. `home_load_test.dart`
/// drives the same class over the real files and a real socket; here it runs
/// over memory so the screen is what varies.
class _MemoryCopies implements HeldCopyStore {
  _MemoryCopies(this.state);

  HeldCopyState state;

  @override
  Future<HeldCopyState> read(String pairingId) async => state;

  @override
  Future<void> hold(String source) async =>
      state = HeldCopyHeld(PhonePayload.fromJsonString(source));
}

class _MemoryDiagnostics implements FetchDiagnosticsStore {
  _MemoryDiagnostics([this.state = const DiagnosticsAbsent()]);

  DiagnosticsState state;

  @override
  Future<DiagnosticsState> read(String pairingId) async => state;

  @override
  Future<void> write(FetchDiagnostics diagnostics) async =>
      state = DiagnosticsHeld(diagnostics);
}

class _MemoryBaselines implements SeqBaselineStore {
  _MemoryBaselines([this.state = const BaselineAbsent()]);

  BaselineState state;

  @override
  Future<BaselineState> read(String pairingId) async => state;

  @override
  Future<void> write(SeqBaseline baseline) async => state = BaselineHeld(baseline);
}

/// A refresh that does nothing and says so, for the tests where the copy on
/// disk is the subject.
///
/// [RefreshNotAttempted] rather than a success: these tests put the copy in the
/// store directly, and a refresher claiming to have fetched one would be the
/// second source for that fact that `HomeLoader` exists to prevent.
class _QuietRefresher implements SnapshotRefreshing {
  int calls = 0;

  @override
  Future<SnapshotRefreshOutcome> refresh() async {
    calls += 1;
    return const RefreshNotAttempted(NoPairing.notPaired);
  }
}

/// A refresh that never returns, so the loading frame can be inspected.
class _PendingRefresher implements SnapshotRefreshing {
  @override
  Future<SnapshotRefreshOutcome> refresh() => Completer<SnapshotRefreshOutcome>().future;
}

/// A refresh that breaks [SnapshotRefreshing]'s never-throws contract.
///
/// The only way to reach the screen's error branch now that every ordinary
/// failure is a [HomeLoad] value — which is the point: that branch is a
/// backstop for a broken collaborator, not the screen an unpaired phone lands
/// on.
class _ThrowingRefresher implements SnapshotRefreshing {
  @override
  Future<SnapshotRefreshOutcome> refresh() async => throw StateError('no payload');
}

/// The loader the app ships, over in-memory collaborators.
///
/// `store` defaults to a vault holding the synthetic pairing bundle, so the
/// common case is a paired phone; passing an empty one produces `HomeNotPaired`
/// from the real `PairingVault` rather than from a stub of it.
HomeLoader loaderOver({
  HeldCopyState copy = const HeldCopyAbsent(),
  SnapshotRefreshing? refresher,
  SecureStringStore? store,
  DiagnosticsState diagnostics = const DiagnosticsAbsent(),
  BaselineState baseline = const BaselineAbsent(),
}) =>
    HomeLoader(
      vault: PairingVault(store: store ?? MemoryPairingStore(encoded)),
      refresher: refresher ?? _QuietRefresher(),
      heldCopies: _MemoryCopies(copy),
      diagnostics: _MemoryDiagnostics(diagnostics),
      baselines: _MemoryBaselines(baseline),
    );

/// The copy a paired phone is holding, parsed from the fixture the other tests
/// use for the same figures.
HeldCopyState heldCopy(String fixture) =>
    HeldCopyHeld(PhonePayload.fromJsonString(readFixture(fixture)));

void main() {
  testWidgets('a held copy reaches the screen end to end', (tester) async {
    await tester.pumpWidget(
      localized(
        HomePage(
          loader: loaderOver(copy: heldCopy(knownFixture)),
          historySource: const _NoHistory(),
          clock: () => DateTime.utc(2026, 9, 15, 12),
        ),
        scaffold: false,
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text(r'$42,500.00'), findsOneWidget);
    expect(find.text('as of Sep 14, 2026, 20:15 UTC'), findsOneWidget);
  });

  testWidgets('while loading there is no headline at all, bare or otherwise', (tester) async {
    // "No intermediate state renders a bare headline" is the criterion. The
    // strongest form of it is that the amount and its age arrive together or not
    // at all — there is no frame in which one is on screen without the other.
    await tester.pumpWidget(
      localized(
        HomePage(
          loader: loaderOver(
            copy: heldCopy(knownFixture),
            refresher: _PendingRefresher(),
          ),
          historySource: const _NoHistory(),
        ),
        scaffold: false,
      ),
    );
    await tester.pump();

    expect(find.byType(Headline), findsNothing);
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    expect(find.textContaining(r'$'), findsNothing);
  });

  testWidgets('a broken collaborator is a refusal, not a number', (tester) async {
    await tester.pumpWidget(
      localized(
        HomePage(
          loader: loaderOver(refresher: _ThrowingRefresher()),
          historySource: const _NoHistory(),
        ),
        scaffold: false,
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsNothing);
    expect(find.text("couldn't read the published snapshot"), findsOneWidget);
  });

  testWidgets('and the failure never shows the owner the exception text', (tester) async {
    // PR #77 re-review blocker 2. The screen rendered `'${snapshot.error}'`
    // under the summary, so a `StateError` put "Bad state: no payload" in front
    // of the owner — past the i18n layer, in a shape nobody chose, and with no
    // bound on what an exception's message might contain.
    //
    // Asserted on the whole rendered surface rather than on the one widget that
    // used to carry it: the defect is internal text reaching the screen, not one
    // particular `Text`.
    await tester.pumpWidget(
      localized(
        HomePage(
          loader: loaderOver(refresher: _ThrowingRefresher()),
          historySource: const _NoHistory(),
        ),
        scaffold: false,
      ),
    );
    await tester.pumpAndSettle();

    final rendered = renderedText(tester, find.byType(HomePage));
    expect(rendered, isNotEmpty);
    for (final line in rendered) {
      for (final leak in ['Bad state', 'no payload', 'Exception', 'Error']) {
        expect(
          line,
          isNot(contains(leak)),
          reason: 'internal error text reached the screen: "$line"',
        );
      }
    }
  });

  group('every state a phone can be in reaches its own sentence', () {
    /// One screenful per state, with the text it renders.
    ///
    /// **Driven through the whole screen rather than through a mapping
    /// function**, because the defect this guards against is a *branch* — a
    /// sentence computed correctly and rendered by a case nobody looked at, or
    /// two cases wired to one string. Each entry below is the case, and the
    /// group's last test is the part a per-case assertion cannot make: that the
    /// six are six.
    final cases = <String, (HomeLoader, String)>{
      'no pairing': (
        loaderOver(store: MemoryPairingStore(null)),
        "this phone isn't paired yet",
      ),
      'a pairing that cannot be read': (
        loaderOver(store: FailingPairingStore()),
        "couldn't read this phone's pairing",
      ),
      'paired and holding nothing': (
        loaderOver(copy: const HeldCopyAbsent()),
        'nothing saved on this phone yet',
      ),
      'a damaged copy': (
        loaderOver(copy: const HeldCopyUnreadable('schema_version is not a number')),
        'the copy saved on this phone is damaged',
      ),
      'a copy that could not be opened': (
        loaderOver(copy: const HeldCopyNotRead('permission denied')),
        "couldn't open the copy saved on this phone",
      ),
      'a copy from another build': (
        loaderOver(
          copy: const HeldCopyOutdated(storedVersion: '2', readableVersion: '1'),
        ),
        'the copy saved on this phone was written by a different version of the app',
      ),
    };

    for (final entry in cases.entries) {
      testWidgets(entry.key, (tester) async {
        final (loader, sentence) = entry.value;
        await tester.pumpWidget(
          localized(
            HomePage(loader: loader, historySource: const _NoHistory()),
            scaffold: false,
          ),
        );
        await tester.pumpAndSettle();

        expect(find.text(sentence), findsOneWidget);
        // None of these is a screenful with a number on it. A state that has no
        // payload must not be able to leave the previous one's total standing.
        expect(find.byType(Headline), findsNothing);
      });
    }

    testWidgets('and no two of them say the same thing', (tester) async {
      // **Read off the screen, not off the table above.** The obvious version of
      // this test asserts that the six *expected* strings differ, which compares
      // the table with itself and would pass over an app that rendered one
      // sentence for all six. So each state is pumped and what it actually put
      // on screen is what gets compared — the collapse `held_copy.dart` argues
      // against (a missing copy read as a damaged one, a damaged one as a
      // never-fetched one) is a property of the app, so it has to be measured
      // there.
      //
      // `findsOneWidget` in the six tests above cannot catch it either: two
      // states are never on screen at the same time.
      final rendered = <String, List<String>>{};
      for (final entry in cases.entries) {
        await tester.pumpWidget(
          localized(
            HomePage(
              // **A distinct key per case, and it is not decoration.** Without
              // one, pumping a second `HomePage` into the same tester reuses the
              // first one's `State` — `initState` does not run again, the future
              // from the previous case is still the one being awaited, and every
              // case after the first renders the first one's screen. The first
              // run of this test failed exactly that way, which is the evidence
              // that it reads the screen rather than the table.
              key: ValueKey(entry.key),
              loader: entry.value.$1,
              historySource: const _NoHistory(),
            ),
            scaffold: false,
          ),
        );
        await tester.pumpAndSettle();
        rendered[entry.key] = renderedText(tester, find.byType(HomePage));
        // A screenful with nothing on it would be "distinct" from the other
        // five for free, which is the way this check fails open.
        expect(rendered[entry.key], isNotEmpty, reason: 'no text at all for ${entry.key}');
      }

      final seen = <String, String>{};
      for (final entry in rendered.entries) {
        final key = entry.value.join(' ');
        expect(
          seen,
          isNot(contains(key)),
          reason: '${entry.key} renders exactly what ${seen[key]} renders',
        );
        seen[key] = entry.key;
      }
    });
  });

  group('a record that cannot be written reaches the screen', () {
    const notRecorded = "this reading couldn't be saved, so it won't appear in the history";

    testWidgets('from the wiring a caller actually writes', (tester) async {
      // **End to end, and that is the point of putting it here.** The widget
      // tests cover what `HistoryCurve` renders given the flag; this covers
      // whether the flag arrives, which is the half that was wrong twice. The
      // first fix passed the recording outcome to `HomePage` as its own
      // optional argument — so this exact wiring, a recorder handed straight in,
      // silently reported success. The reviewer's probe was written this way
      // before the defect existed, which is the evidence that it is the shape a
      // caller reaches for.
      final store = _UnwritableStore();
      final copies = _MemoryCopies(const HeldCopyAbsent());
      await tester.pumpWidget(
        localized(
          HomePage(
            loader: HomeLoader(
              vault: PairingVault(store: MemoryPairingStore(encoded)),
              refresher: RecordingSnapshotRefresher(
                inner: _AcceptingRefresher(copies, knownFixture),
                store: store,
              ),
              heldCopies: copies,
              diagnostics: _MemoryDiagnostics(),
              baselines: _MemoryBaselines(),
            ),
            historySource: store,
            clock: () => DateTime.utc(2026, 9, 15, 12),
          ),
          scaffold: false,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.byType(Headline), findsOneWidget,
          reason: 'a store that will not write must not take the total off the screen');
      expect(find.text(notRecorded), findsOneWidget);
      expect(find.text('no readings recorded yet'), findsNothing);
    });

    testWidgets('and a build that records nothing claims no failure', (tester) async {
      // The control, and a real state: a refresher that is not a
      // `RecordingStatus` records nothing at all. "Attempted nothing" must
      // render as silence rather than as a warning about the owner's record.
      await tester.pumpWidget(
        localized(
          HomePage(
            loader: loaderOver(copy: heldCopy(knownFixture)),
            historySource: const _NoHistory(),
            clock: () => DateTime.utc(2026, 9, 15, 12),
          ),
          scaffold: false,
        ),
      );
      await tester.pumpAndSettle();

      expect(find.text(notRecorded), findsNothing);
      expect(find.text('no readings recorded yet'), findsOneWidget);
    });
  });

  testWidgets('the app boots', (tester) async {
    await tester.pumpWidget(
      NetWorthApp(
        loader: loaderOver(copy: heldCopy(mixedFixture)),
        historySource: const _NoHistory(),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byType(Headline), findsOneWidget);
  });
}

/// A refresh that accepts a payload: it writes the copy and hands the payload
/// back, which is what makes the recorder above record.
class _AcceptingRefresher implements SnapshotRefreshing {
  _AcceptingRefresher(this._copies, this._fixture);

  final _MemoryCopies _copies;
  final String _fixture;

  @override
  Future<SnapshotRefreshOutcome> refresh() async {
    final source = readFixture(_fixture);
    await _copies.hold(source);
    return RefreshAccepted(PhonePayload.fromJsonString(source));
  }
}
