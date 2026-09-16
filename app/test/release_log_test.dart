import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/debug_log.dart';

/// The release-log boundary, made mechanical.
///
/// **Scope is the whole of `lib/`, deliberately, and that is the difference
/// between this file and `i18n_test.dart`.** The i18n guard scans only the UI
/// layer because only the UI layer has user-facing strings. Printing is not a
/// UI concern: a `debugPrint` added to `src/data` ships to release logcat
/// exactly as readily as one added to a widget. Generated l10n sources are
/// scanned too — we do not own that code, so if the generator ever starts
/// printing, that is precisely the thing we would want to be told about.
///
/// **Why a static check is the regression here, rather than a runtime one.**
/// [kDebugMode] is a compile-time constant that is `true` under `flutter test`,
/// so no test executing in this process can observe release behaviour. Asserting
/// "nothing is printed" here would be asserting something the test binary cannot
/// be in a position to check — the kind of evidence that cannot fail, which this
/// project keeps finding in other people's work. The honest form is: pin the
/// *source* property that makes release safety true, and say out loud that it is
/// the source and not the behaviour being pinned.
void main() {
  final libSources = Directory('lib')
      .listSync(recursive: true)
      .whereType<File>()
      .where((f) => f.path.endsWith('.dart'))
      .toList()
    ..sort((a, b) => a.path.compareTo(b.path));

  /// `print(...)` or `debugPrint(...)`, but not `foo.print(` and not `sprint(`.
  final printCall = RegExp(r'(?<![\w.$])(debugPrint|print)\s*\(');

  const boundary = 'lib/src/debug_log.dart';

  test('lib/ is non-empty and the boundary file is in it', () {
    // Guards over a file list are the ones that pass by matching nothing.
    expect(libSources, isNotEmpty);
    expect(libSources.map((f) => f.path), contains(boundary));
  });

  test('nothing in lib/ prints except the one gated boundary', () {
    final offenders = <String>[];
    for (final source in libSources) {
      if (source.path == boundary) {
        continue;
      }
      final lines = source.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        // Doc comments quote `debugPrint` on purpose — including the SDK
        // sentence that is the whole reason this file exists.
        if (line.trimLeft().startsWith('//')) {
          continue;
        }
        if (printCall.hasMatch(line)) {
          offenders.add('${source.path}:${i + 1}: ${line.trim()}');
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason: 'debugPrint logs in release too (SDK foundation/print.dart); route\n'
          'internal text through debugLog() in $boundary instead:\n'
          '${offenders.join('\n')}',
    );
  });

  test('the boundary file actually gates on kDebugMode', () {
    // Without this, the check above is satisfied by moving every print into one
    // file and printing unconditionally — the exemption would have become the
    // leak. What makes `debugLog` safe is the gate, not the address.
    final source = File(boundary).readAsStringSync();
    final code = source
        .split('\n')
        .where((line) => !line.trimLeft().startsWith('//'))
        .join('\n');

    expect(code, contains('kDebugMode'));
    expect(
      RegExp(r'if\s*\(\s*kDebugMode\s*\)').hasMatch(code),
      isTrue,
      reason: '$boundary must guard its print with `if (kDebugMode)`',
    );
  });

  /// `debugLog(` followed by anything that is not the start of a closure.
  ///
  /// This is the third round's defect expressed as a pattern. `debugLog('x: $e')`
  /// interpolates at the call site, which happens before the callee's gate is
  /// reached, so the gate cannot make it dead code and the literal ships.
  final eagerCall = RegExp(r'(?<![\w.$])debugLog\s*\(\s*(?!\(\s*\)\s*(=>|\{))');

  test('every debugLog call site passes work, not a finished string', () {
    // The signature already makes this a compile error, and `flutter analyze`
    // runs before these tests. That is the stronger guard of the two — but it is
    // invisible to mutation: revert the parameter to `String` and every call
    // site reverts with it, leaving the suite green and the analyzer happy. This
    // is the check that stays red for exactly that edit.
    final offenders = <String>[];
    for (final source in libSources) {
      // The boundary is skipped because the *declaration* reads as a call to a
      // pattern this simple, and the exemption is safe for a reason worth
      // stating: what it exempts is checked next door rather than trusted. The
      // test below reads this same file and requires the parameter to be
      // `String Function()`, which is also what makes an eager call inside it a
      // compile error. Last round's lesson was an exemption whose name only
      // sounded like a boundary; this one names the file the adjacent test pins.
      if (source.path == boundary) {
        continue;
      }
      final lines = source.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        if (line.trimLeft().startsWith('//')) {
          continue;
        }
        if (eagerCall.hasMatch(line)) {
          offenders.add('${source.path}:${i + 1}: ${line.trim()}');
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason: 'an argument is evaluated before the callee is entered, so an\n'
          'interpolated string reaches the release image whatever the gate\n'
          'inside $boundary says. Pass `() => ...` instead:\n'
          '${offenders.join('\n')}',
    );
  });

  test('the boundary takes the message lazily', () {
    // And the other half of that edit: the signature itself.
    final code = File(boundary)
        .readAsLinesSync()
        .where((line) => !line.trimLeft().startsWith('//'))
        .join('\n');

    expect(
      RegExp(r'void\s+debugLog\s*\(\s*String\s+Function\s*\(\s*\)').hasMatch(code),
      isTrue,
      reason: '$boundary must take `String Function()`; a `String` parameter is\n'
          'built by the caller, outside the gate that is supposed to elide it',
    );
    expect(
      RegExp(r'debugPrint\s*\(\s*message\s*\(\s*\)\s*\)').hasMatch(code),
      isTrue,
      reason: 'the callback must be invoked inside the gate, not before it',
    );
  });

  test('the guard can actually fail', () {
    // A regex over source passes loudest when it matches nothing at all, so
    // every pattern above is shown going red on the exact line it is meant to
    // catch — including the line this review round was about.
    expect(printCall.hasMatch(r"    debugPrint('snapshot unreadable: ${snapshot.error}');"), isTrue);
    expect(printCall.hasMatch("  print('hi');"), isTrue);
    expect(printCall.hasMatch('  debugPrint ('), isTrue);
    // ...and staying green where a false positive would be annoying enough that
    // someone would weaken the rule to silence it.
    expect(printCall.hasMatch('    final p = blueprint(x);'), isFalse);
    expect(printCall.hasMatch('    buffer.print(x);'), isFalse);
    expect(printCall.hasMatch('    debugLog(\'snapshot unreadable\');'), isFalse);

    expect(RegExp(r'if\s*\(\s*kDebugMode\s*\)').hasMatch('void debugLog(String m) { debugPrint(m); }'), isFalse);

    // The exact line the third round rejected, and the exact line that replaced
    // it — the pattern is worthless if it cannot tell those two apart.
    expect(eagerCall.hasMatch(r"      debugLog('snapshot unreadable: ${snapshot.error}');"), isTrue);
    expect(eagerCall.hasMatch(r'      debugLog(message);'), isTrue);
    expect(eagerCall.hasMatch(r"      debugLog(() => 'snapshot unreadable: ${snapshot.error}');"), isFalse);
    expect(eagerCall.hasMatch(r'      debugLog(() { return describe(e); });'), isFalse);
  });

  test('debugLog is a live channel in a debug build, not a silent no-op', () {
    // The other way this could be "safe": a helper that never prints anything,
    // in which case the developer diagnostic the screen gave up its error text
    // for does not exist either. That failure is invisible to every check above,
    // and it *is* observable here, because this process is a debug build.
    //
    // It also pins the half of laziness that runs: the callback must actually be
    // invoked. A gate that elides the work in release and forgets to do it in
    // debug is the same dead channel by a different route.
    final captured = <String?>[];
    var built = 0;
    final previous = debugPrint;
    debugPrint = (String? message, {int? wrapWidth}) => captured.add(message);
    addTearDown(() => debugPrint = previous);

    debugLog(() {
      built++;
      return 'snapshot unreadable: Bad state: no payload';
    });

    expect(captured, ['snapshot unreadable: Bad state: no payload']);
    expect(built, 1, reason: 'the message is built once, inside the gate');
  });
}
