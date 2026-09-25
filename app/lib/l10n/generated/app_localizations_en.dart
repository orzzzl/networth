// ignore: unused_import
import 'package:intl/intl.dart' as intl;
import 'app_localizations.dart';

// ignore_for_file: type=lint

/// The translations for English (`en`).
class AppLocalizationsEn extends AppLocalizations {
  AppLocalizationsEn([String locale = 'en']) : super(locale);

  @override
  String get appTitle => 'Net worth';

  @override
  String get snapshotUnreadable => 'couldn\'t read the published snapshot';

  @override
  String instantUtc(DateTime date, DateTime time) {
    final intl.DateFormat dateDateFormat = intl.DateFormat.yMMMd(localeName);
    final String dateString = dateDateFormat.format(date);
    final intl.DateFormat timeDateFormat = intl.DateFormat.Hm(localeName);
    final String timeString = timeDateFormat.format(time);

    return '$dateString, $timeString UTC';
  }

  @override
  String totalAsOf(String timestamp) {
    return 'as of $timestamp';
  }

  @override
  String totalUndatable(int undatableCount, int accountCount) {
    String _temp0 = intl.Intl.pluralLogic(
      accountCount,
      locale: localeName,
      other:
          'can\'t date this total — $undatableCount of $accountCount accounts can\'t be dated',
      one:
          'can\'t date this total — $undatableCount of $accountCount account can\'t be dated',
    );
    return '$_temp0';
  }

  @override
  String get totalNoDatedSource => 'no dated source for this total';

  @override
  String includesStaticValuations(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other: 'includes $count fixed manual valuations',
      one: 'includes $count fixed manual valuation',
    );
    return '$_temp0';
  }

  @override
  String get accountsLabel => 'Accounts';

  @override
  String get connectionOk => 'all reporting normally';

  @override
  String get connectionWaiting => 'some data is behind — nothing for you to do';

  @override
  String get connectionActionNeeded => 'an account needs your attention';

  @override
  String get alertsLabel => 'Action needed';

  @override
  String alertNeedsReauth(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count connections need you to sign in again before they can update.',
      one: 'A connection needs you to sign in again before it can update.',
    );
    return '$_temp0';
  }

  @override
  String alertRevoked(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count connections were disconnected and need to be linked again.',
      one: 'A connection was disconnected and needs to be linked again.',
    );
    return '$_temp0';
  }

  @override
  String alertFrozenData(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count accounts\' values have stopped changing, even though their connections look fine.',
      one:
          'An account\'s value has stopped changing, even though its connection looks fine.',
    );
    return '$_temp0';
  }

  @override
  String alertPendingReconciliation(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count accounts aren\'t in your total yet — they still need matching to the accounts they replace.',
      one:
          'An account isn\'t in your total yet — it still needs matching to the account it replaces.',
    );
    return '$_temp0';
  }

  @override
  String alertShareCountUnconfirmed(int count) {
    String _temp0 = intl.Intl.pluralLogic(
      count,
      locale: localeName,
      other:
          '$count share counts haven\'t been confirmed since you set them, so their values may be out of date.',
      one:
          'A share count hasn\'t been confirmed since you set it, so its value may be out of date.',
    );
    return '$_temp0';
  }

  @override
  String get thisCopyLabel => 'This copy';

  @override
  String copyFresh(String timestamp) {
    return 'published $timestamp';
  }

  @override
  String copyStale(String timestamp) {
    return 'showing a copy from $timestamp';
  }

  @override
  String copyUnknownShowing(String timestamp) {
    return 'showing a copy dated $timestamp';
  }

  @override
  String copyReasonHostNotPublishing(String timestamp) {
    return 'nothing newer has been published — your server last confirmed this copy $timestamp';
  }

  @override
  String copyReasonHostServingNothing(String timestamp) {
    return 'your server answered $timestamp and has no snapshot to give';
  }

  @override
  String get copyReasonNeverFetched => 'this device hasn\'t checked yet';

  @override
  String get copyReasonOffline => 'no network here, so it couldn\'t check';

  @override
  String get copyReasonHostUnreachable =>
      'the network is fine, but your server didn\'t answer';

  @override
  String get copyReasonCredentialRejected =>
      'your server refused this device — it needs pairing again';

  @override
  String get copyReasonTransportError =>
      'your server answered with something this app couldn\'t use';

  @override
  String get copyReasonUnknownFailure =>
      'it couldn\'t check, and this device can\'t say why';

  @override
  String get copyReasonNotCheckedSinceDue =>
      'this device hasn\'t needed to check again yet';

  @override
  String get copyReasonRecordsUnusable =>
      'this device can\'t read its own notes, so it can\'t tell why';

  @override
  String get copyReasonServedPayloadNotHeld =>
      'your server\'s latest copy isn\'t the one shown here';

  @override
  String get copyUnknownFuture =>
      'the copy is dated ahead of this device\'s clock, so its age can\'t be worked out';

  @override
  String get copyUnknownClockMovedBackwards =>
      'this device\'s clock has moved backwards, so its age can\'t be worked out';

  @override
  String get copyUnknownClockUnconfirmed =>
      'this device can\'t confirm its own clock, so its age can\'t be worked out';

  @override
  String copyReasonLastChecked(String reason, String timestamp) {
    return '$reason — last checked $timestamp';
  }

  @override
  String get connectionAsOfCopy => 'as of this copy, not now';

  @override
  String get historyLabel => 'History';

  @override
  String get historyEmpty => 'no readings recorded yet';

  @override
  String get historyCurrencyMismatch =>
      'history is in a different currency from the total, so it isn\'t shown';

  @override
  String get historyIncompleteNote => 'dashed where a reading was incomplete';

  @override
  String get historyUnreadable => 'couldn\'t read the history';

  @override
  String get historyGapNote => 'breaks are days with no reading';

  @override
  String get historyNotRecorded =>
      'this reading couldn\'t be saved, so it won\'t appear in the history';
}
