"""Command line: build a corpus, run readers on it, report.

    python -m barbench corpus DIR [--seed N] [--per-cell N]
    python -m barbench sweep DIR [--seed N] [--per-point N]
    python -m barbench photos DIR [--cache DIR] [--sets a,b]
        [--slices e,f] [--added-after YYYY-MM-DD] [--held-out-for a,b] [--dev-for a]
    python -m barbench run DIR [--readers a,b] [--out results.json]
        [--require a,b] [--no-misreads a,b] [--min-read a=0.5,b=0.4]
    python -m barbench time DIR [--readers a,b] [--runs N] [--out timing.json]
    python -m barbench plot REPORT --out curves.svg
    python -m barbench annotate VIEW ... --cache DIR   (views for placing corners; see annotate.py)
    python -m barbench mirror OUT --cache DIR --release photos-N   (a mirror release's assets)

Which photos a reader holds out is its own declaration; see slices.py.
How `time` splits start-up from the cost per image is in timing.py.
"""

import argparse
import json
import os
import sys

from . import __version__, adapters, annotate, corpus, mirror, photos, plot, score, slices, source, timing


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
    shots.add_argument("--sets", help="comma-separated photo sets (default: every list)")
    shots.add_argument("--slices", default="all", help="the slices to write: hex digits and ranges, e.g. 0-d or e,f")
    shots.add_argument("--added-after", help="only photos added after this date, YYYY-MM-DD")
    shots.add_argument(
        "--held-out-for",
        help="comma-separated readers: the photos held out for all of them, as published comparisons use",
    )
    shots.add_argument("--dev-for", help="a reader: every photo it does not hold out, for its own development runs")
    views = sub.add_parser("annotate", help="draw views for placing a symbol's corners by eye")
    kinds = views.add_subparsers(dest="view", required=True)
    for name, extra, about in (
        ("overview", [], "the whole photo, gridded"),
        ("zoom", ["x", "y", "r"], "a square of half-width R round (X, Y)"),
        ("corners", ["quad"], "the four corners x,y;x,y;x,y;x,y magnified"),
        ("overlay", ["quad"], "the whole photo with the quadrilateral drawn"),
    ):
        kind = kinds.add_parser(name, help=about)
        kind.add_argument("photo", help="SET/FILE, as named in the lists")
        for arg in extra:
            kind.add_argument(arg, type=float) if arg in ("x", "y", "r") else kind.add_argument(arg)
        kind.add_argument("out", help="the PNG to write")
        kind.add_argument("--cache", required=True, help="the photo cache `barbench photos` filled")
        if name == "corners":
            kind.add_argument("--radius", type=float, help="half-width of each square, in photo pixels")
    sheets = kinds.add_parser("sheet", help="every listed symbol of a set with its corners, to check them")
    sheets.add_argument("set")
    sheets.add_argument("prefix", help="pages are written as PREFIX-00.png, PREFIX-01.png, ...")
    sheets.add_argument("--cache", required=True, help="the photo cache `barbench photos` filled")
    mirrored = sub.add_parser("mirror", help="write a photo mirror release's assets to OUT, from the sources")
    mirrored.add_argument("out")
    mirrored.add_argument("--cache", required=True, help="where downloads are kept")
    mirrored.add_argument("--release", required=True, help="the release's name, photos-N")
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
    times = sub.add_parser("time", help="time readers on the corpus in DIR: start-up and cost per image")
    times.add_argument("dir")
    times.add_argument("--readers", default=",".join(adapters.ADAPTERS))
    times.add_argument("--runs", type=int, default=timing.RUNS, help="timed runs, of which the median counts")
    times.add_argument("--out", help="write the JSON report here")
    args = parser.parse_args(argv)

    if args.command == "annotate":
        try:
            if args.view == "sheet":
                rows = annotate.sheet_rows(args.set, args.cache)
                for path, files in annotate.write_sheets(rows, args.prefix):
                    print(path, " ".join(files))
                return 0
            img = annotate.load_photo(args.photo, args.cache)
            if args.view == "overview":
                rgb = annotate.overview(img)
            elif args.view == "zoom":
                rgb = annotate.zoom(img, args.x, args.y, args.r)
            elif args.view == "corners":
                rgb = annotate.corners(img, annotate.parse_quad(args.quad, img), args.radius)
            else:
                rgb = annotate.overlay(img, annotate.parse_quad(args.quad, img))
            annotate.write_png(args.out, rgb)
        except (ValueError, OSError) as e:
            parser.error(str(e))
        print(f"{args.photo}: {img.shape[1]}x{img.shape[0]}, wrote {args.out}")
        return 0

    if args.command == "mirror":
        if not mirror.RELEASE.fullmatch(args.release):
            parser.error(f"{args.release!r} is not a release name like photos-1")
        config = mirror.build(args.out, args.cache, args.release, built_from=source.commit())
        for name, asset in config["sets"].items():
            print(f"{asset['file']}: {asset['bytes']} bytes, sha256 {asset['sha256']}")
        print(f"wrote {os.path.join(args.out, 'mirror.json')}")
        return 0

    if args.command in ("corpus", "sweep") and args.seed < 0:
        parser.error(f"seeds are 0 or more, not {args.seed}")

    if args.command == "photos":
        cache = args.cache or args.dir.rstrip("/") + "-cache"
        sets = [s for s in args.sets.split(",") if s] if args.sets else None
        if (args.held_out_for or args.dev_for) and (args.slices != "all" or args.added_after or
                                                    (args.held_out_for and args.dev_for)):
            parser.error("--held-out-for and --dev-for each set the slices and the date themselves")
        try:
            listings = photos.load_lists(sets)
            leave_out = None
            if args.held_out_for:
                chosen, after = slices.held_out_for([r for r in args.held_out_for.split(",") if r])
            else:
                chosen, after = slices.parse(args.slices), args.added_after
                if args.added_after is not None:
                    slices.date(args.added_after, "--added-after")
                if args.dev_for:
                    leave_out = slices.held_out_by(args.dev_for)
        except ValueError as e:
            parser.error(str(e))
        # Only an empty choice is a usage error: a download that fails or a
        # file that is not the listed one is the data's fault, and shows so.
        try:
            manifest = photos.generate(args.dir, cache, listings, chosen=chosen, added_after=after, leave_out=leave_out)
        except photos.NothingChosen as e:
            parser.error(str(e))
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

    if args.command == "time":
        readers = [r for r in args.readers.split(",") if r]
        for name in readers:
            if name not in adapters.ADAPTERS:
                parser.error(f"unknown reader '{name}'")
        if args.runs < 1:
            parser.error("--runs must be at least 1")
        files = [e["file"] for e in manifest["images"]]
        # The cost per image is what the tier adds to a run on one image.
        if len(files) < 2:
            parser.error(f"timing needs two images or more; {args.dir} has {len(files)}")
        report = {
            "barbench": __version__,
            "barbench_commit": source.commit(),
            "tier": manifest["tier"],
            "seed": manifest["seed"],
            **timing.measure(args.dir, files, readers, args.runs),
        }
        if args.out:
            with open(args.out, "w") as f:
                json.dump(report, f, indent=1)
                f.write("\n")
        print(timing.table(report))
        return 0

    paths = [os.path.join(args.dir, e["file"]) for e in manifest["images"]]
    report = {
        "barbench": __version__,
        "barbench_commit": source.commit(),
        "tier": manifest["tier"],
        "seed": manifest["seed"],
        "slices": manifest.get("slices"),
        "added_after": manifest.get("added_after"),
        "left_out": manifest.get("left_out"),
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
