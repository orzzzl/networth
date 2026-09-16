import 'package:flutter/foundation.dart';

/// An amount held as an integer count of a currency's minor units.
///
/// `AGENTS.md`: money is never a float. The payload carries `value_minor` as an
/// integer and it stays an integer all the way to the string that is rendered —
/// there is no point in this file where a balance becomes a `double`.
@immutable
class Money {
  const Money({required this.minorUnits, required this.currency});

  /// The signed amount, in minor units (cents for a two-digit currency).
  final int minorUnits;

  /// The ISO-4217 code exactly as the payload spelled it.
  final String currency;

  /// Minor-unit digits assumed per currency.
  ///
  /// The payload does not carry an exponent, so this is an assumption rather
  /// than a fact read off the data, and it is written down here rather than
  /// spread through the formatter. Every currency the owner holds today is a
  /// two-digit one; a zero-digit currency (JPY) would render 100x too small,
  /// which is why this is a named constant and not an inline `2`.
  static const int minorDigits = 2;

  static const Map<String, String> _symbols = {'USD': r'$'};

  /// A grouped, fixed-point rendering — `$1,234.56`, or `1 234,56`-free plain
  /// `1,234.56 EUR` when there is no symbol we are confident about.
  String format() {
    final negative = minorUnits < 0;
    final digits = minorUnits.abs().toString().padLeft(minorDigits + 1, '0');
    final split = digits.length - minorDigits;
    final body = '${_group(digits.substring(0, split))}.${digits.substring(split)}';
    final symbol = _symbols[currency];
    final rendered = symbol == null ? '$body $currency' : '$symbol$body';
    return negative ? '-$rendered' : rendered;
  }

  static String _group(String whole) {
    final buffer = StringBuffer();
    for (var index = 0; index < whole.length; index++) {
      if (index > 0 && (whole.length - index) % 3 == 0) {
        buffer.write(',');
      }
      buffer.write(whole[index]);
    }
    return buffer.toString();
  }

  @override
  bool operator ==(Object other) =>
      other is Money && other.minorUnits == minorUnits && other.currency == currency;

  @override
  int get hashCode => Object.hash(minorUnits, currency);

  @override
  String toString() => 'Money(${format()})';
}
