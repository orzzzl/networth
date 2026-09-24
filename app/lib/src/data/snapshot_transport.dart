import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../debug_log.dart';

/// The daemon's port, and the app cannot negotiate it.
///
/// It is `networth/listeners.py::SERVE_PORT`, duplicated here because the
/// pairing code deliberately carries *"the VPS full tailnet DNS name — and
/// nothing else"* (`DESIGN.md` §6.3). The QR has no port field to read, so the
/// two ends agree by both hardcoding it; changing one without the other breaks
/// every paired phone, which is why this names its counterpart rather than
/// standing alone as a number.
const int snapshotServePort = 8443;

/// What the phone will read from a `200` before giving up on it.
///
/// The envelope is one total plus a per-account list, so this is orders of
/// magnitude of headroom rather than a tight fit. It exists because
/// `Content-Length` is a *claim by the host*, and a client that sizes its buffer
/// from a claim has handed the other end its heap. The cap is applied to bytes
/// actually received, so a host that under-reports its length and then streams
/// forever is cut off at the same point as an honest one.
const int snapshotMaxBodyBytes = 1 << 20;

/// How long one whole attempt may take — connect, response, and body.
///
/// One deadline over all three phases rather than three, because the failure
/// this bounds is *"the app hangs on open"*, and that is caused by the total.
/// A host that completes its TCP handshake and then sends a header byte a minute
/// is inside any per-phase budget and still unusable.
const Duration snapshotFetchDeadline = Duration(seconds: 10);

/// Why an attempt produced no body — **what the transport observed**, not what
/// the owner should be told.
///
/// That distinction is the reason this enum exists next to §9.1's four error
/// classes instead of being them. `FetchFailureClass` answers *what should he do
/// about it* and its values are deliberately coarse; the mapping onto it is
/// lossy, and it is written once, in one named place, where it can be argued
/// with and tested. Classifying at the socket would spread that argument across
/// every `catch`.
enum SnapshotTransportFault {
  /// The tailnet name did not resolve.
  ///
  /// **On this transport that is close to evidence the phone is off the
  /// tailnet**, and the reason is measured rather than assumed: the pairing code
  /// carries a DNS *name*, and a `*.ts.net` name is served by MagicDNS, which is
  /// not answering when Tailscale is down. A phone on hotel Wi-Fi with the VPN
  /// off lands here, and the honest thing to tell the owner about it is not
  /// *"the host did not answer"* — the host is fine and he cannot see it.
  nameNotResolved,

  /// [snapshotFetchDeadline] expired, in any phase.
  timedOut,

  /// `ECONNREFUSED`. The address is live and answered the handshake with a
  /// reset: something is at that IP and nothing is on that port. The strongest
  /// single signal that the *route*, not the network, is what is down.
  connectionRefused,

  /// `EHOSTUNREACH`. A route existed and the host at the end of it did not.
  hostUnreachable,

  /// `ENETUNREACH` / `ENETDOWN`. The device itself has no path — the kernel
  /// refused before any packet left. Ordinary and self-correcting.
  networkUnreachable,

  /// The exchange started and then died: `ECONNRESET`, `ECONNABORTED`, or a body
  /// that ended before its framing said it would.
  ///
  /// Distinct from [connectionRefused] because it is the opposite diagnosis: a
  /// connection was established, so reachability is proven, and what failed
  /// happened after that.
  connectionLost,

  /// The body passed [snapshotMaxBodyBytes]. Refused rather than buffered.
  responseTooLarge,

  /// A `200` that is not this route's response: a `Content-Type` other than
  /// `application/json`, or bytes that are not UTF-8.
  ///
  /// **The case this is really for is a captive portal**, which is a normal
  /// thing for a phone to be behind. It returns `200` and an HTML login page for
  /// whatever was asked, so without this check the app's most likely encounter
  /// with a coffee shop would arrive at the envelope parser as a corrupt
  /// payload — pointing at the publisher for something the network did.
  notThisRoute,

  /// A socket or HTTP fault this classifier does not name.
  ///
  /// **Deliberately not folded into the nearest neighbour.** Every other value
  /// here is a claim the phone can defend; guessing one it cannot would put
  /// invented evidence into the one channel §11 leaves for host-side failure.
  unclassified,
}

/// The result of one `GET /snapshot`.
///
/// Sealed because two of the four non-body outcomes are **not failures** and a
/// caller must not be able to reach for a nullable body and treat them as one.
sealed class SnapshotFetchOutcome {
  const SnapshotFetchOutcome();
}

/// `200`, complete, within the cap, and `application/json`.
///
/// The body is **not parsed here**. The transport's job ends at "these are the
/// bytes the host sent"; authenticating and opening them belongs to the envelope
/// reader, which is where the payload key is.
final class SnapshotBodyReceived extends SnapshotFetchOutcome {
  const SnapshotBodyReceived(this.body);

  /// The §6.1 envelope document, verbatim.
  final String body;
}

/// `404` — **reached the daemon; it has no active publication.**
///
/// Its own outcome rather than an error, and that is the load-bearing decision
/// in this file. §9.1's `HOST_NOT_PUBLISHING` means *"reached the source;
/// nothing has been published since"* and is, per task `22`, *"how a host-side
/// failure reaches him at all"*. Recorded as a failed attempt this would break
/// the predicate's second conjunct (`last_fetch_attempt_at ==
/// last_fetch_success_at`), the reason would fall to `CANNOT_CHECK` — *"couldn't
/// check"* — and a dead publisher would be reported to the owner as a network
/// problem. The one fault the app exists to surface would be the one it
/// misattributes.
///
/// **What it does not distinguish, and cannot.** `serve.py` answers `404` when
/// no `is_active` envelope exists *and* when the joined pairing is not `ACTIVE`,
/// so a revoked pairing and a host that has never published are the same three
/// digits. §6.3 makes them nearly the same event anyway — revocation deletes the
/// envelope row in the same transaction — but they are different advice
/// (*re-pair* vs *your sync is broken*), and nothing in this response tells them
/// apart. Named here because the phone will have to decide it from its own
/// history, not from the wire.
final class SnapshotNoPublication extends SnapshotFetchOutcome {
  const SnapshotNoPublication();
}

/// `503` — reached the daemon and it could not read its own store.
///
/// `serve.py` sends this for `OSError`, `sqlite3.Error` and a stored envelope
/// that violates the wire contract, and deliberately sends no detail with it.
/// So this says *the host is up and broken*, which is not the same as either
/// [SnapshotNoPublication] or a transport fault.
final class SnapshotSourceUnavailable extends SnapshotFetchOutcome {
  const SnapshotSourceUnavailable();
}

/// A status the route does not define.
///
/// `serve.py` sends exactly `200`, `404`, `503` and `405`, and the app never
/// sends anything but `GET`, so reaching this means the phone is talking to
/// something that is not that route — or to a version of it that has grown a
/// status this build does not know. Redirects land here too, by construction:
/// this client does not follow them.
final class SnapshotUnexpectedStatus extends SnapshotFetchOutcome {
  const SnapshotUnexpectedStatus(this.statusCode);

  final int statusCode;
}

/// No usable response, with what the transport observed.
final class SnapshotTransportFailure extends SnapshotFetchOutcome {
  const SnapshotTransportFailure(this.fault);

  final SnapshotTransportFault fault;
}

/// The value Dart puts in `OSError.errorCode` for **its own** connect timeout.
///
/// Measured in the installed SDK (`_internal/vm/bin/socket_patch.dart`), which
/// hardcodes `110` on every platform but Windows with the comment `// ETIMEDOUT`.
/// That comment is true on Linux and false on the BSDs, where `ETIMEDOUT` is
/// `60` — so this constant is a *Dart* fact and is read before either table
/// below, never out of one.
const int _dartConnectTimeoutCode = 110;

/// `ECONNREFUSED`, `EHOSTUNREACH`, `ENETUNREACH`, `ENETDOWN` — per platform.
///
/// **Two tables because one would be silently wrong on one of them.** Read from
/// `errno.h` on both rather than recalled: every number this classifier cares
/// about is taken on the other platform by an unrelated error, so a single
/// merged table does not degrade, it misclassifies —
///
/// | | Linux / Android | macOS / iOS |
/// |---|---|---|
/// | `ECONNREFUSED` | 111 | 61, which is Linux's `ENODATA` |
/// | `EHOSTUNREACH` | 113 | 65, which is Linux's `ENOPKG` |
/// | `ENETUNREACH` | 101 | 51, which is Linux's `EL2HLT` |
/// | `ENETDOWN` | 100 | 50, which is Linux's `EBADE` |
///
/// The app ships to Android alone, so a Linux-only table would pass CI (which
/// runs `flutter test` on `ubuntu-latest`) and be wrong on every developer's Mac
/// — a test that cannot go red where it is written is the failure this repo has
/// already paid for once.
const Map<int, SnapshotTransportFault> _linuxFaults = {
  111: SnapshotTransportFault.connectionRefused,
  113: SnapshotTransportFault.hostUnreachable,
  101: SnapshotTransportFault.networkUnreachable,
  100: SnapshotTransportFault.networkUnreachable,
  110: SnapshotTransportFault.timedOut, // the kernel's own ETIMEDOUT
  104: SnapshotTransportFault.connectionLost,
  103: SnapshotTransportFault.connectionLost,
};

const Map<int, SnapshotTransportFault> _bsdFaults = {
  61: SnapshotTransportFault.connectionRefused,
  65: SnapshotTransportFault.hostUnreachable,
  51: SnapshotTransportFault.networkUnreachable,
  50: SnapshotTransportFault.networkUnreachable,
  60: SnapshotTransportFault.timedOut,
  54: SnapshotTransportFault.connectionLost,
  53: SnapshotTransportFault.connectionLost,
};

/// Which family's numbers `OSError.errorCode` is drawn from.
///
/// Injected as a value rather than read from [Platform] inside the classifier so
/// both tables are exercised wherever the suite runs. A table that is only
/// reachable on the CI runner is a table nobody can see fail.
enum ErrnoFamily {
  linux,
  bsd;

  static ErrnoFamily current() =>
      Platform.isAndroid || Platform.isLinux || Platform.isFuchsia
          ? ErrnoFamily.linux
          : ErrnoFamily.bsd;

  Map<int, SnapshotTransportFault> get _table =>
      this == ErrnoFamily.linux ? _linuxFaults : _bsdFaults;
}

/// Classify one [SocketException] without reading its message.
///
/// **Structural, not textual.** `OSError.message` is `strerror` output, so it is
/// the operating system's wording and in principle its locale; the outer
/// `message` is Dart's, and matching it would pin this app to SDK prose. The two
/// fields used instead were measured: on a name-resolution failure Dart raises
/// with `address` and `port` both `null`, and on a connect failure both are set,
/// so *"we never got as far as an address"* is readable off the exception's
/// shape.
///
/// The one overlap is the timeout, which also carries no address, and it is
/// separated by [_dartConnectTimeoutCode] before the lookup — deliberately
/// ordered, because `110` reaching the Linux table would be right by accident
/// and reaching the BSD table would be wrong.
SnapshotTransportFault classifySocketFault(
  SocketException exception, {
  required ErrnoFamily family,
}) {
  final code = exception.osError?.errorCode;
  if (code == _dartConnectTimeoutCode && exception.address == null) {
    return SnapshotTransportFault.timedOut;
  }
  if (exception.address == null) {
    // Resolution never produced one. `getaddrinfo` failures do not even share an
    // integer space across platforms — `EAI_NONAME` is `8` on macOS and `-2` on
    // Linux — which is the second reason this branch is decided on shape.
    return SnapshotTransportFault.nameNotResolved;
  }
  return family._table[code] ?? SnapshotTransportFault.unclassified;
}

/// Fetches the published envelope over the tailnet — the app's only outbound
/// request.
///
/// `DESIGN.md` §6.2/§6.3: a server bound to the VPS's tailnet interface serves
/// `GET /snapshot`, plain HTTP, because *"the tailnet link is already end-to-end
/// encrypted and the payload is encrypted underneath it"*. There is no bearer
/// token to send and no certificate to validate; reachability is Tailscale's
/// answer and the payload key is the read credential. So this class holds no
/// secret, and that is why it can be tested against a loopback server.
class SnapshotTransport {
  SnapshotTransport({
    required this.host,
    this.port = snapshotServePort,
    this.deadline = snapshotFetchDeadline,
    this.maxBodyBytes = snapshotMaxBodyBytes,
    HttpClient Function()? openClient,
    ErrnoFamily? family,
  })  : _openClient = openClient ?? HttpClient.new,
        _family = family ?? ErrnoFamily.current();

  /// The VPS's full tailnet DNS name, from the pairing bundle.
  ///
  /// **Full name, never a prefix.** The owner's tailnet holds four MacBook Airs
  /// whose names differ only by suffix (`DESIGN.md` §5), and the rule that came
  /// out of that is repo-wide.
  final String host;
  final int port;
  final Duration deadline;
  final int maxBodyBytes;

  final HttpClient Function() _openClient;
  final ErrnoFamily _family;

  Uri get _url => Uri(scheme: 'http', host: host, port: port, path: '/snapshot');

  /// One attempt. **Never throws** — every outcome is a value.
  ///
  /// That is a requirement rather than politeness: §9.1 has the phone persist
  /// `last_fetch_attempt_at` *"success or not"*, so an attempt that escaped as an
  /// exception would be an attempt the record never learned about, and the
  /// predicate would go on comparing the previous attempt's facts as though
  /// nothing had been tried.
  Future<SnapshotFetchOutcome> fetch() async {
    final client = _openClient();
    try {
      return await _attempt(client).timeout(deadline);
    } on TimeoutException {
      return const SnapshotTransportFailure(SnapshotTransportFault.timedOut);
    } on SocketException catch (error) {
      return SnapshotTransportFailure(
        classifySocketFault(error, family: _family),
      );
    } on HttpException catch (error) {
      // Malformed status line, truncated chunked body, connection closed
      // mid-response: the exchange began and did not finish.
      debugLog(() => 'snapshot fetch: http fault: $error');
      return const SnapshotTransportFailure(SnapshotTransportFault.connectionLost);
    } on TlsException catch (error) {
      debugLog(() => 'snapshot fetch: tls fault on a plaintext route: $error');
      return const SnapshotTransportFailure(SnapshotTransportFault.unclassified);
    } finally {
      // Closes the idle connection pool too, so a wedged host cannot leave a
      // socket behind for every launch.
      client.close(force: true);
    }
  }

  Future<SnapshotFetchOutcome> _attempt(HttpClient client) async {
    client.connectionTimeout = deadline;
    final request = await client.getUrl(_url);
    // **Not followed, deliberately.** `HttpClient` follows by default, and a
    // redirect is the one way a host on the tailnet could send this request
    // somewhere the owner never paired with. A `30x` becomes an unexpected
    // status instead, which is the truth about it.
    request.followRedirects = false;
    final response = await request.close();

    switch (response.statusCode) {
      case HttpStatus.ok:
        return _readBody(response);
      case HttpStatus.notFound:
        await _discard(response);
        return const SnapshotNoPublication();
      case HttpStatus.serviceUnavailable:
        await _discard(response);
        return const SnapshotSourceUnavailable();
      default:
        final status = response.statusCode;
        await _discard(response);
        return SnapshotUnexpectedStatus(status);
    }
  }

  Future<SnapshotFetchOutcome> _readBody(HttpClientResponse response) async {
    if (response.headers.contentType?.mimeType != ContentType.json.mimeType) {
      await _discard(response);
      return const SnapshotTransportFailure(SnapshotTransportFault.notThisRoute);
    }

    final builder = BytesBuilder(copy: false);
    await for (final chunk in response) {
      builder.add(chunk);
      // Checked per chunk, so the refusal costs one chunk of memory rather than
      // the whole claim. Reading to the end and *then* measuring would make the
      // cap a report instead of a limit.
      if (builder.length > maxBodyBytes) {
        // **Not drained**, unlike every other early return here — and the
        // reason is not the one it looks like. Draining *would* be wrong in
        // principle, since reading the rest of a body the cap just refused
        // turns the limit into a report. But it is also not possible: this is
        // inside `await for`, so `drain` finds a stream that has already been
        // listened to and throws `Bad state` before reading a byte. An earlier
        // draft called `_discard` here and the only effect was a logged error
        // on a perfectly normal path, which is worse than either — it spends
        // the debug channel on a non-event.
        //
        // The connection is abandoned instead; `client.close(force: true)` in
        // `fetch` tears it down.
        return const SnapshotTransportFailure(
          SnapshotTransportFault.responseTooLarge,
        );
      }
    }

    try {
      return SnapshotBodyReceived(utf8.decode(builder.takeBytes()));
    } on FormatException {
      // Not text at all. The envelope parser would report this as a malformed
      // document, which would blame the publisher for something that never came
      // from it.
      return const SnapshotTransportFailure(SnapshotTransportFault.notThisRoute);
    }
  }

  /// Drain and drop, so the socket is returned rather than left half-read.
  ///
  /// Swallows its own errors on purpose: the outcome is already decided, and a
  /// host that dies while being ignored must not turn a `404` into a fault.
  Future<void> _discard(HttpClientResponse response) async {
    try {
      await response.drain<void>();
    } on Object catch (error) {
      debugLog(() => 'snapshot fetch: discard failed: $error');
    }
  }
}
