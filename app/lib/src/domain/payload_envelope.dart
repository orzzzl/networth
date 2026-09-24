import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/foundation.dart';

import 'aes_gcm.dart';
import 'payload_format_exception.dart';
import 'phone_payload.dart';

/// The envelope is not the wire shape section 6.1 fixes.
///
/// Separate from [PayloadFormatException], which is about the *body* — by the
/// time that one can be raised the bytes have already been authenticated, so
/// the two describe faults on opposite sides of the tag check and a caller
/// records them as different things.
class PayloadEnvelopeException implements Exception {
  const PayloadEnvelopeException(this.message);

  final String message;

  @override
  String toString() => 'PayloadEnvelopeException: $message';
}

/// The clear header and the decrypted body disagree about which publication
/// this is.
///
/// **This cannot be caused by an attacker, and it is still refused.** Both
/// copies are authenticated under the same tag — the header through the AAD,
/// the body through the ciphertext — so anyone who could make them differ could
/// already forge the whole envelope. What it means in practice is a defect in
/// the publisher.
///
/// Refused anyway, because the alternative is that the app holds two answers to
/// "which publication is this" and different parts of it pick different ones.
/// That is not hypothetical: I6 (section 9.3) compares `seq` against the stored
/// baseline, and task `23a`'s record identifies a point by `published_at`/`seq`.
/// Advancing the downgrade baseline on one number while recording history under
/// another would leave the defence pinned to a publication the curve has no
/// point for — and it would do it silently, since each half is individually
/// authentic.
class PayloadHeaderMismatchException implements Exception {
  const PayloadHeaderMismatchException(this.field);

  /// The first of the four authenticated header fields that disagreed.
  final String field;

  @override
  String toString() =>
      'PayloadHeaderMismatchException: envelope and body disagree on "$field"';
}

/// One verified open: the exact authenticated bytes, and what they parsed to.
///
/// **Two representations of one publication, and the point is that nothing can
/// hold one without the other.** The held copy (section 9.2) is stored verbatim
/// — `held_copy_store.dart` argues at length why the file *is* the payload
/// rather than a record containing one — while I6, the history record and every
/// display read the parsed [payload]. A caller handed the text alone could
/// store bytes it never parsed; a caller handed the payload alone would have to
/// re-serialise it to store anything, which is the second derivation that store
/// exists to refuse.
///
/// So this is constructed in exactly one place, [PayloadEnvelope.open], after
/// the tag has verified and the header/body agreement has been established. The
/// constructor is private to this library for that reason: there is no way to
/// assemble a text and a payload that did not come out of the same open.
///
/// **[payload] is authoritative.** Where the two could be read as disagreeing —
/// they cannot, having been produced together — the parsed value decides, and
/// [text] is only ever handed to storage.
@immutable
final class OpenedPayload {
  const OpenedPayload._({required this.text, required this.payload});

  /// The decrypted body, byte for byte as it came out of the envelope.
  ///
  /// Never logged and never interpolated into a message: it is the owner's
  /// balances in clear text.
  final String text;

  /// [text], parsed.
  final PhonePayload payload;
}

/// The host-to-phone envelope from DESIGN section 6.1, as the phone reads it.
///
/// Four clear-text header fields, authenticated through one canonical
/// length-delimited byte string, plus an opaque nonce and sealed payload. The
/// counterpart is `networth/payload.py::PayloadEnvelope`; this type deliberately
/// mirrors its validation so a document one end considers well-formed is not
/// quietly accepted by the other.
///
/// **The phone parses and opens; it never builds one.** There is no `toJson`
/// here for the same reason `aes_gcm.dart` has no `seal`.
@immutable
class PayloadEnvelope {
  const PayloadEnvelope._({
    required this.schemaVersion,
    required this.pairingId,
    required this.seq,
    required this.publishedAt,
    required this.nonce,
    required this.payload,
  });

  /// Kept as the exact strings the wire carries, because that is what the AAD
  /// is computed over. Re-rendering any of them — parsing `publishedAt` to a
  /// `DateTime` and printing it back, say — would compute a different AAD and
  /// fail authentication on a perfectly good envelope.
  final String schemaVersion;
  final String pairingId;
  final String seq;
  final String publishedAt;

  final Uint8List nonce;
  final Uint8List payload;

  static const Set<String> _fields = {
    'schema_version',
    'pairing_id',
    'seq',
    'published_at',
    'nonce',
    'payload',
  };

  factory PayloadEnvelope.fromJsonString(String source) {
    final Object? decoded;
    try {
      decoded = jsonDecode(source);
    } on FormatException catch (error) {
      throw PayloadEnvelopeException('envelope is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadEnvelopeException('envelope is not a JSON object');
    }
    return PayloadEnvelope.fromJson(decoded);
  }

  factory PayloadEnvelope.fromJson(Map<String, Object?> body) {
    // An unexpected key is refused rather than ignored. A field the phone
    // silently drops is a field the host believes it delivered, and section 6.1
    // is the one contract the two share; a future version that adds one has to
    // say so through `schema_version`.
    for (final key in body.keys) {
      if (!_fields.contains(key)) {
        throw PayloadEnvelopeException('envelope has unexpected field "$key"');
      }
    }
    for (final key in _fields) {
      if (!body.containsKey(key)) {
        throw PayloadEnvelopeException('envelope is missing "$key"');
      }
    }
    final nonce = _base64url(body['nonce'], field: 'nonce');
    if (nonce.length != aesGcmNonceBytes) {
      throw const PayloadEnvelopeException('nonce must contain exactly 12 bytes');
    }
    final payload = _base64url(body['payload'], field: 'payload');
    if (payload.length < aesGcmTagBytes) {
      throw const PayloadEnvelopeException(
        'payload must contain ciphertext and a 16-byte tag',
      );
    }
    return PayloadEnvelope._(
      schemaVersion: _headerText(body['schema_version'], field: 'schema_version', decimal: true),
      pairingId: _headerText(body['pairing_id'], field: 'pairing_id'),
      seq: _headerText(body['seq'], field: 'seq', decimal: true),
      publishedAt: _headerText(body['published_at'], field: 'published_at'),
      nonce: nonce,
      payload: payload,
    );
  }

  /// The exact length-delimited AAD tuple both ends must reproduce.
  ///
  /// Each field is a big-endian `uint64` byte length followed by its UTF-8
  /// bytes, in this fixed order. The length prefixes are what stop two
  /// different header tuples from sharing an encoding — without them
  /// `("ab", "c")` and `("a", "bc")` authenticate the same bytes.
  Uint8List get aad {
    final encoded = BytesBuilder(copy: false);
    for (final value in [schemaVersion, pairingId, seq, publishedAt]) {
      final raw = utf8.encode(value);
      final length = Uint8List(8);
      ByteData.sublistView(length).setUint64(0, raw.length);
      encoded
        ..add(length)
        ..add(raw);
    }
    return encoded.takeBytes();
  }

  /// Authenticate, decrypt, and return the body — refusing anything whose
  /// header and body do not describe the same publication.
  ///
  /// Throws [AesGcmAuthenticationException] if the envelope was not sealed by a
  /// holder of `key`, [PayloadHeaderMismatchException] if the two authenticated
  /// copies of the header disagree, and [PayloadFormatException] if the body is
  /// not a shape this build reads.
  ///
  /// Returns both the verified text and its parse, for the reason
  /// [OpenedPayload] gives: this is the only place the two can be paired, and
  /// pairing them anywhere else would be a claim rather than a fact.
  OpenedPayload open({required Uint8List key}) {
    final plaintext = aes256GcmOpen(payload, key: key, nonce: nonce, aad: aad);

    final String text;
    try {
      text = utf8.decode(plaintext);
    } on FormatException catch (error) {
      throw PayloadFormatException('payload body is not UTF-8: ${error.message}');
    }

    final Object? decoded;
    try {
      decoded = jsonDecode(text);
    } on FormatException catch (error) {
      throw PayloadFormatException('payload is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadFormatException('payload is not a JSON object');
    }

    // Compared before the body is parsed, so a mismatch cannot be reported as
    // whichever field `PhonePayload` happens to validate first.
    _requireAgreement(decoded, 'schema_version', schemaVersion);
    _requireAgreement(decoded, 'pairing_id', pairingId);
    _requireAgreement(decoded, 'seq', seq);
    _requireAgreement(decoded, 'published_at', publishedAt);

    // `text` and not a re-encoding of `decoded`: `jsonEncode` would reorder
    // nothing on a `Map` but would normalise spacing and escapes, and the copy
    // this phone keeps has to be the bytes that were authenticated rather than
    // a spelling of them this build happened to produce.
    return OpenedPayload._(text: text, payload: PhonePayload.fromJson(decoded));
  }

  /// Byte-for-byte string equality, deliberately, rather than comparing parsed
  /// values.
  ///
  /// `publisher.py` builds the header and the body from **one** `published_at`
  /// variable and one `seq` variable, so identical spelling is the invariant
  /// the host actually maintains. Comparing parsed instants instead would
  /// accept `...T09:00:00Z` against `...T09:00:00+00:00` — the same moment,
  /// but two different strings going into the AAD and into the history
  /// record's identity, which is the ambiguity this check exists to remove.
  static void _requireAgreement(
    Map<String, Object?> body,
    String field,
    String headerValue,
  ) {
    if (body[field] != headerValue) {
      throw PayloadHeaderMismatchException(field);
    }
  }

  static String _headerText(Object? value, {required String field, bool decimal = false}) {
    if (value is! String || value.isEmpty || value.trim() != value) {
      throw PayloadEnvelopeException(
        '$field must be non-empty text without surrounding space',
      );
    }
    if (decimal && !isCanonicalDecimal(value)) {
      throw PayloadEnvelopeException('$field must be a canonical positive decimal string');
    }
    return value;
  }

  /// Whether [value] is the canonical positive decimal spelling this envelope
  /// fixes `schema_version` and `seq` to.
  ///
  /// Public because a *second* reader of a document this envelope wrote must
  /// apply the same rule rather than keep its own copy of the pattern:
  /// `held_copy_store.dart` classifies a `schema_version` read back off disk,
  /// and two spellings of this predicate are two ways for a build to disagree
  /// with a document it wrote itself.
  static bool isCanonicalDecimal(String value) => _decimal.hasMatch(value);

  /// No sign, no leading zero, no zero. A counter with two spellings is a
  /// counter I6's `last_fetch_seq == last_seq` equality cannot be trusted on.
  static final RegExp _decimal = RegExp(r'^[1-9][0-9]*$');

  /// Unpadded, canonical base64url — the host's `_open_base64url`.
  ///
  /// Canonicality is checked by re-encoding rather than by inspecting the
  /// characters, which also rules out the standard `+/` alphabet: Dart's
  /// decoder accepts both, and only the round trip notices that the input was
  /// not the spelling section 6.1 fixes.
  static Uint8List _base64url(Object? value, {required String field}) {
    if (value is! String || value.isEmpty || value.contains('=')) {
      throw PayloadEnvelopeException('$field must be unpadded base64url text');
    }
    final Uint8List raw;
    try {
      raw = base64Url.decode(base64Url.normalize(value));
    } on FormatException {
      throw PayloadEnvelopeException('$field must be unpadded base64url text');
    }
    if (base64Url.encode(raw).replaceAll('=', '') != value) {
      throw PayloadEnvelopeException('$field must use canonical base64url encoding');
    }
    return raw;
  }
}
