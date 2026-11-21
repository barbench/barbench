"""The synthetic tier: Code 128 symbols from third-party encoders, rendered
and degraded with numpy.

Each image is a symbol on paper: its modules come from zxing-cpp or
python-barcode (alternating), and it is drawn at a module size, rotation,
blur and noise level from a fixed grid. A few negatives hold no symbol:
plain noise, and random bars that spell nothing. Everything follows from
the seed, and `manifest.json` records what each image holds. Every clean
symbol is read back before it is used, and payloads an encoder gets wrong
are listed in the manifest and replaced.
"""

import itertools
import json
import math
import os

from . import pgm, split

#: Quiet zone on each side of a symbol, in modules (ISO/IEC 15417's minimum).
QUIET = 10
#: Paper and ink levels.
PAPER, INK = 225.0, 35.0
#: Each pixel's ink is averaged over SUPERSAMPLE × SUPERSAMPLE points.
SUPERSAMPLE = 4

#: Payloads an encoder may get wrong in a row before the corpus gives up.
MAX_REJECTS = 20

#: The grid every positive image is drawn from.
GRID = {
    "module_px": [1.0, 1.5, 2.0, 3.0],
    "blur": [0.0, 0.3, 0.6],  # Gaussian sigma, in modules
    "rotation": [0.0, 15.0, 45.0, 90.0],  # degrees
    "noise": [0.0, 8.0],  # Gaussian sigma, in grey levels
}

ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-./ "


def payload(rng):
    """A payload of 4 to 16 characters, text (code set B) or digits (set C)
    with even odds, drawn from `rng`."""
    n = int(rng.integers(4, 17))
    if rng.integers(0, 2) == 0:
        return "".join(str(int(d)) for d in rng.integers(0, 10, n))
    # Text neither starts nor ends with a space, so it is what it says.
    edge = ALPHABET.replace(" ", "")
    inner = "".join(ALPHABET[int(k)] for k in rng.integers(0, len(ALPHABET), n - 2))
    first, last = (edge[int(k)] for k in rng.integers(0, len(edge), 2))
    return first + inner + last


def zxing_modules(text):
    """The modules zxing-cpp encodes `text` as, without quiet zones."""
    import zxingcpp

    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.Code128)
    image = barcode.to_image(scale=1, add_quiet_zones=False)
    import numpy as np

    row = np.asarray(image)[0]
    return [1 if v < 128 else 0 for v in row]


def python_barcode_modules(text):
    """The modules python-barcode encodes `text` as, without quiet zones."""
    from barcode import Code128

    return [int(c) for c in Code128(text).build()[0]]


ENCODERS = {"zxing-cpp": zxing_modules, "python-barcode": python_barcode_modules}


def round_trips(modules, text):
    """Whether a clean, large drawing of `modules` reads back as `text`.

    Encoders have bugs (python-barcode 0.16.1 drops a leading "99" in code
    set C), and a corpus image whose truth is wrong would count against
    every reader. A perfect symbol at four pixels per module is read the
    same by any working reader; zxing-cpp's reader checks it. (zxing-cpp
    is also benchmarked: it encodes half the symbols and checks them all,
    on perfect drawings, where any working reader agrees.)
    """
    import numpy as np
    import zxingcpp

    row = np.array([0] * QUIET + list(modules) + [0] * QUIET, dtype=np.uint8)
    image = np.repeat(np.repeat(255 - 255 * row[np.newaxis, :], 4, axis=1), 40, axis=0)
    found = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.Code128)
    return [r.text for r in found] == [text]


def encoder_versions():
    import importlib.metadata

    return {name: importlib.metadata.version(name) for name in ENCODERS}


def render(modules, module_px, rotation, blur, noise, rng, shift=(0.0, 0.0)):
    """`modules` (1 = bar) drawn as a symbol with quiet zones, rotated by
    `rotation` degrees about its centre, blurred and noised, as a uint8
    image just large enough to hold it with a margin. `shift` moves the
    symbol by a fraction of a pixel (x, y), so that its edges need not fall
    on pixel boundaries."""
    import numpy as np

    pattern = np.array([0] * QUIET + list(modules) + [0] * QUIET, dtype=np.float64)
    length = len(pattern)
    height = max(25.0, 0.3 * length)  # bar height, in modules
    theta = math.radians(rotation)
    c, s = math.cos(theta), math.sin(theta)
    half_w, half_h = length * module_px / 2, height * module_px / 2
    margin = 8 + 4 * blur * module_px
    width = int(math.ceil(2 * (abs(c) * half_w + abs(s) * half_h) + 2 * margin))
    tall = int(math.ceil(2 * (abs(s) * half_w + abs(c) * half_h) + 2 * margin))
    offsets = (np.arange(SUPERSAMPLE) + 0.5) / SUPERSAMPLE
    ys, xs = np.mgrid[0:tall, 0:width].astype(np.float64)
    ink = np.zeros((tall, width))
    for oy, ox in itertools.product(offsets, offsets):
        dx, dy = xs + ox - width / 2 - shift[0], ys + oy - tall / 2 - shift[1]
        u = (dx * c + dy * s) / module_px + length / 2
        v = (-dx * s + dy * c) / module_px + height / 2
        inside = (u >= 0) & (u < length) & (v >= 0) & (v < height)
        index = np.clip(u.astype(np.int64), 0, length - 1)
        ink += np.where(inside, pattern[index], 0.0)
    ink /= SUPERSAMPLE * SUPERSAMPLE
    image = PAPER + (INK - PAPER) * ink
    sigma = blur * module_px
    if sigma > 0:
        radius = int(math.ceil(3 * sigma))
        taps = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
        taps /= taps.sum()
        padded = np.pad(image, radius, mode="edge")
        image = np.apply_along_axis(lambda r: np.convolve(r, taps, "valid"), 1, padded)
        image = np.apply_along_axis(lambda r: np.convolve(r, taps, "valid"), 0, image)
    if noise > 0:
        image = image + rng.normal(0.0, noise, image.shape)
    return np.clip(np.rint(image), 0, 255).astype(np.uint8)


def generate(out_dir, seed=1, per_cell=2, negatives=8):
    """Writes the corpus to `out_dir`: PGM images and `manifest.json`.
    Returns the manifest."""
    import numpy as np

    part = split.of_seed(seed)
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)
    # Cells as their values and their indices along each axis.
    axes = [range(len(v)) for v in GRID.values()]
    cells = list(zip(itertools.product(*GRID.values()), itertools.product(*axes)))
    names = list(ENCODERS)
    entries = []
    rejected = []
    for i in range(len(cells) * per_cell):
        (module_px, blur, rotation, noise), index = cells[i // per_cell]
        # The encoder follows the parity of the cell's indices and the
        # image's place in it: every value of every axis gets each encoder
        # equally often, so no axis stands in for the encoder. (Any fixed
        # cycle over images would line up with some axis.)
        encoder = names[(sum(index) + i % per_cell) % len(names)]
        text, modules = symbol(rng, encoder, rejected)
        image = render(modules, module_px, rotation, blur, noise, rng)
        name = f"pos-{i:04d}.pgm"
        pgm.write(os.path.join(out_dir, name), image)
        entries.append(
            {
                "file": name,
                "expected": text,
                "split": part,
                "encoder": encoder,
                "module_px": module_px,
                "blur": blur,
                "rotation": rotation,
                "noise": noise,
            }
        )
    for i in range(negatives):
        name = f"neg-{i:04d}.pgm"
        if i % 2 == 0:
            image = np.clip(
                np.rint(rng.normal(128.0, 40.0, (120, 240))), 0, 255
            ).astype(np.uint8)
            kind = "noise"
        else:
            # Random bars and spaces of 1 to 4 modules: no symbol.
            widths = rng.integers(1, 5, 60)
            modules = [1 - k % 2 for k, w in enumerate(widths) for _ in range(int(w))]
            image = render(modules, 2.0, float(rng.integers(0, 180)), 0.0, 4.0, rng)
            kind = "random-bars"
        pgm.write(os.path.join(out_dir, name), image)
        entries.append({"file": name, "expected": None, "split": part, "kind": kind})
    manifest = {
        "tier": "synthetic",
        "seed": seed,
        "split": part,
        "encoders": encoder_versions(),
        "grid": GRID,
        "rejected": rejected,
        "images": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest


#: Where every sweep starts: a symbol most readers read. It is tilted as a
#: hand-held one is (`TILT`), not turned at random: some readers read only
#: near-upright symbols, and a random turn would make every curve of
#: theirs a rotation curve. Rotation has its own sweep.
BASELINE = {"module_px": 2.0, "blur": 0.2, "noise": 4.0}

#: The baseline's tilt: up to this many degrees either way, and upside
#: down half the time, so readers must read symbols both ways round.
TILT = 5.0

#: Each image's module pitch is its point's nominal value times a factor
#: drawn within this much of 1, so that every point averages over where
#: edges fall within pixels. At an exact whole number of pixels per module
#: every edge falls at the same phase, and how a reader fares there differs
#: from just beside it, by far more than a point's spread: in one run with
#: blur 0.6 and 40 symbols a point, zxing-cpp read 90% at one pixel against
#: none at 0.95 and 1.05, and 32% at two pixels against 95% and 80% at 1.9
#: and 2.1. No camera holds a pitch that exact.
JITTER = 0.05

#: The values each sweep moves one parameter through, the others at the
#: baseline. Rotation is swept from upright to a quarter turn (and upside
#: down half the time); elsewhere it is the baseline's tilt. Blur is a
#: Gaussian sigma in modules, noise one in grey levels. Module sizes are
#: nominal: each image's pitch is jittered (`JITTER`).
SWEEPS = {
    "module_px": [0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 2.0, 2.5, 3.0, 4.0, 6.0],
    "blur": [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0],
    "noise": [0.0, 4.0, 8.0, 16.0, 24.0, 32.0, 48.0, 64.0],
    "rotation": [0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0],
}


def symbol(rng, encoder, rejected):
    """A payload and its modules from `encoder`, read back before use: a
    payload the encoder gets wrong is recorded in `rejected` and replaced,
    a bounded number of times."""
    text = payload(rng)
    modules = ENCODERS[encoder](text)
    tries = 0
    while not round_trips(modules, text):
        rejected.append({"encoder": encoder, "payload": text})
        tries += 1
        if tries >= MAX_REJECTS:
            raise RuntimeError(f"{encoder} got {tries} payloads in a row wrong")
        text = payload(rng)
        modules = ENCODERS[encoder](text)
    return text, modules


def generate_sweep(out_dir, seed=1, per_point=20):
    """Writes the sweep tier to `out_dir`: for each parameter in `SWEEPS`,
    `per_point` symbols at each of its values, every other parameter at the
    baseline, as PGM images and `manifest.json`. Encoders alternate image
    by image, so each point gets each encoder equally often when
    `per_point` is even. Returns the manifest."""
    import numpy as np

    part = split.of_seed(seed)
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)
    names = list(ENCODERS)
    entries = []
    rejected = []
    i = 0
    for axis, values in SWEEPS.items():
        for value in values:
            for k in range(per_point):
                flip = 180.0 * int(rng.integers(0, 2))
                tilt = float(rng.uniform(-TILT, TILT))
                params = dict(BASELINE, rotation=(flip + tilt) % 360.0)
                params[axis] = (flip + value) % 360.0 if axis == "rotation" else value
                # A camera does not line its pixels up with the modules: at
                # one pixel per module, edges on pixel boundaries would draw
                # a symbol crisper than any photo of it.
                shift = (float(rng.uniform(0.0, 1.0)), float(rng.uniform(0.0, 1.0)))
                pitch = params["module_px"] * float(rng.uniform(1.0 - JITTER, 1.0 + JITTER))
                encoder = names[k % len(names)]
                text, modules = symbol(rng, encoder, rejected)
                image = render(
                    modules,
                    pitch,
                    params["rotation"],
                    params["blur"],
                    params["noise"],
                    rng,
                    shift,
                )
                name = f"sweep-{i:04d}.pgm"
                pgm.write(os.path.join(out_dir, name), image)
                entries.append(
                    {
                        "file": name,
                        "expected": text,
                        "split": part,
                        "encoder": encoder,
                        "sweep": axis,
                        "point": value,
                        **params,
                        "pitch_px": pitch,
                        "shift": list(shift),
                    }
                )
                i += 1
    manifest = {
        "tier": "sweep",
        "seed": seed,
        "split": part,
        "encoders": encoder_versions(),
        "grid": {},
        "baseline": dict(BASELINE, tilt=TILT, jitter=JITTER),
        "sweeps": {axis: list(values) for axis, values in SWEEPS.items()},
        "per_point": per_point,
        "rejected": rejected,
        "images": entries,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    return manifest
