import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/aes_gcm.dart';

Uint8List _hex(String value) {
  final bytes = Uint8List(value.length ~/ 2);
  for (var i = 0; i < bytes.length; i++) {
    bytes[i] = int.parse(value.substring(i * 2, i * 2 + 2), radix: 16);
  }
  return bytes;
}

String _hexOf(Uint8List value) =>
    value.map((byte) => byte.toRadixString(16).padLeft(2, '0')).join();

/// Every vector below is an **external literal** — published GCM test data this
/// implementation must reproduce a plaintext from. None of it was produced by
/// this file or by anything that shares code with it.
///
/// That is the whole design of this suite. The phone implements `open` and no
/// `seal` (see `aes_gcm.dart`), so there is no matched encoder here to round
/// trip against — and a round trip is precisely the test that cannot see a
/// mistake both halves share. A wrong S-box, a wrong reduction polynomial or a
/// byte-swapped length block all survive `open(seal(x)) == x` and all fail
/// against these.
void main() {
  group('AES-256-GCM against published vectors', () {
    // McGrew & Viega, "The Galois/Counter Mode of Operation", test case 13.
    test('empty plaintext, empty AAD, zero key and nonce', () {
      final plaintext = aes256GcmOpen(
        _hex('530f8afbc74536b9a963b4f1c4cb738b'),
        key: Uint8List(32),
        nonce: Uint8List(12),
        aad: Uint8List(0),
      );

      expect(plaintext, isEmpty);
    });

    // Test case 14: one block of zeros. Exercises the block-aligned path.
    test('single zero block', () {
      final plaintext = aes256GcmOpen(
        _hex(
          'cea7403d4d606b6e074ec5d3baf39d18'
          'd0d1c8a799996bf0265b98b5d48ab919',
        ),
        key: Uint8List(32),
        nonce: Uint8List(12),
        aad: Uint8List(0),
      );

      expect(_hexOf(plaintext), '00' * 16);
    });

    // Test case 15: 64 bytes, four whole blocks, no AAD.
    test('four blocks, no AAD', () {
      final plaintext = aes256GcmOpen(
        _hex(
          '522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa'
          '8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662898015ad'
          'b094dac5d93471bdec1a502270e3cc6c',
        ),
        key: _hex(
          'feffe9928665731c6d6a8f9467308308'
          'feffe9928665731c6d6a8f9467308308',
        ),
        nonce: _hex('cafebabefacedbaddecaf888'),
        aad: Uint8List(0),
      );

      expect(
        _hexOf(plaintext),
        'd9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72'
        '1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b391aafd255',
      );
    });

    // Test case 16, and the one that earns its place: **both** lengths are
    // non-multiples of the block size — 60 bytes of ciphertext and 20 bytes of
    // AAD. The partial-block padding in GHASH and the short final block in CTR
    // are each wrong in a way the aligned vectors above cannot see, and the
    // real payload is a JSON document of arbitrary length, so this is the
    // shape the product actually runs.
    test('partial final block and partial AAD block', () {
      final plaintext = aes256GcmOpen(
        _hex(
          '522dc1f099567d07f47f37a32a84427d643a8cdcbfe5c0c97598a2bd2555d1aa'
          '8cb08e48590dbb3da7b08b1056828838c5f61e6393ba7a0abcc9f662'
          '76fc6ece0f4e1768cddf8853bb2d551b',
        ),
        key: _hex(
          'feffe9928665731c6d6a8f9467308308'
          'feffe9928665731c6d6a8f9467308308',
        ),
        nonce: _hex('cafebabefacedbaddecaf888'),
        aad: _hex('feedfacedeadbeeffeedfacedeadbeefabaddad2'),
      );

      expect(
        _hexOf(plaintext),
        'd9313225f88406e5a55909c5aff5269a86a7a9531534f7da2e4c303d8a318a72'
        '1c3c0c95956809532fcf0e2449a6b525b16aedf5aa0de657ba637b39',
      );
    });

    // CAVP gcmEncryptExtIV256, Count 0 — the same vector the host pins
    // `networth/payload.py` with. Sharing it is deliberate: it is the one
    // assertion that the two hand-written implementations agree with the
    // standard *and* with each other at the same point.
    test('CAVP gcmEncryptExtIV256 count 0', () {
      final plaintext = aes256GcmOpen(
        _hex(
          '8995ae2e6df3dbf96fac7b7137bae67f'
          'eca5aa77d51d4a0a14d9c51e1da474ab',
        ),
        key: _hex('92e11dcdaa866f5ce790fd24501f92509aacf4cb8b1339d50c9c1240935dd08b'),
        nonce: _hex('ac93a1a6145299bde902f21a'),
        aad: _hex('1e0889016f67601c8ebea4943bc23ad6'),
      );

      expect(_hexOf(plaintext), '2d71bcfa914e4ac045b2aa60955fad24');
    });
  });

  group('refusals', () {
    final key = _hex('92e11dcdaa866f5ce790fd24501f92509aacf4cb8b1339d50c9c1240935dd08b');
    final nonce = _hex('ac93a1a6145299bde902f21a');
    final aad = _hex('1e0889016f67601c8ebea4943bc23ad6');
    final sealed = _hex(
      '8995ae2e6df3dbf96fac7b7137bae67f'
      'eca5aa77d51d4a0a14d9c51e1da474ab',
    );

    test('a flipped tag bit is refused', () {
      final tampered = Uint8List.fromList(sealed)..[sealed.length - 1] ^= 0x01;

      expect(
        () => aes256GcmOpen(tampered, key: key, nonce: nonce, aad: aad),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    test('a flipped ciphertext bit is refused', () {
      final tampered = Uint8List.fromList(sealed)..[0] ^= 0x01;

      expect(
        () => aes256GcmOpen(tampered, key: key, nonce: nonce, aad: aad),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    /// The AAD is where section 6.1 puts the clear header, so this is the test
    /// that the header is *authenticated* rather than merely carried alongside.
    /// Without it, a route that can rewrite `seq` or `published_at` in transit
    /// rewrites what the phone believes about freshness and I6.
    test('a single changed AAD byte is refused', () {
      final tampered = Uint8List.fromList(aad)..[0] ^= 0x01;

      expect(
        () => aes256GcmOpen(sealed, key: key, nonce: nonce, aad: tampered),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    test('a truncated AAD is refused', () {
      expect(
        () => aes256GcmOpen(
          sealed,
          key: key,
          nonce: nonce,
          aad: Uint8List.sublistView(aad, 0, aad.length - 1),
        ),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    test('the wrong key is refused', () {
      final other = Uint8List.fromList(key)..[0] ^= 0x01;

      expect(
        () => aes256GcmOpen(sealed, key: other, nonce: nonce, aad: aad),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    test('the wrong nonce is refused', () {
      final other = Uint8List.fromList(nonce)..[0] ^= 0x01;

      expect(
        () => aes256GcmOpen(sealed, key: key, nonce: other, aad: aad),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    /// Shorter than a tag is not a decryption failure in any interesting sense,
    /// but it must take the same exit — a distinguishable one would tell a
    /// caller, and therefore an attacker, which of the two it hit.
    test('input shorter than the tag is refused as authentication failure', () {
      expect(
        () => aes256GcmOpen(Uint8List(15), key: key, nonce: nonce, aad: aad),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });

    test('a truncated ciphertext is refused', () {
      expect(
        () => aes256GcmOpen(
          Uint8List.sublistView(sealed, 0, sealed.length - 1),
          key: key,
          nonce: nonce,
          aad: aad,
        ),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });
  });

  group('argument shapes are rejected rather than mis-derived', () {
    test('a key that is not 32 bytes', () {
      expect(
        () => aes256GcmOpen(
          Uint8List(16),
          key: Uint8List(16),
          nonce: Uint8List(12),
          aad: Uint8List(0),
        ),
        throwsArgumentError,
      );
    });

    /// A nonce of another length needs a different J0 derivation (GHASH of the
    /// nonce rather than `nonce || 1`). Accepting one here would silently
    /// compute the wrong J0, so the length is a hard error.
    test('a nonce that is not 12 bytes', () {
      expect(
        () => aes256GcmOpen(
          Uint8List(16),
          key: Uint8List(32),
          nonce: Uint8List(16),
          aad: Uint8List(0),
        ),
        throwsArgumentError,
      );
    });
  });
}
