import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/platform_clock_continuity_source.dart';
import 'package:networth_app/src/domain/clock_anchor.dart';

/// The Android host's half of `ClockContinuitySource`.
///
/// **Every rejection below resolves to `COPY_UNKNOWN`, and every acceptance
/// feeds a comparison that decides whether a stale copy renders fresh.** So the
/// tests are written against the two directions separately: a reply this class
/// refuses costs the owner a "couldn't tell how old this is", and a reply it
/// accepts wrongly costs him a confident wrong answer. The asymmetry is why the
/// shape checks are exhaustive rather than representative.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  final messenger = TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  const source = PlatformClockContinuitySource();

  /// Answer the channel the shipped source actually talks to.
  void answerWith(Future<Object?>? Function(MethodCall call) handler) {
    messenger.setMockMethodCallHandler(monotonicClockChannel, handler);
    addTearDown(() => messenger.setMockMethodCallHandler(monotonicClockChannel, null));
  }

  /// Reply with exactly these bytes, bypassing the method codec.
  void answerWithBytes(ByteData? reply) {
    messenger.setMockMessageHandler(monotonicClockChannel.name, (_) async => reply);
    addTearDown(() => messenger.setMockMessageHandler(monotonicClockChannel.name, null));
  }

  group('a reading the platform can supply', () {
    test('becomes a MonotonicReading with both fields', () async {
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': 1234});

      expect(
        await source.read(),
        const MonotonicReading(runId: 'boot-id:abc', elapsed: Duration(milliseconds: 1234)),
      );
    });

    test('is accepted at zero — a reading taken the moment the run began', () async {
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': 0});

      expect(await source.read(), isNotNull);
      expect((await source.read())!.elapsed, Duration.zero);
    });

    test('keeps the run id opaque: no shape is required of it', () async {
      // Only equality is ever asked of a run id, so this layer must not decide
      // what the platform's identity scheme may look like. A source that
      // validated the `boot-id:` spelling would reject the very next scheme
      // rather than let the mismatch lose the evidence, which is the fail-closed
      // behaviour the prefix exists to produce.
      answerWith((_) async => {'runId': '?', 'elapsedMillis': 7});

      expect((await source.read())!.runId, '?');
    });

    test('is re-read on every call rather than cached', () async {
      // The value answers "how long has this run been going" *now*. A cached
      // reading would make the monotonic delta stop growing while the wall clock
      // kept moving, which reads as forward drift — a spurious COPY_UNKNOWN at
      // best, and at worst an answer about a moment that has passed.
      var elapsed = 10;
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': elapsed += 5});

      expect((await source.read())!.elapsed, const Duration(milliseconds: 15));
      expect((await source.read())!.elapsed, const Duration(milliseconds: 20));
    });

    test('is requested as the `read` method with no arguments', () async {
      final calls = <MethodCall>[];
      answerWith((call) async {
        calls.add(call);
        return {'runId': 'boot-id:abc', 'elapsedMillis': 1};
      });

      await source.read();

      expect(calls.single.method, 'read');
      expect(calls.single.arguments, isNull);
    });
  });

  group('the platform cannot supply one', () {
    test('no handler registered at all — the only platform check there is', () async {
      // Nothing installed: the messenger returns a null envelope, which is
      // exactly what a host that never registered the channel produces, and
      // `MethodChannel` turns into `MissingPluginException`. This is the case
      // every non-Android build of the app is permanently in.
      expect(await source.read(), isNull);
    });

    test('the platform side raised — a refusal, not a reading', () async {
      answerWith((_) async => throw PlatformException(code: 'unavailable'));

      expect(await source.read(), isNull);
    });

    test('the reply cannot be decoded at all', () async {
      // Neither a plugin-missing nor an error envelope: bytes the method codec
      // rejects. Without the catch-all this escapes as a `FormatException` from
      // a function whose contract is to always have an answer.
      answerWithBytes(ByteData.sublistView(Uint8List.fromList([0xff, 0x01, 0x02])));

      expect(await source.read(), isNull);
    });

    test('the platform answered null — it will not name its run', () async {
      // The deliberate answer: the host is there, and it read neither a boot
      // identity nor anything it would substitute for one.
      answerWith((_) async => null);

      expect(await source.read(), isNull);
    });
  });

  group('a reply that is not the contract', () {
    test('is not a map', () async {
      answerWith((_) async => 'boot-id:abc');

      expect(await source.read(), isNull);
    });

    test('carries no run id', () async {
      answerWith((_) async => {'elapsedMillis': 1234});

      expect(await source.read(), isNull);
    });

    test('carries a run id that is not text', () async {
      answerWith((_) async => {'runId': 7, 'elapsedMillis': 1234});

      expect(await source.read(), isNull);
    });

    test('carries an empty run id', () async {
      // An empty string compares equal to another empty string, so accepting it
      // would let two unrelated runs prove continuity to each other — the one
      // failure this field exists to prevent.
      answerWith((_) async => {'runId': '', 'elapsedMillis': 1234});

      expect(await source.read(), isNull);
    });

    test('carries no elapsed reading', () async {
      answerWith((_) async => {'runId': 'boot-id:abc'});

      expect(await source.read(), isNull);
    });

    test('carries an elapsed reading that is not a number', () async {
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': '1234'});

      expect(await source.read(), isNull);
    });

    test('carries an elapsed reading that is a double', () async {
      // The codec delivers a platform `double` as a Dart `double`, and
      // `Duration(milliseconds:)` takes an `int` — so this is the one wrong type
      // that a laxer check (`is num`) would let through and then have to round.
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': 1234.5});

      expect(await source.read(), isNull);
    });

    test('carries a negative elapsed reading', () async {
      // Not a reading with an odd value: evidence that whatever produced it is
      // not a counter that starts at boot and counts up. Accepted, it would park
      // a stored anchor ahead of every future reading for as long as it lives.
      answerWith((_) async => {'runId': 'boot-id:abc', 'elapsedMillis': -1});

      expect(await source.read(), isNull);
    });
  });

  group('the Dart and Kotlin halves name the same channel', () {
    // Both sides hardcode the same literals, and nothing else in the suite can
    // notice them drifting apart: every test above installs its handler on the
    // *Dart* constant, so renaming one side alone leaves the suite green and the
    // shipped app talking to nobody — which the source resolves to
    // `COPY_UNKNOWN` without complaint.
    final kotlin = File(
      'android/app/src/main/kotlin/com/orzzzl/networth_app/MonotonicClockChannel.kt',
    );

    test('the Kotlin source is where this test expects it', () {
      // A guard over a file's contents passes by reading nothing at all.
      expect(kotlin.existsSync(), isTrue, reason: '${kotlin.path} is missing');
      expect(kotlin.readAsStringSync(), isNotEmpty);
    });

    test('the channel name and method name match literally', () {
      final source = kotlin.readAsStringSync();

      expect(source, contains('"${monotonicClockChannel.name}"'));
      expect(source, contains('"read"'));
    });

    test('the Android host registers the channel', () {
      final activity = File(
        'android/app/src/main/kotlin/com/orzzzl/networth_app/MainActivity.kt',
      );

      expect(activity.existsSync(), isTrue);
      expect(activity.readAsStringSync(), contains('MonotonicClockChannel.register'));
    });
  });
}
