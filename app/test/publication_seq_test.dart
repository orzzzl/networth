import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/publication_seq.dart';

void main() {
  group('the counter I6 compares is a number, not the wire string', () {
    test('9 is less than 10, which a string comparison gets backwards', () {
      // **The whole reason this type exists.** Lexicographically `'9' > '10'`,
      // so a phone comparing the stored strings reads the tenth publication as
      // a downgrade — refuses it, and raises the persistent §9.3 warning that
      // clears only when a *greater* seq arrives, which under the same broken
      // comparison means `'91'`.
      final ninth = PublicationSeq.parse('9');
      final tenth = PublicationSeq.parse('10');

      expect(tenth > ninth, isTrue);
      expect(ninth < tenth, isTrue);
      // The string form really is the misleading one — this asserts the hazard
      // is real rather than hypothetical, so the test above cannot quietly
      // become a tautology if the wire format changes to zero-padded.
      expect('9'.compareTo('10'), greaterThan(0));
    });

    test('every power-of-ten boundary orders correctly', () {
      // One crossing could pass by luck; the counter meets a new one every
      // order of magnitude, and the host never stops incrementing.
      for (var digits = 1; digits <= 6; digits++) {
        final last = PublicationSeq.parse('9' * digits);
        final next = PublicationSeq.parse('1${'0' * digits}');
        expect(next > last, isTrue, reason: '${next.wire} should follow ${last.wire}');
      }
    });

    test('equality is by value, and ordering is total', () {
      expect(PublicationSeq.parse('42'), PublicationSeq.parse('42'));
      expect(PublicationSeq.parse('42').hashCode, PublicationSeq.parse('42').hashCode);
      expect(PublicationSeq.parse('42') > PublicationSeq.parse('42'), isFalse);
      expect(PublicationSeq.parse('42') < PublicationSeq.parse('42'), isFalse);
    });
  });

  group('it refuses anything the host would not have published', () {
    test('the stored form is the host bytes, so there is one of them', () {
      // `str(int)` never produces a leading zero, so `'007'` did not come from
      // the host. Tolerating it would give one counter value two stored
      // representations, and I6's `last_fetch_seq == last_seq` equality would
      // then depend on which spelling each publication happened to use.
      expect(() => PublicationSeq.parse('007'), throwsA(isA<PayloadFormatException>()));
      expect(PublicationSeq.parse('0').wire, '0');
      expect(PublicationSeq.parse('70').wire, '70');
    });

    test('non-digits, signs and emptiness are refused, not coerced', () {
      for (final bad in <String>['', ' 7', '7 ', '+7', '-7', '7.0', '0x7', '七', '1e3']) {
        expect(
          () => PublicationSeq.parse(bad),
          throwsA(isA<PayloadFormatException>()),
          reason: 'accepted ${bad.isEmpty ? '<empty>' : bad}',
        );
      }
    });

    test('a counter too large to compare is refused rather than wrapped', () {
      // I6 cannot be evaluated against a value that does not fit, and an
      // unevaluable I6 must not read as "accept".
      expect(
        () => PublicationSeq.parse('9' * 30),
        throwsA(isA<PayloadFormatException>()),
      );
    });

    test('the refusal is the parse layer\'s own exception', () {
      // Not an ArgumentError: an unparseable seq is an unusable payload, and it
      // has to travel the same path every other malformed field travels.
      expect(() => PublicationSeq.parse('nope'), throwsA(isA<PayloadFormatException>()));
    });
  });
}
