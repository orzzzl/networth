import '../debug_log.dart';
import '../domain/fetch_diagnostics.dart';
import '../domain/held_copy.dart';
import '../domain/payload_envelope.dart';
import '../domain/payload_format_exception.dart';
import '../domain/phone_payload.dart';
import '../domain/publication_seq.dart';
import '../domain/seq_baseline.dart';
import 'fetch_diagnostics_store.dart';
import 'held_copy_store.dart';
import 'seq_baseline_store.dart';
import 'snapshot_reader.dart';
import 'snapshot_transport.dart';

/// Which of §9.1's classes a transport fault is, **written once in one named
/// place**.
///
/// `SnapshotTransportFault` says so itself: it exists next to the error classes
/// instead of being them because *"the mapping onto it is lossy, and it is
/// written once, in one named place, where it can be argued with and tested."*
/// This is that place. Three of the nine are worth the argument:
///
/// - **[SnapshotTransportFault.timedOut] is [FetchFailureClass.hostUnreachable],
///   not [FetchFailureClass.unknownFailure]**, and this is the mapping the whole
///   row turns on. §9.1 exists so that *"the shape a lost VPS takes on the only
///   screen that can report it"* is distinguishable from an ordinary phone
///   problem — and on a tailnet a lost VPS **is** a timeout: MagicDNS keeps
///   resolving an offline node's name, so the connect goes out and nothing comes
///   back. Answering that with *"this device can't say why"* would mute the one
///   fault task `22` says the app exists to surface. **What it over-claims, so
///   that the limit is stated rather than hidden:** the deadline covers
///   resolution too, so a resolver that never answers for ten seconds also lands
///   here and is reported as a host that did not answer. That is the safe
///   direction — it over-reports a fault that self-corrects rather than
///   under-reporting one that does not.
/// - **[SnapshotTransportFault.notThisRoute] is
///   [FetchFailureClass.unknownFailure]**, and mapping it to
///   [FetchFailureClass.transportError] would undo the fault's own reason for
///   existing one layer up. Its doc names the case it is really for — a captive
///   portal, *"a normal thing for a phone to be behind"*, which answers any
///   request with `200` and an HTML page — and says the check exists so that
///   *"the app's most likely encounter with a coffee shop"* does not end up
///   *"pointing at the publisher for something the network did."*
///   [FetchFailureClass.transportError] renders as *"your server answered with
///   something this app couldn't use"*, which is that same misattribution
///   arriving through the copy. The phone genuinely cannot tell a portal from
///   something else on that port, and *"cannot say where"* is the one sentence
///   true of both.
/// - **[SnapshotTransportFault.connectionRefused] is
///   [FetchFailureClass.hostUnreachable]** even though the address answered: a
///   `RST` proves the route works and the port is dead, which is *"the network
///   is fine, but your server didn't answer"* exactly. It is not
///   [FetchFailureClass.transportError], because nothing answered — the copy
///   there would credit the host with a reply it never sent.
FetchFailureClass classifyTransportFault(SnapshotTransportFault fault) => switch (fault) {
      // No path at all: MagicDNS is not answering, so Tailscale is down on this
      // device, or the kernel refused before a packet left.
      SnapshotTransportFault.nameNotResolved => FetchFailureClass.offline,
      SnapshotTransportFault.networkUnreachable => FetchFailureClass.offline,
      SnapshotTransportFault.timedOut => FetchFailureClass.hostUnreachable,
      SnapshotTransportFault.connectionRefused => FetchFailureClass.hostUnreachable,
      SnapshotTransportFault.hostUnreachable => FetchFailureClass.hostUnreachable,
      // The exchange began and did not finish, or a `200` overran the cap: the
      // host answered and its answer was unusable.
      SnapshotTransportFault.connectionLost => FetchFailureClass.transportError,
      SnapshotTransportFault.responseTooLarge => FetchFailureClass.transportError,
      SnapshotTransportFault.notThisRoute => FetchFailureClass.unknownFailure,
      SnapshotTransportFault.unclassified => FetchFailureClass.unknownFailure,
    };

/// Which class a refused body is — the envelope layer's half of the same table.
///
/// **[FetchFailureClass.credentialRejected] is reachable only from here**, and
/// that is a property of the transport rather than an accident of this switch.
/// `GET /snapshot` carries no bearer token and validates no certificate:
/// reachability is Tailscale's answer and the payload key is the read
/// credential (§6.2/§6.3). So no status code on that route can mean *"refused
/// this device"* — a `401` from something on the tailnet would be a status this
/// route does not define, not a rejection of a credential the phone never sent.
/// The only evidence the phone can have that **its pairing** is what is wrong is
/// an envelope it could not open, which is these two values.
FetchFailureClass classifyRejection(SnapshotRejection reason) => switch (reason) {
      // The tag did not verify, or the envelope names a pairing this phone is
      // not in: both say the key relationship is what broke, and both are
      // answered by re-pairing.
      SnapshotRejection.notAuthentic => FetchFailureClass.credentialRejected,
      SnapshotRejection.otherPairing => FetchFailureClass.credentialRejected,
      // Bytes arrived over `application/json` and are not a document this build
      // can use. `notAnEnvelope` is decided before the tag check and so says
      // nothing about who sent it — but it got a `200` with this route's content
      // type from the paired host's own name, which is as much provenance as
      // [FetchFailureClass.transportError] claims.
      SnapshotRejection.notAnEnvelope => FetchFailureClass.transportError,
      SnapshotRejection.headerDisagreement => FetchFailureClass.transportError,
      SnapshotRejection.payloadNotReadable => FetchFailureClass.transportError,
    };

/// What one refresh did to the phone's records — **never what the screen should
/// show**.
///
/// The screen reads the held copy itself, because it has to: it renders before
/// any fetch returns, and on a launch with no network it renders without one
/// happening at all. Handing the copy back from here as well would give the
/// screen two sources for the same fact, free to disagree in exactly the window
/// where this file writes to one of them. So an accepted payload travels back
/// for the caller that wants to skip a re-read, and every other case reports
/// what was recorded rather than what is on disk.
sealed class SnapshotRefreshOutcome {
  const SnapshotRefreshOutcome();
}

/// A payload arrived, I6 accepted it, and it is the held copy now.
final class RefreshAccepted extends SnapshotRefreshOutcome {
  const RefreshAccepted(this.payload);

  /// The payload now on disk. The *same parse* the store holds the text of, so
  /// a caller rendering this and a caller re-reading the store see one value.
  final PhonePayload payload;
}

/// The attempt is recorded and the copy the phone already had is untouched.
final class RefreshKeptHeldCopy extends SnapshotRefreshOutcome {
  const RefreshKeptHeldCopy(this.refusal);

  final RefreshRefusal refusal;
}

/// Nothing was attempted and **nothing was recorded**.
///
/// Every record this file writes is scoped to a `pairing_id` (§9.1, §9.3), so a
/// phone with no readable pairing has nowhere to file an attempt. Recording one
/// anyway would mean inventing a scope, and `FetchDiagnostics` spells out what
/// facts filed under the wrong pairing cost.
final class RefreshNotAttempted extends SnapshotRefreshOutcome {
  const RefreshNotAttempted(this.reason);

  final NoPairing reason;
}

/// Why the held copy stood. Each value is a different thing to have happened,
/// and the detail behind it is in the stores this refresh just wrote.
enum RefreshRefusal {
  /// No payload arrived. The attempt is recorded with its [FetchFailureClass].
  attemptFailed,

  /// The host answered and has no publication at all — `serve.py`'s `404`,
  /// recorded as [FetchDiagnostics.foundNoPublication] so §9.1 can reach
  /// `HOST_SERVING_NOTHING` instead of blaming the network.
  hostHasNoPublication,

  /// I6: the payload's `seq` is below the baseline. A persistent downgrade
  /// warning is stored beside the baseline, which is left where it was.
  downgradeRefused,

  /// I6: the envelope named a pairing this phone is not in — §9.3's fourth row,
  /// *"the phone is talking to something that is not its daemon"*.
  foreignPairing,

  /// A baseline is stored for this pairing and **cannot be read**, so I6 cannot
  /// be evaluated. Refused rather than accepted on trust: §9.3's accept-on-trust
  /// case is *no* baseline, and treating a damaged one as absent would make
  /// corruption the bypass.
  baselineUnreadable,

  /// I6 accepted it and **the copy could not be written**. Nothing else was
  /// advanced, because the copy is written first, so every record still
  /// describes the copy that is on disk.
  ///
  /// A *baseline* write that fails after the copy landed is deliberately not
  /// this: the payload is on disk and on screen, and the replay floor is read
  /// from it — see [SnapshotRefresher].
  notStored,
}

/// Why no attempt was made. The two are not one state: see
/// [SnapshotReadPairingUnreadable] for what collapsing them would suggest to an
/// owner whose device is fine.
enum NoPairing {
  notPaired,
  pairingUnreadable,
}

/// Joins one `GET /snapshot` to the three records §9 makes the phone keep.
///
/// `PairedSnapshotReader` deliberately stops at *"here is what the attempt
/// produced"* and `snapshot_source.dart`'s `SnapshotSource` is
/// `Future<PhonePayload> load()`, which has no channel for the outcomes §9.1
/// requires the phone to persist — so neither of them could be the thing that
/// writes. This is that thing, and it is the only writer of all three stores.
///
/// **Every attempt is recorded, and a payload arriving is recorded as a success
/// even when I6 refuses it.** Those are different facts: §9.1's `last_fetch_seq`
/// is *"the `seq` the last **successful** fetch returned"*, not the one the
/// phone kept, and `last_seq` is the one it kept. The predicate's third conjunct
/// compares them precisely so a refusal is visible — `ServedPayloadNotHeld`,
/// whose doc names this as its one cause. Recording a refused payload as a
/// failed *fetch* would erase that and report a network problem instead.
class SnapshotRefresher {
  SnapshotRefresher({
    required this.reader,
    required this.diagnostics,
    required this.baselines,
    required this.heldCopies,
    required this.clock,
  });

  final PairedSnapshotReader reader;
  final FetchDiagnosticsStore diagnostics;
  final SeqBaselineStore baselines;
  final HeldCopyStore heldCopies;

  /// The device clock. Public like the stores beside it and for the same reason
  /// `PairedSnapshotReader.vault` is: a collaborator the caller passed in is not
  /// a secret from that caller.
  final DateTime Function() clock;

  /// Refreshes run one at a time, and the three stores are why.
  ///
  /// **This is a transaction across three files with no lock between them**, so
  /// its read-decide-write has to be serialised by the thing that owns it.
  /// Nothing here is thread-safety in the usual sense — Dart has one isolate —
  /// and that is exactly the trap: every `await` in [_refresh] is a point where
  /// a second `refresh()` can run to completion in between. Review reproduced
  /// it: two refreshes of the same publication, the first parked in
  /// `HeldCopyStore.hold`, and the second reading a baseline the first had
  /// already advanced and acting on it. One refresh at a time removes the whole
  /// class rather than the instance that was found.
  ///
  /// **The contract this cannot enforce, stated rather than implied:** it
  /// serialises *this instance*. The three stores must have exactly one writer,
  /// and this class is it; a second [SnapshotRefresher] over the same directory
  /// is outside the contract, and on the phone there is one.
  ///
  /// Errors are swallowed from the chain, not from the caller: [_refresh] never
  /// throws by contract, and a chain that a throw could poison would turn one
  /// defect into a refresher that never runs again.
  Future<SnapshotRefreshOutcome> refresh() {
    final next = _queue.then((_) => _refresh());
    _queue = next.then((_) {}, onError: (Object _) {});
    return next;
  }

  Future<void> _queue = Future<void>.value();

  /// One refresh. **Never throws** — the same contract as the two layers under
  /// it, for the same reason: an attempt that escaped as an exception is an
  /// attempt no record learned about, and §9.1's predicate would go on comparing
  /// the previous attempt's facts as though nothing had been tried.
  Future<SnapshotRefreshOutcome> _refresh() async {
    final outcome = await reader.read();
    // **One reading, taken after the attempt returned.** One instant for the
    // whole attempt means `FetchDiagnostics.succeeded` gets its two coinciding
    // stamps from a single value rather than from two reads that happen to
    // agree, and an attempt is never stamped earlier than the answer it
    // records. Taking it *before* would also invert `evaluateCopyState`'s rule
    // in the other direction: it reads `deviceNow` after the records precisely
    // so a stamp cannot appear to come from the future.
    final at = clock();

    switch (outcome) {
      case SnapshotReadNotPaired():
        return const RefreshNotAttempted(NoPairing.notPaired);
      case SnapshotReadPairingUnreadable():
        return const RefreshNotAttempted(NoPairing.pairingUnreadable);
      case SnapshotReadNotDelivered(:final pairingId, :final transport):
        if (transport is SnapshotNoPublication) {
          return _recordNoPublication(pairingId: pairingId, at: at);
        }
        return _recordFailure(
          pairingId: pairingId,
          at: at,
          error: _classOf(transport),
        );
      case SnapshotReadRejected(:final pairingId, :final reason):
        return _recordRejection(pairingId: pairingId, at: at, reason: reason);
      case SnapshotReadPayload(:final pairingId, :final opened):
        return _recordPayload(pairingId: pairingId, at: at, opened: opened);
    }
  }

  /// The class for a delivery that produced no body and was not a `404`.
  ///
  /// `503` and an undefined status are the host answering with something
  /// unusable — [FetchFailureClass.transportError]'s own examples — and the
  /// transport faults go through the table at the top of this file.
  FetchFailureClass _classOf(SnapshotFetchOutcome transport) => switch (transport) {
        SnapshotTransportFailure(:final fault) => classifyTransportFault(fault),
        SnapshotSourceUnavailable() => FetchFailureClass.transportError,
        SnapshotUnexpectedStatus() => FetchFailureClass.transportError,
        // Neither can reach here: `SnapshotReadNotDelivered` asserts against the
        // first and the `404` is decided by the caller. Answering rather than
        // throwing keeps a never-throws contract that does not depend on a
        // sealed hierarchy one file over staying the shape it is today, and
        // `unknownFailure` is the honest class for a state this file cannot
        // explain.
        SnapshotBodyReceived() || SnapshotNoPublication() => FetchFailureClass.unknownFailure,
      };

  Future<SnapshotRefreshOutcome> _recordNoPublication({
    required String pairingId,
    required DateTime at,
  }) async {
    if (await _priorRecord(pairingId) case _AmendWith(:final success)) {
      await _write(
        FetchDiagnostics.foundNoPublication(pairingId: pairingId, at: at, after: success),
      );
    }
    return const RefreshKeptHeldCopy(RefreshRefusal.hostHasNoPublication);
  }

  Future<SnapshotRefreshOutcome> _recordFailure({
    required String pairingId,
    required DateTime at,
    required FetchFailureClass error,
  }) async {
    if (await _priorRecord(pairingId) case _AmendWith(:final success)) {
      await _write(
        FetchDiagnostics.failed(
          pairingId: pairingId,
          at: at,
          error: error,
          after: success,
        ),
      );
    }
    return const RefreshKeptHeldCopy(RefreshRefusal.attemptFailed);
  }

  /// A body that arrived and was refused.
  ///
  /// [SnapshotRejection.otherPairing] does one thing more than the others:
  /// §9.3's table answers *"`pairing_id` is not the paired one"* with **refuse,
  /// same warning** as a downgrade, so the persistent warning is raised here and
  /// not only on the `seq` comparison. Nothing else about the refusal differs —
  /// the attempt is a failed fetch either way, because no payload was accepted.
  Future<SnapshotRefreshOutcome> _recordRejection({
    required String pairingId,
    required DateTime at,
    required SnapshotRejection reason,
  }) async {
    if (reason == SnapshotRejection.otherPairing) {
      await _warn(
        pairingId: pairingId,
        cause: DowngradeCause.foreignPairing,
        at: at,
        refused: null,
      );
      await _recordFailure(
        pairingId: pairingId,
        at: at,
        error: classifyRejection(reason),
      );
      return const RefreshKeptHeldCopy(RefreshRefusal.foreignPairing);
    }
    return _recordFailure(
      pairingId: pairingId,
      at: at,
      error: classifyRejection(reason),
    );
  }

  Future<SnapshotRefreshOutcome> _recordPayload({
    required String pairingId,
    required DateTime at,
    required OpenedPayload opened,
  }) async {
    final PublicationSeq seq;
    try {
      seq = PublicationSeq.parse(opened.payload.seq);
    } on PayloadFormatException catch (error) {
      // **Nearly unreachable, and caught rather than asserted away.**
      // `PayloadEnvelope` fixes the header `seq` to a canonical positive decimal
      // and requires the body to spell it identically, so the shapes
      // `PublicationSeq.parse` refuses are mostly gone before this line. What
      // survives is the one check the regex cannot make: a counter past the
      // 64-bit boundary, which `parse` refuses rather than wrapping. An
      // unparseable `seq` is an unusable payload — I6 cannot be evaluated
      // against it — so it is refused like any other body the phone cannot use,
      // and the value never reaches a message.
      debugLog(() => 'payload refused: $error');
      return _recordFailure(
        pairingId: pairingId,
        at: at,
        error: FetchFailureClass.transportError,
      );
    }

    final state = await baselines.read(pairingId);
    if (state case BaselineUnreadable(:final reason)) {
      // **Never accept-on-trust.** §9.3's accept-on-trust case is a phone with
      // *no* baseline for its pairing; a stored one that will not parse is a
      // different phone, and treating the two alike would make damaging one
      // file the way to disable I6. The fetch still succeeded, so it is recorded
      // as one — the predicate then reports `RecordsUnusable`, which is the true
      // statement about this phone.
      debugLog(() => 'baseline unreadable, payload not accepted: $reason');
      await _writeSuccess(pairingId: pairingId, at: at, seq: seq);
      return const RefreshKeptHeldCopy(RefreshRefusal.baselineUnreadable);
    }
    final stored = state is BaselineHeld ? state.baseline : null;
    final floor = await _replayFloor(pairingId, stored);

    if (floor != null && seq < floor) {
      // I6's refusal. The floor does **not** move down — the phone keeps the
      // newer copy it already holds — and the warning is persistent, clearing
      // only when a greater `seq` actually arrives (§9.3 point 1). Writing the
      // floor back as `last_seq` also repairs a baseline that a partial write
      // left behind the copy.
      await _warn(
        pairingId: pairingId,
        cause: DowngradeCause.olderPublication,
        at: at,
        refused: seq,
        holding: floor,
      );
      await _writeSuccess(pairingId: pairingId, at: at, seq: seq);
      return const RefreshKeptHeldCopy(RefreshRefusal.downgradeRefused);
    }

    return _accept(
      pairingId: pairingId,
      at: at,
      seq: seq,
      opened: opened,
      stored: stored,
      floor: floor,
    );
  }

  /// The `seq` at or below which a payload is a downgrade — **the higher of the
  /// two records, not the baseline alone.**
  ///
  /// The baseline and the held copy are two files and one fact, and there is no
  /// transaction between them: a write failure or a process kill between the two
  /// leaves them disagreeing, and review found that the rollback this replaced
  /// could not cover the second of those at all — nothing runs after a kill. So
  /// the skew is treated as a *state to read correctly* rather than one to
  /// prevent, and the reading is the maximum. `SeqBaseline.lastSeq` is *"the
  /// `seq` of the payload the phone actually holds"*; when it is behind the copy
  /// it is simply a stale reading of that, and the copy's own `seq` is the
  /// fresher one. Taking the maximum means a half-finished accept can never
  /// *lower* the bar an earlier accept set, which is the only direction that
  /// costs anything.
  ///
  /// `null` means neither record names one — a phone with no baseline and no
  /// readable copy, which is §9.3 point 3's accept-on-trust case.
  ///
  /// **A copy this build cannot read contributes nothing**, deliberately: an
  /// unreadable or outdated copy has no `seq` to offer, and that is precisely
  /// why the baseline is a separate file that survives it.
  Future<PublicationSeq?> _replayFloor(String pairingId, SeqBaseline? stored) async {
    final held = await _heldSeq(pairingId);
    final baseline = stored?.lastSeq;
    if (baseline == null || (held != null && held > baseline)) {
      return held ?? baseline;
    }
    return baseline;
  }

  Future<PublicationSeq?> _heldSeq(String pairingId) async {
    final HeldCopyState state;
    try {
      state = await heldCopies.read(pairingId);
    } on Object catch (error) {
      debugLog(() => 'held copy not read for the replay floor: $error');
      return null;
    }
    if (state is! HeldCopyHeld) {
      return null;
    }
    try {
      return PublicationSeq.parse(state.payload.seq);
    } on PayloadFormatException catch (error) {
      // The copy is stored as the payload document, and `PhonePayload` does not
      // fix the spelling of `seq` the way `PayloadEnvelope` does — so a damaged
      // copy can carry one this cannot read. It contributes nothing rather than
      // throwing; the baseline is still there.
      debugLog(() => 'held copy seq unusable for the replay floor: $error');
      return null;
    }
  }

  /// §9.3's two accepting rows: a greater `seq` replaces the copy and advances
  /// the floor, an equal one is *"nothing new"*.
  ///
  /// **The copy is written first and there is no rollback**, which reverses the
  /// order this file shipped with, and the reason is the failure that order
  /// could not cover. Baseline-first plus a rollback was chosen because a
  /// baseline ahead of the copy refuses a replay while one behind accepts one —
  /// true, but it treated the skew as something to undo, and **a process killed
  /// between the two writes runs no rollback at all.** Review reached the same
  /// place from the other side: when the rollback itself failed, the advanced
  /// baseline satisfied `last_fetch_seq == last_seq` over a copy that never
  /// landed and produced `HOST_NOT_PUBLISHING` — *"nothing has been published
  /// since"* about a host that had just published. The rollback was written to
  /// prevent exactly that and reintroduced it on its own failure path.
  ///
  /// [_replayFloor] makes the skew safe instead of rare, and once it is safe the
  /// order can be the honest one. Both partial states now read correctly:
  ///
  /// - **copy written, baseline not.** The floor comes from the copy, so I6 still
  ///   refuses everything below the new `seq`. The baseline still names the old
  ///   one, so conjunct 3 fails and §9.1 says `ServedPayloadNotHeld` —
  ///   *"couldn't check"*, which blames nobody. The next refresh repairs it.
  /// - **copy not written.** Nothing was advanced, because nothing is written
  ///   before it. The phone holds what it held and the record says so.
  ///
  /// **The copy is written on an equal `seq` too, not only on a greater one.** It
  /// costs one idempotent write and it is the only way out of a real state: a
  /// floor at `seq` with a damaged held copy would otherwise show nothing until
  /// the host published again, which on a daemon that has stopped is never.
  /// Re-holding the publication the floor already names cannot downgrade
  /// anything.
  Future<SnapshotRefreshOutcome> _accept({
    required String pairingId,
    required DateTime at,
    required PublicationSeq seq,
    required OpenedPayload opened,
    required SeqBaseline? stored,
    required PublicationSeq? floor,
  }) async {
    try {
      await heldCopies.hold(opened.text);
    } on Object catch (error) {
      // The text parsed on the way in — it is what `PayloadEnvelope.open` just
      // returned — so what can fail here is the filesystem.
      debugLog(() => 'held copy not written: $error');
      await _writeSuccess(pairingId: pairingId, at: at, seq: seq);
      return const RefreshKeptHeldCopy(RefreshRefusal.notStored);
    }

    // **Two different questions, and collapsing them clears a warning it must
    // not.** Whether this is a *newer publication* is asked of the floor: §9.3
    // clears the warning only when a `seq` greater than the one held arrives,
    // *"not on the next successful fetch, which would let a single good response
    // paper over an unexplained downgrade."* Whether the *baseline file* needs
    // writing is asked of the baseline, so that one left behind the copy by a
    // partial write is repaired here rather than waiting for a new publication.
    final isNewerPublication = floor == null || seq > floor;
    if (stored == null || stored.lastSeq != seq) {
      final written = await _tryWriteBaseline(
        SeqBaseline(
          pairingId: pairingId,
          lastSeq: seq,
          warning: isNewerPublication ? null : stored?.warning,
        ),
      );
      if (!written) {
        // The copy is on disk, so the payload *was* accepted and the screen
        // should show it. What lags is the baseline, and [_replayFloor] reads
        // the copy, so the floor does not lag with it. Reporting this as a
        // refusal would tell a caller the phone kept the old copy when it is
        // holding the new one.
        debugLog(() => 'baseline lags the held copy; the floor comes from the copy');
      }
    }
    await _writeSuccess(pairingId: pairingId, at: at, seq: seq);
    return RefreshAccepted(opened.payload);
  }

  /// Raise §9.3's persistent warning without disturbing what the phone holds.
  ///
  /// [holding] is the replay floor the refusal was measured against, and it is
  /// required for a downgrade because the record cannot exist without one.
  /// Writing it back as `last_seq` also repairs a baseline a partial write left
  /// behind the held copy. A foreign pairing passes none: its refusal is decided
  /// before any `seq` comparison, so on a phone with no baseline there is no
  /// `last_seq` to attach the warning to — and nothing for it to defend either,
  /// since a phone that has accepted no payload under this pairing holds no copy
  /// that could be downgraded.
  ///
  /// The warning it writes **replaces** any warning already stored, which is the
  /// right direction: both are refusals, and the newer one carries the `seq` and
  /// the instant that are now true.
  /// [SeqBaseline] makes that unrepresentable rather than storing a placeholder,
  /// which is the same refusal `DowngradeWarning.refusedSeq` makes about the
  /// foreign counter itself.
  Future<void> _warn({
    required String pairingId,
    required DowngradeCause cause,
    required DateTime at,
    required PublicationSeq? refused,
    PublicationSeq? holding,
  }) async {
    var lastSeq = holding;
    if (lastSeq == null) {
      switch (await baselines.read(pairingId)) {
        case BaselineHeld(:final baseline):
          lastSeq = baseline.lastSeq;
        case BaselineAbsent():
        case BaselineUnreadable():
          debugLog(() => 'no baseline to warn on: $cause');
          return;
      }
    }
    await _tryWriteBaseline(
      SeqBaseline(
        pairingId: pairingId,
        lastSeq: lastSeq,
        warning: DowngradeWarning(cause: cause, at: at, refusedSeq: refused),
      ),
    );
  }

  Future<bool> _tryWriteBaseline(SeqBaseline baseline) async {
    try {
      await baselines.write(baseline);
      return true;
    } on Object catch (error) {
      debugLog(() => 'baseline not written: $error');
      return false;
    }
  }

  Future<void> _writeSuccess({
    required String pairingId,
    required DateTime at,
    required PublicationSeq seq,
  }) =>
      _write(FetchDiagnostics.succeeded(pairingId: pairingId, at: at, seq: seq));

  /// A store write must not take the screen down.
  ///
  /// `RecordingSnapshotSource` set this precedent for the history store and the
  /// reasoning transfers: the number with its age is what this product is for,
  /// and a full disk is an ordinary way to reach an unwritable store. What is
  /// lost here is a *reason* — the predicate falls to `CANNOT_CHECK`, which
  /// blames nobody and repopulates on the next successful fetch.
  Future<void> _write(FetchDiagnostics record) async {
    try {
      await diagnostics.write(record);
    } on Object catch (error) {
      debugLog(() => 'fetch diagnostics not written: $error');
    }
  }

  /// The last success to carry forward onto a failed attempt, or a refusal to
  /// amend the record at all.
  ///
  /// **An unreadable record is not overwritten**, and that is the whole reason
  /// this returns a type rather than a nullable. `FetchDiagnostics.failed` takes
  /// `after: null` to mean *"this phone has attempted under this pairing and
  /// never succeeded"* — `CannotCheck.since` renders exactly that — so writing
  /// `null` because the old record would not parse states a fact about the
  /// owner's history that nothing observed. It is the `NeverFetched`-instead-of-
  /// `RecordsUnusable` mistake `copy_freshness.dart` names, one field down.
  ///
  /// Leaving the damaged file alone costs this attempt's timestamp and keeps
  /// `RecordsUnusable` on screen, which is true. It heals on the next successful
  /// fetch, because a success needs no carried-forward instant:
  /// `FetchDiagnostics.succeeded` sets both of its stamps from one value.
  Future<_PriorRecord> _priorRecord(String pairingId) async {
    switch (await diagnostics.read(pairingId)) {
      case DiagnosticsAbsent():
        return const _AmendWith(null);
      case DiagnosticsHeld(diagnostics: final record):
        return _AmendWith(record.lastSuccess);
      case DiagnosticsUnreadable(:final reason):
        debugLog(() => 'fetch diagnostics unreadable, not overwritten: $reason');
        return const _DoNotOverwrite();
    }
  }
}

/// What the stored diagnostics allow this attempt to do to them.
sealed class _PriorRecord {
  const _PriorRecord();
}

/// Write the attempt, carrying [success] forward — `null` when this pairing has
/// genuinely never had one.
final class _AmendWith extends _PriorRecord {
  const _AmendWith(this.success);

  final FetchSuccess? success;
}

/// The stored record could not be read, so this attempt is not written over it.
final class _DoNotOverwrite extends _PriorRecord {
  const _DoNotOverwrite();
}
