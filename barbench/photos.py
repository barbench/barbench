"""The photo tier: real photographs of Code 128 labels, and of retail
barcodes that are not Code 128 as negatives.

Each set of photos is a listing in `lists/`, with every photo's
SHA-256: ZXing's blackbox test images (Apache-2.0), downloaded one by one
from a pinned commit; photos of Code 128 labels from Wikimedia Commons,
each under its own licence, downloaded one by one from their pinned
originals; and the KAIST and ArTe-Lab barcode sets (CC BY 4.0 and 3.0),
whose listed photos are taken from a pinned archive. The repository
carries none of the photos; barbench's photo mirror (mirror.py) carries
every one, and is asked first. Each is checked against its digest,
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
from . import slices

HERE = os.path.dirname(os.path.abspath(__file__))
LISTINGS = os.path.join(HERE, "lists")
MANIFEST = os.path.join(LISTINGS, "zxing-blackbox.json")

#: The most bytes one photo may hold: the largest is about 3.2 MB.
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
#: The one host a listing of single files may name, Wikimedia Commons'
#: file store, and what a photo's pinned URL there must look like: an
#: original (no thumbnail, no query), under its two hash folders.
COMMONS_HOST = "upload.wikimedia.org"
COMMONS_URL = re.compile(r"https://upload\.wikimedia\.org/wikipedia/commons/([0-9a-f])/\1[0-9a-f]/[A-Za-z0-9._%-]+")
#: What such a photo is called in the cache, under the set's folder.
LOCAL_NAME = re.compile(r"[a-z0-9-]+\.jpg")
#: The User-Agent sent to Wikimedia, and only there. Its robot policy asks
#: automated clients for contact details, so it names this project's
#: public repository; a client without them is answered 429 after a few
#: downloads, for minutes on end.
COMMONS_USER_AGENT = "barbench/0.1 (https://github.com/barbench/barbench)"
#: The downloads from Commons are one at a time, this many seconds apart
#: (the policy allows a client with contact details far more); a 429 or 503
#: answer is waited out as its Retry-After says, up to MAX_WAIT seconds,
#: and tried again up to COMMONS_TRIES times.
COMMONS_PAUSE, COMMONS_TRIES, MAX_WAIT = 1, 4, 120


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
        listing = json.load(f)
    check_rows(listing)
    check_symbols(listing)
    return listing


#: The symbologies a listed symbol may name; `null` names a symbol whose
#: kind cannot be told. A UPC-A symbol is `upca` with its 12 digits, never
#: the EAN-13 with a leading zero that has the same bars, so each symbol
#: has one spelling (a reader that reports the EAN-13 form, as zxing-cpp
#: 3.1.1 does, is to be scored as reading the UPC-A).
SYMBOLOGIES = ("code128", "ean13", "upca", "itf", "qr", "aztec")
#: The largest a corner's coordinate may be: far past any photo
#: MAX_BYTES allows.
MAX_COORDINATE = 1 << 24
#: The digits an EAN-13 and a UPC-A symbol hold, check digit included.
GTIN_DIGITS = {"ean13": 13, "upca": 12}


def check_symbols(listing):
    """Refuses a listing whose photos do not each list the symbols they
    show: a list, maybe empty, of symbologies from SYMBOLOGIES and texts,
    `null` where unknown. EAN-13 and UPC-A texts must have their digits
    and check digit, which also catches a text copied from the wrong
    place."""
    name = listing.get("name")
    for entry in listing["images"]:
        where = f"{name}: {entry.get('file')!r}"
        symbols = entry.get("symbols")
        if not isinstance(symbols, list):
            raise ValueError(f"{where} lists no symbols")
        for symbol in symbols:
            if not isinstance(symbol, dict) or {"symbology", "text"} - set(symbol) or set(symbol) - {
                "symbology",
                "text",
                "corners",
                "note",
            }:
                raise ValueError(f"{where}: {symbol!r} is not a symbology, a text and maybe corners and a note")
            if "corners" in symbol:
                check_corners(symbol["corners"], where)
            kind, text = symbol["symbology"], symbol["text"]
            if not isinstance(symbol.get("note", ""), str):
                raise ValueError(f"{where}: note {symbol['note']!r} is not text")
            if kind is not None and kind not in SYMBOLOGIES:
                raise ValueError(f"{where}: unknown symbology {kind!r}")
            if text is not None and (not isinstance(text, str) or not text or kind is None):
                raise ValueError(f"{where}: text {text!r} of symbology {kind!r}")
            if kind in GTIN_DIGITS and text is not None:
                if not (len(text) == GTIN_DIGITS[kind] and text.isascii() and text.isdigit() and gtin_checks(text)):
                    raise ValueError(f"{where}: {text!r} is not the digits of a valid {kind}")
                if kind == "ean13" and text.startswith("0"):
                    raise ValueError(f"{where}: {text!r} is a UPC-A; list it as upca without the 0")
        expected(entry, where)


def check_corners(corners, where):
    """Refuses corners that are not four points in pixels (x right, y
    down, (0, 0) the top-left corner of the top-left pixel; `generate`
    checks that they lie in the photo), in the order start-top, stop-top,
    stop-bottom, start-bottom: with y down, a convex quadrilateral that
    runs clockwise. The order is what tells a symbol's direction apart
    from its mirror image; it cannot tell a wrong start corner."""
    if not (
        isinstance(corners, list)
        and len(corners) == 4
        and all(
            isinstance(p, list)
            and len(p) == 2
            # The bounds also refuse NaN and infinity, and an integer too
            # large for a float before any arithmetic overflows.
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < MAX_COORDINATE for v in p)
            for p in corners
        )
    ):
        raise ValueError(f"{where}: corners {corners!r} are not four points [x, y] of 0 or more")
    turns = []
    for i in range(4):
        (ax, ay), (bx, by), (cx, cy) = corners[i], corners[(i + 1) % 4], corners[(i + 2) % 4]
        turns.append((bx - ax) * (cy - by) - (by - ay) * (cx - bx))
    if not all(t > 0 for t in turns):
        raise ValueError(f"{where}: corners {corners!r} do not run clockwise round a convex quadrilateral")


def gtin_checks(digits):
    """Whether the last of `digits` is the EAN/UPC check digit of the rest."""
    body = [int(d) for d in digits[:-1]]
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return (10 - total % 10) % 10 == int(digits[-1])


def expected(entry, where=None):
    """The text a Code 128 reader must report on a photo: that of its Code
    128 symbols, or None when it shows none. Refuses what scoring cannot
    yet judge: Code 128 symbols of different texts, or one of unknown
    text."""
    texts = {s["text"] for s in entry["symbols"] if s["symbology"] == "code128"}
    where = where or repr(entry.get("file"))
    if None in texts:
        raise ValueError(f"{where}: a Code 128 symbol of unknown text cannot be scored")
    if len(texts) > 1:
        raise ValueError(f"{where}: Code 128 symbols of different texts cannot be scored yet")
    return texts.pop() if texts else None


def check_rows(listing):
    """Refuses a photo without the facts a reader's held-out data is worked
    out from (see slices.py): a group named for its set, the slice that
    group's hash gives, and the date the photo was added. The slice is
    stored so it can be read without hashing, and checked so it never
    drifts from the rule."""
    name = listing.get("name")
    for entry in listing["images"]:
        where = f"{name}: {entry.get('file')!r}"
        group = entry.get("group")
        if not (isinstance(group, str) and slices.GROUP_NAME.fullmatch(group) and group.startswith(f"{name}/")):
            raise ValueError(f"{where}: group {group!r} is not {name}/ and a label or scene")
        if entry.get("slice") != slices.of_group(group):
            raise ValueError(f"{where}: slice {entry.get('slice')!r}, but group {group!r} is in {slices.of_group(group)}")
        slices.date(entry.get("added"), f"{where}: added")


def load_lists(names=None):
    """The listings in `lists/`, by name; all of them by default."""
    if names is not None and not names:
        raise ValueError("no photo sets named")
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


def cache_path(listing, file, cache):
    """Where `fetch` keeps a listed photo: ZXing's under the cache itself,
    as its files were cached before there were other sets, and every
    other set's under a folder named for the set."""
    if "archive" in listing or "host" in listing:
        return os.path.join(cache, listing["name"], file)
    return os.path.join(cache, file)


def fetch(listing, cache, opener=urllib.request.urlopen, use_mirror=True):
    """Downloads every photo in `listing` into `cache` unless a file with
    the right digest is already there: from barbench's photo mirror first,
    when `use_mirror` and the mirror has the set, then from the set's own
    source. Returns the paths, in order."""
    if use_mirror:
        # Imported here: mirror.py builds on this module.
        from . import mirror

        mirror.fill(listing, cache, opener)
    if "archive" in listing:
        return _fetch_archive(listing, cache, opener)
    if "host" in listing:
        return _fetch_files(listing, cache, opener)
    base = (
        listing["repository"].replace("github.com", "raw.githubusercontent.com")
        + f"/{listing['commit']}/{listing['path']}/"
    )
    paths = []
    for entry in listing["images"]:
        if not FILE_NAME.fullmatch(entry["file"]):
            raise ValueError(f"{entry['file']!r}: not a listed photo's name")
        path = cache_path(listing, entry["file"], cache)
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


def _fetch_files(listing, cache, opener):
    """Downloads each listed photo from its own pinned URL on Commons into
    the set's folder, unless a file with the right digest is there."""
    if listing["host"] != COMMONS_HOST:
        raise ValueError(f"{listing['host']!r}: not a host photos are taken from")
    if not SET_NAME.fullmatch(listing["name"]):
        raise ValueError(f"{listing['name']!r}: not a set's name")
    folder = os.path.join(cache, listing["name"])
    paths, downloaded = [], False
    for entry in listing["images"]:
        if not LOCAL_NAME.fullmatch(entry["file"]):
            raise ValueError(f"{entry['file']!r}: not a listed photo's name")
        if not COMMONS_URL.fullmatch(entry["url"]):
            raise ValueError(f"{entry['url']!r}: not a pinned Commons original")
        path = cache_path(listing, entry["file"], cache)
        if not (os.path.exists(path) and _digest(path) == entry["sha256"]):
            if downloaded:
                time.sleep(COMMONS_PAUSE)
            data = _download_patiently(entry["url"], opener)
            downloaded = True
            if len(data) > MAX_BYTES:
                raise ValueError(f"{entry['file']}: larger than {MAX_BYTES} bytes")
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValueError(f"{entry['file']}: digest does not match the list")
            os.makedirs(folder, exist_ok=True)
            with open(path, "wb") as f:
                f.write(data)
        paths.append(path)
    return paths


def _download_patiently(url, opener):
    """The bytes at `url`, at most one more than MAX_BYTES, tried
    COMMONS_TRIES times, waiting out a 429 or 503 as its Retry-After says."""
    request = urllib.request.Request(url, headers={"User-Agent": COMMONS_USER_AGENT})
    for attempt in range(COMMONS_TRIES):
        try:
            with opener(request, timeout=TIMEOUT) as response:
                return response.read(MAX_BYTES + 1)
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            if attempt + 1 == COMMONS_TRIES:
                raise
            wait = 2 << attempt
            if isinstance(e, urllib.error.HTTPError):
                e.close()
                if e.code in (429, 503):
                    after = (e.headers or {}).get("Retry-After", "").strip()
                    # ASCII seconds only, at most MAX_WAIT; an HTTP date, or
                    # nothing, keeps the backoff.
                    if re.fullmatch(r"[0-9]+", after, re.ASCII):
                        wait = min(max(int(after), 1), MAX_WAIT)
            time.sleep(wait)
    raise AssertionError("unreachable")


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
        path = cache_path(listing, entry["file"], cache)
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


def _download_to(url, path, size, opener, tries=ARCHIVE_TRIES):
    """Writes the `size` bytes at `url` to `path`, tried `tries` times. More
    bytes than that is an error; fewer, a transfer cut short, is tried
    again. A 4xx answer is not: the file is not there to try again for."""
    request = urllib.request.Request(url, headers={"User-Agent": "barbench"})
    for attempt in range(tries):
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
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, ConnectionError) as e:
            if attempt + 1 == tries or (isinstance(e, urllib.error.HTTPError) and 400 <= e.code < 500):
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
CREDIT = ("name", "repository", "commit", "host", "archive", "archive_sha256", "source", "license", "notice")
#: What it records of each photo in a set whose photos are each licensed
#: on their own.
PHOTO_CREDIT = ("author", "license", "page")


class NothingChosen(ValueError):
    """No listed photo is in the slices and dates asked for."""


def generate(out_dir, cache, listings=None, opener=urllib.request.urlopen, chosen=slices.SLICES, added_after=None,
             leave_out=None):
    """Writes the photo tier to `out_dir` as PGMs with `manifest.json`,
    downloading into `cache` as needed: the photos in the `chosen` slices,
    added after `added_after` (a date) when that is given, less those that
    `leave_out` (slices and a date, as slices.held_out_by gives) names.
    Returns the manifest."""
    listings = load_lists() if listings is None else listings
    for listing in listings:
        check_rows(listing)
        check_symbols(listing)
    if added_after is not None:
        slices.date(added_after, "added after")

    def wanted(e):
        if e["slice"] not in chosen or (added_after is not None and e["added"] <= added_after):
            return False
        return leave_out is None or not (e["slice"] in leave_out[0] and e["added"] > leave_out[1])

    # Only the chosen photos are fetched: an archive is still downloaded
    # whole, but a set of single files fetches no more than it needs.
    listings = [
        dict(listing, images=[e for e in listing["images"] if wanted(e)])
        for listing in listings
    ]
    listings = [listing for listing in listings if listing["images"]]
    if not listings:
        raise NothingChosen(f"no listed photo is in slices {','.join(chosen)}"
                            + (f" and added after {added_after}" if added_after else ""))
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
            height, width = image.shape
            for symbol in entry["symbols"]:
                if any(x > width or y > height for x, y in symbol.get("corners", ())):
                    raise ValueError(f"{listing['name']}: {entry['file']}: corners outside its {width}x{height} pixels")
            stem = os.path.splitext(entry["file"])[0].replace("/", "-")
            name = listing["name"] + "-" + re.sub(r"[^A-Za-z0-9_-]", "", stem) + ".pgm"
            if name in names:
                raise ValueError(f"{entry['file']}: two photos would be written as {name}")
            names.add(name)
            pgm.write(os.path.join(out_dir, name), image)
            text = expected(entry)
            row = {
                "file": name,
                "expected": text,
                "symbols": entry["symbols"],
                "set": listing["name"],
                "from": entry["file"],
                "group": entry["group"],
                "slice": entry["slice"],
                "added": entry["added"],
            }
            row.update({k: entry[k] for k in PHOTO_CREDIT if k in entry})
            if text is not None:
                row["source"] = entry["source"]
                if entry["source"] not in sources:
                    sources.append(entry["source"])
            entries.append(row)
    manifest = {
        "tier": "photos",
        "seed": None,
        "slices": "".join(chosen),
        "added_after": added_after,
        "left_out": list(leave_out) if leave_out else None,
        "encoders": {},
        "photos": [{k: listing[k] for k in CREDIT if k in listing} for listing in listings],
        "grid": {"source": sources},
        "images": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest
