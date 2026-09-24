import 'package:flutter/foundation.dart';

import 'payload_format_exception.dart';

/// The alert vocabulary `DESIGN.md` §11 closed, spelled as
/// `networth/model/alert.py::AlertKind` spells it on the wire.
///
/// **Declaration order is the host's, and it is also the order the screen
/// renders in** ([summarizeAlerts]). The payload carries no severity field, so a
/// ranking invented on this side would be the phone asserting a judgement the
/// daemon never made. The host's own order is at least a decision someone took
/// once, in the file that defines the vocabulary.
enum AlertKind {
  needsReauth('NEEDS_REAUTH'),
  revoked('REVOKED'),
  frozenData('FROZEN_DATA'),
  pendingReconciliation('PENDING_RECONCILIATION'),
  shareCountUnconfirmed('SHARE_COUNT_UNCONFIRMED');

  const AlertKind(this.wire);

  /// The exact string `publisher.py::_alert` puts in `kind`.
  final String wire;

  /// The kind [wire] names, or `null` when this build does not know it.
  ///
  /// **`null` is an answer, not a failure, and the reason is this field's own
  /// recorded history rather than general caution.** §11 added
  /// `SHARE_COUNT_UNCONFIRMED` as a fifth kind *inside* `schema_version` 1 —
  /// *"This lands inside the first `schema_version`"* — so a host newer than the
  /// app sending a kind the app has no copy for is a thing that has already
  /// happened once here, without the version gate moving.
  ///
  /// The two stricter alternatives both lose the owner information on the only
  /// surface §11 has: refusing the payload would discard the four alerts that
  /// arrived beside the unknown one *and* the total, and dropping the row would
  /// hide an alert while showing everything around it as normal. So an unknown
  /// kind keeps its place in the set and is rendered from the host's own
  /// sentence — see [AlertSummary.hostMessages].
  static AlertKind? forWire(String wire) {
    for (final kind in values) {
      if (kind.wire == wire) {
        return kind;
      }
    }
    return null;
  }
}

/// Whether an alert is about an Item — one connection to an institution — or
/// about a single account.
///
/// **Refused when unknown, unlike [AlertKind], and the asymmetry is the point.**
/// On the host this is not a vocabulary at all: `_alert` derives it from the
/// boolean `AlertKind.is_item_scoped`, so it has exactly two values and cannot
/// grow a third the way the kind list grew a fifth. A third value here would
/// mean the wire contract has broken rather than moved on, and the honest
/// response to that is a refusal.
enum AlertSubjectScope {
  item('ITEM'),
  account('ACCOUNT');

  const AlertSubjectScope(this.wire);

  final String wire;

  static AlertSubjectScope fromWire(String wire, {required String field}) {
    for (final scope in values) {
      if (scope.wire == wire) {
        return scope;
      }
    }
    throw PayloadFormatException('$field is not a known alert subject: "$wire"');
  }
}

/// What one alert is about.
@immutable
final class AlertSubject {
  const AlertSubject({required this.scope, required this.id});

  final AlertSubjectScope scope;

  /// The host's own row id for the Item or account (`Alert.subject_id`).
  ///
  /// **Kept so subjects can be counted, and never rendered.** It is a local
  /// SQLite row id — not a name, not an institution, and not anything the owner
  /// could recognise on a screen. The payload carries no label for it either:
  /// `publisher.py::_account` emits `account_id`, states and figures, and no
  /// human-readable name anywhere, which is the same separation `AGENTS.md`
  /// rule 0 keeps in the repository. So the most a phone can honestly say is
  /// *how many* accounts a kind is open on, which is what [AlertSummary]
  /// carries.
  final int id;

  @override
  bool operator ==(Object other) =>
      other is AlertSubject && other.scope == scope && other.id == id;

  @override
  int get hashCode => Object.hash(scope, id);
}

/// One row of the payload's open alert set (§11), as the host serialized it.
@immutable
final class PayloadAlert {
  const PayloadAlert({
    required this.wireKind,
    required this.kind,
    required this.subject,
    required this.hostMessage,
  });

  /// What the payload called this kind, kept verbatim.
  ///
  /// Retained alongside [kind] because it is the only handle on an alert whose
  /// kind this build does not know: it is what groups such rows together and
  /// what a bug report can quote.
  final String wireKind;

  /// The known kind, or `null` — see [AlertKind.forWire].
  final AlertKind? kind;

  final AlertSubject subject;

  /// The host's own user-facing sentence for this alert.
  ///
  /// Rendered **only** when [kind] is `null`. For a known kind the app has its
  /// own localized copy and uses it, exactly as it already does for
  /// `connection_state` rather than displaying host prose — this app has an ARB
  /// and the payload's `message` is English the daemon hard-codes
  /// (`networth/alerts.py::_MESSAGES`).
  final String hostMessage;

  factory PayloadAlert.fromJson(Map<String, Object?> body, {required int index}) {
    final where = 'alerts[$index]';
    final wireKind = _string(body, 'kind', where: where);
    final subject = body['subject'];
    if (subject is! Map<String, Object?>) {
      throw PayloadFormatException('$where.subject is not a JSON object');
    }
    // **Checked and deliberately not kept.** §11 gives `prompt` one consumer —
    // "the same in-app alerts, when the app evaluates a newly-fetched payload in
    // the background", a *local notification* it describes as best-effort and
    // "never the alert itself". This build has no background evaluation and no
    // notification channel, so a stored `prompt` would be a field that looks
    // like support for a surface that does not exist; `phone_payload.dart` makes
    // exactly that argument about `accounts` and `item_budget`. Validating it
    // without keeping it costs nothing and still turns a change to the field
    // into a red test rather than a surprise for whoever builds that slice.
    final prompt = body['prompt'];
    if (prompt is! bool) {
      throw PayloadFormatException('$where.prompt is not a boolean');
    }
    return PayloadAlert(
      wireKind: wireKind,
      kind: AlertKind.forWire(wireKind),
      subject: AlertSubject(
        scope: AlertSubjectScope.fromWire(
          _string(subject, 'kind', where: '$where.subject'),
          field: '$where.subject.kind',
        ),
        id: _int(subject, 'id', where: '$where.subject'),
      ),
      hostMessage: _string(body, 'message', where: where),
    );
  }

  static String _string(Map<String, Object?> body, String field, {required String where}) {
    final value = body[field];
    if (value is! String) {
      throw PayloadFormatException('$where.$field is not a string');
    }
    return value;
  }

  static int _int(Map<String, Object?> body, String field, {required String where}) {
    final value = body[field];
    // This rejects `true` on its own, because in Dart a `bool` is not an `int`.
    // Worth a line only because the host's validator of the same field has to
    // say so explicitly — in Python `True` *is* an `int`, and
    // `_validate_alert_fields` carries a separate `isinstance(..., bool)` check
    // for exactly that. The two ends agree; they just need different code to.
    if (value is! int) {
      throw PayloadFormatException('$where.$field is not an integer');
    }
    return value;
  }
}

/// One kind's worth of the open set, as a screen can state it.
@immutable
final class AlertSummary {
  const AlertSummary({
    required this.wireKind,
    required this.kind,
    required this.subjectCount,
    required this.hostMessages,
  });

  final String wireKind;

  /// `null` when this build has no copy for [wireKind].
  final AlertKind? kind;

  /// How many **distinct** subjects this kind is open on.
  ///
  /// Distinct rather than a row count: the host keeps at most one open alert per
  /// kind per subject (`AlertEvaluator` looks up `existing_by_kind` before
  /// raising), so a duplicate would be a host defect, and the phone would be
  /// reporting "3 accounts" about two if it counted rows.
  final int subjectCount;

  /// The host's sentences for this kind, de-duplicated, in first-seen order.
  ///
  /// Empty is never correct — an alert always carries a message — but this is
  /// read only when [kind] is `null`, so for the five known kinds it is carried
  /// and unused.
  final List<String> hostMessages;
}

/// The open set grouped for display: one entry per kind, counted by subject.
///
/// **Grouped rather than one row per alert**, because the payload gives the
/// phone nothing to tell two subjects of the same kind apart with (see
/// [AlertSubject.id]) — three re-auth alerts would render as three identical
/// sentences, which reads as a rendering bug rather than as three accounts.
///
/// Order: the five known kinds in [AlertKind]'s own declaration order, then any
/// unknown kinds sorted by their wire string. Unknown ones go last because
/// nothing here can rank them, and they are sorted so that two payloads with the
/// same content cannot draw two different screens.
List<AlertSummary> summarizeAlerts(Iterable<PayloadAlert> alerts) {
  final subjects = <String, Set<AlertSubject>>{};
  final messages = <String, List<String>>{};
  for (final alert in alerts) {
    subjects.putIfAbsent(alert.wireKind, () => <AlertSubject>{}).add(alert.subject);
    final seen = messages.putIfAbsent(alert.wireKind, () => <String>[]);
    if (!seen.contains(alert.hostMessage)) {
      seen.add(alert.hostMessage);
    }
  }

  final known = <String>[for (final kind in AlertKind.values) kind.wire];
  final order = subjects.keys.toList()
    ..sort((a, b) {
      final rankA = known.indexOf(a);
      final rankB = known.indexOf(b);
      if (rankA == rankB) {
        return a.compareTo(b);
      }
      // `indexOf` answers -1 for an unknown kind, which would sort it first.
      return (rankA < 0 ? known.length : rankA).compareTo(rankB < 0 ? known.length : rankB);
    });

  return List.unmodifiable([
    for (final wireKind in order)
      AlertSummary(
        wireKind: wireKind,
        kind: AlertKind.forWire(wireKind),
        subjectCount: subjects[wireKind]!.length,
        hostMessages: List.unmodifiable(messages[wireKind]!),
      ),
  ]);
}
