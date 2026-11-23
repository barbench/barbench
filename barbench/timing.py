"""Timing: what each reader costs to start, and what it costs per image.

    python -m barbench time DIR [--readers a,b] [--runs N] [--out timing.json]

`run` gives each reader's seconds for its whole batch, which mix start-up
with work and are not comparable between readers. This splits them.

A reader that runs as a process (streepjes, zbar, vision) runs on the
tier's smallest image, and on the whole tier, each time as one process.
The run on one image is its start-up: starting the process, loading what
it loads, and one small image. The rest of the run on the whole tier,
divided by its other images, is its cost per image, reading the files
included. ZBar runs as one process here, although `run` starts it once
per image (it stops at the first file it cannot read, and its output does
not say which file a code came from): started per image, its start-up
would count in every image.

zxing-cpp runs in barbench's own process, as `run` reads it. Its start-up
is importing it in a fresh Python, less starting that Python; its cost per
image is a read of the whole tier, divided by its images, from images
already in memory, so it leaves out reading the files that the others
count.

Every figure is the median of N runs, after one warm-up run of each that
is not counted, so that the files are cached and the reader loaded once.
Runs on one image and on the tier take turns, so a change in the machine's
load falls on both. Every run is checked: a reader that exits with an
error, or (streepjes and vision, which print a line per file) prints the
wrong number of results, stops the timing rather than give a time for
work it did not do. With few runs, zxing-cpp's start-up, a difference of
two Python start-ups, is mostly noise.

Times are wall-clock, so they depend on the machine and on what else runs
on it: the report names the machine and gives the load average before and
after.

Every reader is asked for the corpus's symbology alone: Code 128, or QR
Code or Data Matrix on a corpus `barbench corpus --symbology` drew. A
reader that does not read it (ZBar reads no Data Matrix, and a streepjes
build may lack either) is skipped, as one that is not installed is, so
that every time in a report is for the same request. Those corpora are
drawn and checked by zxing-cpp alone (corpus.py).
"""

import importlib.metadata
import os
import platform
import statistics
import subprocess
import sys
import time

from . import adapters, pgm

RUNS = 5


def split(startup_runs, tier_runs, count):
    """Start-up and cost per image, from runs on one image and runs on all
    COUNT images of a tier, in seconds."""
    if count < 2:
        raise ValueError("a cost per image needs a tier of two images or more")
    startup = statistics.median(startup_runs)
    tier = statistics.median(tier_runs)
    # On a tier of a few small images, noise can make the whole run no
    # longer than the run on one; no image costs less than nothing.
    per_image = max(tier - startup, 0.0) / (count - 1)
    return {
        "startup_s": startup,
        "per_image_s": per_image,
        "tier_s": tier,
        "startup_runs": list(startup_runs),
        "tier_runs": list(tier_runs),
    }


def _streepjes(symbology):
    binary = adapters._streepjes_binary()
    adapters.streepjes_reads(binary, symbology)
    return [binary, *adapters.streepjes_options(symbology)], (0, 1), True, adapters._version(binary)


def _zbar(symbology):
    binary = adapters._zbar_binary()
    # Exit 4 means it found nothing in any file; it prints no line per file.
    return [binary, *adapters.zbar_options(symbology)], (0, 4), False, "zbar " + adapters._version(binary)


def _vision(symbology):
    options = adapters.vision_options(symbology)
    binary = adapters._vision_binary()
    return [binary, *options], (0,), True, adapters._vision_version(binary)


# How each process reader is run, asked for a symbology: its command before
# the paths, the exit codes that mean it worked, whether it prints a JSON
# line per file, and its version. Each raises `adapters.Unavailable` if the
# reader is missing or does not read the symbology.
PROCESSES = {"streepjes": _streepjes, "zbar": _zbar, "vision": _vision}


def time_process(name, paths, small, runs, symbology="code128"):
    """Times a reader that runs as a process; see the module's docstring."""
    command, ok, lines, version = PROCESSES[name](symbology)

    def once(chosen):
        start = time.perf_counter()
        done = subprocess.run([*command, *chosen], capture_output=True, text=True, check=False)
        seconds = time.perf_counter() - start
        if done.returncode not in ok:
            raise RuntimeError(f"{name} exited {done.returncode}: {done.stderr.strip()}")
        if lines:
            adapters._json_lines(name, done.stdout, len(chosen))
        return seconds

    # Warm-up: not counted.
    once([small])
    once(paths)
    startup, tier = [], []
    for _ in range(runs):
        startup.append(once([small]))
        tier.append(once(paths))
    return {"version": version, **split(startup, tier, len(paths))}


def time_zxing_cpp(paths, runs, symbology="code128"):
    """Times zxing-cpp in this process; see the module's docstring."""
    adapters.format_name("zxing-cpp", symbology)
    try:
        import zxingcpp
    except ImportError as e:
        raise adapters.Unavailable("zxing-cpp is not installed") from e
    images = [pgm.read(p) for p in paths]

    def python(code):
        start = time.perf_counter()
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=False)
        seconds = time.perf_counter() - start
        if done.returncode != 0:
            # Isolated (-I), Python ignores PYTHONPATH and the user's site
            # packages, which may be where this process found zxing-cpp.
            raise adapters.Unavailable(
                f"python -I -c '{code}' exited {done.returncode}, so zxing-cpp's start-up cannot"
                f" be timed: {done.stderr.strip()}"
            )
        return seconds

    def read():
        start = time.perf_counter()
        for image in images:
            adapters._zxing_read(zxingcpp, image, symbology)
        return time.perf_counter() - start

    # Warm-up: not counted.
    python("pass")
    python("import zxingcpp")
    read()
    bare, imported, reads = [], [], []
    for _ in range(runs):
        bare.append(python("pass"))
        imported.append(python("import zxingcpp"))
        reads.append(read())
    startup = max(statistics.median(imported) - statistics.median(bare), 0.0)
    tier = statistics.median(reads)
    return {
        "version": "zxing-cpp " + importlib.metadata.version("zxing-cpp"),
        "startup_s": startup,
        "per_image_s": tier / len(paths),
        "tier_s": startup + tier,
        "python_runs": bare,
        "import_runs": imported,
        "read_runs": reads,
    }


def measure(directory, files, readers, runs=RUNS, symbology="code128"):
    """Times READERS on the images FILES in DIRECTORY, RUNS runs each,
    asking each for SYMBOLOGY."""
    paths = [os.path.join(directory, f) for f in files]
    pixels = []
    for path in paths:
        width, height = pgm.size(path)
        pixels.append(width * height)
    smallest = pixels.index(min(pixels))
    report = {
        "symbology": symbology,
        "runs": runs,
        "images": len(paths),
        "median_pixels": statistics.median(pixels),
        "smallest": files[smallest],
        "machine": machine(),
        "load_before": _load(),
        "readers": {},
    }
    for name in readers:
        try:
            if name == "zxing-cpp":
                timed = time_zxing_cpp(paths, runs, symbology)
            else:
                timed = time_process(name, paths, paths[smallest], runs, symbology)
        except adapters.Unavailable as e:
            timed = {"skipped": str(e)}
        report["readers"][name] = timed
    report["load_after"] = _load()
    return report


def machine():
    """What the times were measured on."""
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu": _cpu(),
        "cpus": os.cpu_count(),
        "python": platform.python_version(),
    }


def _cpu():
    """The processor's name, such as "Apple M3", or None."""
    try:
        if sys.platform == "darwin":
            done = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, check=False
            )
            return done.stdout.strip() or None
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                key, _, value = line.partition(":")
                if key.strip() == "model name":
                    return value.strip()
    except OSError:
        pass
    return platform.processor() or None


def _load():
    """The 1, 5 and 15 minute load averages, where the system has them."""
    try:
        return list(os.getloadavg())
    except (AttributeError, OSError):
        return None


def table(report):
    """A plain-text summary of a timing report."""
    load = ""
    if report["load_before"] and report["load_after"]:
        load = f", load {report['load_before'][0]:.1f} before and {report['load_after'][0]:.1f} after"
    lines = [
        f"{report['images']} images of {report['median_pixels']:.0f} pixels (median), {report['symbology']},"
        f" median of {report['runs']} runs{load}",
        f"{'reader':<12} {'start-up':>10} {'per image':>11} {'whole tier':>11}",
    ]
    for name, r in report["readers"].items():
        if "skipped" in r:
            lines.append(f"{name:<12} skipped: {r['skipped']}")
            continue
        lines.append(
            f"{name:<12} {r['startup_s'] * 1e3:>7.2f} ms {r['per_image_s'] * 1e3:>8.3f} ms"
            f" {r['tier_s']:>9.3f} s"
        )
    return "\n".join(lines)
