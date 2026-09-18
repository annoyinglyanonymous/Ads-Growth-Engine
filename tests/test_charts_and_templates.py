"""Charts and templates, without a database.

The chart tests are mostly about degenerate input -- a flat series, one point,
all zeros, a None in the middle. Those are what actually turn up on a new ad
account, and each of them is a division by zero waiting in a naive
implementation.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

import charts

ROOT = Path(__file__).resolve().parent.parent


def series(values, start=date(2026, 9, 1)):
    return [(start + timedelta(days=i), v) for i, v in enumerate(values)]


# ------------------------------------------------------------------ charts

def test_line_chart_renders_svg():
    svg = charts.line_chart(series([1, 5, 3, 9]), kind="spend")
    assert svg.startswith("<svg")
    assert 'role="img"' in svg and "aria-label=" in svg
    assert svg.count("<circle") == 4


def test_line_chart_refuses_to_imply_a_trend_from_one_point():
    """A single dot drawn on an axis reads as a flat line, which is a claim."""
    assert "<svg" not in charts.line_chart(series([5]), kind="spend")
    assert "<svg" not in charts.line_chart([], kind="spend")


def test_line_chart_survives_a_flat_series():
    # hi == lo, so the naive span is zero and every y is a division by zero.
    svg = charts.line_chart(series([4, 4, 4]), kind="spend")
    assert "<svg" in svg
    assert "nan" not in svg.lower() and "inf" not in svg.lower()


def test_line_chart_survives_all_zeros():
    svg = charts.line_chart(series([0, 0, 0, 0]), kind="spend")
    assert "<svg" in svg and "nan" not in svg.lower()


def test_line_chart_skips_undefined_points_rather_than_plotting_zero():
    """A day with no CPA is not a day with a CPA of zero.

    Plotting the gap as zero would draw a cliff that never happened, and a
    cliff is exactly what somebody would act on.
    """
    svg = charts.line_chart(series([10, None, 12, 14]), kind="cpa")
    assert svg.count("<circle") == 3


def test_every_chart_colour_is_a_token():
    """Otherwise the chart is the one thing that ignores dark mode."""
    out = [
        charts.line_chart(series([1, 2, 3]), kind="spend"),
        charts.bar_chart([("a", 3), ("b", 1)], kind="cpa"),
        charts.sparkline([1, 2, 3]),
    ]
    for svg in out:
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b", svg), svg[:200]
        assert "rgb(" not in svg


def test_bar_chart_sorts_by_magnitude():
    svg = charts.bar_chart([("low", 1), ("high", 9), ("mid", 5)], kind="cpa")
    order = re.findall(r'class="lbl"[^>]*>([^<]+)<', svg)
    assert order == ["high", "mid", "low"]


def test_bar_chart_keeps_rows_with_no_value():
    """Dropping them would turn "no conversions" into "never tried"."""
    svg = charts.bar_chart([("has", 4), ("none", None)], kind="cpa")
    assert "none" in svg and "no conversions" in svg


def test_bar_chart_handles_empty_and_all_none():
    assert "<svg" not in charts.bar_chart([], kind="cpa")
    svg = charts.bar_chart([("a", None), ("b", None)], kind="cpa")
    assert "<svg" in svg


def test_sparkline_needs_two_points():
    assert "<svg" not in charts.sparkline([1])
    assert "<svg" in charts.sparkline([1, 2])


def test_sparkline_breaks_the_line_across_gaps():
    # Two M commands: the pen lifts rather than bridging a missing day.
    assert charts.sparkline([1, None, 3]).count("M ") == 2


def test_formatters_never_render_none_as_zero():
    """042's rule: an undefined cost is NULL, never 0.

    A zero sorts to the top of a cheapest-CPA column and reads as the best ad
    on the page.
    """
    assert charts.money(None) == "--"
    assert charts.num(None) == "--"
    assert charts.pct(None) == "--"
    assert charts.fmt(None, "cpa") == "--"


def test_formatters_accept_decimal():
    # psycopg returns numeric as Decimal, and float() on it is the only place
    # money would silently lose precision.
    assert charts.money(Decimal("46.2300")) == "$46.23"
    assert charts.pct(Decimal("2.5000")) == "2.50%"


def test_labels_are_escaped():
    svg = charts.bar_chart([("<script>x</script>", 1)], kind="cpa")
    assert "<script>" not in svg


# --------------------------------------------------------------- templates

def _env():
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(loader=FileSystemLoader(str(ROOT / "templates")),
                      undefined=StrictUndefined)
    env.globals.update(money=charts.money, num=charts.num, pct=charts.pct,
                       fmt=charts.fmt, line_chart=charts.line_chart,
                       bar_chart=charts.bar_chart, sparkline=charts.sparkline)
    return env


TEMPLATES = sorted(p.name for p in (ROOT / "templates").glob("*.html"))


def test_templates_exist():
    assert len(TEMPLATES) >= 7, TEMPLATES


@pytest.mark.parametrize("name", TEMPLATES)
def test_template_compiles(name):
    _env().get_template(name)


def test_error_page_renders():
    html = _env().get_template("error.html").render(
        nav="overview", brand=None, settled=None, caveat=None,
        error="ADS_DATABASE_URL_RO is not set.", detail="ADS_DATABASE_URL_RO is not set.")
    assert "ADS_DATABASE_URL_RO" in html
    assert "ads_migrate.py --status" in html


def test_the_dashboard_does_not_follow_the_operating_system_theme():
    """One palette: Agency Heights white and navy, on any machine.

    This replaces a test that checked the opposite -- that every token had a
    dark counterpart. It was a good test of a decision that turned out to be
    wrong: the page rendered navy-on-near-black for anyone whose Windows sat
    in dark mode, which is not the design system and is not what was asked
    for. The test changed because the decision changed, and this is the shape
    that stops it coming back by accident.

    `color-scheme` is asserted beside it, because it is the half people
    forget: drop the media query and the browser STILL paints the toolbar
    <select>, the scrollbars and the focus ring dark on a dark OS, leaving a
    white page with black holes punched through it.
    """
    css = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    # Strip CSS comments first: the note in base.html explains why the media
    # query is absent, and a substring search cannot tell that sentence from
    # an @media rule. Same lesson as test_migrations._strip_comments.
    live = re.sub(r"/[*].*?[*]/", "", css, flags=re.DOTALL)
    assert "prefers-color-scheme" not in live, (
        "base.html follows the OS theme again. If a dark mode is wanted it "
        "wants a control a person chooses, not a palette the OS imposes.")
    assert re.search(r"color-scheme:[ ]*light", live), (
        "color-scheme: light is missing, so form controls and scrollbars "
        "render dark on a dark OS whatever the palette above says.")


def test_every_colour_token_is_defined_once_and_every_var_resolves():
    """With one theme there is one place a colour is decided.

    The old two-block arrangement made "defined in both" the property worth
    checking. What matters now is that nothing redefines a token further down
    the sheet, where a reader looking at :root would never see it -- and that
    no var() reaches for a token the block does not declare. That resolves to
    nothing and paints black or transparent, with no error anywhere.
    """
    css = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    live = re.sub(r"/[*].*?[*]/", "", css, flags=re.DOTALL)
    # [^}] rather than a lazy dot: the token block contains no nested
    # rule, so the first closing brace IS the end of it.
    root = re.search(r":root [{]([^}]*)[}]", live, re.DOTALL)
    assert root, "no :root token block"
    declared = re.findall(r"(--[a-z0-9-]+):", root.group(1))
    assert len(declared) >= 30, declared
    assert len(declared) == len(set(declared)), "a token is declared twice"
    used = set(re.findall(r"var[(](--[a-z0-9-]+)", live))
    assert not (used - set(declared)), f"undefined: {sorted(used - set(declared))}"
