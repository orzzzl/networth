import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../domain/fetch_diagnostics.dart';
import '../domain/payload_format_exception.dart';
import '../domain/publication_seq.dart';
import 'stored_file.dart';

/// Where the phone's own fetch history lives.
abstract interface class FetchDiagnosticsStore {
  /// The diagnostics for [pairingId], or why there are none.
  Future<DiagnosticsState> read(String pairingId);

  /// Replace the stored record. There is only ever one.
  Future<void> write(FetchDiagnostics diagnostics);
}

/// A small JSON object in the app's own directory, beside the baseline and not
/// inside it.
///
/// The two files are split on cost-of-corruption rather than on tidiness, and
/// the costs are not symmetric. Losing *these* four facts degrades a **reason**:
/// the predicate can no longer establish `HOST_NOT_PUBLISHING` and falls to
/// `CANNOT_CHECK`, which repopulates on the next successful fetch. Losing the
/// baseline would, without [BaselineUnreadable], re-baseline the one check that
/// survives a valid ciphertext. The write frequencies point the same way:
/// [lastAttemptAt] is rewritten on **every** attempt including failures, while a
/// baseline changes only on an accepted advance or a refusal — so sharing a file
/// would put the rarely-written security record in the most-rewritten one.
class FileFetchDiagnosticsStore implements FetchDiagnosticsStore {
  FileFetchDiagnosticsStore({required this.open});

  /// The production wiring: `<app documents>/fetch_diagnostics.json`, the same
  /// app-private directory and the same reasoning as [FileHistoryStore].
  factory FileFetchDiagnosticsStore.appPrivate() => FileFetchDiagnosticsStore(
        open: () async => File(
          '${(await getApplicationDocumentsDirectory()).path}/$fileName',
        ),
      );

  static const String fileName = 'fetch_diagnostics.json';

  /// Where the file is. Injected so a test uses a temporary directory and so the
  /// production path is named in exactly one place.
  final Future<File> Function() open;

  @override
  Future<DiagnosticsState> read(String pairingId) async {
    // Same proof as the baseline store, for a weaker reason that is still worth
    // having: nothing here can be bypassed by damage, but "never fetched" and
    // "cannot read what I recorded" are different things to tell the owner, and
    // an unreadable record must not be able to spell itself as the former.
    final String bytes;
    switch (await readStoredFile(open)) {
      case StoredBytes(bytes: final read):
        bytes = read;
      case StoredAbsent():
        return const DiagnosticsAbsent();
      case StoredUnreadable(reason: final reason):
        return DiagnosticsUnreadable('fetch diagnostics could not be read: $reason');
    }
    try {
      return DiagnosticsHeld(_parse(bytes, pairingId: pairingId));
    } on _ForeignPairing {
      return const DiagnosticsAbsent();
    } on PayloadFormatException catch (error) {
      return DiagnosticsUnreadable(error.message);
    }
  }

  @override
  Future<void> write(FetchDiagnostics diagnostics) async {
    final file = await open();
    final bytes = jsonEncode(_json(diagnostics));
    // Writability and readability are one predicate, not two that can drift —
    // `23a`'s precedent. **It cannot throw today**, and that is worth saying
    // rather than leaving as an implied guarantee: every invariant [_parse]
    // checks is already enforced by the two constructors, so no
    // [FetchDiagnostics] exists whose encoding it would refuse. What it defends
    // against is drift — a field added to [_json] and not to [_parse], or a
    // rule added to [_parse] that the constructors do not hold — which is
    // exactly the change that would otherwise be discovered by a phone that
    // wrote a record and could not read it back.

    _parse(bytes, pairingId: diagnostics.pairingId);
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.writing');
    await temporary.writeAsString(bytes, flush: true);
    await temporary.rename(file.path);
  }

  Map<String, Object?> _json(FetchDiagnostics diagnostics) => <String, Object?>{
        'pairing_id': diagnostics.pairingId,
        // §9.1's own field names, so the file can be read against the spec.
        'last_fetch_attempt_at': diagnostics.lastAttemptAt.toUtc().toIso8601String(),
        if (diagnostics.lastError case final FetchFailureClass error)
          'last_fetch_error': _errorWire[error],
        // Written only when true, so a record from before this key existed
        // reads back as what it was: an attempt that either succeeded or
        // failed. Absent and `false` mean the same thing and one of them is
        // what every already-stored file says.
        if (diagnostics.foundNoPublication) noPublicationKey: true,
        if (diagnostics.lastSuccess case final FetchSuccess success) ...<String, Object?>{
          'last_fetch_success_at': success.at.toUtc().toIso8601String(),
          // The host's own bytes, not this app's rendering of a parsed number.
          'last_fetch_seq': success.seq.wire,
        },
      };

  FetchDiagnostics _parse(String bytes, {required String pairingId}) {
    final Object? decoded;
    try {
      decoded = jsonDecode(bytes);
    } on FormatException catch (error) {
      throw PayloadFormatException('stored fetch diagnostics are not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadFormatException('stored fetch diagnostics are not a JSON object');
    }
    final storedPairing = decoded['pairing_id'];
    if (storedPairing is! String || storedPairing.isEmpty) {
      throw const PayloadFormatException('stored fetch diagnostics have no pairing_id');
    }
    // Checked before anything else, so a record belonging to a previous pairing
    // is scoped away rather than refused for a field this pairing would never
    // have consulted.
    if (storedPairing != pairingId) {
      throw const _ForeignPairing();
    }
    final attemptAt = _instant(decoded['last_fetch_attempt_at'], 'last_fetch_attempt_at');
    if (attemptAt == null) {
      throw const PayloadFormatException(
        'stored fetch diagnostics have no last_fetch_attempt_at',
      );
    }
    final successAt = _instant(decoded['last_fetch_success_at'], 'last_fetch_success_at');
    final storedSeq = decoded['last_fetch_seq'];
    if (storedSeq != null && storedSeq is! String) {
      throw const PayloadFormatException(
        'stored fetch diagnostics last_fetch_seq is not a string',
      );
    }
    if ((successAt == null) != (storedSeq == null)) {
      // The pair is both-or-neither in [FetchSuccess]; on disk it has to be
      // checked, and half of it is not a success this store wrote.
      throw const PayloadFormatException(
        'stored fetch diagnostics have only half of the last successful fetch',
      );
    }
    final success = successAt == null
        ? null
        : FetchSuccess(
            at: successAt,
            // The same parser the wire goes through, so a `seq` shape the
            // payload layer refuses is refused here on the same commit.
            seq: PublicationSeq.parse(storedSeq! as String, field: 'stored last_fetch_seq'),
          );
    final storedError = decoded['last_fetch_error'];
    final noPublication = decoded[noPublicationKey];
    if (noPublication != null && noPublication != true) {
      // `false` is refused as well as a non-boolean. The key is written only
      // when true, so a `false` on disk was not written by this store, and a
      // record whose shape nothing here produced is one whose other fields
      // there is no reason to trust either.
      throw PayloadFormatException(
        'stored fetch diagnostics $noPublicationKey is not true',
      );
    }
    if (storedError != null && noPublication == true) {
      // Contradictory: the attempt cannot both have failed to reach the host
      // and have reached one that had nothing. Guessing which half to believe
      // picks between "your network is down" and "your server has nothing" —
      // opposite ends of how much the owner should care, which is the very
      // distinction §9.1 makes this record carry.
      throw const PayloadFormatException(
        'stored fetch diagnostics record an attempt that both failed and reached a host',
      );
    }
    if (noPublication == true) {
      return FetchDiagnostics.foundNoPublication(
        pairingId: storedPairing,
        at: attemptAt,
        after: success,
      );
    }
    if (storedError == null) {
      if (success == null || !success.at.isAtSameMomentAs(attemptAt)) {
        // No error and no success at the attempt's instant: the record says the
        // last attempt neither succeeded nor failed. Nothing true can be read
        // out of it, and guessing either way feeds §9.1's predicate a fact it
        // did not observe.
        throw const PayloadFormatException(
          'stored fetch diagnostics record an attempt that neither succeeded nor failed',
        );
      }
      return FetchDiagnostics.succeeded(
        pairingId: storedPairing,
        at: attemptAt,
        seq: success.seq,
      );
    }
    return FetchDiagnostics.failed(
      pairingId: storedPairing,
      at: attemptAt,
      error: _errorOf(storedError),
      after: success,
    );
  }

  /// A stored instant, or `null` if the key is absent.
  ///
  /// **An instant with no timezone designator is refused rather than read as
  /// local time**, which is what `DateTime.parse` would do with it. `AGENTS.md`
  /// stores every timestamp in UTC with an explicit zone and does staleness math
  /// in UTC; a bare `2026-09-22T10:00:00` read on this owner's phone would land
  /// seven or eight hours off, and conjunct 1 of `HOST_NOT_PUBLISHING`
  /// (`last_fetch_success_at >= stale_after`) is a comparison that flips inside
  /// that margin. `isUtc` is the discriminator and not a guess: Dart returns a
  /// UTC `DateTime` for any input carrying `Z` or an offset, and a local one for
  /// an input carrying neither.
  DateTime? _instant(Object? stored, String field) {
    if (stored == null) {
      return null;
    }
    if (stored is! String) {
      throw PayloadFormatException('stored fetch diagnostics $field is not a string');
    }
    final parsed = DateTime.tryParse(stored);
    if (parsed == null) {
      throw PayloadFormatException(
        'stored fetch diagnostics $field is not ISO-8601: $stored',
      );
    }
    if (!parsed.isUtc) {
      throw PayloadFormatException(
        'stored fetch diagnostics $field carries no timezone: $stored',
      );
    }
    return parsed;
  }

  FetchFailureClass _errorOf(Object? stored) {
    for (final entry in _errorWire.entries) {
      if (entry.value == stored) {
        return entry.key;
      }
    }
    throw PayloadFormatException('stored fetch diagnostics error class is unknown: $stored');
  }

  /// The one key that is not one of §9.1's five facts.
  ///
  /// Named here rather than spelled at its three use sites for the reason the
  /// error table below is: the file format must not be changeable by an
  /// editor's rename, and a key written in one place and read in another is a
  /// format two literals wide.
  static const String noPublicationKey = 'last_fetch_found_no_publication';

  /// The stored spelling of each class, kept apart from the enum's Dart name so
  /// renaming the identifier cannot silently change the file format.
  static const Map<FetchFailureClass, String> _errorWire = <FetchFailureClass, String>{
    FetchFailureClass.offline: 'OFFLINE',
    FetchFailureClass.hostUnreachable: 'HOST_UNREACHABLE',
    FetchFailureClass.credentialRejected: 'CREDENTIAL_REJECTED',
    FetchFailureClass.transportError: 'TRANSPORT_ERROR',
  };
}

/// A stored record for a different pairing. Internal: it is a scoping outcome,
/// not a format error, and [DiagnosticsState] is how that reaches a caller.
class _ForeignPairing implements Exception {
  const _ForeignPairing();
}
