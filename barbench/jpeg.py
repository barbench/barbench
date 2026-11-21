"""A baseline JPEG decoder that returns the luma plane, for photos that come
as JPEG.

Python JPEG libraries bundle libjpeg or libjpeg-turbo, whose IJG licence is
not on the allowlist in AGENTS.md, so barbench decodes JPEG itself. It
reads what most camera and editor files use: sequential DCT (SOF0 or SOF1)
with 8-bit samples and Huffman coding, grayscale or YCbCr, any sampling
factors, interleaved or not, with restart intervals. It refuses
progressive, arithmetic-coded, lossless, 12-bit, CMYK and Adobe RGB files.
Only the luma component is transformed; the others are decoded to keep the
bit stream in step and then dropped. Implemented from ITU-T T.81.

Input is untrusted: every count and size comes from the file, so each is
checked, and decoding stops at the end of the data rather than reading the
padding forever. Memory follows the size the frame claims, so that is
capped, and a claim the file is too short to hold is refused before
anything is allocated.
"""

from array import array

#: Most pixels a photo may claim: 16 megapixels (the listed photos have at
#: most 2). Decoding one that large holds about 70 MB of coefficients and,
#: briefly, four times as much for the transform.
MAX_PIXELS = 1 << 24

#: Natural (row-major) index of the k-th coefficient in zigzag order.
ZIGZAG = [
    0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5,
    12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28,
    35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51,
    58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63,
]  # fmt: skip


class JpegError(ValueError):
    """A JPEG this decoder does not read."""


def decode_jpeg(data):
    """A baseline JPEG as a 2D uint8 luma array."""
    try:
        return _Decoder(bytes(data)).run()
    except IndexError as e:
        raise JpegError(f"truncated JPEG: {e}") from e


def _huffman_table(counts, symbols):
    """A 65536-entry lookup from the next 16 bits to (length << 8) | symbol;
    0 where no code starts with those bits."""
    if sum(counts) != len(symbols) or len(symbols) > 256:
        raise JpegError("bad Huffman table")
    table = [0] * 65536
    code, at = 0, 0
    for length in range(1, 17):
        for _ in range(counts[length - 1]):
            span = 1 << (16 - length)
            start = code << (16 - length)
            if start + span > 65536:
                raise JpegError("Huffman codes overflow")
            table[start : start + span] = [(length << 8) | symbols[at]] * span
            code += 1
            at += 1
        code <<= 1
    return table


class _Decoder:
    def __init__(self, data):
        self.data = data
        self.at = 0
        self.quant = {}
        self.dc, self.ac = {}, {}
        self.restart = 0
        self.frame = None
        self.adobe = None
        self.coefs = None
        self.luma_quant = None

    def u16(self, at):
        if at + 2 > len(self.data):
            raise JpegError("truncated JPEG")
        return (self.data[at] << 8) | self.data[at + 1]

    def segment(self):
        """The body of the marker segment at self.at, which it skips."""
        length = self.u16(self.at)
        if length < 2 or self.at + length > len(self.data):
            raise JpegError("bad segment length")
        body = self.data[self.at + 2 : self.at + length]
        self.at += length
        return body

    def run(self):
        if self.data[:2] != b"\xff\xd8":
            raise JpegError("not a JPEG")
        self.at = 2
        while True:
            if self.at + 2 > len(self.data):
                raise JpegError("no end of image")
            if self.data[self.at] != 0xFF:
                raise JpegError("expected a marker")
            marker = self.data[self.at + 1]
            self.at += 2
            if marker == 0xFF:  # fill byte
                self.at -= 1
            elif marker == 0xD9:  # EOI
                break
            elif marker in (0xC0, 0xC1):
                self.read_frame(self.segment())
            elif 0xC2 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                raise JpegError(f"unsupported JPEG process (SOF{marker - 0xC0})")
            elif marker == 0xCC:
                raise JpegError("arithmetic coding is not supported")
            elif marker == 0xC4:
                self.read_huffman(self.segment())
            elif marker == 0xDB:
                self.read_quant(self.segment())
            elif marker == 0xDD:
                body = self.segment()
                if len(body) != 2:
                    raise JpegError("bad restart interval")
                self.restart = (body[0] << 8) | body[1]
            elif marker == 0xEE:
                body = self.segment()
                if body[:5] == b"Adobe" and len(body) >= 12:
                    self.adobe = body[11]
            elif marker == 0xDA:
                self.read_scan(self.segment())
            elif 0xD0 <= marker <= 0xD7 or marker == 0x01:
                pass  # stray restart or TEM: no length
            else:
                self.segment()  # APPn, COM and the like
        if self.coefs is None:
            raise JpegError("no scan for the luma component")
        return self.finish()

    def read_quant(self, body):
        at = 0
        while at < len(body):
            precision, slot = body[at] >> 4, body[at] & 15
            if precision > 1 or slot > 3:
                raise JpegError("bad quantization table")
            size = 128 if precision else 64
            values = body[at + 1 : at + 1 + size]
            if len(values) != size:
                raise JpegError("truncated quantization table")
            if precision:
                values = [(values[2 * i] << 8) | values[2 * i + 1] for i in range(64)]
            table = [0] * 64
            for k in range(64):
                table[ZIGZAG[k]] = values[k]
            self.quant[slot] = table
            at += 1 + size

    def read_huffman(self, body):
        at = 0
        while at < len(body):
            kind, slot = body[at] >> 4, body[at] & 15
            if kind > 1 or slot > 3:
                raise JpegError("bad Huffman table")
            counts = list(body[at + 1 : at + 17])
            if len(counts) != 16:
                raise JpegError("truncated Huffman table")
            total = sum(counts)
            symbols = list(body[at + 17 : at + 17 + total])
            if len(symbols) != total:
                raise JpegError("truncated Huffman table")
            (self.ac if kind else self.dc)[slot] = _huffman_table(counts, symbols)
            at += 17 + total

    def read_frame(self, body):
        if self.frame is not None:
            raise JpegError("more than one frame")
        if len(body) < 6:
            raise JpegError("bad frame header")
        precision, height, width, count = body[0], (body[1] << 8) | body[2], (body[3] << 8) | body[4], body[5]
        if precision != 8:
            raise JpegError(f"{precision}-bit samples are not supported")
        if height == 0:
            raise JpegError("height defined later (DNL) is not supported")
        if width == 0 or width * height > MAX_PIXELS:
            raise JpegError(f"unsupported size {width}×{height}")
        if count not in (1, 3) or len(body) != 6 + 3 * count:
            raise JpegError(f"unsupported component count {count}")
        components = []
        for i in range(count):
            ident, factors, slot = body[6 + 3 * i : 9 + 3 * i]
            h, v = factors >> 4, factors & 15
            if not (1 <= h <= 4 and 1 <= v <= 4) or slot > 3:
                raise JpegError("bad component")
            components.append({"id": ident, "h": h, "v": v, "q": slot})
        hmax = max(c["h"] for c in components)
        vmax = max(c["v"] for c in components)
        mcux, mcuy = -(-width // (8 * hmax)), -(-height // (8 * vmax))
        for c in components:
            # The component's own size and the block grid that covers it
            # when MCUs interleave (a whole number of MCUs).
            c["width"] = -(-width * c["h"] // hmax)
            c["height"] = -(-height * c["v"] // vmax)
            c["cols"], c["rows"] = mcux * c["h"], mcuy * c["v"]
        self.frame = {"width": width, "height": height, "components": components, "mcux": mcux, "mcuy": mcuy}

    def read_scan(self, body):
        frame = self.frame
        if frame is None:
            raise JpegError("scan before frame")
        if len(body) < 1 or len(body) != 4 + 2 * body[0]:
            raise JpegError("bad scan header")
        count = body[0]
        if not 1 <= count <= len(frame["components"]):
            raise JpegError("bad scan component count")
        if body[-3:] != b"\x00\x3f\x00":
            raise JpegError("not a sequential scan")
        if len(frame["components"]) == 3 and self.adobe == 0:
            raise JpegError("Adobe RGB JPEG is not supported")
        by_id = {c["id"]: c for c in frame["components"]}
        scan = []
        for i in range(count):
            ident, tables = body[1 + 2 * i], body[2 + 2 * i]
            if ident not in by_id:
                raise JpegError("scan names an unknown component")
            dc, ac = tables >> 4, tables & 15
            if dc not in self.dc or ac not in self.ac:
                raise JpegError("scan uses a missing Huffman table")
            scan.append((by_id[ident], self.dc[dc], self.ac[ac]))
        luma = frame["components"][0]
        segments = self.entropy_segments()
        if count == 1:
            # A scan of one component has one block per MCU, row by row
            # over the blocks that cover the component (T.81 A.2.2).
            c = scan[0][0]
            cols, rows = -(-c["width"] // 8), -(-c["height"] // 8)
            mcus = [(x, y) for y in range(rows) for x in range(cols)]
            layout = [(scan[0], [(0, 0)], 1, 1)]
        else:
            mcus = [(x, y) for y in range(frame["mcuy"]) for x in range(frame["mcux"])]
            layout = [
                (s, [(dx, dy) for dy in range(s[0]["v"]) for dx in range(s[0]["h"])], s[0]["h"], s[0]["v"])
                for s in scan
            ]
        if any(s[0] is luma for s in scan):
            if self.coefs is not None:
                raise JpegError("more than one scan of the luma component")
            # A table may be redefined after this scan; this one applies.
            if luma["q"] not in self.quant:
                raise JpegError("missing quantization table")
            self.luma_quant = self.quant[luma["q"]]
            # Every block takes at least two bits (a DC code and an end of
            # block), so the file must hold that many before the blocks'
            # memory is taken.
            blocks = luma["cols"] * luma["rows"]
            if 2 * blocks > 8 * len(self.data):
                raise JpegError("file too short for the size its frame claims")
            self.coefs = array("i", bytes(4 * 64 * blocks))
        self.decode(segments, mcus, layout, luma)

    def entropy_segments(self):
        """The entropy-coded data after a scan header, unstuffed and split at
        restart markers. Leaves self.at on the marker that ends it."""
        data, at, start = self.data, self.at, self.at
        segments = []
        while True:
            at = data.find(b"\xff", at)
            if at < 0 or at + 1 >= len(data):
                raise JpegError("scan runs to the end of the file")
            following = data[at + 1]
            if following == 0x00:
                at += 2
            elif 0xD0 <= following <= 0xD7:
                segments.append(data[start:at].replace(b"\xff\x00", b"\xff"))
                at += 2
                start = at
            elif following == 0xFF:
                at += 1  # fill byte before a marker
            else:
                segments.append(data[start:at].replace(b"\xff\x00", b"\xff"))
                self.at = at
                return segments

    def decode(self, segments, mcus, layout, luma):
        coefs = self.coefs
        restart = self.restart or len(mcus)
        if -(-len(mcus) // restart) > len(segments):
            raise JpegError("fewer restart intervals than the scan needs")
        scratch = [0] * 64
        for interval in range(-(-len(mcus) // restart)):
            # Peeking reads three bytes from any byte offset, padded with
            # ones as T.81 F.2.2.5 has the encoder do.
            seg = segments[interval]
            padded = seg + b"\xff\xff\xff\xff"
            words = [(padded[i] << 16) | (padded[i + 1] << 8) | padded[i + 2] for i in range(len(seg) + 2)]
            limit = 8 * len(seg)
            pos = 0
            preds = [0] * len(layout)
            for mx, my in mcus[interval * restart : (interval + 1) * restart]:
                for n, ((comp, dc, ac), units, h, v) in enumerate(layout):
                    store = comp is luma
                    for dx, dy in units:
                        if store:
                            index = ((my * v + dy) * comp["cols"] + mx * h + dx) * 64
                            block = coefs
                        else:
                            index, block = 0, scratch
                        # DC: a category, then that many bits.
                        entry = dc[(words[pos >> 3] >> (8 - (pos & 7))) & 0xFFFF]
                        if not entry:
                            raise JpegError("bad Huffman code")
                        pos += entry >> 8
                        size = entry & 0xFF
                        if size > 11:
                            raise JpegError("bad DC category")
                        diff = 0
                        if size:
                            diff = ((words[pos >> 3] >> (8 - (pos & 7))) & 0xFFFF) >> (16 - size)
                            pos += size
                            if diff < 1 << (size - 1):
                                diff -= (1 << size) - 1
                        preds[n] += diff
                        block[index] = preds[n]
                        # AC: run and size pairs to end of block.
                        k = 1
                        while k < 64:
                            entry = ac[(words[pos >> 3] >> (8 - (pos & 7))) & 0xFFFF]
                            if not entry:
                                raise JpegError("bad Huffman code")
                            pos += entry >> 8
                            rs = entry & 0xFF
                            run, size = rs >> 4, rs & 15
                            if size == 0:
                                if run != 15:
                                    break
                                k += 16
                                continue
                            k += run
                            if k > 63:
                                raise JpegError("coefficient past the end of the block")
                            value = ((words[pos >> 3] >> (8 - (pos & 7))) & 0xFFFF) >> (16 - size)
                            pos += size
                            if value < 1 << (size - 1):
                                value -= (1 << size) - 1
                            block[index + ZIGZAG[k]] = value
                            k += 1
                        if k > 64:
                            raise JpegError("coefficient past the end of the block")
                        if pos > limit:
                            raise JpegError("scan data ends mid-block")

    def finish(self):
        import numpy as np

        luma = self.frame["components"][0]
        rows, cols = luma["rows"], luma["cols"]
        blocks = np.frombuffer(self.coefs, np.int32).astype(np.float64).reshape(rows * cols, 8, 8)
        blocks *= np.array(self.luma_quant, np.float64).reshape(8, 8)
        # Inverse DCT as basis[u, x] = c(u)/2 cos((2x + 1)uπ/16), applied
        # along rows and columns (T.81 A.3.3).
        u = np.arange(8)[:, None]
        x = np.arange(8)[None, :]
        basis = np.cos((2 * x + 1) * u * np.pi / 16) / 2
        basis[0] /= np.sqrt(2)
        pixels = np.einsum("vy,nvu,ux->nyx", basis, blocks, basis) + 128
        image = pixels.reshape(rows, cols, 8, 8).transpose(0, 2, 1, 3).reshape(rows * 8, cols * 8)
        image = np.clip(np.rint(image), 0, 255).astype(np.uint8)
        return image[: luma["height"], : luma["width"]].copy()
