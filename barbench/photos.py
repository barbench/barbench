"""The photo tier: real photographs of Code 128 labels, and of retail
barcodes that are not Code 128 as negatives.

Each set of photos is a listing in `bench/photos/`, with every photo's
SHA-256: ZXing's blackbox test images (Apache-2.0), downloaded one by one
from a pinned commit, and the KAIST and ArTe-Lab barcode sets (CC BY 4.0
and 3.0), whose listed photos are taken from a pinned archive. The
repository carries none of the photos. Each is checked against its digest,
decoded here from PNG or baseline JPEG (zlib and numpy, no imaging library;
see jpeg.py for why) and written as a PGM with the same manifest the
synthetic tier writes.
"""

import glob
import hashlib
import http.client
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
import zipfile
import zlib

from . import jpeg, pgm

HERE = os.path.dirname(os.path.abspath(__file__))
LISTINGS = os.path.join(HERE, os.pardir, "photos")
MANIFEST = os.path.join(LISTINGS, "zxing-blackbox.json")

#: The most bytes one photo may hold: the largest is under 2 MB.
MAX_BYTES = 4 << 20
#: The most bytes one archive may hold: the largest is about 225 MB. A
#: listing also pins each archive's exact size.
MAX_ARCHIVE_BYTES = 512 << 20
#: Seconds a download may stall before it fails, and how often it is tried.
TIMEOUT, TRIES = 60, 2
#: How often an archive download is tried. Zenodo answers with server
#: errors now and then, so it waits 2, 4, 8 and 16 seconds between tries.
ARCHIVE_TRIES = 5
#: What a listed file may be called: a folder and a numbered PNG, never a
#: path out of the cache.
FILE_NAME = re.compile(r"[a-z0-9-]+/[0-9]+\.png")
#: The same for a photo in an archive. It names the member to read, never
#: a path the archive chooses, and its cache path under the set's folder.
MEMBER_NAME = re.compile(r"[A-Za-z0-9_-]+(/[A-Za-z0-9_()-]+)*\.jpg")
#: What a set may be called; it names its folder in the cache.
SET_NAME = re.compile(r"[a-z0-9-]+")


class PngError(ValueError):
    """A PNG this decoder does not read."""


def decode_png(data):
    """An 8-bit, non-interlaced grayscale, RGB, palette, grayscale-alpha or
    RGBA PNG as a 2D uint8 luma array (ITU-R BT.601 weights)."""
    import numpy as np

    try:
        return _decode_png(data)
    except (struct.error, IndexError, zlib.error) as e:
        raise PngError(f"malformed PNG: {e}") from e


def _decode_png(data):
    import numpy as np

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("not a PNG")
    at, header, palette, idat = 8, None, None, []
    while at + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[at : at + 8])
        body = data[at + 8 : at + 8 + length]
        if len(body) != length:
            raise PngError("truncated chunk")
        if kind == b"IHDR":
            if length != 13:
                raise PngError("bad header")
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            if length % 3:
                raise PngError("bad palette")
            palette = np.frombuffer(body, np.uint8).reshape(-1, 3)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        at += 12 + length
    if header is None or not idat:
        raise PngError("no header or no image data")
    width, height, depth, colour, _, _, interlace = header
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(colour)
    if depth != 8 or interlace != 0 or channels is None or (colour == 3 and palette is None):
        raise PngError(f"unsupported PNG: depth {depth}, colour {colour}, interlace {interlace}")
    stride = width * channels
    size = height * (stride + 1)
    # Inflate no more than the image needs, whatever the data claims.
    raw = zlib.decompressobj().decompress(b"".join(idat), size)
    if len(raw) < size:
        raise PngError("truncated image data")
    rows = np.zeros((height, stride), np.uint8)
    previous = np.zeros(stride, np.int32)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        line = np.frombuffer(raw[start + 1 : start + 1 + stride], np.uint8).astype(np.int32)
        out = np.zeros(stride, np.int32)
        if kind in (0, 2, 3):
            # None and up are whole-row operations; average depends on the
            # byte before, so it goes byte by byte, as Paeth does.
            if kind == 0:
                out = line
            elif kind == 2:
                out = (line + previous) & 0xFF
            else:
                for i in range(stride):
                    left = out[i - channels] if i >= channels else 0
                    out[i] = (line[i] + (left + previous[i]) // 2) & 0xFF
        elif kind == 1:
            # Sub: a running sum along each channel.
            out = np.cumsum(line.reshape(width, channels), axis=0).reshape(stride) & 0xFF
        elif kind == 4:
            for i in range(stride):
                left = out[i - channels] if i >= channels else 0
                up = previous[i]
                corner = previous[i - channels] if i >= channels else 0
                p = left + up - corner
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else corner
                out[i] = (line[i] + pred) & 0xFF
        else:
            raise PngError(f"unknown filter {kind}")
        rows[y] = out
        previous = out
    pixels = rows.reshape(height, width, channels)
    if colour == 3:
        if int(pixels.max(initial=0)) >= len(palette):
            raise PngError("palette index out of range")
        rgb = palette[pixels[:, :, 0]].astype(np.int32)
    elif colour in (0, 4):
        return pixels[:, :, 0].copy()
    else:
        rgb = pixels[:, :, :3].astype(np.int32)
    luma = (299 * rgb[:, :, 0] + 587 * rgb[:, :, 1] + 114 * rgb[:, :, 2] + 500) // 1000
    return luma.astype(np.uint8)


def load_list(path=MANIFEST):
    with open(path) as f:
        return json.load(f)


def load_lists(names=None):
    """The listings in bench/photos, by name; all of them by default."""
    found = {}
    for path in sorted(glob.glob(os.path.join(LISTINGS, "*.json"))):
        listing = load_list(path)
        found[listing["name"]] = listing
    if names is None:
        return list(found.values())
    missing = [n for n in names if n not in found]
    if missing:
        raise ValueError(f"no photo set named {', '.join(missing)} (have {', '.join(found)})")
    return [found[n] for n in names]


def fetch(listing, cache, opener=urllib.request.urlopen):
    """Downloads every photo in `listing` into `cache` unless a file with
    the right digest is already there. Returns the paths, in order."""
    if "archive" in listing:
        return _fetch_archive(listing, cache, opener)
    base = (
        listing["repository"].replace("github.com", "raw.githubusercontent.com")
        + f"/{listing['commit']}/{listing['path']}/"
    )
    paths = []
    for entry in listing["images"]:
        if not FILE_NAME.fullmatch(entry["file"]):
            raise ValueError(f"{entry['file']!r}: not a listed photo's name")
        path = os.path.join(cache, entry["file"])
        if not (os.path.exists(path) and _digest(path) == entry["sha256"]):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            data = _download(base + entry["file"], opener)
            if len(data) > MAX_BYTES:
                raise ValueError(f"{entry['file']}: larger than {MAX_BYTES} bytes")
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"{entry['file']}: digest does not match the list")
            with open(path, "wb") as f:
                f.write(data)
        paths.append(path)
    return paths


def _fetch_archive(listing, cache, opener):
    """Takes the listed photos from the listing's archive, which is
    downloaded only if a photo is missing from the cache, checked against
    its size and digest, and deleted once the photos are out."""
    if not SET_NAME.fullmatch(listing["name"]):
        raise ValueError(f"{listing['name']!r}: not a set's name")
    folder = os.path.join(cache, listing["name"])
    paths, missing = [], []
    for entry in listing["images"]:
        if not MEMBER_NAME.fullmatch(entry["file"]) or ".." in entry["file"]:
            raise ValueError(f"{entry['file']!r}: not a listed photo's name")
        path = os.path.join(folder, entry["file"])
        if not (os.path.exists(path) and _digest(path) == entry["sha256"]):
            missing.append((entry, path))
        paths.append(path)
    if not missing:
        return paths
    size = listing["archive_bytes"]
    if not 0 < size <= MAX_ARCHIVE_BYTES:
        raise ValueError(f"{listing['name']}: archive size {size} is out of range")
    os.makedirs(folder, exist_ok=True)
    archive = os.path.join(folder, "archive.zip.part")
    try:
        _download_to(listing["archive"], archive, size, opener)
        if _digest(archive) != listing["archive_sha256"]:
            raise ValueError(f"{listing['name']}: archive digest does not match the list")
        with zipfile.ZipFile(archive) as z:
            for entry, path in missing:
                try:
                    info = z.getinfo(entry["file"])
                except KeyError:
                    raise ValueError(f"{entry['file']}: not in the archive") from None
                if info.file_size > MAX_BYTES:
                    raise ValueError(f"{entry['file']}: larger than {MAX_BYTES} bytes")
                # Read no more than the cap, whatever the header claims.
                with z.open(info) as member:
                    data = member.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError(f"{entry['file']}: larger than {MAX_BYTES} bytes")
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError(f"{entry['file']}: digest does not match the list")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
    finally:
        if os.path.exists(archive):
            os.remove(archive)
    return paths


def _download_to(url, path, size, opener):
    """Writes the `size` bytes at `url` to `path`, tried ARCHIVE_TRIES times.
    More bytes than that is an error; fewer, a transfer cut short, is tried
    again."""
    request = urllib.request.Request(url, headers={"User-Agent": "barbench"})
    for attempt in range(ARCHIVE_TRIES):
        try:
            with opener(request, timeout=TIMEOUT) as response, open(path, "wb") as f:
                written = 0
                while chunk := response.read(1 << 20):
                    written += len(chunk)
                    if written > size:
                        raise ValueError(f"{url}: larger than the listed {size} bytes")
                    f.write(chunk)
            if written != size:
                raise ConnectionError(f"{url}: {written} bytes, not the listed {size}")
            return
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError):
            if attempt + 1 == ARCHIVE_TRIES:
                raise
            time.sleep(2 << attempt)


def _download(url, opener):
    """The bytes at `url`, at most one more than MAX_BYTES, tried TRIES
    times."""
    request = urllib.request.Request(url, headers={"User-Agent": "barbench"})
    for attempt in range(TRIES):
        try:
            with opener(request, timeout=TIMEOUT) as response:
                return response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt + 1 == TRIES:
                raise
            time.sleep(2)
    raise AssertionError("unreachable")


def _digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


#: What the manifest records of each set: where it came from and under
#: which licence.
CREDIT = ("name", "repository", "commit", "archive", "archive_sha256", "source", "license", "notice")


def generate(out_dir, cache, listings=None, opener=urllib.request.urlopen):
    """Writes the photo tier to `out_dir` as PGMs with `manifest.json`,
    downloading into `cache` as needed. Returns the manifest."""
    listings = load_lists() if listings is None else listings
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    sources = []
    names = set()
    for listing in listings:
        paths = fetch(listing, cache, opener)
        for entry, path in zip(listing["images"], paths):
            with open(path, "rb") as f:
                data = f.read()
            image = jpeg.decode_jpeg(data) if entry["file"].endswith(".jpg") else decode_png(data)
            stem = os.path.splitext(entry["file"])[0].replace("/", "-")
            name = listing["name"] + "-" + re.sub(r"[^A-Za-z0-9_-]", "", stem) + ".pgm"
            if name in names:
                raise ValueError(f"{entry['file']}: two photos would be written as {name}")
            names.add(name)
            pgm.write(os.path.join(out_dir, name), image)
            row = {"file": name, "expected": entry["expected"], "set": listing["name"], "from": entry["file"]}
            if entry["expected"] is None:
                row["kind"] = entry["kind"]
                if "payload" in entry:
                    row["payload"] = entry["payload"]
            else:
                row["source"] = entry["source"]
                if entry["source"] not in sources:
                    sources.append(entry["source"])
            entries.append(row)
    manifest = {
        "tier": "photos",
        "seed": None,
        "encoders": {},
        "photos": [{k: listing[k] for k in CREDIT if k in listing} for listing in listings],
        "grid": {"source": sources},
        "images": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest
