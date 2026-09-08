"""The standard primitives task 03a's sealed archive depends on."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from networth.backup.crypto import (
    AuthenticationError,
    BackupKeyError,
    _chacha20_block,
    _chacha20_xor,
    _poly1305,
    _tag,
    load_backup_key,
    open_sealed,
    seal,
)


def test_chacha20_matches_the_rfc_8439_block_vector() -> None:
    key = bytes(range(32))
    nonce = bytes.fromhex("000000090000004a00000000")
    expected = bytes.fromhex(
        "10f1e7e4d13b5915500fdd1fa32071c4"
        "c7d1f4c733c068030422aa9ac3d46c4e"
        "d2826446079faa0914c2d705d98b02a2"
        "b5129cd1de164eb9cbd083e8a2503c4e"
    )
    assert _chacha20_block(key, 1, nonce) == expected


def test_poly1305_matches_the_rfc_vector() -> None:
    key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
    message = b"Cryptographic Forum Research Group"
    assert _poly1305(message, key).hex() == "a8061dc1305136c6c22b8baf0c0127a9"


def test_chacha20_poly1305_matches_the_rfc_8439_aead_vector() -> None:
    key = bytes(range(0x80, 0xA0))
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    plaintext = bytes.fromhex(
        "4c616469657320616e642047656e746c"
        "656d656e206f662074686520636c6173"
        "73206f66202739393a20496620492063"
        "6f756c64206f6666657220796f75206f"
        "6e6c79206f6e652074697020666f7220"
        "746865206675747572652c2073756e73"
        "637265656e20776f756c642062652069"
        "742e"
    )
    ciphertext = _chacha20_xor(key, nonce, plaintext, counter=1)
    assert ciphertext.hex() == (
        "d31a8d34648e60db7b86afbc53ef7ec2"
        "a4aded51296e08fea9e2b5a736ee62d6"
        "3dbea45e8ca9671282fafb69da92728b"
        "1a71de0a9e060b2905d6a5b67ecd3b36"
        "92ddbd7f2d778b8c9803aee328091b58"
        "fab324e4fad675945585808b4831d7bc"
        "3ff4def08e4b7a9de576d26586cec64b6116"
    )
    assert _tag(key, nonce, aad, ciphertext).hex() == "1ae10b594f09e26a7e902ecbd0600691"


def test_seal_round_trip_and_every_envelope_region_is_authenticated() -> None:
    key = bytes(range(32))
    plaintext = b"self-contained archive" * 10
    envelope = seal(plaintext, key, nonce=bytes(range(12)))
    assert open_sealed(envelope, key) == plaintext

    for offset in (0, 16, 20, len(envelope) // 2, len(envelope) - 1):
        changed = bytearray(envelope)
        changed[offset] ^= 1
        with pytest.raises(AuthenticationError):
            open_sealed(bytes(changed), key)


def test_key_file_is_one_private_line_in_hex_or_base64(tmp_path: Path) -> None:
    key = bytes(range(32))
    path = tmp_path / "backup.key"
    path.write_text(key.hex() + "\n", encoding="ascii")
    path.chmod(0o600)
    assert load_backup_key(path) == key

    path.write_text(base64.b64encode(key).decode("ascii") + "\n", encoding="ascii")
    assert load_backup_key(path) == key

    path.chmod(0o644)
    with pytest.raises(BackupKeyError, match="group or others"):
        load_backup_key(path)


def test_key_errors_never_repeat_the_file_contents(tmp_path: Path) -> None:
    planted = "not-a-usable-key-value"
    path = tmp_path / "backup.key"
    path.write_text(planted + "\n", encoding="ascii")
    path.chmod(0o600)
    with pytest.raises(BackupKeyError) as caught:
        load_backup_key(path)
    assert planted not in str(caught.value)
