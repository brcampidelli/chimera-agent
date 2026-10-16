"""A QR code for a short text, with no dependency: what ``chimera pair`` and the Access card show.

Pairing a phone means getting a URL with a one-time code onto it, and a camera is how a person does
that without typing. Nothing in the project drew QR codes, and the job is small and fully specified
(ISO/IEC 18004): byte mode, error-correction level M, versions 1 to 10 — up to 213 bytes, four times
the longest pairing URL. A dependency for that would be a package on every install, in every
container image, kept up to date forever, for one screen; this is the algorithm itself, in the shape
Nayuki's reference implementation popularised, which is short enough to read and to test.

What it returns is the module matrix (``True`` is dark), and the two renderings its callers need: a
terminal block (:func:`to_terminal`) and rows of ``1``/``0`` that the screen turns into an SVG.
"""

from __future__ import annotations

from collections.abc import Callable

#: Error-correction level M recovers ~15% of the symbol: a phone screen photographed at an angle.
_EC_PER_BLOCK_M = (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26)
_BLOCKS_M = (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5)
#: The two format bits of level M (``L=01, M=00, Q=11, H=10``).
_FORMAT_BITS_M = 0
MAX_VERSION = 10

Matrix = list[list[bool]]


class TooLong(ValueError):
    """The text does not fit a version-10 symbol at level M."""


def _gf_multiply(x: int, y: int) -> int:
    """Multiplication in GF(2^8) modulo the QR polynomial x^8 + x^4 + x^3 + x^2 + 1."""
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_multiply(root, 0x02)
    return result


def _rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coefficient in enumerate(divisor):
            result[i] ^= _gf_multiply(coefficient, factor)
    return result


def _raw_modules(version: int) -> int:
    """Data-carrying modules of a version: everything but the function patterns."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        aligns = version // 7 + 2
        result -= (25 * aligns - 10) * aligns - 55
        if version >= 7:
            result -= 36
    return result


def _data_codewords(version: int) -> int:
    return _raw_modules(version) // 8 - _EC_PER_BLOCK_M[version] * _BLOCKS_M[version]


def _alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    aligns = version // 7 + 2
    size = version * 4 + 17
    step = (version * 8 + aligns * 3 + 5) // (aligns * 4 - 4) * 2
    return [6, *reversed([size - 7 - i * step for i in range(aligns - 1)])]


def _codewords(data: bytes) -> tuple[int, list[int]]:
    """The smallest version that holds ``data``, and its data codewords (mode, count, padding)."""
    for version in range(1, MAX_VERSION + 1):
        count_bits = 8 if version <= 9 else 16
        capacity = _data_codewords(version) * 8
        if 4 + count_bits + 8 * len(data) <= capacity:
            break
    else:
        raise TooLong(f"{len(data)} bytes do not fit a version-{MAX_VERSION} QR code")
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)  # byte mode
    put(len(data), count_bits)
    for byte in data:
        put(byte, 8)
    put(0, min(4, capacity - len(bits)))  # terminator
    put(0, (-len(bits)) % 8)
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0xEC
    while len(words) < capacity // 8:
        words.append(pad)
        pad ^= 0xEC ^ 0x11
    return version, words


def _interleave(version: int, data: list[int]) -> list[int]:
    blocks_n = _BLOCKS_M[version]
    ec_len = _EC_PER_BLOCK_M[version]
    raw = _raw_modules(version) // 8
    short_blocks = blocks_n - raw % blocks_n
    short_len = raw // blocks_n
    divisor = _rs_divisor(ec_len)
    blocks: list[list[int]] = []
    k = 0
    for i in range(blocks_n):
        take = short_len - ec_len + (0 if i < short_blocks else 1)
        chunk = data[k : k + take]
        k += take
        ecc = _rs_remainder(chunk, divisor)
        if i < short_blocks:
            chunk = [*chunk, 0]  # a placeholder, skipped below
        blocks.append(chunk + ecc)
    result: list[int] = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ec_len or j >= short_blocks:
                result.append(block[i])
    return result


_MASKS: tuple[Callable[[int, int], bool], ...] = (
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
)


class _Symbol:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.modules: Matrix = [[False] * self.size for _ in range(self.size)]
        self.function: Matrix = [[False] * self.size for _ in range(self.size)]

    def set_function(self, x: int, y: int, dark: bool) -> None:
        self.modules[y][x] = dark
        self.function[y][x] = True

    def draw_function_patterns(self) -> None:
        for i in range(self.size):
            self.set_function(6, i, i % 2 == 0)
            self.set_function(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (self.size - 4, 3), (3, self.size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < self.size and 0 <= y < self.size:
                        self.set_function(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = _alignment_positions(self.version)
        last = len(positions) - 1
        for i, ay in enumerate(positions):
            for j, ax in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # the finder corners
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_function(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserved now, rewritten once the mask is chosen
        self.draw_version()

    def draw_format(self, mask: int) -> None:
        bits = format_bits(mask)
        size = self.size
        for i in range(6):
            self.set_function(8, i, _bit(bits, i))
        self.set_function(8, 7, _bit(bits, 6))
        self.set_function(8, 8, _bit(bits, 7))
        self.set_function(7, 8, _bit(bits, 8))
        for i in range(9, 15):
            self.set_function(14 - i, 8, _bit(bits, i))
        for i in range(8):
            self.set_function(size - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15):
            self.set_function(8, size - 15 + i, _bit(bits, i))
        self.set_function(8, size - 8, True)  # the dark module

    def draw_version(self) -> None:
        if self.version < 7:
            return
        bits = version_bits(self.version)
        for i in range(18):
            dark = _bit(bits, i)
            a, b = self.size - 11 + i % 3, i // 3
            self.set_function(a, b, dark)
            self.set_function(b, a, dark)

    def draw_codewords(self, data: list[int]) -> None:
        i = 0
        right = self.size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vertical in range(self.size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = self.size - 1 - vertical if upward else vertical
                    if not self.function[y][x] and i < len(data) * 8:
                        self.modules[y][x] = _bit(data[i >> 3], 7 - (i & 7))
                        i += 1
            right -= 2

    def apply_mask(self, mask: int) -> None:
        test = _MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.function[y][x] and test(x, y):
                    self.modules[y][x] = not self.modules[y][x]


def _bit(value: int, index: int) -> bool:
    return (value >> index) & 1 != 0


def format_bits(mask: int) -> int:
    """The 15 format bits for level M and ``mask``: BCH(15,5), then the fixed XOR pattern."""
    data = _FORMAT_BITS_M << 3 | mask
    remainder = data
    for _ in range(10):
        remainder = (remainder << 1) ^ ((remainder >> 9) * 0x537)
    return (data << 10 | remainder) ^ 0x5412


def version_bits(version: int) -> int:
    """The 18 version bits (versions 7 and up): BCH(18,6) over the version number."""
    remainder = version
    for _ in range(12):
        remainder = (remainder << 1) ^ ((remainder >> 11) * 0x1F25)
    return version << 12 | remainder


def _penalty(modules: Matrix) -> int:
    """Rules 1, 2 and 4 of the standard's mask evaluation (runs, 2x2 blocks, balance).

    Rule 3 (finder-like sequences) is left out: every mask produces a valid symbol, so the choice
    only tunes how easily a camera reads it, and three of the four rules carry most of that."""
    size = len(modules)
    score = 0
    for lines in (modules, [list(col) for col in zip(*modules, strict=True)]):
        for line in lines:
            run = 1
            for a, b in zip(line, line[1:], strict=False):
                if a == b:
                    run += 1
                else:
                    if run >= 5:
                        score += run - 2
                    run = 1
            if run >= 5:
                score += run - 2
    for y in range(size - 1):
        for x in range(size - 1):
            cell = modules[y][x]
            if cell == modules[y][x + 1] == modules[y + 1][x] == modules[y + 1][x + 1]:
                score += 3
    dark = sum(row.count(True) for row in modules)
    total = size * size
    score += (abs(dark * 20 - total * 10) + total - 1) // total * 10 - 10 if total else 0
    return score


def encode(text: str, *, mask: int | None = None) -> Matrix:
    """The module matrix of ``text`` (UTF-8, byte mode, level M). ``mask`` forces one (tests)."""
    version, data = _codewords(text.encode("utf-8"))
    symbol = _Symbol(version)
    symbol.draw_function_patterns()
    symbol.draw_codewords(_interleave(version, data))
    if mask is None:
        best: tuple[int, int] | None = None
        for candidate in range(8):
            symbol.apply_mask(candidate)
            symbol.draw_format(candidate)
            score = _penalty(symbol.modules)
            if best is None or score < best[0]:
                best = (score, candidate)
            symbol.apply_mask(candidate)  # XOR again: undone
        mask = best[1] if best else 0
    symbol.apply_mask(mask)
    symbol.draw_format(mask)
    return symbol.modules


def to_rows(matrix: Matrix) -> list[str]:
    """``"1"``/``"0"`` per module, one string per row: what the screen draws as an SVG."""
    return ["".join("1" if dark else "0" for dark in row) for row in matrix]


def to_terminal(matrix: Matrix, *, quiet: int = 2) -> str:
    """Two module rows per line in half blocks, light on dark terminals and dark on light alike.

    Drawn as the INVERSE (a light module is a full block): terminals are mostly dark, and a scanner
    needs the code's dark modules darker than its light ones. The quiet zone is part of the code."""
    size = len(matrix)
    span = range(-quiet, size + quiet)

    def dark(x: int, y: int) -> bool:
        return 0 <= x < size and 0 <= y < size and matrix[y][x]

    lines = []
    for y in range(-quiet, size + quiet, 2):
        line = []
        for x in span:
            top, bottom = not dark(x, y), not dark(x, y + 1)
            line.append("█" if top and bottom else "▀" if top else "▄" if bottom else " ")
        lines.append("".join(line))
    return "\n".join(lines)
