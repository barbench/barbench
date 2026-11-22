"""Adapters: one per reader, all with the same contract.

An adapter takes the paths of PGM images and returns, for each, the texts
the reader decoded (Code 128 only), in order, plus the wall-clock seconds
the whole batch took. A reader that is not installed raises `Unavailable`,
and the run skips it.
"""

import contextlib
import hashlib
import importlib.resources
import json
import os
import shutil
import subprocess
import sys
import time

from . import pgm


class Unavailable(Exception):
    """The reader is not installed here."""


def _json_lines(reader, stdout, count):
    """The results of a reader that prints one JSON line per image, in
    order: `{"codes": [{"text": ..., "bytes": ...}]}` or `{"error": ...}`."""
    lines = [json.loads(line) for line in stdout.splitlines()]
    if len(lines) != count:
        raise RuntimeError(f"{reader} printed {len(lines)} lines for {count} files")
    results = []
    for line in lines:
        if "error" in line:
            results.append({"texts": [], "error": line["error"]})
        else:
            # Bytes that are not UTF-8 stay in the results as hex, so a
            # misread of them still counts. A code with neither decoded
            # nothing, and is left out rather than stopping the run.
            texts = [
                c["text"] if c["text"] is not None else "hex:" + c["bytes"]
                for c in line["codes"]
                if c["text"] is not None or c["bytes"] is not None
            ]
            results.append({"texts": texts})
    return results


def streepjes(paths, binary=None):
    """The Streepjes reader's `streepjes` command: one process for the
    batch, with the default (fast) effort and Code 128 only."""
    binary = binary or os.environ.get("STREEPJES_BIN") or shutil.which("streepjes")
    if not binary or not os.path.exists(binary):
        raise Unavailable("streepjes binary not found (set STREEPJES_BIN)")
    start = time.perf_counter()
    done = subprocess.run(
        [binary, "--formats", "code128", "--", *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    seconds = time.perf_counter() - start
    if done.returncode not in (0, 1):
        raise RuntimeError(f"streepjes exited {done.returncode}: {done.stderr.strip()}")
    results = _json_lines("streepjes", done.stdout, len(paths))
    version = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout
    return results, seconds, version.strip()


def zxing_cpp(paths):
    """zxing-cpp's Python binding, in this process, Code 128 only."""
    try:
        import zxingcpp
    except ImportError as e:
        raise Unavailable("zxing-cpp is not installed") from e
    import importlib.metadata

    images = [pgm.read(p) for p in paths]
    start = time.perf_counter()
    results = []
    for image in images:
        # Plain text, as streepjes reports it: GS1 data without the
        # parentheses of its application identifiers, which the default
        # (HRI) adds and which the symbol does not hold.
        found = zxingcpp.read_barcodes(
            image, formats=zxingcpp.BarcodeFormat.Code128, text_mode=zxingcpp.TextMode.Plain
        )
        results.append({"texts": [r.text for r in found if r.valid]})
    seconds = time.perf_counter() - start
    return results, seconds, "zxing-cpp " + importlib.metadata.version("zxing-cpp")


def zbar(paths):
    """ZBar's `zbarimg`, one process per image (it stops at the first
    unreadable file), Code 128 only."""
    binary = shutil.which("zbarimg")
    if not binary:
        raise Unavailable("zbarimg is not installed")
    results = []
    start = time.perf_counter()
    for path in paths:
        done = subprocess.run(
            [binary, "--quiet", "--raw", "-Sdisable", "-Scode128.enable", path],
            capture_output=True,
            text=True,
            check=False,
        )
        # Exit 4 means nothing was found; anything else non-zero is an error.
        if done.returncode in (0, 4):
            results.append({"texts": [t for t in done.stdout.splitlines() if t]})
        else:
            results.append({"texts": [], "error": done.stderr.strip()})
    seconds = time.perf_counter() - start
    version = subprocess.run([binary, "--version"], capture_output=True, text=True).stdout
    return results, seconds, "zbar " + version.strip()


def vision(paths, binary=None):
    """Apple Vision's barcode detector (VNDetectBarcodesRequest at a pinned
    revision), through barbench/vision.swift: one process for the batch,
    Code 128 only. macOS only; the helper is built with `swiftc` the first
    time and kept (`_vision_binary`)."""
    binary = binary or _vision_binary()
    if not os.path.exists(binary):
        raise Unavailable(f"the Vision helper is missing: {binary}")
    # The helper refuses, with exit 3, a macOS without the pinned revision.
    probe = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    if probe.returncode == 3:
        raise Unavailable(probe.stderr.strip())
    if probe.returncode != 0:
        raise RuntimeError(f"vision --version exited {probe.returncode}: {probe.stderr.strip()}")
    start = time.perf_counter()
    done = subprocess.run([binary, *paths], capture_output=True, text=True, check=False)
    seconds = time.perf_counter() - start
    if done.returncode != 0:
        raise RuntimeError(f"vision exited {done.returncode}: {done.stderr.strip()}")
    return _json_lines("vision", done.stdout, len(paths)), seconds, probe.stdout.strip()


def _vision_binary():
    """The Vision helper, built from barbench/vision.swift unless a build of
    this very source is kept already. It is kept in BARBENCH_VISION_CACHE,
    or ~/Library/Caches/barbench, under the source's digest, so a changed
    source is built anew and an old build is never run in its place."""
    if sys.platform != "darwin":
        raise Unavailable("Apple Vision runs only on macOS")
    source = importlib.resources.files("barbench").joinpath("vision.swift")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    cache = os.environ.get("BARBENCH_VISION_CACHE") or os.path.expanduser(
        "~/Library/Caches/barbench"
    )
    binary = os.path.join(cache, f"vision-{digest}")
    if os.path.exists(binary):
        return binary
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise Unavailable("swiftc is not installed (xcode-select --install)")
    os.makedirs(cache, exist_ok=True)
    # Built under a name of its own, then moved into place, so that a build
    # cut short is never taken for a whole one.
    building = f"{binary}.{os.getpid()}"
    with importlib.resources.as_file(source) as path:
        done = subprocess.run(
            [swiftc, "-O", "-o", building, str(path)], capture_output=True, text=True, check=False
        )
    if done.returncode != 0:
        with contextlib.suppress(FileNotFoundError):
            os.remove(building)
        raise Unavailable(f"the Vision helper did not build: {done.stderr.strip()}")
    os.replace(building, binary)
    return binary


ADAPTERS = {"streepjes": streepjes, "zxing-cpp": zxing_cpp, "zbar": zbar, "vision": vision}
