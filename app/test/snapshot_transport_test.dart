import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:networth_app/src/data/snapshot_transport.dart';

/// A real HTTP server on the loopback, because the thing under test *is* the
/// transport.
///
/// A mocked `HttpClient` would have let this file assert that the code calls the
/// methods it calls. What needs pinning is what happens to an actual response —
/// a `Content-Length` that lies, a socket that dies mid-body, a status the route
/// never defines — and none of that is expressible against a mock without
/// re-implementing the HTTP client inside the test.
class _Route {
  _Route(this._handle);

  final Future<void> Function(HttpRequest request) _handle;

  late HttpServer _server;
  final List<String> paths = <String>[];

  Future<int> start() async {
    _server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    _server.listen((request) async {
      paths.add(request.uri.toString());
      try {
        await _handle(request);
      } on Object {
        // A handler that deliberately destroys its own socket must not take the
        // server down with it.
      }
    });
    return _server.port;
  }

  HttpConnectionsInfo connections() => _server.connectionsInfo();

  Future<void> stop() => _server.close(force: true);
}

Future<SnapshotFetchOutcome> _fetchFrom(
  _Route route, {
  Duration deadline = const Duration(seconds: 5),
  int maxBodyBytes = snapshotMaxBodyBytes,
}) async {
  final port = await route.start();
  addTearDown(route.stop);
  final transport = SnapshotTransport(
    host: InternetAddress.loopbackIPv4.address,
    port: port,
    deadline: deadline,
    maxBodyBytes: maxBodyBytes,
  );
  return transport.fetch();
}

void _sendJson(HttpRequest request, String body) {
  request.response
    ..statusCode = HttpStatus.ok
    ..headers.contentType = ContentType.json;
  request.response.write(body);
}

void main() {
  group('the route the daemon actually serves', () {
    test('200 with an application/json body yields the bytes verbatim', () async {
      const document = '{"schema_version":"1","seq":"7"}';
      final route = _Route((request) async {
        _sendJson(request, document);
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      expect(outcome, isA<SnapshotBodyReceived>());
      expect((outcome as SnapshotBodyReceived).body, document);
    });

    test('the request goes to exactly /snapshot', () async {
      final route = _Route((request) async {
        _sendJson(request, '{}');
        await request.response.close();
      });

      await _fetchFrom(route);

      expect(route.paths, <String>['/snapshot']);
    });

    test('the default port is the daemon\'s SERVE_PORT', () {
      // Pinned as a literal rather than compared to the constant, which would
      // compare the constant to itself. The counterpart is
      // `networth/listeners.py::SERVE_PORT`, and the pairing code carries no
      // port for these two to disagree through.
      expect(snapshotServePort, 8443);
    });

    test('404 is a reach with nothing published, NOT a failure', () async {
      final route = _Route((request) async {
        request.response.statusCode = HttpStatus.notFound;
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      // The distinction this whole file exists for: recorded as a failure, a
      // dead publisher reaches the owner as "couldn't check".
      expect(outcome, isA<SnapshotNoPublication>());
      expect(outcome, isNot(isA<SnapshotTransportFailure>()));
    });

    test('503 is the host up and unable to read its own store', () async {
      final route = _Route((request) async {
        request.response.statusCode = HttpStatus.serviceUnavailable;
        await request.response.close();
      });

      expect(await _fetchFrom(route), isA<SnapshotSourceUnavailable>());
    });

    test('a status the route does not define is carried, not collapsed', () async {
      final route = _Route((request) async {
        request.response.statusCode = HttpStatus.internalServerError;
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      expect(outcome, isA<SnapshotUnexpectedStatus>());
      expect((outcome as SnapshotUnexpectedStatus).statusCode,
          HttpStatus.internalServerError);
    });

    test('a redirect is not followed', () async {
      var served = 0;
      final route = _Route((request) async {
        served += 1;
        if (request.uri.path == '/snapshot') {
          request.response
            ..statusCode = HttpStatus.movedPermanently
            ..headers.set(HttpHeaders.locationHeader, '/elsewhere');
          await request.response.close();
          return;
        }
        _sendJson(request, '{"not":"ours"}');
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      // Following it would have produced a perfectly valid-looking body from a
      // location the owner never paired with.
      expect(outcome, isA<SnapshotUnexpectedStatus>());
      expect((outcome as SnapshotUnexpectedStatus).statusCode,
          HttpStatus.movedPermanently);
      expect(served, 1);
      expect(route.paths, <String>['/snapshot']);
    });
  });

  group('a 200 that did not come from this route', () {
    test('an HTML body under a 200 is refused as notThisRoute', () async {
      // The captive-portal shape: the network answers for the host.
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.html
          ..write('<html><body>Sign in to continue</body></html>');
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      expect(outcome, isA<SnapshotTransportFailure>());
      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.notThisRoute);
    });

    test('a 200 with no Content-Type at all is refused', () async {
      final route = _Route((request) async {
        request.response.statusCode = HttpStatus.ok;
        request.response.headers.removeAll(HttpHeaders.contentTypeHeader);
        request.response.write('{}');
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.notThisRoute);
    });

    test('bytes that are not UTF-8 are a transport fault, not a bad payload',
        () async {
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          // A lone continuation byte: never valid UTF-8 anywhere in a document.
          ..add(<int>[0x7b, 0x80, 0x7d]);
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      // Blaming the publisher for this would be blaming it for the network's
      // interference.
      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.notThisRoute);
    });
  });

  group('the body is bounded by what arrives, not by what is claimed', () {
    test('a body over the cap is refused', () async {
      final route = _Route((request) async {
        _sendJson(request, 'x' * 4096);
        await request.response.close();
      });

      final outcome = await _fetchFrom(route, maxBodyBytes: 1024);

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.responseTooLarge);
    });

    test('a body exactly at the cap is accepted', () async {
      // The boundary in the accepting direction, so `>` cannot silently become
      // `>=` without a named test going red.
      final route = _Route((request) async {
        _sendJson(request, 'x' * 1024);
        await request.response.close();
      });

      final outcome = await _fetchFrom(route, maxBodyBytes: 1024);

      expect(outcome, isA<SnapshotBodyReceived>());
      expect((outcome as SnapshotBodyReceived).body.length, 1024);
    });

    test('a host that under-reports Content-Length and streams on is still cut off',
        () async {
      // The reason the cap counts received bytes rather than sizing a buffer
      // from the header: this host's claim is a lie in the dangerous direction.
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          ..headers.contentLength = -1;
        for (var i = 0; i < 64; i++) {
          request.response.add(List<int>.filled(1024, 0x20));
        }
        await request.response.close();
      });

      final outcome = await _fetchFrom(route, maxBodyBytes: 2048);

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.responseTooLarge);
    });

    test('refusing the body abandons it instead of reading it to the end',
        () async {
      // The assertion the outcome alone cannot make. Returning
      // `responseTooLarge` is correct whether the client stopped at the cap or
      // drained the rest first, so the *outcome* is blind to the difference —
      // and the difference is the whole feature: a drain would pull the
      // gigabyte the cap exists to refuse. Timing is the observable that can
      // tell them apart, so timing is what this asserts.
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          ..headers.contentLength = -1;
        for (var i = 0; i < 50; i++) {
          request.response.add(List<int>.filled(1024, 0x20));
          await request.response.flush();
          await Future<void>.delayed(const Duration(milliseconds: 100));
        }
        await request.response.close();
      });

      final started = DateTime.now();
      final outcome = await _fetchFrom(route, maxBodyBytes: 2048);
      final elapsed = DateTime.now().difference(started);

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.responseTooLarge);
      // The host needs ~5s to finish. The cap is passed by the third chunk, so
      // ~300ms; anything near the full send means the body was consumed.
      expect(elapsed, lessThan(const Duration(seconds: 2)),
          reason: 'the refused body was read to the end rather than abandoned');
    });
  });

  group('the attempt is bounded and always returns a value', () {
    test('a host that answers nothing times out rather than hanging', () async {
      final route = _Route((request) async {
        // Accept, and never respond.
        await Completer<void>().future;
      });

      final outcome = await _fetchFrom(
        route,
        deadline: const Duration(milliseconds: 300),
      );

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.timedOut);
    });

    test('a socket that dies mid-body is connectionLost, not a short read',
        () async {
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          ..headers.contentLength = 4096
          ..write('{"partial":');
        await request.response.flush();
        // Kill the connection with the declared body unfinished. Returning the
        // truncated prefix as a body would hand the envelope parser half a
        // document.
        await request.response.close().catchError((Object _) {});
        (await request.response.detachSocket(writeHeaders: false)).destroy();
      });

      final outcome = await _fetchFrom(route);

      expect(outcome, isA<SnapshotTransportFailure>());
      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.connectionLost);
    });

    test('nothing listening on the port is connectionRefused', () async {
      // A real ECONNREFUSED through the real classifier, which also means this
      // test goes red on whichever platform has the wrong errno table.
      final socket = await ServerSocket.bind(InternetAddress.loopbackIPv4, 0);
      final port = socket.port;
      await socket.close();

      final outcome = await SnapshotTransport(
        host: InternetAddress.loopbackIPv4.address,
        port: port,
        deadline: const Duration(seconds: 2),
      ).fetch();

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.connectionRefused);
    });

    test('a name that does not resolve is nameNotResolved', () async {
      // `.invalid` is reserved by RFC 2606 and never resolves, so this needs no
      // network and cannot accidentally reach a real host.
      final outcome = await SnapshotTransport(
        host: 'no-such-host.invalid',
        deadline: const Duration(seconds: 5),
      ).fetch();

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.nameNotResolved);
    });

    test('the connection is not left behind after a completed fetch', () async {
      final route = _Route((request) async {
        _sendJson(request, '{}');
        await request.response.close();
      });

      await _fetchFrom(route);
      // Give the close a turn to land on the server side.
      await Future<void>.delayed(const Duration(milliseconds: 100));

      final info = route.connections();
      expect(info.active, 0);
      expect(info.idle, 0);
    });
  });

  group('a body the client itself cannot decode is still a value', () {
    // `HttpClient` advertises `Accept-Encoding: gzip` on every request and
    // inflates the response before the app sees a byte, so the decoder sits
    // *inside* the stream this transport reads. A corrupt deflate member
    // therefore surfaces as a `FormatException` thrown by `await for` — not by
    // anything the body-reading code calls — which is a fifth way out of an
    // attempt that promises to produce only values.
    test('a malformed gzip body is a fault rather than a thrown exception',
        () async {
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          ..headers.set(HttpHeaders.contentEncodingHeader, 'gzip')
          // Announced as compressed and not compressed at all.
          ..add(utf8.encode('oops'));
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      // `notThisRoute` for the same reason an HTML body gets it: bytes that do
      // not match their own framing are the network's doing, and reporting them
      // as a publisher fault would put invented evidence in the one channel
      // §11 leaves for host-side failure.
      expect(outcome, isA<SnapshotTransportFailure>());
      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.notThisRoute);
    });

    test('a correctly gzipped body is still accepted', () async {
      // The control that keeps the fix from being "refuse compression". The
      // daemon does not compress today, but the client asks for it unprompted,
      // so a host that starts answering the ask must not break this app.
      const document = '{"schema_version":"1","seq":"9"}';
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.json
          ..headers.set(HttpHeaders.contentEncodingHeader, 'gzip')
          ..add(gzip.encode(utf8.encode(document)));
        await request.response.close();
      });

      final outcome = await _fetchFrom(route);

      expect(outcome, isA<SnapshotBodyReceived>());
      expect((outcome as SnapshotBodyReceived).body, document);
    });
  });

  group('an ignored body cannot overwrite a status already read', () {
    /// A host that sends its status, flushes one byte, and holds the body open.
    ///
    /// The shape that matters because the status arrived *first*: the transport
    /// has already decided, and anything the body does afterwards is news about
    /// a question no longer being asked.
    _Route neverEnding(void Function(HttpResponse response) respond) =>
        _Route((request) async {
          respond(request.response);
          request.response.headers.contentLength = -1;
          request.response.write('x');
          await request.response.flush();
          await Completer<void>().future;
        });

    /// Short enough that a red run is quick, long enough that returning inside
    /// [fastEnough] cannot be the deadline firing.
    const deadline = Duration(seconds: 2);
    const fastEnough = Duration(seconds: 1);

    Future<SnapshotFetchOutcome> fetchAndAssertPrompt(_Route route) async {
      final started = DateTime.now();
      final outcome = await _fetchFrom(route, deadline: deadline);
      // The outcome alone cannot tell "let the body go" from "waited out a
      // bounded drain", and the second one spends the attempt's whole budget on
      // a response it is discarding. Timing is the observable that separates
      // them, exactly as it is for the over-cap body above.
      expect(DateTime.now().difference(started), lessThan(fastEnough),
          reason: 'the ignored body was waited on rather than let go');
      return outcome;
    }

    test('404 survives a body that never ends', () async {
      final outcome = await fetchAndAssertPrompt(
        neverEnding((response) => response.statusCode = HttpStatus.notFound),
      );

      // The regression this group is named for: awaiting the drain under the
      // attempt's own deadline turned a read `404` into `timedOut`, so a host
      // with nothing published reached the owner as "couldn't check" — the one
      // misattribution the 404 outcome exists to prevent, arriving by a
      // different door than the one that was guarded.
      expect(outcome, isA<SnapshotNoPublication>());
    });

    test('503 survives a body that never ends', () async {
      final outcome = await fetchAndAssertPrompt(
        neverEnding(
          (response) => response.statusCode = HttpStatus.serviceUnavailable,
        ),
      );

      expect(outcome, isA<SnapshotSourceUnavailable>());
    });

    test('an unexpected status survives a body that never ends', () async {
      final outcome = await fetchAndAssertPrompt(
        neverEnding(
          (response) => response.statusCode = HttpStatus.internalServerError,
        ),
      );

      expect(outcome, isA<SnapshotUnexpectedStatus>());
      expect((outcome as SnapshotUnexpectedStatus).statusCode,
          HttpStatus.internalServerError);
    });

    test('a 200 that is not this route survives a body that never ends',
        () async {
      // The captive portal again, this time one that keeps the page open. The
      // content-type check has already decided; the endless body must not
      // relabel it as a timeout.
      final outcome = await fetchAndAssertPrompt(
        neverEnding((response) => response
          ..statusCode = HttpStatus.ok
          ..headers.contentType = ContentType.html),
      );

      expect((outcome as SnapshotTransportFailure).fault,
          SnapshotTransportFault.notThisRoute);
    });

    test('a 404 with an ordinary body is unaffected', () async {
      // The healthy control: the common case is a short body that ends, and
      // letting go of it must not change the outcome either.
      final route = _Route((request) async {
        request.response
          ..statusCode = HttpStatus.notFound
          ..write('no active publication');
        await request.response.close();
      });

      expect(await _fetchFrom(route), isA<SnapshotNoPublication>());
    });

    // **There is deliberately no "the socket is not left behind" test here**,
    // and the reason is measured rather than assumed. The obvious one —
    // `connectionsInfo().active == 0` after abandoning an endless body, like
    // the completed-fetch test above — reads `1` both before and after this
    // change, so it discriminates nothing. Probing it showed why: `active`
    // falls to zero when the *server's own handler* returns, and a handler that
    // holds the body open by construction never does. Two causes for one
    // absence, which is exactly when an absence stops being evidence.
    //
    // The property itself needs no new test: `close(force: true)` runs in
    // `fetch`'s `finally` on every path and is untouched by this change, so
    // teardown cannot have regressed with it. A probe writing 100 MiB into an
    // abandoned response saw the connection already released.
  });

  group('classifying a SocketException by shape, not by message', () {
    SocketException connectFailure(int code) => SocketException(
          'Connection failed',
          osError: OSError('whatever the OS called it', code),
          address: InternetAddress('100.64.0.1'),
          port: snapshotServePort,
        );

    test('Linux and BSD numbers are read from their own table', () {
      // The same integer means different things on the two platforms, so each
      // row is asserted against BOTH families. A single merged table passes the
      // first column and fails here.
      const cases = <int, (SnapshotTransportFault, SnapshotTransportFault)>{
        // errno: (linux meaning, bsd meaning)
        111: (SnapshotTransportFault.connectionRefused,
            SnapshotTransportFault.unclassified),
        113: (SnapshotTransportFault.hostUnreachable,
            SnapshotTransportFault.unclassified),
        101: (SnapshotTransportFault.networkUnreachable,
            SnapshotTransportFault.unclassified),
        100: (SnapshotTransportFault.networkUnreachable,
            SnapshotTransportFault.unclassified),
        61: (SnapshotTransportFault.unclassified,
            SnapshotTransportFault.connectionRefused),
        65: (SnapshotTransportFault.unclassified,
            SnapshotTransportFault.hostUnreachable),
        51: (SnapshotTransportFault.unclassified,
            SnapshotTransportFault.networkUnreachable),
        50: (SnapshotTransportFault.unclassified,
            SnapshotTransportFault.networkUnreachable),
      };

      for (final entry in cases.entries) {
        expect(
          classifySocketFault(connectFailure(entry.key),
              family: ErrnoFamily.linux),
          entry.value.$1,
          reason: 'errno ${entry.key} on Linux',
        );
        expect(
          classifySocketFault(connectFailure(entry.key), family: ErrnoFamily.bsd),
          entry.value.$2,
          reason: 'errno ${entry.key} on BSD',
        );
      }
    });

    test('an unknown errno is unclassified rather than a guessed neighbour', () {
      for (final family in ErrnoFamily.values) {
        expect(
          classifySocketFault(connectFailure(4242), family: family),
          SnapshotTransportFault.unclassified,
        );
      }
    });

    test('no address means resolution never produced one', () {
      // Measured shape: Dart raises a lookup failure with both `address` and
      // `port` null, and a connect failure with both set. The EAI code itself is
      // unusable — `EAI_NONAME` is 8 on macOS and -2 on Linux.
      for (final family in ErrnoFamily.values) {
        for (final code in <int>[8, -2, -3]) {
          expect(
            classifySocketFault(
              SocketException('Failed host lookup',
                  osError: OSError('nodename nor servname provided', code)),
              family: family,
            ),
            SnapshotTransportFault.nameNotResolved,
            reason: 'EAI $code on $family',
          );
        }
      }
    });

    test('Dart\'s own connect timeout is read before either table', () {
      // The SDK hardcodes 110 with the comment `// ETIMEDOUT`, which is true on
      // Linux and false on BSD, where ETIMEDOUT is 60. Reaching the BSD table
      // with 110 would have called it nameNotResolved.
      final dartTimeout = SocketException(
        'Connection timed out, host: x, port: 8443',
        osError: const OSError('Connection timed out', 110),
      );

      for (final family in ErrnoFamily.values) {
        expect(
          classifySocketFault(dartTimeout, family: family),
          SnapshotTransportFault.timedOut,
          reason: '$family',
        );
      }
    });

    test('the kernel\'s own ETIMEDOUT lands on timedOut in both families', () {
      expect(
        classifySocketFault(connectFailure(110), family: ErrnoFamily.linux),
        SnapshotTransportFault.timedOut,
      );
      expect(
        classifySocketFault(connectFailure(60), family: ErrnoFamily.bsd),
        SnapshotTransportFault.timedOut,
      );
    });

    test('this platform\'s family matches the errno space it will report', () {
      // The one assertion that ties the injected value back to reality: if
      // `current()` picks the wrong family, every real-socket test above is
      // asserting against the wrong table.
      expect(
        ErrnoFamily.current(),
        Platform.isMacOS || Platform.isIOS
            ? ErrnoFamily.bsd
            : ErrnoFamily.linux,
      );
    });
  });
}
