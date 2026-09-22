import 'package:flutter/foundation.dart';

import 'dated_total.dart';
import 'instant.dart';
import 'payload_format_exception.dart';

/// The UTC calendar day an instant falls on, as UTC midnight.
///
/// `.toUtc()` first, always. The components of a local `DateTime` are its local
/// ones, so bucketing without the conversion would put a 23:30Z reading on the
/// previous day for a device in `-07:00` — the same class of error
/// `parseWireInstant` refuses zone-less strings to avoid. `AGENTS.md` puts all
/// staleness math in UTC and `instant.dart` renders in UTC rather than
/// converting, so the curve's days are the same days the rest of the app names.
DateTime utcDayOf(DateTime instant) {
  final utc = instant.toUtc();
  return DateTime.utc(utc.year, utc.month, utc.day);
}

/// One point on the curve: a total the host computed and stored, on the UTC day
/// it was published.
///
/// **The point holds the whole [DatedTotal], not its amount.** Invariant **I2**
/// — no total rendered without its age state — has no exception for a chart, and
/// a series of bare numbers is still numbers with no age. So there is no
/// constructor here that takes a bare `Money`, and the curve cannot acquire one
/// to plot. That is also why it draws no value labels (`ui/history_curve.dart`).
@immutable
class HistoryPoint {
  const HistoryPoint({required this.publishedAt, required this.total});

  /// One stored reading: the instant it was published and the total as stored.
  ///
  /// The `total` object goes through [DatedTotal.fromJson] — the same strict
  /// parser the headline uses — so a reading the headline would have refused
  /// cannot enter the curve by a side door.
  factory HistoryPoint.fromJson(Map<String, Object?> reading) {
    final total = reading['total'];
    if (total is! Map<String, Object?>) {
      throw const PayloadFormatException('history reading total is not a JSON object');
    }
    final publishedAt = reading['published_at'];
    if (publishedAt is! String) {
      throw const PayloadFormatException('history reading published_at is not a string');
    }
    return HistoryPoint(
      publishedAt: parseWireInstant(publishedAt, field: 'published_at'),
      total: DatedTotal.fromJson(total),
    );
  }

  /// When the host published this reading.
  ///
  /// The host's snapshot row has `taken_at`, which is what "when this reading
  /// was taken" properly means, but `publisher.py::_plaintext` does not put it
  /// on the wire — so this is the only per-publication instant the phone has,
  /// and it is a proxy rather than the thing itself.
  ///
  /// Deliberately **not** `total.as_of`: two readings whose data did not advance
  /// carry one `as_of` and would land on the same day, erasing the fact that
  /// time passed without news; and `UNKNOWN`/`STATIC_ONLY` totals carry no
  /// `as_of` at all, so half the states could not be placed.
  final DateTime publishedAt;

  final DatedTotal total;

  /// `snapshot.is_complete`, read off the total rather than stored again here.
  ///
  /// Two copies of one fact is how they come to disagree: the total on this
  /// point and the flag describing it arrived in the same `total` object, and
  /// nothing may set one without the other.
  bool get isComplete => total.isComplete;

  /// The UTC day this point sits on. The curve's x axis.
  DateTime get day => utcDayOf(publishedAt);
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
      if (held == null || held.publishedAt.isBefore(reading.publishedAt)) {
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
}

/// Whole days between two UTC midnights.
///
/// Both arguments come from [utcDayOf], so this is exact: UTC has no DST, and
/// the difference of two midnights is a whole number of 24-hour days.
int _daysBetween(DateTime from, DateTime to) => to.difference(from).inDays;
