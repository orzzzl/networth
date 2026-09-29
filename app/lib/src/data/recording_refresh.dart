import '../debug_log.dart';
import 'history_store.dart';
import 'snapshot_refresh.dart';
import 'snapshot_source.dart';

/// Keeps the phone's own series, one reading per **accepted** payload.
///
/// This is `RecordingSnapshotSource`'s half of task `23a` moved onto the seam
/// that replaces it. That class records what a [SnapshotSource] loads, and the
/// build it was written for had one: a bundled fixture, loaded once per launch.
/// The build this app is becoming fetches instead, and the event worth a
/// reading is no longer "a payload was loaded" but "a payload was **kept**" —
/// which is [RefreshAccepted] and nothing else.
///
/// **Why the outcome and not the held copy.** The copy on disk is where the
/// screen reads its total, and recording from there would file a reading every
/// launch, including the launches that fetched nothing — a flat run of
/// identical points that says the net worth held steady when what actually
/// happened is that nobody looked. [RefreshAccepted] happens exactly when I6
/// admitted a new payload, so one accepted payload is one point.
///
/// It wraps the refresh for `RecordingSnapshotSource`'s reason, restated
/// because the seam moved: accepting a payload and recording it are the same
/// event by construction, so there is no second call site that could forget.
/// [HomeLoader] deliberately ignores the outcome when deciding what to *draw*,
/// and that is the point of putting this here instead of there — drawing reads
/// the stores, recording keys on the attempt, and the two want different
/// halves of the same call.
class RecordingSnapshotRefresher implements SnapshotRefreshing, RecordingStatus {
  RecordingSnapshotRefresher({required this.inner, required this.store});

  final SnapshotRefreshing inner;
  final HistoryStore store;

  /// Answers for the copy the screen is **about to show**, which is what makes
  /// the two non-accepting branches below leave it alone rather than clear it.
  @override
  bool get lastRecordingFailed => _lastRecordingFailed;
  bool _lastRecordingFailed = false;

  @override
  Future<SnapshotRefreshOutcome> refresh() async {
    final outcome = await inner.refresh();
    if (outcome is! RefreshAccepted) {
      // **Unchanged, not cleared — and this is where this class differs from
      // `RecordingSnapshotSource`, deliberately.** That one resets on every
      // load because every load produces the payload it is answering about, so
      // "the failure is over" is a fact it has just measured. Here a refusal
      // means no payload was accepted and *the copy already on screen stays on
      // screen*. Clearing would announce that the reading now displayed reached
      // the record, on the strength of a write that never happened.
      return outcome;
    }
    try {
      await store.record(outcome.payload);
      _lastRecordingFailed = false;
    } on Object catch (error) {
      // Not propagated, for `RecordingSnapshotSource`'s reason: a store that
      // cannot be written is not a reason to take the total off the screen.
      // Not silent either — that was the defect found in the first version of
      // that class, where a phone quietly lost every reading and showed the
      // owner an ordinary curve. Awaited rather than fired and forgotten, so a
      // caller that refreshes then reads sees its own write.
      _lastRecordingFailed = true;
      debugLog(() => 'history not recorded: $error');
    }
    return outcome;
  }
}
