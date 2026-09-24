import 'package:flutter/foundation.dart';

import 'payload_format_exception.dart';

/// The publication counter **I6** compares — as a number, not as the string the
/// wire carries.
///
/// `DESIGN.md` §9.3 turns on three comparisons (`seq > last_seq` accept,
/// `== ` nothing new, `< ` **refuse**), and `PhonePayload.seq` is deliberately
/// *"kept as the string the wire carries"* so the phone stores the host's own
/// bytes. Those two facts are both right and they do not compose: comparing the
/// stored strings is lexicographic, and lexicographically `'9' > '10'`.
///
/// **That is not a corner case, it is day ten.** The host allocates
/// `max(seq) + 1` and renders it with `str()` (`publisher.py`), so the very
/// first crossing is the tenth publication — at which point a string comparison
/// reads a legitimate new payload as a *downgrade*, refuses it under I6, and
/// raises the persistent warning that §9.3 says clears *only* when a greater
/// `seq` arrives. Under the same broken comparison the next `seq` that counts as
/// greater is `'91'`. The phone would sit on a stale copy for eighty-one
/// publications, warning about an attack that never happened.
///
/// So this type exists rather than a helper function: parsing is the only way to
/// obtain one, comparison is a method on it, and there is no `<` on the wire
/// string for a call site to reach for. The guard is the funnel.
@immutable
class PublicationSeq implements Comparable<PublicationSeq> {
  const PublicationSeq._(this.value, this.wire);

  /// Parse the wire form, refusing anything the host would not have produced.
  ///
  /// The host renders `int(max(seq)) + 1` through `str()`, so the accepted shape
  /// is exactly that: a non-empty run of ASCII digits, no sign and no leading
  /// zero. Leading zeros are refused rather than tolerated because [wire] is the
  /// form that gets *stored* — `'007'` and `'7'` would be one value with two
  /// stored representations, and the equality I6 relies on would depend on which
  /// one a given publication happened to use.
  ///
  /// Refusing is `PayloadFormatException`, the same class the rest of the parse
  /// layer throws, because an unparseable `seq` is an unusable payload: I6
  /// cannot be evaluated against it, and accepting it "just to display" would
  /// silently disable the one check that survives a valid ciphertext.
  factory PublicationSeq.parse(String wire, {String field = 'seq'}) {
    if (wire.isEmpty) {
      throw PayloadFormatException('$field is empty');
    }
    for (final unit in wire.codeUnits) {
      if (unit < _zero || unit > _nine) {
        throw PayloadFormatException('$field is not a decimal counter: $wire');
      }
    }
    if (wire.length > 1 && wire.codeUnitAt(0) == _zero) {
      throw PayloadFormatException('$field has a leading zero: $wire');
    }
    // `int.parse` cannot fail here — the digits are already checked — but it can
    // overflow into a double's territory on a 64-bit boundary. A counter that
    // reached 2^63 would be a different problem entirely; refusing is still the
    // honest answer rather than wrapping.
    final parsed = int.tryParse(wire);
    if (parsed == null) {
      throw PayloadFormatException('$field is out of range: $wire');
    }
    return PublicationSeq._(parsed, wire);
  }

  static const int _zero = 0x30;
  static const int _nine = 0x39;

  /// The counter, for comparison.
  final int value;

  /// The exact string the host published, for storage.
  ///
  /// Kept beside [value] so a stored baseline round-trips to the bytes the host
  /// sent rather than to this app's rendering of a number it parsed.
  final String wire;

  @override
  int compareTo(PublicationSeq other) => value.compareTo(other.value);

  bool operator >(PublicationSeq other) => value > other.value;

  bool operator <(PublicationSeq other) => value < other.value;

  @override
  bool operator ==(Object other) => other is PublicationSeq && other.value == value;

  @override
  int get hashCode => value.hashCode;

  @override
  String toString() => wire;
}
