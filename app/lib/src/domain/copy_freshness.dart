/// How old *this phone's copy* of the snapshot is — `DESIGN.md` §9.1.
///
/// This is the second of invariant **I4**'s two dimensions, and it is a
/// different question from how old the institutions' data is. A payload can say
/// `age_state: KNOWN, as_of: today` and still be a copy the phone fetched a week
/// ago, with six newer publications it never saw. Rendering only the first
/// dimension would print today's date over week-old money — which is the
/// original lie, reproduced inside the app that exists to refuse it.
///
/// So the two dimensions are separate types, evaluated separately, and rendered
/// as separate lines. They are never reduced to one badge.
enum CopyFreshness {
  /// The copy is within `publish_interval + grace` of its publication.
  fresh,

  /// The copy is past its deadline. §9.1 splits this by *reason*
  /// (`HOST_NOT_PUBLISHING` vs `CANNOT_CHECK`) using the device's own fetch
  /// history — `last_fetch_attempt_at`, `last_fetch_success_at`,
  /// `last_fetch_seq`. A fixture-backed build has never fetched anything, so it
  /// has none of those three facts and cannot honestly name a culprit. Task 22
  /// adds the persisted history and with it the reason; this build says the copy
  /// is overdue and stops there, which is the true statement available to it.
  stale,

  /// The device clock and the payload disagree, so no age can be computed.
  unknown,
}

/// The deadline that travels with the payload, per §9.1.
///
/// `stale_after = published_at + publish_interval_seconds + grace_seconds`. The
/// phone never hardcodes the daemon's cadence — both numbers come off the wire
/// precisely so the promise can change without shipping a new APK.
DateTime staleAfter({
  required DateTime publishedAt,
  required Duration publishInterval,
  required Duration grace,
}) =>
    publishedAt.add(publishInterval).add(grace);

/// Evaluate §9.1's ordered rules.
///
/// Only the first of §9.1's two clock-disagreement branches is implemented here.
/// "Payload from the future" is decidable from one payload and one clock reading,
/// which is all a fixture-backed build has. The other branch — the device clock
/// moving *backwards* between fetches — is only detectable by pairing each stored
/// timestamp with a monotonic reading across runs, and there is no persisted
/// fetch history until task 22. It is named here rather than silently missing,
/// because the gap is in what the app can observe, not in what it believes.
CopyFreshness evaluateCopyFreshness({
  required DateTime publishedAt,
  required Duration publishInterval,
  required Duration grace,
  required DateTime deviceNow,
}) {
  const tolerance = Duration(minutes: 5);
  if (publishedAt.isAfter(deviceNow.add(tolerance))) {
    return CopyFreshness.unknown;
  }
  final deadline = staleAfter(
    publishedAt: publishedAt,
    publishInterval: publishInterval,
    grace: grace,
  );
  // §9.1 rule 2 is `device_now <= stale_after`, so the boundary instant itself
  // is still fresh.
  return deviceNow.isAfter(deadline) ? CopyFreshness.stale : CopyFreshness.fresh;
}

/// The connection dimension, evaluated by the daemon and carried on the wire.
///
/// §9.2 is explicit that the phone never re-implements this policy — it arrives
/// already decided, split so that the "no owner action" states can never be
/// rendered as a demand to re-link.
enum ConnectionDisplayState {
  ok,
  waiting,
  actionNeeded;

  static ConnectionDisplayState fromWire(String value) => switch (value) {
        'OK' => ConnectionDisplayState.ok,
        'WAITING' => ConnectionDisplayState.waiting,
        'ACTION_NEEDED' => ConnectionDisplayState.actionNeeded,
        _ => throw ArgumentError.value(value, 'connection_state'),
      };
}
