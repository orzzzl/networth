import 'dart:async';

import 'package:flutter/foundation.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:intl/intl.dart' as intl;

import 'app_localizations_en.dart';

// ignore_for_file: type=lint

/// Callers can lookup localized strings with an instance of AppLocalizations
/// returned by `AppLocalizations.of(context)`.
///
/// Applications need to include `AppLocalizations.delegate()` in their app's
/// `localizationDelegates` list, and the locales they support in the app's
/// `supportedLocales` list. For example:
///
/// ```dart
/// import 'generated/app_localizations.dart';
///
/// return MaterialApp(
///   localizationsDelegates: AppLocalizations.localizationsDelegates,
///   supportedLocales: AppLocalizations.supportedLocales,
///   home: MyApplicationHome(),
/// );
/// ```
///
/// ## Update pubspec.yaml
///
/// Please make sure to update your pubspec.yaml to include the following
/// packages:
///
/// ```yaml
/// dependencies:
///   # Internationalization support.
///   flutter_localizations:
///     sdk: flutter
///   intl: any # Use the pinned version from flutter_localizations
///
///   # Rest of dependencies
/// ```
///
/// ## iOS Applications
///
/// iOS applications define key application metadata, including supported
/// locales, in an Info.plist file that is built into the application bundle.
/// To configure the locales supported by your app, you’ll need to edit this
/// file.
///
/// First, open your project’s ios/Runner.xcworkspace Xcode workspace file.
/// Then, in the Project Navigator, open the Info.plist file under the Runner
/// project’s Runner folder.
///
/// Next, select the Information Property List item, select Add Item from the
/// Editor menu, then select Localizations from the pop-up menu.
///
/// Select and expand the newly-created Localizations item then, for each
/// locale your application supports, add a new item and select the locale
/// you wish to add from the pop-up menu in the Value field. This list should
/// be consistent with the languages listed in the AppLocalizations.supportedLocales
/// property.
abstract class AppLocalizations {
  AppLocalizations(String locale)
    : localeName = intl.Intl.canonicalizedLocale(locale.toString());

  final String localeName;

  static AppLocalizations of(BuildContext context) {
    return Localizations.of<AppLocalizations>(context, AppLocalizations)!;
  }

  static const LocalizationsDelegate<AppLocalizations> delegate =
      _AppLocalizationsDelegate();

  /// A list of this localizations delegate along with the default localizations
  /// delegates.
  ///
  /// Returns a list of localizations delegates containing this delegate along with
  /// GlobalMaterialLocalizations.delegate, GlobalCupertinoLocalizations.delegate,
  /// and GlobalWidgetsLocalizations.delegate.
  ///
  /// Additional delegates can be added by appending to this list in
  /// MaterialApp. This list does not have to be used at all if a custom list
  /// of delegates is preferred or required.
  static const List<LocalizationsDelegate<dynamic>> localizationsDelegates =
      <LocalizationsDelegate<dynamic>>[
        delegate,
        GlobalMaterialLocalizations.delegate,
        GlobalCupertinoLocalizations.delegate,
        GlobalWidgetsLocalizations.delegate,
      ];

  /// A list of this localizations delegate's supported locales.
  static const List<Locale> supportedLocales = <Locale>[Locale('en')];

  /// The app's name, used as the window/task title and the app bar title.
  ///
  /// In en, this message translates to:
  /// **'Net worth'**
  String get appTitle;

  /// Shown when the payload cannot be loaded or parsed. Deliberately says nothing about a total: there is no total to speak of, and I2/I4 forbid a number without its age.
  ///
  /// In en, this message translates to:
  /// **'couldn\'t read the published snapshot'**
  String get snapshotUnreadable;

  /// An instant, always displayed in UTC with the zone named. The zone is shown rather than silently converted so the displayed date and the stored one stay the same fact. The month name comes from the locale's own date symbols, which is why this is a placeholder rather than a Dart constant list.
  ///
  /// In en, this message translates to:
  /// **'{date}, {time} UTC'**
  String instantUtc(DateTime date, DateTime time);

  /// The age annotation for a total whose whole basis has a known source clock. The timestamp is the OLDEST clock in the basis.
  ///
  /// In en, this message translates to:
  /// **'as of {timestamp}'**
  String totalAsOf(String timestamp);

  /// DESIGN.md §8.1 R3: when any part of the basis has no clock, the date is withheld entirely rather than shown beside a caveat — the section's argument is that a count next to a confident date does not stop the date being read. So this says what is missing and how much of the money it covers, and shows no date at all.
  ///
  /// In en, this message translates to:
  /// **'{accountCount, plural, one{can\'t date this total — {undatableCount} of {accountCount} account can\'t be dated} other{can\'t date this total — {undatableCount} of {accountCount} accounts can\'t be dated}}'**
  String totalUndatable(int undatableCount, int accountCount);

  /// The STATIC_ONLY age state. Says only what that state guarantees — the age basis is empty — and asserts no cause. The producer reaches it whenever no account contributed an advancing clock, which includes a portfolio whose linked accounts are all still unreconciled, so the earlier wording 'no linked accounts yet' claimed more than the wire proves. Task 22 parses enough detail to distinguish the causes.
  ///
  /// In en, this message translates to:
  /// **'no dated source for this total'**
  String get totalNoDatedSource;

  /// DESIGN.md §8.1: fixed valuations sit outside the age basis, and saying so out loud is the condition on which leaving them out is honest rather than hiding them.
  ///
  /// In en, this message translates to:
  /// **'{count, plural, one{includes {count} fixed manual valuation} other{includes {count} fixed manual valuations}}'**
  String includesStaticValuations(int count);

  /// Row label for the connection dimension of staleness — whether the institutions are reporting. Invariant I4 keeps this separate from the copy dimension below; they have different fixes and must never merge into one badge.
  ///
  /// In en, this message translates to:
  /// **'Accounts'**
  String get accountsLabel;

  /// No description provided for @connectionOk.
  ///
  /// In en, this message translates to:
  /// **'all reporting normally'**
  String get connectionOk;

  /// DESIGN.md §9.2 splits the connection axis precisely so WAITING can never read as a demand to re-link. The second clause is the whole point of the state existing.
  ///
  /// In en, this message translates to:
  /// **'some data is behind — nothing for you to do'**
  String get connectionWaiting;

  /// Deliberately does not name the remedy. The producer returns ACTION_NEEDED for three different causes — an unreconciled account, an institution needing re-authentication, and a frozen source clock — whose next actions are reconciliation, reconnection and neither. The earlier wording named reconnection for all three. Task 22 parses enough detail to distinguish them.
  ///
  /// In en, this message translates to:
  /// **'an account needs your attention'**
  String get connectionActionNeeded;

  /// Row label for the copy dimension: how old THIS PHONE's copy of the snapshot is, as opposed to how old the data behind it is.
  ///
  /// In en, this message translates to:
  /// **'This copy'**
  String get thisCopyLabel;

  /// No description provided for @copyFresh.
  ///
  /// In en, this message translates to:
  /// **'published {timestamp}'**
  String copyFresh(String timestamp);

  /// No description provided for @copyStale.
  ///
  /// In en, this message translates to:
  /// **'overdue — nothing new since {timestamp}'**
  String copyStale(String timestamp);

  /// Neither clock is called wrong, because this side cannot tell which one is. DESIGN.md §9.1 rule 1: the two clocks are never merged.
  ///
  /// In en, this message translates to:
  /// **'this device\'s clock disagrees with the server\'s'**
  String get copyUnknown;

  /// Section label above the net-worth curve. The curve shows shape over time and no figures: I4 forbids a widget that renders an amount without its age, and an axis label would be exactly that. The number is in the headline above.
  ///
  /// In en, this message translates to:
  /// **'History'**
  String get historyLabel;

  /// Shown instead of the curve when the series is empty. Says only that nothing has been recorded — it does not promise when something will be, because the app cannot know when the next payload arrives.
  ///
  /// In en, this message translates to:
  /// **'no readings recorded yet'**
  String get historyEmpty;

  /// Shown instead of the curve when the recorded series and the headline total are in different currencies. States the fact and the consequence; it does not name either currency, because the owner cannot act on that and the pair would read as a figure.
  ///
  /// In en, this message translates to:
  /// **'history is in a different currency from the total, so it isn\'t shown'**
  String get historyCurrencyMismatch;

  /// Explains the dashed/hollow treatment for snapshots with is_complete = FALSE (DESIGN.md §10.5). Rendered only when the series actually contains one, so the note never describes something absent from the screen. Deliberately does not name a cause: is_complete is FALSE when anything was carried forward, stale, or unreconciled, and those have different remedies.
  ///
  /// In en, this message translates to:
  /// **'dashed where a reading was incomplete'**
  String get historyIncompleteNote;

  /// The series exists but could not be loaded or parsed. Distinct from historyEmpty on purpose: saying 'no readings recorded yet' over a record that failed to load would be a false claim about the owner's own history.
  ///
  /// In en, this message translates to:
  /// **'couldn\'t read the history'**
  String get historyUnreadable;

  /// Explains the horizontal holes in the line. Needed because a break is otherwise ambiguous — it could read as a value falling away. Rendered only when the series actually has a gap. Joining across one would assert a value that was never stored, which §12 rules out.
  ///
  /// In en, this message translates to:
  /// **'breaks are days with no reading'**
  String get historyGapNote;

  /// The reading on screen reached the app but not its record — a full disk being the ordinary cause. A third state, independent of historyEmpty and historyUnreadable: reading and writing the record fail separately, so the series can render perfectly while every new reading is dropped. It replaces historyEmpty over an empty store (there, 'no readings recorded yet' is true but its 'yet' promises readings that are in fact being lost) and is appended below a series that does render. Not latched — a later launch that records successfully stops showing it.
  ///
  /// In en, this message translates to:
  /// **'this reading couldn\'t be saved, so it won\'t appear in the history'**
  String get historyNotRecorded;
}

class _AppLocalizationsDelegate
    extends LocalizationsDelegate<AppLocalizations> {
  const _AppLocalizationsDelegate();

  @override
  Future<AppLocalizations> load(Locale locale) {
    return SynchronousFuture<AppLocalizations>(lookupAppLocalizations(locale));
  }

  @override
  bool isSupported(Locale locale) =>
      <String>['en'].contains(locale.languageCode);

  @override
  bool shouldReload(_AppLocalizationsDelegate old) => false;
}

AppLocalizations lookupAppLocalizations(Locale locale) {
  // Lookup logic when only language code is specified.
  switch (locale.languageCode) {
    case 'en':
      return AppLocalizationsEn();
  }

  throw FlutterError(
    'AppLocalizations.delegate failed to load unsupported locale "$locale". This is likely '
    'an issue with the localizations generation tool. Please file an issue '
    'on GitHub with a reproducible sample app and the gen-l10n configuration '
    'that was used.',
  );
}
