import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../domain/dated_total.dart';
import '../domain/net_worth_history.dart';
import '../domain/payload_format_exception.dart';
import '../domain/phone_payload.dart';
import 'history_source.dart';

/// The phone's own record of the readings it accepted — the curve's other half.
///
/// `23` built the curve and `DESIGN.md` §6.2 names where its points live: *"the
/// phone's local cache … does contain the history window the curve renders,
/// because the curve has to come from somewhere."* The payload carries one
/// total, not a series (`publisher.py::_plaintext`), so the series is
/// accumulated here, one reading per accepted payload.
abstract interface class HistoryStore implements HistorySource {
  /// Record one accepted payload. Idempotent: recording the same payload twice
  /// writes nothing the second time.
  Future<void> record(PhonePayload payload);
}

/// A JSON array in the app's own directory. Nothing else reads it.
///
/// **Outside the key vault, deliberately** (task `23a`). `flutter_secure_storage`
/// holds the pairing bundle because that key is the revocable thing; the owner's
/// own curve is not a capability, so re-pairing — or losing and re-provisioning
/// secure storage entirely — must leave the record standing. Nothing in this
/// file reads or writes `pairing_id`: a point is identified by `published_at`
/// and `seq`, both of which are the host's, so a new pairing continues the same
/// series rather than starting a second one.
///
/// **The file format is the format the curve already parses.** [load] is
/// `parseHistory` over these bytes, the same strict parser the bundled series
/// went through, so a reading this store wrote and a reading it could not read
/// back cannot be different things.
class FileHistoryStore implements HistoryStore {
  FileHistoryStore({
    required this.open,
    this.retainedDays = defaultRetainedDays,
  }) : assert(retainedDays > 0);

  /// The production wiring: `<app documents>/history.json`.
  ///
  /// `getApplicationDocumentsDirectory()` is the platform's app-private path —
  /// on Android `/data/data/<package>/app_flutter`, readable by this app and
  /// nothing else, and `android:allowBackup="false"` in the manifest keeps it on
  /// the device. The whole of `path_provider`'s job here is resolving that path;
  /// the file is `dart:io`.
  factory FileHistoryStore.appPrivate({int retainedDays = defaultRetainedDays}) =>
      FileHistoryStore(
        open: () async => File(
          '${(await getApplicationDocumentsDirectory()).path}/$fileName',
        ),
        retainedDays: retainedDays,
      );

  static const String fileName = 'history.json';

  /// **The bound, and it is in days rather than readings on purpose.**
  ///
  /// The store keeps at most one reading per UTC day — the latest recorded for
  /// that day, which is the point §7 says the curve draws — so the file grows
  /// with *days*, not with how often the app is opened or how often the host
  /// publishes. A phone that fetches every five minutes and one that fetches
  /// once a day reach the same size.
  ///
  /// **What 400 costs, and it is 400 *recorded* days rather than a time
  /// window.** The cap is on entries, and an entry exists only for a day the
  /// phone actually recorded something — so on a phone fetching daily it is just
  /// over thirteen months, and on one opened occasionally the same 400 entries
  /// can reach back years. Size is bounded either way, which is what the bound
  /// is for: about 100 KB of JSON at ~250 bytes a reading. Past the cap the
  /// oldest recorded day is dropped and the phone has genuinely forgotten it —
  /// the only complete record is the host's SQLite, which keeps every snapshot
  /// row (§7). The other half of the cost is the one §6.2 measures: a stolen
  /// phone leaks the window it holds, and this is what bounds it. Stated as a
  /// duration it would be a claim about calendar reach that nothing enforces.
  static const int defaultRetainedDays = 400;

  /// Where the file is. Injected so a test uses a temporary directory rather
  /// than the host's real documents directory, and so the production path is
  /// named in exactly one place ([FileHistoryStore.appPrivate]).
  final Future<File> Function() open;

  /// How many daily readings to keep. See [defaultRetainedDays].
  final int retainedDays;

  @override
  Future<NetWorthHistory> load() async {
    final file = await open();
    if (!await file.exists()) {
      // A new install has recorded nothing, and that is a true statement rather
      // than a failure: `HistoryCurve` renders it as "no readings recorded yet".
      return NetWorthHistory.empty;
    }
    return parseHistory(await file.readAsString());
  }

  @override
  Future<void> record(PhonePayload payload) async {
    final file = await open();
    // Read first, and **never** fall back to an empty list on a file that exists
    // but does not parse — where "does not parse" is everything [load] refuses,
    // including a malformed total (see [_readingFromStored]). A store that
    // recovers by overwriting is a store that silently deletes the owner's
    // record the one time something goes wrong with it; the failure travels up
    // instead, where `RecordingSnapshotSource` turns it into a state the screen
    // shows, and the bytes are still on disk for the next launch.
    final stored = await _read(file);
    final merged = _merge(stored, _readingOf(payload));
    if (merged == null) {
      return;
    }
    await _write(file, merged);
  }

  /// One accepted payload, as a reading.
  ///
  /// `total` comes straight from [DatedTotal.toJson], which is the inverse of
  /// the parser that built it — so the stored number is the host's, not one this
  /// app computed. `published_at` identifies the point (never `pairing_id`), and
  /// `seq` rides along as the second half of that identity: two readings of the
  /// same instant from different publications are different readings, and the
  /// phone would otherwise have no way to say so.
  _Reading _readingOf(PhonePayload payload) => (
        publishedAt: payload.publishedAt,
        seq: payload.seq,
        json: <String, Object?>{
          'published_at': payload.publishedAt.toIso8601String(),
          'seq': payload.seq,
          'total': payload.total.toJson(),
        },
      );

  /// The stored readings with [reading] merged in, or null when there is
  /// nothing to write.
  ///
  /// **A later payload never mutates an earlier point.** A reading only ever
  /// touches its own UTC day: every other day in the list comes back untouched,
  /// so revaluing in 2026 leaves the 2024 points exactly as they were stored —
  /// the phone-side analogue of `13`'s criterion.
  ///
  /// Within one day the later reading wins, which is `NetWorthHistory.reduce`'s
  /// rule (§7: *"latest per day for the curve"*) applied where the bytes are
  /// rather than only where they are drawn. **It is a selection, never an
  /// arithmetic:** the day shows a reading the host published, and no reading is
  /// ever computed from another.
  ///
  /// `isBefore` is the comparison, so a reading that is not strictly later than
  /// the day's stored one changes nothing — which is what makes re-recording the
  /// same payload (every app launch, for as long as the host publishes nothing
  /// new) a no-op, and what stops an out-of-order arrival from displacing a
  /// later reading of the same day.
  List<_Reading>? _merge(List<_Reading> stored, _Reading reading) {
    final day = utcDayOf(reading.publishedAt);
    final existing = stored.indexWhere((other) => utcDayOf(other.publishedAt) == day);
    final List<_Reading> merged;
    if (existing >= 0) {
      if (!stored[existing].publishedAt.isBefore(reading.publishedAt)) {
        return null;
      }
      merged = List<_Reading>.of(stored)..[existing] = reading;
    } else {
      merged = List<_Reading>.of(stored)..add(reading);
      merged.sort((a, b) => a.publishedAt.compareTo(b.publishedAt));
    }
    // Drop from the front: the oldest days are the ones the window has moved
    // past. The bound is applied on write rather than on read so that the file
    // itself is bounded — trimming only at render time would leave the size on
    // disk growing forever with nothing to show for it.
    return merged.length <= retainedDays
        ? merged
        : merged.sublist(merged.length - retainedDays);
  }

  Future<List<_Reading>> _read(File file) async {
    if (!await file.exists()) {
      return const <_Reading>[];
    }
    final Object? decoded;
    try {
      decoded = jsonDecode(await file.readAsString());
    } on FormatException catch (error) {
      throw PayloadFormatException('stored history is not JSON: ${error.message}');
    }
    if (decoded is! List<Object?>) {
      throw const PayloadFormatException('stored history is not a JSON array');
    }
    final readings = <_Reading>[];
    final points = <HistoryPoint>[];
    for (final stored in decoded) {
      final (reading, point) = _readingFromStored(stored);
      readings.add(reading);
      points.add(point);
    }
    // The collection-level refusal [load] makes, made here too: `reduce` is what
    // rejects a series that mixes currencies, and a file it would reject is a
    // file this method must not report as understood. Its result is discarded —
    // what is wanted is the throw.
    NetWorthHistory.reduce(points);
    return readings;
  }

  /// Reads back a stored reading, validating **everything [load] validates**.
  ///
  /// The two fields this class decides with are `published_at` and `seq`, and
  /// for a while those were the only ones checked here — `total` was left to
  /// [load], on the argument that validating it on the write path would let one
  /// bad reading block every future recording and so turn a display problem into
  /// a permanent one.
  ///
  /// **That trade was not real, and review reproduced the cost.** [load] refuses
  /// the *whole file* when any reading's total is malformed, so by the time this
  /// matters the curve already says "couldn't read the history" and keeps saying
  /// it: readings recorded after the damage are no more renderable than the
  /// damaged one. The laxness bought no display, and it spent the thing this
  /// store promises — [_merge] replaces a stored reading that shares the new
  /// one's UTC day and drops the oldest past [retainedDays], either of which
  /// destroys the bytes [record]'s "the failure travels up instead" exists to
  /// keep. `HomePage` records before it loads, so that happened *before* the
  /// owner was ever shown that something was wrong.
  ///
  /// So writability and readability are now one predicate rather than two that
  /// can disagree, and the way that is kept true is structural: the check is
  /// `HistoryPoint.fromJson` itself — the same parser, not a second
  /// reimplementation of it — so a field it learns to refuse is refused here on
  /// the same commit.
  (_Reading, HistoryPoint) _readingFromStored(Object? reading) {
    if (reading is! Map<String, Object?>) {
      throw const PayloadFormatException('stored reading is not a JSON object');
    }
    final seq = reading['seq'];
    if (seq is! String) {
      throw const PayloadFormatException('stored reading seq is not a string');
    }
    // Validates `published_at` and the total, through `DatedTotal.fromJson`.
    final point = HistoryPoint.fromJson(reading);
    return (
      (publishedAt: point.publishedAt, seq: seq, json: reading),
      point,
    );
  }

  /// Write through a temporary name, then rename over the file.
  ///
  /// A truncating write is the one moment the whole record is gone; a process
  /// death there would cost every reading, not the one being added. `rename` on
  /// the same filesystem is atomic, so a reader sees the old file or the new one
  /// — and `flush: true` is what makes that a real guarantee rather than a
  /// statement about a buffer the kernel had not written yet.
  Future<void> _write(File file, List<_Reading> readings) async {
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.writing');
    await temporary.writeAsString(
      jsonEncode(<Map<String, Object?>>[for (final reading in readings) reading.json]),
      flush: true,
    );
    await temporary.rename(file.path);
  }
}

/// One stored reading: the two fields the store decides with, and its bytes.
///
/// [json] is carried rather than rebuilt so that a reading written by an older
/// build survives a rewrite of the file unchanged.
typedef _Reading = ({DateTime publishedAt, String seq, Map<String, Object?> json});
