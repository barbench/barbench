"""Tests for the baseline JPEG decoder. Run from bench/: python -m unittest discover -s tests

The JPEGs are written here by a small baseline encoder, so no image files
are needed. Its Huffman tables are made up (any complete-enough canonical
table is valid), and the expected luma is rebuilt from the quantized
coefficients with a direct double sum, independent of the decoder's
matrix form.
"""

import math
import struct
import time
import unittest

import numpy as np

from barbench import jpeg

ZIGZAG = jpeg.ZIGZAG

#: (counts per code length 1..16) for the DC and AC tables, and their
#: symbols: DC categories 0..11; AC end of block, run of 16 and every
#: run/size pair with size 1..10.
DC_COUNTS = [0, 1, 3, 3, 3, 2] + [0] * 10
DC_SYMBOLS = list(range(12))
AC_COUNTS = [0, 1, 2, 0, 6, 0, 20, 0, 60, 0, 73, 0, 0, 0, 0, 0]
AC_SYMBOLS = [0x00, 0x01, 0xF0] + [(r << 4) | s for r in range(16) for s in range(1, 11) if (r, s) != (0, 1)]


def codes(counts, symbols):
    """Canonical Huffman codes, symbol -> (code, length) (T.81 C.2)."""
    table, code, at = {}, 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            table[symbols[at]] = (code, length)
            code += 1
            at += 1
        code <<= 1
    return table


DC_CODES, AC_CODES = codes(DC_COUNTS, DC_SYMBOLS), codes(AC_COUNTS, AC_SYMBOLS)


def basis():
    u = np.arange(8)[:, None]
    x = np.arange(8)[None, :]
    b = np.cos((2 * x + 1) * u * np.pi / 16) / 2
    b[0] /= np.sqrt(2)
    return b


def forward(block, quant):
    """Quantized DCT coefficients of an 8×8 block, natural order."""
    b = basis()
    coefs = b @ (block.astype(np.float64) - 128) @ b.T
    return np.rint(coefs / quant).astype(np.int64)


def inverse(coefs, quant):
    """Pixels from quantized coefficients by the T.81 A.3.3 double sum."""
    out = np.zeros((8, 8))
    f = coefs * quant
    for y in range(8):
        for x in range(8):
            total = 0.0
            for v in range(8):
                for u in range(8):
                    cu = 1 / math.sqrt(2) if u == 0 else 1.0
                    cv = 1 / math.sqrt(2) if v == 0 else 1.0
                    total += (
                        cu * cv * f[v, u] * math.cos((2 * x + 1) * u * math.pi / 16) * math.cos((2 * y + 1) * v * math.pi / 16)
                    )
            out[y, x] = total / 4 + 128
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


class Bits:
    def __init__(self):
        self.bits = []

    def put(self, value, length):
        self.bits.extend((value >> (length - 1 - i)) & 1 for i in range(length))

    def flush(self):
        """The bytes so far, padded with ones and stuffed; then empty."""
        bits = self.bits + [1] * (-len(self.bits) % 8)
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = int("".join(map(str, bits[i : i + 8])), 2)
            out.append(byte)
            if byte == 0xFF:
                out.append(0)
        self.bits = []
        return bytes(out)


def category(value):
    return abs(value).bit_length()


def put_value(bits, value, size):
    bits.put(value if value >= 0 else value + (1 << size) - 1, size)


#: How many runs of 16 zeros the encoder has written, so a test can check
#: it made some.
ZRL_WRITTEN = [0]


def put_block(bits, coefs, pred):
    zz = [int(coefs.flat[ZIGZAG[k]]) for k in range(64)]
    diff = zz[0] - pred
    size = category(diff)
    bits.put(*DC_CODES[size])
    if size:
        put_value(bits, diff, size)
    run = 0
    for k in range(1, 64):
        if zz[k] == 0:
            run += 1
            continue
        while run > 15:
            bits.put(*AC_CODES[0xF0])
            ZRL_WRITTEN[0] += 1
            run -= 16
        size = category(zz[k])
        assert size <= 10, "AC coefficient too large for baseline"
        bits.put(*AC_CODES[(run << 4) | size])
        put_value(bits, zz[k], size)
        run = 0
    if run:
        bits.put(*AC_CODES[0x00])
    return zz[0]


def segment(marker, body):
    return struct.pack(">BBH", 0xFF, marker, len(body) + 2) + body


def encode(
    planes, factors, quant=3, restart=0, interleave=True, sof=0xC0, wide_table=False, one_dht=False, fill=False, after_first_scan=b""
):
    """A baseline JPEG of `planes` (each component's samples at its own
    resolution, the first the luma), with `factors` [(h, v), ...].
    `wide_table` writes the quantization table with 16-bit entries,
    `one_dht` both Huffman tables in one segment, `fill` a fill byte before
    each restart marker, and `after_first_scan` goes between the first
    scan and the next. Returns the file and the luma the decoder should
    produce."""
    height, width = planes[0].shape
    hmax = max(h for h, _ in factors)
    vmax = max(v for _, v in factors)
    # Steps that differ along each row and column, so reading the table in
    # the wrong order gives other pixels.
    q = quant + (np.arange(64).reshape(8, 8) * 5) % 7
    mcux, mcuy = -(-width // (8 * hmax)), -(-height // (8 * vmax))
    blocks = []
    for plane, (h, v) in zip(planes, factors):
        rows, cols = mcuy * v * 8, mcux * h * 8
        # Pad by repeating the edge, as encoders do.
        padded = np.pad(plane, ((0, rows - plane.shape[0]), (0, cols - plane.shape[1])), mode="edge")
        grid = {}
        for by in range(rows // 8):
            for bx in range(cols // 8):
                grid[by, bx] = forward(padded[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8], q)
        blocks.append(grid)
    luma = np.zeros((mcuy * factors[0][1] * 8, mcux * factors[0][0] * 8), np.uint8)
    for (by, bx), c in blocks[0].items():
        luma[by * 8 : by * 8 + 8, bx * 8 : bx * 8 + 8] = inverse(c, q)

    zz = [int(q.flat[ZIGZAG[k]]) for k in range(64)]
    out = b"\xff\xd8" + segment(0xE0, b"JFIF\0\x01\x01\0\0\x01\0\x01\0\0")
    if wide_table:
        out += segment(0xDB, b"\x10" + b"".join(struct.pack(">H", v) for v in zz))
    else:
        out += segment(0xDB, b"\x00" + bytes(zz))
    frame = struct.pack(">BHHB", 8, height, width, len(planes))
    for i, (h, v) in enumerate(factors):
        frame += bytes([i + 1, (h << 4) | v, 0])
    out += segment(sof, frame)
    dc = b"\x00" + bytes(DC_COUNTS) + bytes(DC_SYMBOLS)
    ac = b"\x10" + bytes(AC_COUNTS) + bytes(AC_SYMBOLS)
    out += segment(0xC4, dc + ac) if one_dht else segment(0xC4, dc) + segment(0xC4, ac)
    if restart:
        out += segment(0xDD, struct.pack(">H", restart))

    def scan(components):
        body = bytes([len(components)])
        for i in components:
            body += bytes([i + 1, 0x00])
        data = segment(0xDA, body + b"\x00\x3f\x00")
        bits = Bits()
        preds = [0] * len(planes)
        if len(components) == 1:
            i = components[0]
            cw = -(-width * factors[i][0] // hmax)
            ch = -(-height * factors[i][1] // vmax)
            units = [[(i, by, bx)] for by in range(-(-ch // 8)) for bx in range(-(-cw // 8))]
        else:
            units = []
            for my in range(mcuy):
                for mx in range(mcux):
                    units.append(
                        [
                            (i, my * factors[i][1] + dy, mx * factors[i][0] + dx)
                            for i in components
                            for dy in range(factors[i][1])
                            for dx in range(factors[i][0])
                        ]
                    )
        for n, unit in enumerate(units):
            if restart and n and n % restart == 0:
                data += bits.flush() + (b"\xff" if fill else b"") + bytes([0xFF, 0xD0 + (n // restart - 1) % 8])
                preds = [0] * len(planes)
            for i, by, bx in unit:
                preds[i] = put_block(bits, blocks[i][by, bx], preds[i])
        return data + bits.flush()

    if interleave:
        out += scan(list(range(len(planes)))) + after_first_scan
    else:
        for i in range(len(planes)):
            out += scan([i]) + (after_first_scan if i == 0 else b"")
    return out + b"\xff\xd9", luma[:height, :width]


def picture(height, width, seed):
    """Smooth shading plus noise, so coefficients vary and 0xFF bytes
    (which need stuffing) turn up."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:height, 0:width]
    base = 128 + 90 * np.sin(x / 5.0) * np.cos(y / 7.0)
    return np.clip(base + rng.normal(0, 25, (height, width)), 0, 255).astype(np.uint8)


class DecodeTest(unittest.TestCase):
    def check(self, data, luma):
        got = jpeg.decode_jpeg(data)
        self.assertEqual(got.shape, luma.shape)
        # The decoder's float matrix product and the double sum may round a
        # value that lands on .5 differently; nothing else may differ.
        self.assertLessEqual(int(np.abs(got.astype(int) - luma.astype(int)).max()), 1)
        self.assertLess(float(np.mean(got != luma)), 0.01)

    def test_grayscale_with_restarts(self):
        data, luma = encode([picture(21, 37, 1)], [(1, 1)], restart=3)
        self.assertIn(b"\xff\x00", data)
        self.assertIn(b"\xff\xd1", data)
        self.check(data, luma)

    def test_subsampled_colour_interleaved(self):
        y = picture(29, 45, 2)
        cb, cr = picture(15, 23, 3), picture(15, 23, 4)
        data, luma = encode([y, cb, cr], [(2, 2), (1, 1), (1, 1)], restart=2)
        self.check(data, luma)

    def test_colour_in_one_scan_per_component(self):
        y = picture(19, 30, 5)
        cb, cr = picture(10, 15, 6), picture(10, 15, 7)
        data, luma = encode([y, cb, cr], [(2, 2), (1, 1), (1, 1)], interleave=False)
        self.check(data, luma)

    def test_vertical_subsampling_and_extended_frame(self):
        y = picture(24, 16, 8)
        cb, cr = picture(12, 16, 9), picture(12, 16, 10)
        data, luma = encode([y, cb, cr], [(1, 2), (1, 1), (1, 1)], sof=0xC1)
        self.check(data, luma)

    def test_one_scan_per_component_with_restarts_and_fill_bytes(self):
        y = picture(20, 28, 17)
        cb, cr = picture(10, 14, 18), picture(10, 14, 19)
        data, luma = encode([y, cb, cr], [(2, 2), (1, 1), (1, 1)], interleave=False, restart=4, fill=True)
        self.assertIn(b"\xff\xff\xd0", data)
        self.check(data, luma)

    def test_wide_table_and_tables_sharing_a_segment(self):
        data, luma = encode([picture(16, 16, 20)], [(1, 1)], wide_table=True, one_dht=True)
        self.check(data, luma)

    def test_runs_of_sixteen_zeros(self):
        # One high-frequency pattern on a flat ground: blocks whose only
        # AC coefficient comes after more than 16 zeros in zigzag order.
        y, x = np.mgrid[0:24, 0:24]
        wave = np.cos((2 * x + 1) * 5 * np.pi / 16) * np.cos((2 * y + 1) * 6 * np.pi / 16)
        image = np.clip(128 + 60 * wave, 0, 255).astype(np.uint8)
        before = ZRL_WRITTEN[0]
        data, luma = encode([image], [(1, 1)], quant=1)
        self.assertGreater(ZRL_WRITTEN[0], before)
        self.check(data, luma)

    def test_a_table_redefined_after_the_luma_scan_does_not_apply_to_it(self):
        y = picture(16, 16, 21)
        cb, cr = picture(8, 8, 22), picture(8, 8, 23)
        other = segment(0xDB, b"\x00" + bytes(range(1, 65)))
        data, luma = encode([y, cb, cr], [(2, 2), (1, 1), (1, 1)], interleave=False, after_first_scan=other)
        self.check(data, luma)

    def test_luma_close_to_the_original(self):
        original = picture(16, 24, 11)
        data, _ = encode([original], [(1, 1)], quant=1)
        got = jpeg.decode_jpeg(data)
        self.assertLessEqual(int(np.abs(got.astype(int) - original.astype(int)).max()), 6)


class RefuseTest(unittest.TestCase):
    def setUp(self):
        self.good, _ = encode([picture(16, 16, 12)], [(1, 1)])
        self.colour, _ = encode(
            [picture(16, 16, 13), picture(8, 8, 14), picture(8, 8, 15)], [(2, 2), (1, 1), (1, 1)]
        )

    def refused(self, data, message=None):
        if message:
            with self.assertRaisesRegex(jpeg.JpegError, message):
                jpeg.decode_jpeg(data)
        else:
            with self.assertRaises(jpeg.JpegError):
                jpeg.decode_jpeg(data)

    def test_unsupported_files(self):
        sof = self.good.index(b"\xff\xc0")
        frame = self.good[sof + 4 :]
        self.refused(b"GIF89a", "not a JPEG")
        self.refused(self.good[: sof + 1] + b"\xc2" + self.good[sof + 2 :], "SOF2")
        self.refused(self.good[: sof + 1] + b"\xc3" + self.good[sof + 2 :], "SOF3")
        self.refused(self.good[: sof + 1] + b"\xc9" + self.good[sof + 2 :], "SOF9")
        self.refused(self.good[: sof + 4] + b"\x0c" + frame[1:], "12-bit")
        huge = self.good[: sof + 5] + struct.pack(">HH", 0xFFFF, 0xFFFF) + frame[5:]
        self.refused(huge, "size")
        # A size within the cap that the file is far too short to hold is
        # refused before its blocks are allocated.
        claimed = self.good[: sof + 5] + struct.pack(">HH", 4000, 4000) + frame[5:]
        self.refused(claimed, "too short")
        dnl = self.good[: sof + 5] + struct.pack(">H", 0) + frame[3:]
        self.refused(dnl, "DNL")
        marker = segment(0xEE, b"Adobe\x00\x64\x00\x00\x00\x00\x00")
        self.refused(self.colour[:2] + marker + self.colour[2:], "Adobe RGB")
        separate, _ = encode(
            [picture(16, 16, 13), picture(8, 8, 14), picture(8, 8, 15)], [(2, 2), (1, 1), (1, 1)], interleave=False
        )
        self.refused(separate[:2] + marker + separate[2:], "Adobe RGB")

    def test_broken_files(self):
        sos = self.good.index(b"\xff\xda")
        self.refused(self.good[: sos + 20], "end of the file")
        self.refused(self.good[:sos] + b"\xff\xd9", "no scan")
        # Scan data cut short: the rest is read as padding ones.
        self.refused(self.good[: sos + 14] + b"\xff\xd9")
        self.refused(self.good.replace(b"\xff\xdb", b"\xff\xfe", 1), "quantization")
        self.refused(b"\xff\xd8" + b"\xff" * 100)
        self.refused(b"\xff\xd8\xff\xc4\x00\x13\x00" + bytes([2, 2] + [0] * 14), "Huffman")

    def test_mutations_never_crash_or_hang(self):
        # Flipped and dropped bytes: refused or decoded, never another
        # exception, and fast.
        rng = np.random.default_rng(16)
        start = time.perf_counter()
        for base in (self.good, self.colour):
            for _ in range(150):
                data = bytearray(base)
                for _ in range(int(rng.integers(1, 4))):
                    at = int(rng.integers(0, len(data)))
                    if rng.random() < 0.5:
                        data[at] = int(rng.integers(0, 256))
                    else:
                        del data[at]
                try:
                    image = jpeg.decode_jpeg(bytes(data))
                    self.assertEqual(image.dtype, np.uint8)
                except jpeg.JpegError:
                    pass
        self.assertLess(time.perf_counter() - start, 60)


if __name__ == "__main__":
    unittest.main()
