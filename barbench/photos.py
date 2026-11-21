"""The photo tier: real photographs of Code 128 labels, and of retail
barcodes that are not Code 128 as negatives.

The photos are ZXing's blackbox test images (Apache-2.0), listed with their
SHA-256 in `bench/photos/zxing-blackbox.json` and downloaded from a pinned
commit at run time, so the repository carries none of them. Each is checked
against its digest, decoded from PNG here (zlib and numpy, no imaging
library) and written as a PGM with the same manifest the synthetic tier
writes.
"""

import hashlib
import json
import os
import re
import struct
import time
import urllib.error
import urllib.request
import zlib

from . import pgm

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, os.pardir, "photos", "zxing-blackbox.json")

#: The most bytes one download may hold: the largest photo is under 1 MB.
MAX_BYTES = 4 << 20
#: Seconds a download may stall before it fails, and how often it is tried.
TIMEOUT, TRIES = 60, 2
#: What a listed file may be called: a folder and a numbered PNG, never a
#: path out of the cache.
FILE_NAME = re.compile(r"[a-z0-9-]+/[0-9]+\.png")


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


def fetch(listing, cache, opener=urllib.request.urlopen):
    """Downloads every photo in `listing` into `cache` unless a file with
    the right digest is already there. Returns the paths, in order."""
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


def generate(out_dir, cache, listing=None, opener=urllib.request.urlopen):
    """Writes the photo tier to `out_dir` as PGMs with `manifest.json`,
    downloading into `cache` as needed. Returns the manifest."""
    listing = listing or load_list()
    paths = fetch(listing, cache, opener)
    os.makedirs(out_dir, exist_ok=True)
    entries = []
    sources = []
    for entry, path in zip(listing["images"], paths):
        with open(path, "rb") as f:
            image = decode_png(f.read())
        name = entry["file"].replace("/", "-")[: -len(".png")] + ".pgm"
        pgm.write(os.path.join(out_dir, name), image)
        row = {"file": name, "expected": entry["expected"], "from": entry["file"]}
        if entry["expected"] is None:
            row["kind"] = entry["kind"]
        else:
            row["source"] = entry["source"]
            if entry["source"] not in sources:
                sources.append(entry["source"])
        entries.append(row)
    manifest = {
        "tier": "photos",
        "seed": None,
        "encoders": {},
        "photos": {k: listing[k] for k in ("name", "repository", "commit", "license", "notice")},
        "grid": {"source": sources},
        "images": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest
