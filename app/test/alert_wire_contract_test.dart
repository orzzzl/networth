import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/domain/payload_alert.dart';

/// The alert contract, checked against the host's own source rather than against
/// a copy of it.
///
/// **Why this shape.** `AGENTS.md` names `publisher.py::_plaintext` as the one
/// contract the daemon and the phone share, and nothing compiles both sides — the
/// two CI jobs do not even share a toolchain. The same gap on the Kotlin channel
/// (#100) was closed the same way: a test that reads the other language's literals.
/// A hand-written fixture cannot do this job, because the fixture and the parser
/// would be edited together by whoever changes the app and neither would notice
/// the host moving.
///
/// **What it is for, concretely.** §11's kind vocabulary has already grown once
/// *inside* `schema_version` 1 — `SHARE_COUNT_UNCONFIRMED`, task `27`. When it
/// grows again the app needs new copy, and nothing else in either suite would
/// say so: the payload still parses, the screen still renders, and the new kind
/// quietly falls through to [AlertKind.forWire]'s `null` branch.
///
/// So the two mechanisms are deliberate and sit at different layers. This test
/// makes the drift **visible in CI**; the unknown-kind fallback makes it
/// **harmless on the phone**. Either alone is a worse design: a red test with no
/// fallback means the owner sees nothing until someone ships, and a fallback with
/// no test means English host prose is the app's permanent answer.
void main() {
  final alertModel = File('../networth/model/alert.py');
  final publisher = File('../networth/publisher.py');

  /// The `NAME = "WIRE"` pairs inside `class AlertKind(StrEnum)`.
  ///
  /// **A literal scan over a text slice, and only that.** It does not parse
  /// Python: it trusts four-space indentation, one member per line, a
  /// double-quoted value, and `@property` as the end of the member block. A
  /// member written any other way is simply not seen, and this returns a
  /// *smaller* set rather than a wrong one — which is the safe direction here,
  /// because the assertions below compare it against the app's vocabulary and a
  /// missing host kind cannot manufacture agreement. What it cannot do is prove
  /// the class it read is the one `publisher.py` imports.
  Set<String> hostKinds(String source) {
    final start = source.indexOf('class AlertKind(StrEnum):');
    if (start < 0) {
      return const {};
    }
    // The members end at the first `@property` — everything below that is
    // behaviour, and `is_item_scoped`'s body mentions two of the names.
    final end = source.indexOf('@property', start);
    final body = source.substring(start, end < 0 ? source.length : end);
    return RegExp(r'^\s{4}[A-Z_]+ = "([A-Z_]+)"$', multiLine: true)
        .allMatches(body)
        .map((match) => match.group(1)!)
        .toSet();
  }

  /// Every string literal followed by a colon in `_alert`'s text.
  ///
  /// **That is the honest description, and it is weaker than "the keys `_alert`
  /// puts on the wire at any nesting depth", which is what this used to claim.**
  /// The regex sees a flat bag of literals in a slice delimited by the next
  /// top-level `def`. It cannot tell a key in the returned dict from one in a
  /// nested dict, a comprehension, a docstring or a comment; it cannot associate
  /// a key with the object it belongs to; and it says nothing about whether a
  /// branch that emits it is reachable.
  ///
  /// It is still worth having, because the drift it exists to catch is a
  /// *vocabulary* change — a key added to or renamed on the wire — and that does
  /// show up in the bag of literals. Its assertion is an exact set equality
  /// against a written-out set, so a discrepancy in **either** direction goes
  /// red: a stray literal from a comment or a nested dict is a false red, and a
  /// real key the regex cannot match (anything not lower-snake-case, or built
  /// from a variable) is also red. False reds cost a reading; neither direction
  /// is a false pass.
  ///
  /// The gap that *is* a false pass is structural: the same bag of literals
  /// arranged differently — `id` moved out of `subject`, say — compares equal
  /// and this test stays green. Nothing here can see that, and
  /// `payload_alert_test.dart` parsing real fixture shapes is what pins it.
  Set<String> wireKeys(String source) {
    final start = source.indexOf('def _alert(');
    if (start < 0) {
      return const {};
    }
    final end = source.indexOf('\ndef ', start + 1);
    final body = source.substring(start, end < 0 ? source.length : end);
    return RegExp(r'"([a-z_]+)":')
        .allMatches(body)
        .map((match) => match.group(1)!)
        .toSet();
  }

  test('the host source is where this test thinks it is', () {
    // A missing file would make every extractor below return an empty set, and
    // two empty sets compare equal. Checked first and separately so that
    // "the contract matches" can never be a way of saying "nothing was read".
    expect(alertModel.existsSync(), isTrue, reason: alertModel.path);
    expect(publisher.existsSync(), isTrue, reason: publisher.path);
  });

  test('the app knows exactly the kinds the host can send', () {
    final host = hostKinds(alertModel.readAsStringSync());

    expect(host, isNotEmpty);
    expect(
      host,
      AlertKind.values.map((kind) => kind.wire).toSet(),
      reason: 'AlertKind drifted from networth/model/alert.py. A kind added there '
          'needs an ARB message and a branch in alert_list.dart; until it has '
          'them the app falls back to the host sentence, which is not localized.',
    );
  });

  test('the app reads exactly the fields the host sends', () {
    final keys = wireKeys(publisher.readAsStringSync());

    expect(
      keys,
      {'kind', 'subject', 'id', 'message', 'prompt'},
      reason: 'publisher.py::_alert changed shape. A new field is a decision for '
          'the phone, not something to ignore silently.',
    );
  });

  test('the subject vocabulary is the two values the app refuses to grow', () {
    final body = publisher.readAsStringSync();
    final start = body.indexOf('def _alert(');
    final slice = body.substring(start, body.indexOf('\ndef ', start + 1));

    for (final scope in AlertSubjectScope.values) {
      expect(slice, contains('"${scope.wire}"'), reason: scope.wire);
    }
    // The host derives this from a boolean, so a third value would mean the
    // contract broke rather than moved on — which is why the parser refuses one.
    expect(
      RegExp(r'"([A-Z_]+)"').allMatches(slice).map((match) => match.group(1)!).toSet(),
      AlertSubjectScope.values.map((scope) => scope.wire).toSet(),
    );
  });

  test('the extractors can actually fail', () {
    // Two regexes over another language's source is exactly the kind of check
    // that passes because it matched nothing at all, and both assertions above
    // are set comparisons — the failure mode is silent agreement on empty.
    const fakeModel = '''
class AlertKind(StrEnum):
    """Three, for the test."""

    NEEDS_REAUTH = "NEEDS_REAUTH"
    REVOKED = "REVOKED"
    CUSTODIAN_MERGED = "CUSTODIAN_MERGED"

    @property
    def is_item_scoped(self) -> bool:
        return self in (AlertKind.NEEDS_REAUTH, AlertKind.REVOKED)
''';
    expect(hostKinds(fakeModel), {'NEEDS_REAUTH', 'REVOKED', 'CUSTODIAN_MERGED'});
    // The property body names two of them and must not be read as a declaration.
    expect(hostKinds(fakeModel), hasLength(3));
    expect(hostKinds('class Something(Enum):\n    A = "A"\n'), isEmpty);

    const fakePublisher = '''
def _alert(deliverable: DeliverableAlert) -> dict[str, object]:
    return {
        "kind": alert.kind.value,
        "subject": {"kind": "ITEM" if x else "ACCOUNT", "id": alert.subject_id},
        "message": alert.message,
        "prompt": deliverable.prompt,
        "raised_at": alert.created_at,
    }


def _plaintext(read: NetWorthRead) -> bytes:
    return {"total": 1}
''';
    // Including the added field, which is the drift this must not miss — and
    // stopping at the next `def`, so `_plaintext`'s keys are not counted.
    expect(wireKeys(fakePublisher), {'kind', 'subject', 'id', 'message', 'prompt', 'raised_at'});
    expect(wireKeys('def _other(): return {"kind": 1}'), isEmpty);
  });
}
