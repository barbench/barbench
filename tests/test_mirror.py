"""Tests for the photo mirror."""

import contextlib
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_photos import FakeResponse, encode, facts  # noqa: E402

from barbench import mirror, photos  # noqa: E402

RAW = "https://raw.githubusercontent.com/example/photos/" + "0" * 40 + "/"


class MirrorTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.png = encode(np.full((4, 6, 3), 200, np.uint8), 2)
        self.other = encode(np.full((4, 6, 3), 100, np.uint8), 2)
        self.listing = {
            "name": "test",
            "repository": "https://github.com/example/photos",
            "commit": "0" * 40,
            "path": "images",
            "licence_files": {"LICENSE": hashlib.sha256(b"Apache License text\n").hexdigest()},
            "license": "Apache-2.0",
            "notice": "Copyright the photographers.",
            "images": [
                {"file": "a/1.png", "sha256": hashlib.sha256(self.png).hexdigest(), **facts("test/a"),
                 "symbols": [{"symbology": "code128", "text": "A"}], "source": "a"},
                {"file": "b/1.png", "sha256": hashlib.sha256(self.other).hexdigest(), **facts("test/b"),
                 "symbols": [{"symbology": "ean13", "text": None}]},
            ],
        }
        self.served = {RAW + "images/a/1.png": self.png, RAW + "images/b/1.png": self.other,
                       RAW + "LICENSE": b"Apache License text\n"}
        self.asked = []
        # Every download retries after a pause; not here.
        patcher = mock.patch("barbench.photos.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.dir.cleanup()

    def path(self, *parts):
        return os.path.join(self.dir.name, *parts)

    def opener(self, request, timeout=None):
        url = request.full_url
        self.asked.append(url)
        if url not in self.served:
            raise urllib.error.URLError(f"not served: {url}")
        return FakeResponse(self.served[url])

    def build(self, out="out"):
        return mirror.build(self.path(out), self.path("source-cache"), "photos-1", [self.listing], self.opener)

    def serve_release(self, config, out="out"):
        asset = config["sets"]["test"]
        with open(self.path(out, asset["file"]), "rb") as f:
            self.served[mirror.RELEASES + "photos-1/" + asset["file"]] = f.read()

    def test_a_release_is_reproducible_and_carries_its_credits(self):
        config = self.build("one")
        again = self.build("two")
        self.assertEqual(config, again)
        asset = config["sets"]["test"]
        self.assertEqual(asset["file"], "photos-test.tar")
        with open(self.path("one", "mirror.json")) as f:
            self.assertEqual(json.load(f), config)
        with tarfile.open(self.path("one", "photos-test.tar")) as tar:
            names = tar.getnames()
            self.assertEqual(names, sorted(names))
            self.assertEqual(set(names), {"a/1.png", "b/1.png", "_mirror/test/LICENSE", "_mirror/test/CREDITS.md"})
            self.assertTrue(all(i.mtime == 0 and i.uid == 0 and i.uname == "" for i in tar.getmembers()))
            self.assertEqual(tar.extractfile("a/1.png").read(), self.png)
            credits = tar.extractfile("_mirror/test/CREDITS.md").read().decode()
        self.assertIn("unmodified", credits)
        self.assertIn(mirror.LEGAL["Apache-2.0"], credits)
        self.assertIn("LICENSE", credits)
        with self.assertRaisesRegex(ValueError, "not a release name"):
            mirror.build(self.path("x"), self.path("c"), "photos-one", [self.listing], self.opener)

    def test_credits_mark_photos_whose_public_domain_tag_covers_only_the_label(self):
        listing = {"name": "c", "source": "https://commons.wikimedia.org/", "license": "CC-BY-4.0 AND LicenseRef-Public-Domain",
                   "notice": "test", "host": "upload.wikimedia.org", "images": [
                       {"file": "x.jpg", "author": "A", "license": "CC-BY-4.0", "commons_license": "Cc-by-4.0",
                        "page": "https://commons.wikimedia.org/wiki/File:X.jpg"},
                       {"file": "y.jpg", "author": "B", "license": "LicenseRef-Public-Domain",
                        "commons_license": "PD-ineligible", "page": "https://commons.wikimedia.org/wiki/File:Y.jpg"}]}
        text = mirror.credits(listing)
        self.assertIn("| c/x.jpg | A | CC-BY-4.0 (Commons: Cc-by-4.0) |", text)
        self.assertIn("| c/y.jpg | B | LicenseRef-Public-Domain (Commons: PD-ineligible) (see below) |", text)
        self.assertIn("may carry rights of its own", text)
        self.assertIn(mirror.LEGAL["CC-BY-4.0"], text)

    def test_photos_come_from_the_mirror_first(self):
        config = self.build()
        self.serve_release(config)
        self.asked.clear()
        cache = self.path("cache")
        mirror.fill(self.listing, cache, self.opener, config)
        self.assertEqual(self.asked, [mirror.RELEASES + "photos-1/photos-test.tar"])
        with open(photos.cache_path(self.listing, "a/1.png", cache), "rb") as f:
            self.assertEqual(f.read(), self.png)
        # The source is not asked, and the asset is not kept.
        with mock.patch.object(mirror, "load", return_value=config):
            photos.fetch(self.listing, cache, self.opener)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual(os.listdir(self.path("cache", "_mirror")), [])
        # Nothing missing, nothing asked.
        mirror.fill(self.listing, cache, self.opener, config)
        self.assertEqual(len(self.asked), 1)

    def test_a_failing_mirror_leaves_the_photos_to_the_source(self):
        config = self.build()
        asset_url = mirror.RELEASES + "photos-1/photos-test.tar"
        broken = {
            "unreachable": None,
            "another asset": b"x" * config["sets"]["test"]["bytes"],
        }
        # An asset whose digest matches but lacks a photo, or holds the
        # wrong bytes for one.
        for name, members in (("missing photo", [("a/1.png", self.png)]),
                              ("wrong photo", [("a/1.png", self.other), ("b/1.png", self.other)]),
                              ("unnamed asset", [("a/1.png", self.png), ("b/1.png", self.other)])):
            mirror._tar(self.path(name + ".tar"), members)
            with open(self.path(name + ".tar"), "rb") as f:
                broken[name] = f.read()
        for name, data in broken.items():
            cache = self.path("cache-" + name.replace(" ", "-"))
            used = dict(config["sets"]["test"])
            if data is not None:
                self.served[asset_url] = data
                if name != "another asset":
                    used = {**used, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                # The right photos, in an asset mirror.json does not name.
                if name == "unnamed asset":
                    used["sha256"] = "0" * 64
            else:
                self.served.pop(asset_url, None)
            this = {"release": "photos-1", "sets": {"test": used}}
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), mock.patch.object(mirror, "load", return_value=this):
                paths = photos.fetch(self.listing, cache, self.opener)
            self.assertRegex(stderr.getvalue(), r"the photo mirror (failed|has no 1 of the photos listed) for test", name)
            for path, data_ in zip(paths, (self.png, self.other)):
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), data_, name)
            self.assertFalse(os.listdir(os.path.join(cache, "_mirror")), name)

    def test_the_mirror_can_be_skipped(self):
        config = self.build()
        self.serve_release(config)
        self.asked.clear()
        with mock.patch.dict(os.environ, {"BARBENCH_NO_MIRROR": "1"}):
            mirror.fill(self.listing, self.path("cache"), self.opener, config)
        self.assertEqual(self.asked, [])
        # A set the mirror does not have, or no mirror at all.
        mirror.fill(dict(self.listing, name="other"), self.path("cache"), self.opener, config)
        with mock.patch.object(mirror, "CONFIG", self.path("nothing.json")):
            self.assertIsNone(mirror.load(mirror.CONFIG))
        self.assertEqual(self.asked, [])

    def test_a_photo_is_never_written_outside_the_cache(self):
        config = self.build()
        self.serve_release(config)
        self.asked.clear()
        outside = dict(self.listing, images=[dict(self.listing["images"][0], file="../escape.png")])
        mirror.fill(outside, self.path("cache"), self.opener, config)
        self.assertEqual(self.asked, [])
        self.assertFalse(os.path.exists(self.path("escape.png")))

    def test_a_release_older_than_the_lists_still_gives_what_it_has(self):
        config = self.build()
        self.serve_release(config)
        newer = encode(np.full((4, 6, 3), 50, np.uint8), 2)
        self.served[RAW + "images/c/1.png"] = newer
        self.listing["images"].insert(0, {"file": "c/1.png", "sha256": hashlib.sha256(newer).hexdigest(),
                                          **facts("test/c"), "symbols": []})
        self.asked.clear()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), mock.patch.object(mirror, "load", return_value=config):
            photos.fetch(self.listing, self.path("cache"), self.opener)
        self.assertIn("has no 1 of the photos listed for test (added after photos-1)", stderr.getvalue())
        # The two it has come from the mirror; only the new one from the source.
        self.assertEqual(self.asked, [mirror.RELEASES + "photos-1/photos-test.tar", RAW + "images/c/1.png"])

    def test_a_mirror_that_is_down_costs_little(self):
        config = self.build()
        self.asked.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            mirror.fill(self.listing, self.path("cache"), self.opener, config)
        self.assertEqual(len(self.asked), mirror.MIRROR_TRIES)
        # A 4xx answer is not tried again.
        def missing(request, timeout=None):
            self.asked.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)

        self.asked.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            mirror.fill(self.listing, self.path("cache"), missing, config)
        self.assertEqual(len(self.asked), 1)

    def test_a_mirror_json_that_is_not_right_is_reported_not_raised(self):
        path = self.path("bad.json")
        with open(path, "w") as f:
            f.write("{")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), mock.patch.object(mirror, "CONFIG", path):
            photos.fetch(self.listing, self.path("cache"), self.opener)
        self.assertIn("is not set up right", stderr.getvalue())

    def test_only_regular_photos_within_the_cap_are_taken(self):
        config = self.build()
        for name, add in (("link", lambda tar: tar.addfile(tarfile.TarInfo("a/1.png"))),
                          ("big", None)):
            path = self.path(name + ".tar")
            with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tar:
                if name == "link":
                    info = tarfile.TarInfo("a/1.png")
                    info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                    tar.addfile(info)
                else:
                    info = tarfile.TarInfo("a/1.png")
                    info.size = 1
                    tar.addfile(info, io.BytesIO(b"x"))
            with open(path, "rb") as f:
                data = f.read()
            self.served[mirror.RELEASES + "photos-1/photos-test.tar"] = data
            this = {"release": "photos-1", "sets": {"test": {"file": "photos-test.tar", "bytes": len(data),
                                                             "sha256": hashlib.sha256(data).hexdigest()}}}
            stderr = io.StringIO()
            cache = self.path("cache-" + name)
            with contextlib.redirect_stderr(stderr), mock.patch.object(photos, "MAX_BYTES", 0 if name == "big" else photos.MAX_BYTES):
                mirror.fill(self.listing, cache, self.opener, this)
            self.assertIn("not a photo", stderr.getvalue(), name)
            self.assertFalse(os.path.exists(photos.cache_path(self.listing, "a/1.png", cache)), name)

    def test_a_release_is_built_from_the_sources(self):
        config = self.build()
        self.serve_release(config)
        self.asked.clear()
        # A mirror that has the set is not asked when a release is built.
        with mock.patch.object(mirror, "load", return_value=config):
            again = mirror.build(self.path("again"), self.path("fresh-cache"), "photos-1", [self.listing], self.opener,
                                 built_from="ab" * 20)
        self.assertNotIn(mirror.RELEASES + "photos-1/photos-test.tar", self.asked)
        self.assertEqual(again["built_from"], "ab" * 20)
        self.assertEqual(again["sets"], config["sets"])

    def test_licence_files_are_pinned_and_plainly_named(self):
        for files, message in (({"LICENSE": "0" * 64}, "not the listed file"),
                               ({"../LICENSE": "0" * 64}, "not a licence file's name"),
                               ({"..": "0" * 64}, "not a licence file's name"),
                               ({"CREDITS.md": "0" * 64}, "not a licence file's name")):
            with self.assertRaisesRegex(ValueError, message):
                mirror.build(self.path("x"), self.path("c"), "photos-1", [dict(self.listing, licence_files=files)],
                             self.opener)
        commons = {k: v for k, v in self.listing.items() if k not in ("repository", "commit", "path")}
        with self.assertRaisesRegex(ValueError, "come from a repository"), \
                mock.patch.object(photos, "fetch", return_value=[]):
            mirror.build(self.path("x"), self.path("c"), "photos-1", [dict(commons, images=[])], self.opener)

    def test_the_configuration_is_checked(self):
        good = {"release": "photos-1", "sets": {"test": {"file": "photos-test.tar", "bytes": 10, "sha256": "0" * 64}}}
        path = self.path("mirror.json")
        for bad in ({**good, "release": "latest"}, {**good, "sets": []},
                    {**good, "sets": {"test": {**good["sets"]["test"], "file": "../x.tar"}}},
                    {**good, "sets": {"test": {**good["sets"]["test"], "bytes": 0}}},
                    {**good, "sets": {"test": {**good["sets"]["test"], "sha256": "x"}}},
                    {**good, "sets": {"Test": good["sets"]["test"]}}, ["photos-1"], {**good, "extra": 1}):
            with open(path, "w") as f:
                json.dump(bad, f)
            with self.assertRaises(ValueError, msg=bad):
                mirror.load(path)
        with open(path, "w") as f:
            json.dump(good, f)
        self.assertEqual(mirror.load(path), good)


if __name__ == "__main__":
    unittest.main()
