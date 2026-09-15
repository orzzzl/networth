"""The encrypted host-to-phone envelope from DESIGN section 6.1.

The project cannot add a runtime dependency unless a task explicitly permits
one.  This module therefore implements the small AES-256-GCM surface task 19
needs directly and pins it with NIST vectors in the test suite.  The public
surface remains the envelope contract: callers never handle AES blocks or GCM
state themselves.

The four clear-text header fields are authenticated through one canonical,
length-delimited byte string.  The nonce and ciphertext are stored as bytes in
SQLite; base64url exists only at the JSON serving boundary.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16

_DECIMAL = re.compile(r"[1-9][0-9]*\Z")
_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "pairing_id", "seq", "published_at", "nonce", "payload"}
)
_GCM_REDUCTION = 0xE1000000000000000000000000000000
_MAX_GCM_PAYLOAD_BYTES = ((1 << 32) - 2) * 16


class PayloadError(RuntimeError):
    """Base class for malformed or unauthenticated payload envelopes."""


class PayloadAuthenticationError(PayloadError):
    """The payload key or any authenticated byte did not match the tag."""


class PayloadEnvelopeError(PayloadError):
    """The clear envelope is not the exact section-6.1 wire shape."""


def _gf_byte_multiply(left: int, right: int) -> int:
    result = 0
    for _ in range(8):
        if right & 1:
            result ^= left
        high = left & 0x80
        left = (left << 1) & 0xFF
        if high:
            left ^= 0x1B
        right >>= 1
    return result


def _byte_power(value: int, exponent: int) -> int:
    result = 1
    factor = value
    while exponent:
        if exponent & 1:
            result = _gf_byte_multiply(result, factor)
        factor = _gf_byte_multiply(factor, factor)
        exponent >>= 1
    return result


def _rotate_byte(value: int, count: int) -> int:
    return ((value << count) | (value >> (8 - count))) & 0xFF


def _substitute_byte(value: int) -> int:
    inverse = 0 if value == 0 else _byte_power(value, 254)
    return (
        inverse
        ^ _rotate_byte(inverse, 1)
        ^ _rotate_byte(inverse, 2)
        ^ _rotate_byte(inverse, 3)
        ^ _rotate_byte(inverse, 4)
        ^ 0x63
    )


_SBOX = tuple(_substitute_byte(value) for value in range(256))


def _xor(left: bytes, right: bytes) -> bytes:
    return bytes(a ^ b for a, b in zip(left, right, strict=True))


def _round_keys(key: bytes) -> tuple[bytes, ...]:
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise ValueError("payload key must contain exactly 32 bytes")
    words = [list(key[offset : offset + 4]) for offset in range(0, KEY_BYTES, 4)]
    round_constant = 1
    while len(words) < 60:
        temporary = words[-1].copy()
        if len(words) % 8 == 0:
            temporary = [_SBOX[value] for value in (*temporary[1:], temporary[0])]
            temporary[0] ^= round_constant
            round_constant = _gf_byte_multiply(round_constant, 2)
        elif len(words) % 8 == 4:
            temporary = [_SBOX[value] for value in temporary]
        words.append([left ^ right for left, right in zip(words[-8], temporary, strict=True)])
    return tuple(
        bytes(value for word in words[offset : offset + 4] for value in word)
        for offset in range(0, len(words), 4)
    )


def _shift_rows(state: list[int]) -> list[int]:
    return [state[4 * ((column + row) % 4) + row] for column in range(4) for row in range(4)]


def _mix_columns(state: list[int]) -> list[int]:
    mixed: list[int] = []
    for offset in range(0, 16, 4):
        first, second, third, fourth = state[offset : offset + 4]
        mixed.extend(
            (
                _gf_byte_multiply(first, 2) ^ _gf_byte_multiply(second, 3) ^ third ^ fourth,
                first ^ _gf_byte_multiply(second, 2) ^ _gf_byte_multiply(third, 3) ^ fourth,
                first ^ second ^ _gf_byte_multiply(third, 2) ^ _gf_byte_multiply(fourth, 3),
                _gf_byte_multiply(first, 3) ^ second ^ third ^ _gf_byte_multiply(fourth, 2),
            )
        )
    return mixed


def _encrypt_block(block: bytes, round_keys: tuple[bytes, ...]) -> bytes:
    if len(block) != 16 or len(round_keys) != 15:
        raise ValueError("AES-256 requires one block and fifteen round keys")
    state = [value ^ key for value, key in zip(block, round_keys[0], strict=True)]
    for round_key in round_keys[1:-1]:
        state = [_SBOX[value] for value in state]
        state = _shift_rows(state)
        state = _mix_columns(state)
        state = [value ^ key for value, key in zip(state, round_key, strict=True)]
    state = [_SBOX[value] for value in state]
    state = _shift_rows(state)
    return bytes(value ^ key for value, key in zip(state, round_keys[-1], strict=True))


def _increment_counter(counter: bytes) -> bytes:
    value = (int.from_bytes(counter[-4:], "big") + 1) & 0xFFFFFFFF
    return counter[:12] + value.to_bytes(4, "big")


def _counter_xor(data: bytes, *, initial: bytes, round_keys: tuple[bytes, ...]) -> bytes:
    output = bytearray(len(data))
    counter = initial
    for offset in range(0, len(data), 16):
        counter = _increment_counter(counter)
        block = data[offset : offset + 16]
        mask = _encrypt_block(counter, round_keys)
        output[offset : offset + len(block)] = bytes(
            value ^ key for value, key in zip(block, mask, strict=False)
        )
    return bytes(output)


def _galois_multiply(left: int, right: int) -> int:
    product = 0
    factor = right
    for bit in range(127, -1, -1):
        if left & (1 << bit):
            product ^= factor
        factor = (factor >> 1) ^ (_GCM_REDUCTION if factor & 1 else 0)
    return product


def _blocks(value: bytes) -> tuple[bytes, ...]:
    return tuple(
        value[offset : offset + 16].ljust(16, b"\x00") for offset in range(0, len(value), 16)
    )


def _ghash(hash_subkey: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    if len(aad) >= 1 << 61 or len(ciphertext) >= 1 << 61:
        raise ValueError("GCM input is too long for its 64-bit length encoding")
    accumulator = 0
    multiplier = int.from_bytes(hash_subkey, "big")
    length_block = struct.pack(">QQ", len(aad) * 8, len(ciphertext) * 8)
    for block in (*_blocks(aad), *_blocks(ciphertext), length_block):
        accumulator = _galois_multiply(accumulator ^ int.from_bytes(block, "big"), multiplier)
    return accumulator.to_bytes(16, "big")


def aes_256_gcm_seal(plaintext: bytes, key: bytes, *, nonce: bytes, aad: bytes) -> bytes:
    """Return ``ciphertext || tag`` using the section-6.1 construction."""

    if not isinstance(plaintext, bytes) or not isinstance(aad, bytes):
        raise TypeError("plaintext and aad must be bytes")
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise ValueError("payload nonce must contain exactly 12 bytes")
    if len(plaintext) > _MAX_GCM_PAYLOAD_BYTES:
        raise ValueError("payload is too long for GCM's 32-bit counter")
    round_keys = _round_keys(key)
    initial = nonce + b"\x00\x00\x00\x01"
    ciphertext = _counter_xor(plaintext, initial=initial, round_keys=round_keys)
    authentication = _ghash(_encrypt_block(bytes(16), round_keys), aad, ciphertext)
    tag = _xor(_encrypt_block(initial, round_keys), authentication)
    return ciphertext + tag


def aes_256_gcm_open(sealed: bytes, key: bytes, *, nonce: bytes, aad: bytes) -> bytes:
    """Authenticate before returning plaintext from ``ciphertext || tag``."""

    if not isinstance(sealed, bytes) or not isinstance(aad, bytes):
        raise TypeError("sealed payload and aad must be bytes")
    if not isinstance(nonce, bytes) or len(nonce) != NONCE_BYTES:
        raise ValueError("payload nonce must contain exactly 12 bytes")
    if len(sealed) < TAG_BYTES:
        raise PayloadAuthenticationError("payload authentication failed")
    ciphertext, supplied_tag = sealed[:-TAG_BYTES], sealed[-TAG_BYTES:]
    if len(ciphertext) > _MAX_GCM_PAYLOAD_BYTES:
        raise ValueError("payload is too long for GCM's 32-bit counter")
    round_keys = _round_keys(key)
    initial = nonce + b"\x00\x00\x00\x01"
    authentication = _ghash(_encrypt_block(bytes(16), round_keys), aad, ciphertext)
    expected_tag = _xor(_encrypt_block(initial, round_keys), authentication)
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise PayloadAuthenticationError("payload authentication failed")
    return _counter_xor(ciphertext, initial=initial, round_keys=round_keys)


def _header_text(value: str, *, field: str, decimal: bool = False) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PayloadEnvelopeError(f"{field} must be non-empty text without surrounding space")
    if decimal and _DECIMAL.fullmatch(value) is None:
        raise PayloadEnvelopeError(f"{field} must be a canonical positive decimal string")
    return value


def encode_aad(*, schema_version: str, pairing_id: str, seq: str, published_at: str) -> bytes:
    """Encode the exact length-prefixed AAD tuple both ends must reproduce."""

    values = (
        _header_text(schema_version, field="schema_version", decimal=True),
        _header_text(pairing_id, field="pairing_id"),
        _header_text(seq, field="seq", decimal=True),
        _header_text(published_at, field="published_at"),
    )
    encoded = bytearray()
    for value in values:
        raw = value.encode("utf-8")
        encoded.extend(struct.pack(">Q", len(raw)))
        encoded.extend(raw)
    return bytes(encoded)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _open_base64url(value: object, *, field: str) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise PayloadEnvelopeError(f"{field} must be unpadded base64url text")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise PayloadEnvelopeError(f"{field} must be unpadded base64url text") from None
    if _base64url(raw) != value:
        raise PayloadEnvelopeError(f"{field} must use canonical base64url encoding")
    return raw


@dataclass(frozen=True, slots=True)
class PayloadEnvelope:
    """The clear authenticated header and opaque encrypted payload."""

    schema_version: str
    pairing_id: str
    seq: str
    published_at: str
    nonce: bytes
    payload: bytes

    def __post_init__(self) -> None:
        encode_aad(
            schema_version=self.schema_version,
            pairing_id=self.pairing_id,
            seq=self.seq,
            published_at=self.published_at,
        )
        if not isinstance(self.nonce, bytes) or len(self.nonce) != NONCE_BYTES:
            raise PayloadEnvelopeError("nonce must contain exactly 12 bytes")
        if not isinstance(self.payload, bytes) or len(self.payload) < TAG_BYTES:
            raise PayloadEnvelopeError("payload must contain ciphertext and a 16-byte tag")

    @property
    def aad(self) -> bytes:
        return encode_aad(
            schema_version=self.schema_version,
            pairing_id=self.pairing_id,
            seq=self.seq,
            published_at=self.published_at,
        )

    def as_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "pairing_id": self.pairing_id,
            "seq": self.seq,
            "published_at": self.published_at,
            "nonce": _base64url(self.nonce),
            "payload": _base64url(self.payload),
        }

    def to_json(self) -> bytes:
        """Return the deterministic JSON representation task 20 must reproduce."""

        return json.dumps(self.as_mapping(), sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> PayloadEnvelope:
        if not isinstance(raw, Mapping) or set(raw) != _ENVELOPE_FIELDS:
            raise PayloadEnvelopeError("payload envelope has the wrong fields")
        return cls(
            schema_version=_header_text(
                cast(str, raw["schema_version"]), field="schema_version", decimal=True
            ),
            pairing_id=_header_text(cast(str, raw["pairing_id"]), field="pairing_id"),
            seq=_header_text(cast(str, raw["seq"]), field="seq", decimal=True),
            published_at=_header_text(cast(str, raw["published_at"]), field="published_at"),
            nonce=_open_base64url(raw["nonce"], field="nonce"),
            payload=_open_base64url(raw["payload"], field="payload"),
        )

    @classmethod
    def from_json(cls, encoded: bytes) -> PayloadEnvelope:
        if not isinstance(encoded, bytes):
            raise TypeError("encoded envelope must be bytes")
        try:
            raw = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PayloadEnvelopeError("payload envelope is not valid UTF-8 JSON") from None
        if not isinstance(raw, dict):
            raise PayloadEnvelopeError("payload envelope must be a JSON object")
        return cls.from_mapping(cast(dict[str, Any], raw))


def seal_payload(
    plaintext: bytes,
    key: bytes,
    *,
    schema_version: str,
    pairing_id: str,
    seq: str,
    published_at: str,
    nonce: bytes | None = None,
) -> PayloadEnvelope:
    """Seal plaintext and return its authenticated clear-header envelope."""

    chosen_nonce = os.urandom(NONCE_BYTES) if nonce is None else nonce
    aad = encode_aad(
        schema_version=schema_version,
        pairing_id=pairing_id,
        seq=seq,
        published_at=published_at,
    )
    payload = aes_256_gcm_seal(plaintext, key, nonce=chosen_nonce, aad=aad)
    return PayloadEnvelope(
        schema_version=schema_version,
        pairing_id=pairing_id,
        seq=seq,
        published_at=published_at,
        nonce=chosen_nonce,
        payload=payload,
    )


def open_payload(envelope: PayloadEnvelope, key: bytes) -> bytes:
    """Authenticate and decrypt one envelope without trusting its clear fields."""

    if not isinstance(envelope, PayloadEnvelope):
        raise TypeError("envelope must be a PayloadEnvelope")
    return aes_256_gcm_open(envelope.payload, key, nonce=envelope.nonce, aad=envelope.aad)


__all__ = [
    "KEY_BYTES",
    "NONCE_BYTES",
    "PayloadAuthenticationError",
    "PayloadEnvelope",
    "PayloadEnvelopeError",
    "PayloadError",
    "aes_256_gcm_open",
    "aes_256_gcm_seal",
    "encode_aad",
    "open_payload",
    "seal_payload",
]
