"""Tests for barbench. Run from bench/: python -m unittest discover -s tests"""

import contextlib
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
from barbench import adapters, corpus, pgm, score


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


class GateTest(unittest.TestCase):
    """The run's gates fail it, and refuse to pass without checking."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        pgm.write(os.path.join(d, "a.pgm"), np.full((8, 8), 255, np.uint8))
        manifest = {
            "tier": "synthetic",
            "seed": 0,
            "encoders": {},
            "grid": {"blur": [0.0]},
            "images": [{"file": "a.pgm", "expected": "A", "blur": 0.0}],
        }
        with open(os.path.join(d, "manifest.json"), "w") as f:
            json.dump(manifest, f)

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
