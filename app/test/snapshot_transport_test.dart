import 'dart:async';
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
