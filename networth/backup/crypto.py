"""Standard ChaCha20-Poly1305 sealing with no third-party dependency.

The repository's dependency rule forbids adding a package to task 03a.  This is
the RFC 8439 construction directly: ChaCha20 for encryption, Poly1305 over AAD
and ciphertext, and a random 96-bit nonce.  The small implementation is pinned
by the RFC block and Poly1305 vectors in the test suite; archive tests also
exercise ciphertext, header, and tag tampering.

The on-disk envelope is ``magic || version || nonce || ciphertext || tag``.
Magic, version, and nonce are authenticated as AAD.  Nothing in an error message
contains key bytes or plaintext.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import stat
import struct
from pathlib import Path

MAGIC = b"NETWORTH-BACKUP\x00"
FORMAT_VERSION = 1
KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16


class BackupKeyError(RuntimeError):
    """A backup key file cannot safely supply one 256-bit key."""


class AuthenticationError(RuntimeError):
    """A sealed archive is malformed or fails its authentication tag."""


def load_backup_key(path: Path) -> bytes:
    """Read a mode-0600, one-line 256-bit key in hex or base64 form."""

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise BackupKeyError(f"cannot open backup key {path}: {exc.strerror}") from None
    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupKeyError(f"backup key {path} is not a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise BackupKeyError(f"backup key {path} must not be accessible by group or others")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(1024)
    finally:
        os.close(fd)

    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise BackupKeyError("backup key is not one ASCII line") from None
    if len(raw) == 1024 or len(text.splitlines()) != 1:
        raise BackupKeyError("backup key is not one bounded ASCII line")
    encoded = text.strip()
    try:
        if len(encoded) == KEY_BYTES * 2:
            key = bytes.fromhex(encoded)
        else:
            key = base64.b64decode(encoded, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise BackupKeyError("backup key is neither 64 hex characters nor valid base64") from None
    if len(key) != KEY_BYTES:
        raise BackupKeyError("backup key must decode to exactly 32 bytes")
    return key


def hkdf_sha256(key: bytes, *, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256, used for archive-scoped fingerprint keys."""

    if length <= 0 or length > 255 * hashlib.sha256().digest_size:
        raise ValueError("HKDF length is outside RFC 5869's bounds")
    extract_salt = salt or bytes(hashlib.sha256().digest_size)
    pseudorandom_key = hmac.new(extract_salt, key, hashlib.sha256).digest()
    output = bytearray()
    previous = b""
    counter = 1
    while len(output) < length:
        previous = hmac.new(
            pseudorandom_key,
            previous + info + bytes((counter,)),
            hashlib.sha256,
        ).digest()
        output.extend(previous)
        counter += 1
    return bytes(output[:length])


def _rotate_left(value: int, count: int) -> int:
    return ((value << count) & 0xFFFFFFFF) | (value >> (32 - count))


def _quarter_round(state: list[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left(state[d] ^ state[a], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left(state[b] ^ state[c], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] = _rotate_left(state[d] ^ state[a], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] = _rotate_left(state[b] ^ state[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    if len(key) != KEY_BYTES or len(nonce) != NONCE_BYTES:
        raise ValueError("ChaCha20 requires a 32-byte key and 12-byte nonce")
    if not 0 <= counter <= 0xFFFFFFFF:
        raise ValueError("ChaCha20 counter exhausted")
    constants = struct.unpack("<4I", b"expand 32-byte k")
    initial = [
        *constants,
        *struct.unpack("<8I", key),
        counter,
        *struct.unpack("<3I", nonce),
    ]
    working = initial.copy()
    for _ in range(10):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    return struct.pack(
        "<16I",
        *((word + base) & 0xFFFFFFFF for word, base in zip(working, initial, strict=True)),
    )


def _chacha20_xor(key: bytes, nonce: bytes, data: bytes, *, counter: int) -> bytes:
    output = bytearray(len(data))
    for offset in range(0, len(data), 64):
        block_counter = counter + offset // 64
        block = _chacha20_block(key, block_counter, nonce)
        chunk = data[offset : offset + 64]
        output[offset : offset + len(chunk)] = bytes(
            value ^ mask for value, mask in zip(chunk, block, strict=False)
        )
    return bytes(output)


def _poly1305(message: bytes, one_time_key: bytes) -> bytes:
    if len(one_time_key) != 32:
        raise ValueError("Poly1305 requires a 32-byte one-time key")
    r = int.from_bytes(one_time_key[:16], "little")
    r &= 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(one_time_key[16:], "little")
    accumulator = 0
    modulus = (1 << 130) - 5
    for offset in range(0, len(message), 16):
        chunk = message[offset : offset + 16]
        number = int.from_bytes(chunk + b"\x01", "little")
        accumulator = ((accumulator + number) * r) % modulus
    return ((accumulator + s) % (1 << 128)).to_bytes(16, "little")


def _pad16(value: bytes) -> bytes:
    return bytes((-len(value)) % 16)


def _tag(key: bytes, nonce: bytes, aad: bytes, ciphertext: bytes) -> bytes:
    one_time_key = _chacha20_block(key, 0, nonce)[:32]
    mac_input = b"".join(
        (
            aad,
            _pad16(aad),
            ciphertext,
            _pad16(ciphertext),
            struct.pack("<QQ", len(aad), len(ciphertext)),
        )
    )
    return _poly1305(mac_input, one_time_key)


def seal(plaintext: bytes, key: bytes, *, nonce: bytes | None = None) -> bytes:
    """Authenticate and encrypt ``plaintext`` into the archive envelope."""

    if len(key) != KEY_BYTES:
        raise ValueError("backup key must contain exactly 32 bytes")
    chosen_nonce = os.urandom(NONCE_BYTES) if nonce is None else nonce
    if len(chosen_nonce) != NONCE_BYTES:
        raise ValueError("archive nonce must contain exactly 12 bytes")
    header = MAGIC + bytes((FORMAT_VERSION,)) + chosen_nonce
    ciphertext = _chacha20_xor(key, chosen_nonce, plaintext, counter=1)
    return header + ciphertext + _tag(key, chosen_nonce, header, ciphertext)


def open_sealed(envelope: bytes, key: bytes) -> bytes:
    """Authenticate then decrypt one archive envelope."""

    if len(key) != KEY_BYTES:
        raise ValueError("backup key must contain exactly 32 bytes")
    header_size = len(MAGIC) + 1 + NONCE_BYTES
    if len(envelope) < header_size + TAG_BYTES:
        raise AuthenticationError("archive envelope is truncated")
    if envelope[: len(MAGIC)] != MAGIC:
        raise AuthenticationError("archive envelope has the wrong magic")
    version = envelope[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise AuthenticationError("archive envelope uses an unsupported format version")
    nonce = envelope[len(MAGIC) + 1 : header_size]
    ciphertext = envelope[header_size:-TAG_BYTES]
    supplied_tag = envelope[-TAG_BYTES:]
    expected_tag = _tag(key, nonce, envelope[:header_size], ciphertext)
    if not hmac.compare_digest(supplied_tag, expected_tag):
        raise AuthenticationError("archive authentication failed")
    return _chacha20_xor(key, nonce, ciphertext, counter=1)
