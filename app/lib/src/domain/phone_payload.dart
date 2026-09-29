import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'clock_continuity.dart';
import 'copy_freshness.dart';
import 'dated_total.dart';
import 'fetch_diagnostics.dart';
import 'instant.dart';
import 'payload_alert.dart';
import 'payload_format_exception.dart';
import 'publication_seq.dart';
import 'seq_baseline.dart';

/// The plaintext body of a task-19 publication, as far as this build reads it.
///
/// The field names are not invented here: they are the exact keys built by
/// `networth/publisher.py::_plaintext` on the host, which is the one contract
/// the daemon and the phone share (`AGENTS.md`). This build reads the header,
/// the total, the connection state and the open alert set. `accounts` and
/// `item_budget` are on the wire and are deliberately not parsed — the
/// per-account breakdown is the rest of task 22, and parsing a field this build
/// cannot render would be dead weight that looks like support for it.
///
/// `alerts` was in that list until the alert surface existed to render it; §11
/// makes the phone the **only** place an alert can ever be seen, so leaving the
/// field unparsed meant every alert the host raised was invisible to its one
/// reader.
@immutable
class PhonePayload {
  const PhonePayload({
    required this.schemaVersion,
    required this.pairingId,
    required this.seq,
    required this.publishedAt,
    required this.publishInterval,
    required this.grace,
    required this.total,
    required this.connectionState,
    required this.alerts,
  });

  /// The schema this build speaks.
  ///
  /// The contract is explicit that an older app must *refuse* a newer payload
  /// rather than misread it, so this is compared, not logged.
  static const String supportedSchemaVersion = '1';

  final String schemaVersion;
  final String pairingId;

  /// Monotonic publication sequence, kept as the string the wire carries.
  final String seq;

  final DateTime publishedAt;
  final Duration publishInterval;
  final Duration grace;
  final DatedTotal total;
  final ConnectionDisplayState connectionState;

  /// §11's open alert set, in the order the payload carried it.
  ///
  /// The whole open set travels on every publish — §11's alerts persist until
  /// resolved, so `bulletin()` sends all of them rather than a delta and a
  /// cached payload is a complete picture of what was true when it was
  /// published. Display order is decided by [summarizeAlerts], not here.
  final List<PayloadAlert> alerts;

  factory PhonePayload.fromJsonString(String source) {
    final Object? decoded;
    try {
      decoded = jsonDecode(source);
    } on FormatException catch (error) {
      throw PayloadFormatException('payload is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadFormatException('payload is not a JSON object');
    }
    return PhonePayload.fromJson(decoded);
  }

  factory PhonePayload.fromJson(Map<String, Object?> body) {
    final version = _string(body, 'schema_version');
    if (version != supportedSchemaVersion) {
      throw PayloadFormatException(
        'payload schema_version "$version" is not "$supportedSchemaVersion"',
      );
    }
    final total = body['total'];
    if (total is! Map<String, Object?>) {
      throw const PayloadFormatException('total is not a JSON object');
    }
    final connection = _string(body, 'connection_state');
    final ConnectionDisplayState connectionState;
    try {
      connectionState = ConnectionDisplayState.fromWire(connection);
    } on ArgumentError {
      throw PayloadFormatException('unknown connection_state "$connection"');
    }
    return PhonePayload(
      schemaVersion: version,
      pairingId: _string(body, 'pairing_id'),
      seq: _string(body, 'seq'),
      publishedAt: _timestamp(body, 'published_at'),
      publishInterval: Duration(seconds: _int(body, 'publish_interval_seconds')),
      grace: Duration(seconds: _int(body, 'grace_seconds')),
      total: DatedTotal.fromJson(total),
      connectionState: connectionState,
      alerts: _alerts(body),
    );
  }

  /// This copy's age dimension **with its reason**, evaluated against the
  /// device's clock.
  ///
  /// [continuity] has no default **on purpose**. A default would be a value
  /// this app fabricated rather than measured, and the only one that would not
  /// change behaviour is the one asserting the clock is fine — which is the
  /// exact claim the type exists to stop anybody making for free.
  ///
  /// **The two records arrive here, and they are required for the reason
  /// [continuity] is.** They used to be hardcoded to [DiagnosticsAbsent] and
  /// [BaselineAbsent], and the note that stood here defended it correctly: a
  /// build that performs no fetches *has* no fetch records, so absence was a
  /// measurement rather than a default, and it said this accessor is where the
  /// records arrive once task `22`'s transport lands. That is this change.
  ///
  /// What flips with it is who may claim absence. Absence is now one of three
  /// things the stores can report — absent, held, unreadable — so a caller that
  /// does not read them is not establishing the first, it is *asserting* it, and
  /// [DiagnosticsAbsent] is the reassuring value: it is §9.1's "never fetched",
  /// which drops the reason to [CannotCheck] and so can never say
  /// `HOST_NOT_PUBLISHING` about a host that has genuinely stopped publishing —
  /// the one fault §11 makes this screen the only possible reporter of. A
  /// default would hand that silence to every caller who forgot, which is the
  /// same bargain [continuity] refuses.
  ///
  /// Dart enforces this at compile time rather than at review time, which is
  /// the point: the callers up the cascade — [SnapshotView], then `HomePage`,
  /// then `main.dart` — do not build until each has said where its records come
  /// from.
  CopyState copyState(
    DateTime deviceNow, {
    required ClockContinuity continuity,
    required DiagnosticsState diagnostics,
    required BaselineState baseline,
  }) =>
      evaluateCopyState(
        publishedAt: publishedAt,
        publishInterval: publishInterval,
        grace: grace,
        copySeq: publicationSeq,
        deviceNow: deviceNow,
        continuity: continuity,
        diagnostics: diagnostics,
        baseline: baseline,
      );

  /// [seq] as the counter I6 compares, or `null` when this document spells it a
  /// way no host would have published.
  ///
  /// **Nullable rather than throwing, and that is the whole reason it is here
  /// rather than at the call site.** [seq] is *"kept as the string the wire
  /// carries"* and [fromJson] accepts any string for it, so a stored copy can
  /// carry one `PublicationSeq.parse` refuses. This is read while *rendering* a
  /// copy the phone is already holding; a throw there would take the owner's
  /// number off the screen over a field he is not being shown. `null` reaches
  /// `CopyNotConfirmed` instead — the records cannot confirm a copy whose own
  /// counter is unreadable — which is true and costs him nothing.
  ///
  /// Very nearly unreachable in practice: `PayloadEnvelope` fixes the header
  /// `seq` to a canonical positive decimal and requires the body to spell it
  /// identically, and the envelope is the only door into the held-copy store.
  PublicationSeq? get publicationSeq {
    try {
      return PublicationSeq.parse(seq);
    } on PayloadFormatException {
      return null;
    }
  }

  /// The dimension alone, for callers that do not need the reason.
  ///
  /// Delegates to [copyState] rather than to `evaluateCopyFreshness`, so the
  /// indicator this returns is by construction the one belonging to the reason
  /// the screen shows. Two independent evaluations of the same instant is how
  /// a row comes to warn about a state its own text denies.
  CopyFreshness copyFreshness(
    DateTime deviceNow, {
    required ClockContinuity continuity,
    required DiagnosticsState diagnostics,
    required BaselineState baseline,
  }) =>
      copyState(
        deviceNow,
        continuity: continuity,
        diagnostics: diagnostics,
        baseline: baseline,
      ).freshness;

  static String _string(Map<String, Object?> body, String field) {
    final value = body[field];
    if (value is! String) {
      throw PayloadFormatException('$field is not a string');
    }
    return value;
  }

  static int _int(Map<String, Object?> body, String field) {
    final value = body[field];
    if (value is! int) {
      throw PayloadFormatException('$field is not an integer');
    }
    return value;
  }

  static DateTime _timestamp(Map<String, Object?> body, String field) =>
      parseWireInstant(_string(body, field), field: field);

  /// **A missing `alerts` is a refusal, not an empty set.**
  ///
  /// The two failures are one careless decode apart and they mean opposite
  /// things: an empty array is the host saying "nothing is wrong", and a missing
  /// field is the app having no idea. `[]` is the overwhelmingly common case, so
  /// defaulting to it would be silently right almost always and catastrophically
  /// wrong in the case §11 exists for — the same argument `19`'s acceptance
  /// makes about `null` and `0` for the Item budget.
  static List<PayloadAlert> _alerts(Map<String, Object?> body) {
    final value = body['alerts'];
    if (value is! List) {
      throw const PayloadFormatException('alerts is not a JSON array');
    }
    final parsed = <PayloadAlert>[];
    for (var index = 0; index < value.length; index++) {
      final row = value[index];
      if (row is! Map<String, Object?>) {
        throw PayloadFormatException('alerts[$index] is not a JSON object');
      }
      parsed.add(PayloadAlert.fromJson(row, index: index));
    }
    return List.unmodifiable(parsed);
  }
}
