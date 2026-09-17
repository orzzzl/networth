"""Small dependency-free terminal QR encoder for task 19a.

Pairing payloads use byte mode, error-correction level L, and version 10.  That
version holds 271 bytes; ``pairing.normalize_tailnet_name`` caps the only
variable-length field so every valid pairing payload fits.  Keeping this here
avoids adding a runtime dependency solely to print one local terminal code.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

_VERSION = 10
_SIZE = _VERSION * 4 + 17
_DATA_CODEWORDS = 274
_ECC_CODEWORDS_PER_BLOCK = 18
_DATA_BLOCK_LENGTHS = (68, 68, 69, 69)
_ALIGNMENT_POSITIONS = (6, 28, 50)


class QrEncodingError(ValueError):
    """The terminal QR contract cannot encode this value."""


def _append_bits(bits: list[int], value: int, width: int) -> None:
    if value < 0 or value >= 1 << width:
        raise QrEncodingError("value does not fit the requested QR bit width")
    bits.extend((value >> shift) & 1 for shift in range(width - 1, -1, -1))


def _data_codewords(payload: bytes) -> list[int]:
    if len(payload) > 271:
        raise QrEncodingError("pairing payload is too large for the terminal QR contract")
    bits: list[int] = []
    _append_bits(bits, 0b0100, 4)  # byte mode
    _append_bits(bits, len(payload), 16)  # versions 10 through 26
    for value in payload:
        _append_bits(bits, value, 8)

    capacity = _DATA_CODEWORDS * 8
    bits.extend((0,) * min(4, capacity - len(bits)))
    bits.extend((0,) * (-len(bits) % 8))
    result = [
        sum(bits[index + offset] << (7 - offset) for offset in range(8))
        for index in range(0, len(bits), 8)
    ]
    for index in range(_DATA_CODEWORDS - len(result)):
        result.append(0xEC if index % 2 == 0 else 0x11)
    return result


def _multiply(left: int, right: int) -> int:
    result = 0
    for _ in range(8):
        result = (result << 1) ^ ((result >> 7) * 0x11D)
        result ^= -(right >> 7) & left
        right = (right << 1) & 0xFF
    return result


def _divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for index in range(degree):
            result[index] = _multiply(result[index], root)
            if index + 1 < degree:
                result[index] ^= result[index + 1]
        root = _multiply(root, 0x02)
    return result


def _remainder(data: Sequence[int], divisor: Sequence[int]) -> list[int]:
    result = [0] * len(divisor)
    for value in data:
        factor = value ^ result.pop(0)
        result.append(0)
        for index, coefficient in enumerate(divisor):
            result[index] ^= _multiply(coefficient, factor)
    return result


def _all_codewords(payload: bytes) -> list[int]:
    data = _data_codewords(payload)
    blocks: list[list[int]] = []
    offset = 0
    divisor = _divisor(_ECC_CODEWORDS_PER_BLOCK)
    ecc: list[list[int]] = []
    for length in _DATA_BLOCK_LENGTHS:
        block = data[offset : offset + length]
        offset += length
        blocks.append(block)
        ecc.append(_remainder(block, divisor))
    result: list[int] = []
    for index in range(max(_DATA_BLOCK_LENGTHS)):
        result.extend(block[index] for block in blocks if index < len(block))
    for index in range(_ECC_CODEWORDS_PER_BLOCK):
        result.extend(block[index] for block in ecc)
    return result


def _set_function(
    modules: list[list[bool]], functions: list[list[bool]], x: int, y: int, black: bool
) -> None:
    modules[y][x] = black
    functions[y][x] = True


def _finder(
    modules: list[list[bool]], functions: list[list[bool]], center_x: int, center_y: int
) -> None:
    for delta_y in range(-4, 5):
        for delta_x in range(-4, 5):
            x, y = center_x + delta_x, center_y + delta_y
            if 0 <= x < _SIZE and 0 <= y < _SIZE:
                distance = max(abs(delta_x), abs(delta_y))
                _set_function(modules, functions, x, y, distance not in (2, 4))


def _alignment(
    modules: list[list[bool]], functions: list[list[bool]], center_x: int, center_y: int
) -> None:
    for delta_y in range(-2, 3):
        for delta_x in range(-2, 3):
            distance = max(abs(delta_x), abs(delta_y))
            _set_function(modules, functions, center_x + delta_x, center_y + delta_y, distance != 1)


def _format_bits(
    modules: list[list[bool]], functions: list[list[bool]], mask: int, *, mark: bool
) -> None:
    data = (0b01 << 3) | mask  # error-correction level L
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    bits = ((data << 10) | remainder) ^ 0x5412

    def put(x: int, y: int, bit: int) -> None:
        modules[y][x] = ((bits >> bit) & 1) != 0
        if mark:
            functions[y][x] = True

    for index in range(6):
        put(8, index, index)
    put(8, 7, 6)
    put(8, 8, 7)
    put(7, 8, 8)
    for index in range(9, 15):
        put(14 - index, 8, index)
    for index in range(8):
        put(_SIZE - 1 - index, 8, index)
    for index in range(8, 15):
        put(8, _SIZE - 15 + index, index)
    _set_function(modules, functions, 8, _SIZE - 8, True)


def _version_bits(modules: list[list[bool]], functions: list[list[bool]]) -> None:
    remainder = _VERSION
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    bits = (_VERSION << 12) | remainder
    for index in range(18):
        black = ((bits >> index) & 1) != 0
        a, b = _SIZE - 11 + index % 3, index // 3
        _set_function(modules, functions, a, b, black)
        _set_function(modules, functions, b, a, black)


def _base_matrix(codewords: Sequence[int]) -> tuple[list[list[bool]], list[list[bool]]]:
    modules = [[False] * _SIZE for _ in range(_SIZE)]
    functions = [[False] * _SIZE for _ in range(_SIZE)]
    for index in range(8, _SIZE - 8):
        _set_function(modules, functions, 6, index, index % 2 == 0)
        _set_function(modules, functions, index, 6, index % 2 == 0)
    _finder(modules, functions, 3, 3)
    _finder(modules, functions, _SIZE - 4, 3)
    _finder(modules, functions, 3, _SIZE - 4)
    last = _ALIGNMENT_POSITIONS[-1]
    for y in _ALIGNMENT_POSITIONS:
        for x in _ALIGNMENT_POSITIONS:
            if (x, y) not in ((6, 6), (last, 6), (6, last)):
                _alignment(modules, functions, x, y)
    _format_bits(modules, functions, 0, mark=True)
    _version_bits(modules, functions)

    bits = [((value >> shift) & 1) != 0 for value in codewords for shift in range(7, -1, -1)]
    bit_index = 0
    right = _SIZE - 1
    while right >= 1:
        if right == 6:
            right = 5
        upward = ((right + 1) & 2) == 0
        for vertical in range(_SIZE):
            y = _SIZE - 1 - vertical if upward else vertical
            for offset in range(2):
                x = right - offset
                if not functions[y][x] and bit_index < len(bits):
                    modules[y][x] = bits[bit_index]
                    bit_index += 1
        right -= 2
    if bit_index != len(bits):
        raise AssertionError("QR layout did not consume every codeword bit")
    return modules, functions


_MASKS: tuple[Callable[[int, int], bool], ...] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: (x * y) % 2 + (x * y) % 3 == 0,
    lambda x, y: ((x * y) % 2 + (x * y) % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + (x * y) % 3) % 2 == 0,
)


def _masked(
    base: Sequence[Sequence[bool]], functions: Sequence[Sequence[bool]], mask: int
) -> list[list[bool]]:
    result = [list(row) for row in base]
    predicate = _MASKS[mask]
    for y in range(_SIZE):
        for x in range(_SIZE):
            if not functions[y][x] and predicate(x, y):
                result[y][x] = not result[y][x]
    mutable_functions = [list(row) for row in functions]
    _format_bits(result, mutable_functions, mask, mark=False)
    return result


def _penalty(modules: Sequence[Sequence[bool]]) -> int:
    score = 0
    lines = [list(row) for row in modules]
    lines.extend([[modules[y][x] for y in range(_SIZE)] for x in range(_SIZE)])
    for line in lines:
        run = 1
        for index in range(1, _SIZE):
            if line[index] == line[index - 1]:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                run = 1
        pattern = "".join("1" if value else "0" for value in line)
        score += 40 * (pattern.count("10111010000") + pattern.count("00001011101"))
    for y in range(_SIZE - 1):
        for x in range(_SIZE - 1):
            value = modules[y][x]
            if (
                modules[y][x + 1] == value
                and modules[y + 1][x] == value
                and modules[y + 1][x + 1] == value
            ):
                score += 3
    black = sum(value for row in modules for value in row)
    score += abs(black * 20 - _SIZE * _SIZE * 10) // (_SIZE * _SIZE) * 10
    return score


def encode_qr(value: str) -> tuple[tuple[bool, ...], ...]:
    """Return a version-10-L QR matrix for UTF-8 ``value``."""

    if not isinstance(value, str):
        raise TypeError("QR value must be text")
    payload = value.encode("utf-8")
    base, functions = _base_matrix(_all_codewords(payload))
    candidates = [_masked(base, functions, mask) for mask in range(8)]
    best = min(candidates, key=_penalty)
    return tuple(tuple(row) for row in best)


def render_terminal_qr(value: str, *, ansi: bool = False) -> str:
    """Render a four-module quiet zone using half-height terminal blocks."""

    matrix = encode_qr(value)
    quiet = 4
    width = _SIZE + quiet * 2
    rows = [[False] * width for _ in range(quiet)]
    rows.extend([[False] * quiet + list(row) + [False] * quiet for row in matrix])
    rows.extend([[False] * width for _ in range(quiet)])
    if len(rows) % 2:
        rows.append([False] * width)
    glyph = {
        (False, False): " ",
        (True, False): "▀",
        (False, True): "▄",
        (True, True): "█",
    }
    lines = [
        "".join(glyph[(rows[index][column], rows[index + 1][column])] for column in range(width))
        for index in range(0, len(rows), 2)
    ]
    if ansi:
        return "\n".join(f"\x1b[30;47m{line}\x1b[0m" for line in lines)
    return "\n".join(lines)
