import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/money.dart';

Money _usd(int minorUnits) => Money(minorUnits: minorUnits, currency: 'USD');

void main() {
  group('money is integer minor units all the way to the string', () {
    test('groups thousands and keeps both minor digits', () {
      expect(_usd(4250000).format(), r'$42,500.00');
      expect(_usd(68000000).format(), r'$680,000.00');
      expect(_usd(123456789).format(), r'$1,234,567.89');
    });

    test('small amounts keep their leading zero', () {
      expect(_usd(5).format(), r'$0.05');
      expect(_usd(0).format(), r'$0.00');
      expect(_usd(99).format(), r'$0.99');
      expect(_usd(100).format(), r'$1.00');
    });

    test('the group separator lands on the right boundaries', () {
      expect(_usd(99999).format(), r'$999.99');
      expect(_usd(100000).format(), r'$1,000.00');
      expect(_usd(99999999).format(), r'$999,999.99');
      expect(_usd(100000000).format(), r'$1,000,000.00');
    });

    test('a negative net worth is rendered as negative, not as an absolute value', () {
      expect(_usd(-4250000).format(), r'-$42,500.00');
      expect(_usd(-5).format(), r'-$0.05');
    });

    test('a currency with no symbol we are sure of is spelled out', () {
      expect(Money(minorUnits: 4250000, currency: 'EUR').format(), '42,500.00 EUR');
    });

    test('value equality, so widget tests compare figures rather than objects', () {
      expect(_usd(100), _usd(100));
      expect(_usd(100), isNot(Money(minorUnits: 100, currency: 'EUR')));
    });
  });
}
