#!/usr/bin/env python3
"""Seal a shipped app fixture into a section-6.1 envelope for the app's tests.

`app/test/payload_envelope_test.dart` opens a **host-produced** envelope and
checks it recovers the shipped fixture byte for byte.  That is the only place
the two hand-written AES-256-GCM implementations — `networth/payload.py` and
`app/lib/src/domain/aes_gcm.dart` — are checked against each other rather than
each against the standard, so the envelope it reads has to come from here and
not from anything on the Dart side.

Regenerate after editing `app/assets/fixtures/known.json`; the test fails until
you do, which is the intended direction.  It compares the opened payload with
the fixture as it is on disk *now*, so a stale envelope shows up as a
disagreement rather than as a test that quietly still passes against last
month's shape.

    ./scripts/seal-app-test-envelope.py

The key and nonce are the fixed, non-secret test values below.  They are
`bytes(range(32))` and `bytes(range(12))` — the same shape `tests/test_payload.py`
uses — and they protect a fixture whose "net worth" is made up.  Nothing here
is a credential, and nothing here may ever be used by a build.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY))

from networth.payload import seal_payload  # noqa: E402

FIXTURE = REPOSITORY / "app" / "assets" / "fixtures" / "known.json"
FIXTURES = REPOSITORY / "app" / "test" / "fixtures"

TEST_KEY = bytes(range(32))
TEST_NONCE = bytes(range(12))
MISMATCH_NONCE = bytes(range(1, 13))
NO_TOTAL_NONCE = bytes(range(2, 14))


def _write(name: str, envelope: object) -> None:
    path = FIXTURES / name
    path.write_bytes(envelope.to_json() + b"\n")  # type: ignore[attr-defined]
    print(f"wrote {path.relative_to(REPOSITORY)}")


def main() -> int:
    body = FIXTURE.read_bytes()
    document = json.loads(body)

    _write(
        "known_envelope.json",
        seal_payload(
            body,
            TEST_KEY,
            schema_version=document["schema_version"],
            pairing_id=document["pairing_id"],
            seq=document["seq"],
            published_at=document["published_at"],
            nonce=TEST_NONCE,
        ),
    )

    # A **validly sealed** envelope whose clear header and encrypted body
    # disagree about `seq`.  Nothing short of the payload key can produce one —
    # the header is authenticated through the AAD, so an attacker cannot induce
    # this — which is exactly why it has to be generated here rather than
    # assembled by tampering in the test.  It is what a publisher bug looks like
    # on the wire, and `payload_envelope_test.dart` pins that the phone refuses
    # it instead of silently preferring one of the two numbers.
    _write(
        "seq_mismatch_envelope.json",
        seal_payload(
            body,
            TEST_KEY,
            schema_version=document["schema_version"],
            pairing_id=document["pairing_id"],
            seq=str(int(document["seq"]) + 1),
            published_at=document["published_at"],
            nonce=MISMATCH_NONCE,
        ),
    )

    # Authentic, header-consistent, and still not a payload: the `total` the
    # headline is computed from is gone.  This is the third of the three
    # distinct failures the transport has to tell apart — the tag did not
    # verify, the two headers disagreed, or the body is not a shape this build
    # reads — and only the last one says the phone reached the right host and
    # got something it cannot render.
    incomplete = {
        key: document[key] for key in ("schema_version", "pairing_id", "seq", "published_at")
    }
    incomplete["publish_interval_seconds"] = document["publish_interval_seconds"]
    incomplete["grace_seconds"] = document["grace_seconds"]
    incomplete["connection_state"] = document["connection_state"]
    _write(
        "no_total_envelope.json",
        seal_payload(
            json.dumps(incomplete, sort_keys=True, separators=(",", ":")).encode(),
            TEST_KEY,
            schema_version=document["schema_version"],
            pairing_id=document["pairing_id"],
            seq=document["seq"],
            published_at=document["published_at"],
            nonce=NO_TOTAL_NONCE,
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
