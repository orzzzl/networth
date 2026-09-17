import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'pairing_provision.dart';

/// The narrow string boundary the pairing vault needs from protected storage.
///
/// Tests use an in-memory implementation. Production uses
/// [AndroidKeystoreStringStore], whose plugin defaults are AES-GCM data
/// encryption with a key protected by the Android Keystore.
abstract interface class SecureStringStore {
  Future<void> write({required String key, required String value});

  Future<String?> read({required String key});

  Future<void> delete({required String key});
}

final class AndroidKeystoreStringStore implements SecureStringStore {
  AndroidKeystoreStringStore({FlutterSecureStorage? storage})
    : _storage =
          storage ??
          const FlutterSecureStorage(
            aOptions: AndroidOptions(storageNamespace: 'networth_pairing'),
          );

  final FlutterSecureStorage _storage;

  @override
  Future<void> write({required String key, required String value}) =>
      _storage.write(key: key, value: value);

  @override
  Future<String?> read({required String key}) => _storage.read(key: key);

  @override
  Future<void> delete({required String key}) => _storage.delete(key: key);
}

/// Stores the whole pairing bundle under one protected key.
///
/// One value prevents a crash from leaving the key, pairing id, and tailnet
/// name from different scans. Parsing happens before the write, so extra fields
/// and the deleted read-token design never reach protected storage.
final class PairingVault {
  PairingVault({SecureStringStore? store})
    : _store = store ?? AndroidKeystoreStringStore();

  static const _storageKey = 'networth.pairing.v1';
  final SecureStringStore _store;

  Future<PairingProvision> provision(String scannedOrTyped) async {
    final provision = PairingProvision.parse(scannedOrTyped);
    await _store.write(key: _storageKey, value: provision.encoded);
    return provision;
  }

  Future<PairingProvision?> read() async {
    final encoded = await _store.read(key: _storageKey);
    return encoded == null ? null : PairingProvision.parse(encoded);
  }

  Future<void> clear() => _store.delete(key: _storageKey);
}
