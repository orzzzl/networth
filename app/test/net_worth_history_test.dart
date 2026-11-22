import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/history_source.dart';
import 'package:networth_app/src/domain/dated_total.dart';
import 'package:networth_app/src/domain/money.dart';
import 'package:networth_app/src/domain/net_worth_history.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';

import 'fixtures.dart';

HistoryPoint point(
  String publishedAt,
  int valueMinor, {
  bool isComplete = true,
  String currency = 'USD',
}) {
  final at = DateTime.parse(publishedAt);
  return HistoryPoint(
    at: at,
    total: KnownAgeTotal(
      amount: Money(minorUnits: valueMinor, currency: currency),
      assets: Money(minorUnits: valueMinor, currency: currency),
      liabilities: Money(minorUnits: 0, currency: currency),
      staticAccountCount: 0,
      isComplete: isComplete,
      asOf: at,
    ),
  );
}

void main() {
  group('days are the owner\'s days, in Los Angeles', () {
    test('a reading taken after UTC midnight is still his evening', () {
      // The owner's own first snapshot: 00:19Z on the 22nd is 16:19 PST on the
      // 21st. On UTC days the curve put it on a date that had not yet arrived.
      expect(point('2026-11-22T00:19:18Z', 1).day, DateTime.utc(2026, 11, 21));
    });

    test('the day turns at his midnight, on both sides of a DST change', () {
      expect(point('2026-09-10T06:59:00Z', 1).day, DateTime.utc(2026, 9, 9));
      expect(point('2026-09-10T07:00:00Z', 1).day, DateTime.utc(2026, 9, 10));
      expect(point('2026-12-10T07:59:00Z', 1).day, DateTime.utc(2026, 12, 9));
      expect(point('2026-12-10T08:00:00Z', 1).day, DateTime.utc(2026, 12, 10));
    });

    test('a device-local instant lands on the same day as its UTC self', () {
      // The conversion itself is pinned in `pacific_time_test.dart`; this is
      // the point that uses it, every hour of one day, whatever zone runs it.
      for (var hour = 0; hour < 24; hour++) {
        final utc = DateTime.utc(2026, 9, 10, hour, 30);
        expect(
          HistoryPoint(at: utc.toLocal(), total: point('2026-09-10T00:00:00Z', 1).total).day,
          point(utc.toIso8601String(), 1).day,
          reason: 'hour $hour',
        );
      }
    });
  });

  group('one point per day: the latest reading of that day', () {
    test('a later reading on the same day supersedes the earlier one', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-12T16:00:00Z', 4790000),
        point('2026-09-12T19:30:00Z', 4844100),
      ]);

      expect(history.points, hasLength(1));
      expect(history.points.single.total.amount.minorUnits, 4844100);
    });

    test('an out-of-order arrival does not displace the later reading', () {
      // I6 (§9.3) is the real defence — the phone refuses a payload whose `seq`
      // regressed — but "latest per day" must be a property of this reduction,
      // not of the caller having sorted first.
      final history = NetWorthHistory.reduce([
        point('2026-09-12T19:30:00Z', 4844100),
        point('2026-09-12T16:00:00Z', 4790000),
      ]);

      expect(history.points.single.total.amount.minorUnits, 4844100);
    });

    test('points come out ascending however they went in', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-13T04:00:00Z', 3),
        point('2026-09-11T04:00:00Z', 1),
        point('2026-09-12T04:00:00Z', 2),
      ]);

      expect(
        [for (final p in history.points) p.total.amount.minorUnits],
        [1, 2, 3],
      );
    });
  });

  group('a gap in the record is a gap in the series', () {
    test('consecutive days are one segment', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 1),
        point('2026-09-11T04:00:00Z', 2),
        point('2026-09-12T04:00:00Z', 3),
      ]);

      expect(history.segments, hasLength(1));
      expect(history.hasGap, isFalse);
    });

    test('a missing day splits the run', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 1),
        point('2026-09-11T04:00:00Z', 2),
        // 09-12 never recorded.
        point('2026-09-13T04:00:00Z', 3),
      ]);

      expect(history.segments, hasLength(2));
      expect(history.segments.first, hasLength(2));
      expect(history.segments.last, hasLength(1));
      expect(history.hasGap, isTrue);
    });

    test('the span counts days, not points, so the hole has a width', () {
      final history = NetWorthHistory.reduce([
        point('2026-09-10T04:00:00Z', 1),
        point('2026-09-20T04:00:00Z', 2),
      ]);

      expect(history.points, hasLength(2));
      expect(history.spanInDays, 10);
      expect(history.dayOffsetOf(history.points.last), 10);
    });
  });

  group('a point is a stored total, and nothing later moves it', () {
    test('a later reading leaves every earlier point exactly where it was', () {
      // The phone-side form of task 13's criterion — "revaluing the property in
      // 2026 leaves the 2024 points on the curve unchanged". On this side the
      // deformation cannot arrive from a query, so the way it would arrive is a
      // reduction that lets a new reading touch an older day's point.
      final early = point('2026-09-10T04:00:00Z', 4000000);
      final before = NetWorthHistory.reduce([early]);
      final after = NetWorthHistory.reduce([
        early,
        point('2026-09-11T04:00:00Z', 9999999),
      ]);

      expect(after.points.first.total.amount, before.points.first.total.amount);
      expect(after.points.first.day, before.points.first.day);
    });

    test('no point carries a number that was never stored', () {
      // The sharp end of "never recomputes a past point". Interpolating across
      // the fixture's gap, averaging the two readings of 09-12, or smoothing the
      // series would each put an amount on the curve that appears nowhere in the
      // source — so the assertion is membership, not equality to a guess.
      final source = jsonDecode(demoSeriesJson) as List<Object?>;
      final stored = {
        for (final reading in source)
          ((reading as Map<String, Object?>)['total'] as Map<String, Object?>)['value_minor']
              as int,
      };

      final history = demoSeries();
      expect(history.points, isNotEmpty);
      for (final p in history.points) {
        expect(
          stored,
          contains(p.total.amount.minorUnits),
          reason: 'the curve invented ${p.total.amount.minorUnits} on ${p.day}',
        );
      }
    });
  });

  group('a series in the shape the store writes', () {
    test('parses, and carries both treatments so a test can render them', () {
      final history = demoSeries();

      expect(history.hasGap, isTrue, reason: 'no gap to render');
      expect(history.hasIncompletePoint, isTrue, reason: 'no incomplete reading to render');
    });

    test('its two readings on one day reduce to the later one', () {
      final day = demoSeries()
          .points
          .singleWhere((p) => p.day == DateTime.utc(2026, 9, 12));

      expect(day.total.amount.minorUnits, 4844100);
      expect(day.at, DateTime.utc(2026, 9, 12, 19, 30));
    });

    test('is refused rather than guessed at when it is not a series', () {
      expect(() => parseHistory('{}'), throwsA(isA<PayloadFormatException>()));
      expect(() => parseHistory('not json'), throwsA(isA<PayloadFormatException>()));
      expect(() => parseHistory('[3]'), throwsA(isA<PayloadFormatException>()));
      expect(
        () => parseHistory('[{"total": {}}]'),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('is_complete comes off the wire and is required', () {
    test('a false flag survives the parse onto the point', () {
      final incomplete = demoSeries()
          .points
          .singleWhere((p) => p.day == DateTime.utc(2026, 9, 11));

      expect(incomplete.isComplete, isFalse);
      expect(incomplete.total.isComplete, isFalse);
    });

    test('a total with no is_complete is refused, not assumed complete', () {
      // Assuming `true` is the dangerous default: it would render a partial
      // reading as a whole one, which is precisely §10.5's concern.
      final body = jsonDecode(readFixture(knownFixture)) as Map<String, Object?>;
      final total = Map<String, Object?>.from(body['total']! as Map<String, Object?>)
        ..remove('is_complete');

      expect(
        () => DatedTotal.fromJson(total),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('a non-boolean is_complete is refused too', () {
      final body = jsonDecode(readFixture(knownFixture)) as Map<String, Object?>;
      final total = Map<String, Object?>.from(body['total']! as Map<String, Object?>)
        ..['is_complete'] = 'true';

      expect(
        () => DatedTotal.fromJson(total),
        throwsA(isA<PayloadFormatException>()),
      );
    });
  });

  group('mixed currencies fail loudly (§10 item 6)', () {
    // The series that differs from the accepted one by exactly one field. Any
    // larger difference would leave it open which difference caused the refusal.
    List<HistoryPoint> series({required String secondCurrency}) => [
          point('2026-09-10T04:00:00Z', 100),
          point('2026-09-11T04:00:00Z', 200, currency: secondCurrency),
          point('2026-09-12T04:00:00Z', 300),
        ];

    test('one point in another currency is refused', () {
      expect(
        () => NetWorthHistory.reduce(series(secondCurrency: 'EUR')),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('the same series in one currency is accepted', () {
      // The control. Without it the test above passes just as well against a
      // `reduce` that refuses everything.
      expect(NetWorthHistory.reduce(series(secondCurrency: 'USD')).points, hasLength(3));
    });

    test('a series arriving as JSON is refused at the same place', () {
      // Through `parseHistory`, since that is the path a payload takes. The
      // refusal lives in `reduce` so that a series the app *accumulates* is
      // checked too, but the JSON door must still be shut.
      final source = jsonEncode([
        {
          'published_at': '2026-09-10T04:00:00Z',
          'total': {
            'value_minor': 100,
            'assets_minor': 100,
            'liabilities_minor': 0,
            'currency': 'USD',
            'static_account_count': 0,
            'is_complete': true,
            'age_state': 'KNOWN',
            'as_of': '2026-09-10T04:00:00Z',
          },
        },
        {
          'published_at': '2026-09-11T04:00:00Z',
          'total': {
            'value_minor': 200,
            'assets_minor': 200,
            'liabilities_minor': 0,
            'currency': 'EUR',
            'static_account_count': 0,
            'is_complete': true,
            'age_state': 'KNOWN',
            'as_of': '2026-09-11T04:00:00Z',
          },
        },
      ]);

      expect(() => parseHistory(source), throwsA(isA<PayloadFormatException>()));
    });

    test('the currency getter reports the one currency, and null when empty', () {
      expect(NetWorthHistory.reduce(series(secondCurrency: 'USD')).currency, 'USD');
      expect(NetWorthHistory.empty.currency, isNull);
    });

    group('nor a third one marked "edit"', () {
      // PR #83 review, round 3. Making the constructor private closed the door
      // marked *build*; `points` was still a growable list, so the same
      // invariant fell over after construction:
      //
      //   NetWorthHistory.reduce([usd]).points.add(eur)   // succeeded
      //   history.points[1] = eur                          // succeeded
      //
      // and `currency` still answered USD while the series held both, which is
      // the exact state `reduce` refuses to construct. `final` protects the
      // binding, never the contents — the sorted, one-per-day and
      // never-redraw-the-past guarantees were standing open behind it too.
      //
      // Unlike the source scan below, these are behavioural: the property is
      // observable at runtime, so nothing here has to settle for reading the
      // source. Codex measured 1 control pass / 2 failures on the head before
      // this fix.
      test('the control: a reduced series is readable and says what it holds', () {
        final history = NetWorthHistory.reduce([
          point('2026-09-10T04:00:00Z', 100),
          point('2026-09-11T04:00:00Z', 200),
        ]);
        expect(history.points, hasLength(2));
        expect(history.currency, 'USD');
      });

      test('a later reading cannot be appended past the currency check', () {
        final history = NetWorthHistory.reduce([point('2026-09-10T04:00:00Z', 100)]);
        expect(
          () => history.points.add(point('2026-09-11T04:00:00Z', 200, currency: 'EUR')),
          throwsUnsupportedError,
          reason: 'appending bypasses reduce, and the series would then hold '
              'USD and EUR while currency still answered USD',
        );
      });

      test('nor can an existing point be replaced by assignment', () {
        final history = NetWorthHistory.reduce([
          point('2026-09-10T04:00:00Z', 100),
          point('2026-09-11T04:00:00Z', 200),
        ]);
        expect(
          () => history.points[1] = point('2026-09-11T04:00:00Z', 200, currency: 'EUR'),
          throwsUnsupportedError,
          reason: 'a fixed-length list would still allow this, which is why the '
              'points are unmodifiable rather than merely non-growable',
        );
      });
    });

    group('and there is no second door into the room', () {
      // PR #83 review, round 2. The refusal above lived in `reduce` while the
      // generative constructor stood open beside it, public and `const`:
      // `NetWorthHistory([usd, eur])` walked past the check and the screen drew
      // the chart. Codex found it by *trying* it, which is the only way that
      // shape of defect is ever found.
      //
      // **This is a source scan, and that is a deliberate second-best.** A
      // private constructor cannot be called from a test at all — the failure
      // is a compile error in the test file, not a red test — so "the door is
      // shut" is not a runtime observation this suite can make. What it can do
      // is pin the source property the guarantee rests on, and say out loud
      // that it is the source and not the behaviour being checked. Same
      // technique and same caveat as `release_log_test.dart`.
      final code = File('lib/src/domain/net_worth_history.dart')
          .readAsLinesSync()
          .where((line) => !line.trimLeft().startsWith('//'))
          .join('\n');

      /// Every declaration of a `NetWorthHistory` constructor, named or not.
      final declarations = RegExp(
        r'^\s*(?:const\s+|factory\s+)?NetWorthHistory(\.\w+)?\s*\(',
        multiLine: true,
      ).allMatches(code).map((m) => m.group(1) ?? '<unnamed>').toList();

      test('the scan finds the constructors at all', () {
        // A regex over source is the kind of check that passes by matching
        // nothing. There are two: `._` and `.reduce`.
        expect(declarations, hasLength(2));
      });

      test('every constructor is private or the validating one', () {
        for (final name in declarations) {
          expect(
            name == '._' || name == '.reduce',
            isTrue,
            reason: 'NetWorthHistory$name can build a series without the currency '
                'check; make it private or route it through reduce',
          );
        }
      });
    });
  });
}
