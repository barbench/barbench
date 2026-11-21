"""Adapters: one per reader, all with the same contract.

An adapter takes the paths of PGM images and returns, for each, the texts
the reader decoded (Code 128 only), in order, plus the wall-clock seconds
the whole batch took. A reader that is not installed raises `Unavailable`,
and the run skips it.
"""

import json
import os
import shutil
import subprocess
import time

from . import pgm


class Unavailable(Exception):
    """The reader is not installed here."""


def streepjes(paths, binary=None):
    """The `streepjes` command (crates/streepjes-cli): one process for the
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
    lines = [json.loads(line) for line in done.stdout.splitlines()]
    if len(lines) != len(paths):
        raise RuntimeError(f"streepjes printed {len(lines)} lines for {len(paths)} files")
    results = []
    for line in lines:
        if "error" in line:
            results.append({"texts": [], "error": line["error"]})
        else:
            # Bytes that are not UTF-8 stay in the results as hex, so a
            # misread of them still counts.
            texts = [
                c["text"] if c["text"] is not None else "hex:" + c["bytes"] for c in line["codes"]
            ]
            results.append({"texts": texts})
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


ADAPTERS = {"streepjes": streepjes, "zxing-cpp": zxing_cpp, "zbar": zbar}
