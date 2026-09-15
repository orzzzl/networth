"""Task 19's cryptographic envelope and canonical AAD contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from networth.payload import (
    PayloadAuthenticationError,
    PayloadEnvelope,
    PayloadEnvelopeError,
    aes_256_gcm_open,
    aes_256_gcm_seal,
    encode_aad,
    open_payload,
    seal_payload,
)

KEY = bytes(range(32))
NONCE = bytes(range(12))
HEADER = {
    "schema_version": "1",
    "pairing_id": "pair-synthetic",
    "seq": "137",
    "published_at": "2026-08-30T09:00:00Z",
}


@pytest.mark.parametrize(
    ("plaintext", "expected"),
    [
        (b"", "530f8afbc74536b9a963b4f1c4cb738b"),
        (
            bytes(16),
            "cea7403d4d606b6e074ec5d3baf39d18d0d1c8a799996bf0265b98b5d48ab919",
        ),
    ],
)
def test_aes_256_gcm_matches_nist_vectors(plaintext: bytes, expected: str) -> None:
    key = bytes(32)
    nonce = bytes(12)

    sealed = aes_256_gcm_seal(plaintext, key, nonce=nonce, aad=b"")

    assert sealed.hex() == expected
    assert aes_256_gcm_open(sealed, key, nonce=nonce, aad=b"") == plaintext


def test_aes_256_gcm_matches_nist_vector_with_aad() -> None:
    """CAVP gcmEncryptExtIV256: 128-bit plaintext, AAD and tag, Count 0."""

    key = bytes.fromhex("92e11dcdaa866f5ce790fd24501f92509aacf4cb8b1339d50c9c1240935dd08b")
    nonce = bytes.fromhex("ac93a1a6145299bde902f21a")
    plaintext = bytes.fromhex("2d71bcfa914e4ac045b2aa60955fad24")
    aad = bytes.fromhex("1e0889016f67601c8ebea4943bc23ad6")
    expected = bytes.fromhex("8995ae2e6df3dbf96fac7b7137bae67feca5aa77d51d4a0a14d9c51e1da474ab")

    sealed = aes_256_gcm_seal(plaintext, key, nonce=nonce, aad=aad)

    assert sealed == expected
    assert aes_256_gcm_open(sealed, key, nonce=nonce, aad=aad) == plaintext


def test_aad_is_the_exact_length_delimited_utf8_tuple() -> None:
    aad = encode_aad(
        schema_version="1",
        pairing_id="pair-α",
        seq="137",
        published_at="2026-08-30T09:00:00Z",
    )

    # ``pair-α`` is six code points but seven UTF-8 bytes. This literal pins
    # both uint64 byte lengths and field order, not merely a round trip through
    # the same encoder.
    assert aad.hex() == (
        "000000000000000131"
        "0000000000000007706169722dceb1"
        "0000000000000003313337"
        "0000000000000014323032362d30382d33305430393a30303a30305a"
    )


def test_wire_json_round_trips_with_decimal_sequence_and_unpadded_base64url() -> None:
    envelope = seal_payload(b'{"synthetic":true}', KEY, nonce=NONCE, **HEADER)

    encoded = envelope.to_json()
    reopened = PayloadEnvelope.from_json(encoded)

    assert reopened == envelope
    assert b'"seq":"137"' in encoded
    assert b"=" not in encoded
    assert open_payload(reopened, KEY) == b'{"synthetic":true}'


@pytest.mark.parametrize("field", ["schema_version", "pairing_id", "seq", "published_at"])
def test_each_clear_header_field_is_authenticated(field: str) -> None:
    envelope = seal_payload(b"synthetic payload", KEY, nonce=NONCE, **HEADER)
    if field == "schema_version":
        changed = replace(envelope, schema_version="2")
    elif field == "pairing_id":
        changed = replace(envelope, pairing_id="pair-other")
    elif field == "seq":
        changed = replace(envelope, seq="138")
    else:
        changed = replace(envelope, published_at="2026-08-30T09:00:01Z")

    with pytest.raises(PayloadAuthenticationError, match="authentication failed"):
        open_payload(changed, KEY)


@pytest.mark.parametrize("field", ["nonce", "payload"])
def test_nonce_and_ciphertext_tag_are_authenticated(field: str) -> None:
    envelope = seal_payload(b"synthetic payload", KEY, nonce=NONCE, **HEADER)
    changed_bytes = bytearray(getattr(envelope, field))
    changed_bytes[-1] ^= 1
    changed = (
        replace(envelope, nonce=bytes(changed_bytes))
        if field == "nonce"
        else replace(envelope, payload=bytes(changed_bytes))
    )

    with pytest.raises(PayloadAuthenticationError, match="authentication failed"):
        open_payload(changed, KEY)


def test_wrong_key_never_returns_plaintext() -> None:
    envelope = seal_payload(b"synthetic payload", KEY, nonce=NONCE, **HEADER)

    with pytest.raises(PayloadAuthenticationError, match="authentication failed"):
        open_payload(envelope, bytes(reversed(KEY)))


@pytest.mark.parametrize("field", ["schema_version", "seq"])
@pytest.mark.parametrize("value", ["0", "01", "-1", "1.0"])
def test_decimal_header_fields_have_one_canonical_spelling(field: str, value: str) -> None:
    values = dict(HEADER)
    values[field] = value

    with pytest.raises(PayloadEnvelopeError, match="canonical positive decimal"):
        seal_payload(b"synthetic", KEY, nonce=NONCE, **values)


def test_envelope_decoder_rejects_extra_fields_and_padded_base64() -> None:
    envelope = seal_payload(b"synthetic", KEY, nonce=NONCE, **HEADER).as_mapping()
    with_extra = {**envelope, "ignored": "field"}
    padded = {**envelope, "nonce": envelope["nonce"] + "="}

    with pytest.raises(PayloadEnvelopeError, match="wrong fields"):
        PayloadEnvelope.from_mapping(with_extra)
    with pytest.raises(PayloadEnvelopeError, match="unpadded base64url"):
        PayloadEnvelope.from_mapping(padded)
