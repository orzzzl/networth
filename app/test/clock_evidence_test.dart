import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/clock_anchor_store.dart';
import 'package:networth_app/src/data/clock_continuity_source.dart';
import 'package:networth_app/src/domain/clock_anchor.dart';
import 'package:networth_app/src/domain/clock_continuity.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/domain/publication_seq.dart';
import 'package:networth_app/src/domain/seq_baseline.dart';

/// A monotonic source under the test's control, standing in for the platform
/// channel task `22` still owes.
class _FakeSource implements ClockContinuitySource {
  _FakeSource({this.reading, this.error, this.onRead});

  MonotonicReading? reading;
  Object? error;

  /// Runs before the reading is returned, so a test can land a fetch in the
  /// window between the two reads.
  final Future<void> Function()? onRead;

  @override
  Future<MonotonicReading?> read() async {
    await onRead?.call();
    final thrown = error;
    if (thrown != null) {
      throw thrown;
    }
    return reading;
  }
}

void main() {
  late Directory directory;
  late File file;
  late FileClockAnchorStore anchors;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('clock-evidence-test');
    file = File('${directory.path}/${FileClockAnchorStore.fileName}');
    anchors = FileClockAnchorStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  const pairing = 'pairing-a';
  const publishInterval = Duration(hours: 6);
  const grace = Duration(hours: 6);

  /// The copy this phone holds throughout: published Sep 1, so its deadline is
  /// Sep 2 00:00 and anything later is stale.
  final publishedAt = DateTime.utc(2026, 9, 1, 12);

  Future<void> anchorAt({
    required DateTime at,
    required String runId,
    Duration elapsed = Duration.zero,
    String seq = '41',
  }) =>
      anchors.establish(
        ClockAnchor(
          pairingId: pairing,
          anchoredAt: at,
          reading: MonotonicReading(runId: runId, elapsed: elapsed),
          seq: PublicationSeq.parse(seq),
        ),
      );

  Future<ClockEvidence> observe(_FakeSource source) =>
      ClockEvidenceReader(anchors: anchors, source: source).observe(pairing);

  /// The production order: observe the records, **then** read the wall clock,
  /// then evaluate. [deviceNow] stands in for that reading.
  Future<CopyState> verdict({
    required _FakeSource source,
    required DateTime deviceNow,
    DateTime? published,
    DiagnosticsState diagnostics = const DiagnosticsAbsent(),
    BaselineState baseline = const BaselineAbsent(),
  }) async {
    final evidence = await observe(source);
    return evaluateCopyState(
      publishedAt: published ?? publishedAt,
      publishInterval: publishInterval,
      grace: grace,
      deviceNow: deviceNow,
      continuity: evidence.at(deviceNow),
      diagnostics: diagnostics,
      baseline: baseline,
    );
  }

  ClockDisagreement unknown(CopyState state) {
    expect(state, isA<CopyUnknown>(), reason: 'got $state');
    return (state as CopyUnknown).disagreement;
  }

  ContinuityGap gap(ClockEvidence evidence) {
    expect(evidence, isA<NoClockEvidence>(), reason: 'got $evidence');
    return (evidence as NoClockEvidence).gap;
  }

  group('case 1 — nine days offline, then the clock is moved back', () {
    test('the copy is not fresh, and the deadline alone would have said it was', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1');
      // Nine days pass with the app closed. The process is gone, so the source
      // cannot identify the run the anchor came from.
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration.zero),
      );
      final deviceNow = DateTime.utc(2026, 9, 1, 13);

      // The precondition, asserted rather than assumed: with the clock moved
      // back, §9.1 rule 2 on its own puts this copy comfortably inside its
      // deadline. Continuity is the only thing standing between a nine-day-old
      // copy and a confident "fresh".
      expect(
        staleAfter(publishedAt: publishedAt, publishInterval: publishInterval, grace: grace)
            .isAfter(deviceNow),
        isTrue,
      );

      expect(
        unknown(await verdict(source: source, deviceNow: deviceNow)),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('and when the process survived, the drift itself is the evidence', () async {
      // The same rollback with the app never killed: the source counts the nine
      // days, the wall clock claims one hour, and the disagreement is measured
      // rather than merely unproven.
      await anchorAt(at: publishedAt, runId: 'boot-1');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
      );
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.deviceClockMovedBackwards,
      );
    });
  });

  group('case 2 — a reachable host whose publisher has stopped', () {
    test('a successful fetch of the publication we hold cannot re-anchor', () async {
      // Codex's counterexample, and the one that killed the "the host is a
      // second clock" argument: `published_at` is the host's clock at
      // publication, not at this response, so a frozen publisher corroborates
      // nothing — least of all in the case `HOST_NOT_PUBLISHING` exists for.
      await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration.zero),
      );
      final deviceNow = DateTime.utc(2026, 9, 1, 13);

      await expectLater(
        anchorAt(at: deviceNow, runId: 'boot-2', seq: '41'),
        throwsA(isA<AnchorNotAdvanced>()),
      );

      expect(
        unknown(await verdict(source: source, deviceNow: deviceNow)),
        ClockDisagreement.clockContinuityUnknown,
      );
    });
  });

  test('case 3 — an unchanged clock and a young copy is fresh', () async {
    final published = DateTime.utc(2026, 9, 20, 9);
    await anchorAt(at: published, runId: 'boot-1', elapsed: const Duration(hours: 1));
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 2)),
    );
    expect(
      await verdict(
        source: source,
        published: published,
        deviceNow: DateTime.utc(2026, 9, 20, 10),
      ),
      isA<CopyFresh>(),
    );
  });

  test('case 4 — days asleep still age the copy', () async {
    // The obligation on the source, from the other side: it counts suspended
    // time, so nine days of sleep are nine days of drift-free ageing and the
    // copy is stale rather than unknown. A source that stopped while the device
    // slept would report a nine-day *drift* here and hide a stale copy behind
    // an unknown clock.
    await anchorAt(at: publishedAt, runId: 'boot-1');
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
    );
    final state = await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 10, 12));
    expect(state, isA<CopyStale>());
    expect((state as CopyStale).reason, isA<CannotCheck>());
  });

  group('case 5 — missing or corrupt evidence is never a good clock', () {
    test('no anchor yet', () async {
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration.zero),
      );
      expect(gap(await observe(source)), ContinuityGap.noAnchor);
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('an anchor that will not parse', () async {
      await file.writeAsString('{not json');
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration.zero),
      );
      expect(gap(await observe(source)), ContinuityGap.anchorUnreadable);
      expect(
        unknown(await verdict(source: source, deviceNow: DateTime.utc(2026, 9, 1, 13))),
        ClockDisagreement.clockContinuityUnknown,
      );
    });

    test('a source that cannot answer', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1');
      expect(gap(await observe(_FakeSource())), ContinuityGap.sourceUnavailable);
    });

    test('a source that throws something this layer has never heard of', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1');
      expect(
        gap(await observe(_FakeSource(error: 'a platform channel error'))),
        ContinuityGap.sourceUnavailable,
      );
    });

    test('the source that ships today answers nothing at all', () async {
      // Deliberate: no monotonic source has landed, so the honest verdict on
      // every copy is unknown rather than fresh.
      await anchorAt(at: publishedAt, runId: 'boot-1');
      final reader = ClockEvidenceReader(
        anchors: anchors,
        source: const UnavailableClockContinuitySource(),
      );
      expect(gap(await reader.observe(pairing)), ContinuityGap.sourceUnavailable);
    });
  });

  group('case 6 — a bigger number is not proof of the same run', () {
    test('a different run is discontinuous even when its reading is larger', () async {
      await anchorAt(
        at: publishedAt,
        runId: 'boot-1',
        elapsed: const Duration(seconds: 10),
      );
      const now = MonotonicReading(runId: 'boot-2', elapsed: Duration(days: 3));
      // Asserted rather than assumed: the naive "it went up, so it is the same
      // counter" test passes here, which is the whole reason the run id exists.
      expect(now.elapsed > const Duration(seconds: 10), isTrue);
      expect(gap(await observe(_FakeSource(reading: now))), ContinuityGap.discontinuous);
    });

    test('the same run reading backwards is discontinuous, not negative drift', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1', elapsed: const Duration(hours: 5));
      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(hours: 4)),
      );
      expect(gap(await observe(source)), ContinuityGap.discontinuous);
    });
  });

  test('the anchor is read before the live reading, not after', () async {
    // A fetch landing between the two reads is an ordinary race, and reading
    // them the other way round turns it into a fault report: the new anchor
    // paired with a reading taken before it gives a negative monotonic delta,
    // which this reader classifies as a broken source. So the order is checked
    // here rather than trusted, by landing exactly that fetch.
    await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
    final source = _FakeSource(
      reading: const MonotonicReading(runId: 'boot-1', elapsed: Duration(days: 9)),
      onRead: () => anchorAt(
        at: DateTime.utc(2026, 9, 10, 12),
        runId: 'boot-1',
        elapsed: const Duration(days: 9),
        seq: '42',
      ),
    );

    final evidence = await observe(source);
    expect(evidence, isA<AnchoredClockEvidence>(), reason: 'got $evidence');
    final anchored = evidence as AnchoredClockEvidence;
    expect(anchored.anchoredAt, publishedAt);
    expect(anchored.monotonicElapsed, const Duration(days: 9));
  });

  group('case 7 — a success cannot wash out an earlier disagreement', () {
    test('an accepted newer publication may re-anchor, and the future check still holds',
        () async {
      // Recovery is allowed — a copy that has just arrived is dated from now —
      // and it is only sound because §9.1 rule 1's *first* branch corroborates
      // the device clock against the host's publication instant. This test
      // exists to pin that coupling: weaken the future check and a back-dated
      // phone starts calling a brand-new copy fresh on evidence it minted for
      // itself moments earlier.
      await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
      final deviceNow = DateTime.utc(2026, 9, 1, 13);
      // The host is publishing fine; its clock says Sep 10, this phone's says
      // Sep 1. The fetch advances the seq, so the anchor is allowed to move.
      final published = DateTime.utc(2026, 9, 10, 13);
      await anchorAt(at: deviceNow, runId: 'boot-2', seq: '42');

      final source = _FakeSource(
        reading: const MonotonicReading(runId: 'boot-2', elapsed: Duration(minutes: 5)),
      );
      final later = deviceNow.add(const Duration(minutes: 5));
      final evidence = await observe(source);
      // Continuity itself is now held and trustworthy — the new anchor really
      // does agree with the monotonic source.
      expect(evidence.at(later), isA<ContinuityHeld>());
      expect((evidence.at(later) as ContinuityHeld).isTrustworthy, isTrue);

      expect(
        unknown(
          await verdict(source: source, published: published, deviceNow: later),
        ),
        ClockDisagreement.payloadFromTheFuture,
      );
    });

    test('a refused re-anchor leaves the earlier anchor intact on disk', () async {
      await anchorAt(at: publishedAt, runId: 'boot-1', seq: '41');
      await expectLater(
        anchorAt(at: DateTime.utc(2026, 9, 10, 13), runId: 'boot-2', seq: '41'),
        throwsA(isA<AnchorNotAdvanced>()),
      );
      final state = await anchors.read(pairing);
      expect(state, isA<AnchorHeld>());
      expect((state as AnchorHeld).anchor.anchoredAt, publishedAt);
      expect(state.anchor.reading.runId, 'boot-1');
    });
  });
}
