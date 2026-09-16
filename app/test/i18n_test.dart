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
/// is worse than none.** It checks that no `Text(...)` widget in the app's own
/// source is constructed from a string literal — that being the one mechanism by
/// which copy reaches the screen in this app. It does *not* prove that every
/// user-facing string is localised in general: a literal passed to some future
/// widget's `semanticLabel`, or to a `SnackBar`, would pass this. When such a
/// surface is added, it belongs in the pattern list below.
void main() {
  final uiSources = [
    File('lib/main.dart'),
    ...Directory('lib/src/ui').listSync(recursive: true).whereType<File>(),
  ].where((f) => f.path.endsWith('.dart'));

  /// `Text('...')` or `Text("...")`, allowing whitespace and a leading `const`.
  final literalText = RegExp(r'''\bText\(\s*['"]''');

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

  test('the guard can actually fail', () {
    // A checker is evidence only where it has been shown to go red. This one is
    // a regex over source, which is exactly the kind of thing that passes
    // because it matched nothing at all.
    expect(literalText.hasMatch("      Text('Net worth'),"), isTrue);
    expect(literalText.hasMatch('    child: const Text("hello"),'), isTrue);
    expect(literalText.hasMatch('        Text(l10n.appTitle),'), isFalse);
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
