import '../debug_log.dart';
import 'pairing_provision.dart';
import 'pairing_vault.dart';

/// What came of handing the vault a typed bundle.
///
/// **Three cases, because two of them are failures that must not look alike.**
/// A bundle this app cannot parse and a phone whose protected storage refused
/// the write are both "you are not paired yet", and telling the owner to check
/// his typing when the keystore is the thing that failed sends him to retype a
/// 100-character secret that was already correct.
sealed class PairingIntakeOutcome {
  const PairingIntakeOutcome();
}

/// Parsed, written to protected storage, and the write was awaited.
///
/// Carries nothing on purpose. The `pairing_id` and the tailnet name are both in
/// hand at this point and neither is something a caller needs in order to
/// re-load: the screen's next act is to read the vault again, which is the copy
/// of that fact that cannot be stale.
final class PairingIntakeStored extends PairingIntakeOutcome {
  const PairingIntakeStored();
}

/// Not a v1 bundle, so nothing was written and a previous pairing still stands.
///
/// [PairingVault.provision] parses before it writes, which is what makes that
/// second sentence true rather than hopeful — see the ordering test.
final class PairingIntakeUnreadable extends PairingIntakeOutcome {
  const PairingIntakeUnreadable();
}

/// The bundle was good and this device could not keep it.
///
/// Recoverable, and distinct from [PairingIntakeUnreadable] for the reason given
/// above. Whether the previous pairing survived is **not** claimed by this state:
/// the write is one `flutter_secure_storage` call and what it did before failing
/// belongs to the Android keystore, so the honest thing on screen is that this
/// phone is not paired *now* and the owner should try again.
final class PairingIntakeNotStored extends PairingIntakeOutcome {
  const PairingIntakeNotStored();
}

/// The half of §6.3 the app never had: a way to be *given* the bundle.
///
/// `19a` built the host side — `networth pair` renders the bundle with a typed
/// fallback string — and nothing in `app/lib/` called [PairingVault.provision],
/// so a build with a real transport could not be paired at all and rendered
/// *"not paired"* permanently (`tasks/README.md` `21a`).
///
/// Thin on purpose. The parse, the canonical encoding and the one protected key
/// are all the vault's, already built and already tested; what did not exist was
/// a caller that turns the three ways this can end into three values a screen
/// can render without inspecting an exception.
final class PairingIntake {
  const PairingIntake({required this.vault});

  final PairingVault vault;

  /// Never throws: every outcome is a sentence the form can show.
  ///
  /// The submitted text appears in no log, no exception and no return value.
  /// [PairingProvisionFormatException] is safe to log because its messages are
  /// fixed strings and it reports `source => null` deliberately; the second catch
  /// is the one that had to be written differently, and the reason is below.
  Future<PairingIntakeOutcome> submit(String typed) async {
    try {
      await vault.provision(typed);
      return const PairingIntakeStored();
    } on PairingProvisionFormatException catch (error) {
      debugLog(() => 'pairing intake rejected: ${error.message}');
      return const PairingIntakeUnreadable();
    } on Object catch (error) {
      // **The error object is deliberately not logged, only its type.** Every
      // other catch in this app logs the exception, and this is the one place
      // that would be a leak rather than a diagnostic: the write is the one
      // call that crosses a platform channel *holding the bundle*, so a plugin
      // that quotes its argument back — `PlatformException(message: "could not
      // write <value>")` is an ordinary shape — would put the payload key in
      // logcat under a name that reads like caution. The type separates a
      // keystore refusal from a missing plugin, which is all a reader needs.
      debugLog(() => 'pairing intake could not store: ${error.runtimeType}');
      return const PairingIntakeNotStored();
    }
  }
}
