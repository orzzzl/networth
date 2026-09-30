import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/main.dart';
import 'package:networth_app/src/data/fetch_diagnostics_store.dart';
import 'package:networth_app/src/data/held_copy_store.dart';
import 'package:networth_app/src/data/history_source.dart';
import 'package:networth_app/src/data/home_load.dart';
import 'package:networth_app/src/data/seq_baseline_store.dart';
import 'package:networth_app/src/data/snapshot_refresh.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/held_copy.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/phone_payload.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';
import 'package:networth_app/src/pairing/pairing_intake.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';

import 'fixtures.dart';

/// Task `21a` — the app being *given* a pairing, driven from the app's own entry
/// point.
///
/// **Every test here pumps [NetWorthApp], not `HomePage`, and issue #111 asks for
/// exactly that**: *"prove the actual app entry point can reach intake and then
/// the real-source path"*. The difference is not ceremony. `HomePage` inside a
/// test-owned `MaterialApp` proves a widget works inside a wrapper the test
/// wrote; intake is reached by a `Navigator.push`, so the navigator, the
/// localizations delegates and the route stack it runs against have to be the
/// app's own. What a `HomePage`-level test cannot fail on is `NetWorthApp`
/// forgetting to pass `intake` down at all, which is the shape of the defect this
/// row exists to close one layer out.
///
/// **Where the boundary honestly is:** `main()` itself is not pumped. It builds
/// `FileHeldCopyStore.appPrivate()` and `AndroidKeystoreStringStore`, both of
/// which need platform channels, so the furthest a widget test reaches is the
/// widget `main()` hands to `runApp` with its collaborators replaced. The wiring
/// *inside* `main()` is held by the compiler instead — `intake` is required on
/// both [NetWorthApp] and `HomePage` — and by `21a`'s installed-build check,
/// which is task 24's release gate and is not a test.
///
/// Nothing here uses a real payload key: every bundle is `fixtures.dart`'s
/// synthetic one.

/// The held copies of *several* pairings, answering per scope.
///
/// The one thing a single-state stub cannot do, and the whole point of it: a
/// replacement must show the new pairing's screen, and a store that answers the
/// same copy whatever `pairingId` it is handed would render an identical screen
/// before and after, passing while proving nothing.
class _ScopedCopies implements HeldCopyStore {
  _ScopedCopies(this.byPairing);

  final Map<String, HeldCopyState> byPairing;

  /// Every scope this store was ever read under, in order.
  final reads = <String>[];

  @override
  Future<HeldCopyState> read(String pairingId) async {
    reads.add(pairingId);
    return byPairing[pairingId] ?? const HeldCopyAbsent();
  }

  @override
  Future<void> hold(String source) async =>
      throw StateError('no test here accepts a payload');
}

class _NoDiagnostics implements FetchDiagnosticsStore {
  @override
  Future<DiagnosticsState> read(String pairingId) async => const DiagnosticsAbsent();

  @override
  Future<void> write(FetchDiagnostics diagnostics) async {}
}

class _NoBaselines implements SeqBaselineStore {
  @override
  Future<BaselineState> read(String pairingId) async => const BaselineAbsent();

  @override
  Future<void> write(SeqBaseline baseline) async {}
}

class _NoHistory implements HistorySource {
  const _NoHistory();

  @override
  Future<NetWorthHistory> load() async => NetWorthHistory.empty;
}

/// A refresh that reads the vault and remembers which pairing it would have
/// fetched under.
///
/// **Modelled on `PairedSnapshotReader`, which is the class this stands in for:**
/// it reads the vault first and the `pairing_id` it gets there is the scope every
/// fact the refresh files is written under. So `seen` is the answer to *"did the
/// refresh after provisioning go to the new pairing"* — the acceptance's
/// *"refresh task 22's real source from the newly stored pairing"* — and it is
/// read off the same collaborator the shipped code reads it off.
///
/// [gateFrom] parks the *n*-th call and every call after it until [release] is
/// called, which is how a fetch is made to be in flight at a chosen moment.
class _VaultReadingRefresher implements SnapshotRefreshing {
  _VaultReadingRefresher(this.vault, {this.gateFrom});

  final PairingVault vault;
  final int? gateFrom;

  /// The `pairing_id` each refresh read, `null` for a call that found no pairing.
  final seen = <String?>[];

  final _gate = Completer<void>();

  void release() => _gate.complete();

  @override
  Future<SnapshotRefreshOutcome> refresh() async {
    final provision = await vault.read();
    seen.add(provision?.pairingId);
    if (gateFrom != null && seen.length >= gateFrom!) {
      await _gate.future;
    }
    // Never an acceptance: these tests put copies in the store directly, and a
    // refresher claiming to have fetched one would be `HomeLoader`'s second
    // source for a single fact.
    return provision == null
        ? const RefreshNotAttempted(NoPairing.notPaired)
        : const RefreshKeptHeldCopy(RefreshRefusal.hostHasNoPublication);
  }
}

/// Protected storage that counts, and can refuse or stall the write.
///
/// One store behind both the intake's vault and the loader's, because on the
/// phone there is one Android keystore — and because the fact under test in half
/// of these is precisely that what intake wrote is what the next load reads.
class _CountingStore implements SecureStringStore {
  _CountingStore({this.stored, this.failWrites = false, this.stallWrites = false});

  String? stored;
  final bool failWrites;
  final bool stallWrites;

  int writes = 0;
  final _stall = Completer<void>();

  void releaseWrite() => _stall.complete();

  @override
  Future<String?> read({required String key}) async => stored;

  @override
  Future<void> write({required String key, required String value}) async {
    writes += 1;
    if (stallWrites) {
      await _stall.future;
    }
    if (failWrites) {
      throw const KeystoreUnavailable();
    }
    stored = value;
  }

  @override
  Future<void> delete({required String key}) async => stored = null;
}

/// One app, one keystore, one set of scoped stores — assembled the way
/// `main.dart` assembles them.
///
/// **The vault is shared rather than duplicated, and that is the fact under
/// test in most of this file.** `main.dart` hoists one `PairingVault` so intake's
/// write and the next load's read cross the same object; a helper that built two
/// would make every test here pass over an app whose form provisions a vault
/// nothing reads.
class _Harness {
  _Harness({
    required this.store,
    Map<String, HeldCopyState>? copies,
    int? gateRefreshFrom,
  })  : vault = PairingVault(store: store),
        scopedCopies = _ScopedCopies(copies ?? const {}) {
    refresher = _VaultReadingRefresher(vault, gateFrom: gateRefreshFrom);
  }

  final _CountingStore store;
  final PairingVault vault;
  final _ScopedCopies scopedCopies;
  late final _VaultReadingRefresher refresher;

  Widget get app => NetWorthApp(
        loader: HomeLoader(
          vault: vault,
          refresher: refresher,
          heldCopies: scopedCopies,
          diagnostics: _NoDiagnostics(),
          baselines: _NoBaselines(),
        ),
        historySource: const _NoHistory(),
        intake: PairingIntake(vault: vault),
      );
}

/// What `known.json` renders as, and what `static_only.json` renders as.
///
/// Two different figures on purpose: the replacement tests tell the two pairings
/// apart by the total on screen, and two scopes holding the same number would be
/// indistinguishable exactly where the distinction is the assertion.
const totalOfKnown = r'$42,500.00';
const totalOfStaticOnly = r'$680,000.00';

const notPairedYet = "this phone isn't paired yet";
const nothingSaved = 'nothing saved on this phone yet';
const notAPairingLine = "that isn't a pairing line this app can read, so nothing changed";
const couldNotSave = "this phone couldn't save the pairing, so it isn't paired now — try again";

HeldCopyState copyOf(String fixture) =>
    HeldCopyHeld(PhonePayload.fromJsonString(readFixture(fixture)));

/// Walks the app from wherever it is into the intake form.
///
/// Found by key rather than by copy, so the wording stays free to change.
Future<void> openIntake(WidgetTester tester, {required bool replacing}) async {
  await tester.tap(
    find.byKey(Key(replacing ? 'replace-pairing' : 'open-pairing-intake')),
  );
  await tester.pumpAndSettle();
}

Future<void> typeAndSubmit(WidgetTester tester, String bundle) async {
  await tester.enterText(find.byKey(const Key('pairing-bundle-field')), bundle);
  await tester.tap(find.byKey(const Key('pairing-submit')));
  await tester.pumpAndSettle();
}

/// Submit, then advance time by a bounded amount instead of settling.
///
/// **For the gated-refresh test, and `pumpAndSettle` cannot serve it.** What that
/// test is about is the frame where a load has not resolved, and an unresolved
/// load renders a `CircularProgressIndicator` — an animation that never ends, so
/// `pumpAndSettle` waits for a quiescence that by construction never comes and
/// fails as a timeout rather than as the assertion. A second's worth of frames is
/// enough for the write, the pop transition and the `setState`, and is deliberately
/// not enough for anything the test then claims did not happen.
Future<void> typeAndSubmitWithoutSettling(WidgetTester tester, String bundle) async {
  await tester.enterText(find.byKey(const Key('pairing-bundle-field')), bundle);
  await tester.tap(find.byKey(const Key('pairing-submit')));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 500));
  await tester.pump(const Duration(milliseconds: 500));
}

void main() {
  testWidgets('a fresh install can be paired through the app it ships as', (tester) async {
    final harness = _Harness(
      store: _CountingStore(),
      copies: {pairingId: copyOf(knownFixture)},
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();

    // Where an unpaired phone starts, and where it stayed forever before `21a`.
    expect(find.text(notPairedYet), findsOneWidget);
    expect(find.byKey(const Key('open-pairing-intake')), findsOneWidget);
    // No replace action: there is nothing to replace, and offering to replace a
    // pairing that does not exist contradicts the sentence above it.
    expect(find.byKey(const Key('replace-pairing')), findsNothing);

    await openIntake(tester, replacing: false);
    await typeAndSubmit(tester, encoded);

    // The bundle reached protected storage, exactly once, in the canonical form.
    expect(harness.store.writes, 1);
    expect(harness.store.stored, encoded);

    // **And the screen is now built from it.** This is the half that a vault test
    // cannot reach: the load ran again, and the total on screen is the one held
    // under the pairing that was just typed in.
    expect(find.text(notPairedYet), findsNothing);
    expect(find.text(totalOfKnown), findsOneWidget);

    // The refresh that followed went to the *new* pairing — the acceptance's
    // "refresh task 22's real source from the newly stored pairing". The first
    // call found none, which is what makes the second one evidence.
    expect(harness.refresher.seen, [null, pairingId]);
    expect(harness.scopedCopies.reads, [pairingId]);
  });

  testWidgets('text the parser refuses leaves the pairing it had, and says so', (tester) async {
    final harness = _Harness(
      store: _CountingStore(stored: encoded),
      copies: {pairingId: copyOf(knownFixture)},
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();
    expect(find.text(totalOfKnown), findsOneWidget);

    await openIntake(tester, replacing: true);
    await typeAndSubmit(tester, unparseableBundle);

    // **Nothing was written.** `PairingVault.provision` parses before it writes,
    // which is what makes "the prior pairing is intact" a property rather than a
    // hope — and the count, not just the stored value, is what says so: a write
    // of the same bytes would leave `stored` looking untouched.
    expect(harness.store.writes, 0);
    expect(harness.store.stored, encoded);

    // Still on the form, with the refusal on it. A failure that navigated away
    // would be a screen saying nothing happened while looking like it had.
    expect(find.byKey(const Key('pairing-bundle-field')), findsOneWidget);
    expect(find.text(notAPairingLine), findsOneWidget);
    // Not the other failure's sentence: the two send the owner to different acts.
    expect(find.text(couldNotSave), findsNothing);

    // And no refresh followed. A refusal is not a reason to go and fetch.
    expect(harness.refresher.seen, [pairingId]);
  });

  testWidgets('what the submitted text was never appears on screen', (tester) async {
    // The field holds a payload key. The two failure messages are where an
    // echo would most naturally have been written — "«...» is not a pairing
    // line" is the ordinary shape of a validation message — and a screenshot of
    // one would then carry the secret. Asserted over the whole rendered surface
    // rather than over the one `Text` that shows the refusal.
    final harness = _Harness(store: _CountingStore(stored: encoded));
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();

    await openIntake(tester, replacing: true);
    // Refused by `parse`, and shaped like a real bundle so that an echo of it
    // would be unmistakable — and so that a substring of it cannot match by
    // accident against ordinary copy.
    const typed = 'networth-pairing:v1:not-a-uuid:$encodedKey:$tailnetName';
    await typeAndSubmit(tester, typed);
    expect(find.text(notAPairingLine), findsOneWidget);

    for (final line in renderedText(tester, find.byType(NetWorthApp))) {
      for (final leak in [encodedKey, 'not-a-uuid', tailnetName, 'networth-pairing']) {
        expect(
          line,
          isNot(contains(leak)),
          reason: 'submitted material reached the screen: "$line"',
        );
      }
    }
  });

  testWidgets('a keystore that will not keep it never reports a pairing', (tester) async {
    final harness = _Harness(store: _CountingStore(failWrites: true));
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();
    expect(find.text(notPairedYet), findsOneWidget);

    await openIntake(tester, replacing: false);
    // A **valid** bundle: the subject is the write, so the parse must not be
    // what fails. This is the distinction the three-outcome type exists for.
    await typeAndSubmit(tester, encoded);

    expect(harness.store.writes, 1);
    expect(harness.store.stored, isNull);

    // Its own sentence, not the parser's — sending the owner to re-check a
    // 100-character secret that was already correct is the wrong instruction.
    expect(find.text(couldNotSave), findsOneWidget);
    expect(find.text(notAPairingLine), findsNothing);
    // An explicit recoverable state: still on the form, still able to retry.
    expect(find.byKey(const Key('pairing-bundle-field')), findsOneWidget);
    expect(find.byKey(const Key('pairing-submit')), findsOneWidget);
    // And no refresh: a storage failure is not a pairing.
    expect(harness.refresher.seen, [null]);
  });

  testWidgets('dismissing the form pairs nothing, even with a valid bundle typed in', (tester) async {
    final harness = _Harness(store: _CountingStore());
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();

    await openIntake(tester, replacing: false);
    await tester.enterText(find.byKey(const Key('pairing-bundle-field')), encoded);
    // Backing out without submitting. Typed-but-not-submitted is the state a
    // saved draft would survive, which is why there is no `restorationId`.
    await tester.pageBack();
    await tester.pumpAndSettle();

    expect(harness.store.writes, 0);
    expect(harness.store.stored, isNull);
    expect(find.text(notPairedYet), findsOneWidget);
    // **The load did not run again.** A screen that reloaded on every return
    // from the form would put an unpaired phone through a fetch it has no key
    // for and record the result as an attempt.
    expect(harness.refresher.seen, [null]);
  });

  testWidgets('two taps on submit are one provision', (tester) async {
    // The button is disabled while a submission is in flight *and* the handler
    // returns early, and this test is about the second half.
    //
    // **There is deliberately no `pump` between the two taps, and the first
    // version of this test had one.** `_submit` sets `_submitting` synchronously,
    // so a pump makes the button `onPressed: null` and the second tap lands on a
    // disabled widget — the test then passes with the early return deleted,
    // because a *different* guard satisfied it. Delivering both taps inside one
    // frame is what the early return is for, and it is the only arrangement in
    // which its absence is observable.
    final harness = _Harness(store: _CountingStore(stallWrites: true));
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();

    await openIntake(tester, replacing: false);
    await tester.enterText(find.byKey(const Key('pairing-bundle-field')), encoded);
    await tester.tap(find.byKey(const Key('pairing-submit')));
    await tester.tap(find.byKey(const Key('pairing-submit')), warnIfMissed: false);
    await tester.pump();

    expect(harness.store.writes, 1, reason: 'the second tap started a second write');

    harness.store.releaseWrite();
    await tester.pumpAndSettle();
    expect(harness.store.writes, 1);
  });

  testWidgets('the protected write is awaited before the source is refreshed', (tester) async {
    final harness = _Harness(
      store: _CountingStore(stallWrites: true),
      copies: {pairingId: copyOf(knownFixture)},
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();
    expect(harness.refresher.seen, [null]);

    await openIntake(tester, replacing: false);
    await tester.enterText(find.byKey(const Key('pairing-bundle-field')), encoded);
    await tester.tap(find.byKey(const Key('pairing-submit')));
    await tester.pump();

    // **Mid-write.** The write has started and has not returned, so the vault
    // has no pairing yet. A refresh here would read the vault, find nothing, and
    // file a "not attempted" against a phone that is in the middle of pairing.
    expect(harness.store.writes, 1);
    expect(harness.refresher.seen, [null], reason: 'refreshed before the write returned');

    harness.store.releaseWrite();
    await tester.pumpAndSettle();

    // Only now, and reading the pairing the write left behind.
    expect(harness.refresher.seen, [null, pairingId]);
    expect(find.text(totalOfKnown), findsOneWidget);
  });

  testWidgets('replacing a pairing moves the screen to the new scope', (tester) async {
    final harness = _Harness(
      store: _CountingStore(stored: encoded),
      copies: {
        pairingId: copyOf(knownFixture),
        replacementPairingId: copyOf(staticOnlyFixture),
      },
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();
    expect(find.text(totalOfKnown), findsOneWidget);

    await openIntake(tester, replacing: true);
    // The warning only a replacement gets: the old pairing's saved snapshot
    // stops being shown, which is a consequence he cannot see coming.
    expect(
      find.textContaining('the snapshot saved under the old pairing stops being shown'),
      findsOneWidget,
    );
    await typeAndSubmit(tester, replacementEncoded);

    expect(harness.store.stored, replacementEncoded);
    // **The new scope's figure, and not the old one.** Two different totals is
    // what makes this an assertion rather than a coincidence.
    expect(find.text(totalOfStaticOnly), findsOneWidget);
    expect(find.text(totalOfKnown), findsNothing);
    expect(harness.refresher.seen, [pairingId, replacementPairingId]);
    // The old pairing's records were never re-read under the new one's name.
    expect(harness.scopedCopies.reads, [pairingId, replacementPairingId]);
  });

  testWidgets('history is not erased as a shortcut: replacing rescopes, it does not delete', (tester) async {
    // The acceptance asks for this in as many words — *"preserve the reviewed
    // pairing-scoped history behavior; do not erase history as an intake
    // shortcut"*. The tempting implementation of "the old copy must not show"
    // is to clear the stores on provision; the reviewed behaviour is that
    // records are read under a `pairing_id` and a new pairing simply has none of
    // its own yet. The observable difference is whether the old scope still
    // answers, so that is what is asked.
    final harness = _Harness(
      store: _CountingStore(stored: encoded),
      copies: {pairingId: copyOf(knownFixture)},
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();

    await openIntake(tester, replacing: true);
    await typeAndSubmit(tester, replacementEncoded);

    // The new pairing holds nothing, which is the honest screen for it.
    expect(find.text(nothingSaved), findsOneWidget);
    // And the old pairing's copy is still there, under its own scope.
    expect(
      await harness.scopedCopies.read(pairingId),
      isA<HeldCopyHeld>(),
      reason: 'the old pairing lost its copy, so intake deleted rather than rescoped',
    );
  });

  testWidgets('a refresh still in flight cannot leave the old pairing on the new screen', (tester) async {
    // **The case the acceptance calls "a pending old fetch across replacement",
    // and the window is real rather than theoretical: it is the whole time
    // between the provision returning and the reload resolving.** Gate the
    // refresh that follows the replacement and the phone is paired to B while a
    // fetch is outstanding; what must not be on screen in that window is A's
    // total. A number belonging to a relationship that has ended is the one
    // thing this product exists not to render.
    final harness = _Harness(
      store: _CountingStore(stored: encoded),
      copies: {
        pairingId: copyOf(knownFixture),
        replacementPairingId: copyOf(staticOnlyFixture),
      },
      // The first refresh runs, so the screen paints A and the replace action is
      // reachable; the second one — the one the replacement triggers — parks.
      gateRefreshFrom: 2,
    );
    await tester.pumpWidget(harness.app);
    await tester.pumpAndSettle();
    expect(find.text(totalOfKnown), findsOneWidget);

    await openIntake(tester, replacing: true);
    await typeAndSubmitWithoutSettling(tester, replacementEncoded);

    // Mid-reload: the vault already holds B and the refresh for it has not
    // returned.
    expect(harness.store.stored, replacementEncoded);
    expect(harness.refresher.seen, [pairingId, replacementPairingId]);
    expect(
      find.text(totalOfKnown),
      findsNothing,
      reason: "the old pairing's total survived the replacement it ended",
    );
    expect(find.byType(CircularProgressIndicator), findsOneWidget);
    // Nor may the action that belongs to a resolved paired screen be offered
    // over a load that has not resolved.
    expect(find.byKey(const Key('replace-pairing')), findsNothing);

    harness.refresher.release();
    await tester.pumpAndSettle();
    expect(find.text(totalOfStaticOnly), findsOneWidget);
  });
}
