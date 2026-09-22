import 'dart:convert';

import '../domain/net_worth_history.dart';
import '../domain/payload_format_exception.dart';

/// Where the curve's points come from.
///
/// **The payload carries one total, not a series.** `publisher.py::_plaintext`
/// sends a single `total`, and `DESIGN.md` §6.2 states the other half where it
/// describes what a compromised phone leaks — *"the phone's local cache … does
/// contain the history window the curve renders, because the curve has to come
/// from somewhere."* So the series is the phone's, accumulated one reading per
/// accepted payload and kept across launches.
///
/// **This is the reading half only**, and the split is deliberate: `HomePage`
/// draws the curve and has no business being able to write the record. The
/// writing half is `HistoryStore` in `history_store.dart`, which implements this
/// interface and adds `record`, so the one object the app wires is both and the
/// one thing the screen is handed is this.
abstract interface class HistorySource {
  Future<NetWorthHistory> load();
}

/// Parse a series of readings into the curve's points.
///
/// Refuses rather than guesses, the same way `DatedTotal.fromJson` does — each
/// reading's `total` goes through that very parser, so a series cannot hold a
/// total the headline would have rejected.
///
/// Reduction to one point per day happens here and nowhere else
/// ([NetWorthHistory.reduce]), which is the rule `DESIGN.md` §7 states rather
/// than leaves to a renderer.
///
/// The one producer of this format is now `FileHistoryStore`, which writes what
/// this reads: the store's file *is* a series, so the bytes the app records and
/// the bytes it can render back are checked against each other by construction
/// rather than by two formats agreeing.
NetWorthHistory parseHistory(String source) {
  final Object? decoded;
  try {
    decoded = jsonDecode(source);
  } on FormatException catch (error) {
    throw PayloadFormatException('history is not JSON: ${error.message}');
  }
  if (decoded is! List<Object?>) {
    throw const PayloadFormatException('history is not a JSON array');
  }
  return NetWorthHistory.reduce([
    for (final reading in decoded) HistoryPoint.fromJson(_object(reading)),
  ]);
}

Map<String, Object?> _object(Object? reading) {
  if (reading is! Map<String, Object?>) {
    throw const PayloadFormatException('history reading is not a JSON object');
  }
  return reading;
}
