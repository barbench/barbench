"""Scoring: what each reader got right, wrong and missed."""


def score(manifest, results):
    """Scores one reader's `results` (one per manifest image, in order).

    Per image: `read` if the expected text was among the outputs; `misread`
    if any output is not the expected text (on a negative, any output at
    all); an error counts as reading nothing. Totals, and read rates
    broken down by each grid parameter.
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
    return {
        "positives": len(positives),
        "read": read,
        "read_rate": read / len(positives) if positives else 0.0,
        "misread": misread,
        "negatives": len(negatives),
        "false_positive": false_positive,
        "errors": errors,
        "by": by,
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
    return "\n".join(lines)
