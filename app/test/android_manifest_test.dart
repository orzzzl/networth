import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// What the *shipped* APK is allowed to do, made mechanical.
///
/// **This file exists because 643 green tests said nothing about a release
/// build that could not make a single network call.** `INTERNET` was declared
/// in `android/app/src/debug/AndroidManifest.xml` and
/// `android/app/src/profile/AndroidManifest.xml` — the two configurations that
/// never reach the owner — and nowhere else. Every test and every debug build
/// therefore had the permission for free, and only the release APK went
/// without. Review caught it with `aapt` on an independently built exact head.
///
/// **Why it was invisible until the entry point changed.** Until `main.dart`
/// was wired to the real transport the app read a bundled fixture and made no
/// network call at all, so the missing permission cost nothing. The commit that
/// made the app networked is the commit that made this a defect, and nothing in
/// the Dart suite can observe it: `flutter test` runs on the host VM with no
/// Android manifest in the picture.
///
/// **So the check reads the manifest that ships, by name.** The tempting
/// version globs `android/app/src/*/AndroidManifest.xml` and asserts the
/// permission appears somewhere — and that version is **green against the very
/// bug this file is about**, because `debug` and `profile` have always had it.
/// Naming `main` is the whole content of the test.
void main() {
  final main = File('android/app/src/main/AndroidManifest.xml');
  final permission = RegExp(
    r'''<uses-permission\s+android:name\s*=\s*["']android\.permission\.INTERNET["']''',
  );

  test('the shipped manifest grants the app the network it now depends on', () {
    expect(main.existsSync(), isTrue, reason: 'no main manifest at ${main.path}');
    expect(
      permission.hasMatch(main.readAsStringSync()),
      isTrue,
      reason: 'a release APK without INTERNET cannot fetch a payload at all',
    );
  });

  test('and the build-type manifests are not what makes that true', () {
    // The premise of the test above, asserted rather than assumed. If `debug`
    // and `profile` ever stop declaring it, "the permission is somewhere in the
    // android tree" starts meaning what we want it to mean by accident, and the
    // test above stops being the thing that is load-bearing. This is the line
    // that would tell us the reasoning had drifted.
    for (final type in ['debug', 'profile']) {
      final overlay = File('android/app/src/$type/AndroidManifest.xml');
      expect(overlay.existsSync(), isTrue, reason: 'no $type manifest');
      expect(
        permission.hasMatch(overlay.readAsStringSync()),
        isTrue,
        reason: '$type used to declare INTERNET; if it no longer does, the '
            'main-manifest check above is being satisfied for a new reason',
      );
    }
  });

  test('nothing has quietly added a second permission we wrote', () {
    // Not a style rule. A permission is visible to anyone who inspects the APK,
    // and this app's whole claim is that it holds no credential and reads one
    // host; one arriving without a reason written beside it is worth being told
    // about. `INTERNET` is the only one this app has ever needed.
    //
    // **This pins what we declare, not what the APK carries**, and the two are
    // not the same: `aapt2 dump permissions` on the built release APK also
    // reports `…DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION`, which AndroidX adds
    // during manifest merge. Asserting the APK's full set here would fail on
    // someone else's dependency bump and would be asserting a thing this
    // repository does not author. The merged output is what `aapt2` is for.
    final declared = RegExp(r'''<uses-permission\s+android:name\s*=\s*["']([^"']+)["']''')
        .allMatches(main.readAsStringSync())
        .map((m) => m.group(1))
        .toList();

    expect(declared, ['android.permission.INTERNET']);
  });
}
