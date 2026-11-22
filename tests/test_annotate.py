"""Tests for the views that corners are placed and checked with."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import struct
import unittest
import zlib
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_photos import encode, facts  # noqa: E402

from barbench import __main__ as cli  # noqa: E402
from barbench import annotate, photos  # noqa: E402


def gradient(width=200, height=100):
    """A photo whose every pixel tells where it is."""
    x = np.arange(width)[None, :]
    y = np.arange(height)[:, None]
    return ((x + 2 * y) % 251).astype(np.uint8)


class ViewTest(unittest.TestCase):
    def test_a_view_samples_the_nearest_pixel_and_pads_outside(self):
        img = gradient()
        rgb = annotate.view(img, 10, 20, 14, 22, 8, 4)
        # Each photo pixel spans two by two view pixels, in all channels.
        self.assertEqual(rgb.shape, (4, 8, 3))
        self.assertTrue((rgb[0, 0] == img[20, 10]).all() and (rgb[1, 1] == img[20, 10]).all())
        self.assertTrue((rgb[3, 7] == img[21, 13]).all())
        outside = annotate.view(img, -4, -4, 0, 0, 4, 4)
        self.assertTrue((outside == 128).all())

    def test_grid_lines_and_labels_fall_on_photo_pixels(self):
        img = np.full((100, 200), 255, np.uint8)
        rgb = annotate.overview(img)
        self.assertEqual(rgb.shape, (100, 200, 3))
        # A 200-pixel photo gets a line every 20 pixels, labelled in red.
        for x in (0, 20, 100, 180):
            self.assertEqual(tuple(rgb[50, x]), annotate.GRID)
        self.assertEqual(tuple(rgb[50, 30]), (255, 255, 255))
        # The label "20" sits just right of its line, at the top.
        self.assertTrue((rgb[2:12, 22:36] == annotate.LABEL).all(axis=2).any())
        # A large photo is shown smaller, its grid still in photo pixels.
        big = annotate.overview(np.zeros((3000, 4000), np.uint8))
        self.assertEqual(big.shape[:2], (825, 1100))

    def test_zoom_marks_its_centre(self):
        rgb = annotate.zoom(gradient(), 50, 40, 10)
        self.assertEqual(rgb.shape, (600, 600, 3))
        self.assertEqual(tuple(rgb[300, 305]), annotate.FIRST)

    def test_corners_lay_out_four_marked_squares_in_reading_order(self):
        img = gradient()
        quad = [[20, 20], [180, 25], [175, 80], [25, 75]]
        rgb = annotate.corners(img, quad, r=10)
        side = annotate.TILE
        self.assertEqual(rgb.shape, (2 * side + 6, 2 * side + 6, 3))
        # start-top, stop-top on top; start-bottom, stop-bottom below.
        for (x, y), (row, col) in zip(quad, ((0, 0), (0, 1), (1, 1), (1, 0))):
            top, left = row * (side + 6), col * (side + 6)
            self.assertEqual(tuple(rgb[top + side // 2, left + side // 2 + 3]), annotate.MARK)
            # 20 view pixels are one photo pixel here: 30 off the mark,
            # between grid lines, the square shows the photo pixel down and
            # right of (x, y).
            self.assertEqual(rgb[top + side // 2 + 30, left + side // 2 + 30, 0], img[y + 1, x + 1])

    def test_the_overlay_draws_the_first_edge_green(self):
        img = np.full((100, 200), 255, np.uint8)
        rgb = annotate.overlay(img, [[20, 20], [180, 20], [180, 80], [20, 80]])
        self.assertEqual(tuple(rgb[20, 100]), annotate.FIRST)
        self.assertEqual(tuple(rgb[80, 100]), annotate.MARK)
        self.assertEqual(tuple(rgb[50, 180]), annotate.MARK)
        self.assertEqual(tuple(rgb[50, 100]), (255, 255, 255))

    def test_a_png_round_trips(self):
        rgb = np.stack([gradient()] * 3, axis=2)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.png")
            annotate.write_png(path, rgb)
            with open(path, "rb") as f:
                data = f.read()
            self.assertTrue((photos.decode_png(data) == gradient()).all())
            # Every chunk's CRC is right, which decode_png does not check.
            pos = 8
            while pos < len(data):
                (length,) = struct.unpack(">I", data[pos : pos + 4])
                body = data[pos + 4 : pos + 8 + length]
                self.assertEqual(struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0], zlib.crc32(body))
                pos += 12 + length

    def test_corners_are_parsed_or_refused(self):
        img = gradient()
        self.assertEqual(annotate.parse_quad("1,2;30,4;25.5,60;7,58", img), [[1, 2], [30, 4], [25.5, 60], [7, 58]])
        for bad, message in (
            ("1,2;3,4;5,6", "four points"),
            ("1,2;3,4;5,6;7", "four points"),
            ("a,2;3,4;5,6;7,8", "four points"),
            ("1,2;7,58;25.5,60;30,4", "clockwise"),
            ("1,2;30,4;25.5,nan;7,58", "four points"),
            ("1,2;30,4;inf,60;7,58", "four points"),
            ("1,2;300,4;250,60;7,58", "not in the photo's 200x100 pixels"),
        ):
            with self.assertRaisesRegex(ValueError, message):
                annotate.parse_quad(bad, img)

    def test_points_and_half_widths_must_fit_the_photo(self):
        img = gradient()
        for x, y, r, message in ((201, 50, 10, "not in the photo"), (50, -1, 10, "not in the photo"),
                                 (float("nan"), 50, 10, "not in the photo"), (50, 50, 0.5, "half-width"),
                                 (50, 50, 201, "half-width"), (50, 50, float("inf"), "half-width")):
            with self.assertRaisesRegex(ValueError, message):
                annotate.zoom(img, x, y, r)
        with self.assertRaisesRegex(ValueError, "half-width of 0"):
            annotate.corners(img, [[20, 20], [180, 25], [175, 80], [25, 75]], 0)

    def test_a_shrunk_view_keeps_its_grid_and_quadrilateral_in_photo_pixels(self):
        # 2200 by 1100 pixels, shown at half size.
        img = np.full((1100, 2200), 255, np.uint8)
        rgb = annotate.overview(img)
        self.assertEqual(rgb.shape[:2], (550, 1100))
        # Lines every 200 photo pixels: x 400 at view 200, y 600 at view 300.
        self.assertEqual(tuple(rgb[120, 200]), annotate.GRID)
        self.assertEqual(tuple(rgb[300, 550]), annotate.GRID)
        # Photo x 200 and y 200 are both at view 100.
        self.assertEqual(tuple(rgb[120, 100]), annotate.GRID)
        self.assertEqual(tuple(rgb[100, 550]), annotate.GRID)
        self.assertEqual(tuple(rgb[120, 250]), (255, 255, 255))
        self.assertEqual(tuple(rgb[350, 550]), (255, 255, 255))
        quad = [[402, 302], [1802, 302], [1802, 902], [402, 902]]
        rgb = annotate.overlay(img, quad)
        self.assertEqual(tuple(rgb[151, 600]), annotate.FIRST)
        self.assertEqual(tuple(rgb[451, 600]), annotate.MARK)
        self.assertEqual(tuple(rgb[300, 901]), annotate.MARK)
        self.assertEqual(tuple(rgb[300, 600]), (255, 255, 255))


class CommandTest(unittest.TestCase):
    """`barbench annotate` names a photo as the lists do and reads it from
    the cache `barbench photos` filled."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.lists, self.cache = os.path.join(d, "lists"), os.path.join(d, "cache")
        os.makedirs(self.lists)
        self.png = encode(np.stack([gradient()] * 3, axis=2), 2)
        listing = {
            "name": "set",
            "repository": "https://github.com/example/photos",
            "images": [
                {"file": "a/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), **facts("set/A"),
                 "symbols": [{"symbology": "code128", "text": "A", "corners": [[20, 20], [180, 25], [175, 80], [25, 75]]}],
                 "source": "a"},
                {"file": "a/2.png", "sha256": hashlib.sha256(self.png).hexdigest(), **facts("set/a-2"),
                 "symbols": [{"symbology": "ean13", "text": None}]},
            ],
        }
        with open(os.path.join(self.lists, "set.json"), "w") as f:
            json.dump(listing, f)
        # A second set, in another slice.
        test = dict(listing, name="held", images=[dict(listing["images"][0], **facts("held/A"))])
        with open(os.path.join(self.lists, "held.json"), "w") as f:
            json.dump(test, f)
        os.makedirs(os.path.join(self.cache, "a"))
        for name in ("1.png", "2.png"):
            with open(os.path.join(self.cache, "a", name), "wb") as f:
                f.write(self.png)

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        with mock.patch.object(photos, "LISTINGS", self.lists):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                status = cli.main(["annotate", *args, "--cache", self.cache])
        return status, out.getvalue()

    def test_every_view_writes_a_png(self):
        out = os.path.join(self.dir.name, "v.png")
        quad = "20,20;180,25;175,80;25,75"
        for args in (["overview", "set/a/1.png"], ["zoom", "set/a/1.png", "50", "40", "10"],
                     ["corners", "set/a/1.png", quad], ["overlay", "set/a/1.png", quad],
                     ["overview", "held/a/1.png"]):
            os.path.exists(out) and os.remove(out)
            status, printed = self.run_cli(*args, out)
            self.assertEqual(status, 0, args)
            self.assertIn("200x100", printed)
            with open(out, "rb") as f:
                self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_the_radius_reaches_the_corners_view(self):
        out = os.path.join(self.dir.name, "v.png")
        quad = "20,20;180,25;175,80;25,75"
        with mock.patch.object(annotate, "corners", wraps=annotate.corners) as corners:
            self.assertEqual(self.run_cli("corners", "set/a/1.png", quad, out, "--radius", "5")[0], 0)
        self.assertEqual(corners.call_args.args[2], 5.0)

    def test_bad_arguments_are_usage_errors(self):
        out = os.path.join(self.dir.name, "v.png")
        quad = "20,20;180,25;175,80;25,75"
        for args in (["zoom", "set/a/1.png", "50", "40", "0", out],
                     ["zoom", "set/a/1.png", "inf", "40", "10", out],
                     ["zoom", "set/a/1.png", "1e20", "40", "1", out],
                     ["corners", "set/a/1.png", quad, out, "--radius", "-1"],
                     ["corners", "set/a/1.png", "1,1;2,1", out],
                     ["overlay", "set/a/1.png", "20,20;1e30,25;175,80;25,75", out],
                     ["overview", "set/a/1.png", os.path.join(self.dir.name, "no", "such", "v.png")]):
            with self.assertRaises(SystemExit) as e:
                self.run_cli(*args)
            self.assertEqual(e.exception.code, 2, args)
        self.assertFalse(os.path.exists(out))

    def test_a_sheet_shows_each_symbol_with_corners(self):
        prefix = os.path.join(self.dir.name, "sheet")
        status, printed = self.run_cli("sheet", "set", prefix)
        self.assertEqual(status, 0)
        # One symbol has corners: one page, one row of five squares.
        self.assertEqual(printed.split(), [prefix + "-00.png", "a/1.png"])
        with open(prefix + "-00.png", "rb") as f:
            self.assertEqual(photos.decode_png(f.read()).shape, (204, 5 * 204))

    def test_unlisted_uncached_or_changed_photos_are_refused(self):
        out = os.path.join(self.dir.name, "v.png")
        for photo in ("set/a/3.png", "other/a/1.png", "set"):
            with self.assertRaises(SystemExit) as e:
                self.run_cli("overview", photo, out)
            self.assertEqual(e.exception.code, 2, photo)
        os.remove(os.path.join(self.cache, "a", "2.png"))
        with self.assertRaises(SystemExit):
            self.run_cli("overview", "set/a/2.png", out)
        with open(os.path.join(self.cache, "a", "1.png"), "ab") as f:
            f.write(b"x")
        with self.assertRaises(SystemExit):
            self.run_cli("overview", "set/a/1.png", out)
        self.assertFalse(os.path.exists(out))
        with self.assertRaises(SystemExit):
            self.run_cli("sheet", "nope", os.path.join(self.dir.name, "s"))


if __name__ == "__main__":
    unittest.main()
