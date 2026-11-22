import 'package:flutter/foundation.dart';

import 'dated_total.dart';
import 'instant.dart';
import 'display_zone.dart';
import 'payload_format_exception.dart';

/// One point on the curve: a total the host computed and stored, on the
/// calendar day it is placed at where the phone is.
///
/// **The phone's own days, because those are the days the owner reads.** Every
/// time on screen is in the device's zone (`ui/instant.dart`), and a reading
/// taken at 00:07Z on the 22nd is his afternoon of the 21st in Los Angeles;
/// filing it under the 22nd would put a date on the curve that had not yet
/// arrived where he was. The host picks each day's last snapshot by his home
/// zone (`networth/publisher.py::OWNER_ZONE`), and this re-buckets by the
/// phone's, so the two agree whenever the phone is at home. Staleness math
/// stays UTC; this is only which day a point is drawn on.
@immutable
class HistoryPoint {
  const HistoryPoint({required this.at, required this.total});

  /// One reading of the phone's own record: the instant the payload was
  /// published, and its total as stored.
  ///
  /// The `total` object goes through [DatedTotal.fromJson] — the same strict
  /// parser the headline uses — so a reading the headline would have refused
  /// cannot enter the curve by a side door.
  factory HistoryPoint.fromJson(Map<String, Object?> reading) =>
      _fromJson(reading, instantField: 'published_at');

  /// One point of the series the host publishes (`publisher.py::_history`):
  /// the snapshot's own `taken_at`, and its total as stored.
  factory HistoryPoint.fromHostJson(Map<String, Object?> point) =>
      _fromJson(point, instantField: 'taken_at');

  static HistoryPoint _fromJson(Map<String, Object?> reading, {required String instantField}) {
    final total = reading['total'];
    if (total is! Map<String, Object?>) {
      throw const PayloadFormatException('history reading total is not a JSON object');
    }
    final at = reading[instantField];
    if (at is! String) {
      throw PayloadFormatException('history reading $instantField is not a string');
    }
    return HistoryPoint(
      at: parseWireInstant(at, field: instantField),
      total: DatedTotal.fromJson(total),
    );
  }

  /// The instant this reading is placed at.
  ///
  /// For a point of the host's series it is the snapshot's `taken_at` — what
  /// "when this reading was taken" properly means. For a reading of the phone's
  /// own record it is the payload's `published_at`, the only per-publication
  /// instant a payload without a series carries, and a proxy rather than the
  /// thing itself.
  ///
  /// Deliberately **not** `total.as_of`: two readings whose data did not advance
  /// carry one `as_of` and would land on the same day, erasing the fact that
  /// time passed without news; and `UNKNOWN`/`STATIC_ONLY` totals carry no
  /// `as_of` at all, so half the states could not be placed.
  final DateTime at;

  final DatedTotal total;

  /// `snapshot.is_complete`, read off the total rather than stored again here.
  ///
  /// Two copies of one fact is how they come to disagree: the total on this
  /// point and the flag describing it arrived in the same `total` object, and
  /// nothing may set one without the other.
  bool get isComplete => total.isComplete;

  /// The day this point sits on where the phone is. The curve's x axis.
  DateTime get day => localDayOf(at);
}

/// The curve's points, one per day, already split where the record has a hole.
///
/// **[segments] is why this type exists rather than a bare `List`.** "A gap in
/// the record must look like a gap" is not a property a painter can be trusted
/// to remember; here the painter is handed runs of consecutive days and never
/// the whole series, so drawing a line across a day nobody recorded would take a
/// deliberate flatten rather than a forgotten check. Interpolating across that
/// hole would assert a value that was never stored, which is the same class of
/// lie §12 rules out — just arriving from the renderer instead of the query.
@immutable
class NetWorthHistory {
  /// **Private, and that is the whole enforcement.**
  ///
  /// The currency refusal below lived in [reduce] while this constructor stood
  /// open beside it, public and `const` — so `NetWorthHistory([usd, eur])`
  /// walked straight past the check and the screen drew the chart. A guard on
  /// the funnel is worth nothing while there is a second door into the same
  /// room, and review found this one by trying it rather than by reading.
  ///
  /// So there is now exactly one way to build a non-empty series, and it is the
  /// one that validates. Points arrive already one-per-day and ascending
  /// because [reduce] is what puts them that way.
  const NetWorthHistory._(this.points);

  /// The only [const] instance, and the only one that needs no checking: an
  /// empty series cannot mix anything.
  static const NetWorthHistory empty = NetWorthHistory._(<HistoryPoint>[]);

  /// Ascending by day, at most one per day.
  ///
  /// **Unmodifiable, and that is part of the invariant rather than tidiness.**
  /// `final` protects the binding, not the contents: while this was a growable
  /// list, `reduce([usd]).points.add(eur)` walked past the currency check after
  /// construction, and `points[1] = eur` did the same by assignment. Making the
  /// constructor private closed the door marked *build*; this closes the one
  /// marked *edit*, and the sorted, one-per-day and never-redraw-the-past
  /// guarantees were standing open behind it too.
  final List<HistoryPoint> points;

  /// Collapse readings to the curve's points: **the latest reading of each day**,
  /// and the only public way to build a non-empty series.
  ///
  /// `DESIGN.md` §7 states the rule rather than leaving it to the renderer —
  /// *"'Today's number' is a view (latest row, or latest per day for the
  /// curve)"* — because snapshots are appended, so a day with three successful
  /// runs has three rows and one point.
  ///
  /// Nothing here recomputes a total. Each point carries the number the host
  /// stored, and the only decision made is which stored reading a day shows.
  factory NetWorthHistory.reduce(Iterable<HistoryPoint> readings) {
    final latestPerDay = <DateTime, HistoryPoint>{};
    String? currency;
    for (final reading in readings) {
      // `DESIGN.md` §10 item 6: *"Single currency (USD). The schema carries
      // currency so mixed units fail loudly."* Loudly is the operative word —
      // this curve draws no figures, so two currencies would render as one
      // plausible shape with nothing on screen able to give it away. Comparing
      // integer minor units across currencies is not a rendering bug that looks
      // wrong; it is arithmetic on quantities that are not the same quantity.
      //
      // **Here rather than in `parseHistory`**, because this is the one funnel:
      // every series in the app is built by this factory, including any a later
      // task accumulates from live payloads rather than parses from JSON. A
      // check on the parser would cover today's only caller and leave the one
      // that does not exist yet uncovered.
      final readingCurrency = reading.total.amount.currency;
      currency ??= readingCurrency;
      if (readingCurrency != currency) {
        throw PayloadFormatException(
          'history mixes currencies: $currency and $readingCurrency',
        );
      }
      final held = latestPerDay[reading.day];
      // `isBefore`, so an out-of-order arrival cannot displace a later reading
      // of the same day. I6 (§9.3) is the real defence — the phone refuses a
      // payload whose `seq` regressed — but a store that reorders whatever it is
      // handed is not relying on a caller having done that first.
      if (held == null || held.at.isBefore(reading.at)) {
        latestPerDay[reading.day] = reading;
      }
    }
    final days = latestPerDay.keys.toList()..sort();
    // `unmodifiable`, not `List.of(..., growable: false)`: a fixed-length list
    // still accepts `points[i] = other`, which is the same bypass by a
    // different verb.
    return NetWorthHistory._(
      List<HistoryPoint>.unmodifiable([for (final day in days) latestPerDay[day]!]),
    );
  }

  bool get isEmpty => points.isEmpty;

  /// The one currency every point is in, or null when there are no points.
  ///
  /// Safe to read off the first point because [reduce] refuses a series that
  /// mixes them; this getter exists so the *screen* can check the other half —
  /// that the series and the headline above it are the same quantity. A series
  /// that agrees with itself and disagrees with the total is exactly the state
  /// the refusal in [reduce] cannot see.
  String? get currency => isEmpty ? null : points.first.total.amount.currency;

  /// Runs of consecutive days. A day with no reading ends a run.
  List<List<HistoryPoint>> get segments {
    final result = <List<HistoryPoint>>[];
    for (final point in points) {
      final current = result.isEmpty ? null : result.last;
      if (current == null || _daysBetween(current.last.day, point.day) != 1) {
        result.add(<HistoryPoint>[point]);
      } else {
        current.add(point);
      }
    }
    return result;
  }

  /// Whether any day inside the rendered span has no reading.
  bool get hasGap => segments.length > 1;

  bool get hasIncompletePoint => points.any((point) => !point.isComplete);

  /// Whole days from the first point's day to the last's; 0 for one point.
  ///
  /// The x axis is this span, **not the point count**, so a missing day takes up
  /// its own width. Indexing by position would put the two sides of a gap next
  /// to each other and the break would render as a hairline artifact rather than
  /// as the hole it is.
  int get spanInDays =>
      isEmpty ? 0 : _daysBetween(points.first.day, points.last.day);

  /// Whole days from the first point's day to [point]'s.
  int dayOffsetOf(HistoryPoint point) => _daysBetween(points.first.day, point.day);

  /// The points on or after [day] — a range of the chart.
  ///
  /// A subset of a series [reduce] built is still one point per day, ascending
  /// and in one currency, so nothing needs checking again. A range that reaches
  /// back before the first reading simply starts at the first reading: nothing
  /// is padded in front of it.
  NetWorthHistory since(DateTime day) => NetWorthHistory._(
        List<HistoryPoint>.unmodifiable(points.where((point) => !point.day.isBefore(day))),
      );
}

/// Whole days between two day keys.
///
/// Both arguments are [ZonedTime.day] values — UTC midnights standing for
/// local dates — so this is exact: the difference of two UTC midnights is
/// a whole number of 24-hour days, whatever the offset was on either date.
int _daysBetween(DateTime from, DateTime to) => to.difference(from).inDays;

/// What a payload carries for the curve.
///
/// Three answers, kept apart for the reason `HistoryCurve` keeps an unreadable
/// record apart from an empty one: drawing "no readings yet" over a series that
/// arrived and failed to parse would be a false statement about the owner's own
/// history.
sealed class CarriedHistory {
  const CarriedHistory();

  /// Read the payload body's `history`, without ever refusing the payload.
  ///
  /// **A damaged series must not take the total off the screen** — the number
  /// with its age is what this product is for, and the curve is what it adds —
  /// so a series that does not parse becomes [HistoryUnreadable] here rather
  /// than a [PayloadFormatException] that would fail the whole copy.
  factory CarriedHistory.fromBody(Map<String, Object?> body) {
    if (!body.containsKey('history')) {
      return const HistoryNotCarried();
    }
    final value = body['history'];
    if (value is! List<Object?>) {
      return const HistoryUnreadable('history is not a JSON array');
    }
    try {
      final points = [
        for (final point in value)
          if (point is Map<String, Object?>)
            HistoryPoint.fromHostJson(point)
          else
            throw const PayloadFormatException('history point is not a JSON object'),
      ];
      return HistoryCarried(NetWorthHistory.reduce(points));
    } on PayloadFormatException catch (error) {
      return HistoryUnreadable(error.message);
    }
  }
}

/// The payload has no `history` key: a host that predates the series, or a
/// copy saved by an app that did. The phone's own record is what is left.
final class HistoryNotCarried extends CarriedHistory {
  const HistoryNotCarried();
}

/// The host's own series, as it published it.
final class HistoryCarried extends CarriedHistory {
  const HistoryCarried(this.history);

  final NetWorthHistory history;
}

/// The payload carried a series this build could not read.
final class HistoryUnreadable extends CarriedHistory {
  const HistoryUnreadable(this.reason);

  /// For `debugLog`, never the screen.
  final String reason;
}
