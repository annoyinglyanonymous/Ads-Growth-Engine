"""Inline SVG charts, rendered server-side.

WHY SVG BUILT HERE RATHER THAN A CHART LIBRARY

No build step and no CDN is a rule both repos keep (growth-engine's ui.py:9
records rejecting HTMX on the same grounds). Beyond that, a library would take
its colours from a config object and this app's colours live in CSS custom
properties -- so the chart would be the one thing on the page that does not
follow the viewer's dark mode. Inline SVG with stroke="var(--accent)" follows
it for free.

WHY EVERY CHART HERE IS SINGLE-SERIES

growth-engine's palette is blue and white deliberately: "that removes hue as a
way of saying 'this needs you', so severity is carried by INTENSITY instead"
(base.html:16-27). Multi-series charts need distinguishable hues, which would
break that rule for the whole app.

It turns out not to cost anything, because the charts this dashboard needs are
single-series anyway:

    change over time         -> a line. One series, so the title names it and
                                no legend is needed at all.
    comparison across items  -> a bar chart sorted by magnitude. Identity is
                                carried by the LABEL, not by colour.
    trend inside a table row -> a sparkline.
    one number               -> a stat tile, which is not a chart.

The chart that would force the issue -- two angles plotted against each other
over time -- is deliberately absent. Ranked bars answer the same question, and
adding a second hue is a decision about the design system, not about this page.

NO DUAL-AXIS CHART, EVER. Spend and conversions on one plot with two y-scales
is the most common dashboard mistake there is: the crossover point is an
artefact of two arbitrary scales and people read it as a finding. Two charts.

Geometry is computed here rather than in Jinja because it is arithmetic with
edge cases (a flat series, a single point, all zeros) and those are worth
testing. Nothing in this module computes a METRIC -- the numbers arrive already
derived from ads.rate().
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

Number = float | int | Decimal | None

# Jinja is configured StrictUndefined, so a key a template asks for and the
# data does not have arrives here as an Undefined rather than as None -- and
# float(Undefined) raises UndefinedError, which is a TemplateError and neither
# a TypeError nor a ValueError. It therefore walked straight through the guard
# in _f and became a 500 on a page whose only problem was a missing number.
#
# That is the wrong failure by a wide margin. Every other absent value in this
# module renders "--"; a page should not die because one field was not in the
# result. Imported defensively so charts.py stays usable without jinja2 --
# isinstance against an empty tuple is simply always False.
try:
    from jinja2 import Undefined as _Undefined
except ImportError:  # pragma: no cover - only when rendering outside a template
    _Undefined = ()  # type: ignore[assignment,misc]


def _f(v: Number) -> float | None:
    if v is None or isinstance(v, _Undefined):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(v: Number, currency: str = "$") -> str:
    n = _f(v)
    if n is None:
        return "--"
    return f"{currency}{n:,.2f}" if abs(n) < 1000 else f"{currency}{n:,.0f}"


def num(v: Number, places: int = 0) -> str:
    n = _f(v)
    return "--" if n is None else f"{n:,.{places}f}"


def pct(v: Number, places: int = 2) -> str:
    n = _f(v)
    return "--" if n is None else f"{n:,.{places}f}%"


def fmt(v: Number, kind: str) -> str:
    """Format by metric kind, so a CPA never renders as a bare float."""
    if kind in ("cpa", "cpc", "cpm", "cost_per_link_click", "spend"):
        return money(v)
    if kind in ("ctr", "link_ctr", "conversion_rate", "lp_view_rate"):
        return pct(v)
    return num(v)


def _nice_ceiling(v: float) -> float:
    """Round an axis maximum up to something a person would have chosen."""
    if v <= 0:
        return 1.0
    import math
    mag = 10 ** math.floor(math.log10(v))
    for step in (1, 2, 2.5, 5, 10):
        if v <= step * mag:
            return step * mag
    return 10 * mag


def line_chart(points: list[tuple[date, Number]], *, kind: str = "spend",
               label: str = "", height: int = 150, width: int = 760) -> str:
    """One series over time. Title names it, so there is no legend.

    A series with fewer than two defined points is not a trend and is not
    drawn as one -- an empty state says so instead of a chart implying a shape
    from a single dot.
    """
    defined = [(d, _f(v)) for d, v in points if _f(v) is not None]
    if len(defined) < 2:
        return ('<div class="empty small">Not enough days in this window to '
                'draw a trend.</div>')

    pad_l, pad_r, pad_t, pad_b = 52, 10, 12, 24
    plot_w, plot_h = width - pad_l - pad_r, height - pad_t - pad_b
    lo, hi = min(v for _, v in defined), max(v for _, v in defined)
    lo = min(lo, 0.0)
    span = _nice_ceiling(hi - lo) if hi > lo else 1.0
    top = lo + span

    def x(i: int) -> float:
        return pad_l + (plot_w * i / (len(defined) - 1))

    def y(v: float) -> float:
        return pad_t + plot_h - ((v - lo) / (top - lo) * plot_h)

    pts = [(x(i), y(v)) for i, (_, v) in enumerate(defined)]
    path = "M " + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
    area = (f"M {pts[0][0]:.1f} {pad_t + plot_h:.1f} L "
            + " L ".join(f"{px:.1f} {py:.1f}" for px, py in pts)
            + f" L {pts[-1][0]:.1f} {pad_t + plot_h:.1f} Z")

    grid = []
    for frac in (0, 0.5, 1):
        gy = pad_t + plot_h * frac
        val = top - (top - lo) * frac
        grid.append(f'<line class="grid-line" x1="{pad_l}" y1="{gy:.1f}" '
                    f'x2="{width - pad_r}" y2="{gy:.1f}"/>')
        grid.append(f'<text class="axis" x="{pad_l - 7}" y="{gy + 3.5:.1f}" '
                    f'text-anchor="end">{escape(fmt(val, kind))}</text>')

    # Each point carries a native <title>, which gives a tooltip with no
    # JavaScript at all. app.js adds a crosshair on top for the line itself;
    # if it never loads, the chart is still inspectable.
    dots = []
    for (px, py), (d, v) in zip(pts, defined):
        dots.append(
            f'<circle class="dot" cx="{px:.1f}" cy="{py:.1f}" r="2.5">'
            f'<title>{escape(d.isoformat())}: {escape(fmt(v, kind))}</title>'
            f'</circle>')

    first, last = defined[0][0], defined[-1][0]
    aria = (f"{label or kind} from {first.isoformat()} to {last.isoformat()}, "
            f"ranging {fmt(lo, kind)} to {fmt(hi, kind)}")
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(aria)}" data-chart="line">'
        f'{"".join(grid)}'
        f'<path class="area" d="{area}"/>'
        f'<path class="line" d="{path}"/>'
        f'{"".join(dots)}'
        f'<text class="axis" x="{pad_l}" y="{height - 6}">{first.isoformat()}</text>'
        f'<text class="axis" x="{width - pad_r}" y="{height - 6}" '
        f'text-anchor="end">{last.isoformat()}</text>'
        f'</svg>')


def bar_chart(rows: list[tuple[str, Number]], *, kind: str = "cpa",
              width: int = 760, row_h: int = 28, label_w: int = 190) -> str:
    """Horizontal bars, sorted by magnitude. Identity is the label.

    Sorted because an unsorted bar chart makes the reader do the ranking, and
    ranking is the only thing this chart is for. Rows with no value are kept
    and drawn dim rather than dropped -- "this angle has no CPA because it has
    no conversions" is a finding, and silently omitting it turns an absence
    into an impression that the angle was never tried.
    """
    if not rows:
        return '<div class="empty small">Nothing to compare in this window.</div>'

    ranked = sorted(rows, key=lambda r: (_f(r[1]) is None, -(_f(r[1]) or 0)))
    values = [_f(v) for _, v in ranked if _f(v) is not None]
    top = _nice_ceiling(max(values)) if values else 1.0
    height = len(ranked) * row_h + 10
    bar_w = width - label_w - 90

    out = []
    for i, (name, v) in enumerate(ranked):
        y = i * row_h + 5
        n = _f(v)
        out.append(
            f'<text class="lbl" x="0" y="{y + row_h * 0.62:.1f}">'
            f'{escape(name[:28])}</text>')
        if n is None:
            out.append(
                f'<text class="axis" x="{label_w}" y="{y + row_h * 0.62:.1f}">'
                f'no conversions in this window</text>')
            continue
        w = max(1.0, bar_w * (n / top))
        out.append(
            f'<rect class="bar" x="{label_w}" y="{y + 4}" width="{w:.1f}" '
            f'height="{row_h - 12}" rx="4">'
            f'<title>{escape(name)}: {escape(fmt(n, kind))}</title></rect>')
        out.append(
            f'<text class="val" x="{label_w + w + 8:.1f}" '
            f'y="{y + row_h * 0.62:.1f}">{escape(fmt(n, kind))}</text>')

    best = ranked[0][0] if values else ""
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{escape(kind)} by item, {len(ranked)} items, '
        f'highest is {escape(best)}" data-chart="bar">'
        f'{"".join(out)}</svg>')


def sparkline(values: list[Number], *, width: int = 110, height: int = 26) -> str:
    """A shape, not a readout. No axis, no labels -- the table has the numbers."""
    nums = [_f(v) for v in values]
    defined = [v for v in nums if v is not None]
    if len(defined) < 2:
        return '<span class="muted small">--</span>'
    lo, hi = min(defined), max(defined)
    span = (hi - lo) or 1.0
    step = width / (len(nums) - 1) if len(nums) > 1 else width

    pts, pen = [], False
    for i, v in enumerate(nums):
        if v is None:
            pen = False
            continue
        px = i * step
        py = height - 2 - ((v - lo) / span) * (height - 4)
        pts.append(f'{"M" if not pen else "L"} {px:.1f} {py:.1f}')
        pen = True
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'aria-hidden="true"><path d="{" ".join(pts)}"/></svg>')


def column_chart(rows: list[tuple[str, Number]], *, kind: str = "conversions",
                 width: int = 360, height: int = 168,
                 highlight: str = "max") -> str:
    """Vertical bars with the peak picked out. One series, so no legend.

    The counterpart to bar_chart, which is horizontal and sorted by magnitude.
    This one keeps the given ORDER -- days stay in day order -- because a
    reordered time axis is not a time axis. Identity is the x label.

    `highlight` names the one bar drawn in full accent; the rest are recessive.
    That is the whole point of the form: the eye lands on the peak without a
    second colour being introduced to say so. Passing highlight="none" turns it
    off for a series where no single bar is the answer.
    """
    vals = [(str(k), _f(v)) for k, v in rows]
    if not any(v is not None for _, v in vals):
        return '<div class="empty small">No data in this window.</div>'

    pad_t, pad_b, pad_x = 16, 22, 4
    plot_h = height - pad_t - pad_b
    top = _nice_ceiling(max((v for _, v in vals if v is not None), default=0)) or 1.0

    peak = None
    if highlight == "max":
        defined = [(i, v) for i, (_, v) in enumerate(vals) if v is not None]
        if defined:
            peak = max(defined, key=lambda p: p[1])[0]

    slot = (width - pad_x * 2) / max(len(vals), 1)
    bw = min(slot * 0.52, 26)

    out = []
    for i, (name, v) in enumerate(vals):
        cx = pad_x + slot * i + slot / 2
        bx = cx - bw / 2
        # A null bar is drawn as nothing, not as zero. "No conversions" and
        # "no data" look identical at zero height otherwise, and only one of
        # them is a fact about the advertising.
        if v is not None:
            bh = max((v / top) * plot_h, 1.5) if top else 1.5
            by = pad_t + plot_h - bh
            cls = "col" + ("" if peak is not None and i == peak else " dim")
            out.append(
                f'<rect class="{cls}" x="{bx:.1f}" y="{by:.1f}" '
                f'width="{bw:.1f}" height="{bh:.1f}" rx="4">'
                f'<title>{escape(name)}: {escape(fmt(v, kind))}</title></rect>')
            if peak is not None and i == peak:
                out.append(
                    f'<text class="val" x="{cx:.1f}" y="{by - 5:.1f}" '
                    f'text-anchor="middle">{escape(fmt(v, kind))}</text>')
        out.append(
            f'<text class="axis" x="{cx:.1f}" y="{height - 6:.1f}" '
            f'text-anchor="middle">{escape(name)}</text>')

    hi_name = vals[peak][0] if peak is not None else ""
    aria = (f"{kind} by {len(vals)} periods"
            + (f", highest {hi_name}" if hi_name else ""))
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(aria)}" data-chart="column">'
            f'{"".join(out)}</svg>')


# --------------------------------------------------------------- miniatures
# Two forms sized to sit beside a sentence rather than to be looked at on
# their own. The brief's findings band is the only caller: a finding is one
# claim, and a picture of the claim earns its place there precisely because it
# lets the claim be shorter. Anywhere a reader would STUDY the shape, the
# full-size forms above are the right answer.


def split_bar(segments: list[tuple[str, Number, str]], *, height: int = 12) -> str:
    """One bar cut into proportional parts. Composition, not magnitude.

    For the places where the FINDING IS THE SPLIT -- what share of the window's
    spend bought which optimisation goal, how many readable ads carry how many
    fatigue symptoms, how much of the money was tagged at all. A pie answers
    the same question worse: people read angles badly, and the ~2% of this
    account that bought thruplay becomes a sliver nobody can see or hover.

    `tone` names a CSS custom property, so a segment follows the palette rather
    than carrying a colour of its own. Segments draw in the order GIVEN and are
    never sorted here: for the symptom distribution the order is the scale, and
    ranking it by size would destroy the thing it is showing.

    A zero segment is dropped rather than drawn one pixel wide. "No ad carries
    three symptoms" is said better by absence than by a sliver that reads as
    one ad and cannot be hovered to find out.
    """
    vals = [(str(name), _f(v) or 0.0, tone) for name, v, tone in segments]
    total = sum(v for _, v, _ in vals if v > 0)
    if total <= 0:
        return '<div class="empty small">Nothing to split in this window.</div>'

    out, x = [], 0.0
    for name, v, tone in vals:
        if v <= 0:
            continue
        w = 100.0 * v / total
        out.append(
            f'<rect x="{x:.4f}" y="0" width="{w:.4f}" height="{height}" '
            f'fill="var({tone})"><title>{escape(name)}</title></rect>')
        x += w

    # The height is inline rather than in the stylesheet because the viewBox
    # is a percentage ruler, not a size: with preserveAspectRatio="none" the
    # bar stretches to whatever box CSS gives it, so a fixed height in
    # base.html would silently make this argument do nothing.
    aria = ", ".join(f"{name} {v:g}" for name, v, _ in vals if v > 0)
    return (f'<svg class="split" viewBox="0 0 100 {height}" '
            f'preserveAspectRatio="none" style="height:{height}px" role="img" '
            f'aria-label="{escape(aria)}" data-chart="split">'
            f'{"".join(out)}</svg>')


def effect_bars(rows: list[tuple[str, Number, int]], *, kind: str = "cpa",
                width: int = 200, row_h: int = 17, label_w: int = 30,
                value_w: int = 58) -> str:
    """Signed bars around a shared zero, at about the size of a line of text.

    The CPA bridge in miniature: rate effect one way, mix effect the other, the
    net between them. Two bars pointing opposite ways say "these cancelled"
    faster than a sentence can, which is the entire reason this exists -- it is
    what lets the finding beside it run to two lines instead of four.

    `merit` is +1 when a positive value is GOOD, -1 when a positive value is
    BAD, 0 when the number carries no direction of merit at all. That is
    base.html's rule and it is NOT the sign: a rate effect of +$38 pushes CPA
    up, so it is bad, and it still points right. Colour follows merit,
    direction follows sign, and neither is ever allowed to stand in for the
    other -- which is also why the value prints beside every bar.

    One scale across all rows, taken from the largest magnitude present. Per-row
    scaling would make three unrelated pictures stacked up and the comparison
    between them is the only thing this form is for.
    """
    vals = [(str(name), _f(v), int(merit)) for name, v, merit in rows]
    mags = [abs(v) for _, v, _ in vals if v is not None]
    if not mags or max(mags) <= 0:
        return '<div class="empty small">No effect to split in this window.</div>'

    top = max(mags)
    track = width - label_w - value_w - 12
    half = track / 2.0
    mid = label_w + 6 + half
    height = len(vals) * row_h + 4

    out = []
    for i, (name, v, merit) in enumerate(vals):
        y = i * row_h + 2
        cy = y + row_h * 0.62
        out.append(f'<text class="axis" x="{label_w}" y="{cy:.1f}" '
                   f'text-anchor="end">{escape(name[:9])}</text>')
        out.append(f'<rect class="track" x="{label_w + 6}" y="{y + 3}" '
                   f'width="{track:.1f}" height="{row_h - 8}" rx="2"/>')
        if v is None:
            out.append(f'<text class="axis" x="{width}" y="{cy:.1f}" '
                       f'text-anchor="end">--</text>')
            continue
        tone = "--accent" if merit == 0 else (
            "--good" if (v > 0) == (merit > 0) else "--bad")
        w = max(1.0, half * (abs(v) / top))
        x = mid if v > 0 else mid - w
        out.append(
            f'<rect x="{x:.1f}" y="{y + 3}" width="{w:.1f}" '
            f'height="{row_h - 8}" rx="2" fill="var({tone})">'
            f'<title>{escape(name)}: {escape(fmt(v, kind))}</title></rect>')
        out.append(
            f'<text class="val" x="{width}" y="{cy:.1f}" text-anchor="end" '
            f'fill="var({tone})">{escape(fmt(v, kind))}</text>')

    out.append(f'<line class="zero" x1="{mid:.1f}" y1="2" x2="{mid:.1f}" '
               f'y2="{height - 2}"/>')
    # Width inline, and capped, for the reason split_bar states: the caller
    # asks for 200 beside a finding and 560 inside a card, and a width pinned
    # in the stylesheet would make one of those a lie. max-width keeps the
    # wide one from overflowing its card on a narrow screen.
    aria = ", ".join(f"{name} {fmt(v, kind)}" for name, v, _ in vals)
    return (f'<svg class="chart micro" viewBox="0 0 {width} {height}" '
            f'style="width:{width}px; max-width:100%" role="img" '
            f'aria-label="{escape(aria)}" data-chart="effect">'
            f'{"".join(out)}</svg>')
