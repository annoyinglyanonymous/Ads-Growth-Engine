"""The formatters, and the one palette, without a database.

The charts moved to frontend/src/components/charts.tsx and their tests with
them (charts.test.tsx). What stays in Python is the formatting every sentence
is written with -- intel/prose.py, intel/readings.py and brief_render.py print
through charts.money and friends -- and the palette rule the whole dashboard
is held to, which is a property of the stylesheet and the page sources rather
than of either renderer.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import charts

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"
BASE_CSS = SRC / "styles" / "base.css"


# -------------------------------------------------------------- formatters

def test_formatters_never_render_none_as_zero():
    """042's rule: an undefined cost is NULL, never 0.

    A zero sorts to the top of a cheapest-CPA column and reads as the best ad
    on the page.
    """
    assert charts.money(None) == "--"
    assert charts.num(None) == "--"
    assert charts.pct(None) == "--"
    assert charts.fmt(None, "cpa") == "--"


def test_money_puts_the_sign_before_the_currency():
    """$-0.15 reads as a typo, and every negative CPA delta is one of these."""
    assert charts.money(-0.15) == "−$0.15"
    assert charts.money(Decimal("-1234.6")) == "−$1,235"
    assert charts.money(0.15) == "$0.15"
    # A sign on a figure that rounds to nothing is noise.
    assert charts.money(-0.004) == "$0.00"


def test_formatters_accept_decimal():
    # psycopg returns numeric as Decimal, and float() on it is the only place
    # money would silently lose precision.
    assert charts.money(Decimal("46.2300")) == "$46.23"
    assert charts.pct(Decimal("2.5000")) == "2.50%"


def test_signed_pct():
    """A change says which way it went, with the same minus as money()."""
    assert charts.signed_pct(12.34) == "+12.3%"
    assert charts.signed_pct(-8.06) == "−8.1%"
    assert charts.signed_pct(None) == "--"
    assert charts.signed_pct(Decimal("-22.4100")) == "−22.4%"
    assert charts.signed_pct("5") == "+5.0%"
    assert charts.signed_pct(-0.04) == "0.0%", "a rounded-away change has no sign"
    assert charts.signed_pct(1234.5, 0) == "+1,234%"


# ----------------------------------------------------------------- palette

def _live_css(path: Path) -> str:
    # Strip comments first: the note in base.css explains why the media query
    # is absent, and a substring search cannot tell that sentence from an
    # @media rule. Same lesson as test_migrations._strip_comments.
    return re.sub(r"/[*].*?[*]/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*")
                  if p.suffix in (".tsx", ".ts", ".css") and not p.name.endswith(".test.tsx"))


def test_the_sources_exist():
    assert BASE_CSS.is_file()
    assert len([p for p in _sources() if p.parent.name == "pages"]) >= 9


def test_the_error_page_names_the_usual_causes():
    tsx = (SRC / "pages" / "ErrorPage.tsx").read_text(encoding="utf-8")
    assert "ADS_DATABASE_URL_RO" in tsx
    assert "ads_migrate.py --status" in tsx


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
    for css in (p for p in _sources() if p.suffix == ".css"):
        assert "prefers-color-scheme" not in _live_css(css), (
            f"{css.name} follows the OS theme again. If a dark mode is wanted it "
            "wants a control a person chooses, not a palette the OS imposes.")
    assert re.search(r"color-scheme:[ ]*light", _live_css(BASE_CSS)), (
        "color-scheme: light is missing, so form controls and scrollbars "
        "render dark on a dark OS whatever the palette above says.")


BANNED = re.compile(r"var\(--(ok|ok-bg|ok-line|warn|warn-bg|warn-line|"
                    r"err|err-bg|err-line|on-err|good|bad)\)")


def test_the_dashboard_is_blue_and_white():
    """No rule and no page fills anything red, green or amber.

    The success, warning and error families are still DECLARED in :root --
    the palette does not change, and the test below still wants every token
    defined once -- but nothing may use them. Severity is intensity of navy
    and merit is a word; a page that turns amber is not this system.
    """
    live = re.sub(r":root [{][^}]*[}]", "", _live_css(BASE_CSS), count=1, flags=re.DOTALL)
    assert not BANNED.findall(live), BANNED.findall(live)
    for path in _sources():
        # THE ONE EXCEPTION, granted by the user on 2026-09-23: the campaign
        # triage cards on /suggestions are red / yellow / green, because that
        # was the request ("critical in red, requires some changes in yellow
        # and none in green"). Scoped to that component and its stylesheet by
        # name. Every other page, base.css and charts.tsx stay under the rule.
        if path.name in ("Triage.tsx", "triage.css") or path.parent.name == "triage":
            continue
        text = path.read_text(encoding="utf-8")
        hit = BANNED.search(_live_css(path) if path.suffix == ".css" else text)
        assert not hit, f"{path.relative_to(ROOT)} uses {hit.group(0)}"
    assert not BANNED.search((ROOT / "charts.py").read_text(encoding="utf-8"))


def test_every_colour_token_is_defined_once_and_every_var_resolves():
    """With one theme there is one place a colour is decided.

    What matters is that nothing redefines a token further down a sheet,
    where a reader looking at :root would never see it -- and that no var()
    anywhere reaches for a token the block does not declare. That resolves to
    nothing and paints black or transparent, with no error anywhere.
    """
    live = _live_css(BASE_CSS)
    root = re.search(r":root [{]([^}]*)[}]", live, re.DOTALL)
    assert root, "no :root token block"
    declared = re.findall(r"(--[a-z0-9-]+):", root.group(1))
    assert len(declared) >= 30, declared
    assert len(declared) == len(set(declared)), "a token is declared twice"
    used: set[str] = set()
    for path in _sources():
        text = _live_css(path) if path.suffix == ".css" else path.read_text(encoding="utf-8")
        used |= set(re.findall(r"var[(](--[a-z0-9-]+)", text))
        if path.suffix == ".css" and path != BASE_CSS:
            assert ":root" not in text, f"{path.name} redefines tokens outside base.css"
    # Tokens a chart passes by name, `fill={`var(${tone})`}` with tone
    # "--accent": the literal is the token.
    for path in _sources():
        if path.suffix != ".css":
            used |= set(re.findall(r"[\"'](--[a-z0-9-]+)[\"']", path.read_text(encoding="utf-8")))
    assert not (used - set(declared)), f"undefined: {sorted(used - set(declared))}"
