import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../domain/payload_format_exception.dart';
import '../domain/publication_seq.dart';

/// Why a fetch attempt failed, as a class rather than a message.
///
/// `DESIGN.md` §9.1 requires this and says why in a sentence worth keeping: a
/// dead host and a phone that has dropped off the tailnet both land in
/// `CANNOT_CHECK`, and with alerting reduced to the one in-app channel (§11)
/// those two *"sit at opposite ends of how much the owner should care"*.
///
/// Each value here is a different answer to *what should he do about it*, which
/// is the test for whether a class earns its place — a class that maps to the
/// same action as its neighbour is a message, not a class, and belongs in the
/// text beside it.
enum FetchFailureClass {
  /// No network at all. Ordinary and self-correcting; the owner does nothing.
  offline,

  /// The network is fine and the host did not answer. *"The shape a lost VPS
  /// takes on the only screen that can report it."*
  hostUnreachable,

  /// Reached it and it refused us. The pairing is what is wrong, so this is the
  /// one class whose fix is re-pairing.
  credentialRejected,

  /// Reached it, it answered, and the answer was unusable — TLS failure, a 5xx,
  /// a body that would not parse. Something is broken, but not simply gone.
  transportError,
}

/// The last fetch that actually returned a payload.
///
/// The instant and the `seq` are one value rather than two nullable fields
/// because §9.1's five facts contain a both-or-neither pair: `last_fetch_seq` is
/// *"the `seq` the last successful fetch returned"*, so it exists exactly when
/// that fetch does. Separate nullables would make "succeeded but returned no
/// `seq`" representable, and the predicate's third conjunct
/// (`last_fetch_seq == last_seq`) would then have to invent an answer for it.
@immutable
class FetchSuccess {
  const FetchSuccess({required this.at, required this.seq});

  /// By the device's own clock, which is the only clock an attempt has.
  final DateTime at;

  /// The `seq` that fetch returned — not necessarily the one the phone holds.
  /// They differ exactly when a fetch was refused, which is what makes the two
  /// separate facts in §9.1's list.
  final PublicationSeq seq;

  @override
  bool operator ==(Object other) =>
      other is FetchSuccess && other.at.isAtSameMomentAs(at) && other.seq == seq;

  @override
  int get hashCode => Object.hash(at.toUtc(), seq);
}

/// What this phone knows about its own fetching, under one pairing.
///
/// Four of §9.1's five facts live here; the fifth (`last_seq`, the `seq` the
/// phone actually *holds*) is I6's baseline and lives in [SeqBaselineStore].
///
/// **Scoped to a `pairing_id`, and that is not symmetry with the baseline —
/// the third conjunct forces it.** `HOST_NOT_PUBLISHING` requires
/// `last_fetch_seq == last_seq`, and `last_seq` is per-pairing by §9.3. A
/// `last_fetch_seq` left over from a previous pairing is a reading of a
/// different counter: the daemon allocates `max(seq) + 1` within its own
/// database, so two pairings' counters are unrelated numbers that are entirely
/// free to coincide. Comparing them would produce `HOST_NOT_PUBLISHING` —
/// *"reached the source; nothing has been published since"* — about a host that
/// is publishing perfectly well, which is the exact misattribution §9.1 says
/// this state exists to prevent. The other three facts point the same way: a
/// `credentialRejected` carried across a re-pairing would tell the owner to fix
/// the thing he just fixed.
@immutable
class FetchDiagnostics {
  /// The last attempt succeeded, so it *is* the last success. The two instants
  /// coincide by construction rather than by agreement between two arguments.
  FetchDiagnostics.succeeded({
    required this.pairingId,
    required DateTime at,
    required PublicationSeq seq,
  })  : lastAttemptAt = at,
        lastError = null,
        lastSuccess = FetchSuccess(at: at, seq: seq) {
    _checkPairing(pairingId);
  }

  /// The last attempt failed. [after] is the last success before it, if any —
  /// `null` on a phone that has attempted under this pairing and never
  /// succeeded, which is a different state from having never attempted at all.
  FetchDiagnostics.failed({
    required this.pairingId,
    required DateTime at,
    required FetchFailureClass error,
    FetchSuccess? after,
  })  : lastAttemptAt = at,
        lastError = error,
        lastSuccess = after {
    _checkPairing(pairingId);
    if (after != null && !after.at.isBefore(at)) {
      // **The one state §9.1's second conjunct cannot describe honestly.** It
      // reads `last_fetch_attempt_at == last_fetch_success_at` as "and nothing
      // has failed since", so a failure recorded at or before the instant of the
      // last success makes that conjunct true while an error is held — the
      // phone would announce `HOST_NOT_PUBLISHING` on the strength of a fetch it
      // knows failed. Refusing is the conservative direction: the record keeps
      // its previous contents and the predicate falls to `CANNOT_CHECK`, which
      // blames nobody. Unreachable from the transport, where attempts are
      // sequential and a round trip cannot complete inside one microsecond, so
      // this guards the bytes on disk and a future caller, not today's.
      throw PayloadFormatException(
        'a failed attempt at $at cannot be recorded at or before the last '
        'success at ${after.at}',
      );
    }
  }

  /// The pairing every fact below was observed under.
  final String pairingId;

  /// `last_fetch_attempt_at` — when this phone last tried, success or not.
  final DateTime lastAttemptAt;

  /// `last_fetch_error` — the class of the failure, `null` **iff** the last
  /// attempt succeeded. Not a history: it explains the phone's *current*
  /// inability, and a success ends that.
  final FetchFailureClass? lastError;

  /// `last_fetch_success_at` + `last_fetch_seq`, or `null` if no fetch under
  /// this pairing has ever returned a payload.
  final FetchSuccess? lastSuccess;

  static void _checkPairing(String pairingId) {
    if (pairingId.isEmpty) {
      throw const PayloadFormatException('fetch diagnostics have no pairing_id');
    }
  }

  @override
  bool operator ==(Object other) =>
      other is FetchDiagnostics &&
      other.pairingId == pairingId &&
      other.lastAttemptAt.isAtSameMomentAs(lastAttemptAt) &&
      other.lastError == lastError &&
      other.lastSuccess == lastSuccess;

  @override
  int get hashCode => Object.hash(pairingId, lastAttemptAt.toUtc(), lastError, lastSuccess);
}

/// What the store found for a pairing — **three** outcomes, not two.
///
/// The reason is not the one [BaselineState] has, and the difference is worth
/// stating so this type is not read as inheriting a security argument it does
/// not have. Damaging *this* file cannot bypass a check: without the four facts
/// no conjunct of `HOST_NOT_PUBLISHING` can be established, so the predicate
/// falls to `CANNOT_CHECK` and the phone accuses nobody.
///
/// It exists for the other half of the same requirement — **a missing fact must
/// never be readable as a satisfied one.** §9.1 ends `CANNOT_CHECK` with an
/// error class, and *"never fetched"* is one of them; telling the owner that on
/// a phone that has fetched for a month and lost its notes is a claim with
/// nothing behind it. A nullable record invites exactly that, because the
/// predicate's conjuncts are comparisons and `null == null` is `true`: a damaged
/// file and a fresh install would both offer an absent `last_fetch_seq` that
/// compares equal to an absent `last_seq`, and the conjunct a caller expected to
/// fail would hold. A `sealed` type makes the caller write the branch instead.
sealed class DiagnosticsState {
  const DiagnosticsState();
}

/// No fetch has ever been attempted under this pairing. §9.1's *"never
/// fetched"*, and deliberately **not** a [FetchFailureClass] value: the record's
/// own existence carries that fact, and a second spelling of it could disagree
/// with the first.
final class DiagnosticsAbsent extends DiagnosticsState {
  const DiagnosticsAbsent();
}

/// Diagnostics for this pairing, read back intact.
final class DiagnosticsHeld extends DiagnosticsState {
  const DiagnosticsHeld(this.diagnostics);

  final FetchDiagnostics diagnostics;
}

/// A record exists and could not be read. Never *"never fetched"*.
final class DiagnosticsUnreadable extends DiagnosticsState {
  const DiagnosticsUnreadable(this.reason);

  /// What was wrong, for the screen. Carries no stored bytes.
  final String reason;
}

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
    final String bytes;
    try {
      final file = await open();
      if (!await file.exists()) {
        return const DiagnosticsAbsent();
      }
      bytes = await file.readAsString();
    } on Object catch (error) {
      // `exists()` answering false is the only thing that may mean absent —
      // everything else between "where is the file" and "here are its bytes" is
      // a record this phone has and cannot read. Caught as `Object` because the
      // types this path can raise are the union of `dart:io`'s and whatever
      // `open` reaches for: `path_provider` raises
      // `MissingPlatformDirectoryException`, which is not a
      // `FileSystemException`, and a type this catch had not heard of would
      // surface to a caller that is holding a [DiagnosticsState] and reasonably
      // assuming its three cases are all of them.
      return DiagnosticsUnreadable('fetch diagnostics could not be read: $error');
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
