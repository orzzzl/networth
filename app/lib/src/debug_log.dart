import 'package:flutter/foundation.dart';

/// The one place in this app that writes internal text to a console.
///
/// It exists because `debugPrint` is not the boundary its name suggests. The
/// installed SDK says so itself, in `foundation/print.dart`:
///
/// > The [debugPrint] function logs to console even in release mode. As per
/// > convention, calls to [debugPrint] should be within a debug mode check or
/// > an assert.
///
/// PR #77's second round moved a raw `${snapshot.error}` off the screen and into
/// `debugPrint`, which reads like a developer channel and is not one: the
/// unbounded exception text stopped reaching the owner's eyes and kept reaching
/// the release APK's logcat. For a net-worth app, whose exception messages can
/// quote balances, account names or a `link_token`, that is the same leak with
/// a longer path.
///
/// Gating each call site would work too. A single function is preferred because
/// it turns the rule into somewhere a checker can *look*: the release boundary
/// is this file, so `release_log_test.dart` can require that no other file in
/// `lib/` prints at all, without having to decide by regex whether some call
/// site's enclosing braces happen to be a `kDebugMode` block.
///
/// **The message is a callback, and that is the third round's blocker rather
/// than a style choice.** This function took a `String`, so the caller built the
/// string *before* control ever reached the gate. The gate then decided whether
/// to print text that had already been interpolated — and the literal that
/// interpolation is built from is a constant in the compiled image, so it
/// shipped. Measured on the exact-head ARMv7 AOT library, not reasoned about:
///
/// ```
/// $ strings -a build/.../armeabi-v7a/libapp.so | grep 'snapshot unreadable'
/// snapshot unreadable:
/// ```
///
/// A `kDebugMode` check cannot make its argument dead code. It can only make
/// dead code of what it encloses, so the work has to be *inside* it — which is
/// what a callback achieves: the interpolation is the body of a closure that
/// nothing in a release build ever calls, so both the call and the literal are
/// unreachable and the compiler drops them.
///
/// The signature is the durable half. `debugLog('...$secret')` is no longer a
/// leak that review has to notice; it is a type error the analyzer rejects at
/// every call site, before any test runs.
///
/// [kDebugMode] stays a compile-time constant here for the same reason — an
/// injectable gate would be testable and would also be a runtime value, which is
/// exactly what stops the compiler from eliminating the branch.
void debugLog(String Function() message) {
  if (kDebugMode) {
    debugPrint(message());
  }
}
