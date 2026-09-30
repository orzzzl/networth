import 'package:flutter/material.dart';

import '../../l10n/generated/app_localizations.dart';
import '../pairing/pairing_intake.dart';

/// The form that takes the bundle in — §6.3 step 2, typed.
///
/// **A text field, and that is the whole of v0.** §6.3 step 1 already renders the
/// bundle *"with a typed fallback string"* and [PairingProvision.parse] already
/// accepts it, so this row needs a form, the existing parser and the existing
/// vault: no new dependency and no camera permission. A scanner is the nicer
/// thing, costs a package, and is deferred beyond v0 — nothing here may read as
/// one (`tasks/README.md` `21a`, *must not*).
///
/// **Everything unusual below is because the field holds a payload key.** The
/// keyboard must not learn it, no draft of it may outlive the route, and it may
/// not appear in any message this page renders. Typing it locally is also
/// sufficient by construction — there is no share sheet, no clipboard helper and
/// no network call on this route, so the bundle never has to travel through
/// messaging or a cloud clipboard to arrive, which is the one thing §6.3 exists
/// to prevent.
class PairingIntakePage extends StatefulWidget {
  const PairingIntakePage({super.key, required this.intake, required this.isReplacement});

  final PairingIntake intake;

  /// Whether a pairing already exists, which changes the copy and nothing else.
  ///
  /// The owner about to replace a working pairing is told so before he does it;
  /// the write itself is the same write, and the vault holds one value under one
  /// key either way.
  final bool isReplacement;

  @override
  State<PairingIntakePage> createState() => _PairingIntakePageState();
}

class _PairingIntakePageState extends State<PairingIntakePage> {
  final _controller = TextEditingController();

  /// The last failure, or `null` before the first submission.
  ///
  /// Typed as the outcome rather than as a `String` so that adding a fourth
  /// outcome is a compile error in [build] instead of a sentence nobody wrote.
  PairingIntakeOutcome? _failure;

  /// Guards against a second submission while the first is in flight.
  ///
  /// Both halves matter: the button is disabled *and* [_submit] returns early.
  /// Disabling alone is a property of one frame — a double tap delivered before
  /// the rebuild, or a test pumping two taps, reaches the handler twice, and two
  /// provisions of the same bundle is the case where the second write lands
  /// after the screen has already moved on.
  bool _submitting = false;

  @override
  void dispose() {
    // Cleared before disposal, on every exit including the back button: the
    // controller's text is the bundle, and a disposed controller is not a
    // zeroed one.
    _controller.clear();
    _controller.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (_submitting) {
      return;
    }
    setState(() {
      _submitting = true;
      _failure = null;
    });
    final outcome = await widget.intake.submit(_controller.text);
    if (!mounted) {
      return;
    }
    switch (outcome) {
      case PairingIntakeStored():
        // Cleared here as well as in [dispose]: the route is about to pop, and
        // the pop is what triggers the caller's reload, so this is the earliest
        // point at which the text is certainly no longer needed.
        _controller.clear();
        Navigator.of(context).pop(true);
      case PairingIntakeUnreadable():
      case PairingIntakeNotStored():
        // **Stays on the form.** A failure that navigated away would be a screen
        // saying nothing happened while looking like something did; the owner's
        // next act is to retype or retry, and both are here.
        setState(() {
          _submitting = false;
          _failure = outcome;
        });
    }
  }

  @override
  Widget build(BuildContext context) {
    final l10n = AppLocalizations.of(context);
    final theme = Theme.of(context);
    final failure = switch (_failure) {
      PairingIntakeUnreadable() => l10n.pairingIntakeUnreadable,
      PairingIntakeNotStored() => l10n.pairingIntakeNotStored,
      PairingIntakeStored() || null => null,
    };
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.isReplacement ? l10n.pairingReplaceTitle : l10n.pairingIntakeTitle),
      ),
      body: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(l10n.pairingIntakeInstructions, style: theme.textTheme.bodyMedium),
              if (widget.isReplacement) ...[
                const SizedBox(height: 12),
                Text(l10n.pairingReplaceWarning, style: theme.textTheme.bodyMedium),
              ],
              const SizedBox(height: 24),
              TextField(
                key: const Key('pairing-bundle-field'),
                controller: _controller,
                enabled: !_submitting,
                // The keyboard must not keep it. `enableIMEPersonalizedLearning`
                // is the one that matters most and the one easiest to forget:
                // the other two stop the field suggesting, while this stops the
                // IME *retaining* what was typed, where it would outlive both
                // this route and the app.
                enableIMEPersonalizedLearning: false,
                enableSuggestions: false,
                autocorrect: false,
                // No `restorationId`, deliberately: state restoration would
                // write the in-progress bundle into the platform's restoration
                // data, which is a draft on disk by another name.
                autofillHints: null,
                maxLines: 4,
                minLines: 3,
                textInputAction: TextInputAction.done,
                onSubmitted: (_) => _submit(),
                decoration: InputDecoration(
                  labelText: l10n.pairingIntakeFieldLabel,
                  border: const OutlineInputBorder(),
                ),
              ),
              if (failure != null) ...[
                const SizedBox(height: 16),
                Row(
                  children: [
                    Icon(Icons.error_outline, color: theme.colorScheme.error),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        failure,
                        style: theme.textTheme.bodyMedium?.copyWith(
                          color: theme.colorScheme.error,
                        ),
                      ),
                    ),
                  ],
                ),
              ],
              const SizedBox(height: 24),
              FilledButton(
                key: const Key('pairing-submit'),
                onPressed: _submitting ? null : _submit,
                child: Text(l10n.pairingIntakeSubmit),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
