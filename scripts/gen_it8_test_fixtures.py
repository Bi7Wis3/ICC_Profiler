"""Render a synthetic 'scan' TIFF of a generic IT8.7/2 target that matches
a given .cht's own box geometry, plus a matching CGATS .cie reference
file, so scanin can actually recognize and read it.

Usage: python scripts/gen_it8_test_fixtures.py <cht_file> <out.tif> <out.cie>

For test-fixture generation when no real physical IT8 target/scanner is
available -- e.g. against shared/it8_references/it8.cht, ArgyllCMS's
bundled generic IT8.7/2 template. The colors are real values lifted from
the .cht's own EXPECTED XYZ section (itself a generic published IT8.7/2
reference baked into ArgyllCMS), with plausible placeholder values for
the handful of grid boxes that have no EXPECTED entry (columns 20-22 in
rows A-H, which the real Wolf-Faust layout leaves unpatched).

Verified against a real installed ArgyllCMS 3.1.0 `scanin`: without the
extra frame border and fine registration lines added below, scanin's
edge-detection correlation came out too weak to match (cc ~0.35); with
them, it matches essentially perfectly (cc ~1.06) and reads all patches.
"""
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw

SCALE = 4.0  # px per .cht unit
MARGIN = 25.0  # extra blank units around the chart, like a real scan border


def xyz_to_srgb(X, Y, Z):
    X, Y, Z = X / 100.0, Y / 100.0, Z / 100.0
    r = X * 3.2406 + Y * -1.5372 + Z * -0.4986
    g = X * -0.9689 + Y * 1.8758 + Z * 0.0415
    b = X * 0.0557 + Y * -0.2040 + Z * 1.0570

    def enc(c):
        c = max(0.0, min(1.0, c))
        c = 1.055 * (c ** (1 / 2.4)) - 0.055 if c > 0.0031308 else 12.92 * c
        return max(0, min(255, round(c * 255)))

    return (enc(r), enc(g), enc(b))


def parse_cht(path: Path):
    text = path.read_text()
    boxes = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 11 and parts[0] in ("F", "D", "X", "Y"):
            boxes.append(parts)

    expected = {}
    m = re.search(r"EXPECTED XYZ (\d+)\n(.*?)(?:\n\s*\n|\Z)", text, re.S)
    for line in m.group(2).splitlines():
        line = line.strip()
        if not line:
            continue
        label, x, y, z = line.split()
        expected[label] = (float(x), float(y), float(z))

    def parse_tick_list(keyword):
        m = re.search(rf"{keyword} (\d+)\n(.*?)(?:\n\s*\n|\Z)", text, re.S)
        ticks = []
        for line in m.group(2).splitlines():
            line = line.strip()
            if not line:
                continue
            pos, strength, crossing = line.split()
            ticks.append((float(pos), float(strength), float(crossing)))
        return ticks

    xlist = parse_tick_list("XLIST")
    ylist = parse_tick_list("YLIST")
    return boxes, expected, xlist, ylist


def expand_boxes(boxes):
    """Return {label: (x, y, w, h)} for every X/Y sample box (skip F/D)."""
    patches = {}

    def label_range(start, end):
        if start.isdigit():
            width = len(start)
            return [str(n).zfill(width) for n in range(int(start), int(end) + 1)]
        if len(start) == 1 and len(end) == 1 and start.isalpha():
            return [chr(c) for c in range(ord(start), ord(end) + 1)]
        # Alphanumeric prefix + numeric suffix, e.g. GS00..GS23.
        m_start = re.match(r"([A-Za-z]*)(\d+)$", start)
        m_end = re.match(r"([A-Za-z]*)(\d+)$", end)
        prefix = m_start.group(1)
        width = len(m_start.group(2))
        return [
            f"{prefix}{n:0{width}d}"
            for n in range(int(m_start.group(2)), int(m_end.group(2)) + 1)
        ]

    for kl, lxs, lxe, lys, lye, w, h, xo, yo, xi, yi in boxes:
        if kl not in ("X", "Y"):
            continue
        w, h, xo, yo, xi, yi = map(float, (w, h, xo, yo, xi, yi))
        xlabels = [None] if lxs == "_" else label_range(lxs, lxe)
        ylabels = [None] if lys == "_" else label_range(lys, lye)
        for yi_idx, ylab in enumerate(ylabels):
            for xi_idx, xlab in enumerate(xlabels):
                if kl == "Y":
                    label = (ylab or "") + (xlab or "")
                else:
                    label = (xlab or "") + (ylab or "")
                x = xo + xi_idx * xi
                y = yo + yi_idx * yi
                patches[label] = (x, y, w, h)
    return patches


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    cht_path, out_tif, out_cie = (Path(p) for p in sys.argv[1:4])

    boxes, expected, xlist, ylist = parse_cht(cht_path)
    patches = expand_boxes(boxes)

    # Fill in placeholder XYZ for grid boxes with no EXPECTED entry (e.g.
    # the unpatched columns 20-22 in rows A-H): reuse that row's last known
    # column value. Fine for a synthetic test fixture -- recognition is
    # purely geometric, these colors never feed a real profile.
    for label in patches:
        if label in expected or label.startswith("GS"):
            continue
        row = label[0]
        col = int(label[1:])
        fallback = None
        for c in range(col - 1, 0, -1):
            cand = f"{row}{c}"
            if cand in expected:
                fallback = expected[cand]
                break
        expected[label] = fallback or (40.0, 40.0, 40.0)

    max_x = max(x + w for x, y, w, h in patches.values())
    max_y = max(y + h for x, y, w, h in patches.values())
    width_px = int((max_x + 2 * MARGIN) * SCALE)
    height_px = int((max_y + 2 * MARGIN) * SCALE)

    img = Image.new("RGB", (width_px, height_px), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    for label, (x, y, w, h) in patches.items():
        X, Y, Z = expected[label]
        color = xyz_to_srgb(X, Y, Z)
        x0 = int((x + MARGIN) * SCALE)
        y0 = int((y + MARGIN) * SCALE)
        x1 = int((x + w + MARGIN) * SCALE)
        y1 = int((y + h + MARGIN) * SCALE)
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], fill=color)

    # The real reference target has extra visible features our flat patch
    # grid doesn't (an outer frame, fine registration marks) -- scanin's
    # recognition cross-correlates detected edges against the .cht's own
    # XLIST/YLIST tick positions, so fill in any reference tick that isn't
    # already produced naturally by a patch boundary with an explicit thin
    # line, otherwise correlation comes out too weak to match.
    boundary_xs = sorted({x for x, y, w, h in patches.values()} | {x + w for x, y, w, h in patches.values()})
    boundary_ys = sorted({y for x, y, w, h in patches.values()} | {y + h for x, y, w, h in patches.values()})

    def nearest_gap(value, boundaries):
        return min(abs(value - b) for b in boundaries)

    LINE_THICK = 2  # units
    for pos, strength, _crossing in xlist:
        if nearest_gap(pos, boundary_xs) > 1.5:
            x0 = int((pos - LINE_THICK / 2 + MARGIN) * SCALE)
            x1 = int((pos + LINE_THICK / 2 + MARGIN) * SCALE)
            draw.rectangle([x0, 0, x1, height_px - 1], fill=(20, 20, 20))
    for pos, strength, _crossing in ylist:
        if nearest_gap(pos, boundary_ys) > 1.5:
            y0 = int((pos - LINE_THICK / 2 + MARGIN) * SCALE)
            y1 = int((pos + LINE_THICK / 2 + MARGIN) * SCALE)
            draw.rectangle([0, y0, width_px - 1, y1], fill=(20, 20, 20))

    # Outer frame border, matching the fiducial/overall-bounds box.
    fx0 = int(MARGIN * SCALE)
    fy0 = int(MARGIN * SCALE)
    fx1 = int((max_x + MARGIN) * SCALE)
    fy1 = int((max_y + MARGIN) * SCALE)
    draw.rectangle([fx0, fy0, fx1, fy1], outline=(20, 20, 20), width=2)

    out_tif.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_tif, format="TIFF")

    out_cie.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "IT8.7/2",
        'ORIGINATOR "ICC_profiler test-fixture generator"',
        'DESCRIPTOR "Synthetic generic IT8.7/2 reference (from ArgyllCMS it8.cht EXPECTED XYZ)"',
        "",
        "NUMBER_OF_FIELDS 4",
        "BEGIN_DATA_FORMAT",
        "SAMPLE_ID XYZ_X XYZ_Y XYZ_Z",
        "END_DATA_FORMAT",
        "",
        f"NUMBER_OF_SETS {len(patches)}",
        "BEGIN_DATA",
    ]
    for label in patches:
        X, Y, Z = expected[label]
        lines.append(f"{label} {X:.3f} {Y:.3f} {Z:.3f}")
    lines.append("END_DATA")
    out_cie.write_text("\n".join(lines) + "\n")

    print(f"Wrote {out_tif} ({width_px}x{height_px}px), {len(patches)} patches")
    print(f"Wrote {out_cie}")


if __name__ == "__main__":
    main()
