import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/aes_gcm.dart';
import 'package:networth_app/src/domain/payload_envelope.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

import 'fixtures.dart';

/// The fixed, non-secret key the checked-in envelopes are sealed under.
/// `scripts/seal-app-test-envelope.py` holds the other copy; if these two ever
/// disagree every test in the first group fails at once, which is the failure
/// you want rather than a subtle one.
final Uint8List testKey = Uint8List.fromList(List<int>.generate(32, (i) => i));

String readEnvelope(String name) => File('test/fixtures/$name').readAsStringSync();

Map<String, Object?> envelopeMap(String name) =>
    jsonDecode(readEnvelope(name)) as Map<String, Object?>;

String withField(String name, String field, Object? value) {
  final map = envelopeMap(name);
  if (value == null) {
    map.remove(field);
  } else {
    map[field] = value;
  }
  return jsonEncode(map);
}

void main() {
  group('a real host-sealed envelope', () {
    /// **The only place the two hand-written AES-256-GCM implementations meet.**
    /// Everything in `aes_gcm_test.dart` checks this one against published
    /// vectors; this checks it against `networth/payload.py` over a document
    /// the product actually ships. An agreement bug that both files share with
    /// the standard is impossible; one they share with each other and not with
    /// the standard is what the vectors catch. This covers the remaining case:
    /// the framing *around* the cipher — AAD field order, base64url spelling,
    /// where the tag lives — which no NIST vector says anything about.
    test('opens to exactly the shipped fixture', () {
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('known_envelope.json'));

      final opened = envelope.open(key: testKey).payload;
      final expected = PhonePayload.fromJsonString(readFixture(knownFixture));

      expect(opened.schemaVersion, expected.schemaVersion);
      expect(opened.pairingId, expected.pairingId);
      expect(opened.seq, expected.seq);
      expect(opened.publishedAt, expected.publishedAt);
      expect(opened.publishInterval, expected.publishInterval);
      expect(opened.grace, expected.grace);
      expect(opened.connectionState, expected.connectionState);
      expect(opened.total.amount, expected.total.amount);
      expect(opened.total.assets, expected.total.assets);
      expect(opened.total.liabilities, expected.total.liabilities);
      expect(opened.total.staticAccountCount, expected.total.staticAccountCount);
      expect(opened.total.isComplete, expected.total.isComplete);
    });

    test('the text it returns is the text it parsed, byte for byte', () {
      // **The correspondence `OpenedPayload` exists to make unbreakable.** The
      // held copy is stored as `text` and everything else reads `payload`, so a
      // build in which those two describe different publications would show one
      // thing now and a different thing after a restart — on the figures this
      // product exists to state carefully.
      //
      // Checked both ways round, because either alone passes on a defect: that
      // the text parses to the same payload rules out `text` being some other
      // document, and that it is *not* a re-encoding rules out the plausible
      // "fix" of returning `jsonEncode(decoded)`, which would parse identically
      // and still not be the bytes the tag authenticated.
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('known_envelope.json'));

      final opened = envelope.open(key: testKey);
      final reparsed = PhonePayload.fromJsonString(opened.text);

      expect(reparsed.seq, opened.payload.seq);
      expect(reparsed.publishedAt, opened.payload.publishedAt);
      expect(reparsed.total.amount, opened.payload.total.amount);
      expect(opened.text, readFixture(knownFixture));
    });

    test('the clear header is the one the body carries', () {
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('known_envelope.json'));

      expect(envelope.schemaVersion, '1');
      expect(envelope.pairingId, 'fixture-pairing');
      expect(envelope.seq, '41');
      expect(envelope.publishedAt, '2026-09-15T04:00:00.000000Z');
      expect(envelope.nonce.length, aesGcmNonceBytes);
    });

    test('the wrong key is refused', () {
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('known_envelope.json'));
      final other = Uint8List.fromList(testKey)..[0] ^= 0x01;

      expect(
        () => envelope.open(key: other),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });
  });

  /// The clear header is *authenticated* rather than merely carried, and these
  /// are the tests that say so. Each rewrites one header field in transit — the
  /// thing a hostile route on the tailnet can actually do — and each must fail
  /// the tag rather than change what the phone believes.
  ///
  /// `seq` and `published_at` are the two that matter most concretely: `seq` is
  /// I6's downgrade baseline (section 9.3) and `published_at` is what every
  /// staleness verdict on the screen is computed from. A header that could be
  /// edited in flight would let a replaying route choose both.
  group('rewriting the clear header breaks authentication', () {
    for (final entry in {
      'seq': '42',
      'published_at': '2026-09-15T05:00:00.000000Z',
      'pairing_id': 'other-pairing',
      'schema_version': '2',
    }.entries) {
      test('${entry.key} cannot be changed in transit', () {
        final envelope = PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', entry.key, entry.value),
        );

        expect(
          () => envelope.open(key: testKey),
          throwsA(isA<AesGcmAuthenticationException>()),
        );
      });
    }

    test('the sealed payload cannot be swapped for another publication', () {
      final envelope = PayloadEnvelope.fromJsonString(
        withField(
          'known_envelope.json',
          'payload',
          envelopeMap('seq_mismatch_envelope.json')['payload'],
        ),
      );

      expect(
        () => envelope.open(key: testKey),
        throwsA(isA<AesGcmAuthenticationException>()),
      );
    });
  });

  /// The pair that makes the point: **the same visible header, two different
  /// refusals.** Above, `seq: "42"` was edited in and failed the tag. Here it
  /// is genuinely sealed that way — only the key holder could produce it — and
  /// it is still refused, because the body underneath says `41`.
  group('a sealed envelope whose header and body disagree', () {
    test('is refused rather than resolved in favour of either number', () {
      final envelope = PayloadEnvelope.fromJsonString(
        readEnvelope('seq_mismatch_envelope.json'),
      );

      expect(
        () => envelope.open(key: testKey),
        throwsA(
          isA<PayloadHeaderMismatchException>().having((e) => e.field, 'field', 'seq'),
        ),
      );
    });

    test('authenticates cleanly, which is why the check has to exist', () {
      final envelope = PayloadEnvelope.fromJsonString(
        readEnvelope('seq_mismatch_envelope.json'),
      );

      // The refusal above is *not* the tag failing. Every byte here is
      // authentic under the payload key; what is wrong is that the publisher
      // sealed two different answers into one envelope. If the check in
      // `open` were removed, this envelope would be accepted and the phone
      // would hold `seq` 42 in the header and 41 in the record.
      expect(envelope.seq, '42');
      expect(
        () => envelope.open(key: testKey),
        isNot(throwsA(isA<AesGcmAuthenticationException>())),
      );
    });
  });

  group('the AAD is the exact length-delimited UTF-8 tuple', () {
    /// A literal, not a round trip through our own encoder.
    ///
    /// `pair-α` is **six code points and seven UTF-8 bytes**, and the prefix
    /// below says seven. That single digit is the whole test: a length written
    /// in characters instead of bytes agrees with the host on every ASCII
    /// pairing id and disagrees on the first non-ASCII one, which is a defect
    /// that ships. The host pins the same string in
    /// `tests/test_payload.py::test_aad_is_the_exact_length_delimited_utf8_tuple`.
    test('pins byte lengths and field order', () {
      final envelope = PayloadEnvelope.fromJson({
        'schema_version': '1',
        'pairing_id': 'pair-α',
        'seq': '137',
        'published_at': '2026-08-30T09:00:00Z',
        'nonce': 'AAECAwQFBgcICQoL',
        'payload': base64Url.encode(Uint8List(16)).replaceAll('=', ''),
      });

      expect(
        envelope.aad.map((b) => b.toRadixString(16).padLeft(2, '0')).join(),
        '0000000000000001' '31'
        '0000000000000007' '706169722dceb1'
        '0000000000000003' '313337'
        '0000000000000014' '323032362d30382d33305430393a30303a30305a',
      );
    });
  });

  group('the wire shape is refused when it is not section 6.1', () {
    for (final field in [
      'schema_version',
      'pairing_id',
      'seq',
      'published_at',
      'nonce',
      'payload',
    ]) {
      test('a missing $field', () {
        expect(
          () => PayloadEnvelope.fromJsonString(withField('known_envelope.json', field, null)),
          throwsA(isA<PayloadEnvelopeException>()),
        );
      });
    }

    /// An extra field is refused rather than ignored. A field the phone drops
    /// is a field the host believes it delivered; a version that adds one says
    /// so through `schema_version`.
    test('an unexpected field', () {
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'alerts', <Object?>[]),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    test('a non-object envelope', () {
      expect(
        () => PayloadEnvelope.fromJsonString('[]'),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    test('text that is not JSON', () {
      expect(
        () => PayloadEnvelope.fromJsonString('not json'),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    for (final value in <Object?>[7, true, <String, Object?>{}, <Object?>[]]) {
      test('a seq that is ${value.runtimeType} rather than text', () {
        expect(
          () => PayloadEnvelope.fromJsonString(withField('known_envelope.json', 'seq', value)),
          throwsA(isA<PayloadEnvelopeException>()),
        );
      });
    }

    /// One counter must have one spelling. I6 compares `last_fetch_seq` with
    /// `last_seq` for *equality*, so `"007"` and `"7"` being both acceptable
    /// would make that comparison answer a question nobody asked.
    for (final seq in ['', ' 41', '41 ', '007', '0', '-1', '4.1', '1e3', '٤١']) {
      test('a seq spelled "$seq"', () {
        expect(
          () => PayloadEnvelope.fromJsonString(withField('known_envelope.json', 'seq', seq)),
          throwsA(isA<PayloadEnvelopeException>()),
        );
      });
    }

    for (final version in ['', '01', '0', 'v1']) {
      test('a schema_version spelled "$version"', () {
        expect(
          () => PayloadEnvelope.fromJsonString(
            withField('known_envelope.json', 'schema_version', version),
          ),
          throwsA(isA<PayloadEnvelopeException>()),
        );
      });
    }

    for (final value in ['', ' fixture-pairing', 'fixture-pairing ']) {
      test('a pairing_id spelled "$value"', () {
        expect(
          () => PayloadEnvelope.fromJsonString(
            withField('known_envelope.json', 'pairing_id', value),
          ),
          throwsA(isA<PayloadEnvelopeException>()),
        );
      });
    }
  });

  group('base64url must be unpadded and canonical', () {
    test('padding is refused', () {
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'nonce', 'AAECAwQFBgcICQoL='),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    /// **This is the case the re-encode check exists for, and measurement says
    /// it is the only one.** `base64Url.decode` accepts the standard `+/`
    /// alphabet perfectly happily — the first two expectations below establish
    /// that rather than assume it — so nothing but comparing the re-encoding
    /// against the input notices that the string was not the spelling section
    /// 6.1 fixes. Two encodings of one nonce is one more than the contract
    /// allows.
    ///
    /// Worth recording because the host needs the same check for the *opposite*
    /// reason: Python's decoder rejects the wrong alphabet under `validate=True`
    /// and accepts non-canonical trailing bits, while Dart's does exactly the
    /// reverse (see the next test). The two implementations agree on what is
    /// refused only because each carries the check the other's decoder makes
    /// redundant.
    test('the standard alphabet is refused where base64url is required', () {
      final withPlus = base64.encode(List<int>.generate(12, (i) => 0xFB)).replaceAll('=', '');

      expect(withPlus.contains('+') || withPlus.contains('/'), isTrue);
      expect(
        base64Url.decode(base64Url.normalize(withPlus)),
        hasLength(12),
        reason: 'the decoder accepts it; the round-trip check is what must not',
      );

      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'nonce', withPlus),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    /// Non-canonical trailing bits, refused — **by Dart's decoder, not by the
    /// round-trip check**, and the test says which because the difference is
    /// exactly the kind of thing that otherwise gets assumed.
    ///
    /// The case cannot arise on `nonce` at all: twelve bytes is ninety-six
    /// bits, exactly sixteen base64 characters, so there are no spare bits to
    /// differ in. Seventeen bytes leaves two, and a decoder that ignored them
    /// would give four strings one meaning. `base64Url.decode` raises
    /// `FormatException` instead, which this type reports as the same refusal
    /// as any other bad encoding — one outcome for the caller however it was
    /// reached.
    test('non-canonical trailing bits are refused', () {
      final canonical = base64Url.encode(Uint8List(17)).replaceAll('=', '');
      final sameBytes = '${canonical.substring(0, canonical.length - 1)}B';

      expect(sameBytes, isNot(canonical));
      expect(
        () => base64Url.decode(base64Url.normalize(sameBytes)),
        throwsFormatException,
        reason: "Dart's decoder rejects spare bits; Python's accepts them",
      );

      // The canonical spelling of the same length is accepted, so the refusal
      // below is about the spelling and not about the length.
      expect(
        PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'payload', canonical),
        ).payload.length,
        17,
      );
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'payload', sameBytes),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    test('characters outside the alphabet are refused', () {
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'nonce', 'AAECAwQFBgcICQo!'),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    test('a nonce of the wrong length is refused', () {
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'nonce', base64Url.encode(Uint8List(16)).replaceAll('=', '')),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });

    test('a payload shorter than the tag is refused', () {
      expect(
        () => PayloadEnvelope.fromJsonString(
          withField('known_envelope.json', 'payload', base64Url.encode(Uint8List(15)).replaceAll('=', '')),
        ),
        throwsA(isA<PayloadEnvelopeException>()),
      );
    });
  });

  /// **Three distinct failures, and the transport must tell them apart.** The
  /// tag did not verify (something other than the host, or a corrupted route);
  /// the two authenticated headers disagreed (a publisher defect); or the body
  /// is a shape this build cannot read. Only the last one means the phone
  /// reached the right host and got something genuine it cannot render — which
  /// is a different sentence on screen and a different `last_fetch_error` in
  /// the record, so collapsing them would cost the owner the diagnosis.
  group('an authenticated body that is not a payload', () {
    test('is a format failure, after authentication has passed', () {
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('no_total_envelope.json'));

      expect(
        () => envelope.open(key: testKey),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('is not reported as an authentication or a header failure', () {
      final envelope = PayloadEnvelope.fromJsonString(readEnvelope('no_total_envelope.json'));

      expect(
        () => envelope.open(key: testKey),
        isNot(throwsA(isA<AesGcmAuthenticationException>())),
      );
      expect(
        () => envelope.open(key: testKey),
        isNot(throwsA(isA<PayloadHeaderMismatchException>())),
      );
    });
  });
}
