"""Digitise Excel charts pasted as EMF pictures: recover series values from the vector drawing.

An EMF from Excel is vector: the plotted line is a POLYLINE16 record, bars are POLYGON16
records, and every axis tick label is an EXTTEXTOUTW record drawn under a world transform
that carries its position. Fit value <-> device-y from the y-axis labels and date <-> device-x
from the x-axis labels, then read every point of the series polyline. The result is
approximate (sub-pixel on the value axis, nearest month on the date axis) and is labelled so.

No model, no rendering, no network. Works only for vector EMFs (Excel paste); raster EMFs
contain a bitmap and yield nothing.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import struct
from dataclasses import dataclass, field

# EMR record types
HEADER, POLYBEZIER, POLYGON, POLYLINE, POLYLINETO = 1, 2, 3, 4, 5
SAVEDC, RESTOREDC, SETWORLDTRANSFORM, MODIFYWORLDTRANSFORM, SELECTOBJECT = 33, 34, 35, 36, 37
CREATEPEN, DELETEOBJECT, EXTCREATEFONTINDIRECTW, EXTTEXTOUTW = 38, 40, 82, 84
POLYBEZIER16, POLYGON16, POLYLINE16, POLYBEZIERTO16, POLYLINETO16, POLYPOLYLINE16, POLYPOLYGON16 = 85, 86, 87, 88, 89, 90, 91
EXTCREATEPEN = 95
MWT_IDENTITY, MWT_LEFTMULTIPLY, MWT_RIGHTMULTIPLY, MWT_SET = 1, 2, 3, 4

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


@dataclass
class Text:
    text: str
    x: float
    y: float
    height: float  # font height in device units (positive)


@dataclass
class Poly:
    kind: str  # line | polygon
    pts: list[tuple[float, float]]
    color: str | None  # pen colour
    fill: str | None = None  # brush colour (polygons)


@dataclass
class Drawing:
    texts: list[Text] = field(default_factory=list)
    polys: list[Poly] = field(default_factory=list)
    bounds: tuple[int, int, int, int] | None = None


# --------------------------------------------------------------------------- transform tracking

def _mul(a, b):
    """2x3 affine multiply: apply a then b (row-vector convention, as GDI does)."""
    a11, a12, a21, a22, adx, ady = a
    b11, b12, b21, b22, bdx, bdy = b
    return (a11 * b11 + a12 * b21, a11 * b12 + a12 * b22,
            a21 * b11 + a22 * b21, a21 * b12 + a22 * b22,
            adx * b11 + ady * b21 + bdx, adx * b12 + ady * b22 + bdy)


def _apply(m, x, y):
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def parse_emf(data: bytes) -> Drawing:
    dr = Drawing()
    if len(data) < 8 or struct.unpack_from("<I", data, 0)[0] != HEADER:
        return dr
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    xf = ident
    stack: list[tuple] = []
    objects: dict[int, tuple[str, object]] = {}
    pen_color: str | None = None
    brush_color: str | None = None
    font_height = 0.0
    CREATEBRUSHINDIRECT = 39
    BS_NULL = 1
    off, n = 0, len(data)
    while off + 8 <= n:
        rtype, size = struct.unpack_from("<II", data, off)
        if size < 8 or off + size > n:
            break
        body = off + 8
        try:
            if rtype == HEADER:
                dr.bounds = struct.unpack_from("<4i", data, body)
            elif rtype == SAVEDC:
                stack.append((xf, pen_color, font_height, brush_color))
            elif rtype == RESTOREDC:
                if stack:
                    xf, pen_color, font_height, brush_color = stack.pop()
            elif rtype == CREATEBRUSHINDIRECT:
                h, style, col = struct.unpack_from("<III", data, body)
                objects[h] = ("brush", None if style == BS_NULL else "%02X%02X%02X" % (col & 0xFF, (col >> 8) & 0xFF, (col >> 16) & 0xFF))
            elif rtype == SETWORLDTRANSFORM:
                xf = struct.unpack_from("<6f", data, body)
            elif rtype == MODIFYWORLDTRANSFORM:
                m = struct.unpack_from("<6f", data, body)
                mode = struct.unpack_from("<I", data, body + 24)[0]
                if mode == MWT_IDENTITY:
                    xf = ident
                elif mode == MWT_LEFTMULTIPLY:
                    xf = _mul(m, xf)
                elif mode == MWT_RIGHTMULTIPLY:
                    xf = _mul(xf, m)
                elif mode == MWT_SET:
                    xf = m
            elif rtype == CREATEPEN:
                h = struct.unpack_from("<I", data, body)[0]
                col = struct.unpack_from("<I", data, body + 16)[0]
                objects[h] = ("pen", "%02X%02X%02X" % (col & 0xFF, (col >> 8) & 0xFF, (col >> 16) & 0xFF))
            elif rtype == EXTCREATEPEN:
                h = struct.unpack_from("<I", data, body)[0]
                # ihPen, offBmi, cbBmi, offBits, cbBits, then LogPenEx: PenStyle, Width, BrushStyle, ColorRef
                col = struct.unpack_from("<I", data, body + 20 + 12)[0]
                objects[h] = ("pen", "%02X%02X%02X" % (col & 0xFF, (col >> 8) & 0xFF, (col >> 16) & 0xFF))
            elif rtype == EXTCREATEFONTINDIRECTW:
                h = struct.unpack_from("<I", data, body)[0]
                lf_height = struct.unpack_from("<i", data, body + 4)[0]
                objects[h] = ("font", abs(lf_height))
            elif rtype == SELECTOBJECT:
                h = struct.unpack_from("<I", data, body)[0]
                obj = objects.get(h)
                if obj and obj[0] == "pen":
                    pen_color = obj[1]
                elif obj and obj[0] == "font":
                    font_height = float(obj[1])
                elif obj and obj[0] == "brush":
                    brush_color = obj[1]
                elif obj is None and h & 0x80000000:
                    # stock objects: NULL_BRUSH (5), WHITE/GRAY/BLACK brushes 0..4, pens 6..8
                    idx = h & 0x7FFFFFFF
                    if idx == 5:
                        brush_color = None
                    elif idx in (0, 1, 2, 3, 4):
                        brush_color = {0: "FFFFFF", 1: "C0C0C0", 2: "808080", 3: "404040", 4: "000000"}[idx]
            elif rtype == DELETEOBJECT:
                objects.pop(struct.unpack_from("<I", data, body)[0], None)
            elif rtype == EXTTEXTOUTW:
                # Bounds(16) iGraphicsMode(4) exScale(4) eyScale(4) EmrText{ptlReference(8) nChars offString Options Rect offDx}
                rx, ry, nchars, off_string = struct.unpack_from("<iiII", data, body + 28)
                s = data[off + off_string: off + off_string + nchars * 2].decode("utf-16le", "ignore").replace("\x00", "").strip()
                if s:
                    dx, dy = _apply(xf, rx, ry)
                    scale = math.hypot(xf[0], xf[1]) or 1.0
                    dr.texts.append(Text(s, dx, dy, font_height * scale))
            elif rtype in (POLYLINE16, POLYLINETO16, POLYGON16, POLYLINE, POLYLINETO, POLYGON):
                cnt = struct.unpack_from("<I", data, body + 16)[0]
                fmt = "<%dh" % (2 * cnt) if rtype >= POLYBEZIER16 else "<%di" % (2 * cnt)
                raw = struct.unpack_from(fmt, data, body + 20)
                pts = [_apply(xf, raw[i], raw[i + 1]) for i in range(0, 2 * cnt, 2)]
                is_poly = rtype in (POLYGON16, POLYGON)
                dr.polys.append(Poly("polygon" if is_poly else "line", pts, pen_color, brush_color if is_poly else None))
            elif rtype in (POLYPOLYLINE16, POLYPOLYGON16):
                npolys, cnt = struct.unpack_from("<II", data, body + 16)
                counts = struct.unpack_from("<%dI" % npolys, data, body + 24)
                raw = struct.unpack_from("<%dh" % (2 * cnt), data, body + 24 + 4 * npolys)
                pos = 0
                for c in counts:
                    pts = [_apply(xf, raw[2 * i], raw[2 * i + 1]) for i in range(pos, pos + c)]
                    is_poly = rtype == POLYPOLYGON16
                    dr.polys.append(Poly("polygon" if is_poly else "line", pts, pen_color, brush_color if is_poly else None))
                    pos += c
        except struct.error:
            pass
        off += size
    return dr


# --------------------------------------------------------------------------- axis parsing

NUM_RE = re.compile(r"^\(?-?[\d,]*\.?\d+\)?%?$")


def parse_number(s: str) -> float | None:
    t = s.strip().replace(",", "").replace("$", "").replace("€", "").replace("£", "").replace("¥", "")
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    pct = t.endswith("%")
    t = t.rstrip("%")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def parse_date(s: str) -> dt.date | None:
    t = s.strip()
    m = re.fullmatch(r"([A-Za-z]{3})[- ]?(\d{2}|\d{4})", t)
    if m and m.group(1).lower() in MONTHS:
        y = int(m.group(2))
        y = y + (2000 if y < 70 else 1900) if y < 100 else y
        return dt.date(y, MONTHS[m.group(1).lower()], 1)
    m = re.fullmatch(r"(\d{4})", t)
    if m:
        return dt.date(int(m.group(1)), 1, 1)
    m = re.fullmatch(r"Q([1-4])[- ]?(\d{2}|\d{4})", t, re.I)
    if m:
        y = int(m.group(2))
        y = y + (2000 if y < 70 else 1900) if y < 100 else y
        return dt.date(y, 3 * int(m.group(1)) - 2, 1)
    return None


def _months(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def _from_months(m: float) -> str:
    m = int(round(m))
    return dt.date(m // 12, m % 12 + 1, 1).isoformat()[:7]


def _fit(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    """Least squares y = a + b*x; None if degenerate."""
    n = len(xs)
    if n < 2:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _cluster_x(texts: list[Text], tol: float) -> list[list[Text]]:
    """Group texts by similar x (a column of y-axis labels)."""
    groups: list[list[Text]] = []
    for t in sorted(texts, key=lambda t: t.x):
        if groups and abs(groups[-1][-1].x - t.x) <= tol:
            groups[-1].append(t)
        else:
            groups.append([t])
    return groups


def digitise(data: bytes) -> dict | None:
    dr = parse_emf(data)
    if not dr.texts or not dr.polys:
        return None
    width = (dr.bounds[2] - dr.bounds[0]) if dr.bounds else 1000
    # structural lines Excel draws: horizontal gridlines (span most of the width) and short vertical tick marks
    two = [p for p in dr.polys if p.kind == "line" and len(p.pts) == 2]
    hlines = sorted({round(p.pts[0][1], 1) for p in two
                     if abs(p.pts[0][1] - p.pts[1][1]) < 0.5 and abs(p.pts[0][0] - p.pts[1][0]) > width * 0.4})
    vticks = sorted({round(p.pts[0][0], 1) for p in two
                     if abs(p.pts[0][0] - p.pts[1][0]) < 0.5 and 0 < abs(p.pts[0][1] - p.pts[1][1]) < width * 0.03})
    confidence = []

    # y axes: numeric texts forming a vertical column (same x, >= 3 of them)
    numeric = [t for t in dr.texts if parse_number(t.text) is not None]
    y_axes = []
    for grp in _cluster_x(numeric, tol=max(4.0, width * 0.02)):
        if len(grp) < 3:
            continue
        grp = sorted(grp, key=lambda t: t.y)
        vals = [parse_number(t.text) for t in grp]
        # a real axis is monotone with regular steps; a column of data labels or map numbers is not
        steps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        if not (all(s < 0 for s in steps) or all(s > 0 for s in steps)):
            continue
        mean_step = sum(abs(s) for s in steps) / len(steps)
        if mean_step == 0 or max(abs(abs(s) - mean_step) for s in steps) > 0.25 * mean_step:
            continue
        if len(hlines) == len(grp):
            ys = hlines  # labels sit on the gridlines, top to bottom
        else:
            # reference point is the baseline (TA_BASELINE); the visual centre is ~0.3 of the text height above it
            ys = [t.y - 0.3 * t.height for t in grp]
            confidence.append("y labels not matched to gridlines")
        fit = _fit(ys, vals)
        if fit:
            y_axes.append({"labels": [t.text for t in grp], "x": sum(t.x for t in grp) / len(grp), "fit": fit,
                           "pct": any(t.text.strip().endswith("%") for t in grp)})
    if not y_axes:
        return None
    y_axes.sort(key=lambda a: a["x"])
    left, right = y_axes[0], (y_axes[-1] if len(y_axes) > 1 else None)

    def label_centres(row: list[Text]) -> list[float]:
        """x of each label's tick: pair with tick marks when the counts agree, else the constant-offset rule."""
        xs = [t.x for t in row]
        if len(vticks) == len(row):
            return list(vticks)
        if len(vticks) >= 2:
            # labels are a constant offset right of their tick: take the median offset to the nearest tick on the left
            offs = []
            for x in xs:
                lefts = [v for v in vticks if v <= x]
                if lefts:
                    offs.append(x - max(lefts))
            if offs:
                offs.sort()
                return [x - offs[len(offs) // 2] for x in xs]
        confidence.append("x labels not matched to tick marks")
        return [t.x + 0.14 * t.height * len(t.text) / 2 for t in row]

    # x-axis: date-like labels in a row (same y)
    dated = [(t, parse_date(t.text)) for t in dr.texts if parse_date(t.text) is not None]
    x_fit = None
    x_mode = None
    if len(dated) >= 2:
        row_y = max(set(round(t.y) for t, _ in dated), key=lambda y: sum(1 for t, _ in dated if round(t.y) == y))
        row = sorted([(t, d) for t, d in dated if abs(t.y - row_y) <= 2], key=lambda td: td[0].x)
        if len(row) >= 2:
            xs = label_centres([t for t, _ in row])
            x_fit = _fit(xs, [_months(d) for _, d in row])
            x_mode = "months"
    if x_fit is None:
        # categorical x labels (e.g. 2025E, country names): use the non-numeric row with most labels
        cats = [t for t in dr.texts if parse_number(t.text) is None and parse_date(t.text) is None]
        if cats:
            row_y = max(set(round(t.y) for t in cats), key=lambda y: sum(1 for t in cats if round(t.y) == y))
            row = sorted([t for t in cats if abs(t.y - row_y) <= 2], key=lambda t: t.x)
            if len(row) >= 2:
                x_mode = "categories"
                x_cats = list(zip(label_centres(row), [t.text for t in row]))

    # plot area: the rectangle spanned by the gridlines / axis lines
    plot_top, plot_bottom = (min(hlines), max(hlines)) if len(hlines) >= 2 else (None, None)

    def rect_of(p: Poly):
        """(x0, x1, y0, y1) for an axis-aligned closed rectangle polygon, else None."""
        if p.kind != "polygon" or len(p.pts) > 8:
            return None
        xs = sorted({round(x, 2) for x, _ in p.pts})
        ys = sorted({round(y, 2) for _, y in p.pts})
        if len(xs) != 2 or len(ys) != 2:
            return None
        return xs[0], xs[1], ys[0], ys[1]

    # legend: a short horizontal line (line series) or a small filled square (bars/areas) left of a text
    legend: dict[str, str] = {}
    non_axis = [t for t in dr.texts if parse_number(t.text) is None and parse_date(t.text) is None]

    def nearest_label(cy: float, x_right: float):
        near = [t for t in non_axis if abs((t.y - 0.3 * t.height) - cy) < max(t.height, 8) and t.x >= x_right - 2]
        return min(near, key=lambda t: t.x - x_right).text if near else None

    for p in dr.polys:
        if p.kind == "line" and len(p.pts) == 2 and abs(p.pts[0][1] - p.pts[1][1]) < 1 and abs(p.pts[0][0] - p.pts[1][0]) < 200:
            name = nearest_label(p.pts[0][1], p.pts[1][0])
            if name and p.color:
                legend[p.color] = name
        r = rect_of(p)
        if r and p.fill and (r[1] - r[0]) < 60 and (r[3] - r[2]) < 60 and abs((r[1] - r[0]) - (r[3] - r[2])) < 6:
            if plot_bottom is None or r[2] > plot_bottom or r[3] < (plot_top or 0):
                name = nearest_label((r[2] + r[3]) / 2, r[1])
                if name:
                    legend[p.fill] = name

    # series: long polylines (>= 6 points, not axis/grid lines)
    series = []
    for p in dr.polys:
        if p.kind != "line" or len(p.pts) < 6:
            continue
        xs_ = [x for x, _ in p.pts]
        ys_ = [y for _, y in p.pts]
        if max(ys_) - min(ys_) < 1e-6 or max(xs_) - min(xs_) < 1e-6:
            continue  # grid line
        name = legend.get(p.color or "", None)
        axis = right if (right and name and re.search(r"\b(rhs|right)\b", name, re.I)) else left
        a, b = axis["fit"]
        pts = []
        # evenly spaced points are a regular time series: date by index from the first point, so no month is
        # duplicated or skipped by rounding each point separately
        step_months = None
        if x_mode == "months" and len(xs_) > 2:
            gaps = [xs_[i + 1] - xs_[i] for i in range(len(xs_) - 1)]
            mean_gap = sum(gaps) / len(gaps)
            if mean_gap > 0 and max(abs(g - mean_gap) for g in gaps) < 0.25 * mean_gap:
                step_months = max(1, round(x_fit[1] * mean_gap))
                # a point dated mid-month sits just past its month's tick: floor, with a small tolerance for
                # points that sit exactly on the tick (validated against native charts of the same series)
                start_m = math.floor(x_fit[0] + x_fit[1] * xs_[0] + 0.05)
        for i, (x, y) in enumerate(p.pts):
            v = a + b * y
            if x_mode == "months" and step_months:
                pts.append((_from_months(start_m + i * step_months), v))
            elif x_mode == "months":
                xa, xb = x_fit
                pts.append((_from_months(xa + xb * x), v))
            elif x_mode == "categories":
                cat = min(x_cats, key=lambda c: abs(c[0] - x))[1]
                pts.append((cat, v))
            else:
                pts.append((None, v))
        series.append({"name": name, "color": p.color, "axis": "right" if axis is right else "left",
                       "points": pts, "n_points": len(pts), "pct": axis["pct"]})
    # bars: rectangles inside the plot area, grouped by fill colour; value = top - bottom through the axis fit
    def x_label(x: float):
        if x_mode == "months":
            return _from_months(x_fit[0] + x_fit[1] * x) if x_fit else None
        if x_mode == "categories":
            return min(x_cats, key=lambda c: abs(c[0] - x))[1]
        return None

    bars: dict[str, list[tuple[float, float, float]]] = {}
    for p in dr.polys:
        r = rect_of(p)
        if not r or not p.fill or p.fill in ("FFFFFF",):
            continue
        x0, x1, y0, y1 = r
        if plot_top is not None and not (y0 >= plot_top - 2 and y1 <= plot_bottom + 2):
            continue
        if (x1 - x0) > width * 0.25 or (y1 - y0) <= 0:
            continue
        bars.setdefault(p.fill, []).append(((x0 + x1) / 2, y0, y1))
    for fill, rects in bars.items():
        if len(rects) < 2:
            continue
        rects.sort()
        heights = [y1 - y0 for _, y0, y1 in rects]
        plot_h = (plot_bottom - plot_top) if plot_top is not None else width
        if len(rects) > 6 and (max(heights) - min(heights)) < 0.02 * plot_h:
            continue  # scatter markers: many small shapes of the same size, not bars

        name = legend.get(fill)
        axis = right if (right and name and re.search(r"\b(rhs|right)\b", name, re.I)) else left
        a, b = axis["fit"]
        pts = []
        xs_b = [r[0] for r in rects]
        gaps = [xs_b[i + 1] - xs_b[i] for i in range(len(xs_b) - 1)]
        mean_gap = sum(gaps) / len(gaps) if gaps else 0
        regular = x_mode == "months" and x_fit and mean_gap > 0 and max(abs(g - mean_gap) for g in gaps) < 0.25 * mean_gap
        if regular:
            step = max(1, round(x_fit[1] * mean_gap))
            start = math.floor(x_fit[0] + x_fit[1] * xs_b[0] + 0.05)
        for i, (cx, y0, y1) in enumerate(rects):
            v = (a + b * y0) - (a + b * y1)  # top minus bottom: the segment's own value, stacked or not
            pts.append((_from_months(start + i * step) if regular else x_label(cx), v))
        series.append({"name": name, "color": fill, "axis": "right" if axis is right else "left",
                       "points": pts, "n_points": len(pts), "pct": axis["pct"], "mark": "bar"})

    # areas: filled polygons with many points, read along the upper edge; stacked value = upper - lower edge
    def y_at(edge, x):
        for j in range(1, len(edge)):
            if edge[j - 1][0] <= x <= edge[j][0]:
                x0, y0 = edge[j - 1]
                x1, y1 = edge[j]
                return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
        return edge[0][1] if x < edge[0][0] else edge[-1][1]

    for p in dr.polys:
        if p.kind != "polygon" or len(p.pts) < 8 or not p.fill or rect_of(p):
            continue
        ys_a = [y for _, y in p.pts]
        if plot_top is not None and (min(ys_a) < plot_top - 2 or max(ys_a) > plot_bottom + 2):
            continue
        xs_a = [x for x, _ in p.pts]
        if max(xs_a) - min(xs_a) < 0.25 * width:
            continue  # markers, data-label boxes and other small filled shapes are not areas
        turn = next((i for i in range(1, len(xs_a)) if xs_a[i] < xs_a[i - 1]), len(xs_a))
        fwd, back = p.pts[:turn], list(reversed(p.pts[turn:]))
        if len(fwd) < 4 or len(back) < 2:
            continue
        name = legend.get(p.fill)
        axis = right if (right and name and re.search(r"\b(rhs|right)\b", name, re.I)) else left
        a, b = axis["fit"]
        upper = fwd if sum(y for _, y in fwd) / len(fwd) <= sum(y for _, y in back) / len(back) else back
        lower = back if upper is fwd else fwd
        pts = [(x_label(x), (a + b * yu) - (a + b * y_at(lower, x))) for x, yu in upper]
        series.append({"name": name, "color": p.fill, "axis": "right" if axis is right else "left",
                       "points": pts, "n_points": len(pts), "pct": axis["pct"], "mark": "area"})

    if not series:
        return None
    series, issues = _vet_series(series, y_axes, x_mode)
    note = "values read from the drawing; value-axis error below 0.5% of axis range, dates to the nearest month"
    if confidence:
        note += "; lower confidence: " + ", ".join(sorted(set(confidence)))
    # Quality gate. Numbers are only worth indexing when they pass the hard checks (sign agrees with the axis, points
    # have x positions, no duplicates) and every series can be named: a value nobody can attribute to a series
    # misleads more than it helps (Fig 4 of RA01220 came out as nine copies of an unnamed, negative, undated series).
    # The positional notes in `confidence` stay in the precision note as "lower confidence" but do not gate on their
    # own: Fig 84 of RA01220 carries that note and its values were validated against the BoJ balance sheet.
    # Callers show the picture to a vision model instead when quality is "low".
    reasons = list(issues)
    unnamed = [s for s in series if not s["name"]]
    if len(series) > 1 and unnamed:
        reasons.append(f"{len(unnamed)} of {len(series)} series could not be matched to a legend entry")
    if not series:
        reasons.append("no series survived the checks")
    return {
        "method": "emf-vector-digitised",
        "precision_note": note,
        "quality": "low" if reasons else "ok",
        "issues": reasons,
        "x_mode": x_mode,
        "y_axes": [{"labels": a["labels"], "side": "left" if a is left else "right"} for a in y_axes],
        "legend": list(legend.values()),
        "series": series,
    }


def _vet_series(series: list[dict], y_axes: list[dict], x_mode) -> tuple[list[dict], list[str]]:
    """Drop series that cannot be right, and say why: duplicates of another series (the same shape drawn again as a
    border or a shadow), series whose points have no x position, and series whose sign contradicts the axis they
    were read from (an axis fitted to the wrong labels)."""
    issues: list[str] = []
    out: list[dict] = []
    seen: set = set()
    dropped_dup = dropped_x = dropped_sign = 0
    axis_min = {}
    for a in y_axes:
        vals = [parse_number(t) for t in a["labels"]]
        axis_min[id(a)] = min(v for v in vals if v is not None) if any(v is not None for v in vals) else None
    for s in series:
        vals = [v for _, v in s["points"] if isinstance(v, (int, float))]
        if len(vals) < 2:
            continue
        key = (s["name"], s.get("mark"), tuple(round(v, 4) for v in vals))
        if key in seen:
            dropped_dup += 1
            continue
        seen.add(key)
        if all(c is None for c, _ in s["points"]):
            dropped_x += 1
            continue
        # the axis a series was read from is the left one unless it was sent right; both are non-negative in most
        # financial charts, so a clearly negative reading from a non-negative axis means the fit is wrong
        side_axes = [a for a in y_axes if (a is y_axes[0]) == (s["axis"] == "left")] or y_axes
        lo = axis_min.get(id(side_axes[0]))
        if lo is not None and lo >= 0:
            labels = [parse_number(t) for t in side_axes[0]["labels"]]
            rng = max(v for v in labels if v is not None) - lo
            if min(vals) < -0.02 * max(rng, 1e-9):
                dropped_sign += 1
                continue
        out.append(s)
    if dropped_x:
        issues.append(f"{dropped_x} series had no x positions")
    if dropped_sign:
        issues.append(f"{dropped_sign} series read negative from a non-negative axis")
    return out, issues


def digitised_summary_text(d: dict) -> str:
    """Text for the index. Empty when the digitisation did not pass the quality gate, so that the caller falls back
    to a picture description (see `digitised_issue_text` for the note to show instead)."""
    if not d or d.get("quality", "ok") != "ok" or not d["series"]:
        return ""
    lines = [f"Values digitised from the drawing (approximate; {d['precision_note']}):"]
    for s in d["series"]:
        pts = [(c, v) for c, v in s["points"] if isinstance(v, float)]
        if not pts:
            continue
        def fmt(v, pct=s["pct"]):
            s_ = f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.3g}"
            return s_ + ("%" if pct else "")
        name = s["name"] or "(unnamed series)"
        if len(pts) <= 30:
            lines.append(f"- {name} ({len(pts)} points): " + ", ".join(f"{c}: {fmt(v)}" for c, v in pts))
            continue
        first, last = pts[0], pts[-1]
        mn, mx = min(pts, key=lambda p: p[1]), max(pts, key=lambda p: p[1])
        tail = ", ".join(f"{c}: {fmt(v)}" for c, v in pts[-6:])
        lines.append(f"- {name} ({len(pts)} points, {first[0]} to {last[0]}): first {fmt(first[1])} at {first[0]}; "
                     f"min {fmt(mn[1])} at {mn[0]}; max {fmt(mx[1])} at {mx[0]}; last six: {tail}")
    return "\n".join(lines)


def digitised_issue_text(d: dict) -> str:
    """One line saying why a chart's numbers were withheld; "" when they were not."""
    if not d or d.get("quality", "ok") == "ok":
        return ""
    return "Chart values were not read from the drawing (" + "; ".join(d.get("issues") or ["unreliable"]) + ")."
