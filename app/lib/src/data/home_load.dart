import '../debug_log.dart';
import '../domain/fetch_diagnostics.dart';
import '../domain/held_copy.dart';
import '../domain/seq_baseline.dart';
import '../pairing/pairing_provision.dart';
import '../pairing/pairing_vault.dart';
import 'fetch_diagnostics_store.dart';
import 'held_copy_store.dart';
import 'seq_baseline_store.dart';
import 'snapshot_refresh.dart';

/// One screenful, after an attempt and a re-read of what is on disk.
///
/// **Three cases, and the third carries [HeldCopyState] itself rather than a
/// state of its own.** A parallel hierarchy mirroring that file's five cases
/// would be the obvious shape and it is the wrong one: the distinctions it owes
/// are exactly the distinctions `held_copy.dart` already draws — a missing copy
/// must never be readable as a damaged one, a damaged one never as never-fetched
/// — and re-expressing them is how a mapping gets to quietly collapse two into
/// one. Not re-expressing them removes that possibility rather than testing for
/// it, and Dart's exhaustiveness check then makes a sixth copy state a compile
/// error at the screen instead of a silently unhandled branch.
sealed class HomeLoad {
  const HomeLoad();
}

/// This phone is in no pairing, so nothing was attempted and nothing is scoped.
///
/// The fresh-install state, and the state after the owner revokes. Distinct
/// from [HomePairingUnreadable] for the reason `SnapshotReadPairingUnreadable`
/// gives: telling an owner whose device is fine that he has never paired sends
/// him to re-pair, which is the one action that destroys the copy he still has.
final class HomeNotPaired extends HomeLoad {
  const HomeNotPaired();
}

/// A pairing may well exist and this device could not read it.
///
/// Secure storage did not answer. Nothing is scoped, so nothing on disk may be
/// read or filed — every record this app keeps is scoped to a `pairing_id`
/// (§9.1, §9.3), and a phone with no readable pairing has no scope to read one
/// under, let alone to write one under.
final class HomePairingUnreadable extends HomeLoad {
  const HomePairingUnreadable();
}

/// A pairing was read, and this is what the phone holds under it.
///
/// All three come from one `pairing_id`, fixed by a single vault read, so the
/// copy and the records on screen cannot be about two different relationships.
final class HomePaired extends HomeLoad {
  const HomePaired({required this.copy, required this.diagnostics, required this.baseline});

  /// What was found when the copy was looked for — **re-read from the store,
  /// never taken from the refresh outcome.** The refresher hands an accepted
  /// payload back for callers that want to skip the re-read; taking it would
  /// give the screen two sources for one fact, free to disagree in exactly the
  /// window where a refresh is writing to one of them.
  final HeldCopyState copy;

  /// §9.1's five facts, for the predicate that dates [copy] and assigns blame.
  final DiagnosticsState diagnostics;

  final BaselineState baseline;
}

/// Refresh, then read: the two halves of opening the app, in that order.
///
/// **The order is the whole of it.** Refreshing writes the three stores and
/// reading renders them, so reading second is what makes an attempt visible on
/// the screen it was made for. Refreshing *only* would show the owner the
/// previous launch's answer; reading only would be the build this replaces.
class HomeLoader {
  HomeLoader({
    required this.vault,
    required this.refresher,
    required this.heldCopies,
    required this.diagnostics,
    required this.baselines,
  });

  /// Read once per load, **after the refresh**, and it fixes the scope for
  /// everything below it.
  ///
  /// This is the second vault read of a load — [SnapshotRefresher]'s reader
  /// takes the first, to decide where to fetch from. Deliberately not shared,
  /// and deliberately the later one: if the pairing rotates mid-load, the
  /// display scope must be the pairing the phone is in *now*. A copy saved
  /// under the pairing that just ended belongs to a relationship that is over
  /// and its key is gone, which is why `HeldCopyAbsent` is the right answer for
  /// it rather than the copy itself.
  final PairingVault vault;

  /// The refresh this load performs before it reads.
  ///
  /// Typed as the interface rather than as [SnapshotRefresher] so the refresh
  /// can be decorated — [RecordingSnapshotRefresher] wraps it to keep the
  /// phone's own history — while the contract this loader depends on is
  /// unchanged: one call, never throws, and the three stores are written before
  /// it returns. Underneath it is still the single writer of those stores.
  final SnapshotRefreshing refresher;

  final HeldCopyStore heldCopies;
  final FetchDiagnosticsStore diagnostics;
  final SeqBaselineStore baselines;

  /// Never throws, for [SnapshotRefresher.refresh]'s reason one layer up: a load
  /// that escaped as an exception is a screen with nothing on it, and the states
  /// below are all renderable answers.
  Future<HomeLoad> load() async {
    // The outcome is not read, and that is the design rather than an oversight:
    // what to draw comes from the stores underneath, re-read below.
    await refresher.refresh();

    final PairingProvision? provision;
    try {
      provision = await vault.read();
    } on Object catch (error) {
      // The one blanket catch, and the same justification `PairedSnapshotReader`
      // gives for its own: this call crosses a platform channel, so what it can
      // raise is decided by the Android keystore and not by any Dart contract in
      // this repo.
      debugLog(() => 'pairing unreadable: $error');
      return const HomePairingUnreadable();
    }
    if (provision == null) {
      return const HomeNotPaired();
    }

    // One id, read once, for all three: the copy and the records on screen
    // cannot end up describing two different relationships.
    final pairingId = provision.pairingId;
    final copy = await heldCopies.read(pairingId);
    final records = await diagnostics.read(pairingId);
    final baseline = await baselines.read(pairingId);
    return HomePaired(copy: copy, diagnostics: records, baseline: baseline);
  }
}
