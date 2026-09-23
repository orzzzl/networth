import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../domain/payload_format_exception.dart';
import '../domain/publication_seq.dart';
import '../domain/seq_baseline.dart';

/// Where I6's baseline lives.
abstract interface class SeqBaselineStore {
  /// The baseline for [pairingId], or why there is none.
  Future<BaselineState> read(String pairingId);

  /// Replace the stored record. There is only ever one.
  Future<void> write(SeqBaseline baseline);
}

/// A small JSON object in the app's own directory.
///
/// **Outside the key vault, and a separate file from the fetch diagnostics**,
/// both deliberately. `flutter_secure_storage` holds the pairing bundle because
/// that key is the revocable thing; this baseline is not a capability, and §9.3a
/// requires it to survive exactly as long as its pairing does. Splitting it from
/// `last_fetch_*` is a cost-of-corruption decision rather than a tidiness one:
/// losing the diagnostics degrades a *reason* — the predicate can no longer
/// establish `HOST_NOT_PUBLISHING` and falls to `CANNOT_CHECK`, which blames
/// nobody and repopulates on the next successful fetch — while losing this file
/// would, without [BaselineUnreadable], re-baseline the one check that survives
/// a valid ciphertext. The write frequencies point the same way: an attempt
/// timestamp is written on every fetch including failures, and this record only
/// on an accepted advance or a refusal. Putting the rarely-written security
/// baseline inside the most-rewritten file is backwards.
class FileSeqBaselineStore implements SeqBaselineStore {
  FileSeqBaselineStore({required this.open});

  /// The production wiring: `<app documents>/seq_baseline.json`, the same
  /// app-private directory and the same reasoning as [FileHistoryStore].
  factory FileSeqBaselineStore.appPrivate() => FileSeqBaselineStore(
        open: () async => File(
          '${(await getApplicationDocumentsDirectory()).path}/$fileName',
        ),
      );

  static const String fileName = 'seq_baseline.json';

  /// Where the file is. Injected so a test uses a temporary directory and so the
  /// production path is named in exactly one place.
  final Future<File> Function() open;

  @override
  Future<BaselineState> read(String pairingId) async {
    final String bytes;
    try {
      final file = await open();
      if (!await file.exists()) {
        return const BaselineAbsent();
      }
      bytes = await file.readAsString();
    } on Object catch (error) {
      // **`exists()` answering false is the only thing that may mean absent.**
      // Everything else that can go wrong between "where is the file" and "here
      // are its bytes" is a baseline this phone has and cannot read, which is
      // the [BaselineUnreadable] case — a permission the app lost, a directory
      // the platform could not resolve, a read that failed mid-file. Caught as
      // `Object` rather than as `FileSystemException` deliberately: the set of
      // exception types this path can raise is the union of `dart:io`'s and
      // whatever `open` reaches for (`path_provider` raises its own
      // `MissingPlatformDirectoryException`, which is not a `FileSystemException`),
      // and a type this catch has not heard of would otherwise propagate to a
      // caller that is looking at a `BaselineState` and reasonably assuming the
      // three cases are all of them.
      return BaselineUnreadable('baseline could not be read: $error');
    }
    try {
      return BaselineHeld(_parse(bytes, pairingId: pairingId));
    } on _ForeignPairing {
      // Stored, but for a pairing that is not this one — which §9.3 scopes away
      // deliberately, so this is the accept-on-trust case and not damage.
      return const BaselineAbsent();
    } on PayloadFormatException catch (error) {
      return BaselineUnreadable(error.message);
    }
  }

  @override
  Future<void> write(SeqBaseline baseline) async {
    final file = await open();
    final bytes = jsonEncode(_json(baseline));
    // Writability and readability are one predicate, not two that can drift —
    // `23a`'s precedent. The bytes about to be committed go through the same
    // parser [read] uses, so a record this store writes and a record it could
    // not read back cannot be different things. The result is discarded; what
    // is wanted is the throw, and it throws before anything on disk is touched.
    _parse(bytes, pairingId: baseline.pairingId);
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.writing');
    await temporary.writeAsString(bytes, flush: true);
    await temporary.rename(file.path);
  }

  Map<String, Object?> _json(SeqBaseline baseline) => <String, Object?>{
        'pairing_id': baseline.pairingId,
        // The host's own bytes, not this app's rendering of a parsed number.
        'last_seq': baseline.lastSeq.wire,
        if (baseline.warning case final DowngradeWarning warning)
          'warning': <String, Object?>{
            'cause': _causeWire[warning.cause],
            'at': warning.at.toUtc().toIso8601String(),
            if (warning.refusedSeq case final PublicationSeq refused) 'refused_seq': refused.wire,
          },
      };

  SeqBaseline _parse(String bytes, {required String pairingId}) {
    final Object? decoded;
    try {
      decoded = jsonDecode(bytes);
    } on FormatException catch (error) {
      throw PayloadFormatException('stored baseline is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadFormatException('stored baseline is not a JSON object');
    }
    final storedPairing = decoded['pairing_id'];
    if (storedPairing is! String || storedPairing.isEmpty) {
      throw const PayloadFormatException('stored baseline has no pairing_id');
    }
    // Checked before anything else is read, so a record belonging to a previous
    // pairing is scoped away rather than refused for a field this pairing would
    // never have consulted.
    if (storedPairing != pairingId) {
      throw const _ForeignPairing();
    }
    final storedSeq = decoded['last_seq'];
    if (storedSeq is! String) {
      throw const PayloadFormatException('stored baseline last_seq is not a string');
    }
    return SeqBaseline(
      pairingId: storedPairing,
      // The same parser the wire goes through, so a `seq` shape the payload
      // layer refuses is refused here on the same commit.
      lastSeq: PublicationSeq.parse(storedSeq, field: 'stored baseline last_seq'),
      warning: _warning(decoded['warning']),
    );
  }

  DowngradeWarning? _warning(Object? stored) {
    if (stored == null) {
      return null;
    }
    if (stored is! Map<String, Object?>) {
      throw const PayloadFormatException('stored baseline warning is not a JSON object');
    }
    final cause = _causeOf(stored['cause']);
    final at = stored['at'];
    if (at is! String) {
      throw const PayloadFormatException('stored baseline warning has no timestamp');
    }
    final parsedAt = DateTime.tryParse(at);
    if (parsedAt == null) {
      throw PayloadFormatException('stored baseline warning timestamp is not ISO-8601: $at');
    }
    final refused = stored['refused_seq'];
    if (refused != null && refused is! String) {
      throw const PayloadFormatException('stored baseline warning refused_seq is not a string');
    }
    if (cause == DowngradeCause.foreignPairing && refused != null) {
      // The encoder never writes one; a file that has one is not a file this
      // store wrote, and the pair would put two unrelated counters side by side.
      throw const PayloadFormatException(
        'stored baseline warning names a refused seq for a foreign pairing',
      );
    }
    if (cause == DowngradeCause.olderPublication && refused == null) {
      throw const PayloadFormatException(
        'stored baseline warning names no refused seq for a downgrade',
      );
    }
    return DowngradeWarning(
      cause: cause,
      at: parsedAt,
      refusedSeq: refused == null
          ? null
          : PublicationSeq.parse(refused as String, field: 'stored baseline refused_seq'),
    );
  }

  DowngradeCause _causeOf(Object? stored) {
    for (final entry in _causeWire.entries) {
      if (entry.value == stored) {
        return entry.key;
      }
    }
    throw PayloadFormatException('stored baseline warning cause is unknown: $stored');
  }

  /// The stored spelling of each cause, kept apart from the enum's Dart name so
  /// renaming the identifier cannot silently change the file format.
  static const Map<DowngradeCause, String> _causeWire = <DowngradeCause, String>{
    DowngradeCause.olderPublication: 'OLDER_PUBLICATION',
    DowngradeCause.foreignPairing: 'FOREIGN_PAIRING',
  };
}

/// A stored record for a different pairing. Internal: it is a scoping outcome,
/// not a format error, and [BaselineState] is how that reaches a caller.
class _ForeignPairing implements Exception {
  const _ForeignPairing();
}
