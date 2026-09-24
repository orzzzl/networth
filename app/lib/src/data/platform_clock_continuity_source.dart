import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

import '../debug_log.dart';
import '../domain/clock_anchor.dart';
import 'clock_continuity_source.dart';

/// The Android host's channel, and the name must match `MonotonicClockChannel`
/// in `MainActivity.kt` literally.
///
/// Exposed so a test can install a mock handler on the same channel the shipped
/// source talks to. A test that built its own channel name would pass while the
/// app talked to nothing.
@visibleForTesting
const MethodChannel monotonicClockChannel = MethodChannel(
  'com.orzzzl.networth_app/monotonic_clock',
);

/// The keys the platform side sends, named once.
const String _runIdKey = 'runId';
const String _elapsedKey = 'elapsedMillis';

/// Reads the device's own boot-scoped clock through the app's Android host.
///
/// This replaces [UnavailableClockContinuitySource] as the source the app can
/// ship — but only where the channel answers. **There is no platform check
/// here, deliberately:** the absence of a handler *is* the check. On a host that
/// never registered one the invocation raises [MissingPluginException], which
/// lands in the same `null` as every other unanswerable case, and a host that
/// registers one later works without this file changing. A `Platform.isAndroid`
/// guard would have to be kept in step with the Android side by hand and would
/// be invisible to every test, since the suite runs on neither platform.
///
/// **Never throws, and that is the contract rather than politeness.**
/// [ClockContinuitySource.read] resolves `null` to
/// `ContinuityGap.sourceUnavailable` and therefore `COPY_UNKNOWN`; an escaping
/// exception would instead reach `ClockEvidenceReader`'s catch-all, which is
/// there as a backstop for platform types nobody anticipated, not as this
/// class's error handling.
class PlatformClockContinuitySource implements ClockContinuitySource {
  const PlatformClockContinuitySource({MethodChannel? channel})
      : _channel = channel ?? monotonicClockChannel;

  final MethodChannel _channel;

  /// One reading, or `null` when the platform will not supply a usable one.
  ///
  /// **No deadline on the call, and the reason is measured rather than assumed.**
  /// The handler on the other side is synchronous and does two reads, so the only
  /// way it fails to return is a blocked Android main thread — which is where
  /// method-call handlers run, and which is an ANR the whole app is already
  /// inside. A timeout here would not rescue that; it would only make this
  /// function report a clock fault for it.
  @override
  Future<MonotonicReading?> read() async {
    final Object? reply;
    try {
      reply = await _channel.invokeMethod<Object?>('read');
    } on MissingPluginException {
      // The host did not register the channel. Not an error: it is what every
      // non-Android build of this app looks like.
      debugLog(() => 'monotonic clock: no platform handler');
      return null;
    } on PlatformException catch (error) {
      debugLog(() => 'monotonic clock: platform refused: ${error.code}');
      return null;
    } on Object catch (error) {
      // The codec itself can fail — a reply this build cannot decode arrives as
      // neither of the two above.
      debugLog(() => 'monotonic clock: unusable reply: $error');
      return null;
    }

    if (reply == null) {
      // The platform answered and said it cannot name its run. Distinct from
      // every branch above, and the only one that is a deliberate answer rather
      // than a fault; it is still the same `null`, because what the caller can
      // do about it is the same.
      debugLog(() => 'monotonic clock: platform has no run identity');
      return null;
    }
    if (reply is! Map) {
      debugLog(() => 'monotonic clock: reply is not a map');
      return null;
    }

    final runId = reply[_runIdKey];
    // Non-empty text and nothing further. The run id is **opaque by contract** —
    // only equality is ever asked of it — so checking its shape here would be
    // this layer quietly deciding what the platform's identity scheme may be.
    if (runId is! String || runId.isEmpty) {
      debugLog(() => 'monotonic clock: reply carries no run id');
      return null;
    }

    final elapsedMillis = reply[_elapsedKey];
    if (elapsedMillis is! int) {
      debugLog(() => 'monotonic clock: reply carries no elapsed reading');
      return null;
    }
    if (elapsedMillis < 0) {
      // A counter that is *documented* to start at boot and count up cannot be
      // negative, so this is not a reading with an odd value — it is evidence
      // that whatever produced it is not the counter this contract describes.
      // Accepting it would let a stored anchor sit ahead of every future
      // reading, which `ClockEvidenceReader` reads as a broken run for as long
      // as the anchor lives.
      debugLog(() => 'monotonic clock: elapsed reading is negative');
      return null;
    }

    return MonotonicReading(
      runId: runId,
      elapsed: Duration(milliseconds: elapsedMillis),
    );
  }
}
