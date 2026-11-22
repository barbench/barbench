"""barbench's photo mirror: every listed photo, byte for byte, as release
assets of this repository, so a source that moves or goes down stops no
one.

A release `photos-N` holds one uncompressed tar per set. Its members are
the photos at the paths `barbench photos` caches them under, so a tar
unpacks into a cache as it stands, and `_mirror/SET/` holds the set's
credits, the licence texts it needs, and a statement that every photo is
unmodified. `build` writes the tars reproducibly (sorted members, no
owners, no times), so anyone can rebuild a release from the sources and
compare digests.

`mirror.json`, beside this file, names the release and each asset's size
and SHA-256. `fill` takes a set's missing photos from the mirror before
the set's own source is asked, and checks each against the list's digest;
when the mirror fails in any way, it says so and leaves the photos to the
source. BARBENCH_NO_MIRROR set to anything skips the mirror.
"""

import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request

from . import photos

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "mirror.json")
#: The one place assets are fetched from: this repository's releases.
RELEASES = "https://github.com/barbench/barbench/releases/download/"
RELEASE = re.compile(r"photos-[0-9]+")
ASSET = re.compile(r"photos-[a-z0-9-]+\.tar")
#: The largest asset a release may hold (GitHub takes up to 2 GiB).
MAX_ASSET_BYTES = 2 << 30
#: How often an asset is tried: the source is there when the mirror is not,
#: so a mirror that is down costs one retry, not the archives' four.
MIRROR_TRIES = 2
#: What a licence file of a set's source may be called: a plain name, never
#: a path, and never the credits' own.
LICENCE_FILE = re.compile(r"[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)?")
#: Public-domain tags on Commons that cover what a label shows, not
#: necessarily a photograph or scan of it, whose maker may hold rights of
#: their own. The mirror carries these photos and says so.
LABEL_ONLY = ("PD-ineligible", "PD-text", "PD-Switzerland-official")
#: Where each licence's text is, for the licences the credits name.
LEGAL = {
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "CC-BY-3.0": "https://creativecommons.org/licenses/by/3.0/legalcode",
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/legalcode",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/legalcode",
}


def load(path=None):
    """The mirror's release and assets, checked; None when there is none."""
    path = path or CONFIG
    if not os.path.exists(path):
        return None
    with open(path) as f:
        config = json.load(f)
    if not (
        isinstance(config, dict)
        and RELEASE.fullmatch(str(config.get("release")))
        and isinstance(config.get("sets"), dict)
        and set(config) <= {"release", "built_from", "sets"}
    ):
        raise ValueError(f"{path}: not a release and its sets")
    for name, asset in config["sets"].items():
        if not (
            photos.SET_NAME.fullmatch(name)
            and isinstance(asset, dict)
            and ASSET.fullmatch(str(asset.get("file")))
            and isinstance(asset.get("bytes"), int)
            and 0 < asset["bytes"] <= MAX_ASSET_BYTES
            and re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256")))
        ):
            raise ValueError(f"{path}: {name}: not an asset's file, size and SHA-256")
    return config


def member_name(listing, entry):
    """A photo's name in its set's tar: its path in the cache."""
    return photos.cache_path(listing, entry["file"], "")


def warn(listing, what, detail):
    print(f"barbench: the photo mirror {what} for {listing['name']} ({detail}); fetching from the source",
          file=sys.stderr)


def fill(listing, cache, opener=urllib.request.urlopen, config=None):
    """Takes the set's photos that are missing from `cache` from the
    mirror, when it has the set. Never fails: a mirror.json that is not
    right, a mirror that cannot be reached, an asset that is not the one
    named or a photo that is not the listed file is reported, and the
    photos it could not give are left to the set's own source, which
    checks every photo again. A photo listed after the release was made
    is simply not in it."""
    if os.environ.get("BARBENCH_NO_MIRROR"):
        return
    try:
        config = load() if config is None else config
    except (OSError, ValueError) as e:
        warn(listing, "is not set up right", e)
        return
    if not config or listing["name"] not in config["sets"]:
        return
    root = os.path.realpath(cache)
    missing = []
    for entry in listing["images"]:
        path = photos.cache_path(listing, entry["file"], cache)
        # A photo is only ever written inside the cache.
        if not os.path.realpath(path).startswith(root + os.sep):
            warn(listing, "is not used", f"{entry['file']!r} would be written outside the cache")
            return
        if not (os.path.exists(path) and photos._digest(path) == entry["sha256"]):
            missing.append((entry, path))
    if not missing:
        return
    asset = config["sets"][listing["name"]]
    url = RELEASES + config["release"] + "/" + asset["file"]
    part = os.path.join(cache, "_mirror", asset["file"] + ".part")
    absent = []
    try:
        os.makedirs(os.path.dirname(part), exist_ok=True)
        photos._download_to(url, part, asset["bytes"], opener, tries=MIRROR_TRIES)
        if photos._digest(part) != asset["sha256"]:
            raise ValueError("the asset is not the one mirror.json names")
        with tarfile.open(part, "r:") as tar:
            for entry, path in missing:
                try:
                    info = tar.getmember(member_name(listing, entry))
                except KeyError:
                    absent.append(entry["file"])
                    continue
                if not info.isreg() or info.size > photos.MAX_BYTES:
                    raise ValueError(f"{info.name}: not a photo")
                data = tar.extractfile(info).read(photos.MAX_BYTES + 1)
                if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                    raise ValueError(f"{info.name}: not the listed file")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "wb") as f:
                    f.write(data)
    # Whatever went wrong, the source is still there, and every photo is
    # checked again when it is taken from there.
    except Exception as e:  # noqa: BLE001
        warn(listing, "failed", f"{url}: {e}")
    else:
        if absent:
            warn(listing, f"has no {len(absent)} of the photos listed", f"added after {config['release']}")
    finally:
        if os.path.exists(part):
            os.remove(part)


def credits(listing, licence_files=()):
    """The set's credits and licences, as Markdown."""
    lines = [f"# {listing['name']}, in barbench's photo mirror", ""]
    origin = listing.get("source") or f"{listing['repository']} at {listing['commit']}, {listing['path']}"
    lines += [f"Source: {origin}", "", f"Licence: {listing['license']}"]
    for licence in listing["license"].split(" AND "):
        if licence in LEGAL:
            lines.append(f"- {licence}: {LEGAL[licence]}")
    lines += ["", listing["notice"], ""]
    if licence_files:
        lines += [f"The licence files of the source are here as they are there: {', '.join(licence_files)}.", ""]
    if "selection" in listing:
        lines += [f"Selection: {listing['selection']}", ""]
    lines += [
        "Every photo here is the file at its source, byte for byte, unmodified; its SHA-256 is in barbench's",
        f"list `barbench/lists/{listing['name']}.json`, at https://github.com/barbench/barbench.",
        "",
    ]
    per_photo = [e for e in listing["images"] if "author" in e]
    if per_photo:
        lines += ["| Photo | Author | Licence | Page |", "| --- | --- | --- | --- |"]
        for e in per_photo:
            tag = e.get("commons_license")
            mark = " (see below)" if tag in LABEL_ONLY else ""
            licence = f"{e['license']}" + (f" (Commons: {tag})" if tag else "") + mark
            lines.append(f"| {member_name(listing, e)} | {e['author']} | {licence} | {e['page']} |")
        if any(e.get("commons_license") in LABEL_ONLY for e in per_photo):
            lines += [
                "",
                "The photos marked \"see below\" carry a public-domain tag that covers what the label shows; the",
                "photograph or scan itself may carry rights of its own. They are here as Commons publishes them, and",
                "are taken out on a credible request.",
            ]
        lines.append("")
    return "\n".join(lines)


def _tar(path, members):
    """Writes `members` (name, bytes) to an uncompressed tar, sorted, with
    no owners or times, so the same members always make the same file."""
    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name, data in sorted(members):
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(data), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            tar.addfile(info, io.BytesIO(data))


def build(out_dir, cache, release, listings=None, opener=urllib.request.urlopen, built_from=None):
    """Writes release `release`'s assets to `out_dir`, one tar per set, and
    the `mirror.json` that names them, with the barbench commit that built
    them, since the credits are written from that commit's lists. Photos
    missing from `cache` come from each set's source, never the mirror.
    Returns that configuration."""
    if not RELEASE.fullmatch(release):
        raise ValueError(f"{release!r} is not a release name like photos-1")
    listings = photos.load_lists() if listings is None else listings
    os.makedirs(out_dir, exist_ok=True)
    config = {"release": release, "built_from": built_from, "sets": {}}
    for listing in listings:
        paths = photos.fetch(listing, cache, opener, use_mirror=False)
        members = []
        for entry, path in zip(listing["images"], paths):
            with open(path, "rb") as f:
                members.append((member_name(listing, entry), f.read()))
        folder = f"_mirror/{listing['name']}/"
        files = listing.get("licence_files", {})
        if files and "repository" not in listing:
            raise ValueError(f"{listing['name']}: licence files come from a repository, which it does not name")
        for name, digest in sorted(files.items()):
            if not LICENCE_FILE.fullmatch(name) or name == "CREDITS.md":
                raise ValueError(f"{listing['name']}: {name!r} is not a licence file's name")
            url = listing["repository"].replace("github.com", "raw.githubusercontent.com") + f"/{listing['commit']}/{name}"
            data = photos._download(url, opener)
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError(f"{listing['name']}: {name} is not the listed file")
            members.append((folder + name, data))
        members.append((folder + "CREDITS.md", credits(listing, files).encode("utf-8")))
        asset = f"photos-{listing['name']}.tar"
        path = os.path.join(out_dir, asset)
        _tar(path, members)
        config["sets"][listing["name"]] = {"file": asset, "bytes": os.path.getsize(path), "sha256": photos._digest(path)}
    with open(os.path.join(out_dir, "mirror.json"), "w") as f:
        json.dump(config, f, indent=1)
        f.write("\n")
    return config
