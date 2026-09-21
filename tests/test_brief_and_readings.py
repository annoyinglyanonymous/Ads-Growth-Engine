"""The brief computes nothing, and every sentence it says is sourced.

Two claims are made in prose elsewhere in this repo, and prose is not what
keeps them true after somebody adds a section in a hurry:

    intel/brief.py    "It performs no arithmetic of any kind"
    intel/readings.py "a reading may only be built out of numbers that are
                       already in the fact pack"

These tests are the enforcement. They follow tests/test_read_only.py's model --
parse the module and assert a property of the source, rather than trusting a
docstring -- for the reason that file gives: a docstring saying "read only" is
a claim; this is the thing that is still true on a Friday.

None of these need a database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from intel import readings

ROOT = Path(__file__).resolve().parent.parent
BRIEF = ROOT / "intel" / "brief.py"
READINGS = ROOT / "intel" / "readings.py"


# ---------------------------------------------------------------------------
# A synthetic fact pack. Deliberately not a fixture from the database: these
# tests are about the shape of what the rules do, and a real pack on an empty
# warehouse fires almost nothing.
# ---------------------------------------------------------------------------

def _facts() -> dict:
    doc = {
        "provisional": True,
        "facts": {
            "trust": {
                "healthy": False,
                "problems": ["one", "two"],
                "conversion_definitions": 0,
                "match": {"approved_assets": 10, "with_tracked_url": 9,
                          "matched": 5, "match_rate": 0.55,
                          "unmatched": [{"campaign": "c", "expected_utm_content": "ad-a-v5"}]},
            },
            "spend": {"currencies": 2, "mixed_currency": True},
            "movement": {
                "cpa_change": 12.5, "rate_effect": 9.0, "mix_effect": 3.5,
                "attributable_share": 0.6, "unattributable_ads": 4,
                "structure_changes": {"available": True, "rows": [{"field": "budget"}]},
            },
            "creative": {
                "rows": [{"ad_key": "k1", "entity_name": "Ad One", "score": 4,
                          "spend_recent": 900,
                          "link_ctr_decline": True, "cpm_rise": True,
                          "cpa_rise": True, "frequency_rise": True,
                          "ranking_drop": False}],
                "suppressed_unconfident": 3,
            },
            "angles": {
                "rows": [
                    {"angle_slug": "cash-upfront", "angle_name": "Cash upfront",
                     "spend": 4000, "rank_within_goal": 1, "spend_sufficient": True,
                     "comparable_on_cost": True, "spend_share_pct": 41,
                     "conversion_share_pct": 12,
                     "optimization_goals": [{"goal": "OFFSITE_CONVERSIONS", "spend": 4000}]},
                    {"angle_slug": "mixed", "angle_name": "Mixed goals",
                     "spend": 2000, "rank_within_goal": None,
                     "spend_sufficient": True, "comparable_on_cost": False,
                     "spend_share_pct": 20, "conversion_share_pct": 19,
                     "optimization_goals": [{"goal": "LINK_CLICKS", "spend": 1000},
                                            {"goal": "OFFSITE_CONVERSIONS", "spend": 1000}]},
                ],
                "never_run": [{"angle_slug": "a"}],
                "under_spent": [{"angle_slug": "b"}],
            },
            "untagged": {"total_spend": 10000, "tagged_spend": 6000,
                         "untagged_spend": 4000, "untagged_ads": 7,
                         "inheritable_ads": 5, "inheritable_spend": 3000,
                         "stale_tag_ads": 2, "stale_tag_spend": 500},
            "experiments": {"rows": [{"name": "MA-015", "state": "running",
                                      "readable": False}]},
        },
        "waiting_on_you": {
            "angles_unsigned": [],
            "experiments_unconcluded": [{"name": "MA-011"}],
        },
    }
    return doc


def test_every_rule_in_the_registry_is_callable():
    """The registry is the governance artifact, so it must not rot."""
    assert readings.RULES, "the registry is empty"
    names = [n for n, _ in readings.RULES]
    assert len(names) == len(set(names)), f"duplicate rule names: {names}"
    for name, fn in readings.RULES:
        assert callable(fn), f"{name} is not callable"


def test_readings_fire_and_every_one_is_shaped():
    out = readings.read(_facts())
    assert out, "no rule fired against a fact pack built to fire several"
    for r in out:
        assert r["rule"], "a reading with no rule name"
        assert r["says"].strip(), f"{r['rule']} says nothing"
        assert r["author"] == f"rule:{r['rule']}"
        assert isinstance(r["cites"], list)


def test_no_rule_raised():
    """`read` converts a raising rule into a rule_failed reading, so a green
    suite that contains one would be hiding a defect behind a caught exception."""
    out = readings.read(_facts())
    failed = [r for r in out if r["rule"] == "rule_failed"]
    assert not failed, f"rules raised: {[r['says'] for r in failed]}"


def test_every_citation_resolves_into_the_fact_pack():
    """A dangling pointer means a rule is reading a fact section that does not
    exist -- a renamed key, or a typo -- and it would silently produce a
    sentence about nothing."""
    doc = _facts()
    for r in readings.read(doc):
        for c in r["cites"]:
            assert readings._at(doc, c["pointer"]) is not None, (
                f"{r['rule']} cites {c['pointer']}, which resolves to nothing")


def test_a_numeric_claim_always_carries_a_citation():
    """The core rule. A reading that states a figure must say where it came
    from; one that cites nothing may not contain a number."""
    for r in readings.read(_facts()):
        if re.search(r"\d", r["says"]) and not r["cites"]:
            pytest.fail(f"{r['rule']} states a figure and cites nothing: "
                        f"{r['says']!r}")


#: Numbers a rule may state without a citation because they are structural --
#: they describe the code, not the data. Kept deliberately tiny: every addition
#: here is a small hole in the rule above, so a new entry needs a reason.
STRUCTURAL = {
    "5",    # ads.fatigue returns exactly five named symptoms
}


def test_every_number_in_a_reading_is_sourced():
    """Each numeric literal in `says` must be a cited value, the length of one,
    or a declared structural constant.

    Length counts as sourced: counting the rows a function returned is not
    computing a rate, a delta or a share, and intel/angles.py already reports
    row_count that way. Dividing two cited values would NOT pass this, which is
    the case it exists to catch.
    """
    doc = _facts()
    for r in readings.read(doc):
        allowed: set[str] = set(STRUCTURAL)
        # A rule that cites one pointer per thing it counted may state that
        # count: the citations under the sentence ARE the evidence, and a
        # reader checks it by counting them. This is how a rule reports a
        # filtered subset without pointing at a list of a different length.
        allowed.add(str(len(r["cites"])))
        for c in r["cites"]:
            v = c["value"]
            allowed.add(str(v))
            if isinstance(v, (list, dict)):
                allowed.add(str(len(v)))
            if isinstance(v, float):
                allowed.add(str(v).rstrip("0").rstrip("."))
                allowed.add(str(int(v)) if v == int(v) else str(v))
        for literal in re.findall(r"\d+(?:\.\d+)?", r["says"]):
            assert literal in allowed, (
                f"{r['rule']} states {literal!r} which is not a cited value, "
                f"the length of one, or structural. says={r['says']!r} "
                f"cited={sorted(allowed)}")


def test_a_rule_does_not_fire_on_a_null_metric():
    """A null rate is undefined, not zero, and must never be ranked or
    asserted on. Blanking every metric should silence the metric-driven rules
    rather than producing sentences about None."""
    doc = _facts()
    doc["facts"]["movement"].update(cpa_change=None, rate_effect=None,
                                    mix_effect=None, attributable_share=None)
    for r in doc["facts"]["angles"]["rows"]:
        r.update(rank_within_goal=None, spend_share_pct=None,
                 conversion_share_pct=None)
    for r in readings.read(doc):
        assert "None" not in r["says"], f"{r['rule']} rendered a null: {r['says']!r}"
        assert r["rule"] not in {"cpa_moved", "rate_dominates", "mix_dominates",
                                 "split_does_not_cover",
                                 "angle_leads_within_goal", "angle_overweight"}, (
            f"{r['rule']} fired on a null metric")


def test_bank_is_inert_fires_when_nothing_is_signed():
    """The single most useful sentence a first brief can carry, so it gets its
    own test rather than riding on the general one."""
    doc = _facts()
    doc["facts"]["angles"]["rows"] = []
    doc["waiting_on_you"]["angles_unsigned"] = [{"slug": "x"}, {"slug": "y"}]
    fired = {r["rule"] for r in readings.read(doc)}
    assert "bank_is_inert" in fired

    doc["facts"]["angles"]["rows"] = [{"angle_slug": "signed"}]
    assert "bank_is_inert" not in {r["rule"] for r in readings.read(doc)}


def test_angle_ranking_never_crosses_an_optimization_goal():
    """rank_within_goal is NULL where comparable_on_cost is false, and no rule
    may rank on it anyway. CPL is not comparable across optimization goals
    (042), and this is where that stops being advisory."""
    doc = _facts()
    ranked = [r for r in readings.read(doc)
              if r["rule"] == "angle_leads_within_goal"]
    named = {(r["subject"] or {}).get("key") for r in ranked}
    assert "mixed" not in named, (
        "an angle spanning two optimization goals was ranked on cost")


# ---------------------------------------------------------------------------
# The AST half. Same device as tests/test_read_only.py.
# ---------------------------------------------------------------------------

def _arithmetic_on_data(path: Path) -> list[str]:
    """Division, multiplication or subtraction applied to something subscripted
    out of a row, plus sum() over one. Deliberately crude: it is meant to catch
    a rate being computed, and a false positive is a prompt to move the
    arithmetic into SQL rather than an inconvenience to argue with."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []

    def looks_like_data(node: ast.AST) -> bool:
        # row["spend"], r.get("spend"), d["facts"]["x"]
        if isinstance(node, ast.Subscript):
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr == "get"
        return False

    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Div, ast.Mult, ast.Sub, ast.FloorDiv)):
            if looks_like_data(node.left) or looks_like_data(node.right):
                bad.append(f"line {node.lineno}: arithmetic on a fetched value")
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "sum"):
            bad.append(f"line {node.lineno}: sum() -- the function already "
                       f"totalled this")
    return bad


def test_brief_computes_nothing():
    """CLAUDE.md's first rule, enforced on the module most able to break it.

    A brief is sixty numbers, dated and archived. A rate computed here and a
    rate on the dashboard would disagree in a document nobody can adjudicate
    weeks later.
    """
    assert not _arithmetic_on_data(BRIEF), (
        "intel/brief.py does arithmetic on fetched data:\n  " +
        "\n  ".join(_arithmetic_on_data(BRIEF)) +
        "\nIf a number is missing, that is a missing SQL function -- name it.")


def test_readings_computes_nothing():
    """Same rule, applied to the rules. A reading interpolates a cited value;
    it never derives a new one."""
    assert not _arithmetic_on_data(READINGS), (
        "intel/readings.py derives a figure instead of citing one:\n  " +
        "\n  ".join(_arithmetic_on_data(READINGS)))


def test_brief_does_not_import_the_writer():
    """intel/brief.py is a read verb. tests/test_read_only.py asserts this for
    intel/ generally; stated here too because the brief is the module most
    likely to grow a "file the proposals it suggested" convenience."""
    tree = ast.parse(BRIEF.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "db_owner" not in node.module
            assert "record" not in (node.module or "")
        if isinstance(node, ast.Import):
            for a in node.names:
                assert "db_owner" not in a.name


# ---------------------------------------------------------------------------
# Every page renders against an EMPTY warehouse.
#
# This is the state the repo ships in and the state it spends most of its life
# in before a first pull, and "zero rows is the failure mode, not an error" is
# a named hazard in CLAUDE.md. The overview page broke exactly here once: the
# brand-level tiles moved from a Python roll-up to a single SQL row, and a
# single row that does not exist is None where a roll-up of nothing was a dict
# of zeroes. Nothing in the suite rendered a page, so nothing caught it.
# ---------------------------------------------------------------------------

PAGES = ("/brief", "/", "/why", "/creative", "/angles", "/experiments", "/ask")


@pytest.fixture(scope="module")
def pages() -> dict[str, str]:
    """Render every page once, through ONE client.

    Module-scoped and not parametrized: each TestClient context runs main.py's
    lifespan, which opens and closes the connection pool against a remote
    database. Seven of those is seven round trips of pool setup for seven
    renders, and it took the suite past two minutes. One client, seven gets.
    """
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        return {path: client.get(path).text for path in PAGES}


def test_every_page_renders_with_no_data(pages):
    for path, body in pages.items():
        assert body.strip(), f"{path} rendered nothing"
        assert "Traceback" not in body, f"{path} rendered a traceback"


def test_no_page_renders_absent_data_as_zero(pages):
    """A missing number is `--`, never `$0.00`.

    003 argues this for cost_per_lead -- "NULL, never 0, when leads = 0" --
    because a zero sorts to the top of a cheapest-CPA column and a null does
    not. The same is true of a reader's eye: $0.00 spend reads as a quiet week,
    and no import at all reads as the same quiet week unless the page says
    otherwise.

    This is a regression test. The overview page broke exactly here when the
    brand tiles moved from a Python roll-up to a single SQL row: a roll-up of
    nothing is a dict of zeroes, and a single row that does not exist is None.
    """
    assert "$0.00" not in pages["/"], (
        "the overview rendered an absent figure as $0.00, which reads as "
        "'spent nothing' rather than 'nothing imported'")


def test_a_missing_value_renders_as_a_dash_not_a_traceback():
    """charts._f must survive jinja2's StrictUndefined.

    A template asking for a key the data does not have gets an Undefined, and
    float(Undefined) raises UndefinedError -- a TemplateError, so neither of
    the exceptions _f originally guarded. The result was a 500 on a page whose
    only problem was an absent number, which is the wrong failure: every other
    missing value in charts.py renders "--".
    """
    from jinja2 import StrictUndefined

    import charts

    missing = StrictUndefined(name="cpa")
    assert charts.money(missing) == "--"
    assert charts.num(missing) == "--"
    assert charts.pct(missing) == "--"
    assert charts.fmt(missing, "cpa") == "--"
