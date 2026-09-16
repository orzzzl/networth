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
/// PR #77's previous head moved a raw `${snapshot.error}` off the screen and
/// into `debugPrint`, which reads like a developer channel and is not one: the
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
/// [kDebugMode] is a compile-time constant, so in a release build the call below
/// is dead code and the tree-shaker removes it along with the interpolated
/// string — the text is not merely unprinted, it is not built.
void debugLog(String message) {
  if (kDebugMode) {
    debugPrint(message);
  }
}
