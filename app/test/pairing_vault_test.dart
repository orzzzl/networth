import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/pairing/pairing_provision.dart';
import 'package:networth_app/src/pairing/pairing_vault.dart';

const pairingId = '00000000-0000-4000-8000-000000000002';
const encodedKey = 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8';
const tailnetName = 'vps.synthetic-tailnet.ts.net';
const encoded = 'networth-pairing:v1:$pairingId:$encodedKey:$tailnetName';

final class MemorySecureStringStore implements SecureStringStore {
  final values = <String, String>{};

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }

  @override
  Future<String?> read({required String key}) async => values[key];

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }
}

void main() {
  test('the daemon pairing string parses without a read token', () {
    final provision = PairingProvision.parse(encoded);

    expect(provision.pairingId, pairingId);
    expect(
      listEquals(
        provision.payloadKey,
        List<int>.generate(32, (index) => index),
      ),
      isTrue,
    );
    expect(provision.tailnetName, tailnetName);
    expect(provision.encoded, encoded);
    expect(encoded.toLowerCase(), isNot(contains('token')));
    expect(provision.toString(), contains('payloadKey: <redacted>'));
    expect(provision.toString(), isNot(contains(encodedKey)));
  });

  test('version, extra field, key, id, and host all fail closed', () {
    final invalid = <String>[
      encoded.replaceFirst(':v1:', ':v2:'),
      '$encoded:read-token',
      encoded.replaceFirst(encodedKey, '$encodedKey='),
      encoded.replaceFirst('-4000-', '-3000-'),
      encoded.replaceFirst(tailnetName, 'host-prefix'),
    ];

    for (final value in invalid) {
      expect(
        () => PairingProvision.parse(value),
        throwsA(isA<PairingProvisionFormatException>()),
        reason: value,
      );
    }
  });

  test(
    'the vault validates first and stores the whole bundle as one value',
    () async {
      final store = MemorySecureStringStore();
      final vault = PairingVault(store: store);

      final provision = await vault.provision(encoded);
      expect(provision.encoded, encoded);
      expect(store.values, {'networth.pairing.v1': encoded});
      expect((await vault.read())?.encoded, encoded);

      await expectLater(
        vault.provision('$encoded:read-token'),
        throwsA(isA<PairingProvisionFormatException>()),
      );
      expect(store.values, {'networth.pairing.v1': encoded});

      await vault.clear();
      expect(await vault.read(), isNull);
    },
  );
}
