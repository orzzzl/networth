import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:intl/date_symbol_data_local.dart';
import 'package:networth_app/l10n/generated/app_localizations_en.dart';
import 'package:networth_app/src/domain/copy_freshness.dart';
import 'package:networth_app/src/domain/fetch_diagnostics.dart';
import 'package:networth_app/src/ui/copy_text.dart';

/// Every sentence §9.1's verdict can produce, pinned without a widget.
///
/// **Ten of these are unreachable from `SnapshotView` in this build** — it
/// performs no fetches, so its diagnostics are `DiagnosticsAbsent` and the
/// predicate can only reach fresh, the three clock disagreements and
/// `NeverFetched`. The rest become reachable when this task's transport starts
/// writing records, and until then a widget test cannot see them at all.
///
/// That gap is how the sentence this change deletes survived: the reason was
/// computed correctly and rendered by a branch nothing looked at. Pinning the
/// mapping directly is the fix, and it is why `copyDetailText`/`copyReasonText`
/// are public.
void main() {
  // These sentences embed instants, and the month name comes from the locale's
  // own date symbols rather than a Dart constant list. A widget test gets that
  // for free from the localizations delegates; a unit test has to ask.
  setUpAll(() => initializeDateFormatting('en'));

  final l10n = AppLocalizationsEn();

  // Instants are chosen distinct so a test cannot pass by printing the wrong one.
  final published = DateTime.utc(2026, 9, 15, 4);
  final confirmed = DateTime.utc(2026, 9, 18, 6, 30);

  String? reason(CopyState copy) => copyReasonText(l10n, copy);
  String detail(CopyState copy) => copyDetailText(l10n, copy, published);

  CopyState cannotCheck(CannotCheckCause cause, {DateTime? since}) =>
      CopyStale(CannotCheck(cause: cause, since: since));

  /// Every verdict the predicate can hand this file, built once.
  final everyCopyState = <CopyState>[
    const CopyFresh(),
    for (final disagreement in ClockDisagreement.values) CopyUnknown(disagreement),
    CopyStale(HostNotPublishing(lastPublishedAt: published, confirmedAt: confirmed)),
    CopyStale(HostServingNothing(lastPublishedAt: published, reachedAt: confirmed)),
    for (final cause in <CannotCheckCause>[
      const NeverFetched(),
      const NotCheckedSinceDue(),
      const ServedPayloadNotHeld(),
      const RecordsUnusable('anchor file is not JSON'),
      for (final c in FetchFailureClass.values) FetchFailed(c),
    ])
      cannotCheck(cause),
  ];

  test("the count in the file's own doc comment is the count there is", () {
    // **The doc comment said twelve and eight, and both were wrong before this
    // test existed** — `HostServingNothing` arrived with the `404`'s own state
    // and nothing recounted. That is the same defect this task keeps finding in
    // its own prose: a number stated in a sentence, load-bearing for a reader
    // deciding what a widget test can cover, and checked by nobody. Adding a
    // fifth `FetchFailureClass` is what forced the recount and exposed it.
    //
    // So the sentence is pinned to the measurement rather than to a literal:
    // the only way to keep this green is to recount and rewrite the comment.
    const words = <int, String>{
      8: 'eight',
      9: 'nine',
      10: 'ten',
      11: 'eleven',
      12: 'twelve',
      13: 'thirteen',
      14: 'fourteen',
      15: 'fifteen',
      16: 'sixteen',
      17: 'seventeen',
    };
    // `reason` is `null` for a fresh copy — no second line at all, which is not
    // a sentence and must not be counted as one.
    final sentences = everyCopyState.map(reason).whereType<String>().toSet();

    // The four a widget test in this build can actually drive: the three clock
    // disagreements and `NeverFetched`.
    const reachableFromTheWidget = 4;
    // A count outside the table would otherwise interpolate `null` into both
    // matchers and fail as "the comment is wrong" — which it would not be.
    expect(words.keys, contains(sentences.length));
    expect(words.keys, contains(sentences.length - reachableFromTheWidget));

    final source = File('lib/src/ui/copy_text.dart').readAsStringSync();
    expect(
      source,
      contains('There are ${words[sentences.length]} distinct sentences here'),
      reason: 'copy_text.dart states a total; there are ${sentences.length}',
    );
    expect(
      source,
      contains('The other\n/// ${words[sentences.length - reachableFromTheWidget]}'),
      reason: 'copy_text.dart states how many are out of the widget path; '
          'there are ${sentences.length - reachableFromTheWidget}',
    );
  });

  group('the claim on the first line names no cause', () {
    test('a fresh copy says when it was published', () {
      expect(detail(const CopyFresh()), 'published Sep 15, 2026, 04:00 UTC');
    });

    test('a stale copy says how old it is and blames nobody', () {
      final line = detail(cannotCheck(const NeverFetched()));
      expect(line, 'showing a copy from Sep 15, 2026, 04:00 UTC');
      // The regression, stated as the string that must never come back. Through
      // task 22 this line was `overdue — nothing new since <t>` for EVERY stale
      // copy, which asserts §9.1's HOST_NOT_PUBLISHING on the strength of the
      // indicator alone.
      expect(line, isNot(contains('nothing new')));
      expect(line, isNot(contains('overdue')));
    });

    test('an undatable copy says "dated", never "from"', () {
      // "from" quietly claims the age the reason line goes on to disclaim.
      final line = detail(const CopyUnknown(ClockDisagreement.clockContinuityUnknown));
      expect(line, 'showing a copy dated Sep 15, 2026, 04:00 UTC');
      expect(line, isNot(contains('from')));
    });
  });

  group('only HOST_NOT_PUBLISHING may say anything about the server', () {
    test('and it does, with the instant it was last confirmed', () {
      expect(
        reason(CopyStale(HostNotPublishing(lastPublishedAt: published, confirmedAt: confirmed))),
        'nothing newer has been published — your server last confirmed this copy '
        'Sep 18, 2026, 06:30 UTC',
      );
    });

    test('it carries its own instant, so it never takes the last-checked suffix', () {
      // Otherwise the same instant is stated twice in one sentence.
      final line = reason(
        CopyStale(HostNotPublishing(lastPublishedAt: published, confirmedAt: confirmed)),
      )!;
      expect(line, isNot(contains('last checked')));
    });

    test('a host with nothing to serve says that, and not the other two', () {
      final line = reason(
        CopyStale(HostServingNothing(lastPublishedAt: published, reachedAt: confirmed)),
      )!;

      expect(line, 'your server answered Sep 18, 2026, 06:30 UTC and has no snapshot to give');
      // Not `HOST_NOT_PUBLISHING`: that sentence says the server *confirmed*
      // this copy, and a 404 disowns it.
      expect(line, isNot(contains('confirmed')));
      // Not a `CANNOT_CHECK`: the check worked.
      expect(line, isNot(contains("couldn't check")));
      // And its instant is inside the sentence, so no suffix restates it.
      expect(line, isNot(contains('last checked')));
    });

    test('and it names neither of the two causes it cannot tell apart', () {
      // `serve.py` answers 404 both for a revoked pairing and for a host that
      // has published nothing, with different remedies. Task 21's review
      // settled that copy states what the state guarantees, never one of the
      // ways to reach it — the named cause reads as the only one.
      final line = reason(
        CopyStale(HostServingNothing(lastPublishedAt: published, reachedAt: confirmed)),
      )!;

      expect(line, isNot(contains('pair')));
      expect(line, isNot(contains('revoke')));
      expect(line, isNot(contains('sync')));
    });

    test('no CANNOT_CHECK cause claims the host published nothing', () {
      // The whole point of §9.1's split: CANNOT_CHECK means the phone did not
      // reach the host, so a sentence about what the host did has nothing behind
      // it. Every cause is enumerated here rather than spot-checked, so a cause
      // added later fails this until somebody has read it.
      final causes = <CannotCheckCause>[
        const NeverFetched(),
        const NotCheckedSinceDue(),
        const ServedPayloadNotHeld(),
        const RecordsUnusable('anchor file is not JSON'),
        for (final c in FetchFailureClass.values) FetchFailed(c),
      ];
      expect(causes, hasLength(9));
      for (final cause in causes) {
        final line = reason(cannotCheck(cause, since: confirmed))!;
        expect(line, isNot(contains('has been published')), reason: '$cause');
        expect(line, isNot(contains('nothing new')), reason: '$cause');
      }
    });
  });

  group('each inability is its own sentence, because each is its own answer', () {
    test('never fetched asks for nothing', () {
      expect(reason(cannotCheck(const NeverFetched())), "this device hasn't checked yet");
    });

    test('offline and host-unreachable are not the same news', () {
      // §9.1: these two "sit at opposite ends of how much the owner should
      // care", which is the entire reason FetchFailureClass exists. Rendering
      // them alike would throw that away at the last step.
      final offline = reason(cannotCheck(const FetchFailed(FetchFailureClass.offline)))!;
      final unreachable =
          reason(cannotCheck(const FetchFailed(FetchFailureClass.hostUnreachable)))!;
      expect(offline, 'no network here, so it couldn\'t check');
      expect(unreachable, "the network is fine, but your server didn't answer");
      expect(offline, isNot(unreachable));
    });

    test('a rejected credential is the only one that names a remedy', () {
      // It is the one class whose fix is re-pairing. The others must not send
      // the owner anywhere.
      expect(
        reason(cannotCheck(const FetchFailed(FetchFailureClass.credentialRejected))),
        'your server refused this device — it needs pairing again',
      );
      // Derived from the enum rather than listed, and the fifth class is why:
      // written out by hand, the three names here stayed green while a class
      // that had never been read against this rule was added beside them. The
      // property is "every class but one", so it is spelled that way.
      for (final other
          in FetchFailureClass.values.where((c) => c != FetchFailureClass.credentialRejected)) {
        expect(
          reason(cannotCheck(FetchFailed(other)))!,
          isNot(contains('pairing')),
          reason: '$other',
        );
      }
    });

    test('an unusable answer says so without naming TLS, a status or a parser', () {
      final line = reason(cannotCheck(const FetchFailed(FetchFailureClass.transportError)))!;
      expect(line, "your server answered with something this app couldn't use");
      for (final jargon in ['TLS', '500', 'JSON', 'parse']) {
        expect(line, isNot(contains(jargon)));
      }
    });

    test('an unnamed fault claims neither the network nor the server', () {
      // The class exists because the transport's `unclassified` fault could not
      // be placed among the other four without asserting something this phone
      // has no evidence for. So the sentence is checked for what it must NOT
      // say: the two halves of the path, and the remedy.
      final line = reason(cannotCheck(const FetchFailed(FetchFailureClass.unknownFailure)))!;
      expect(line, "it couldn't check, and this device can't say why");
      expect(line, isNot(contains('network')));
      expect(line, isNot(contains('server')));
      expect(line, isNot(contains('pairing')));

      // And it is not any of the other four, which is the whole point of adding
      // it rather than reusing the nearest one.
      for (final other
          in FetchFailureClass.values.where((c) => c != FetchFailureClass.unknownFailure)) {
        expect(reason(cannotCheck(FetchFailed(other))), isNot(line), reason: '$other');
      }
    });

    test('not-yet-due blames nobody at all', () {
      expect(
        reason(cannotCheck(const NotCheckedSinceDue())),
        "this device hasn't needed to check again yet",
      );
    });

    test('unusable records are not "never fetched", and leak no bytes', () {
      // Saying "hasn't checked yet" to a phone that fetched for a month and lost
      // its notes is a claim with nothing behind it — the reason the three
      // store states are `sealed`.
      final line = reason(cannotCheck(const RecordsUnusable('/data/x.json: not JSON')))!;
      expect(line, "this device can't read its own notes, so it can't tell why");
      expect(line, isNot(contains('/data')));
      expect(line, isNot(contains('JSON')));
    });

    test('a served copy we do not hold accuses the host of nothing', () {
      // On this design that divergence has exactly one cause: a fetch this phone
      // REFUSED under I6.
      expect(
        reason(cannotCheck(const ServedPayloadNotHeld())),
        "your server's latest copy isn't the one shown here",
      );
    });
  });

  group('§9.1 rule 1 keeps the three clock states apart', () {
    test('each disagreement is a different sentence', () {
      final lines = {
        for (final d in ClockDisagreement.values) d: reason(CopyUnknown(d))!,
      };
      expect(lines.values.toSet(), hasLength(ClockDisagreement.values.length));
    });

    test('a copy from the future blames neither clock', () {
      final line = reason(const CopyUnknown(ClockDisagreement.payloadFromTheFuture))!;
      expect(
        line,
        "the copy is dated ahead of this device's clock, so its age can't be worked out",
      );
      // This side cannot tell which clock is wrong, so it must call neither one
      // wrong.
      expect(line, isNot(contains('wrong')));
    });

    test('a clock caught moving backwards is distinct from one merely unconfirmed', () {
      // A detected fault and an absence of evidence. Telling the owner they are
      // the same thing is the confident answer §9.1 rule 1 forbids — and the
      // unconfirmed one is what the shipped build renders today, since HomePage
      // fails closed until this task's monotonic source lands.
      final caught = reason(const CopyUnknown(ClockDisagreement.deviceClockMovedBackwards))!;
      final unproven = reason(const CopyUnknown(ClockDisagreement.clockContinuityUnknown))!;
      expect(caught, "this device's clock has moved backwards, so its age can't be worked out");
      expect(unproven, "this device can't confirm its own clock, so its age can't be worked out");
      expect(caught, isNot(unproven));
    });
  });

  group('the last-checked suffix appears exactly when there is an instant', () {
    test('it is appended when a fetch has ever succeeded', () {
      expect(
        reason(cannotCheck(const FetchFailed(FetchFailureClass.offline), since: confirmed)),
        "no network here, so it couldn't check — last checked Sep 18, 2026, 06:30 UTC",
      );
    });

    test('and nothing is substituted when none has', () {
      // `since` is null on a phone that has attempted under this pairing and
      // never succeeded. An invented instant would be the same lie the nullable
      // exists to refuse, one layer up — and "never" is not an instant either.
      final line = reason(cannotCheck(const FetchFailed(FetchFailureClass.offline)))!;
      expect(line, "no network here, so it couldn't check");
      expect(line, isNot(contains('last checked')));
      expect(line, isNot(contains('never')));
    });
  });

  test('a fresh copy has no second line', () {
    // A row that always carries a cause teaches the eye to skip the cause.
    expect(reason(const CopyFresh()), isNull);
  });
}
