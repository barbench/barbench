"""Tests for barbench. Run from the repository root: python -m unittest discover -s tests"""

import collections
import contextlib
import copy
import hashlib
import importlib.metadata
import importlib.resources
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

from barbench import __main__ as cli
import barbench
from barbench import adapters, corpus, pgm, photos, plot, score, slices, source, timing


class PgmTest(unittest.TestCase):
    def test_round_trip(self):
        image = np.arange(60, dtype=np.uint8).reshape(5, 12)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            np.testing.assert_array_equal(pgm.read(path), image)

    def test_the_size_is_read_from_the_header(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, np.zeros((5, 12), np.uint8))
            self.assertEqual(pgm.size(path), (12, 5))

    def test_truncated_files_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            with open(path, "wb") as f:
                f.write(b"P5\n4 4\n255\n\x00\x00")
            with self.assertRaises(ValueError):
                pgm.read(path)


class RenderTest(unittest.TestCase):
    def test_upright_symbol_has_its_modules(self):
        modules = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 1]
        rng = np.random.default_rng(0)
        image = corpus.render(modules, 2.0, 0.0, 0.0, 0.0, rng)
        row = image[image.shape[0] // 2]
        dark = np.nonzero(row < 128)[0]
        # The symbol is centred, its first bar right after the quiet zone.
        span = (len(modules) + 2 * corpus.QUIET) * 2.0
        expected = image.shape[1] / 2 - span / 2 + 2 * corpus.QUIET
        start = dark[0]
        self.assertLessEqual(abs(start - expected), 1.0)
        sampled = [int(row[start + 2 * i + 1] < 128) for i in range(len(modules))]
        self.assertEqual(sampled, modules)
        # Nothing but paper beyond the symbol.
        self.assertEqual(dark[-1], start + 2 * (len(modules) - 1) + 1)

    def test_a_shift_moves_the_symbol_by_part_of_a_pixel(self):
        modules = [1, 0, 1, 1, 0, 0, 1, 0, 1, 1, 1, 0, 1]
        still = corpus.render(modules, 1.0, 0.0, 0.0, 0.0, np.random.default_rng(0))
        moved = corpus.render(modules, 1.0, 0.0, 0.0, 0.0, np.random.default_rng(0), (0.5, 0.0))
        self.assertEqual(still.shape, moved.shape)
        row_still, row_moved = still[still.shape[0] // 2], moved[moved.shape[0] // 2]
        # Unshifted, one pixel per module draws pure ink and paper; half a
        # pixel over, every edge pixel is half of each.
        mid = (corpus.PAPER + corpus.INK) / 2
        self.assertFalse(np.any(np.abs(row_still.astype(float) - mid) < 2))
        self.assertTrue(np.any(np.abs(row_moved.astype(float) - mid) < 2))

    def test_encoders_agree_with_a_reader(self):
        for name, encode in corpus.ENCODERS.items():
            self.assertTrue(corpus.round_trips(encode("Streepjes 128"), "Streepjes 128"), name)

    def test_encoder_bugs_are_caught(self):
        # python-barcode 0.16.1 drops a leading "99" in code set C.
        modules = corpus.python_barcode_modules("9925732")
        self.assertFalse(corpus.round_trips(modules, "9925732"))


class CorpusTest(unittest.TestCase):
    def test_payloads(self):
        rng = np.random.default_rng(3)
        drawn = [corpus.payload(rng) for _ in range(500)]
        self.assertTrue(all(4 <= len(p) <= 16 for p in drawn))
        self.assertTrue(all(p == p.strip() for p in drawn))
        digits = sum(p.isdigit() for p in drawn)
        self.assertTrue(150 < digits < 350, digits)

    def test_no_parameter_stands_in_for_the_encoder(self):
        for per_cell in (1, 2):
            with tempfile.TemporaryDirectory() as d:
                manifest = corpus.generate(d, seed=5, per_cell=per_cell, negatives=0)
            images = manifest["images"]
            for key, values in corpus.GRID.items():
                for value in values:
                    encoders = [e["encoder"] for e in images if e[key] == value]
                    counts = {name: encoders.count(name) for name in corpus.ENCODERS}
                    self.assertEqual(len(set(counts.values())), 1, (per_cell, key, value, counts))
            for name in corpus.ENCODERS:
                kinds = {e["expected"].isdigit() for e in images if e["encoder"] == name}
                self.assertEqual(kinds, {True, False}, name)

    def test_an_encoder_that_is_always_wrong_stops_the_corpus(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(corpus, "round_trips", return_value=False):
            with self.assertRaises(RuntimeError):
                corpus.generate(d, seed=1, per_cell=1, negatives=0)

    def test_the_seed_decides_everything(self):
        def digest(d):
            h = hashlib.sha256()
            for name in sorted(os.listdir(d)):
                with open(os.path.join(d, name), "rb") as f:
                    h.update(name.encode() + f.read())
            return h.hexdigest()

        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = corpus.generate(a, seed=7, per_cell=1, negatives=2)
            corpus.generate(b, seed=7, per_cell=1, negatives=2)
            self.assertEqual(digest(a), digest(b))
            cells = 1
            for values in corpus.GRID.values():
                cells *= len(values)
            self.assertEqual(len(first["images"]), cells + 2)
            self.assertTrue(all(corpus.round_trips(corpus.ENCODERS[e["encoder"]](e["expected"]), e["expected"])
                                for e in first["images"] if e["expected"] is not None))


class ScoreTest(unittest.TestCase):
    MANIFEST = {
        "grid": {"blur": [0.0, 0.5]},
        "images": [
            {"file": "a", "expected": "A", "blur": 0.0},
            {"file": "b", "expected": "B", "blur": 0.5},
            {"file": "c", "expected": "C", "blur": 0.5},
            {"file": "n", "expected": None},
        ],
    }

    def test_photos_are_counted_by_group_too(self):
        images = [{"file": "a", "expected": "A", "group": "s/A"}, {"file": "b", "expected": "A", "group": "s/A"},
                  {"file": "c", "expected": "C", "group": "s/C"}, {"file": "n", "expected": None, "group": "s/N"}]
        results = [{"texts": ["A"]}, {"texts": []}, {"texts": ["C"]}, {"texts": []}]
        s = score.score({"grid": {}, "images": images}, results)
        self.assertEqual((s["positives"], s["positive_groups"], s["negatives"], s["negative_groups"]), (3, 2, 1, 1))
        self.assertEqual(s["read_interval"], list(score.wilson(2, 3)))
        # The synthetic tiers have no groups.
        self.assertIsNone(score.score(self.MANIFEST, [{"texts": []}] * 4)["positive_groups"])

    def test_reads_misreads_and_false_positives(self):
        results = [
            {"texts": ["A"]},
            {"texts": ["B", "X"]},
            {"texts": [], "error": "broken"},
            {"texts": ["N"]},
        ]
        s = score.score(self.MANIFEST, results)
        self.assertEqual((s["read"], s["positives"], s["misread"]), (2, 3, 1))
        self.assertEqual((s["false_positive"], s["negatives"], s["errors"]), (1, 1, 1))
        self.assertEqual(s["by"]["blur"]["0.5"], {"read": 1, "of": 2, "rate": 0.5})

    def test_table_marks_values_without_symbols(self):
        report = {
            "grid": {"blur": [0.0, 0.5]},
            "readers": {
                "r": {
                    "seconds": 1.0,
                    "score": score.score(
                        {"grid": {"blur": [0.0, 0.5]}, "images": self.MANIFEST["images"][:1]},
                        [{"texts": ["A"]}],
                    ),
                },
                "s": {"skipped": "not installed"},
            },
        }
        text = score.table(report)
        self.assertIn("100%", text)
        self.assertIn("      -", text)
        self.assertIn("skipped: not installed", text)

    def test_result_count_must_match(self):
        with self.assertRaises(ValueError):
            score.score(self.MANIFEST, [])


class SweepTest(unittest.TestCase):
    #: A short sweep, so the test renders few images.
    SMALL = {"module_px": [1.5, 3.0], "blur": [0.0, 0.6], "rotation": [0.0, 45.0]}

    def make(self, d, per_point=2, seed=3):
        with mock.patch.dict(corpus.SWEEPS, self.SMALL, clear=True):
            return corpus.generate_sweep(d, seed, per_point)

    def test_each_point_moves_one_parameter(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = self.make(d)
            images = manifest["images"]
            self.assertEqual(len(images), 6 * 2)
            for e in images:
                self.assertTrue(os.path.exists(os.path.join(d, e["file"])))
                # Rotation is the point, or the point upside down.
                if e["sweep"] == "rotation":
                    self.assertIn(e["rotation"], (e["point"], (e["point"] + 180.0) % 360.0))
                else:
                    self.assertEqual(e[e["sweep"]], e["point"])
                    # The baseline tilt: within TILT of upright either way round.
                    off = (e["rotation"] + corpus.TILT) % 180.0
                    self.assertLessEqual(off, 2 * corpus.TILT)
                for key, base in corpus.BASELINE.items():
                    if key != e["sweep"]:
                        self.assertEqual(e[key], base)
            # Each point gets each encoder once in two.
            for axis, values in self.SMALL.items():
                for v in values:
                    at = [e["encoder"] for e in images if e["sweep"] == axis and e["point"] == v]
                    self.assertEqual(sorted(at), sorted(corpus.ENCODERS))
            self.assertEqual(manifest["sweeps"], self.SMALL)
            self.assertEqual(manifest["grid"], {})

    def test_shifts_pitches_and_flips_vary_image_by_image(self):
        with tempfile.TemporaryDirectory() as d:
            manifest = self.make(d, per_point=8, seed=4)
            images = manifest["images"]
            shifts = [s for e in images for s in e["shift"]]
            self.assertTrue(all(0.0 <= s < 1.0 for s in shifts))
            self.assertGreater(len(set(shifts)), 1)
            for e in images:
                ratio = e["pitch_px"] / e["module_px"]
                self.assertLessEqual(abs(ratio - 1.0), corpus.JITTER)
            self.assertGreater(len({e["pitch_px"] for e in images}), 1)
            # The rotation sweep reads its angles both ways round.
            turned = {(e["point"], e["rotation"]) for e in images if e["sweep"] == "rotation"}
            for point in self.SMALL["rotation"]:
                self.assertIn((point, point), turned)
                self.assertIn((point, (point + 180.0) % 360.0), turned)
            self.assertEqual(manifest["baseline"]["jitter"], corpus.JITTER)

    def test_the_cli_writes_a_sweep_and_runs_it(self):
        with tempfile.TemporaryDirectory() as d:
            corpus_dir, out = os.path.join(d, "s"), os.path.join(d, "r.json")
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    cli.main(["sweep", corpus_dir, "--per-point", "0"])
            with mock.patch.dict(corpus.SWEEPS, self.SMALL, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["sweep", corpus_dir, "--per-point", "1"]), 0)

            def reads_all(paths):
                with open(os.path.join(corpus_dir, "manifest.json")) as f:
                    images = json.load(f)["images"]
                return [{"texts": [e["expected"]]} for e in images], 0.1, "test"

            with mock.patch.dict(adapters.ADAPTERS, {"fake": reads_all}):
                printed = io.StringIO()
                with contextlib.redirect_stdout(printed):
                    cli.main(["run", corpus_dir, "--readers", "fake", "--out", out])
            with open(out) as f:
                report = json.load(f)
            self.assertEqual((report["slices"], report["added_after"]), (None, None))
            self.assertEqual(report["sweeps"], self.SMALL)
            self.assertEqual(report["baseline"]["module_px"], corpus.BASELINE["module_px"])
            self.assertEqual(
                [p["rate"] for p in report["readers"]["fake"]["score"]["curves"]["blur"]],
                [1.0, 1.0],
            )
            # The table shows a row per sweep.
            for axis in self.SMALL:
                self.assertIn(axis, printed.getvalue())
            svg_path = os.path.join(d, "c.svg")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["plot", out, "--out", svg_path]), 0)
            with open(svg_path, encoding="utf-8") as f:
                self.assertIn("σ", f.read())

    def test_the_seed_decides_everything(self):
        digests = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as d:
                manifest = self.make(d, per_point=1, seed=9)
                blob = json.dumps(manifest["images"]).encode()
                for e in manifest["images"]:
                    with open(os.path.join(d, e["file"]), "rb") as f:
                        blob += f.read()
                digests.append(hashlib.sha256(blob).hexdigest())
        self.assertEqual(digests[0], digests[1])

    def test_wilson_intervals(self):
        # Known values: 0 of 10, 10 of 10, 5 of 10, and none.
        low, high = score.wilson(0, 10)
        self.assertEqual(low, 0.0)
        self.assertAlmostEqual(high, 0.2775, places=4)
        low, high = score.wilson(10, 10)
        self.assertAlmostEqual(low, 0.7225, places=4)
        self.assertEqual(high, 1.0)
        low, high = score.wilson(5, 10)
        self.assertAlmostEqual(low, 0.2366, places=4)
        self.assertAlmostEqual(high, 0.7634, places=4)
        self.assertEqual(score.wilson(0, 0), (0.0, 1.0))

    def curve_report(self):
        manifest = {
            "grid": {},
            "sweeps": {"blur": [0.0, 0.5], "module_px": [1.0, 2.0, 4.0]},
            "images": [
                {"expected": "A", "sweep": "blur", "point": 0.0},
                {"expected": "B", "sweep": "blur", "point": 0.0},
                {"expected": "C", "sweep": "blur", "point": 0.5},
                {"expected": "D", "sweep": "module_px", "point": 1.0},
                {"expected": "E", "sweep": "module_px", "point": 2.0},
                {"expected": "F", "sweep": "module_px", "point": 4.0},
            ],
        }
        results = [{"texts": t} for t in (["A"], ["B"], [], [], ["X"], ["F"])]
        return manifest, results

    def test_curves_follow_the_sweeps(self):
        manifest, results = self.curve_report()
        curves = score.score(manifest, results)["curves"]
        self.assertEqual([p["value"] for p in curves["blur"]], [0.0, 0.5])
        self.assertEqual([(p["read"], p["of"]) for p in curves["blur"]], [(2, 2), (0, 1)])
        # A misread is not a read.
        self.assertEqual([p["read"] for p in curves["module_px"]], [0, 0, 1])
        for p in curves["blur"] + curves["module_px"]:
            self.assertLessEqual(p["low"], p["rate"])
            self.assertLessEqual(p["rate"], p["high"])
        # A grid tier has no curves, and a point without symbols no rate.
        self.assertEqual(score.score({"grid": {}, "images": []}, [])["curves"], {})
        manifest["sweeps"]["blur"].append(0.9)
        empty = score.score(manifest, results)["curves"]["blur"][-1]
        self.assertEqual((empty["rate"], empty["of"]), (None, 0))

    def test_plot_draws_every_reader_on_every_sweep(self):
        import xml.etree.ElementTree as ET

        manifest, results = self.curve_report()
        scored = score.score(manifest, results)
        report = {
            "sweeps": manifest["sweeps"],
            "baseline": corpus.BASELINE,
            "grid": {},
            "readers": {
                "one": {"score": scored},
                "two & co": {"score": scored},
                "gone": {"skipped": "not installed"},
            },
        }
        root = ET.fromstring(plot.svg(report))
        ns = "{http://www.w3.org/2000/svg}"
        # One line and one band per drawn reader and sweep.
        self.assertEqual(len(root.findall(f"{ns}polyline")), 2 * 2)
        self.assertEqual(len(root.findall(f"{ns}polygon")), 2 * 2)
        texts = "".join(root.itertext())
        self.assertIn("two & co", texts)
        self.assertNotIn("gone", texts)
        self.assertIn("Module size (pixels)", texts)

    def test_plot_refuses_a_report_without_sweeps(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "r.json")
            with open(path, "w") as f:
                json.dump({"grid": {}, "readers": {}}, f)
            broken = os.path.join(d, "broken.json")
            with open(broken, "w") as f:
                f.write("{not json")
            for report in (path, broken, os.path.join(d, "missing.json")):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        cli.main(["plot", report, "--out", os.path.join(d, "c.svg")])


class SliceTest(unittest.TestCase):
    """The rule that deals photos into slices is fixed: a change would move
    photos that readers have declared held out or not."""

    def test_a_group_is_in_the_slice_of_its_hashs_first_digit(self):
        # SHA-256 of "a" starts ca97, of "b" 3e23.
        known = {"a": "c", "b": "3", "zxing-blackbox/code128-2/005-3379497200006": "0"}
        self.assertEqual({g: slices.of_group(g) for g in known}, known)
        counts = collections.Counter(slices.of_group(f"g{i}") for i in range(16000))
        self.assertEqual(sorted(counts), list(slices.SLICES))
        self.assertTrue(all(900 < n < 1100 for n in counts.values()), counts)

    def test_slices_are_named_by_digits_and_ranges(self):
        for spec, want in (("all", slices.SLICES), ("e,f", "ef"), ("0-d", "0123456789abcd"),
                           ("f,0-2,1", "012f"), ("7", "7")):
            self.assertEqual(slices.parse(spec), want, spec)
        for spec in ("", "g", "ef", "e,", "d-a", "0-", "-3", "0-ff", "E"):
            with self.assertRaises(ValueError, msg=spec):
                slices.parse(spec)

    def test_declarations_are_checked(self):
        good = {"reader": "r", "held_out": ["e", "f"], "since": "2026-11-22", "declared_by": "its maintainers"}
        with tempfile.TemporaryDirectory() as d:
            def declare(name, declared):
                with open(os.path.join(d, name + ".json"), "w") as f:
                    json.dump(declared, f)

            declare("r", good)
            self.assertEqual(slices.declarations(d), {"r": good})
            for bad, message in (({**good, "reader": "q"}, "declares reader 'q'"),
                                 ({**good, "held_out": []}, "not a list of slices"),
                                 ({**good, "held_out": ["e", "e"]}, "not a list of slices"),
                                 ({**good, "held_out": "ef"}, "not a list of slices"),
                                 ({**good, "held_out": ["ef"]}, "not a list of slices"),
                                 ({**good, "held_out": [""]}, "not a list of slices"),
                                 ({**good, "held_out": [14]}, "not a list of slices"),
                                 (["e", "f"], "not a declaration"),
                                 ({**good, "since": "2026-02-30"}, "not a date"),
                                 ({**good, "declared_by": ""}, "who declared it")):
                declare("r", bad)
                with self.assertRaisesRegex(ValueError, message):
                    slices.declarations(d)

    def test_a_comparison_holds_out_the_shared_slices_for_every_reader(self):
        with tempfile.TemporaryDirectory() as d:
            for reader, held, since in (("p", ["e", "f"], "2026-11-22"), ("q", ["0", "e", "f"], "2026-12-05"),
                                        ("r", ["a", "b"], "2026-11-01")):
                with open(os.path.join(d, reader + ".json"), "w") as f:
                    json.dump({"reader": reader, "held_out": held, "since": since, "declared_by": "x"}, f)
            self.assertEqual(slices.held_out_for(["p"], d), ("ef", "2026-11-22"))
            # The latest declaration decides, so every photo is held out for all.
            self.assertEqual(slices.held_out_for(["p", "q"], d), ("ef", "2026-12-05"))
            with self.assertRaisesRegex(ValueError, "no declaration from zbar"):
                slices.held_out_for(["p", "zbar"], d)
            with self.assertRaisesRegex(ValueError, "r do not hold out the shared slices"):
                slices.held_out_for(["p", "r"], d)
            with self.assertRaisesRegex(ValueError, "no readers named"):
                slices.held_out_for([], d)
            # What a reader's own runs leave out: its slices, after its date.
            self.assertEqual(slices.held_out_by("q", d), ("0ef", "2026-12-05"))
            with self.assertRaisesRegex(ValueError, "no declaration from zbar"):
                slices.held_out_by("zbar", d)

    def test_the_committed_declarations(self):
        # Streepjes's is the one declaration so far; a reader that declares
        # adds itself here, and must have an adapter.
        declared = slices.declarations()
        self.assertEqual(sorted(declared), ["streepjes"])
        self.assertEqual(slices.held_out_for(["streepjes"]), ("ef", "2026-11-22"))
        self.assertTrue(set(declared) <= set(adapters.ADAPTERS))

    def test_seeds_are_0_or_more(self):
        with tempfile.TemporaryDirectory() as d:
            for command in ("corpus", "sweep"):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                    cli.main([command, os.path.join(d, command), "--seed", "-1"])
                self.assertEqual(e.exception.code, 2)
                self.assertFalse(os.path.exists(os.path.join(d, command)))

    def test_the_cli_writes_the_photos_it_is_asked_for(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "photos")
            # Stubbed, so a broken check fails here rather than downloading.
            with mock.patch("barbench.photos.generate") as generate:
                generate.return_value = {"images": []}
                with contextlib.redirect_stdout(io.StringIO()):
                    for args, chosen, after, leave_out in (
                        ([], slices.SLICES, None, None),
                        (["--slices", "e,f"], "ef", None, None),
                        (["--added-after", "2026-11-21"], slices.SLICES, "2026-11-21", None),
                        (["--held-out-for", "streepjes"], "ef", "2026-11-22", None),
                        (["--dev-for", "streepjes"], slices.SLICES, None, ("ef", "2026-11-22")),
                    ):
                        self.assertEqual(cli.main(["photos", out, *args]), 0, args)
                        given = generate.call_args.kwargs
                        self.assertEqual((given["chosen"], given["added_after"], given["leave_out"]),
                                         (chosen, after, leave_out), args)
                generate.reset_mock()
                for args in (["--slices", "g"], ["--held-out-for", "zbar"], ["--held-out-for", "streepjes", "--slices", "e"],
                             ["--held-out-for", "streepjes", "--added-after", "2026-11-21"], ["--sets", ","],
                             ["--held-out-for", ","], ["--dev-for", "zbar"], ["--dev-for", "streepjes", "--slices", "e"],
                             ["--dev-for", "streepjes", "--held-out-for", "streepjes"], ["--added-after", "soon"]):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                        cli.main(["photos", out, *args])
                    self.assertEqual(e.exception.code, 2, args)
            generate.assert_not_called()
            self.assertEqual(os.listdir(d), [])
            # A failure in the data is not a usage error: it shows as itself.
            with mock.patch("barbench.photos.generate", side_effect=ValueError("digest does not match the list")):
                with self.assertRaisesRegex(ValueError, "digest"):
                    cli.main(["photos", out])


class GateTest(unittest.TestCase):
    """The run's gates fail it, and refuse to pass without checking."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        pgm.write(os.path.join(d, "a.pgm"), np.full((8, 8), 255, np.uint8))
        self.manifest = {
            "tier": "synthetic",
            "seed": 0,
            "encoders": {},
            "grid": {"blur": [0.0]},
            "images": [{"file": "a.pgm", "expected": "A", "blur": 0.0}],
        }
        self.write_manifest()

    def write_manifest(self):
        with open(os.path.join(self.dir.name, "manifest.json"), "w") as f:
            json.dump(self.manifest, f)

    def tearDown(self):
        self.dir.cleanup()

    def fake_streepjes(self, line):
        """A stand-in for the streepjes command that prints `line` per file."""
        path = os.path.join(self.dir.name, "fake")
        with open(path, "w") as f:
            f.write("#!/bin/sh\n")
            f.write('[ "$1" = --version ] && { echo "streepjes 0.0.0"; exit 0; }\n')
            f.write(f"printf '%s\\n' '{json.dumps(line)}'\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def run_cli(self, *args, line):
        with mock.patch.dict(os.environ, {"STREEPJES_BIN": self.fake_streepjes(line)}):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return cli.main(["run", self.dir.name, "--readers", "streepjes", *args])

    def test_a_reader_that_fails_every_file_fails_the_strict_gate(self):
        line = {"file": "a.pgm", "error": "broken"}
        self.assertEqual(self.run_cli(line=line), 0)
        self.assertEqual(self.run_cli("--no-misreads", "streepjes", line=line), 1)

    def test_misreads_and_floors(self):
        wrong = {"file": "a.pgm", "codes": [{"text": "B", "bytes": "42"}]}
        self.assertEqual(self.run_cli("--no-misreads", "streepjes", line=wrong), 1)
        right = {"file": "a.pgm", "codes": [{"text": "A", "bytes": "41"}]}
        self.assertEqual(self.run_cli("--no-misreads", "streepjes", line=right), 0)
        nothing = {"file": "a.pgm", "codes": []}
        self.assertEqual(self.run_cli("--min-read", "streepjes=0.5", line=nothing), 1)
        self.assertEqual(self.run_cli("--min-read", "streepjes=0.5", line=right), 0)

    def test_text_that_is_not_utf8_still_counts(self):
        binary = {"file": "a.pgm", "codes": [{"text": None, "bytes": "ff"}]}
        self.assertEqual(self.run_cli("--no-misreads", "streepjes", line=binary), 1)

    def test_a_gated_reader_that_is_missing_fails(self):
        with mock.patch.dict(os.environ, {"STREEPJES_BIN": "/nonexistent/streepjes"}):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                for gate in (["--no-misreads", "streepjes"], ["--min-read", "streepjes=0.1"]):
                    status = cli.main(["run", self.dir.name, "--readers", "streepjes", *gate])
                    self.assertEqual(status, 1, gate)

    def test_gates_on_readers_that_do_not_run_are_refused(self):
        line = {"file": "a.pgm", "codes": []}
        for gate in (
            ["--require", "zbar"],
            ["--no-misreads", "streepjs"],
            ["--min-read", "x=1"],
            ["--min-read", "streepjes=nan"],
            ["--min-read", "streepjes=1.5"],
        ):
            with self.assertRaises(SystemExit) as e:
                self.run_cli(*gate, line=line)
            self.assertEqual(e.exception.code, 2, gate)

class FakeDist:
    """What importlib.metadata says of an installed barbench: its
    `__init__.py` is this copy's (or under `here`), and its listed `files`
    are under `root`."""

    def __init__(self, direct_url, here=source.HERE, files=(), root=None):
        self.direct_url, self.here, self.files, self.root = direct_url, here, list(files), root

    def locate_file(self, name):
        if name in self.files:
            return os.path.join(self.root, *name.parts)
        return os.path.join(os.path.dirname(self.here), name)

    def read_text(self, name):
        return self.direct_url if name == "direct_url.json" else None


def git(path, *args):
    # Without the user's settings, such as commit signing.
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return subprocess.run(
        ["git", "-C", path, "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()


class SourceTest(unittest.TestCase):
    """A report names the barbench commit that made it."""

    def test_an_install_from_git_names_its_commit(self):
        url = json.dumps({"url": "https://example.org/barbench", "vcs_info": {"vcs": "git", "commit_id": "ab" * 20}})
        self.assertEqual(source.commit(lambda name: FakeDist(url)), "ab" * 20)
        for url in (json.dumps({"url": "https://example.org/b.whl", "archive_info": {}}), "not json", "[1]", None):
            self.assertIsNone(source.commit(lambda name: FakeDist(url)), url)

    def test_another_install_says_nothing_of_this_copy(self):
        url = json.dumps({"url": "https://example.org/barbench", "vcs_info": {"vcs": "git", "commit_id": "ab" * 20}})
        elsewhere = FakeDist(url, here="/nonexistent/barbench")
        self.assertEqual(source.commit(lambda name: elsewhere), source.checkout_commit(os.path.dirname(source.HERE)))

    def test_an_install_from_a_folder_names_its_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            repo = os.path.join(d, "repo")
            folder = os.path.join(repo, "a b")
            os.makedirs(os.path.join(folder, "barbench"))
            url = json.dumps({"url": "file://" + folder.replace(" ", "%20"), "dir_info": {}})
            self.assertIsNone(source.commit(lambda name: FakeDist(url)))
            try:
                git(repo, "init", "-q")
            except (OSError, subprocess.SubprocessError):
                self.skipTest("no git")
            for path in (os.path.join(folder, "barbench", "x.py"), os.path.join(repo, "outside")):
                with open(path, "w") as f:
                    f.write("1")
            git(repo, "add", "outside")
            git(repo, "commit", "-q", "-m", "one")
            # A folder Git does not track tells nothing, though its
            # checkout has a commit.
            self.assertIsNone(source.commit(lambda name: FakeDist(url)))
            git(repo, "add", ".")
            git(repo, "commit", "-q", "-m", "two")
            head = git(repo, "rev-parse", "HEAD")
            self.assertEqual(source.commit(lambda name: FakeDist(url)), head)
            # Only files under the folder make it dirty.
            with open(os.path.join(repo, "outside"), "w") as f:
                f.write("2")
            self.assertEqual(source.commit(lambda name: FakeDist(url)), head)
            with open(os.path.join(folder, "new"), "w") as f:
                f.write("2")
            self.assertEqual(source.commit(lambda name: FakeDist(url)), head + "-dirty")
            os.remove(os.path.join(folder, "new"))
            # Installed files that are not the checkout's: the checkout has
            # moved on since the install.
            installed = os.path.join(d, "site")
            os.makedirs(os.path.join(installed, "barbench"))
            name = __import__("pathlib").PurePosixPath("barbench/x.py")
            with open(os.path.join(installed, "barbench", "x.py"), "w") as f:
                f.write("1")
            self.assertEqual(source.commit(lambda n: FakeDist(url, files=[name], root=installed)), head)
            with open(os.path.join(installed, "barbench", "x.py"), "w") as f:
                f.write("0")
            self.assertIsNone(source.commit(lambda n: FakeDist(url, files=[name], root=installed)))

    def test_the_report_names_the_commit(self):
        with tempfile.TemporaryDirectory() as d:
            pgm.write(os.path.join(d, "a.pgm"), np.full((8, 8), 255, np.uint8))
            with open(os.path.join(d, "manifest.json"), "w") as f:
                json.dump({"tier": "synthetic", "seed": 0, "encoders": {}, "grid": {},
                           "images": [{"file": "a.pgm", "expected": None, "kind": "noise"}]}, f)
            out = os.path.join(d, "r.json")
            fake = {"fake": lambda paths: ([{"texts": []}], 0.1, "1")}
            with mock.patch.dict(adapters.ADAPTERS, fake), mock.patch.object(source, "commit", return_value="cd" * 20):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["run", d, "--readers", "fake", "--out", out]), 0)
            with open(out) as f:
                self.assertEqual(json.load(f)["barbench_commit"], "cd" * 20)


@unittest.skipUnless(os.environ.get("BARBENCH_INSTALLED"), "runs against an installed barbench")
class InstalledTest(unittest.TestCase):
    """scripts/ci.sh runs the tests against the wheel it installed, with
    BARBENCH_INSTALLED set: barbench must work without this folder."""

    def test_the_installed_wheel_is_what_runs(self):
        self.assertTrue(barbench.__file__.startswith(sys.prefix), barbench.__file__)
        self.assertEqual(barbench.__version__, importlib.metadata.version("barbench"))
        # The photo lists and the readers' declarations ship inside it.
        self.assertEqual(len(photos.load_lists()), 4)
        self.assertIn("streepjes", slices.declarations())
        # So does the Vision helper's source, which the vision adapter builds.
        self.assertTrue(importlib.resources.files("barbench").joinpath("vision.swift").is_file())


class AdapterTest(unittest.TestCase):
    def test_a_missing_binary_is_unavailable(self):
        with self.assertRaises(adapters.Unavailable):
            adapters.streepjes(["x.pgm"], binary="/nonexistent/streepjes")

    def test_zxing_cpp_reports_gs1_data_as_the_symbol_holds_it(self):
        try:
            import zxingcpp  # noqa: F401
            from barcode import Gs1_128
        except ImportError:
            self.skipTest("zxing-cpp or python-barcode is not installed")
        # FNC1 first, then (96) 991001: the text is the digits alone.
        modules = [int(c) for c in Gs1_128("96991001").build()[0]]
        image = corpus.render(modules, 2.0, 0.0, 0.0, 0.0, np.random.default_rng(0))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            results, _, _ = adapters.zxing_cpp([path])
        self.assertEqual(results, [{"texts": ["96991001"]}])

    @unittest.skipUnless(os.environ.get("STREEPJES_BIN"), "STREEPJES_BIN not set")
    def test_streepjes_reads_a_rendered_symbol(self):
        rng = np.random.default_rng(0)
        image = corpus.render(corpus.zxing_modules("BENCH"), 2.0, 10.0, 0.0, 0.0, rng)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            results, _, version = adapters.streepjes([path, os.path.join(d, "missing.pgm")])
        self.assertEqual(results[0], {"texts": ["BENCH"]})
        self.assertIn("error", results[1])
        self.assertTrue(version.startswith("streepjes "))


def _script(directory, name, body):
    """An executable shell script, to stand in for a reader or a compiler."""
    path = os.path.join(directory, name)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class VisionAdapterTest(unittest.TestCase):
    def test_vision_is_unavailable_off_macos(self):
        with mock.patch.object(adapters.sys, "platform", "linux"):
            with self.assertRaisesRegex(adapters.Unavailable, "only on macOS"):
                adapters.vision(["x.pgm"])

    def test_the_helper_is_built_once_per_source(self):
        with tempfile.TemporaryDirectory() as d:
            calls = os.path.join(d, "calls")
            # A swiftc that records each call and writes the file after -o.
            _script(d, "swiftc", f'echo x >> "{calls}"\nwhile [ "$1" != -o ]; do shift; done\n'
                    'printf "#!/bin/sh\\n" > "$2"\n')
            cache = os.path.join(d, "cache")
            env = {"PATH": d, "BARBENCH_VISION_CACHE": cache}
            with mock.patch.object(adapters.sys, "platform", "darwin"), mock.patch.dict(os.environ, env):
                first = adapters._vision_binary()
                second = adapters._vision_binary()
            self.assertEqual(first, second)
            self.assertEqual(os.listdir(cache), [os.path.basename(first)])
            with open(calls) as f:
                self.assertEqual(f.read(), "x\n")
            source = importlib.resources.files("barbench").joinpath("vision.swift").read_bytes()
            self.assertTrue(first.endswith("vision-" + hashlib.sha256(source).hexdigest()[:16]))

    def test_a_failed_build_is_unavailable_and_leaves_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            _script(d, "swiftc", 'while [ "$1" != -o ]; do shift; done\necho half > "$2"\n'
                    'echo "error: no SDK" >&2\nexit 1\n')
            cache = os.path.join(d, "cache")
            env = {"PATH": d, "BARBENCH_VISION_CACHE": cache}
            with mock.patch.object(adapters.sys, "platform", "darwin"), mock.patch.dict(os.environ, env):
                with self.assertRaisesRegex(adapters.Unavailable, "no SDK"):
                    adapters._vision_binary()
            self.assertEqual(os.listdir(cache), [])

    def test_a_macos_without_the_pinned_revision_is_unavailable(self):
        with tempfile.TemporaryDirectory() as d:
            helper = _script(d, "vision", 'echo "revision 4 is not supported here" >&2\nexit 3\n')
            with self.assertRaisesRegex(adapters.Unavailable, "not supported"):
                adapters.vision(["x.pgm"], binary=helper)

    def test_the_helper_must_print_a_line_per_file(self):
        with tempfile.TemporaryDirectory() as d:
            helper = _script(d, "vision", '[ "$1" = --version ] && { echo v; exit 0; }\n'
                             'echo \'{"codes": []}\'\n')
            with self.assertRaisesRegex(RuntimeError, "1 lines for 2 files"):
                adapters.vision(["a.pgm", "b.pgm"], binary=helper)

    def test_text_and_bytes_are_read_from_the_helper(self):
        with tempfile.TemporaryDirectory() as d:
            helper = _script(d, "vision", '[ "$1" = --version ] && { echo "Apple Vision"; exit 0; }\n'
                             'echo \'{"codes": [{"text": "AB", "bytes": "4142"}, {"text": null, "bytes": "ff"},'
                             ' {"text": null, "bytes": null}]}\'\n'
                             'echo \'{"error": "b.pgm: truncated"}\'\n')
            results, _, version = adapters.vision(["a.pgm", "b.pgm"], binary=helper)
        self.assertEqual(results, [{"texts": ["AB", "hex:ff"]}, {"texts": [], "error": "b.pgm: truncated"}])
        self.assertEqual(version, "Apple Vision")

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "needs macOS and swiftc")
    def test_vision_reports_gs1_data_as_the_symbol_holds_it(self):
        from barcode import Gs1_128

        # FNC1 first, then (96) 991001: the text is the digits alone, as
        # zxing-cpp's plain text mode and streepjes give it.
        modules = [int(c) for c in Gs1_128("96991001").build()[0]]
        image = corpus.render(modules, 2.0, 0.0, 0.0, 0.0, np.random.default_rng(0))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            with mock.patch.dict(os.environ, {"BARBENCH_VISION_CACHE": os.path.join(d, "cache")}):
                results, _, _ = adapters.vision([path])
        self.assertEqual(results, [{"texts": ["96991001"]}])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "needs macOS and swiftc")
    def test_vision_reads_a_rendered_symbol(self):
        rng = np.random.default_rng(0)
        image = corpus.render(corpus.zxing_modules("BENCH"), 2.0, 10.0, 0.0, 0.0, rng)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            blank = os.path.join(d, "blank.pgm")
            pgm.write(blank, np.full((40, 60), 255, dtype=np.uint8))
            with mock.patch.dict(os.environ, {"BARBENCH_VISION_CACHE": os.path.join(d, "cache")}):
                results, _, version = adapters.vision([path, blank, os.path.join(d, "missing.pgm")])
        self.assertEqual(results[0], {"texts": ["BENCH"]})
        self.assertEqual(results[1], {"texts": []})
        self.assertIn("error", results[2])
        self.assertTrue(version.startswith("Apple Vision barcodes revision 4, macOS "), version)


def _runs(log):
    """The files of each run a fake reader logged, one run per line."""
    return [line.rstrip("\n").split("|")[:-1] for line in log]


class TimingTest(unittest.TestCase):
    """`barbench time`: what it runs and how it splits the times. No test
    asserts on how long anything took."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.log = os.path.join(d, "log")
        # b.pgm is the smallest image, and so the one start-up is timed on.
        for name, shape in (("a.pgm", (8, 8)), ("b.pgm", (4, 6)), ("c.pgm", (8, 10))):
            pgm.write(os.path.join(d, name), np.full(shape, 255, np.uint8))
        self.write_manifest(["a.pgm", "b.pgm", "c.pgm"])

    def tearDown(self):
        self.dir.cleanup()

    def write_manifest(self, files):
        manifest = {"tier": "synthetic", "seed": 0, "images": [{"file": f} for f in files]}
        with open(os.path.join(self.dir.name, "manifest.json"), "w") as f:
            json.dump(manifest, f)

    def fake_streepjes(self, status=0, lines="for f; do echo '{\"codes\": []}'; done\n"):
        """A streepjes that logs the files of each run and prints LINES."""
        return _script(self.dir.name, "streepjes",
                       '[ "$1" = --version ] && { echo "streepjes 0.0.0"; exit 0; }\n'
                       f'shift 3\nprintf "%s|" "$@" >> "{self.log}"\necho >> "{self.log}"\n{lines}exit {status}\n')

    def time(self, *args, env=None):
        out = os.path.join(self.dir.name, "timing.json")
        with mock.patch.dict(os.environ, env or {}):
            with contextlib.redirect_stdout(io.StringIO()) as printed:
                status = cli.main(["time", self.dir.name, *args, "--out", out])
        self.assertEqual(status, 0)
        self.assertIn("per image", printed.getvalue())
        with open(out) as f:
            return json.load(f)

    def logged(self):
        with open(self.log) as f:
            return _runs(f)

    def test_start_up_and_cost_per_image(self):
        timed = timing.split([0.010, 0.012, 0.011], [0.110, 0.111, 0.120], 101)
        self.assertAlmostEqual(timed["startup_s"], 0.011)
        self.assertAlmostEqual(timed["tier_s"], 0.111)
        self.assertAlmostEqual(timed["per_image_s"], 0.001)
        self.assertEqual(timed["startup_runs"], [0.010, 0.012, 0.011])
        # Noise can make a tier run shorter than a one-image run.
        self.assertEqual(timing.split([0.02], [0.01], 3)["per_image_s"], 0.0)
        with self.assertRaises(ValueError):
            timing.split([0.01], [0.01], 1)

    def test_a_process_reader_runs_on_the_smallest_image_and_on_the_tier(self):
        bin = self.fake_streepjes()
        report = self.time("--readers", "streepjes", "--runs", "2", env={"STREEPJES_BIN": bin})
        d = self.dir.name
        small = [os.path.join(d, "b.pgm")]
        tier = [os.path.join(d, f) for f in ("a.pgm", "b.pgm", "c.pgm")]
        # A warm-up of each, then two counted runs of each, taking turns.
        self.assertEqual(self.logged(), [small, tier] * 3)
        self.assertEqual((report["images"], report["smallest"], report["runs"]), (3, "b.pgm", 2))
        self.assertEqual(report["median_pixels"], 64)
        self.assertEqual(report["tier"], "synthetic")
        streepjes = report["readers"]["streepjes"]
        self.assertEqual(streepjes["version"], "streepjes 0.0.0")
        self.assertEqual((len(streepjes["startup_runs"]), len(streepjes["tier_runs"])), (2, 2))
        self.assertGreaterEqual(streepjes["per_image_s"], 0.0)
        self.assertIn("python", report["machine"])

    def test_a_run_that_fails_gives_no_time(self):
        for fake, error in (
            ({"status": 2}, "exited 2"),
            ({"lines": "echo '{\"codes\": []}'\n"}, "1 lines for 3 files"),
        ):
            bin = self.fake_streepjes(**fake)
            with self.assertRaisesRegex(RuntimeError, error):
                self.time("--readers", "streepjes", "--runs", "1", env={"STREEPJES_BIN": bin})

    def test_zbar_is_timed_as_one_process_for_the_tier(self):
        zbar_log = os.path.join(self.dir.name, "zbar-log")
        _script(self.dir.name, "zbarimg", '[ "$1" = --version ] && { echo 0.23; exit 0; }\n'
                f'shift 4\nprintf "%s|" "$@" >> "{zbar_log}"\necho >> "{zbar_log}"\nexit 4\n')
        path = self.dir.name + os.pathsep + os.environ.get("PATH", "")
        report = self.time("--readers", "zbar", "--runs", "1", env={"PATH": path})
        with open(zbar_log) as f:
            runs = _runs(f)
        tier = [os.path.join(self.dir.name, f) for f in ("a.pgm", "b.pgm", "c.pgm")]
        self.assertEqual(runs, [[os.path.join(self.dir.name, "b.pgm")], tier] * 2)
        self.assertEqual(report["readers"]["zbar"]["version"], "zbar 0.23")

    def test_a_reader_that_is_missing_is_skipped(self):
        report = self.time("--readers", "streepjes", env={"STREEPJES_BIN": "/nonexistent/streepjes"})
        self.assertIn("not found", report["readers"]["streepjes"]["skipped"])
        self.assertIn("skipped", timing.table(report))

    def test_usage_errors(self):
        for args in (["--readers", "streepjs"], ["--runs", "0"]):
            with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
                cli.main(["time", self.dir.name, *args])
            self.assertEqual(e.exception.code, 2, args)
        # One image gives no cost per image.
        self.write_manifest(["a.pgm"])
        with self.assertRaises(SystemExit) as e, contextlib.redirect_stderr(io.StringIO()):
            cli.main(["time", self.dir.name, "--readers", "streepjes"])
        self.assertEqual(e.exception.code, 2)

    def test_every_reader_can_be_timed(self):
        self.assertEqual(set(adapters.ADAPTERS), set(timing.PROCESSES) | {"zxing-cpp"})

    def test_zxing_cpp_is_timed_in_process(self):
        try:
            import zxingcpp  # noqa: F401
        except ImportError:
            self.skipTest("zxing-cpp is not installed")
        report = self.time("--readers", "zxing-cpp", "--runs", "1")
        zxing = report["readers"]["zxing-cpp"]
        self.assertTrue(zxing["version"].startswith("zxing-cpp "))
        self.assertEqual([len(zxing[k]) for k in ("python_runs", "import_runs", "read_runs")], [1, 1, 1])
        self.assertGreaterEqual(zxing["startup_s"], 0.0)
        self.assertAlmostEqual(zxing["tier_s"], zxing["startup_s"] + zxing["read_runs"][0])
        # A Python that cannot import it, isolated, leaves its start-up untimed.
        broken = _script(self.dir.name, "python", 'echo "No module named zxingcpp" >&2\nexit 1\n')
        with mock.patch.object(timing.sys, "executable", broken):
            report = self.time("--readers", "zxing-cpp", "--runs", "1")
        self.assertIn("cannot be timed", report["readers"]["zxing-cpp"]["skipped"])


if __name__ == "__main__":
    unittest.main()
