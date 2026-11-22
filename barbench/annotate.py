"""Views for placing a symbol's corners by eye, and for checking them.

A symbol's corners (see "What each photo shows" in the README) are placed
by a person looking at the photo, never taken from a reader, whose corners
would favour that reader in any score. These commands draw what that
person needs, as PNGs to open in any viewer:

- `overview`: the whole photo with a grid labelled in its own pixels.
- `zoom`: a square round one point, magnified, with a finer grid.
- `corners`: magnified squares round four proposed corners, each marked,
  laid out start-top | stop-top over start-bottom | stop-bottom.
- `overlay`: the whole photo with the quadrilateral drawn, its first edge
  (start-top to stop-top) green.
- `sheet`: for a listed set, a row per symbol with corners: the photo with
  its quadrilateral and the four corners magnified, in the list's order
  (start-top, stop-top, stop-bottom, start-bottom), to check a list.

Coordinates are the photo's stored pixels, as barbench decodes them: x
right, y down, (0, 0) the top-left corner of the top-left pixel. Views
sample the nearest pixel, so a magnified edge shows exactly where it
falls. Grid labels use a built-in digit font, so no imaging library is
needed (see jpeg.py for why there is none).
"""

import hashlib
import math
import struct
import zlib

from . import jpeg, photos

#: Digits and a minus sign, three pixels wide and five high, row by row.
FONT = {
    "0": "111101101101111",
    "1": "010110010010111",
    "2": "111001111100111",
    "3": "111001111001111",
    "4": "101101111001001",
    "5": "111100111001111",
    "6": "111100111101111",
    "7": "111001010010010",
    "8": "111101111101111",
    "9": "111101111001111",
    "-": "000000111000000",
}
GRID, LABEL, MARK, FIRST = (0, 160, 255), (255, 0, 0), (255, 0, 0), (0, 200, 0)
#: The longest side of a whole-photo view, and the side of a magnified one.
VIEW, TILE = 1100, 400


def load_photo(name, cache):
    """The luma of a listed photo, named `SET/FILE` as in the lists, from
    the cache `barbench photos` filled. Refuses a photo that is not listed,
    not cached, or not the listed file."""
    listing, entry = find(name)
    path = photos.cache_path(listing, entry["file"], cache)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        raise ValueError(f"{name}: not in {cache}; run `barbench photos` with this cache first") from None
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise ValueError(f"{name}: the cached file is not the listed one")
    return jpeg.decode_jpeg(data) if entry["file"].endswith(".jpg") else photos.decode_png(data)


def find(name):
    """The listing and entry of a photo named `SET/FILE`."""
    set_name, _, file = name.partition("/")
    (listing,) = photos.load_lists([set_name], split=None)
    for entry in listing["images"]:
        if entry["file"] == file:
            return listing, entry
    raise ValueError(f"{name}: no such listed photo (name it SET/FILE, as in the lists)")


def view(img, x0, y0, x1, y1, width, height):
    """An RGB view of `img` over [x0, x1) x [y0, y1), nearest pixel, grey
    outside the photo."""
    import numpy as np

    xs = x0 + (np.arange(width) + 0.5) * (x1 - x0) / width
    ys = y0 + (np.arange(height) + 0.5) * (y1 - y0) / height
    xi, yi = np.floor(xs).astype(int), np.floor(ys).astype(int)
    h, w = img.shape
    out = np.full((height, width), 128, np.uint8)
    okx, oky = (xi >= 0) & (xi < w), (yi >= 0) & (yi < h)
    out[np.ix_(oky, okx)] = img[np.ix_(yi[oky], xi[okx])]
    return np.repeat(out[:, :, None], 3, axis=2)


def text(rgb, x, y, s, scale=2, colour=LABEL):
    h, w, _ = rgb.shape
    for k, ch in enumerate(s):
        for i, bit in enumerate(FONT.get(ch, "")):
            if bit == "1":
                top, left = y + (i // 3) * scale, x + (k * 4 + i % 3) * scale
                rgb[max(0, top) : max(0, min(h, top + scale)), max(0, left) : max(0, min(w, left + scale))] = colour


def step(span, lines):
    """A round grid spacing that gives about `lines` lines over `span`."""
    for k in range(9):
        for m in (1, 2, 5):
            if m * 10**k >= span / lines:
                return m * 10**k
    return 10**9


def grid(rgb, x0, y0, x1, y1, spacing):
    """Grid lines at multiples of `spacing` photo pixels, each labelled."""
    h, w, _ = rgb.shape
    for g in range(math.ceil(x0 / spacing) * spacing, math.floor(x1) + 1, spacing):
        px = int((g - x0) * w / (x1 - x0))
        if 0 <= px < w:
            rgb[:, px] = GRID
            text(rgb, px + 2, 2, str(g))
    for g in range(math.ceil(y0 / spacing) * spacing, math.floor(y1) + 1, spacing):
        py = int((g - y0) * h / (y1 - y0))
        if 0 <= py < h:
            rgb[py, :] = GRID
            text(rgb, 2, py + 2, str(g))


def cross(rgb, px, py, colour=MARK, r=8):
    h, w, _ = rgb.shape
    for d in range(-r, r + 1):
        if 0 <= py < h and 0 <= px + d < w:
            rgb[py, px + d] = colour
        if 0 <= px < w and 0 <= py + d < h:
            rgb[py + d, px] = colour


def line(rgb, a, b, colour):
    h, w, _ = rgb.shape
    n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1]))) + 1
    for i in range(n + 1):
        t = i / n
        x, y = int(a[0] + t * (b[0] - a[0])), int(a[1] + t * (b[1] - a[1]))
        if 0 <= y < h and 0 <= x < w:
            rgb[y, x] = colour


def overview(img):
    h, w = img.shape
    scale = min(1.0, VIEW / max(w, h))
    rgb = view(img, 0, 0, w, h, max(1, int(w * scale)), max(1, int(h * scale)))
    grid(rgb, 0, 0, w, h, step(max(w, h), 16))
    return rgb


def check_point(img, x, y, what):
    """Refuses a point outside the photo (its edges included), or not a
    number: a view of nothing would help no one, and a huge one would take
    as long to draw as its size."""
    h, w = img.shape
    if not (0 <= x <= w and 0 <= y <= h):
        raise ValueError(f"{what} ({x}, {y}) is not in the photo's {w}x{h} pixels")


def check_radius(img, r):
    """Refuses a magnified square smaller than a pixel or larger than the
    photo."""
    if not 1 <= r <= max(img.shape):
        raise ValueError(f"a half-width of {r} is not between 1 and the photo's {max(img.shape)} pixels")


def zoom(img, x, y, r):
    check_point(img, x, y, "the centre")
    check_radius(img, r)
    rgb = view(img, x - r, y - r, x + r, y + r, 600, 600)
    grid(rgb, x - r, y - r, x + r, y + r, step(2 * r, 14))
    cross(rgb, 300, 300, FIRST)
    return rgb


def half_width(quad):
    """A magnified square's half-width that shows a few modules round a
    corner, whatever the symbol's size."""
    return max(8.0, max(math.dist(quad[0], quad[1]), math.dist(quad[1], quad[2])) / 12)


def corners(img, quad, r=None):
    import numpy as np

    r = min(half_width(quad), max(img.shape)) if r is None else r
    check_radius(img, r)
    tiles = []
    for x, y in quad:
        tile = view(img, x - r, y - r, x + r, y + r, TILE, TILE)
        grid(tile, x - r, y - r, x + r, y + r, step(2 * r, 14))
        cross(tile, TILE // 2, TILE // 2)
        tiles.append(tile)
    gap = np.full((TILE, 6, 3), 255, np.uint8)
    top = np.concatenate([tiles[0], gap, tiles[1]], axis=1)
    bottom = np.concatenate([tiles[3], gap, tiles[2]], axis=1)
    return np.concatenate([top, np.full((6, top.shape[1], 3), 255, np.uint8), bottom], axis=0)


def overlay(img, quad, longest=VIEW):
    h, w = img.shape
    scale = min(1.0, longest / max(w, h))
    rgb = view(img, 0, 0, w, h, max(1, int(w * scale)), max(1, int(h * scale)))
    points = [(x * scale, y * scale) for x, y in quad]
    for i in range(4):
        line(rgb, points[i], points[(i + 1) % 4], FIRST if i == 0 else MARK)
    cross(rgb, int(points[0][0]), int(points[0][1]), FIRST)
    return rgb


def sheet_rows(set_name, cache):
    """A row per symbol with corners in the listed set: the photo with its
    quadrilateral, then the four corners magnified, each marked."""
    import numpy as np

    side = 200
    rows = []
    for listing in photos.load_lists([set_name], split=None):
        for entry in listing["images"]:
            for symbol in entry["symbols"]:
                if "corners" not in symbol:
                    continue
                img = load_photo(f"{set_name}/{entry['file']}", cache)
                quad = symbol["corners"]
                small = overlay(img, quad, side)
                first = np.full((side, side, 3), 255, np.uint8)
                first[: small.shape[0], : small.shape[1]] = small
                r = half_width(quad) * 0.75
                tiles = [first]
                for x, y in quad:
                    tile = view(img, x - r, y - r, x + r, y + r, side, side)
                    cross(tile, side // 2, side // 2)
                    tiles.append(tile)
                gap = np.full((side, 4, 3), 255, np.uint8)
                row = np.concatenate([part for t in tiles for part in (t, gap)], axis=1)
                text(row, 2, side - 12, str(len(rows)), 2, (255, 0, 255))
                rows.append((entry["file"], row))
    if not rows:
        raise ValueError(f"{set_name}: no listed symbol has corners")
    return rows


def write_png(path, rgb):
    """An 8-bit RGB PNG, written with zlib alone."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\0" + rgb[y].tobytes() for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def write_sheets(rows, prefix, per=6):
    """Writes `rows` to PREFIX-00.png, PREFIX-01.png, ..., `per` a page.
    Returns the pages' paths and the files on each."""
    import numpy as np

    pages = []
    for k in range(0, len(rows), per):
        chunk = rows[k : k + per]
        width = max(r.shape[1] for _, r in chunk)
        parts = []
        for _, r in chunk:
            padded = np.full((r.shape[0] + 4, width, 3), 255, np.uint8)
            padded[: r.shape[0], : r.shape[1]] = r
            parts.append(padded)
        path = f"{prefix}-{k // per:02d}.png"
        write_png(path, np.concatenate(parts, axis=0))
        pages.append((path, [f for f, _ in chunk]))
    return pages


def parse_quad(arg, img=None):
    """Four corners written `x,y;x,y;x,y;x,y`, held to the rules a list's
    corners are (see photos.check_corners) and, given the photo, inside
    it, as `barbench photos` will require."""
    try:
        quad = [[float(v) for v in point.split(",")] for point in arg.split(";")]
    except ValueError:
        quad = []
    if len(quad) != 4 or any(len(p) != 2 for p in quad):
        raise ValueError(f"{arg!r}: corners are four points written x,y;x,y;x,y;x,y")
    photos.check_corners(quad, "the corners")
    if img is not None:
        for x, y in quad:
            check_point(img, x, y, "a corner")
    return quad
