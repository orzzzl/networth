import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/history_source.dart';

import 'fixtures.dart';

void main() {
  group('the production entry point does not name the synthetic series', () {
    // A *source* property, pinned the way `release_log_test.dart` pins the
    // print boundary and for the same stated reason: `kReleaseMode` is false in
    // this process, so no test running here can observe what a release build
    // wires. What can be checked is which class `main.dart` names, and that is
    // the property the whole arrangement rests on.
    final main = File('lib/main.dart');
    final demo = File('lib/main_demo.dart');

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

    test('both entry points exist — a scan over a missing file passes vacuously', () {
      expect(main.existsSync(), isTrue);
      expect(demo.existsSync(), isTrue);
      expect(code(main), contains('void main()'));
      expect(code(demo), contains('void main()'));
    });

    test('main.dart names EmptyHistorySource and never FixtureHistorySource', () {
      expect(code(main), contains('EmptyHistorySource'));
      expect(
        code(main),
        isNot(contains('FixtureHistorySource')),
        reason: 'the production entry point may not reach the invented past',
      );
    });

    test('and the demo entry point is where it lives instead — the control', () {
      // Without this, the assertion above is also satisfied by deleting the
      // fixture wiring outright, and nothing would say the demo had been lost.
      expect(code(demo), contains('FixtureHistorySource'));
    });
  });

  group('production wiring cannot render a synthetic past', () {
    test('the empty source is empty, and that is what production gets', () async {
      // `main.dart` names this class and does not name the fixture. The
      // assertion is trivial; what it pins is that the class exists as shipped
      // code rather than as a test helper, so `main.dart` has something honest
      // to point at.
      expect((await const EmptyHistorySource().load()).isEmpty, isTrue);
    });

    test('the fixture source refuses to load in a release build', () async {
      // The flag is injected because `flutter test` runs in debug: a guard
      // keyed only on the real `kReleaseMode` could never be executed here, and
      // a check whose only state is "never ran" is not a check.
      await expectLater(
        const FixtureHistorySource(historyFixture, isReleaseBuild: true).load(),
        throwsA(isA<StateError>()),
      );
    });

    test('and still loads in a debug build, which is the control', () async {
      final history = await FixtureHistorySource(
        historyFixture,
        bundle: StringAssetBundle.ofFixtures(),
        isReleaseBuild: false,
      ).load();

      expect(history.isEmpty, isFalse);
      expect(history.hasGap, isTrue);
      expect(history.hasIncompletePoint, isTrue);
    });

    test('its default is the real build mode, not a convenient false', () async {
      // The parameter exists so the refusal is testable, not so it defaults
      // open. `kReleaseMode` is false under `flutter test`, so this is what the
      // default resolves to here — and if someone changes the default to a
      // literal `false`, the release branch becomes unreachable in every build
      // and this test is where that is written down.
      expect(
        const FixtureHistorySource(historyFixture).isReleaseBuild,
        isFalse,
        reason: 'flutter test runs in debug, so the default must resolve to false here',
      );
    });
  });

  test('a bundled series still parses into the shape the curve needs', () async {
    final history = await FixtureHistorySource(
      historyFixture,
      bundle: StringAssetBundle.ofFixtures(),
    ).load();

    expect(history.points, isNotEmpty);
    expect(history.currency, 'USD');
    expect(history.points.map((point) => point.day).toList(), isA<List<DateTime>>());
    expect(history.segments.length, greaterThan(1), reason: 'the fixture carries a gap');
  });
}
