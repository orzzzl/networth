/// The demo entry point: the same app, wired to the synthetic history fixture.
///
/// ```sh
/// flutter run -t lib/main_demo.dart
/// ```
///
/// This exists so the two treatments §10.5 asks for — a dashed run across a gap
/// and a hollow marker on an incomplete reading — can be *looked at*, not only
/// asserted in a widget test. The fixture carries one of each on purpose.
///
/// **It is a separate entry point rather than a flag, so that `main.dart`
/// contains no reference to the synthetic series.** A flag would leave the
/// production entry point naming the fixture and relying on a default staying
/// false; a second `main` leaves nothing to flip. `FixtureHistorySource` also
/// refuses to load under `kReleaseMode`, so a release APK built from this file
/// renders "couldn't read the history" beside a real headline rather than
/// inventing a past.
library;

import 'package:flutter/material.dart';

import 'main.dart';
import 'src/data/history_source.dart';

void main() {
  runApp(
    const NetWorthApp(
      historySource: FixtureHistorySource('assets/fixtures/history.json'),
    ),
  );
}
