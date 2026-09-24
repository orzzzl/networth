import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/held_copy_store.dart';
import 'package:networth_app/src/domain/held_copy.dart';
import 'package:networth_app/src/domain/payload_format_exception.dart';
import 'package:networth_app/src/domain/phone_payload.dart';

void main() {
  late Directory directory;
  late File file;
  late FileHeldCopyStore store;

  setUp(() async {
    directory = await Directory.systemTemp.createTemp('held-copy-test');
    file = File('${directory.path}/${FileHeldCopyStore.fileName}');
    store = FileHeldCopyStore(open: () async => file);
  });

  tearDown(() async => directory.delete(recursive: true));

  /// The shipped fixture, read off disk rather than pasted here.
  ///
  /// Same reasoning as `fixtures.dart`: a copy of the payload inside the test
  /// would keep passing after the real document's shape moved, and this store's
  /// entire claim is that what it writes is what the transport parses.
  Future<String> shippedPayload() => File('assets/fixtures/known.json').readAsString();

  /// [source] with one top-level field replaced, re-encoded.
  String withField(String source, String field, Object? value) {
    final body = jsonDecode(source) as Map<String, Object?>;
    body[field] = value;
    return jsonEncode(body);
  }

  group('a pairing holding no copy', () {
    test('reads as absent when nothing has ever been held', () async {
      expect(await store.read('fixture-pairing'), isA<HeldCopyAbsent>());
    });

    test('reads as absent when the stored copy belongs to another pairing', () async {
      // Re-pairing rotates the payload key, so the copy the previous pairing
      // left is a picture of a relationship that has ended. Absent rather than
      // shown, and absent rather than damaged: nothing is wrong with it.
      await store.hold(await shippedPayload());

      expect(await store.read('a-different-pairing'), isA<HeldCopyAbsent>());
      expect(await store.read('fixture-pairing'), isA<HeldCopyHeld>());
    });
  });

  group('holding and reading back', () {
    test('a held copy reads back as the payload that was held', () async {
      final source = await shippedPayload();
      await store.hold(source);

      final state = await store.read('fixture-pairing');

      final held = (state as HeldCopyHeld).payload;
      final parsed = PhonePayload.fromJsonString(source);
      expect(held.pairingId, parsed.pairingId);
      expect(held.seq, parsed.seq);
      expect(held.publishedAt, parsed.publishedAt);
      expect(held.publishInterval, parsed.publishInterval);
      expect(held.grace, parsed.grace);
      expect(held.connectionState, parsed.connectionState);
      expect(held.total.amount, parsed.total.amount);
      expect(held.alerts.length, parsed.alerts.length);
    });

    test('the bytes on disk are the payload verbatim', () async {
      // **Asserted against the literal string, not through a round trip.** A
      // round trip cannot see the stored format at all — encode and decode
      // sharing one table agree with each other no matter what the table says —
      // and the format is the whole claim here: this file is the payload
      // document, so a later "improvement" that wraps it in an envelope of this
      // store's own has to break a test rather than pass one.
      final source = await shippedPayload();
      await store.hold(source);

      expect(await file.readAsString(), source);
    });

    test('holding again replaces the copy rather than accumulating', () async {
      final source = await shippedPayload();
      await store.hold(source);
      await store.hold(withField(source, 'seq', '42'));

      final held = (await store.read('fixture-pairing') as HeldCopyHeld).payload;
      expect(held.seq, '42');
    });

    test('leaves no partial file beside the copy', () async {
      await store.hold(await shippedPayload());

      final names = directory.listSync().map((entry) => entry.path.split('/').last).toList();
      expect(names, <String>[FileHeldCopyStore.fileName]);
    });
  });

  group('a copy this build cannot read is never absence', () {
    test('bytes that are not JSON read as unreadable', () async {
      await file.writeAsString('{ not json');

      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });

    test('JSON that is not an object reads as unreadable', () async {
      await file.writeAsString('[]');

      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });

    test('a copy with no pairing_id reads as unreadable, not as another pairing', () async {
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'pairing_id', ''));

      // The tempting shortcut is to treat a missing pairing as "not mine" and
      // answer absent. That would report a damaged file as a fresh install.
      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });

    test('a copy with no schema_version reads as unreadable, not as outdated', () async {
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'schema_version', null));

      // Outdated is a claim that the document is intact and speaks another
      // version. A document with no version at all supports no such claim.
      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });

    test('a well-formed payload with a field this build refuses reads as unreadable', () async {
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'connection_state', 'NOT_A_STATE'));

      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });

    test('the reason never carries the stored document', () async {
      // `PayloadFormatException` interpolates the offending *value* —
      // `unknown connection_state "$value"` — and the reason is on the path to
      // the screen. This file is a decrypted payload, so a reason that quotes it
      // is how real figures reach a surface that was only ever meant to say what
      // went wrong.
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'connection_state', 'SECRET-LOOKING-VALUE'));

      final state = await store.read('fixture-pairing') as HeldCopyUnreadable;
      expect(state.reason, isNot(contains('SECRET-LOOKING-VALUE')));
      expect(state.reason, isNot(contains('4250000')));
    });

    test('a file that cannot be read at all is unreadable rather than absent', () async {
      // A directory where the copy should be: `readAsString` fails and
      // `exists()` answers false, which is exactly the inference `stored_file`
      // exists to refuse.
      await Directory(file.path).create(recursive: true);

      expect(await store.read('fixture-pairing'), isA<HeldCopyUnreadable>());
    });
  });

  group('a copy written by another build', () {
    test('an older schema_version is outdated, not damaged', () async {
      // The state a phone is in the first time it launches a new APK — and the
      // per-account label work bumps `schema_version`, so this is the next
      // release rather than a hypothetical. Reporting it as damage would raise
      // an alarm about a routine upgrade on every phone that had ever fetched.
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'schema_version', '0'));

      final state = await store.read('fixture-pairing');
      expect(state, isA<HeldCopyOutdated>());
      expect(state, isNot(isA<HeldCopyUnreadable>()));
      expect((state as HeldCopyOutdated).storedVersion, '0');
      expect(state.readableVersion, PhonePayload.supportedSchemaVersion);
    });

    test('a newer schema_version is outdated too', () async {
      // An APK rolled back. Same inability, same self-healing answer; the state
      // names which version it found so a log can tell the two apart.
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'schema_version', '99'));

      expect((await store.read('fixture-pairing') as HeldCopyOutdated).storedVersion, '99');
    });

    test('another pairing wins over an unreadable version', () async {
      // Pairing is checked first: a copy from a pairing that has ended is not
      // this pairing's business at any schema, and answering "outdated" would
      // describe a document this build would never have shown anyway.
      final source = await shippedPayload();
      await file.writeAsString(withField(source, 'schema_version', '0'));

      expect(await store.read('a-different-pairing'), isA<HeldCopyAbsent>());
    });
  });

  group('what it refuses to write', () {
    test('refuses bytes it could not read back', () async {
      await expectLater(() => store.hold('{ not json'), throwsA(isA<PayloadFormatException>()));

      expect(file.existsSync(), isFalse);
    });

    test('refuses a payload whose pairing_id is empty', () async {
      // Parses — `_string` accepts `''` — and would occupy the one slot this
      // store has with a copy no pairing can match.
      final source = withField(await shippedPayload(), 'pairing_id', '');

      await expectLater(() => store.hold(source), throwsA(isA<PayloadFormatException>()));
      expect(file.existsSync(), isFalse);
    });

    test('the copy is committed by rename, so an interrupted write cannot truncate it', () async {
      // **Pins the mechanism, because the crash it prevents cannot be
      // injected.** The property that matters is that the destination always
      // holds one complete document — the old one or the new one — so a power
      // loss mid-write costs the owner nothing. Nothing in this suite can
      // interrupt a write, and a mutation replacing the temp-file-and-rename
      // with a direct `writeAsString` survived every other test here.
      //
      // So the discriminator is deterministic instead: occupy the temporary
      // name with a directory. An implementation that stages its bytes there
      // fails and leaves the previous copy standing; one that writes straight
      // to the destination sails past and replaces it. The suffix is named
      // here because `seq_baseline_store.dart` established it as this
      // directory's convention, not because this test invented it.
      final good = await shippedPayload();
      await store.hold(good);
      await Directory('${file.path}.writing').create();

      await expectLater(() => store.hold(withField(good, 'seq', '42')), throwsA(isA<Object>()));

      expect(await file.readAsString(), good);
    });

    test('a refused write leaves the previous copy intact', () async {
      // The copy is what the phone shows when it cannot fetch, so a bad payload
      // must not cost the owner the screen he still had.
      final good = await shippedPayload();
      await store.hold(good);

      await expectLater(
        () => store.hold(withField(good, 'connection_state', 'NOT_A_STATE')),
        throwsA(isA<PayloadFormatException>()),
      );

      expect(await file.readAsString(), good);
      expect(await store.read('fixture-pairing'), isA<HeldCopyHeld>());
    });
  });
}
