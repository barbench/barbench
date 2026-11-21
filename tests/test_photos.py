"""Tests for the photo tier. Run from bench/: python -m unittest discover -s tests"""

import hashlib
import io
import os
import re
import struct
import tempfile
import unittest
import zlib
from unittest import mock

import numpy as np

from barbench import pgm, photos


def chunk(kind, body):
    crc = zlib.crc32(kind + body) & 0xFFFFFFFF
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)


def encode(pixels, colour, palette=None):
    """`pixels` (height × width × channels, uint8) as a PNG, each row with
    the next of the five filters in turn, so all of them are exercised."""
    height, width, channels = pixels.shape
    stride = width * channels
    raw = bytearray()
    previous = np.zeros(stride, np.int64)
    for y in range(height):
        line = pixels[y].reshape(stride).astype(np.int64)
        kind = y % 5
        left = np.concatenate([np.zeros(channels, np.int64), line[:-channels]])
        corner = np.concatenate([np.zeros(channels, np.int64), previous[:-channels]])
        if kind == 0:
            pred = np.zeros(stride, np.int64)
        elif kind == 1:
            pred = left
        elif kind == 2:
            pred = previous
        elif kind == 3:
            pred = (left + previous) // 2
        else:
            p = left + previous - corner
            pa, pb, pc = abs(p - left), abs(p - previous), abs(p - corner)
            pred = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, previous, corner))
        raw.append(kind)
        raw.extend(((line - pred) & 0xFF).astype(np.uint8).tobytes())
        previous = line
    out = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, colour, 0, 0, 0))
    if palette is not None:
        out += chunk(b"PLTE", palette.astype(np.uint8).tobytes())
    return out + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def luma(rgb):
    rgb = rgb.astype(np.int64)
    return ((299 * rgb[..., 0] + 587 * rgb[..., 1] + 114 * rgb[..., 2] + 500) // 1000).astype(np.uint8)


class PngTest(unittest.TestCase):
    rng = np.random.default_rng(11)

    def test_every_colour_type_and_filter(self):
        rgb = self.rng.integers(0, 256, (11, 7, 3), dtype=np.uint8)
        gray = self.rng.integers(0, 256, (11, 7, 1), dtype=np.uint8)
        alpha = self.rng.integers(0, 256, (11, 7, 1), dtype=np.uint8)
        np.testing.assert_array_equal(photos.decode_png(encode(gray, 0)), gray[..., 0])
        np.testing.assert_array_equal(photos.decode_png(encode(rgb, 2)), luma(rgb))
        np.testing.assert_array_equal(
            photos.decode_png(encode(np.concatenate([gray, alpha], 2), 4)), gray[..., 0]
        )
        np.testing.assert_array_equal(
            photos.decode_png(encode(np.concatenate([rgb, alpha], 2), 6)), luma(rgb)
        )
        palette = self.rng.integers(0, 256, (16, 3))
        index = self.rng.integers(0, 16, (11, 7, 1), dtype=np.uint8)
        np.testing.assert_array_equal(
            photos.decode_png(encode(index, 3, palette)), luma(palette[index[..., 0]])
        )

    def test_ties_in_paeth_prediction(self):
        # Few distinct values make the predictor's ties common. A tie that
        # changes the prediction needs distinct neighbours, such as left 0,
        # up 3, corner 2, so values run to 7.
        rgb = self.rng.integers(0, 8, (40, 16, 3), dtype=np.uint8)
        np.testing.assert_array_equal(photos.decode_png(encode(rgb, 2)), luma(rgb))

    def test_unsupported_and_broken_files_are_refused(self):
        gray = np.zeros((3, 4, 1), np.uint8)
        good = encode(gray, 0)
        header = good[8:33]
        idat_at = good.index(b"IDAT") - 4
        cases = {
            "not a PNG": b"GIF89a",
            "16-bit": good.replace(header, header[:16] + b"\x10" + header[17:]),
            "truncated chunk": good[: idat_at + 10],
            "no image data": good[:33] + chunk(b"IEND", b""),
            "short image data": good[:33] + chunk(b"IDAT", zlib.compress(b"\0" * 5)) + chunk(b"IEND", b""),
            "bad header length": good[:8] + chunk(b"IHDR", header[8:20]) + good[33:],
            "bad palette": good[:8] + chunk(b"IHDR", header[8:17] + b"\x03" + header[18:21])
            + chunk(b"PLTE", b"\0" * 5)
            + good[33:],
            "index past palette": encode(np.full((3, 4, 1), 5, np.uint8), 3, np.zeros((2, 3))),
        }
        for name, bad in cases.items():
            with self.assertRaises(photos.PngError, msg=name):
                photos.decode_png(bad)

    def test_inflating_stops_at_the_image_size(self):
        # A small image whose data would inflate to far more.
        gray = np.zeros((2, 2, 1), np.uint8)
        good = encode(gray, 0)
        bomb = good[:33] + chunk(b"IDAT", zlib.compress(b"\0" * (64 << 20))) + chunk(b"IEND", b"")
        np.testing.assert_array_equal(photos.decode_png(bomb), np.zeros((2, 2), np.uint8))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.png = encode(np.full((4, 6, 3), 200, np.uint8), 2)
        self.listing = {
            "name": "test",
            "repository": "https://github.com/example/photos",
            "commit": "0" * 40,
            "path": "images",
            "license": "Apache-2.0",
            "notice": "test",
            "images": [
                {"file": "a/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), "expected": "ABC", "source": "a"},
                {"file": "b/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), "expected": None, "kind": "ean13"},
            ],
        }
        self.urls = []

    def opener(self, data):
        def open_(request, timeout=None):
            self.assertEqual(timeout, photos.TIMEOUT)
            self.urls.append(request.full_url)
            return FakeResponse(data)

        return open_

    def test_downloads_are_checked_and_cached(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out:
            manifest = photos.generate(out, cache, self.listing, self.opener(self.png))
            self.assertEqual(
                self.urls[0], "https://raw.githubusercontent.com/example/photos/" + "0" * 40 + "/images/a/1.png"
            )
            self.assertEqual(manifest["grid"], {"source": ["a"]})
            self.assertEqual([e["expected"] for e in manifest["images"]], ["ABC", None])
            self.assertEqual(manifest["images"][1]["kind"], "ean13")
            image = pgm.read(os.path.join(out, manifest["images"][0]["file"]))
            self.assertEqual(image.shape, (4, 6))
            self.assertTrue((image == 200).all())
            # A second run downloads nothing.
            photos.generate(out, cache, self.listing, self.opener(b"never read"))
            self.assertEqual(len(self.urls), 2)

    def test_a_wrong_or_oversized_download_is_refused(self):
        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaisesRegex(ValueError, "digest"):
                photos.fetch(self.listing, cache, self.opener(self.png + b"x"))
            # Oversized, with a digest that would match: the cap refuses it.
            huge = b"\0" * (photos.MAX_BYTES + 10)
            capped = hashlib.sha256(huge[: photos.MAX_BYTES + 1]).hexdigest()
            self.listing["images"][0]["sha256"] = capped
            with self.assertRaisesRegex(ValueError, "larger than"):
                photos.fetch(self.listing, cache, self.opener(huge))
            self.assertFalse(os.path.exists(os.path.join(cache, "a", "1.png")))

    def test_names_that_leave_the_cache_are_refused(self):
        for name in ("../escape.png", "/etc/x.png", "a/../../x.png", "a/1.txt"):
            self.listing["images"][0]["file"] = name
            with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, "name"):
                photos.fetch(self.listing, cache, self.opener(self.png))

    def test_a_failed_download_is_tried_again(self):
        calls = []

        def flaky(request, timeout=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                raise TimeoutError("stalled")
            return FakeResponse(self.png)

        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep"):
            photos.fetch(self.listing, cache, flaky)
        self.assertEqual(len(calls), 3)


class ListTest(unittest.TestCase):
    def test_the_committed_list(self):
        listing = photos.load_list()
        self.assertRegex(listing["commit"], r"^[0-9a-f]{40}$")
        self.assertEqual(listing["license"], "Apache-2.0")
        images = listing["images"]
        positives = [e for e in images if e["expected"] is not None]
        self.assertEqual((len(images), len(positives)), (120, 40))
        self.assertEqual(len({e["sha256"] for e in images}), len(images))
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) for e in images))
        self.assertTrue(all(e["expected"].isprintable() for e in positives))
        self.assertTrue(all(photos.FILE_NAME.fullmatch(e["file"]) for e in images))


if __name__ == "__main__":
    unittest.main()
