import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/ui/history_curve.dart';

import 'fixtures.dart';
import 'net_worth_history_test.dart' show point;

const Size _box = Size(307, 120);

/// The width of one day in [_box], for the series passed in.
double _dayWidth(NetWorthHistory history) =>
    (_box.width - CurveGeometry.markerRadius * 2) / history.spanInDays;

void main() {
  group('an incomplete reading is visually distinct', () {
    test('its marker is hollow where a whole reading is filled', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 100, isComplete: true),
        point('2026-09-11T04:00:00Z', 200, isComplete: false),
        point('2026-09-12T04:00:00Z', 300, isComplete: true),
      ]);

      final geometry = CurveGeometry.layOut(history, _box);

      expect(
        [for (final marker in geometry.markers) marker.isHollow],
        [false, true, false],
      );
    });

    test('both runs touching it are dashed, and the rest are solid', () {
      // Either endpoint, not both. A run from a whole reading into a partial one
      // is not a run we can vouch for, so drawing its first half solid would
      // vouch for it.
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 100),
        point('2026-09-11T04:00:00Z', 200, isComplete: false),
        point('2026-09-12T04:00:00Z', 300),
        point('2026-09-13T04:00:00Z', 400),
      ]);

      final geometry = CurveGeometry.layOut(history, _box);

      expect(
        [for (final stroke in geometry.strokes) stroke.isDashed],
        [true, true, false],
      );
    });

    test('a series with nothing incomplete has no dashed run at all', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 100),
        point('2026-09-11T04:00:00Z', 200),
      ]);

      final geometry = CurveGeometry.layOut(history, _box);

      expect(geometry.strokes.single.isDashed, isFalse);
      expect(geometry.markers.every((marker) => !marker.isHollow), isTrue);
    });
  });

  group('a gap in the record is never drawn across', () {
    test('no run spans more than one day, whatever the series', () {
      // The criterion in its strongest form. Interpolating across the hole would
      // assert a value that was never stored — the deformation §12 rules out,
      // arriving from the renderer. A run wider than one day is that assertion,
      // so this measures every run rather than counting them.
      final history = loadHistoryFixture();
      final geometry = CurveGeometry.layOut(history, _box);
      final dayWidth = _dayWidth(history);

      expect(geometry.strokes, isNotEmpty);
      for (final stroke in geometry.strokes) {
        expect(
          (stroke.to.dx - stroke.from.dx).abs(),
          closeTo(dayWidth, 0.000001),
          reason: 'a run crossed a day with no reading',
        );
      }
    });

    test('the two sides of the gap are further apart than one day', () {
      // Without this the previous test would also pass on a curve that simply
      // packed the points side by side — the break would exist arithmetically
      // and be invisible, which is not what "must look like a gap" asks for.
      final history = loadHistoryFixture();
      final geometry = CurveGeometry.layOut(history, _box);

      final before = history.segments.first.last;
      final after = history.segments[1].first;
      expect(after.day.difference(before.day).inDays, greaterThan(1));

      final xOf = {
        for (var i = 0; i < history.points.length; i++)
          history.points[i]: geometry.markers[i].at.dx,
      };
      expect(
        xOf[after]! - xOf[before]!,
        closeTo(_dayWidth(history) * after.day.difference(before.day).inDays, 0.000001),
      );
    });

    test('there is exactly one run fewer than points in each segment', () {
      final history = loadHistoryFixture();
      final geometry = CurveGeometry.layOut(history, _box);

      expect(
        geometry.strokes.length,
        history.points.length - history.segments.length,
      );
      expect(geometry.markers.length, history.points.length);
    });
  });

  group('degenerate series do not divide by zero', () {
    test('one reading is a lone centred marker with no run', () {
      final geometry = CurveGeometry.layOut(
        NetWorthHistory.reduce([point('2026-09-10T04:00:00Z', 100)]),
        _box,
      );

      expect(geometry.strokes, isEmpty);
      expect(geometry.markers.single.at.dx, closeTo(_box.width / 2, 0.000001));
      expect(geometry.markers.single.at.dy, closeTo(_box.height / 2, 0.000001));
    });

    test('a flat series is a flat line, not a crash', () {
      final geometry = CurveGeometry.layOut(
        NetWorthHistory.reduce([
          point('2026-09-10T04:00:00Z', 100),
          point('2026-09-11T04:00:00Z', 100),
        ]),
        _box,
      );

      expect(geometry.strokes.single.from.dy, geometry.strokes.single.to.dy);
      expect(geometry.markers.first.at.dy, closeTo(_box.height / 2, 0.000001));
    });

    test('an empty series draws nothing', () {
      final geometry = CurveGeometry.layOut(NetWorthHistory.empty, _box);

      expect(geometry.strokes, isEmpty);
      expect(geometry.markers, isEmpty);
    });
  });

  group('the widget', () {
    Future<void> pump(WidgetTester tester, NetWorthHistory? history) =>
        tester.pumpWidget(localized(HistoryCurve(history: history)));

    testWidgets('renders no figures at all — I4 has no exception for a chart', (
      tester,
    ) async {
      // There is no widget in this app that takes a bare amount, and an axis
      // label would be exactly that: a number with no age beside it. The whole
      // rendered surface is checked rather than one widget, because the defect
      // would be an amount reaching the screen, not a particular `Text`.
      await pump(tester, loadHistoryFixture());

      for (final line in renderedText(tester, find.byType(HistoryCurve))) {
        expect(
          line,
          isNot(matches(RegExp(r'\d'))),
          reason: 'the curve rendered a figure: "$line"',
        );
      }
    });

    testWidgets('names a treatment only when that treatment is on screen', (
      tester,
    ) async {
      await pump(
        tester,
        NetWorthHistory.reduce([
          point('2026-09-10T04:00:00Z', 100),
          point('2026-09-11T04:00:00Z', 200),
        ]),
      );

      expect(find.text('History'), findsOneWidget);
      expect(find.text('dashed where a reading was incomplete'), findsNothing);
      expect(find.text('breaks are days with no reading'), findsNothing);
    });

    testWidgets('and names both when both are', (tester) async {
      await pump(tester, loadHistoryFixture());

      expect(find.text('dashed where a reading was incomplete'), findsOneWidget);
      expect(find.text('breaks are days with no reading'), findsOneWidget);
    });

    testWidgets('an unreadable series is not reported as an empty one', (tester) async {
      // Two different facts. "No readings recorded yet" over a record that
      // exists and failed to load is a false statement about the owner's own
      // history — the product's central failure, one layer down.
      await pump(tester, null);
      expect(find.text("couldn't read the history"), findsOneWidget);

      await pump(tester, NetWorthHistory.empty);
      expect(find.text('no readings recorded yet'), findsOneWidget);
      expect(find.text("couldn't read the history"), findsNothing);
    });
  });
}
