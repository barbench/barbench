"""Slices of the photo library, and what each reader holds out of them.

The library records facts about each photo, never which reader may look at
it: its `group` (the physical label or scene it shows, so near-duplicates
travel together), its `slice`, and the date it was `added`. A photo's
slice is the first hex digit of the SHA-256 of its group name, as UTF-8:

    printf '%s' 'zxing-blackbox/code128-2/005-3379497200006' | sha256sum | cut -c1

The rule is fixed: changing the hash, the digit read or a group's name
would move photos that readers have already declared held out or not.

Whether a photo is fit to measure a reader on depends on that reader:
whether its developers have looked at it. Each reader that takes part
declares, in readers/NAME.json, the slices it holds out and the date from
which it has: its held-out photos are those in its slices added strictly
after that date, so not a photo added the same day. A reader that has looked at every photo listed so far declares from
today; one that never looked can declare from any date before the first
photo was added. barbench records declarations; it cannot check them.

Published comparisons all hold out the same slices, SHARED, so no reader
chooses easy ones, and use only photos added after every compared reader's
declaration, so each is held out for all of them.
A reader's own development runs leave its held-out photos out
(held_out_by).
"""

import datetime
import hashlib
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
READERS = os.path.join(HERE, "readers")

SLICES = "0123456789abcdef"
#: The slices every published comparison holds out.
SHARED = "ef"
#: What a group name looks like: its set, "/", then the label or scene, so
#: it can be checked against a rule rather than chosen for its hash.
GROUP_NAME = re.compile(r"[a-z0-9-]+/[^\s/][^\s]*")


def of_group(group):
    """The slice a group of photos is in."""
    return hashlib.sha256(group.encode("utf-8")).hexdigest()[0]


def parse(spec):
    """The slices a spec names, in order: digits and ranges, comma-separated
    (`e,f`, `0-d`, `0-3,8`), or `all`."""
    if spec == "all":
        return SLICES
    chosen = set()
    for part in spec.split(","):
        low, dash, high = part.partition("-")
        # One digit each: `in` on a string would also accept "ef" or "".
        if len(low) != 1 or low not in SLICES or (dash and (len(high) != 1 or high not in SLICES)):
            raise ValueError(f"{spec!r}: slices are hex digits and ranges, such as e,f or 0-d")
        high = high if dash else low
        if SLICES.index(high) < SLICES.index(low):
            raise ValueError(f"{spec!r}: the range {part} runs backwards")
        chosen.update(SLICES[SLICES.index(low) : SLICES.index(high) + 1])
    return "".join(s for s in SLICES if s in chosen)


def date(value, what):
    """An ISO date `YYYY-MM-DD`, checked."""
    if not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
        raise ValueError(f"{what}: {value!r} is not a date written YYYY-MM-DD")
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{what}: {value!r} is not a date") from None
    return value


def declarations(folder=None):
    """Every reader's declaration, by reader name, checked."""
    folder = folder or READERS
    found = {}
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        if not name.endswith(".json"):
            continue
        with open(os.path.join(folder, name)) as f:
            declared = json.load(f)
        if not isinstance(declared, dict):
            raise ValueError(f"{name}: not a declaration")
        reader = name.removesuffix(".json")
        if declared.get("reader") != reader:
            raise ValueError(f"{name}: declares reader {declared.get('reader')!r}, not {reader!r}")
        held = declared.get("held_out")
        # One digit each: `in` on a string would also accept "ef" or "".
        if not (
            isinstance(held, list)
            and held
            and all(isinstance(s, str) and len(s) == 1 and s in SLICES for s in held)
            and len(set(held)) == len(held)
        ):
            raise ValueError(f"{name}: held_out {held!r} is not a list of slices")
        date(declared.get("since"), name)
        if not isinstance(declared.get("declared_by"), str) or not declared["declared_by"]:
            raise ValueError(f"{name}: says nothing of who declared it")
        found[reader] = declared
    return found


def held_out_by(reader, folder=None):
    """The slices `reader` holds out and the date after which it does, from
    its declaration: what its own runs must leave out."""
    known = declarations(folder)
    if reader not in known:
        raise ValueError(f"no declaration from {reader} (see readers/ and slices.py)")
    return "".join(s for s in SLICES if s in known[reader]["held_out"]), known[reader]["since"]


def held_out_for(readers, folder=None):
    """The slices and the date after which photos are held out for every one
    of `readers`, for a published comparison: SHARED, and the latest of
    their declarations. Refuses a reader without a declaration, or one
    that does not hold out SHARED."""
    if not readers:
        raise ValueError("no readers named")
    known = declarations(folder)
    missing = [r for r in readers if r not in known]
    if missing:
        raise ValueError(f"no declaration from {', '.join(missing)} (see readers/ and slices.py)")
    short = [r for r in readers if not set(SHARED) <= set(known[r]["held_out"])]
    if short:
        raise ValueError(f"{', '.join(short)} do not hold out the shared slices {', '.join(SHARED)}")
    return SHARED, max(known[r]["since"] for r in readers)
