import 'dart:convert';
import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../domain/payload_format_exception.dart';
import '../domain/publication_seq.dart';

/// Why I6 refused a payload. The two refusals in `DESIGN.md` §9.3's table share
/// a warning but not a cause, and the owner's next move differs.
enum DowngradeCause {
  /// `seq < last_seq` — something served an envelope older than the one held.
  /// §9.3's realistic triggers are all operator error: a database restored onto
  /// a running daemon, a rollback to an older build, two daemons answering to
  /// the same name.
  olderPublication,

  /// The payload named a `pairing_id` that is not the paired one — "the phone
  /// is talking to something that is not its daemon".
  foreignPairing,
}

/// The persistent warning §9.3 raises, and why it is on disk.
///
/// *"It survives restarts and clears only when a `seq` greater than `last_seq`
/// actually arrives — not on the next successful fetch, which would let a single
/// good response paper over an unexplained downgrade."* A warning held in memory
/// would be cleared by the one event that proves nothing: the app being reopened.
@immutable
class DowngradeWarning {
  const DowngradeWarning({required this.cause, required this.at, this.refusedSeq});

  final DowngradeCause cause;

  /// When the refusal happened, by the device's own clock.
  ///
  /// Device time, and deliberately: this is a phone-local event with no host
  /// timestamp to borrow. §9.1's clock-skew rules are about deciding a copy's
  /// *age* against the host's clock; nothing here is compared with the host, so
  /// a skewed device makes this display wrong rather than the warning wrong.
  final DateTime at;

  /// The `seq` that was refused — null for [DowngradeCause.foreignPairing].
  ///
  /// A foreign pairing's payload does carry a `seq`, and it is deliberately not
  /// kept: it belongs to a counter this phone has no baseline for, so storing it
  /// beside [SeqBaseline.lastSeq] would put two unrelated numbers in comparable
  /// positions. There is nothing true to say about their order.
  final PublicationSeq? refusedSeq;

  @override
  bool operator ==(Object other) =>
      other is DowngradeWarning &&
      other.cause == cause &&
      other.at.isAtSameMomentAs(at) &&
      other.refusedSeq == refusedSeq;

  @override
  int get hashCode => Object.hash(cause, at.toUtc(), refusedSeq);
}

/// The baseline I6 compares against: one pairing, the `seq` held under it, and
/// any unexplained downgrade seen under it.
///
/// **The `pairing_id` is part of the record rather than a key into a table of
/// them, and that is the whole scoping rule.** §9.3 point 3 stores `last_seq`
/// per `pairing_id` so that a phone with no baseline for its current pairing
/// accepts its first payload on trust — which is what makes §9.3a's restore
/// recoverable by re-pairing instead of by teaching the owner to dismiss an
/// integrity alarm. Only ever one pairing is current ([PairingVault] holds it),
/// and a `pairing_id` is minted fresh by the daemon at pairing time, so a record
/// for any *other* pairing could never be consulted again. Keeping a map of them
/// would be unbounded growth with an eviction policy to get wrong, in exchange
/// for answering a question nobody asks.
@immutable
class SeqBaseline {
  const SeqBaseline({required this.pairingId, required this.lastSeq, this.warning});

  final String pairingId;

  /// The `seq` of the payload the phone actually holds under [pairingId].
  final PublicationSeq lastSeq;

  /// The unexplained downgrade seen under [pairingId], if one has been.
  ///
  /// There is deliberately no `heldSeq` beside it. At the moment of a refusal it
  /// would equal [lastSeq], and the only event that advances [lastSeq] is the
  /// arrival of a greater `seq` — which clears the warning. The two can never
  /// legitimately differ, so a second copy of the number would be state that can
  /// only ever be made *in*consistent.
  final DowngradeWarning? warning;

  @override
  bool operator ==(Object other) =>
      other is SeqBaseline &&
      other.pairingId == pairingId &&
      other.lastSeq == lastSeq &&
      other.warning == warning;

  @override
  int get hashCode => Object.hash(pairingId, lastSeq, warning);
}

/// What the store found for a pairing — **three** outcomes, not two.
///
/// This type exists because of the one way I6 can be disabled without anybody
/// noticing. §9.3 point 3 says a phone with no `last_seq` for its current
/// pairing accepts its first payload on trust; a baseline file that *exists and
/// does not parse* is not that phone, but it looks exactly like it to any reader
/// that recovers from a damaged file by treating it as empty. Corruption would
/// then be the bypass: damage the file, and the next payload served is accepted
/// and becomes the new baseline.
///
/// So "nothing is stored" and "something is stored and cannot be read" are
/// different values of a `sealed` type rather than the same `null`, and Dart's
/// exhaustive `switch` makes the caller write the branch. A thrown exception
/// would also be unmissable, right up until a caller caught it and defaulted —
/// which is the shape of the mistake, not a different mistake.
sealed class BaselineState {
  const BaselineState();
}

/// Nothing is stored for this pairing: a new install, or a pairing this phone
/// has not yet accepted a payload under. §9.3a's accept-on-trust case.
final class BaselineAbsent extends BaselineState {
  const BaselineAbsent();
}

/// A baseline for this pairing, read back intact.
final class BaselineHeld extends BaselineState {
  const BaselineHeld(this.baseline);

  final SeqBaseline baseline;
}

/// A baseline exists and could not be read. **Never** accept-on-trust.
final class BaselineUnreadable extends BaselineState {
  const BaselineUnreadable(this.reason);

  /// What was wrong, for the screen. Carries no stored bytes.
  final String reason;
}

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
