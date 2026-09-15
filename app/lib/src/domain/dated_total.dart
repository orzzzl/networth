import 'package:flutter/foundation.dart';

import 'money.dart';
import 'payload_format_exception.dart';

/// The headline total together with its age state — one value, never two.
///
/// This type is how invariant **I4** is kept structurally rather than by
/// review: there is no constructor anywhere that yields an amount without an
/// age state, so "renders a bare total" is not a code path that exists to be
/// tested for. Every consumer must `switch` over the three variants, and Dart
/// checks that switch for exhaustiveness at compile time, so a fourth age state
/// added later cannot silently fall through to an unannotated number.
///
/// `DESIGN.md` §8.1 R3 defines the three states and, importantly, what each one
/// is *allowed to know*.
@immutable
sealed class DatedTotal {
  const DatedTotal({
    required this.amount,
    required this.assets,
    required this.liabilities,
    required this.staticAccountCount,
  });

  /// Net worth: the number the headline shows.
  final Money amount;
  final Money assets;
  final Money liabilities;

  /// How many accounts are fixed manual valuations.
  ///
  /// §8.1 R3 excludes these from the age basis, and is explicit that excluding
  /// them is only honest if they are still counted out loud — so this rides
  /// along with all three variants rather than only the static one.
  final int staticAccountCount;

  /// Parse the `total` object of a task-19 payload body.
  ///
  /// Refuses rather than guesses. In particular each state's date field is
  /// checked against §8.1 R3's table — `KNOWN` must carry a date and the other
  /// two must not — because a payload that disagrees with the table is a
  /// payload we do not understand, and rendering it would be the exact class of
  /// error this project exists to refuse.
  factory DatedTotal.fromJson(Map<String, Object?> total) {
    final amount = _money(total, 'value_minor');
    final assets = _money(total, 'assets_minor');
    final liabilities = _money(total, 'liabilities_minor');
    final staticAccountCount = _int(total, 'static_account_count');
    final asOf = _optionalTimestamp(total, 'as_of');
    final state = _string(total, 'age_state');

    switch (state) {
      case 'KNOWN':
        if (asOf == null) {
          throw const PayloadFormatException('age_state KNOWN carries no as_of');
        }
        return KnownAgeTotal(
          amount: amount,
          assets: assets,
          liabilities: liabilities,
          staticAccountCount: staticAccountCount,
          asOf: asOf,
        );
      case 'UNKNOWN':
        if (asOf != null) {
          throw const PayloadFormatException('age_state UNKNOWN carries an as_of');
        }
        return UndatableTotal(
          amount: amount,
          assets: assets,
          liabilities: liabilities,
          staticAccountCount: staticAccountCount,
          undatableAccountCount: _int(total, 'unknown_freshness_account_count'),
          accountCount: _int(total, 'account_count'),
        );
      case 'STATIC_ONLY':
        if (asOf != null) {
          throw const PayloadFormatException('age_state STATIC_ONLY carries an as_of');
        }
        return StaticOnlyTotal(
          amount: amount,
          assets: assets,
          liabilities: liabilities,
          staticAccountCount: staticAccountCount,
        );
      default:
        throw PayloadFormatException('unknown age_state "$state"');
    }
  }

  static Money _money(Map<String, Object?> total, String field) =>
      Money(minorUnits: _int(total, field), currency: _string(total, 'currency'));

  static int _int(Map<String, Object?> total, String field) {
    final value = total[field];
    if (value is! int) {
      throw PayloadFormatException('total.$field is not an integer');
    }
    return value;
  }

  static String _string(Map<String, Object?> total, String field) {
    final value = total[field];
    if (value is! String) {
      throw PayloadFormatException('total.$field is not a string');
    }
    return value;
  }

  static DateTime? _optionalTimestamp(Map<String, Object?> total, String field) {
    final value = total[field];
    if (value == null) {
      return null;
    }
    if (value is! String) {
      throw PayloadFormatException('total.$field is not a string');
    }
    final parsed = DateTime.tryParse(value);
    if (parsed == null) {
      throw PayloadFormatException('total.$field is not a timestamp');
    }
    return parsed.toUtc();
  }
}

/// Every account in the age basis has a source clock; [asOf] is the oldest.
final class KnownAgeTotal extends DatedTotal {
  const KnownAgeTotal({
    required super.amount,
    required super.assets,
    required super.liabilities,
    required super.staticAccountCount,
    required this.asOf,
  });

  final DateTime asOf;
}

/// At least one contributing account has no knowable age, so the total has none.
///
/// **This variant deliberately has no date field at all**, and that is the whole
/// point of it. §8.1 R3 records `oldest_known_source_as_of` as a diagnostic and
/// forbids presenting it as the total's age; the failure mode it warns about is
/// an implementation that "quietly prints the oldest known date". Here that is
/// not a discipline anyone has to remember — the date never reaches this object,
/// so the widget has nothing to print even if it tried.
final class UndatableTotal extends DatedTotal {
  const UndatableTotal({
    required super.amount,
    required super.assets,
    required super.liabilities,
    required super.staticAccountCount,
    required this.undatableAccountCount,
    required this.accountCount,
  });

  /// `N` in "N of M accounts can't be dated".
  final int undatableAccountCount;

  /// `M` in "N of M accounts can't be dated".
  final int accountCount;
}

/// The age basis is empty — every asset is a fixed manual valuation.
///
/// §8.1: this is the real state before the first Item is ever linked, not a
/// theoretical one, so the app must be able to say it rather than compute an age
/// over an empty set.
final class StaticOnlyTotal extends DatedTotal {
  const StaticOnlyTotal({
    required super.amount,
    required super.assets,
    required super.liabilities,
    required super.staticAccountCount,
  });
}
