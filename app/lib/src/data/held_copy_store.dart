import 'dart:convert';
import 'dart:io';

import 'package:path_provider/path_provider.dart';

import '../debug_log.dart';
import '../domain/held_copy.dart';
import '../domain/payload_envelope.dart';
import '../domain/payload_format_exception.dart';
import '../domain/phone_payload.dart';
import 'stored_file.dart';

/// Where the copy the phone is showing lives between launches.
abstract interface class HeldCopyStore {
  /// The copy held under [pairingId], or why there is none this build can show.
  Future<HeldCopyState> read(String pairingId);

  /// Replace the held copy with [source]. There is only ever one.
  ///
  /// Takes the payload's **own bytes** rather than a parsed [PhonePayload] —
  /// see [FileHeldCopyStore] for why that direction is the whole design and not
  /// a convenience.
  Future<void> hold(String source);
}

/// The decrypted payload document, stored verbatim in the app's own directory.
///
/// **Verbatim, and that is the load-bearing decision here.** The alternative is
/// to serialise a parsed [PhonePayload] back out, which means writing an
/// encoder for the whole tree — total, dated total, connection state, the alert
/// list — and maintaining it beside the decoder forever. `history_store.dart`
/// already argued this and its sentence transfers exactly: the check must be
/// *"`parseHistory` over bytes **is** `load`, not a second derivation of the
/// read path standing next to it, free to drift."* Here the stake is higher,
/// because the two derivations would not diverge into a wrong *curve* but into
/// a copy that renders differently from the payload it was made from — the
/// screen disagreeing with itself across a restart, on the figures this product
/// exists to state carefully.
///
/// So the file is not a record *containing* a payload. **The file is the
/// payload**, byte for byte as it came out of the §6.1 envelope, and reading it
/// back is [PhonePayload.fromJsonString] — the identical call the transport
/// makes. There is no format of this store's own to keep in step with anything.
///
/// **It is a plaintext file holding real balances, and that is already this
/// directory's bargain.** `history.json` holds the owner's totals in the same
/// app-private directory under the same platform protection. What must not
/// happen is this file's *contents* leaking sideways into a log or an error
/// string, which is why [HeldCopyUnreadable] carries a reason with no bytes in
/// it and why nothing here interpolates the document into a message.
///
/// **No key material is involved.** The envelope is opened before this store is
/// reached; the pairing key stays in `flutter_secure_storage` and is never
/// written beside the copy it opened.
class FileHeldCopyStore implements HeldCopyStore {
  FileHeldCopyStore({required this.open});

  /// The production wiring: `<app documents>/held_copy.json`, the same
  /// app-private directory and the same reasoning as the three stores beside it.
  factory FileHeldCopyStore.appPrivate() => FileHeldCopyStore(
        open: () async => File(
          '${(await getApplicationDocumentsDirectory()).path}/$fileName',
        ),
      );

  static const String fileName = 'held_copy.json';

  /// Where the file is. Injected so a test uses a temporary directory and so the
  /// production path is named in exactly one place.
  final Future<File> Function() open;

  @override
  Future<HeldCopyState> read(String pairingId) async {
    // Absence is `readStoredFile`'s to prove for the reason that file states at
    // length: four different conditions make `File.exists()` answer false and
    // only one of them is absence. Getting it wrong here costs less than it does
    // for the I6 baseline — a copy wrongly read as absent shows an empty screen
    // rather than bypassing a defence — but "your phone has never fetched
    // anything" is still a false claim about the owner's own history, and the
    // proof is already written.
    final String bytes;
    switch (await readStoredFile(open)) {
      case StoredBytes(bytes: final read):
        bytes = read;
      case StoredAbsent():
        return const HeldCopyAbsent();
      case StoredUnreadable(reason: final reason):
        return HeldCopyUnreadable('held copy could not be read: $reason');
    }

    final Object? decoded;
    try {
      decoded = jsonDecode(bytes);
    } on FormatException catch (error) {
      // The message names the JSON fault and never the text that caused it:
      // `FormatException.message` from `jsonDecode` describes the syntax error
      // without quoting the document, and `error.source` — which would — is not
      // read.
      return HeldCopyUnreadable('held copy is not JSON: ${error.message}');
    }
    if (decoded is! Map<String, Object?>) {
      return const HeldCopyUnreadable('held copy is not a JSON object');
    }

    // **Pairing before version, deliberately.** A copy left by a previous
    // pairing is not this pairing's business at any schema, so it is scoped away
    // before this build asks whether it could have read it — the same order
    // `seq_baseline_store.dart` uses, and for the same reason: a record this
    // pairing would never have consulted must not be refused for a field.
    final storedPairing = decoded['pairing_id'];
    if (storedPairing is! String || storedPairing.isEmpty) {
      return const HeldCopyUnreadable('held copy has no pairing_id');
    }
    if (storedPairing != pairingId) {
      return const HeldCopyAbsent();
    }

    // **Read before `fromJson`, not caught after it.** `PhonePayload.fromJson`
    // raises `PayloadFormatException` for a version mismatch exactly as it does
    // for a missing field, so telling the two apart downstream would mean
    // matching on a message — a string this store does not own and which the
    // payload layer is free to reword. Asking the document its version first is
    // the same fact established where it cannot be lost.
    final storedVersion = decoded['schema_version'];
    if (storedVersion is! String || storedVersion.isEmpty) {
      return const HeldCopyUnreadable('held copy has no schema_version');
    }
    if (!PayloadEnvelope.isCanonicalDecimal(storedVersion)) {
      // **Malformed is not "outdated", and the gap between them is the whole
      // point of the state.** [HeldCopyOutdated] says the document is a *valid*
      // one this build cannot read — an APK upgrade, the self-healing case. But
      // `0`, `01`, ` 2 ` and arbitrary text were never written by any build:
      // `PayloadEnvelope` has fixed this field to a canonical positive decimal
      // for as long as the format has existed. A document spelling it any other
      // way is damaged or forged, and answering "outdated" would tell the owner
      // to upgrade his way out of corruption — while a real corruption went
      // unreported.
      //
      // **The value does not travel with the refusal**, which is the other half.
      // [HeldCopyOutdated.storedVersion] is documented as safe to log, and that
      // is only true of a value this predicate has passed. Letting arbitrary
      // text reach it would reopen, through the state object, exactly the
      // document-text channel the `PayloadFormatException` branch below closes
      // so carefully.
      return const HeldCopyUnreadable('held copy has a malformed schema_version');
    }
    if (storedVersion != PhonePayload.supportedSchemaVersion) {
      return HeldCopyOutdated(
        storedVersion: storedVersion,
        readableVersion: PhonePayload.supportedSchemaVersion,
      );
    }

    try {
      return HeldCopyHeld(PhonePayload.fromJson(decoded));
    } on PayloadFormatException catch (error) {
      // **The message does not travel, and this is the one place in this file
      // where that costs something.** `PayloadFormatException.message`
      // interpolates payload-derived content — `phone_payload.dart` raises
      // `unknown connection_state "$value"` and `dated_total.dart` raises
      // `unknown age_state "$value"`, both quoting a field of a decrypted
      // document — and [HeldCopyUnreadable.reason] is on the path to the screen.
      // So the detail stops at the debug gate and the fact of the failure is
      // what reaches the caller, exactly as `snapshot_reader.dart` does it for
      // these same exceptions.
      debugLog(() => 'held copy rejected: $error');
      return const HeldCopyUnreadable('held copy is not a payload this build reads');
    }
  }

  @override
  Future<void> hold(String source) async {
    // Parsed before anything on disk is touched, and the result is discarded:
    // what is wanted is the throw. A store that can write bytes its own `read`
    // would refuse is a store that turns one bad payload into a phone with no
    // copy at all, and `seq_baseline_store.dart` and `history_store.dart` both
    // pay for this check already. Here it is cheaper than either — the parser is
    // the production one, so "writable" and "readable" are not two predicates
    // that can drift but literally the same call.
    final payload = PhonePayload.fromJsonString(source);
    if (payload.pairingId.isEmpty) {
      // **Reachable, and I checked rather than assumed.** `PhonePayload`'s
      // `_string` helper rejects a non-string and accepts `""`, so a payload
      // carrying an empty `pairing_id` parses. `read` scopes on that value, so
      // storing one would fill this store's single slot with a copy no pairing
      // can ever match — read back as damaged, since an empty stored pairing is
      // indistinguishable from a missing one. `FetchDiagnostics._checkPairing`
      // refuses the same value for the same reason; this is that rule at the
      // other store's door.
      throw const PayloadFormatException('held copy has no pairing_id');
    }
    final file = await open();
    await file.parent.create(recursive: true);
    // Write-then-rename, so a power loss mid-write leaves the *previous* copy
    // intact rather than a truncated one. The copy is what the phone shows when
    // it cannot fetch, so the failure this ordering prevents — an interrupted
    // write costing the owner the screen he still had — is the one that hurts.
    final temporary = File('${file.path}.writing');
    await temporary.writeAsString(source, flush: true);
    await temporary.rename(file.path);
  }
}
