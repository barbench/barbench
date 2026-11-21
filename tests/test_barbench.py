"""Tests for barbench. Run from bench/: python -m unittest discover -s tests"""

import contextlib
import copy
import hashlib
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

import numpy as np

from barbench import __main__ as cli
from barbench import adapters, corpus, pgm, plot, score, split


class PgmTest(unittest.TestCase):
    def test_round_trip(self):
        image = np.arange(60, dtype=np.uint8).reshape(5, 12)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a.pgm")
            pgm.write(path, image)
            np.testing.assert_array_equal(pgm.read(path), image)

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
            self.assertEqual({first["split"]} | {e["split"] for e in first["images"]}, {"dev"})
            test = corpus.generate(b, seed=1000, per_cell=1, negatives=2)
            self.assertEqual({test["split"]} | {e["split"] for e in test["images"]}, {"test"})


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
            self.assertEqual(report["split"], "dev")
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


class SplitTest(unittest.TestCase):
    """The rule that deals data into development and test is fixed: a
    change would re-deal photos that have already been looked at."""

    def test_groups_are_dealt_by_their_hash(self):
        # SHA-256 of "a" starts ca97 (51863), of "b" 3e23 (15907); test is
        # below 19661 of 65536, and the two edge groups start just either
        # side of it (19660 and 19661).
        known = {
            "a": "dev",
            "b": "test",
            "edge-1574": "test",
            "edge-59861": "dev",
            "commons-code128/zlin-lamp": "test",
            "own/2026-12-01-market": "dev",
            "own/2026-12-02-depot": "test",
        }
        self.assertEqual({g: split.of_group(g) for g in known}, known)
        share = sum(split.of_group(f"g{i}") == "test" for i in range(20000)) / 20000
        self.assertAlmostEqual(share, 0.3, delta=0.01)

    def test_seeds_from_1000_make_test_images(self):
        self.assertEqual([split.of_seed(s) for s in (0, 1, 999, 1000, 5000)], ["dev"] * 3 + ["test"] * 2)
        with self.assertRaises(ValueError):
            split.of_seed(-1)

    def test_the_cli_keeps_seeds_to_their_split(self):
        with tempfile.TemporaryDirectory() as d:
            for command in ("corpus", "sweep"):
                for args in (["--seed", "1000"], ["--seed", "1", "--split", "test"], ["--seed", "-1"]):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                        cli.main([command, os.path.join(d, command), *args])
                    self.assertEqual(e.exception.code, 2, (command, args))
                    self.assertFalse(os.path.exists(os.path.join(d, command)), (command, args))
            with mock.patch.dict(corpus.SWEEPS, {"blur": [0.2]}, clear=True):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["sweep", os.path.join(d, "t"), "--seed", "1000", "--split", "test",
                                               "--per-point", "1"]), 0)
            with open(os.path.join(d, "t", "manifest.json")) as f:
                manifest = json.load(f)
            self.assertEqual([manifest["split"]] + [e["split"] for e in manifest["images"]], ["test"] * 2)

    def test_the_cli_writes_only_the_photos_of_its_split(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "photos")
            # Stubbed, so a broken check fails here rather than downloading.
            with mock.patch("barbench.photos.generate") as generate:
                for args in (["--split", "test"], ["--sets", "kaist-real", "--split", "test"], ["--sets", ","]):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as e:
                        cli.main(["photos", out, *args])
                    self.assertEqual(e.exception.code, 2, args)
            generate.assert_not_called()
            self.assertEqual(os.listdir(d), [])


class GateTest(unittest.TestCase):
    """The run's gates fail it, and refuse to pass without checking."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        pgm.write(os.path.join(d, "a.pgm"), np.full((8, 8), 255, np.uint8))
        self.manifest = {
            "tier": "synthetic",
            "seed": 0,
            "split": "dev",
            "encoders": {},
            "grid": {"blur": [0.0]},
            "images": [{"file": "a.pgm", "expected": "A", "split": "dev", "blur": 0.0}],
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

    def test_test_data_is_scored_only_when_asked_for(self):
        line = {"file": "a.pgm", "codes": [{"text": "A", "bytes": "41"}]}
        self.assertEqual(self.run_cli(line=line), 0)
        # A development run refuses a test entry, the manifest's split, or
        # a split left unsaid; a test run refuses development data.
        base = copy.deepcopy(self.manifest)
        for where, value in (("image", "test"), ("image", None), ("manifest", "test"), ("manifest", None)):
            for args in ((), ("--split", "test")):
                self.manifest = copy.deepcopy(base)
                target = self.manifest["images"][0] if where == "image" else self.manifest
                if value is None:
                    del target["split"]
                else:
                    target["split"] = value
                self.write_manifest()
                with self.assertRaises(SystemExit) as e:
                    self.run_cli(*args, line=line)
                self.assertEqual(e.exception.code, 2, (where, value, args))
        self.manifest = copy.deepcopy(base)
        self.manifest["split"] = self.manifest["images"][0]["split"] = "test"
        self.write_manifest()
        self.assertEqual(self.run_cli("--split", "test", line=line), 0)


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


if __name__ == "__main__":
    unittest.main()
