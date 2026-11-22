"""Tests for the photo tier. Run from the repository root: python -m unittest discover -s tests"""

import email.message
import hashlib
import http.client
import io
import json
import os
import re
import struct
import tempfile
import unittest
import urllib.error
import zipfile
import zlib
from unittest import mock

import numpy as np

from barbench import pgm, photos, slices


def facts(group, added="2026-11-21"):
    """The facts every listed photo carries: its group, that group's slice,
    and when it was added."""
    return {"group": group, "slice": slices.of_group(group), "added": added}


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
                {"file": "a/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), **facts("test/a"),
                 "symbols": [{"symbology": "code128", "text": "ABC"}], "source": "a"},
                {"file": "b/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), **facts("test/b", "2026-12-01"),
                 "symbols": [{"symbology": "ean13", "text": None}]},
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
            manifest = photos.generate(out, cache, [self.listing], self.opener(self.png))
            self.assertEqual(
                self.urls[0], "https://raw.githubusercontent.com/example/photos/" + "0" * 40 + "/images/a/1.png"
            )
            self.assertEqual(manifest["grid"], {"source": ["a"]})
            self.assertEqual([e["expected"] for e in manifest["images"]], ["ABC", None])
            self.assertEqual(manifest["images"][1]["symbols"], [{"symbology": "ean13", "text": None}])
            self.assertEqual([e["file"] for e in manifest["images"]], ["test-a-1.pgm", "test-b-1.pgm"])
            self.assertEqual((manifest["slices"], manifest["added_after"]), (slices.SLICES, None))
            self.assertEqual([(e["group"], e["slice"], e["added"]) for e in manifest["images"]],
                             [(f"test/{g}", slices.of_group(f"test/{g}"), d) for g, d in (("a", "2026-11-21"),
                                                                                         ("b", "2026-12-01"))])
            # Corners must lie in the photo (6 by 4 pixels), and reach the manifest.
            corners = [[0, 0], [6, 0], [6, 4], [0, 4]]
            self.listing["images"][0]["symbols"][0]["corners"] = corners
            manifest = photos.generate(out, cache, [self.listing], self.opener(self.png))
            self.assertEqual(manifest["images"][0]["symbols"][0]["corners"], corners)
            for corner in ([6.5, 4], [6, 4.5]):
                corners[2] = corner
                with self.assertRaisesRegex(ValueError, "test: a/1.png: corners outside its 6x4 pixels"):
                    photos.generate(out, cache, [self.listing], self.opener(self.png))
            del self.listing["images"][0]["symbols"][0]["corners"]
            self.assertEqual(manifest["photos"][0]["license"], "Apache-2.0")
            image = pgm.read(os.path.join(out, manifest["images"][0]["file"]))
            self.assertEqual(image.shape, (4, 6))
            self.assertTrue((image == 200).all())
            # A second run downloads nothing.
            photos.generate(out, cache, [self.listing], self.opener(b"never read"))
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
            self.assertFalse(os.path.exists(photos.cache_path(self.listing, "a/1.png", cache)))

    def test_a_set_keeps_its_photos_in_its_own_folder(self):
        # Plain names of PNGs and JPEGs are taken too, each set's under a
        # folder of its name, so two sets' files of one name never meet;
        # ZXing's stay under the cache itself, where they always were.
        jpg = jpeg_bytes()
        flat = dict(self.listing, images=[
            dict(self.listing["images"][0], file="label.png"),
            dict(self.listing["images"][1], file="label-2.jpg", sha256=hashlib.sha256(jpg).hexdigest()),
        ])
        served = {"label.png": self.png, "label-2.jpg": jpg}

        def opener(request, timeout=None):
            self.urls.append(request.full_url)
            return FakeResponse(served[request.full_url.rsplit("/", 1)[1]])

        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out:
            manifest = photos.generate(out, cache, [flat], opener)
            self.assertEqual(sorted(os.listdir(os.path.join(cache, "test"))), ["label-2.jpg", "label.png"])
            self.assertEqual(self.urls[-1], "https://raw.githubusercontent.com/example/photos/" + "0" * 40
                             + "/images/label-2.jpg")
            self.assertEqual([e["file"] for e in manifest["images"]], ["test-label.pgm", "test-label-2.pgm"])
            self.assertEqual(pgm.read(os.path.join(out, "test-label-2.pgm")).shape, (8, 8))
        self.assertEqual(photos.cache_path(dict(flat, name="zxing-blackbox"), "a/1.png", "c"), os.path.join("c", "a/1.png"))
        self.assertEqual(photos.cache_path(flat, "a/1.png", "c"), os.path.join("c", "test", "a/1.png"))
        with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, "not a set's name"):
            photos.fetch(dict(flat, name="../up"), cache, opener)

    def test_names_that_leave_the_cache_are_refused(self):
        for name in ("../escape.png", "/etc/x.png", "a/../../x.png", "a/1.txt", "a/b.jpg", "../x.jpg", "x.gif"):
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


def jpeg_bytes():
    """A tiny valid baseline JPEG, from the JPEG tests' encoder."""
    import sys

    # Importable however the tests are run: discovered or by module name.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_jpeg import encode, picture

    return encode([picture(8, 8, 1)], [(1, 1)])[0]


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.photo = jpeg_bytes()
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("set/img/Foto(1).jpg", self.photo)
            z.writestr("set/img/2.jpg", self.photo + b"tail")
            z.writestr("set/big.jpg", b"\0" * (photos.MAX_BYTES + 1))
            z.writestr("../outside.jpg", self.photo)
        self.archive = buffer.getvalue()
        self.listing = {
            "name": "zipped",
            "archive": "https://example.org/set.zip",
            "archive_sha256": hashlib.sha256(self.archive).hexdigest(),
            "archive_bytes": len(self.archive),
            "source": "https://example.org/",
            "license": "CC-BY-3.0",
            "notice": "test",
            "images": [
                {"file": "set/img/Foto(1).jpg", "sha256": hashlib.sha256(self.photo).hexdigest(),
                 **facts("zipped/4006381333931"),
                 "symbols": [{"symbology": "ean13", "text": "4006381333931"}]},
            ],
        }
        self.urls = []

    def opener(self, data):
        def open_(request, timeout=None):
            self.assertEqual(timeout, photos.TIMEOUT)
            self.urls.append(request.full_url)
            return FakeResponse(data)

        return open_

    def test_listed_photos_are_taken_from_the_archive_and_cached(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out:
            manifest = photos.generate(out, cache, [self.listing], self.opener(self.archive))
            self.assertEqual(self.urls, ["https://example.org/set.zip"])
            row = manifest["images"][0]
            self.assertEqual(row["file"], "zipped-set-img-Foto1.pgm")
            self.assertEqual((row["expected"], row["symbols"][0]["text"], row["set"]),
                             (None, "4006381333931", "zipped"))
            self.assertEqual(pgm.read(os.path.join(out, row["file"])).shape, (8, 8))
            self.assertEqual(manifest["photos"][0]["archive_sha256"], self.listing["archive_sha256"])
            # Only the listed photo is kept; the archive is gone.
            kept = [os.path.relpath(os.path.join(d, f), cache) for d, _, fs in os.walk(cache) for f in fs]
            self.assertEqual(kept, [os.path.join("zipped", "set", "img", "Foto(1).jpg")])
            photos.generate(out, cache, [self.listing], self.opener(b"never read"))
            self.assertEqual(len(self.urls), 1)

    def test_a_wrong_archive_is_refused(self):
        cases = {
            "digest": (ValueError, self.archive[:-1] + bytes([self.archive[-1] ^ 1])),
            "larger than the listed": (ValueError, self.archive + b"x"),
            # Cut short: tried again, then given up.
            "not the listed": (ConnectionError, self.archive[:-1]),
        }
        for message, (error, data) in cases.items():
            self.urls = []
            with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep"):
                with self.assertRaisesRegex(error, message):
                    photos.fetch(self.listing, cache, self.opener(data))
                self.assertEqual(os.listdir(os.path.join(cache, "zipped")), [])
            self.assertEqual(len(self.urls), photos.ARCHIVE_TRIES if error is ConnectionError else 1)

    def test_wrong_members_are_refused(self):
        cases = {
            "set/img/2.jpg": "digest",
            "set/img/3.jpg": "not in the archive",
            "set/big.jpg": "larger than",
        }
        for name, message in cases.items():
            self.listing["images"][0]["file"] = name
            with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, message):
                photos.fetch(self.listing, cache, self.opener(self.archive))

    def test_names_that_leave_the_cache_are_refused(self):
        for name in ("../outside.jpg", "/etc/x.jpg", "set/../../x.jpg", "set/x.png", "set/x y.jpg"):
            self.listing["images"][0]["file"] = name
            with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, "name"):
                photos.fetch(self.listing, cache, self.opener(self.archive))
        self.listing["images"][0]["file"] = "set/img/Foto(1).jpg"
        self.listing["name"] = "../up"
        with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, "name"):
            photos.fetch(self.listing, cache, self.opener(self.archive))

    def test_a_failed_download_is_tried_again(self):
        calls = []

        def flaky(request, timeout=None):
            calls.append(request.full_url)
            if len(calls) == 1:
                return FakeResponse(self.archive[:100])  # cut short
            return FakeResponse(self.archive)

        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep") as sleep:
            photos.fetch(self.listing, cache, flaky)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(2)

    def test_an_archive_that_keeps_failing_is_given_up(self):
        calls = []

        def down(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.URLError("server error")

        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.URLError):
                photos.fetch(self.listing, cache, down)
            self.assertEqual(os.listdir(os.path.join(cache, "zipped")), [])
        self.assertEqual(len(calls), photos.ARCHIVE_TRIES)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, 8, 16])


def http_error(code, retry_after=None):
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://upload.wikimedia.org/", code, "error", headers, io.BytesIO())


class CommonsTest(unittest.TestCase):
    URL = "https://upload.wikimedia.org/wikipedia/commons/{}/{}/Label_{}.jpg"

    def setUp(self):
        self.photo = jpeg_bytes()
        digest = hashlib.sha256(self.photo).hexdigest()
        self.listing = {
            "name": "commons-test",
            "host": "upload.wikimedia.org",
            "source": "https://commons.wikimedia.org/wiki/Category:Code_128",
            "license": "CC0-1.0 AND CC-BY-4.0",
            "notice": "test",
            "images": [
                {"file": "label-1.jpg", "url": self.URL.format("a", "ab", 1), "sha256": digest,
                 **facts("commons-test/label-1"),
                 "symbols": [{"symbology": "code128", "text": "ABC"}], "source": "commons",
                 "author": "A. Author", "license": "CC0-1.0",
                 "page": "https://commons.wikimedia.org/wiki/File:Label_1.jpg"},
                {"file": "label-2.jpg", "url": self.URL.format("0", "0f", "%28two%29"), "sha256": digest,
                 **facts("commons-test/label-2"),
                 "symbols": [{"symbology": "code128", "text": "DEF"}, {"symbology": "qr", "text": None}],
                 "source": "commons", "author": "B. Author", "license": "CC-BY-4.0",
                 "page": "https://commons.wikimedia.org/wiki/File:Label_(two).jpg"},
            ],
        }
        self.urls = []

    def opener(self, *answers):
        """Answers each request with the next of `answers` (bytes, or an
        error to raise); the last one repeats."""
        answers = list(answers)

        def open_(request, timeout=None):
            self.assertEqual(timeout, photos.TIMEOUT)
            # Wikimedia asks for contact details: the project's repository.
            self.assertEqual(
                request.get_header("User-agent"), "barbench/0.1 (https://github.com/barbench/barbench)"
            )
            self.urls.append(request.full_url)
            answer = answers.pop(0) if len(answers) > 1 else answers[0]
            if isinstance(answer, Exception):
                raise answer
            return FakeResponse(answer)

        return open_

    def test_photos_are_downloaded_one_at_a_time_checked_and_cached(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out, \
                mock.patch.object(photos.time, "sleep") as sleep:
            manifest = photos.generate(out, cache, [self.listing], self.opener(self.photo))
            self.assertEqual(self.urls, [e["url"] for e in self.listing["images"]])
            # A pause between downloads, none before the first.
            sleep.assert_called_once_with(photos.COMMONS_PAUSE)
            rows = manifest["images"]
            self.assertEqual([r["file"] for r in rows], ["commons-test-label-1.pgm", "commons-test-label-2.pgm"])
            self.assertEqual([r["expected"] for r in rows], ["ABC", "DEF"])
            # Each photo carries its own credit; the set, where they come from.
            self.assertEqual([(r["author"], r["license"]) for r in rows],
                             [("A. Author", "CC0-1.0"), ("B. Author", "CC-BY-4.0")])
            self.assertEqual(rows[1]["page"], "https://commons.wikimedia.org/wiki/File:Label_(two).jpg")
            self.assertEqual(manifest["photos"][0]["host"], "upload.wikimedia.org")
            self.assertEqual(manifest["grid"], {"source": ["commons"]})
            self.assertEqual(pgm.read(os.path.join(out, rows[0]["file"])).shape, (8, 8))
            self.assertEqual(sorted(os.listdir(os.path.join(cache, "commons-test"))), ["label-1.jpg", "label-2.jpg"])
            photos.generate(out, cache, [self.listing], self.opener(b"never read"))
            self.assertEqual(len(self.urls), 2)

    def test_too_many_requests_are_waited_out(self):
        del self.listing["images"][1]
        answers = (http_error(429, "7"), http_error(503, "86400"), http_error(429, "Wed, 21 Oct 2026 07:28:00 GMT"))
        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep") as sleep:
            photos.fetch(self.listing, cache, self.opener(*answers, self.photo))
        self.assertEqual(len(self.urls), 4)
        # As Retry-After says, at most MAX_WAIT, and backing off without one.
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [7, photos.MAX_WAIT, 8])

    def test_cut_short_transfers_and_odd_retry_afters_are_tried_again(self):
        del self.listing["images"][1]
        # A transfer cut short; a digit int() does not take; far too long.
        answers = (http.client.IncompleteRead(b""), http_error(429, "²"), http_error(429, "9" * 30))
        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep") as sleep:
            photos.fetch(self.listing, cache, self.opener(*answers, self.photo))
        self.assertEqual(len(self.urls), 4)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2, 4, photos.MAX_WAIT])

    def test_a_photo_that_keeps_failing_is_given_up(self):
        with tempfile.TemporaryDirectory() as cache, mock.patch.object(photos.time, "sleep"):
            with self.assertRaises(urllib.error.HTTPError):
                photos.fetch(self.listing, cache, self.opener(http_error(429, "1")))
            self.assertFalse(os.path.exists(os.path.join(cache, "commons-test")))
        self.assertEqual(len(self.urls), photos.COMMONS_TRIES)

    def test_a_wrong_or_oversized_download_is_refused(self):
        with tempfile.TemporaryDirectory() as cache:
            with self.assertRaisesRegex(ValueError, "digest"):
                photos.fetch(self.listing, cache, self.opener(self.photo + b"x"))
            huge = b"\0" * (photos.MAX_BYTES + 10)
            self.listing["images"][0]["sha256"] = hashlib.sha256(huge[: photos.MAX_BYTES + 1]).hexdigest()
            with self.assertRaisesRegex(ValueError, "larger than"):
                photos.fetch(self.listing, cache, self.opener(huge))
            self.assertFalse(os.path.exists(os.path.join(cache, "commons-test", "label-1.jpg")))

    def test_names_urls_and_hosts_that_are_not_pinned_are_refused(self):
        def refused(message, **change):
            listing = json.loads(json.dumps(self.listing))
            for key, value in change.items():
                if key in ("file", "url"):
                    listing["images"][0][key] = value
                else:
                    listing[key] = value
            with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(ValueError, message):
                photos.fetch(listing, cache, self.opener(self.photo))
            self.assertEqual(self.urls, [])

        for name in ("../x.jpg", "a/b.jpg", "/etc/x.jpg", "Label.jpg", "x.png", "x.jpg.part"):
            refused("name", file=name)
        for url in (
            "http://upload.wikimedia.org/wikipedia/commons/a/ab/Label.jpg",
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/Label.jpg?width=800",
            "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Label.jpg/800px-Label.jpg",
            "https://upload.wikimedia.org/wikipedia/commons/a/bb/Label.jpg",
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/../../x.jpg",
            "https://upload.wikimedia.org.example.org/wikipedia/commons/a/ab/Label.jpg",
        ):
            refused("pinned", url=url)
        refused("host", host="example.org")
        refused("name", name="../up")


class ListTest(unittest.TestCase):
    def test_the_committed_lists(self):
        listings = {listing["name"]: listing for listing in photos.load_lists()}
        self.assertEqual(sorted(listings), ["artelab-medium", "commons-code128", "kaist-real", "maintainer-photos",
                                            "zxing-blackbox"])
        licences = {
            "zxing-blackbox": "Apache-2.0",
            "kaist-real": "CC-BY-4.0",
            "artelab-medium": "CC-BY-3.0",
            "commons-code128": "CC-BY-4.0 AND CC0-1.0 AND LicenseRef-Public-Domain",
            "maintainer-photos": "CC0-1.0",
        }
        counts = {"zxing-blackbox": (120, 40), "kaist-real": (524, 0), "artelab-medium": (345, 0),
                  "commons-code128": (13, 13), "maintainer-photos": (14, 2)}
        digests = set()
        for name, listing in listings.items():
            self.assertEqual(listing["license"], licences[name])
            images = listing["images"]
            self.assertTrue(listing["truth"])
            positives = [e for e in images if photos.expected(e) is not None]
            self.assertEqual((len(images), len(positives)), counts[name])
            digests |= {e["sha256"] for e in images}
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", e["sha256"]) for e in images))
            self.assertTrue(all(photos.expected(e).isprintable() for e in positives))
            # Every photo shows at least the symbol it was listed for.
            self.assertTrue(all(e["symbols"] for e in images))
            if "archive" in listing:
                self.assertTrue(listing["archive"].startswith("https://"))
                self.assertRegex(listing["archive_sha256"], r"^[0-9a-f]{64}$")
                self.assertLessEqual(listing["archive_bytes"], photos.MAX_ARCHIVE_BYTES)
                self.assertTrue(all(photos.MEMBER_NAME.fullmatch(e["file"]) for e in images))
            elif "host" in listing:
                # Licensed photo by photo, each from the allowlist and in
                # the set's licence, with its author and file page.
                self.assertEqual(listing["host"], photos.COMMONS_HOST)
                allowed = {"CC0-1.0", "CC-BY-3.0", "CC-BY-4.0", "LicenseRef-Public-Domain"}
                for e in images:
                    self.assertTrue(photos.LOCAL_NAME.fullmatch(e["file"]), e["file"])
                    self.assertTrue(photos.COMMONS_URL.fullmatch(e["url"]), e["url"])
                    self.assertTrue(e["page"].startswith("https://commons.wikimedia.org/wiki/File:"))
                    self.assertIn(e["license"], allowed)
                    self.assertIn(e["license"], listing["license"].split(" AND "))
                    self.assertTrue(e["author"])
                self.assertEqual(len({e["file"] for e in images}), len(images))
            else:
                self.assertRegex(listing["commit"], r"^[0-9a-f]{40}$")
                self.assertTrue(all(photos.FILE_NAME.fullmatch(e["file"]) for e in images))
        # No photo is listed twice, within or across sets.
        self.assertEqual(len(digests), sum(len(listing["images"]) for listing in listings.values()))

    def test_the_committed_symbols(self):
        listings = {listing["name"]: listing for listing in photos.load_lists()}
        # KAIST names each crop by its symbol's 13 digits; UPC-A drops the 0.
        for e in listings["kaist-real"]["images"]:
            digits = e["file"].removeprefix("real/img/").removesuffix(".jpg")
            (symbol,) = e["symbols"]
            self.assertEqual(symbol, {"symbology": "upca", "text": digits[1:]} if digits[0] == "0"
                             else {"symbology": "ean13", "text": digits})
        # ZXing's folders are for one symbology each, every text known;
        # some code128-2 photos show other codes too.
        others = {}
        for e in listings["zxing-blackbox"]["images"]:
            symbol = e["symbols"][0]
            self.assertEqual(symbol["symbology"], {"code128-2": "code128", "ean13-2": "ean13",
                                                   "upca-2": "upca"}[e["file"].split("/")[0]])
            self.assertIsNotNone(symbol["text"])
            if e["symbols"][1:]:
                others[e["file"]] = [s["symbology"] for s in e["symbols"][1:]]
        self.assertEqual(others, {
            **{f"code128-2/{n:02d}.png": [None] * k
               for n, k in ((4, 2), (7, 1), (8, 1), (9, 1), (13, 3), (14, 3), (16, 3), (25, 1), (27, 1), (35, 1))},
            **{f"code128-2/{n}.png": ["itf"] for n in (37, 38, 39, 40)},
        })
        # Every Code 128 symbol has its corners, and only those do.
        for listing in listings.values():
            for e in listing["images"]:
                for s in e["symbols"]:
                    self.assertEqual("corners" in s, s["symbology"] == "code128", e["file"])
        # Every ArTe-Lab photo's digits are known; for three, read by eye
        # from the line printed under the bars.
        texts = {e["file"]: e["symbols"][0]["text"] for e in listings["artelab-medium"]["images"]}
        self.assertNotIn(None, texts.values())
        self.assertEqual([texts[f"BarcodeDatasets/{f}.jpg"] for f in ("Dataset1/05102009199", "Dataset2/Foto(844)",
                                                                       "Dataset2/Foto(849)")],
                         ["8002330112752", "7321925005431", "7321925005431"])
        # The maintainer photos' symbologies; the two Code 128 texts were
        # measured from the bars, since neither prints its whole text.
        self.assertEqual({e["file"]: [s["symbology"] for s in e["symbols"]]
                          for e in listings["maintainer-photos"]["images"]}, {
            "holiday-park-brochure.jpg": ["qr"], "picture-book-isbn.jpg": ["ean13"],
            "pretzel-box-1.jpg": ["ean13"], "pretzel-box-2.jpg": ["ean13"],
            "bicycle-frame-label.jpg": ["code128"], "observation-deck-ticket.jpg": ["code39"],
            "newspaper-qr.jpg": ["qr"], "storage-pouffe-label.jpg": ["ean13"],
            "wrapper-ean-datamatrix.jpg": ["ean13", "datamatrix"], "periodical-ean-add-on.png": ["ean13"],
            "supermarket-receipt.jpg": ["code128"], "gel-pen-label.png": ["ean13"],
            "radiator-panel-label.jpg": ["itf"], "postage-datamatrix.png": ["datamatrix"],
        })
        self.assertEqual({e["file"]: photos.expected(e) for e in listings["maintainer-photos"]["images"]
                          if photos.expected(e)},
                         {"bicycle-frame-label.jpg": "GB15I00816", "supermarket-receipt.jpg": "993695272540"})
        # The Commons photos that show more than their Code 128 symbol.
        extra = {e["file"]: [s["symbology"] for s in e["symbols"][1:]]
                 for e in listings["commons-code128"]["images"] if len(e["symbols"]) > 1}
        self.assertEqual(extra, {
            "flagtag-sscc.jpg": [None],
            "dhl-parcel-label-2015.jpg": ["itf"],
            "deposit-voucher.jpg": ["aztec"],
            "zlin-lamp-0360202.jpg": ["qr"],
            "zlin-lamp-0360207.jpg": ["qr"],
            "zlin-lamp-0360220.jpg": ["qr"],
        })

    def test_symbols_are_checked(self):
        def listing(*symbols):
            return {"name": "s", "images": [{"file": "a.jpg", "symbols": list(symbols)}]}

        photos.check_symbols(listing())
        photos.check_symbols(listing({"symbology": "code128", "text": "A",
                                      "corners": [[1, 1], [9.5, 2], [9, 4.5], [1.5, 4]]}))
        photos.check_symbols(listing({"symbology": "upca", "text": "890444000335"},
                                     {"symbology": None, "text": None, "note": "covered"}))
        photos.check_symbols(listing({"symbology": "code39", "text": "CNXRMZW2R1"},
                                     {"symbology": "datamatrix", "text": None}))
        for symbols, message in (
            ([{"symbology": "ean13", "text": "9780804816633"}], "not the digits of a valid ean13"),
            ([{"symbology": "upca", "text": "0890444000335"}], "not the digits of a valid upca"),
            ([{"symbology": "ean13", "text": "0890444000335"}], "is a UPC-A"),
            ([{"symbology": "ean13", "text": "978080481663\uff12"}], "not the digits"),
            ([{"symbology": "code93", "text": "A"}], "unknown symbology 'code93'"),
            ([{"symbology": None, "text": "A"}], "text 'A' of symbology None"),
            ([{"symbology": "code128", "text": ""}], "text '' of symbology"),
            ([{"symbology": "code128", "text": 7}], "text 7 of symbology"),
            ([{"symbology": "qr", "text": None, "note": 7}], "note 7 is not text"),
            ([{"symbology": "code128"}], "not a symbology, a text"),
            ([{"symbology": "code128", "text": "A", "box": []}], "not a symbology, a text"),
            # Corners: four points, clockwise from start-top with y down.
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, 2]]}], "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, 2], [0, -1]]}], "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, 2], [0, float("nan")]]}],
             "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, True], [0, 2]]}], "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, float("inf")], [0, 2]]}],
             "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, 2 ** 1100], [0, 2]]}], "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [4, 2, 1], [0, 2]]}], "not four points"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [0, 2], [4, 2], [4, 0]]}], "clockwise"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 2], [4, 0], [0, 2]]}], "clockwise"),
            ([{"symbology": "qr", "text": None, "corners": [[0, 0], [4, 0], [8, 0], [0, 2]]}], "clockwise"),
            ([{"symbology": "code128", "text": None}], "Code 128 symbol of unknown text"),
            ([{"symbology": "code128", "text": "A"}, {"symbology": "code128", "text": "B"}], "different texts"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                photos.check_symbols(listing(*symbols))
        with self.assertRaisesRegex(ValueError, "lists no symbols"):
            photos.check_symbols({"name": "s", "images": [{"file": "a.jpg"}]})
        # A list file and a listing handed to generate are both checked.
        bad = {"name": "s", "images": [{"file": "a.jpg", **facts("s/a"), "symbols": [{"symbology": "x", "text": None}]}]}
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            with open(path, "w") as f:
                json.dump(bad, f)
            with self.assertRaisesRegex(ValueError, "unknown symbology 'x'"):
                photos.load_list(path)
            with self.assertRaisesRegex(ValueError, "unknown symbology 'x'"):
                photos.generate(d, d, [bad], opener=None)
        # Several Code 128 symbols of one text are one expected text.
        twice = {"symbols": [{"symbology": "code128", "text": "A"}] * 2 + [{"symbology": "qr", "text": None}]}
        self.assertEqual(photos.expected(twice), "A")
        self.assertIsNone(photos.expected({"symbols": [{"symbology": "ean13", "text": None}]}))

    def test_unknown_sets_are_refused(self):
        with self.assertRaisesRegex(ValueError, "no photo set named nope"):
            photos.load_lists(["nope"])
        self.assertEqual([listing["name"] for listing in photos.load_lists(["kaist-real"])], ["kaist-real"])

    def test_the_committed_rows(self):
        listings = {listing["name"]: listing for listing in photos.load_lists()}
        groups = {}
        for name, listing in listings.items():
            for e in listing["images"]:
                self.assertTrue(e["group"].startswith(f"{name}/"), e["file"])
                self.assertEqual(e["slice"], slices.of_group(e["group"]), e["file"])
                # Every photo so far came with the first lists, but for the
                # maintainer photos; a photo added later carries its own
                # date, and this pin moves with it.
                self.assertEqual(e["added"], "2026-11-23" if name == "maintainer-photos" else "2026-11-21", e["file"])
                groups.setdefault(e["group"], []).append(e["file"])
        # Near-duplicates travel together: the three Zlin lamp tags, and the
        # ArTe-Lab photos with no digits from the set, which show the same
        # products as their neighbours.
        self.assertEqual(len(groups["commons-code128/zlin-lamp-tags"]), 3)
        self.assertIn("BarcodeDatasets/Dataset1/05102009199.jpg", groups["artelab-medium/8002330112752"])
        self.assertIn("BarcodeDatasets/Dataset2/Foto(844).jpg", groups["artelab-medium/7321925005431"])
        # ZXing's four shots of each Code 128 label are one group.
        self.assertEqual(groups["zxing-blackbox/code128-2/005-3379497200006"],
                         [f"code128-2/{n:02d}.png" for n in range(1, 5)])
        # 600 groups by text, less the three photos and two lamp tags merged,
        # and 13 maintainer scenes, the two pretzel-box photos being one.
        self.assertEqual(len(groups["maintainer-photos/pretzel-box"]), 2)
        self.assertEqual(len(groups), 608)


class RowsTest(unittest.TestCase):
    """Every photo carries the facts held-out data is worked out from."""

    def listing(self, *rows):
        return {"name": "s", "images": [{"file": f"{i}.jpg", **row, "symbols": []} for i, row in enumerate(rows)]}

    def test_groups_slices_and_dates_are_checked(self):
        photos.check_rows(self.listing(facts("s/a"), facts("s/2026-12-01-depot")))
        for row, message in (
            ({"slice": "0", "added": "2026-11-21"}, "group None"),
            (facts("other/a"), "is not s/"),
            (facts("s/"), "is not s/"),
            (facts("s/a b"), "is not s/"),
            ({**facts("s/a"), "slice": "x"}, "slice 'x', but group 's/a' is in"),
            ({**facts("s/a"), "slice": None}, "slice None"),
            ({**facts("s/a"), "added": "2026-13-01"}, "not a date"),
            ({**facts("s/a"), "added": "21-11-2026"}, "not a date written"),
            ({key: value for key, value in facts("s/a").items() if key != "added"}, "not a date written"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                photos.check_rows(self.listing(row))

    def test_a_tier_takes_the_chosen_slices_and_dates(self):
        png = encode(np.full((4, 6, 3), 200, np.uint8), 2)
        first, second = "s/a", next(f"s/{i}" for i in range(100) if slices.of_group(f"s/{i}") != slices.of_group("s/a"))
        listing = {"name": "s", "repository": "https://github.com/example/photos", "commit": "0" * 40,
                   "path": "images", "license": "Apache-2.0", "notice": "test", "images": [
                       {"file": "a/1.png", "sha256": hashlib.sha256(png).hexdigest(), **facts(first),
                        "symbols": [{"symbology": "code128", "text": "A"}], "source": "a"},
                       {"file": "a/2.png", "sha256": hashlib.sha256(png).hexdigest(), **facts(second, "2026-12-01"),
                        "symbols": [{"symbology": "code128", "text": "A"}], "source": "a"}]}
        urls = []

        def opener(request, timeout=None):
            urls.append(request.full_url)
            return FakeResponse(png)

        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as out:
            manifest = photos.generate(out, cache, [listing], opener, chosen=slices.of_group(first))
            self.assertEqual([e["from"] for e in manifest["images"]], ["a/1.png"])
            self.assertEqual(manifest["slices"], slices.of_group(first))
            # Only the chosen photo was downloaded.
            self.assertEqual([u.rsplit("/", 1)[1] for u in urls], ["1.png"])
            manifest = photos.generate(out, cache, [listing], opener, added_after="2026-11-21")
            self.assertEqual(([e["from"] for e in manifest["images"]], manifest["added_after"]),
                             (["a/2.png"], "2026-11-21"))
            # Left out: the second photo's slice after a date before it was
            # added, as a reader's own runs leave out what it holds out.
            manifest = photos.generate(out, cache, [listing], opener, leave_out=(slices.of_group(second), "2026-11-30"))
            self.assertEqual(([e["from"] for e in manifest["images"]], manifest["left_out"]),
                             (["a/1.png"], [slices.of_group(second), "2026-11-30"]))
            manifest = photos.generate(out, cache, [listing], opener, leave_out=(slices.of_group(second), "2026-12-01"))
            self.assertEqual([e["from"] for e in manifest["images"]], ["a/1.png", "a/2.png"])
            for chosen, after, message in ((slices.of_group(first), "2026-11-21", "no listed photo is in slices"),
                                           (slices.SLICES, "2026-12-01", "added after 2026-12-01"),
                                           (slices.SLICES, "today", "not a date")):
                with self.assertRaisesRegex(ValueError, message):
                    photos.generate(out, cache, [listing], opener, chosen=chosen, added_after=after)

    def test_lists_are_checked_as_they_load(self):
        with tempfile.TemporaryDirectory() as d:
            bad = self.listing({**facts("s/a"), "slice": "x"})
            with open(os.path.join(d, "s.json"), "w") as f:
                json.dump(bad, f)
            with self.assertRaisesRegex(ValueError, "slice 'x'"):
                photos.load_list(os.path.join(d, "s.json"))
            with mock.patch.object(photos, "LISTINGS", d):
                with self.assertRaisesRegex(ValueError, "slice 'x'"):
                    photos.load_lists()
                with self.assertRaisesRegex(ValueError, "no photo sets named"):
                    photos.load_lists([])
            with self.assertRaisesRegex(ValueError, "slice 'x'"):
                photos.generate(d, d, [bad], opener=None)


if __name__ == "__main__":
    unittest.main()
