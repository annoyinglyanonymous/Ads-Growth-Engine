"""The formatters every figure is printed with.

This module used to draw the dashboard's SVG charts as well. The drawing moved
to frontend/src/components/charts.tsx with the React port, same geometry and
the same rules (single-series, no dual axis, colours as tokens, a native
<title> on every point). What stays here is what Python still prints:
intel/prose.py, intel/readings.py and brief_render.py write sentences with
these, and a sentence that says $36.30 above a tile that says $36.3 is the
kind of disagreement nobody notices until they act on one of them.

So the two sides are pinned to one set of answers. frontend/src/lib/format.ts
is this file in TypeScript, and frontend/src/lib/format.cases.json holds the
cases both are tested against -- tests/test_format_parity.py here,
format.test.ts there. Change a formatter on one side and a test fails on the
other until they agree again (`python tests/test_format_parity.py --write`
regenerates the cases after a deliberate change).

The module keeps its name because five modules and their tests import it;
renaming it would be a diff about a word.

Nothing in this module computes a METRIC. The numbers arrive already derived
from ads.rate().
"""

from __future__ import annotations

from decimal import Decimal

Number = float | int | Decimal | None


def _f(v: Number) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def money(v: Number, currency: str = "$") -> str:
    """$41.44, $15,748, and −$0.15 -- the sign goes in front of the currency.

    It used to print $-0.15, which reads as a typo, and every negative CPA
    delta on the site is one of these. The minus is U+2212 rather than a
    hyphen so it sets at the width of a figure in a tabular column.
    """
    n = _f(v)
    if n is None:
        return "--"
    a = abs(n)
    shown = f"{a:,.2f}" if a < 1000 else f"{a:,.0f}"
    # A sign on a figure that rounds to nothing is noise: -0.004 is $0.00.
    sign = "−" if n < 0 and shown.strip("0.,") else ""
    return f"{sign}{currency}{shown}"


def num(v: Number, places: int = 0) -> str:
    n = _f(v)
    return "--" if n is None else f"{n:,.{places}f}"


def pct(v: Number, places: int = 2) -> str:
    n = _f(v)
    return "--" if n is None else f"{n:,.{places}f}%"


def signed_pct(v: Number, places: int = 1) -> str:
    """+12.3%, −8.1%: a change always says which way it went.

    The minus is U+2212, as in money(), so a column of changes lines up. A
    figure that rounds to nothing carries no sign: -0.04 is 0.0%, not −0.0%.
    """
    n = _f(v)
    if n is None:
        return "--"
    shown = f"{abs(n):,.{places}f}"
    if not shown.strip("0.,"):
        return f"{shown}%"
    return f"{'+' if n > 0 else '−'}{shown}%"


def words(v: object) -> str:
    """An enum as a reader says it: QUALITY_LEAD -> "Quality lead".

    Optimization goals, formats and a bridge's reasons arrive as the API's
    constants, and a page that prints LEAD_GENERATION in a pill is a database
    talking. One place turns them into words, so every page says the same
    thing for the same goal.
    """
    s = "" if v is None else str(v).strip()
    if not s:
        return "--"
    s = s.replace("_", " ").lower()
    return s[:1].upper() + s[1:]


def fmt(v: Number, kind: str) -> str:
    """Format by metric kind, so a CPA never renders as a bare float."""
    if kind in ("cpa", "cpc", "cpm", "cost_per_link_click", "spend"):
        return money(v)
    if kind in ("ctr", "link_ctr", "conversion_rate", "lp_view_rate"):
        return pct(v)
    return num(v)


def ago(hours: Number) -> str:
    """How long ago something was written, the way a person says it.

    "0.1h ago" reads as a bug. Minutes under an hour, hours under two days,
    days after that.
    """
    h = _f(hours)
    if h is None:
        return "--"
    if h < 1:
        return f"{max(1, round(h * 60))} min ago"
    if h < 48:
        return f"{round(h)}h ago"
    return f"{int(h // 24)} days ago"
