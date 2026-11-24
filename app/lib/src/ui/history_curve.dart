import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../domain/dated_total.dart';
import '../domain/history_range.dart';
import '../domain/net_worth_history.dart';
import 'headline.dart';

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
  const CurveGeometry({required this.strokes, required this.markers, required this.positions});

  final List<CurveStroke> strokes;
  final List<CurveMarker> markers;

  /// Where each point of the series sits, in the series' order — what a finger
  /// on the curve is matched against.
  final List<Offset> positions;

  /// Radius of a reading's dot, and therefore the inset the curve needs on every
  /// side so an extreme reading's dot is not half outside the box.
  static const double markerRadius = 3.5;

  /// Below this many pixels a day, a dot per reading would merge into a smear,
  /// so only the dots that carry information are drawn: an incomplete reading's
  /// hollow one, and a reading with no neighbour to draw a run to.
  static const double minimumDayWidthForEveryMarker = 6;

  static CurveGeometry layOut(NetWorthHistory history, Size size) {
    if (history.isEmpty) {
      return const CurveGeometry(strokes: [], markers: [], positions: []);
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
    // money.
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

    final everyMarker = days == 0 || width / days >= minimumDayWidthForEveryMarker;
    final strokes = <CurveStroke>[];
    final markers = <CurveMarker>[];
    // Iterating **segments**, never `points`: a segment is a run of consecutive
    // days, so there is no pair of adjacent elements here that straddles a
    // missing day. The break is a consequence of the data structure rather than
    // of a check somebody has to remember to write.
    for (final segment in history.segments) {
      for (var index = 0; index < segment.length; index++) {
        final point = segment[index];
        if (everyMarker || !point.isComplete || segment.length == 1) {
          markers.add(CurveMarker(at: positionOf(point), isHollow: !point.isComplete));
        }
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
    return CurveGeometry(
      strokes: strokes,
      markers: markers,
      positions: [for (final point in history.points) positionOf(point)],
    );
  }

  /// The index of the point nearest [dx] across the curve, or null if none.
  int? nearest(double dx) {
    int? best;
    for (var index = 0; index < positions.length; index++) {
      if (best == null || (positions[index].dx - dx).abs() < (positions[best].dx - dx).abs()) {
        best = index;
      }
    }
    return best;
  }
}

/// The net-worth curve, stock-app style: a range, a dated axis, the change
/// across the range, and the reading under the owner's finger.
///
/// **Every figure it renders carries its date.** Invariant I2 has no exception
/// for a chart: the change names the day it is measured from, and the reading
/// under the finger is shown with its own day and its own age annotation —
/// the headline's wording, from the same function — and says so when it was
/// incomplete. There are no scale labels: a bare number on an axis would be a
/// figure with no age at all.
///
/// **A range is the readings that exist inside it, and nothing else.** One
/// longer than the record starts at the first reading and says so in the change
/// line's date; nothing is padded in front of it, joined across a missing day,
/// or drawn past the last reading.
class HistoryCurve extends StatefulWidget {
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

  /// The range a fresh screen opens on.
  static const ChartRange initialRange = ChartRange.oneYear;

  @override
  State<HistoryCurve> createState() => _HistoryCurveState();
}

class _HistoryCurveState extends State<HistoryCurve> {
  ChartRange _range = HistoryCurve.initialRange;

  /// The reading the owner is pointing at, as an index into the range on screen.
  int? _selected;

  @override
  void didUpdateWidget(HistoryCurve old) {
    super.didUpdateWidget(old);
    if (old.history != widget.history) {
      // An index into the old series means nothing in the new one.
      _selected = null;
    }
  }

  void _select(int? index) {
    if (index != _selected) {
      setState(() => _selected = index);
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final history = widget.history;
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
      return _Note(text: widget.recordingFailed ? l10n.historyNotRecorded : l10n.historyEmpty);
    }
    // §10 item 6: one currency, and mixed units fail *loudly*. Drawing the shape
    // anyway would be the loudest thing on the screen and would say nothing
    // true — a euro series under a dollar total renders as a perfectly ordinary
    // picture of the wrong quantity.
    final List<Widget> series = history.currency != widget.headlineCurrency
        ? <Widget>[_Note(text: l10n.historyCurrencyMismatch)]
        : _series(context, l10n, history);
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
        if (widget.recordingFailed) ...[
          const SizedBox(height: 8),
          _Note(text: l10n.historyNotRecorded),
        ],
      ],
    );
  }

  List<Widget> _series(BuildContext context, AppLocalizations l10n, NetWorthHistory history) {
    final theme = Theme.of(context);
    final range = history.since(_range.startFor(history.points.last.day));
    final selected = _selected == null || _selected! >= range.points.length ? null : _selected;
    return <Widget>[
      Text(l10n.historyLabel, style: theme.textTheme.labelMedium),
      const SizedBox(height: 4),
      // The change across the range, or — while a reading is pointed at — that
      // reading, in the same place so the curve does not jump under the finger.
      ConstrainedBox(
        constraints: const BoxConstraints(minHeight: 56),
        child: Align(
          alignment: Alignment.bottomLeft,
          child: selected == null
              ? _Change(change: RangeChange.over(range))
              : _Readout(point: range.points[selected]),
        ),
      ),
      const SizedBox(height: 8),
      SizedBox(
        height: _chartHeight,
        width: double.infinity,
        child: LayoutBuilder(
          builder: (context, constraints) {
            final geometry = CurveGeometry.layOut(range, constraints.biggest);
            return GestureDetector(
              key: const Key('history-chart'),
              behavior: HitTestBehavior.opaque,
              onHorizontalDragStart: (details) =>
                  _select(geometry.nearest(details.localPosition.dx)),
              onHorizontalDragUpdate: (details) =>
                  _select(geometry.nearest(details.localPosition.dx)),
              onHorizontalDragEnd: (_) => _select(null),
              onHorizontalDragCancel: () => _select(null),
              onTapUp: (details) {
                final index = geometry.nearest(details.localPosition.dx);
                _select(index == selected ? null : index);
              },
              child: CustomPaint(
                painter: _CurvePainter(
                  geometry: geometry,
                  selected: selected,
                  line: theme.colorScheme.primary,
                  fill: theme.colorScheme.surface,
                  guide: theme.colorScheme.outline,
                ),
              ),
            );
          },
        ),
      ),
      const SizedBox(height: 4),
      _TimeAxis(first: range.points.first.day, last: range.points.last.day),
      const SizedBox(height: 12),
      _RangeButtons(
        selected: _range,
        onSelected: (range) => setState(() {
          _range = range;
          _selected = null;
        }),
      ),
      // Each note appears only when the thing it describes is on screen.
      // Explaining a treatment the owner cannot see is noise, and it invites
      // him to go looking for a gap that is not there.
      if (range.hasIncompletePoint) ...[
        const SizedBox(height: 8),
        _Note(text: l10n.historyIncompleteNote),
      ],
      if (range.hasGap) ...[
        const SizedBox(height: 4),
        _Note(text: l10n.historyGapNote),
      ],
    ];
  }

  static const double _chartHeight = 180;
}

/// `+$1,234.56 (+1.23%) since Nov 21, 2025` — or nothing, for a single reading.
class _Change extends StatelessWidget {
  const _Change({required this.change});

  final RangeChange? change;

  @override
  Widget build(BuildContext context) {
    final change = this.change;
    if (change == null) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final amount = formatSignedMoney(change.change);
    final percent = change.percentHundredths;
    return Text(
      percent == null
          ? l10n.historyChangeSince(amount, change.from.day)
          : l10n.historyChangeWithPercentSince(amount, formatSignedPercent(percent), change.from.day),
      key: const Key('history-change'),
      style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w600),
    );
  }
}

/// The reading under the finger: its total, its day, and its age.
class _Readout extends StatelessWidget {
  const _Readout({required this.point});

  final HistoryPoint point;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final quiet = theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    final note = staticNote(l10n, point.total);
    return Column(
      key: const Key('history-readout'),
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(
          point.total.amount.format(),
          style: theme.textTheme.titleMedium?.copyWith(fontWeight: FontWeight.w600),
        ),
        Text(l10n.historyReadingDay(point.day), style: quiet),
        // The headline's own sentence for its own state, so an undatable past
        // total is undatable here too rather than dated by its day.
        Text(
          ageAnnotation(l10n, point.total),
          style: point.total is KnownAgeTotal
              ? quiet
              : theme.textTheme.bodySmall?.copyWith(color: theme.colorScheme.error),
        ),
        if (note != null) Text(note, style: quiet),
        if (!point.isComplete) Text(l10n.historyReadingIncomplete, style: quiet),
      ],
    );
  }
}

/// The dated axis under the curve.
class _TimeAxis extends StatelessWidget {
  const _TimeAxis({required this.first, required this.last});

  final DateTime first;
  final DateTime last;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final l10n = AppLocalizations.of(context);
    final style = theme.textTheme.labelSmall?.copyWith(color: theme.colorScheme.onSurfaceVariant);
    final span = last.difference(first).inDays;
    return SizedBox(
      height: 18,
      width: double.infinity,
      child: Stack(
        children: [
          for (final tick in axisTicks(first, last))
            Align(
              // Edge-aware: a mark at either end sits inside the box rather than
              // half outside it, and one in the middle is centred on its day.
              alignment: Alignment(
                span == 0 ? 0 : 2 * tick.day.difference(first).inDays / span - 1,
                0,
              ),
              child: Text(_label(l10n, tick), style: style),
            ),
        ],
      ),
    );
  }

  static String _label(AppLocalizations l10n, AxisTick tick) => switch (tick.unit) {
        AxisUnit.day => l10n.historyAxisDay(tick.day),
        AxisUnit.month => l10n.historyAxisMonth(tick.day),
        AxisUnit.year => l10n.historyAxisYear(tick.day),
      };
}

class _RangeButtons extends StatelessWidget {
  const _RangeButtons({required this.selected, required this.onSelected});

  final ChartRange selected;
  final ValueChanged<ChartRange> onSelected;

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    return SizedBox(
      width: double.infinity,
      child: SegmentedButton<ChartRange>(
        segments: [
          for (final range in ChartRange.values)
            ButtonSegment(
              value: range,
              // One line, always: a segment is a sixth of the width, and on a
              // 360dp phone the text button's own 12dp each side left "YTD"
              // less room than it needs, so it broke as "YT" over "D". Scaled
              // down rather than wrapped when even the narrower padding is not
              // enough, which only a large system font size reaches.
              label: FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(_label(l10n, range), maxLines: 1, softWrap: false),
              ),
            ),
        ],
        selected: {selected},
        onSelectionChanged: (chosen) => onSelected(chosen.single),
        showSelectedIcon: false,
        style: const ButtonStyle(
          visualDensity: VisualDensity.compact,
          tapTargetSize: MaterialTapTargetSize.shrinkWrap,
          padding: WidgetStatePropertyAll(EdgeInsets.symmetric(horizontal: 4)),
        ),
      ),
    );
  }

  static String _label(AppLocalizations l10n, ChartRange range) => switch (range) {
        ChartRange.oneMonth => l10n.historyRangeOneMonth,
        ChartRange.threeMonths => l10n.historyRangeThreeMonths,
        ChartRange.yearToDate => l10n.historyRangeYearToDate,
        ChartRange.oneYear => l10n.historyRangeOneYear,
        ChartRange.threeYears => l10n.historyRangeThreeYears,
        ChartRange.fiveYears => l10n.historyRangeFiveYears,
      };
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
  const _CurvePainter({
    required this.geometry,
    required this.selected,
    required this.line,
    required this.fill,
    required this.guide,
  });

  final CurveGeometry geometry;
  final int? selected;
  final Color line;
  final Color fill;
  final Color guide;

  @override
  void paint(Canvas canvas, Size size) {
    final stroke = Paint()
      ..color = line
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;

    // The dash pattern runs on across consecutive dashed runs, so an
    // incomplete stretch still reads as dashed when a day is narrower than a
    // dash — at five years, every day is.
    var phase = 0.0;
    Offset? end;
    for (final run in geometry.strokes) {
      if (!run.isDashed) {
        canvas.drawLine(run.from, run.to, stroke);
        end = null;
        continue;
      }
      if (run.from != end) {
        phase = 0;
      }
      phase = _dashed(canvas, run.from, run.to, stroke, phase);
      end = run.to;
    }

    final index = selected;
    if (index != null && index < geometry.positions.length) {
      final at = geometry.positions[index];
      canvas.drawLine(
        Offset(at.dx, 0),
        Offset(at.dx, size.height),
        Paint()
          ..color = guide
          ..strokeWidth = 1,
      );
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
    if (index != null && index < geometry.positions.length) {
      canvas.drawCircle(geometry.positions[index], CurveGeometry.markerRadius + 1.5, dot);
    }
  }

  /// Draws [from]→[to] dashed, starting [phase] pixels into the pattern, and
  /// returns the phase the next run should start at.
  static double _dashed(Canvas canvas, Offset from, Offset to, Paint paint, double phase) {
    const double dash = 4;
    const double gap = 3;
    const double period = dash + gap;
    final delta = to - from;
    final length = delta.distance;
    if (length == 0) {
      return phase;
    }
    final unit = delta / length;
    var travelled = -phase;
    while (travelled < length) {
      final start = math.max(travelled, 0.0);
      final stop = math.min(travelled + dash, length);
      if (stop > start) {
        canvas.drawLine(from + unit * start, from + unit * stop, paint);
      }
      travelled += period;
    }
    return (phase + length) % period;
  }

  @override
  bool shouldRepaint(_CurvePainter old) =>
      old.geometry != geometry ||
      old.selected != selected ||
      old.line != line ||
      old.fill != fill ||
      old.guide != guide;
}
