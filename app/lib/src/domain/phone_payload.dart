import 'dart:convert';

import 'package:flutter/foundation.dart';

import 'copy_freshness.dart';
import 'dated_total.dart';
import 'payload_format_exception.dart';

/// The plaintext body of a task-19 publication, as far as this build reads it.
///
/// The field names are not invented here: they are the exact keys built by
/// `networth/publisher.py::_plaintext` on the host, which is the one contract
/// the daemon and the phone share (`AGENTS.md`). This build reads the header,
/// the total and the connection state. `accounts`, `item_budget` and `alerts`
/// are on the wire and are deliberately not parsed — the per-account breakdown
/// and the alert surface are task 22, and parsing a field this build cannot
/// render would be dead weight that looks like support for it.
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
    );
  }

  /// This copy's age dimension, evaluated against the device's clock.
  CopyFreshness copyFreshness(DateTime deviceNow) => evaluateCopyFreshness(
        publishedAt: publishedAt,
        publishInterval: publishInterval,
        grace: grace,
        deviceNow: deviceNow,
      );

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

  static DateTime _timestamp(Map<String, Object?> body, String field) {
    final parsed = DateTime.tryParse(_string(body, field));
    if (parsed == null) {
      throw PayloadFormatException('$field is not a timestamp');
    }
    return parsed.toUtc();
  }
}
