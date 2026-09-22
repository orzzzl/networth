import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  group('no build can reach an invented past', () {
    // A *source* property, pinned the way `release_log_test.dart` pins the print
    // boundary and for the same stated reason: `kReleaseMode` is false in this
    // process, so no test running here can observe what a release build wires.
    // What can be checked is what the entry points name and what the app
    // bundles, and that is the property the whole arrangement rests on.
    final entryPoints = Directory('lib')
        .listSync()
        .whereType<File>()
        .where((file) => file.path.endsWith('.dart'))
        .toList();

    /// A file's code, with `//` and `///` lines dropped.
    ///
    /// **The property is what the entry point can reach, which is a fact about
    /// code.** The first version of this guard scanned the raw file and went red
    /// on the doc comment two files over that *explains* the arrangement — a
    /// guard that forbids describing the thing it protects will be deleted by
    /// the first person it inconveniences, and it will be right to delete it.
    /// Block comments are not stripped; nothing here uses them, and half a
    /// parser would be worse than none.
    String code(File file) => file
        .readAsLinesSync()
        .where((line) => !line.trimLeft().startsWith('//'))
        .join('\n');

    test('lib/main.dart exists — a scan over a missing file passes vacuously', () {
      expect(File('lib/main.dart').existsSync(), isTrue);
      expect(code(File('lib/main.dart')), contains('void main()'));
    });

    test('main.dart is the only entry point, so there is no second wiring', () {
      // `main_demo.dart` was the one place a synthetic series was wired, and
      // task 23a deleted it with the fixture it rendered. This is the control
      // for the scan below: without it, "no entry point names a series asset"
      // would also be satisfied by a demo nobody noticed had come back.
      expect(
        [for (final file in entryPoints) file.uri.pathSegments.last],
        ['main.dart'],
      );
    });

    test('no entry point names a history asset', () {
      for (final file in entryPoints) {
        expect(
          code(file),
          isNot(contains('assets/fixtures/history')),
          reason: '${file.path} reaches a bundled series',
        );
      }
    });

    test('and the app ships no series to reach', () {
      // The asset directory is bundled wholesale (`pubspec.yaml` lists
      // `assets/fixtures/`), so a file put back there would ship whether or not
      // any code named it. Every remaining fixture must be a *payload* — a
      // synthetic today, which announces itself in the UNKNOWN and stale
      // annotations the screen draws around it — and never a series.
      final assets = Directory('assets/fixtures')
          .listSync()
          .whereType<File>()
          .where((file) => file.path.endsWith('.json'));

      for (final asset in assets) {
        expect(
          jsonDecode(asset.readAsStringSync()),
          isA<Map<String, Object?>>(),
          reason: '${asset.path} is a series, not a payload',
        );
      }
    });
  });
}
