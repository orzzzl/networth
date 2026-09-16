import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// The convention, made mechanical.
///
/// `AGENTS.md`: *user-facing strings go through the UI layer's own i18n, never
/// hard-coded.* PR #77's first head broke that on essentially its whole surface,
/// and the reviewer's objection was not the strings themselves — it was that
/// every screen built on top would inherit the foundation. A rule only a reader
/// enforces is a rule the next screen breaks, so this is the reader.
///
/// **What it covers, stated precisely, because a guard that overstates its reach
/// is worse than none.** Two patterns: no `Text(...)` built from a string
/// literal, and no string literal in this layer that interpolates a value.
///
/// The second was added after the re-review found a bypass the first had let
/// through — `Text('\${snapshot.error}')`, where the literal was not the copy
/// but a hole with an exception message in it. That is worth recording as the
/// shape of this kind of guard's failure: it checked for *hard-coded copy* and
/// the defect was *uncopy*, text nobody wrote at all.
///
/// It still does *not* prove that every user-facing string is localised: a
/// literal passed to a future widget's `semanticLabel`, or to a `SnackBar`,
/// would pass both patterns. When such a surface is added it belongs here.
void main() {
  final uiSources = [
    File('lib/main.dart'),
    ...Directory('lib/src/ui').listSync(recursive: true).whereType<File>(),
  ].where((f) => f.path.endsWith('.dart'));

  /// `Text('...')` or `Text("...")`, allowing whitespace and a leading `const`.
  final literalText = RegExp(r'''\bText\(\s*['"]''');

  /// A string literal that interpolates a value, anywhere in the UI layer.
  ///
  /// Added after the re-review: the check above passed a screen that rendered
  /// `Text('\${snapshot.error}')`, because the literal was not the copy — it was
  /// a hole with an exception message in it. Interpolated user-facing text is
  /// what ICU placeholders in the ARB are for, so a bare `\${...}` in this layer
  /// is either that mistake or a debug string, and both want looking at.
  final interpolated = RegExp(r'''['"][^'"]*\$\{''');

  test('no widget in the UI layer is built from a hard-coded string', () {
    final offenders = <String>[];
    for (final source in uiSources) {
      final lines = source.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        // Doc comments quote the old copy in several places on purpose — the
        // comments explaining *why* a string changed are the most useful part of
        // this change, and they must not be what trips the guard.
        if (line.trimLeft().startsWith('//')) {
          continue;
        }
        if (literalText.hasMatch(line)) {
          offenders.add('${source.path}:${i + 1}: ${line.trim()}');
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason: 'user-facing strings go through AppLocalizations (AGENTS.md):\n'
          '${offenders.join('\n')}',
    );
  });

  test('no user-facing string is assembled by interpolating a value', () {
    final offenders = <String>[];
    for (final source in uiSources) {
      final lines = source.readAsLinesSync();
      for (var i = 0; i < lines.length; i++) {
        final line = lines[i];
        if (line.trimLeft().startsWith('//')) {
          continue;
        }
        // `debugLog` is the sanctioned destination for internal text: not a
        // surface the owner reads, and not one a release build reaches at all.
        //
        // This exemption used to name `debugPrint`, and that was the defect the
        // next review round found: the exempted name has to *be* the boundary,
        // and `debugPrint` is not one — it logs in release too. Exempting it
        // here meant this guard was quietly sanctioning the unbounded exception
        // text it had just chased off the screen. `release_log_test.dart` is
        // what makes `debugLog` the only printer in `lib/`, so naming it here
        // is now an allowance with something behind it.
        if (line.contains('debugLog(')) {
          continue;
        }
        if (interpolated.hasMatch(line)) {
          offenders.add('${source.path}:${i + 1}: ${line.trim()}');
        }
      }
    }

    expect(
      offenders,
      isEmpty,
      reason: 'interpolate through an ARB placeholder, not into a Dart string:\n'
          '${offenders.join('\n')}',
    );
  });

  test('the guard can actually fail', () {
    // A checker is evidence only where it has been shown to go red. This one is
    // a regex over source, which is exactly the kind of thing that passes
    // because it matched nothing at all.
    expect(literalText.hasMatch("      Text('Net worth'),"), isTrue);
    expect(literalText.hasMatch('    child: const Text("hello"),'), isTrue);
    expect(literalText.hasMatch('        Text(l10n.appTitle),'), isFalse);
    // The bypass the first version of this file missed entirely.
    expect(interpolated.hasMatch(r"                detail: '\${snapshot.error}',"), isTrue);
    expect(interpolated.hasMatch('        Text(l10n.totalAsOf(stamp)),'), isFalse);
    // The interpolation exemption is a name, so it is worth pinning which name:
    // the one that compiles away in release, not the one that does not.
    expect(r"debugLog('x: ${e}');".contains('debugLog('), isTrue);
    expect(r"debugPrint('x: ${e}');".contains('debugLog('), isFalse);
  });

  test('every key in the ARB is used by the app', () {
    // Copy nobody renders is copy nobody checked. This also catches the reverse
    // of the rule above: a key deleted from a widget but left in the ARB looks
    // like the screen still says it.
    final arb = jsonDecode(File('lib/l10n/app_en.arb').readAsStringSync())
        as Map<String, Object?>;
    final keys = arb.keys.where((k) => !k.startsWith('@')).toList();
    expect(keys, isNotEmpty);

    final source = uiSources.map((f) => f.readAsStringSync()).join('\n');
    final unused = keys.where((key) => !source.contains(key)).toList();

    expect(unused, isEmpty, reason: 'ARB keys no widget references: $unused');
  });

  test('every message has a description, so a translator is not guessing', () {
    final arb = jsonDecode(File('lib/l10n/app_en.arb').readAsStringSync())
        as Map<String, Object?>;
    // The check is that `@key` exists at all: its absence means nobody wrote
    // anything down about the string. A few entries are one-word labels whose
    // text is its own description and carry an empty `{}` deliberately; the
    // rest carry the reasoning that makes them translatable without reading the
    // daemon's source.
    final undescribed = arb.keys
        .where((k) => !k.startsWith('@'))
        .where((key) => arb['@$key'] is! Map<String, Object?>)
        .toList();

    expect(undescribed, isEmpty, reason: 'ARB messages with no @metadata: $undescribed');
  });
}
