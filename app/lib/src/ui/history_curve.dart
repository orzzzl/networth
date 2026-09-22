import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/net_worth_history.dart';

/// One straight run between two adjacent days.
///
/// Every stroke spans exactly one day. That is not an implementation detail to
/// be read off the code — it is the property that makes a hole in the record
/// impossible to paint over, and `test/history_curve_test.dart` asserts it
/// directly.
@immutable
class CurveStroke {
  const CurveStroke({required this.from, required this.to, required this.isDashed});

  final Offset from;
  final Offset to;

  /// True when either endpoint is an incomplete reading.
  ///
  /// Either, not both: a run from a whole reading to a partial one is not a run
  /// we can vouch for, and drawing half of it solid would vouch for it.
  final bool isDashed;
}

/// One reading's dot.
@immutable
class CurveMarker {
  const CurveMarker({required this.at, required this.isHollow});

  final Offset at;

  /// `is_complete = FALSE`. Hollow, per §10.5's "dashed/hollow".
  final bool isHollow;
}

/// Where every stroke and dot goes, given a series and a box to draw it in.
///
/// **Separated from the painter so the rules can be tested as arithmetic rather
/// than as pixels.** A golden image can tell you the picture changed; it cannot
/// tell you whether a line crossed a day nobody recorded, which is the one thing
/// this widget must never do.
@immutable
class CurveGeometry {
  const CurveGeometry({required this.strokes, required this.markers});

  final List<CurveStroke> strokes;
  final List<CurveMarker> markers;

  /// Radius of a reading's dot, and therefore the inset the curve needs on every
  /// side so an extreme reading's dot is not half outside the box.
  static const double markerRadius = 3.5;

  static CurveGeometry layOut(NetWorthHistory history, Size size) {
    if (history.isEmpty) {
      return const CurveGeometry(strokes: [], markers: []);
    }

    final left = markerRadius;
    final top = markerRadius;
    final width = math.max(0.0, size.width - markerRadius * 2);
    final height = math.max(0.0, size.height - markerRadius * 2);

    final amounts = [for (final point in history.points) point.total.amount.minorUnits];
    final lowest = amounts.reduce(math.min);
    final highest = amounts.reduce(math.max);
    // The balances stay integers all the way to here (`AGENTS.md`: money is
    // never a float). What becomes a double is the *pixel offset* — a position
    // in a box, not an amount — and no value derived below is ever rendered as
    // money. The curve shows no figures at all; see [HistoryCurve].
    final span = highest - lowest;
    final days = history.spanInDays;

    Offset positionOf(HistoryPoint point) {
      // A single reading, or a flat series, has no extent to scale against, so
      // it is centred rather than divided by zero.
      final x = days == 0 ? left + width / 2 : left + width * history.dayOffsetOf(point) / days;
      final y = span == 0
          ? top + height / 2
          : top + height * (highest - point.total.amount.minorUnits) / span;
      return Offset(x, y);
    }

    final strokes = <CurveStroke>[];
    final markers = <CurveMarker>[];
    // Iterating **segments**, never `points`: a segment is a run of consecutive
    // days, so there is no pair of adjacent elements here that straddles a
    // missing day. The break is a consequence of the data structure rather than
    // of a check somebody has to remember to write.
    for (final segment in history.segments) {
      for (var index = 0; index < segment.length; index++) {
        final point = segment[index];
        markers.add(CurveMarker(at: positionOf(point), isHollow: !point.isComplete));
        if (index == 0) {
          continue;
        }
        final previous = segment[index - 1];
        strokes.add(
          CurveStroke(
            from: positionOf(previous),
            to: positionOf(point),
            isDashed: !previous.isComplete || !point.isComplete,
          ),
        );
      }
    }
    return CurveGeometry(strokes: strokes, markers: markers);
  }
}

/// The net-worth curve.
///
/// **It renders no figures, and that is invariant I2 rather than an omission.**
/// There is no widget in this app that takes a bare amount; an axis label would
/// be exactly that — a number with no age beside it — and a series of them would
/// be many. The number lives in `Headline` directly above, with its age state
/// attached. What the curve adds is shape over time, plus the two things §10.5
/// asks it to make visible: which readings were incomplete, and which days have
/// no reading at all.
class HistoryCurve extends StatelessWidget {
  const HistoryCurve({
    super.key,
    required this.history,
    required this.recordingFailed,
    required this.headlineCurrency,
  });

  /// The series, or **null when it could not be read**.
  ///
  /// The two are different facts and the widget says which one it has. Folding
  /// an unreadable series into the empty one would render "no readings recorded
  /// yet" over a record that exists and simply did not load — a false statement
  /// about the owner's own history, and the same shape as the failure this
  /// project exists to refuse, one layer down.
  final NetWorthHistory? history;

  /// Whether the reading on screen failed to reach the record.
  ///
  /// **The third state, independent of the other two.** Reading and writing the
  /// record fail separately, so a readable series says nothing about whether
  /// today's reading was kept. Review reproduced the state this exists for: a
  /// store that loads an empty series and refuses every write rendered "no
  /// readings recorded yet" — which is the same false claim as the one above,
  /// made about the future instead of the past, and it repeats silently on every
  /// launch while each new reading is lost.
  final bool recordingFailed;

  /// The currency of the total shown above this curve.
  ///
  /// Required rather than optional on purpose: a caller that does not have the
  /// headline in scope has no business drawing the curve under it, and a
  /// defaulted parameter is how this check would quietly stop being made.
  final String headlineCurrency;

  static const double _height = 120;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final history = this.history;
    if (history == null) {
      // Not also the recording note. With the store validating the same bytes on
      // both paths, a record this could not read is a record it would refuse to
      // write, so the two would fire together and say one thing twice.
      return _Note(text: l10n.historyUnreadable);
    }
    if (history.isEmpty) {
      // **Replaced rather than joined**, the only branch where that is right.
      // "no readings recorded yet" is literally true of an empty store, which
      // is exactly what makes it the wrong sentence: the *yet* promises the
      // readings are on their way, when the reason there are none is that every
      // one of them is being dropped.
      return _Note(text: recordingFailed ? l10n.historyNotRecorded : l10n.historyEmpty);
    }
    // §10 item 6: one currency, and mixed units fail *loudly*. Drawing the shape
    // anyway would be the loudest thing on the screen and would say nothing —
    // the curve has no figures, so a euro series under a dollar total renders as
    // a perfectly ordinary picture of the wrong quantity.
    final List<Widget> series = history.currency != headlineCurrency
        ? <Widget>[_Note(text: l10n.historyCurrencyMismatch)]
        : <Widget>[
            Text(l10n.historyLabel, style: theme.textTheme.labelMedium),
            const SizedBox(height: 8),
            SizedBox(
              height: _height,
              width: double.infinity,
              child: CustomPaint(
                painter: _CurvePainter(
                  history: history,
                  line: theme.colorScheme.primary,
                  fill: theme.colorScheme.surface,
                ),
              ),
            ),
            // Each note appears only when the thing it describes is on screen.
            // Explaining a treatment the owner cannot see is noise, and it
            // invites him to go looking for a gap that is not there.
            if (history.hasIncompletePoint) ...[
              const SizedBox(height: 8),
              _Note(text: l10n.historyIncompleteNote),
            ],
            if (history.hasGap) ...[
              const SizedBox(height: 4),
              _Note(text: l10n.historyGapNote),
            ],
          ];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        ...series,
        // **Appended to every branch that draws a series, by construction.**
        // The two above are the curve and the currency refusal, and the warning
        // belongs under both: a series on screen says the record can be *read*
        // and nothing at all about whether today's reading was kept. Written as
        // one append rather than a line in each branch, so a third branch added
        // later inherits it instead of being a third place to remember.
        if (recordingFailed) ...[
          const SizedBox(height: 8),
          _Note(text: l10n.historyNotRecorded),
        ],
      ],
    );
  }
}

class _Note extends StatelessWidget {
  const _Note({required this.text});

  final String text;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Text(
      text,
      style: theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant),
    );
  }
}

class _CurvePainter extends CustomPainter {
  const _CurvePainter({required this.history, required this.line, required this.fill});

  final NetWorthHistory history;
  final Color line;
  final Color fill;

  @override
  void paint(Canvas canvas, Size size) {
    final geometry = CurveGeometry.layOut(history, size);
    final stroke = Paint()
      ..color = line
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    for (final run in geometry.strokes) {
      if (run.isDashed) {
        _dashed(canvas, run.from, run.to, stroke);
      } else {
        canvas.drawLine(run.from, run.to, stroke);
      }
    }

    // Shape carries the distinction, not colour — a dashed run and a hollow dot
    // read the same to a colour-blind eye and in a grayscale screenshot.
    final dot = Paint()
      ..color = line
      ..style = PaintingStyle.fill;
    final hollowCentre = Paint()
      ..color = fill
      ..style = PaintingStyle.fill;
    for (final marker in geometry.markers) {
      canvas.drawCircle(marker.at, CurveGeometry.markerRadius, dot);
      if (marker.isHollow) {
        canvas.drawCircle(marker.at, CurveGeometry.markerRadius - 1.5, hollowCentre);
      }
    }
  }

  static void _dashed(Canvas canvas, Offset from, Offset to, Paint paint) {
    const double dash = 4;
    const double gap = 3;
    final delta = to - from;
    final length = delta.distance;
    if (length == 0) {
      return;
    }
    final unit = delta / length;
    var travelled = 0.0;
    while (travelled < length) {
      final end = math.min(travelled + dash, length);
      canvas.drawLine(from + unit * travelled, from + unit * end, paint);
      travelled = end + gap;
    }
  }

  @override
  bool shouldRepaint(_CurvePainter old) =>
      old.history != history || old.line != line || old.fill != fill;
}
