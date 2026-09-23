import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../domain/clock_anchor.dart';
import '../domain/clock_continuity.dart';
import '../domain/payload_format_exception.dart';
import '../domain/publication_seq.dart';
import 'stored_file.dart';

/// Where the clock anchor lives.
abstract interface class ClockAnchorStore {
  /// The anchor for [pairingId], or why there is none.
  Future<AnchorState> read(String pairingId);

  /// Record that a **newer** publication has arrived, stamped by both clocks.
  ///
  /// Throws [AnchorNotAdvanced] when [anchor] does not advance the stored
  /// anchor's `seq`. That refusal is the guard, not an inconvenience — see
  /// [FileClockAnchorStore.establish].
  Future<void> establish(ClockAnchor anchor);
}

/// Asked to re-anchor on a publication that is not newer than the one the
/// stored anchor already vouches for.
///
/// Unreachable in correct operation: the acceptance path calls [establish] only
/// after I6 has accepted an advance, so a caller that trips this has lost the
/// one fact both checks are reading. Raised rather than returned because the
/// harm is silent — a swallowed refusal leaves a stale anchor claiming to
/// vouch for a copy it never saw.
class AnchorNotAdvanced implements Exception {
  const AnchorNotAdvanced({required this.stored, required this.offered});

  final PublicationSeq stored;
  final PublicationSeq offered;

  @override
  String toString() =>
      'AnchorNotAdvanced: anchor holds seq ${stored.wire}, offered ${offered.wire}';
}

/// A small JSON object in the app's own directory, beside the baseline and the
/// diagnostics and in neither of them.
///
/// A third file needs its own reason, and it is not symmetry. This record's
/// write rule is unlike either neighbour's: the diagnostics are rewritten on
/// **every** attempt including failures, the baseline changes on an accepted
/// advance or a refusal, and this one moves **only** when a newer publication
/// is accepted — never on a refusal, never on a failure, never on a fetch that
/// returned what we already hold. Those are three different lifetimes, and the
/// anchor's is the one that must not be touched by the events that move the
/// others. Sharing a file with the diagnostics would put the record whose whole
/// value is *not moving* inside the record that is rewritten most often.
///
/// Corruption costs nothing that can be exploited: a damaged anchor is
/// [AnchorUnreadable], which is a [ContinuityGap] and therefore `COPY_UNKNOWN` —
/// the phone declines to date its own copy, which is the fail-closed answer. The
/// **absence** of clock evidence is never evidence of a good clock, so unlike
/// the I6 baseline there is no accept-on-trust path here for damage to reach.
class FileClockAnchorStore implements ClockAnchorStore {
  FileClockAnchorStore({required this.open});

  /// The production wiring: `<app documents>/clock_anchor.json`, the same
  /// app-private directory and the same reasoning as [FileHistoryStore].
  factory FileClockAnchorStore.appPrivate() => FileClockAnchorStore(
        open: () async => File(
          '${(await getApplicationDocumentsDirectory()).path}/$fileName',
        ),
      );

  static const String fileName = 'clock_anchor.json';

  /// Where the file is. Injected so a test uses a temporary directory and so the
  /// production path is named in exactly one place.
  final Future<File> Function() open;

  @override
  Future<AnchorState> read(String pairingId) async {
    final String bytes;
    switch (await readStoredFile(open)) {
      case StoredBytes(bytes: final read):
        bytes = read;
      case StoredAbsent():
        return const AnchorAbsent();
      case StoredUnreadable(reason: final reason):
        return AnchorUnreadable('clock anchor could not be read: $reason');
    }
    try {
      return AnchorHeld(_parse(bytes, pairingId: pairingId));
    } on _ForeignPairing {
      // An anchor taken under a previous pairing. Absent, not damaged — and
      // note this costs nothing, because absent and unreadable both end in
      // `COPY_UNKNOWN` here. The two cases are kept apart for what they say to
      // the owner, not for what they let through.
      return const AnchorAbsent();
    } on PayloadFormatException catch (error) {
      return AnchorUnreadable(error.message);
    }
  }

  @override
  Future<void> establish(ClockAnchor anchor) async {
    // **The previous anchor is checked before the new one replaces it**, and
    // this ordering is the requirement rather than an implementation detail.
    //
    // An anchor says when the copy this phone holds arrived. A fetch that
    // returns the publication we already hold changes nothing about that
    // instant, so moving the anchor to *now* would grant trust over an interval
    // shorter than the copy's real age — and a copy published nine days ago,
    // re-fetched unchanged from a host whose publisher has stopped, would render
    // fresh. That is not a hypothetical shape: it is the counterexample that
    // killed this task's previous two arguments, and it is the exact case
    // `HOST_NOT_PUBLISHING` exists to report, so the failure lands where the
    // feature is aimed.
    //
    // The caller has already made this comparison — I6 accepts a payload only
    // when its `seq` advances — which is why it is repeated here rather than
    // relied upon. A guard that lives only in the caller is a guard the caller
    // can forget, and this one is invisible when it is missing: the wrong
    // anchor produces a confident, wrong verdict rather than an error.
    final AnchorTrust trust;
    switch (await read(anchor.pairingId)) {
      case AnchorHeld(anchor: final stored):
        if (!(anchor.seq > stored.seq)) {
          throw AnchorNotAdvanced(stored: stored.seq, offered: anchor.seq);
        }
        trust = _inherited(stored, anchor);
      case AnchorAbsent():
        // First-anchor establishment, and **it starts unproven**. There is no
        // previous instant to preserve — but that is an argument for *having no
        // evidence*, not for minting some. The record this writes is a pair of
        // readings taken a moment apart, which agree with each other no matter
        // how wrong the wall clock is. This is also the recovery path, after a
        // lost or foreign anchor, and it starts unproven for the same reason.
        trust = _offered(anchor);
      case AnchorUnreadable():
        // Damage must not block recovery. Refusing here would leave a phone
        // whose anchor file was corrupted permanently unable to date any copy,
        // and the record being replaced is one nothing can read anyway. What it
        // must not do is come back *trusted*: the damaged record may have been
        // the one holding the evidence of a broken clock, so recovery restores
        // the ability to anchor and not the right to be believed.
        trust = _offered(anchor);
    }
    final file = await open();
    final bytes = jsonEncode(_json(anchor, trust));
    // Writability and readability are one predicate, not two that can drift —
    // `23a`'s precedent, and the same standing caveat as the diagnostics store:
    // every invariant [_parse] checks is already held by [ClockAnchor]'s
    // constructor, so this cannot throw today. It defends against a field added
    // to one side and not the other, which is otherwise discovered by a phone
    // that wrote a record and could not read it back.
    _parse(bytes, pairingId: anchor.pairingId);
    await file.parent.create(recursive: true);
    final temporary = File('${file.path}.writing');
    await temporary.writeAsString(bytes, flush: true);
    await temporary.rename(file.path);
  }

  /// What the caller brought, which today is always [AnchorTrust.unproven]
  /// because nothing in this app can construct the other value.
  ///
  /// Named rather than inlined so the three call sites read as one rule: trust
  /// is only ever *carried in* or *inherited*, and is never a consequence of
  /// having written a record.
  AnchorTrust _offered(ClockAnchor anchor) => anchor.trust;

  /// Trust after an accepted advance: **the previous anchor's, and only if the
  /// interval since it survives the same test the predicate applies.**
  ///
  /// This is the fix for the hole the `seq` guard did not close. That guard
  /// stops the anchor being *re-taken on a copy we already hold*; an advance is
  /// a different event and used to re-stamp both readings unconditionally. The
  /// nine-day case, reproduced against the real store: a phone rolled back nine
  /// days correctly reports `COPY_UNKNOWN`, then fetches a `seq` the host
  /// published before it stopped, and the fresh pair of readings — taken a
  /// moment apart and therefore in perfect agreement — renders that
  /// nine-day-old publication `COPY_FRESH`. The measurement was not contradicted
  /// by anything; it was overwritten.
  ///
  /// So the interval from the stored anchor to this one is evaluated with the
  /// **same** [ContinuityHeld] the reader uses — same `drift`, same
  /// [ContinuityHeld.tolerance] — rather than a second copy of the rule that can
  /// drift away from it. A discontinuity, a backwards counter or a drift past
  /// tolerance all land in the same place: whatever the old anchor was worth,
  /// this one is worth nothing.
  AnchorTrust _inherited(ClockAnchor stored, ClockAnchor offered) {
    if (_offered(offered) == AnchorTrust.corroborated) {
      // Fresh proof outranks history: a caller that can corroborate *this*
      // stamp does not need the old one to have been sound.
      return AnchorTrust.corroborated;
    }
    if (stored.trust != AnchorTrust.corroborated) {
      return AnchorTrust.unproven;
    }
    if (stored.reading.runId != offered.reading.runId) {
      return AnchorTrust.unproven;
    }
    final monotonic = offered.reading.elapsed - stored.reading.elapsed;
    if (monotonic.isNegative) {
      // Same run id, smaller reading: one of the two is wrong and there is no
      // way to tell which, so the run identity has stopped meaning anything.
      return AnchorTrust.unproven;
    }
    final continuity = AnchoredClockEvidence(
      anchoredAt: stored.anchoredAt,
      monotonicElapsed: monotonic,
    ).at(offered.anchoredAt);
    return continuity is ContinuityHeld && continuity.isTrustworthy
        ? AnchorTrust.corroborated
        : AnchorTrust.unproven;
  }

  Map<String, Object?> _json(ClockAnchor anchor, AnchorTrust trust) => <String, Object?>{
        'pairing_id': anchor.pairingId,
        'anchored_at': anchor.anchoredAt.toUtc().toIso8601String(),
        'run_id': anchor.reading.runId,
        'monotonic_elapsed_ms': anchor.reading.elapsed.inMilliseconds,
        // The host's own bytes, not this app's rendering of a parsed number.
        'seq': anchor.seq.wire,
        'trust': _trustWire[trust],
      };

  /// The on-disk spelling, written out rather than taken from `enum.name`.
  ///
  /// A rename in Dart would silently change the format and every previously
  /// written record would read as damaged — on the far side of an app update,
  /// where nothing in this repo is watching. The same reason the keys above are
  /// literals and are asserted as a format by the tests.
  static const Map<AnchorTrust, String> _trustWire = <AnchorTrust, String>{
    AnchorTrust.corroborated: 'corroborated',
    AnchorTrust.unproven: 'unproven',
  };

  ClockAnchor _parse(String bytes, {required String pairingId}) {
    final Object? decoded;
    try {
      decoded = jsonDecode(bytes);
    } on FormatException catch (error) {
      throw PayloadFormatException('stored clock anchor is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      throw const PayloadFormatException('stored clock anchor is not a JSON object');
    }
    // Unlike the two checks below, the emptiness half of this one is *not*
    // shadowed by [ClockAnchor]'s constructor, because it is read before the
    // record ever reaches it: an empty pairing compares unequal to every real
    // one, so without this it would be scoped away as *someone else's* anchor
    // and reported as absence. A damaged record spelling itself as a clean
    // first run is the one thing this store's three cases exist to stop.
    final storedPairing = decoded['pairing_id'];
    if (storedPairing is! String || storedPairing.isEmpty) {
      throw const PayloadFormatException('stored clock anchor has no pairing_id');
    }
    // Checked before anything else, so a record belonging to a previous pairing
    // is scoped away rather than refused for a field this pairing would never
    // have consulted.
    if (storedPairing != pairingId) {
      throw const _ForeignPairing();
    }
    final anchoredAt = _instant(decoded['anchored_at']);
    // These two are type checks and nothing more: an empty run id and a
    // negative elapsed time are [ClockAnchor]'s invariants, enforced in its
    // constructor a few lines below, and repeating them here would put the same
    // rule in two places where only one of them can be pinned. A shadowed guard
    // reads as protection and is untestable, which is the shape this project
    // keeps finding in its own green suites.
    final runId = decoded['run_id'];
    if (runId is! String) {
      throw const PayloadFormatException('stored clock anchor has no run_id');
    }
    final elapsedMs = decoded['monotonic_elapsed_ms'];
    if (elapsedMs is! int) {
      throw const PayloadFormatException(
        'stored clock anchor monotonic_elapsed_ms is not an integer',
      );
    }
    final storedSeq = decoded['seq'];
    if (storedSeq is! String) {
      throw const PayloadFormatException('stored clock anchor seq is not a string');
    }
    // **A record that does not say is damaged, never `unproven`.** Defaulting a
    // missing field to the safe value reads as prudence and is the wrong
    // reflex: `unproven` is a *decision this store made*, and a record that
    // cannot state one is a record that cannot be read. Unreadable is already
    // `COPY_UNKNOWN`, so nothing is lost by saying so — and a record written by
    // a build that spelled this key differently is exactly the case the format
    // literals exist to catch rather than paper over.
    final storedTrust = decoded['trust'];
    final trust = _trustWire.entries
        .where((entry) => entry.value == storedTrust)
        .map((entry) => entry.key)
        .firstOrNull;
    if (trust == null) {
      throw PayloadFormatException('stored clock anchor trust is not recognised: $storedTrust');
    }
    return ClockAnchor(
      trust: trust,
      pairingId: storedPairing,
      anchoredAt: anchoredAt,
      reading: MonotonicReading(
        runId: runId,
        elapsed: Duration(milliseconds: elapsedMs),
      ),
      // The same parser the wire goes through, so a `seq` shape the payload
      // layer refuses is refused here on the same commit.
      seq: PublicationSeq.parse(storedSeq, field: 'stored clock anchor seq'),
    );
  }

  /// The stored anchor instant.
  ///
  /// **An instant with no timezone designator is refused rather than read as
  /// local time**, the same guard the diagnostics store carries and for a
  /// sharper reason here: `anchoredAt` is subtracted from `device_now` to get
  /// the wall-clock side of the drift comparison, and a bare local-time reading
  /// on this owner's phone would land seven or eight hours off — orders of
  /// magnitude past [ContinuityHeld.tolerance], so every verdict would be
  /// `COPY_UNKNOWN` for a reason that has nothing to do with the clock.
  DateTime _instant(Object? stored) {
    if (stored is! String) {
      throw const PayloadFormatException('stored clock anchor has no anchored_at');
    }
    final parsed = DateTime.tryParse(stored);
    if (parsed == null) {
      throw PayloadFormatException('stored clock anchor anchored_at is not ISO-8601: $stored');
    }
    if (!parsed.isUtc) {
      throw PayloadFormatException('stored clock anchor anchored_at carries no timezone: $stored');
    }
    return parsed;
  }
}

/// A stored record for a different pairing. Internal: it is a scoping outcome,
/// not a format error, and [AnchorState] is how that reaches a caller.
class _ForeignPairing implements Exception {
  const _ForeignPairing();
}
