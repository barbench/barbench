"""Command line: build a corpus, run readers on it, report.

    python -m barbench corpus DIR [--seed N] [--per-cell N]
    python -m barbench sweep DIR [--seed N] [--per-point N]
    python -m barbench photos DIR [--cache DIR] [--sets a,b]
    python -m barbench run DIR [--readers a,b] [--out results.json]
        [--require a,b] [--no-misreads a,b] [--min-read a=0.5,b=0.4]
    python -m barbench plot REPORT --out curves.svg
"""

import argparse
import json
import os
import sys

from . import __version__, adapters, corpus, photos, plot, score


def main(argv=None):
    parser = argparse.ArgumentParser(prog="barbench")
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("corpus", help="write the synthetic tier to DIR")
    make.add_argument("dir")
    make.add_argument("--seed", type=int, default=1)
    make.add_argument("--per-cell", type=int, default=2, help="images per grid cell")
    make.add_argument("--negatives", type=int, default=8)
    sweep = sub.add_parser("sweep", help="write the sweep tier, for curves, to DIR")
    sweep.add_argument("dir")
    sweep.add_argument("--seed", type=int, default=1)
    sweep.add_argument("--per-point", type=int, default=20, help="images per point of a sweep")
    draw = sub.add_parser("plot", help="draw a sweep run's curves as SVG")
    draw.add_argument("report", help="the JSON report of `run` on a sweep tier")
    draw.add_argument("--out", required=True, help="where to write the SVG")
    shots = sub.add_parser("photos", help="write the photo tier to DIR, downloading as needed")
    shots.add_argument("dir")
    shots.add_argument("--cache", help="where downloads are kept (default: DIR-cache)")
    shots.add_argument("--sets", help="comma-separated photo sets (default: every list in bench/photos)")
    run = sub.add_parser("run", help="run readers on the corpus in DIR")
    run.add_argument("dir")
    run.add_argument("--readers", default=",".join(adapters.ADAPTERS))
    run.add_argument("--out", help="write the JSON report here")
    run.add_argument(
        "--no-misreads",
        default="",
        help="comma-separated readers that must have no misreads, false positives or errors",
    )
    run.add_argument(
        "--min-read",
        default="",
        help="comma-separated READER=RATE: the least read rate each must reach",
    )
    run.add_argument(
        "--require",
        default="",
        help="comma-separated readers that must be available (else exit 1)",
    )
    args = parser.parse_args(argv)

    if args.command == "photos":
        cache = args.cache or args.dir.rstrip("/") + "-cache"
        sets = [s for s in args.sets.split(",") if s] if args.sets else None
        try:
            listings = photos.load_lists(sets)
        except ValueError as e:
            parser.error(str(e))
        manifest = photos.generate(args.dir, cache, listings)
        print(f"wrote {len(manifest['images'])} photos to {args.dir}")
        return 0

    if args.command == "sweep":
        if args.per_point < 1:
            parser.error("--per-point must be at least 1")
        manifest = corpus.generate_sweep(args.dir, args.seed, args.per_point)
        print(f"wrote {len(manifest['images'])} images to {args.dir}")
        return 0

    if args.command == "plot":
        try:
            with open(args.report, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, ValueError) as e:
            parser.error(f"cannot read {args.report}: {e}")
        if not isinstance(report, dict) or not report.get("sweeps"):
            parser.error(f"{args.report} is not a report on a sweep tier")
        # The SVG holds σ, ±, ° and the like: UTF-8, as XML assumes,
        # whatever the platform's default.
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(plot.svg(report))
        print(f"wrote {args.out}")
        return 0

    if args.command == "corpus":
        manifest = corpus.generate(args.dir, args.seed, args.per_cell, args.negatives)
        print(f"wrote {len(manifest['images'])} images to {args.dir}")
        return 0

    with open(os.path.join(args.dir, "manifest.json")) as f:
        manifest = json.load(f)
    paths = [os.path.join(args.dir, e["file"]) for e in manifest["images"]]
    report = {
        "barbench": __version__,
        "tier": manifest["tier"],
        "seed": manifest["seed"],
        "encoders": manifest["encoders"],
        "photos": manifest.get("photos"),
        "grid": manifest["grid"],
        "baseline": manifest.get("baseline"),
        "sweeps": manifest.get("sweeps"),
        "readers": {},
    }
    readers = [r for r in args.readers.split(",") if r]
    required = {r for r in args.require.split(",") if r}
    strict = {r for r in args.no_misreads.split(",") if r}
    floors = {}
    for item in [i for i in args.min_read.split(",") if i]:
        name, _, rate = item.partition("=")
        try:
            floors[name] = float(rate)
        except ValueError:
            parser.error(f"--min-read needs READER=RATE, not '{item}'")
        # NaN would compare false and pass every run.
        if not 0.0 <= floors[name] <= 1.0:
            parser.error(f"--min-read rates are between 0 and 1, not '{rate}'")
    # A gate on a reader that does not run would pass without checking.
    for name in readers:
        if name not in adapters.ADAPTERS:
            parser.error(f"unknown reader '{name}'")
    for name in required | strict | set(floors):
        if name not in readers:
            parser.error(f"'{name}' is gated but not among --readers")
    status = 0
    for name in readers:
        adapter = adapters.ADAPTERS[name]
        try:
            results, seconds, version = adapter(paths)
        except adapters.Unavailable as e:
            report["readers"][name] = {"skipped": str(e)}
            if name in required | strict | set(floors):
                print(f"barbench: required reader {name} unavailable: {e}", file=sys.stderr)
                status = 1
            continue
        scored = score.score(manifest, results)
        if name in strict and (scored["misread"] or scored["false_positive"] or scored["errors"]):
            print(
                f"barbench: {name} misread {scored['misread']} images, read codes in"
                f" {scored['false_positive']} images that hold none, and failed on"
                f" {scored['errors']}",
                file=sys.stderr,
            )
            status = 1
        if name in floors and scored["read_rate"] < floors[name]:
            print(
                f"barbench: {name} read {scored['read_rate']:.0%}, under its floor of"
                f" {floors[name]:.0%}",
                file=sys.stderr,
            )
            status = 1
        report["readers"][name] = {
            "version": version,
            "seconds": seconds,
            "score": scored,
            "results": [{"file": e["file"], **r} for e, r in zip(manifest["images"], results)],
        }
    if args.out:
        with open(args.out, "w") as f:
            json.dump(report, f, indent=1)
            f.write("\n")
    print(score.table(report))
    return status


if __name__ == "__main__":
    sys.exit(main())
