"""Curves: a sweep run's read rates drawn as SVG.

One panel per swept parameter, read rate (0 to 100%) against its value,
one line per reader, over a band holding each point's 95% Wilson interval:
where bands overlap, the run cannot tell the readers apart. Plain Python,
no plotting library: the usual ones are not on the licence allowlist.
"""

import math
from xml.sax.saxutils import escape

#: Okabe and Ito's colours, told apart with any colour vision, one per
#: reader in the order the report lists them.
COLOURS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9"]

#: A dash pattern per reader too, so that readers scoring the same, whose
#: lines lie on top of each other, can still be told apart.
DASHES = ["none", "7 4", "2 3", "9 3 2 3", "4 4", "1 2"]

#: What each sweep's axis shows, and whether it is drawn on a log scale.
AXES = {
    "module_px": ("Module size (pixels)", True),
    "blur": ("Blur σ (modules)", False),
    "noise": ("Noise σ (grey levels)", False),
    "rotation": ("Rotation (degrees)", False),
}

PANEL_W, PANEL_H = 420, 260
#: Space around a panel's plot area: left, top, right, bottom.
PAD = (56, 34, 16, 46)
COLUMNS = 2
LEGEND_H = 30
FONT = "system-ui, -apple-system, 'Segoe UI', sans-serif"


def _ticks(values):
    """Where to put ticks: every value if few, else about six of them."""
    if len(values) <= 7:
        return values
    step = math.ceil(len(values) / 6)
    picked = values[::step]
    return picked if picked[-1] == values[-1] else picked + [values[-1]]


def _label(value):
    return f"{value:g}"


def _panel(axis, values, readers, x0, y0):
    """One panel's SVG elements, its top-left corner at (`x0`, `y0`)."""
    title, log = AXES.get(axis, (axis, False))
    left, top, right, bottom = PAD
    w, h = PANEL_W - left - right, PANEL_H - top - bottom
    ox, oy = x0 + left, y0 + top
    lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
    f = (lambda v: math.log(v)) if log and lo > 0 else (lambda v: v)
    span = (f(hi) - f(lo)) or 1.0

    def x(v):
        return ox + (f(v) - f(lo)) / span * w

    def y(rate):
        return oy + (1.0 - rate) * h

    out = [f'<text x="{ox}" y="{y0 + 20}" class="title">{escape(title)}</text>']
    for rate in (0.0, 0.25, 0.5, 0.75, 1.0):
        out.append(
            f'<line x1="{ox}" y1="{y(rate):.1f}" x2="{ox + w}" y2="{y(rate):.1f}" class="grid"/>'
        )
        out.append(
            f'<text x="{ox - 6}" y="{y(rate) + 4:.1f}" class="tick" text-anchor="end">'
            f"{rate:.0%}</text>"
        )
    for v in _ticks(values):
        out.append(
            f'<text x="{x(v):.1f}" y="{oy + h + 16}" class="tick" text-anchor="middle">'
            f"{escape(_label(v))}</text>"
        )
    out.append(f'<line x1="{ox}" y1="{oy + h}" x2="{ox + w}" y2="{oy + h}" class="axis"/>')
    for (colour, dash), (name, all_points) in readers:
        # A point without symbols has no rate to draw.
        points = [p for p in all_points if p["rate"] is not None]
        band = [(x(p["value"]), y(p["high"])) for p in points]
        band += [(x(p["value"]), y(p["low"])) for p in reversed(points)]
        out.append(
            f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in band)}" '
            f'fill="{colour}" fill-opacity="0.14" stroke="none"/>'
        )
        line = " ".join(f"{x(p['value']):.1f},{y(p['rate']):.1f}" for p in points)
        out.append(
            f'<polyline points="{line}" fill="none" stroke="{colour}" '
            f'stroke-dasharray="{dash}" class="line">'
        )
        out.append(f"<title>{escape(name)}</title></polyline>")
        for p in points:
            out.append(
                f'<circle cx="{x(p["value"]):.1f}" cy="{y(p["rate"]):.1f}" r="3" '
                f'fill="{colour}"><title>{escape(name)}: {escape(_label(p["value"]))} → '
                f'{p["read"]}/{p["of"]} ({p["rate"]:.0%}; 95%: {p["low"]:.0%}–{p["high"]:.0%})'
                "</title></circle>"
            )
    return out


def svg(report):
    """The curves of a sweep run's `report` (from `barbench run`), as a
    standalone SVG document."""
    sweeps = report.get("sweeps") or {}
    readers = [
        (name, r) for name, r in report["readers"].items() if "skipped" not in r
    ]
    coloured = [
        ((COLOURS[i % len(COLOURS)], DASHES[i % len(DASHES)]), name, r)
        for i, (name, r) in enumerate(readers)
    ]
    rows = math.ceil(len(sweeps) / COLUMNS) if sweeps else 0
    width = PANEL_W * COLUMNS
    height = LEGEND_H + PANEL_H * rows + 40
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="{FONT}">',
        "<style>"
        ".title{font-size:13px;font-weight:600;fill:#1f1f1f}"
        ".tick{font-size:11px;fill:#5f5f5f;font-variant-numeric:tabular-nums}"
        ".legend{font-size:12px;fill:#1f1f1f}"
        ".note{font-size:11px;fill:#5f5f5f}"
        ".grid{stroke:#e4e4e4;stroke-width:1}"
        ".axis{stroke:#9a9a9a;stroke-width:1}"
        ".line{stroke-width:2;stroke-linejoin:round}"
        "</style>",
        f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
    ]
    x = PAD[0]
    for (colour, dash), name, _ in coloured:
        out.append(
            f'<line x1="{x}" y1="12" x2="{x + 24}" y2="12" stroke="{colour}" '
            f'stroke-dasharray="{dash}" class="line"/>'
        )
        out.append(f'<text x="{x + 30}" y="16" class="legend">{escape(name)}</text>')
        x += 30 + 8 * len(name) + 24
    for i, (axis, values) in enumerate(sweeps.items()):
        x0 = PANEL_W * (i % COLUMNS)
        y0 = LEGEND_H + PANEL_H * (i // COLUMNS)
        curves = [(colour, (name, r["score"]["curves"][axis])) for colour, name, r in coloured]
        out.extend(_panel(axis, values, curves, x0, y0))
    baseline = dict(report.get("baseline") or {})
    words = {
        "module_px": "{:g} pixels per module",
        "blur": "blur {:g} modules",
        "noise": "noise {:g} grey levels",
        "tilt": "tilt up to ±{:g}° either way round",
        "jitter": "pitch within ±{:.0%}",
    }
    note = ", ".join(words.get(k, k + " {:g}").format(v) for k, v in baseline.items())
    out.append(
        f'<text x="{PAD[0]}" y="{height - 24}" class="note">'
        "Each sweep moves one parameter; the rest stay at the baseline:</text>"
    )
    out.append(
        f'<text x="{PAD[0]}" y="{height - 8}" class="note">'
        f"{escape(note)}. Bands: 95% Wilson intervals.</text>"
    )
    out.append("</svg>")
    return "\n".join(out) + "\n"
