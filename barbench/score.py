"""Scoring: what each reader got right, wrong and missed."""

import math

#: The normal quantile for a two-sided 95% interval.
Z95 = 1.959963984540054


def wilson(read, of, z=Z95):
    """The Wilson score interval for `read` successes in `of` trials, at
    the confidence `z` stands for (95% by default): `(low, high)`, both in
    [0, 1]. Unlike the plain `rate ± z·sqrt(rate·(1-rate)/of)`, it stays
    inside [0, 1] and is not empty at 0 or 1 read, where a benchmark's
    curves start and end."""
    if of <= 0:
        return (0.0, 1.0)
    p = read / of
    centre = p + z * z / (2 * of)
    spread = z * math.sqrt(p * (1 - p) / of + z * z / (4 * of * of))
    scale = 1 + z * z / of
    # All or none read: that end is exact, not left to rounding.
    low = 0.0 if read <= 0 else max(0.0, (centre - spread) / scale)
    high = 1.0 if read >= of else min(1.0, (centre + spread) / scale)
    return (low, high)


def score(manifest, results):
    """Scores one reader's `results` (one per manifest image, in order).

    Per image: `read` if the expected text was among the outputs; `misread`
    if any output is not the expected text (on a negative, any output at
    all); an error counts as reading nothing. Totals, read rates broken
    down by each grid parameter, and, for a sweep tier, a curve per swept
    parameter: each point's read rate with its 95% Wilson interval.
    """
    images = manifest["images"]
    if len(images) != len(results):
        raise ValueError(f"{len(results)} results for {len(images)} images")
    positives = [(e, r) for e, r in zip(images, results) if e["expected"] is not None]
    negatives = [(e, r) for e, r in zip(images, results) if e["expected"] is None]
    read = sum(e["expected"] in r["texts"] for e, r in positives)
    misread = sum(any(t != e["expected"] for t in r["texts"]) for e, r in positives)
    false_positive = sum(bool(r["texts"]) for _, r in negatives)
    errors = sum("error" in r for r in results)
    by = {}
    for key in manifest["grid"]:
        rows = {}
        for e, r in positives:
            row = rows.setdefault(str(e[key]), [0, 0])
            row[0] += e["expected"] in r["texts"]
            row[1] += 1
        by[key] = {k: {"read": v[0], "of": v[1], "rate": v[0] / v[1]} for k, v in rows.items()}
    curves = {}
    for axis, values in manifest.get("sweeps", {}).items():
        points = []
        for value in values:
            hits = [e["expected"] in r["texts"] for e, r in positives
                    if e.get("sweep") == axis and e.get("point") == value]
            low, high = wilson(sum(hits), len(hits))
            points.append({
                "value": value,
                "read": sum(hits),
                "of": len(hits),
                # No symbols, no rate, as the grid table's "-".
                "rate": sum(hits) / len(hits) if hits else None,
                "low": low,
                "high": high,
            })
        curves[axis] = points
    return {
        "positives": len(positives),
        "read": read,
        "read_rate": read / len(positives) if positives else 0.0,
        "misread": misread,
        "negatives": len(negatives),
        "false_positive": false_positive,
        "errors": errors,
        "by": by,
        "curves": curves,
    }


def table(report):
    """A plain-text summary of a run's report."""
    lines = [f"{'reader':<12} {'read':>10} {'misread':>8} {'false+':>7} {'seconds':>8}"]
    for name, r in report["readers"].items():
        if "skipped" in r:
            lines.append(f"{name:<12} skipped: {r['skipped']}")
            continue
        s = r["score"]
        lines.append(
            f"{name:<12} {s['read']:>4}/{s['positives']:<5}"
            f" {s['misread']:>8} {s['false_positive']:>3}/{s['negatives']:<3}"
            f" {r['seconds']:>8.2f}"
        )
    keys = list(report["grid"])
    for key in keys:
        lines.append("")
        values = list(report["grid"][key])
        lines.append(f"{key:<12} " + " ".join(f"{str(v):>7}" for v in values))
        for name, r in report["readers"].items():
            if "skipped" in r:
                continue
            by = r["score"]["by"][key]
            cells = [by.get(str(v)) for v in values]
            lines.append(
                f"{name:<12} "
                + " ".join(f"{c['rate']:>7.0%}" if c else f"{'-':>7}" for c in cells)
            )
    for axis, values in (report.get("sweeps") or {}).items():
        lines.append("")
        lines.append(f"{axis:<12} " + " ".join(f"{str(v):>6}" for v in values))
        for name, r in report["readers"].items():
            if "skipped" in r:
                continue
            points = r["score"]["curves"][axis]
            lines.append(
                f"{name:<12} "
                + " ".join(
                    f"{p['rate']:>6.0%}" if p["rate"] is not None else f"{'-':>6}"
                    for p in points
                )
            )
    return "\n".join(lines)
