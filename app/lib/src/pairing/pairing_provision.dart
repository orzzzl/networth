import 'dart:convert';
import 'dart:typed_data';

/// The one-time secret bundle produced by `networth pair`.
///
/// The wire shape is deliberately closed. A fourth value is not ignored: in
/// particular, the removed read-token design cannot be smuggled back into the
/// app by a newer-looking QR code.
final class PairingProvision {
  PairingProvision._({
    required this.pairingId,
    required Uint8List payloadKey,
    required this.tailnetName,
    required this.encoded,
  }) : payloadKey = Uint8List.fromList(payloadKey).asUnmodifiableView();

  static const _prefix = 'networth-pairing:v1';
  static final _pairingId = RegExp(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$',
  );
  static final _tailnetName = RegExp(
    r'^(?=.{1,170}$)(?=.+\.)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$',
  );

  final String pairingId;
  final Uint8List payloadKey;
  final String tailnetName;

  /// The canonical string stored as one Android-Keystore-protected value.
  final String encoded;

  factory PairingProvision.parse(String value) {
    final parts = value.split(':');
    if (parts.length != 5 || '${parts[0]}:${parts[1]}' != _prefix) {
      throw const PairingProvisionFormatException(
        'Pairing code is not the exact supported v1 shape.',
      );
    }
    final pairingId = parts[2];
    if (!_pairingId.hasMatch(pairingId)) {
      throw const PairingProvisionFormatException(
        'Pairing code has an invalid pairing id.',
      );
    }
    final keyText = parts[3];
    if (keyText.isEmpty || keyText.contains('=')) {
      throw const PairingProvisionFormatException(
        'Pairing code has a non-canonical payload key.',
      );
    }
    late final Uint8List payloadKey;
    try {
      payloadKey = base64Url.decode(base64Url.normalize(keyText));
    } on FormatException {
      throw const PairingProvisionFormatException(
        'Pairing code has an invalid payload key.',
      );
    }
    final canonicalKey = base64Url.encode(payloadKey).replaceAll('=', '');
    if (payloadKey.length != 32 || canonicalKey != keyText) {
      throw const PairingProvisionFormatException(
        'Pairing code payload key must be canonical AES-256 material.',
      );
    }
    final tailnetName = parts[4];
    if (!_tailnetName.hasMatch(tailnetName)) {
      throw const PairingProvisionFormatException(
        'Pairing code must carry the VPS full tailnet DNS name.',
      );
    }
    return PairingProvision._(
      pairingId: pairingId,
      payloadKey: payloadKey,
      tailnetName: tailnetName,
      encoded: value,
    );
  }

  @override
  String toString() =>
      'PairingProvision(pairingId: $pairingId, payloadKey: <redacted>, '
      'tailnetName: $tailnetName)';
}

final class PairingProvisionFormatException implements FormatException {
  const PairingProvisionFormatException(this.message);

  @override
  final String message;

  @override
  int? get offset => null;

  @override
  String? get source => null;

  @override
  String toString() => 'PairingProvisionFormatException: $message';
}
