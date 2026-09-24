/// AES-256-GCM *opening*, and deliberately nothing else.
///
/// **Why this is hand-written rather than a package.** `AGENTS.md` admits no new
/// dependency without an OK written in the task spec, and task `22` grants none
/// — but the more useful precedent is that the host had this exact problem for
/// task `19` and answered it the same way. `networth/payload.py` says so in its
/// own first paragraph: *"The project cannot add a runtime dependency unless a
/// task explicitly permits one. This module therefore implements the small
/// AES-256-GCM surface task 19 needs directly and pins it with NIST vectors in
/// the test suite."* This file is the phone's half of that same decision, and
/// the two are written to be read against each other — same construction, same
/// order of operations, same names where Dart allows them.
///
/// **It opens and it cannot seal.** The phone is only ever the receiving end of
/// section 6.1: it holds the payload key so it can *read* what the host
/// published, and it has nothing to publish back. A `seal` here would be code
/// the product never executes, which is the kind of code that later gets used
/// because it is there. Sealing is also the side where a repeated nonce is
/// catastrophic, so the safest implementation of it is none.
///
/// That leaves testing, and it improves it rather than costing anything: with
/// no encoder of our own to round-trip against, every vector in
/// `test/aes_gcm_test.dart` is an **external literal** — published NIST/CAVP
/// ciphertext that this code must reproduce a plaintext from. A round trip
/// through a matched pair of our own functions cannot see an error that both
/// halves share; a fixed vector can.
library;

import 'dart:typed_data';

/// The 16-byte authentication tag GCM appends to the ciphertext.
const int aesGcmTagBytes = 16;

/// The 12-byte nonce section 6.1 fixes. Other lengths need a different J0
/// derivation and are not accepted rather than silently mis-derived.
const int aesGcmNonceBytes = 12;

const int _keyBytes = 32;

/// The tag did not match, or the sealed input was not even long enough to hold
/// one.
///
/// **One exception for both, with one message.** Distinguishing "wrong tag"
/// from "truncated input" in the type or the text would hand an attacker a
/// classifier for free, and neither answer is one the caller can act on
/// differently: in both cases the bytes are not something the host sealed with
/// this key, and the only correct response is to refuse them.
class AesGcmAuthenticationException implements Exception {
  const AesGcmAuthenticationException();

  @override
  String toString() => 'AesGcmAuthenticationException: payload authentication failed';
}

/// Authenticate `sealed` (`ciphertext || tag`) and return the plaintext.
///
/// Throws [AesGcmAuthenticationException] if the tag does not verify. **Nothing
/// is returned before that check passes** — not even to an internal caller —
/// which is the property the name "open" is carrying: unauthenticated
/// plaintext never exists as a value anyone could accidentally use.
Uint8List aes256GcmOpen(
  Uint8List sealed, {
  required Uint8List key,
  required Uint8List nonce,
  required Uint8List aad,
}) {
  if (key.length != _keyBytes) {
    throw ArgumentError.value(key.length, 'key', 'payload key must contain exactly 32 bytes');
  }
  if (nonce.length != aesGcmNonceBytes) {
    throw ArgumentError.value(
      nonce.length,
      'nonce',
      'payload nonce must contain exactly 12 bytes',
    );
  }
  if (sealed.length < aesGcmTagBytes) {
    throw const AesGcmAuthenticationException();
  }
  final ciphertext = Uint8List.sublistView(sealed, 0, sealed.length - aesGcmTagBytes);
  final suppliedTag = Uint8List.sublistView(sealed, sealed.length - aesGcmTagBytes);

  final roundKeys = _roundKeys(key);
  final initial = Uint8List(16)
    ..setRange(0, aesGcmNonceBytes, nonce)
    ..[15] = 1;

  final authentication = _ghash(_encryptBlock(Uint8List(16), roundKeys), aad, ciphertext);
  final expectedTag = _encryptBlock(initial, roundKeys);
  for (var i = 0; i < aesGcmTagBytes; i++) {
    expectedTag[i] ^= authentication[i];
  }
  if (!_constantTimeEquals(suppliedTag, expectedTag)) {
    throw const AesGcmAuthenticationException();
  }
  return _counterXor(ciphertext, initial: initial, roundKeys: roundKeys);
}

/// Compare in time that does not depend on *where* the first difference is.
///
/// The host uses `hmac.compare_digest` here and this is the same guard. The
/// channel is narrower than the usual one but it is real: the phone is the
/// client, so a malicious responder on the route does not read our timing
/// directly — it infers it from how soon the next request arrives. That is
/// noisy and not obviously exploitable, which is exactly the argument that
/// would have to be *right* for a byte-by-byte early return to be safe. The
/// constant-time version costs sixteen XORs.
bool _constantTimeEquals(Uint8List left, Uint8List right) {
  if (left.length != right.length) {
    return false;
  }
  var difference = 0;
  for (var i = 0; i < left.length; i++) {
    difference |= left[i] ^ right[i];
  }
  return difference == 0;
}

// --- AES-256 block cipher -------------------------------------------------

/// Multiply in GF(2^8) with the AES reduction polynomial.
int _gfByteMultiply(int left, int right) {
  var result = 0;
  var a = left;
  var b = right;
  for (var i = 0; i < 8; i++) {
    if (b & 1 != 0) {
      result ^= a;
    }
    final high = a & 0x80;
    a = (a << 1) & 0xFF;
    if (high != 0) {
      a ^= 0x1B;
    }
    b >>= 1;
  }
  return result;
}

int _bytePower(int value, int exponent) {
  var result = 1;
  var factor = value;
  var remaining = exponent;
  while (remaining != 0) {
    if (remaining & 1 != 0) {
      result = _gfByteMultiply(result, factor);
    }
    factor = _gfByteMultiply(factor, factor);
    remaining >>= 1;
  }
  return result;
}

int _rotateByte(int value, int count) => ((value << count) | (value >> (8 - count))) & 0xFF;

/// The S-box, **derived rather than transcribed**.
///
/// A 256-entry table copied into source is 256 chances to introduce a typo that
/// no round trip would catch, because both directions of a round trip would
/// share it. Building it from the field inverse and the affine transform means
/// the definition is what is checked, and the NIST vectors then check that.
final Uint8List _sbox = Uint8List.fromList(
  List<int>.generate(256, (value) {
    final inverse = value == 0 ? 0 : _bytePower(value, 254);
    return inverse ^
        _rotateByte(inverse, 1) ^
        _rotateByte(inverse, 2) ^
        _rotateByte(inverse, 3) ^
        _rotateByte(inverse, 4) ^
        0x63;
  }),
);

/// Expand the 32-byte key into fifteen 16-byte round keys.
List<Uint8List> _roundKeys(Uint8List key) {
  final words = <List<int>>[
    for (var offset = 0; offset < _keyBytes; offset += 4)
      List<int>.generate(4, (i) => key[offset + i]),
  ];
  var roundConstant = 1;
  while (words.length < 60) {
    var temporary = List<int>.from(words.last);
    if (words.length % 8 == 0) {
      temporary = [
        for (final value in [...temporary.sublist(1), temporary[0]]) _sbox[value],
      ];
      temporary[0] ^= roundConstant;
      roundConstant = _gfByteMultiply(roundConstant, 2);
    } else if (words.length % 8 == 4) {
      temporary = [for (final value in temporary) _sbox[value]];
    }
    final previous = words[words.length - 8];
    words.add([for (var i = 0; i < 4; i++) previous[i] ^ temporary[i]]);
  }
  return [
    for (var offset = 0; offset < words.length; offset += 4)
      Uint8List.fromList([
        for (var word = 0; word < 4; word++) ...words[offset + word],
      ]),
  ];
}

Uint8List _shiftRows(Uint8List state) {
  final shifted = Uint8List(16);
  for (var column = 0; column < 4; column++) {
    for (var row = 0; row < 4; row++) {
      shifted[4 * column + row] = state[4 * ((column + row) % 4) + row];
    }
  }
  return shifted;
}

Uint8List _mixColumns(Uint8List state) {
  final mixed = Uint8List(16);
  for (var offset = 0; offset < 16; offset += 4) {
    final first = state[offset];
    final second = state[offset + 1];
    final third = state[offset + 2];
    final fourth = state[offset + 3];
    mixed[offset] =
        _gfByteMultiply(first, 2) ^ _gfByteMultiply(second, 3) ^ third ^ fourth;
    mixed[offset + 1] =
        first ^ _gfByteMultiply(second, 2) ^ _gfByteMultiply(third, 3) ^ fourth;
    mixed[offset + 2] =
        first ^ second ^ _gfByteMultiply(third, 2) ^ _gfByteMultiply(fourth, 3);
    mixed[offset + 3] =
        _gfByteMultiply(first, 3) ^ second ^ third ^ _gfByteMultiply(fourth, 2);
  }
  return mixed;
}

Uint8List _encryptBlock(Uint8List block, List<Uint8List> roundKeys) {
  var state = Uint8List(16);
  for (var i = 0; i < 16; i++) {
    state[i] = block[i] ^ roundKeys[0][i];
  }
  for (var round = 1; round < roundKeys.length - 1; round++) {
    for (var i = 0; i < 16; i++) {
      state[i] = _sbox[state[i]];
    }
    state = _mixColumns(_shiftRows(state));
    final roundKey = roundKeys[round];
    for (var i = 0; i < 16; i++) {
      state[i] ^= roundKey[i];
    }
  }
  for (var i = 0; i < 16; i++) {
    state[i] = _sbox[state[i]];
  }
  state = _shiftRows(state);
  final last = roundKeys.last;
  for (var i = 0; i < 16; i++) {
    state[i] ^= last[i];
  }
  return state;
}

// --- GCM ------------------------------------------------------------------

void _incrementCounter(Uint8List counter) {
  for (var i = 15; i >= 12; i--) {
    counter[i] = (counter[i] + 1) & 0xFF;
    if (counter[i] != 0) {
      return;
    }
  }
}

Uint8List _counterXor(
  Uint8List data, {
  required Uint8List initial,
  required List<Uint8List> roundKeys,
}) {
  final output = Uint8List(data.length);
  final counter = Uint8List.fromList(initial);
  for (var offset = 0; offset < data.length; offset += 16) {
    _incrementCounter(counter);
    final mask = _encryptBlock(counter, roundKeys);
    final end = offset + 16 < data.length ? offset + 16 : data.length;
    for (var i = offset; i < end; i++) {
      output[i] = data[i] ^ mask[i - offset];
    }
  }
  return output;
}

/// Multiply two 128-bit blocks in GF(2^128), the host's `_galois_multiply`.
///
/// Dart has no 128-bit integer, so the value stays a 16-byte big-endian buffer
/// and the shift is done on bytes. Writing it as two 64-bit halves would need
/// unsigned 64-bit shifts, which Dart's signed `int` does not give without care
/// at the sign bit — a byte buffer has no such edge.
Uint8List _galoisMultiply(Uint8List left, Uint8List right) {
  final product = Uint8List(16);
  final factor = Uint8List.fromList(right);
  for (var bit = 0; bit < 128; bit++) {
    if (left[bit >> 3] & (0x80 >> (bit & 7)) != 0) {
      for (var i = 0; i < 16; i++) {
        product[i] ^= factor[i];
      }
    }
    final lsb = factor[15] & 1;
    for (var i = 15; i > 0; i--) {
      factor[i] = ((factor[i] >> 1) | ((factor[i - 1] & 1) << 7)) & 0xFF;
    }
    factor[0] >>= 1;
    if (lsb != 0) {
      factor[0] ^= 0xE1;
    }
  }
  return product;
}

Uint8List _ghash(Uint8List hashSubkey, Uint8List aad, Uint8List ciphertext) {
  var accumulator = Uint8List(16);

  void absorb(Uint8List block) {
    for (var i = 0; i < 16; i++) {
      accumulator[i] ^= block[i];
    }
    accumulator = _galoisMultiply(accumulator, hashSubkey);
  }

  void absorbPadded(Uint8List value) {
    for (var offset = 0; offset < value.length; offset += 16) {
      final block = Uint8List(16);
      final end = offset + 16 < value.length ? offset + 16 : value.length;
      block.setRange(0, end - offset, value, offset);
      absorb(block);
    }
  }

  absorbPadded(aad);
  absorbPadded(ciphertext);

  final lengths = Uint8List(16);
  ByteData.sublistView(lengths)
    ..setUint64(0, aad.length * 8)
    ..setUint64(8, ciphertext.length * 8);
  absorb(lengths);

  return accumulator;
}
