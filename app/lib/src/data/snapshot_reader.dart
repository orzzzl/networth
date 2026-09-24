import '../debug_log.dart';
import '../domain/aes_gcm.dart';
import '../domain/payload_envelope.dart';
import '../domain/payload_format_exception.dart';
import '../domain/phone_payload.dart';
import '../pairing/pairing_provision.dart';
import '../pairing/pairing_vault.dart';
import 'snapshot_transport.dart';

/// Why bytes that arrived are not a payload this phone may accept.
///
/// Every value here describes something that reached the phone over the tailnet
/// and was refused. None of them is a *reachability* fact — those are
/// [SnapshotReadNotDelivered] — and none is a statement about the owner's
/// pairing being absent, which is [SnapshotReadNotPaired]. Keeping the three
/// apart is what lets §9.1 say "the host answered and its answer was wrong"
/// without saying it about a phone that is merely off the tailnet.
enum SnapshotRejection {
  /// The body is not a §6.1 document at all: not JSON, a field missing, an
  /// unexpected field, a nonce of the wrong length, non-canonical base64url.
  ///
  /// Reached *before* the tag check, so this says nothing about who sent it.
  notAnEnvelope,

  /// A well-formed envelope whose clear header names a different pairing.
  ///
  /// **Decided on unauthenticated bytes, deliberately, and only ever to
  /// refuse.** `pairing_id` is clear text until [PayloadEnvelope.open]
  /// authenticates it through the AAD, so anyone able to reach the phone can
  /// set it to anything. That cannot promote a forgery: a genuine envelope for
  /// this pairing always carries this pairing's id, so the check only ever
  /// turns an accept into a refusal, never the reverse. What it buys is the
  /// diagnosis — after re-pairing, the host publishes under a new id and a
  /// phone still holding the old key would otherwise report
  /// [notAuthentic], which reads as an attack and is answered by *re-pair*.
  otherPairing,

  /// The tag did not verify: this was not sealed by a holder of the phone's
  /// payload key.
  notAuthentic,

  /// Authentic, and its two authenticated copies of the header disagree.
  ///
  /// A publisher defect rather than an attack — see
  /// [PayloadHeaderMismatchException], which explains why it is refused anyway.
  headerDisagreement,

  /// Authentic, and the body is not a shape this build reads.
  payloadNotReadable,
}

/// The result of one attempt to read the published snapshot.
///
/// Sealed, and **no case carries free text**. The rejection reasons above are
/// enum values and their detail goes to [debugLog] and nowhere else, because
/// the exception messages this class catches interpolate payload-derived
/// content — `phone_payload.dart` raises `unknown connection_state "$value"`
/// and `dated_total.dart` raises `unknown age_state "$value"`, both quoting a
/// field of a decrypted payload. A `String detail` on a value type is a channel
/// to the screen and to the owner's history record; this repo already paid once
/// for an exception message that reached a release build's logcat
/// (`debug_log.dart`). So the text stops at the debug gate by construction
/// rather than by everyone downstream remembering.
sealed class SnapshotReadOutcome {
  const SnapshotReadOutcome();
}

/// An attempt that was actually made, and the pairing it was made under.
///
/// **The pairing is on the outcome rather than looked up again afterwards, and
/// §9.1's records are why.** Everything the caller persists about an attempt —
/// the four fetch facts, the I6 baseline, the held copy — is scoped to a
/// `pairing_id`, and a second `vault.read()` after the attempt is a different
/// read: `networth pair` rotates the pairing in one local transaction
/// (§6.3.1), so a rotation landing mid-attempt would file this attempt's facts
/// under the *new* pairing. `FetchDiagnostics` spells out what that costs —
/// two pairings' `seq` counters are unrelated numbers free to coincide, so a
/// `last_fetch_seq` from one compared against a `last_seq` from the other can
/// satisfy `HOST_NOT_PUBLISHING` about a host that is publishing perfectly
/// well. The state that has no pairing to name — [SnapshotReadNotPaired] and
/// [SnapshotReadPairingUnreadable] — is exactly the state in which no attempt
/// was made, so the split is the same fact twice rather than two rules.
sealed class SnapshotAttempted extends SnapshotReadOutcome {
  const SnapshotAttempted(this.pairingId);

  /// The `pairing_id` from the provision this attempt used.
  final String pairingId;
}

/// Authenticated, opened, and parsed.
final class SnapshotReadPayload extends SnapshotAttempted {
  const SnapshotReadPayload(super.pairingId, this.opened);

  /// The verified bytes and their parse, from one open.
  ///
  /// [OpenedPayload.payload] carries `seq`, `published_at`, `publish_interval`
  /// and `grace` itself, so the envelope is not returned alongside it: the
  /// header/body agreement check in [PayloadEnvelope.open] has already
  /// established that the two copies say the same thing, and handing back both
  /// would re-open the question of which one I6 and the history record should
  /// read. [OpenedPayload.text] is the one thing the parse cannot supply — the
  /// exact document `HeldCopyStore.hold` keeps — and it travels *with* the
  /// parse rather than beside it for the reason that class states.
  final OpenedPayload opened;

  /// The parse, which is authoritative everywhere but storage.
  PhonePayload get payload => opened.payload;
}

/// The host sent no body — or nothing that could be reached.
final class SnapshotReadNotDelivered extends SnapshotAttempted {
  SnapshotReadNotDelivered(super.pairingId, this.transport)
      : assert(
          transport is! SnapshotBodyReceived,
          'a received body is not a non-delivery',
        );

  /// The transport's own outcome, verbatim: `404`, `503`, an unexpected status,
  /// or a [SnapshotTransportFault].
  ///
  /// **The one invariant here that the type does not carry**, and it is a
  /// deliberate trade rather than an oversight: [SnapshotBodyReceived] can
  /// never appear, because the only code that constructs this has already
  /// narrowed it away. Expressing that in the type means either re-declaring
  /// the transport's four non-body outcomes as four more classes here — the
  /// duplication then has to be kept in step by hand, across a file boundary,
  /// forever — or editing the sealed hierarchy in `snapshot_transport.dart`
  /// while that file is in review. The assert plus this sentence is the cheaper
  /// half, and `snapshot_reader_test.dart` pins it.
  final SnapshotFetchOutcome transport;
}

/// Bytes arrived and are not a payload this phone may accept.
final class SnapshotReadRejected extends SnapshotAttempted {
  const SnapshotReadRejected(super.pairingId, this.reason);

  final SnapshotRejection reason;
}

/// This phone has never been paired, or its pairing was revoked here.
///
/// Not a failure of anything: it is the state a fresh install is in, and the
/// answer to it is the pairing flow rather than a retry.
final class SnapshotReadNotPaired extends SnapshotReadOutcome {
  const SnapshotReadNotPaired();
}

/// The phone is paired as far as it knows and **could not read its own
/// pairing**.
///
/// Two causes, and they are one outcome on purpose: protected storage raised
/// (the platform channel failed), or it returned a value
/// [PairingProvision.parse] refuses. The second is not hypothetical — the vault
/// stores one string and parses it on the way out, so a truncated write leaves
/// exactly this state.
///
/// Its own outcome rather than [SnapshotReadNotPaired], because collapsing the
/// two would tell an owner whose device is fine that he is not paired, and the
/// action that suggests — pair again — silently rotates the key on a host that
/// was working. The distinction costs one class and prevents a destructive
/// suggestion.
///
/// **It carries no error object.** What `flutter_secure_storage` raises on
/// Android is a platform exception whose message is assembled by the keystore
/// layer; putting it in a value type would put it on the path to the screen and
/// to `debugLog`'s callers, and the one thing that layer handles is key
/// material. The failure is logged where it happens and the fact of it is what
/// travels.
final class SnapshotReadPairingUnreadable extends SnapshotReadOutcome {
  const SnapshotReadPairingUnreadable();
}

/// Reads the published snapshot for **this phone's pairing**: fetch the bytes,
/// open the §6.1 envelope with the paired key, parse the payload.
///
/// It is deliberately *not* a [SnapshotSource](snapshot_source.dart). That
/// interface is `Future<PhonePayload> load()`, which has no channel for the
/// outcomes above and would force each of them to become a throw — and §9.1
/// requires the phone to persist five facts about **every** attempt, successes
/// and failures alike, so they have to survive as values to be recorded at all.
/// The adapter onto `SnapshotSource` therefore lands with the recorder, in the
/// change that also decides where `HOST_NOT_PUBLISHING` sits in
/// `FetchDiagnostics`. This class is the part of that work which does not
/// depend on the answer.
///
/// **Holds no secret of its own.** The payload key is read from the vault per
/// attempt and passed to [PayloadEnvelope.open]; nothing here caches it, so an
/// instance outliving a revocation has nothing to keep using.
class PairedSnapshotReader {
  PairedSnapshotReader({
    required this.vault,
    SnapshotTransport Function(PairingProvision provision)? openTransport,
  }) : _openTransport = openTransport ?? transportFor;

  /// Public like [RecordingSnapshotSource.store], and for the same reason: the
  /// collaborator a caller passed in is not a secret from that caller.
  final PairingVault vault;

  final SnapshotTransport Function(PairingProvision provision) _openTransport;

  /// The host comes from the provision and nothing else.
  ///
  /// **Full tailnet name, never a prefix or a stored copy** — the pairing
  /// bundle is the single place that says where to look, so a rotation cannot
  /// leave this pointed at the previous host.
  ///
  /// Public so a test can read the [SnapshotTransport.host] it produces without
  /// a network, which is the only way to check this wiring: every other path
  /// through [read] goes through the injected factory instead, and a provision
  /// carrying a loopback address cannot exist — `PairingProvision.parse`
  /// requires a dotted DNS name.
  static SnapshotTransport transportFor(PairingProvision provision) =>
      SnapshotTransport(host: provision.tailnetName);

  /// One attempt. Every outcome is a value.
  ///
  /// The exceptions caught below are exactly the four the two layers underneath
  /// document as theirs: [PayloadEnvelopeException] from parsing the document,
  /// and [AesGcmAuthenticationException], [PayloadHeaderMismatchException] and
  /// [PayloadFormatException] from opening it. Nothing catches [Object] here,
  /// matching `SnapshotTransport.fetch`: a fifth exception escaping would be a
  /// defect in one of those files rather than a condition of the network, and
  /// swallowing it into an enum would turn a bug into a fetch result the owner
  /// is shown as host trouble.
  Future<SnapshotReadOutcome> read() async {
    final PairingProvision? provision;
    try {
      provision = await vault.read();
    } on Object catch (error) {
      // The one blanket catch, and the reason it is one: this call crosses a
      // platform channel, so what it can raise is decided by the Android
      // keystore rather than by any Dart contract in this repo.
      debugLog(() => 'pairing unreadable: $error');
      return const SnapshotReadPairingUnreadable();
    }
    if (provision == null) {
      return const SnapshotReadNotPaired();
    }

    // Fixed once, here, and read for every outcome below: the provision this
    // attempt was made under cannot change halfway through it.
    final pairingId = provision.pairingId;

    final fetched = await _openTransport(provision).fetch();
    if (fetched is! SnapshotBodyReceived) {
      return SnapshotReadNotDelivered(pairingId, fetched);
    }

    final PayloadEnvelope envelope;
    try {
      envelope = PayloadEnvelope.fromJsonString(fetched.body);
    } on PayloadEnvelopeException catch (error) {
      debugLog(() => 'snapshot rejected: $error');
      return SnapshotReadRejected(pairingId, SnapshotRejection.notAnEnvelope);
    }

    if (envelope.pairingId != pairingId) {
      // Not logged with either id: the pairing id is the durable half of the
      // pairing bundle, and the bundle's other half is the payload key.
      debugLog(() => 'snapshot rejected: envelope names a different pairing');
      return SnapshotReadRejected(pairingId, SnapshotRejection.otherPairing);
    }

    try {
      // **The vault's own unmodifiable view, not a copy of it.**
      // `_roundKeys` in `aes_gcm.dart` reads the key and never writes to it, so
      // a copy would be defensive against nothing today. Passing the view is
      // the better half of that trade anyway: if some later expansion did
      // expand in place, the view throws on the first write, where a copy would
      // have let it quietly corrupt the key for the rest of the attempt.
      return SnapshotReadPayload(pairingId, envelope.open(key: provision.payloadKey));
    } on AesGcmAuthenticationException {
      return SnapshotReadRejected(pairingId, SnapshotRejection.notAuthentic);
    } on PayloadHeaderMismatchException catch (error) {
      debugLog(() => 'snapshot rejected: $error');
      return SnapshotReadRejected(pairingId, SnapshotRejection.headerDisagreement);
    } on PayloadFormatException catch (error) {
      debugLog(() => 'snapshot rejected: $error');
      return SnapshotReadRejected(pairingId, SnapshotRejection.payloadNotReadable);
    }
  }
}
