"""The 018 wrappers in intel/metrics.py, against a stubbed fetch.

No database: each test replaces metrics.fetch_all and context.brand, then
checks what the wrapper asked for (every argument cast to 018's declared
type) and what it made of the answer (selected, decoded, never computed).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import textwrap
from datetime import date

import pytest
from psycopg import errors

from intel import metrics

BRAND = {"id": "00000000-0000-0000-0000-0000000000b1", "slug": "renegade",
         "name": "Renegade"}

WINDOW = {"pair": "last_7_vs_prior_7",
          "cur_since": date(2026, 9, 22), "cur_until": date(2026, 9, 28),
          "pri_since": date(2026, 9, 15), "pri_until": date(2026, 9, 21),
          "cur_days": 7, "pri_days": 7,
          "settled_edge": date(2026, 9, 28), "unsettled_days": 0}

GOAL_ROW = {"level": "goal", "optimization_goal": "LEAD_GENERATION",
            "campaign_key": None, "campaign_name": None,
            "cur_spend": 1200.0, "pri_spend": 1000.0,
            "cur_conversions": 40, "pri_conversions": 50,
            "cur_cpa": 30.0, "pri_cpa": 20.0, "cpa_pct": 50.0,
            "excess_cost": 400.0, "evidence_level": "moderate",
            "impact_score": 320.0, "goal_cpa_contribution": 10.0,
            "contribution_share_pct": None, "cumulative_share_pct": None,
            "contribution_rank": None, "campaigns_in_goal": 2,
            "multi_goal_campaigns": 0, "mixed_currency": False,
            "ordinality": 1}

CAMPAIGN_ROW = {**GOAL_ROW, "level": "campaign",
                "campaign_key": "c1", "campaign_name": "Become an Agent",
                "goal_cpa_contribution": 7.5, "contribution_share_pct": 75.0,
                "cumulative_share_pct": 75.0, "contribution_rank": 1,
                "multi_goal_campaigns": None, "ordinality": 2}


def _stub(monkeypatch, answer):
    """Replace the fetch with one that records its call and answers.

    `answer` is a list of rows, or an exception instance to raise.
    """
    calls: list[tuple[str, tuple]] = []

    async def fake_brand(slug):
        assert slug == "renegade"
        return BRAND

    async def fake_fetch_all(sql, params=()):
        calls.append((sql, tuple(params)))
        if isinstance(answer, BaseException):
            raise answer
        return [dict(r) for r in answer]

    async def no_fetch_one(sql, params=()):
        raise AssertionError(f"018 wrappers make one round trip: {sql[:80]}")

    monkeypatch.setattr(metrics.context, "brand", fake_brand)
    monkeypatch.setattr(metrics, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(metrics, "fetch_one", no_fetch_one)
    return calls


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# goal_impact
# ---------------------------------------------------------------------------

def test_goal_impact_takes_its_windows_from_analysis_windows(monkeypatch):
    calls = _stub(monkeypatch, [{**WINDOW, **GOAL_ROW},
                                {**WINDOW, **CAMPAIGN_ROW}])
    out = _run(metrics.goal_impact("renegade"))

    assert len(calls) == 1, "one round trip"
    sql, params = calls[0]
    assert "ads.analysis_windows(%s::uuid, now())" in sql
    assert "ads.goal_impact(" in sql and "w.settled_edge" in sql
    assert "w.pair = %s::text" in sql
    assert params == (BRAND["id"], BRAND["id"], "last_7_vs_prior_7")

    assert out["available"] is True
    assert out["pair"] == "last_7_vs_prior_7"
    assert out["settled_through"] == date(2026, 9, 28)
    assert out["windows"]["cur_since"] == date(2026, 9, 22)
    assert "pair" not in out["windows"] and "settled_edge" not in out["windows"]
    assert [r["level"] for r in out["rows"]] == ["goal", "campaign"]
    for row in out["rows"]:
        assert "ordinality" not in row
        assert "cur_since" not in row and "settled_edge" not in row
    # Every figure is the function's, passed through untouched.
    assert out["rows"][1]["cumulative_share_pct"] == 75.0
    assert out["rows"][0]["impact_score"] == 320.0


def test_goal_impact_with_no_goal_in_the_windows_keeps_the_dates(monkeypatch):
    empty = {k: None for k in GOAL_ROW}
    _stub(monkeypatch, [{**WINDOW, **empty}])
    out = _run(metrics.goal_impact("renegade", pair="last_14_vs_prior_14"))
    assert out["available"] is True
    assert out["rows"] == []
    assert out["windows"]["cur_days"] == 7


def test_goal_impact_with_no_pair_says_so(monkeypatch):
    _stub(monkeypatch, [])
    out = _run(metrics.goal_impact("renegade"))
    assert out["available"] is True
    assert out["windows"] is None and out["rows"] == []


def test_goal_impact_refuses_an_unknown_pair(monkeypatch):
    _stub(monkeypatch, [])
    with pytest.raises(ValueError, match="unknown pair"):
        _run(metrics.goal_impact("renegade", pair="last_3_vs_prior_3"))


# ---------------------------------------------------------------------------
# Every wrapper degrades before 018 is applied, and none of them crashes.
# ---------------------------------------------------------------------------

CALLS = {
    "goal_impact": lambda: metrics.goal_impact("renegade"),
    "facet_by_goal": lambda: metrics.facet_by_goal(
        "renegade", date(2026, 9, 1), date(2026, 9, 28), "hook"),
    "entity_outcome": lambda: metrics.entity_outcome(
        "renegade", "campaign", "c1", date(2026, 9, 1), date(2026, 9, 14),
        date(2026, 9, 15), date(2026, 9, 28)),
    "arm_compare": lambda: metrics.arm_compare(
        "renegade", "a1", "a2", date(2026, 9, 15), date(2026, 9, 28), 500),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_without_018_every_wrapper_is_unavailable(monkeypatch, name):
    _stub(monkeypatch, errors.UndefinedFunction(
        f"function ads.{name} does not exist"))
    out = _run(CALLS[name]())
    assert out == {"available": False,
                   "why": "migration 018 is not applied "
                          "(python ads_migrate.py --status)"}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_refusal_from_018_is_one_sentence(monkeypatch, name):
    _stub(monkeypatch, errors.RaiseException(
        "windows overlap: 2026-09-01 .. 2026-09-14 share days\n"
        "HINT:  Take both windows from ads.analysis_windows."))
    with pytest.raises(ValueError) as exc:
        _run(CALLS[name]())
    assert str(exc.value) == ("windows overlap: 2026-09-01 .. 2026-09-14 "
                              "share days")


# ---------------------------------------------------------------------------
# facet_by_goal
# ---------------------------------------------------------------------------

def test_facet_by_goal_casts_and_decodes(monkeypatch):
    rows = [{"optimization_goal": "LEAD_GENERATION", "value": "problem",
             "ads_run": 3, "spend": 900.0, "conversions": 30,
             "rates": json.dumps({"cpa": 30.0}), "goal_cpa": 25.0,
             "relative_pct": 20.0, "relative": "worse",
             "evidence_level": "moderate", "spend_share_pct": 60.0,
             "mixed_currency": False},
            {"optimization_goal": "LEAD_GENERATION", "value": None,
             "ads_run": 9, "spend": 600.0, "conversions": 30,
             "rates": {"cpa": 20.0}, "goal_cpa": 25.0,
             "relative_pct": -20.0, "relative": None,
             "evidence_level": "moderate", "spend_share_pct": 40.0,
             "mixed_currency": False}]
    calls = _stub(monkeypatch, rows)
    out = _run(metrics.facet_by_goal("renegade", date(2026, 9, 1),
                                     date(2026, 9, 28), "hook"))
    sql, params = calls[0]
    assert "ads.facet_by_goal(%s::uuid, %s::date, %s::date, %s::text)" in sql
    assert params == (BRAND["id"], date(2026, 9, 1), date(2026, 9, 28), "hook")
    assert out["available"] is True and out["dimension"] == "hook"
    assert out["rows"][0]["rates"] == {"cpa": 30.0}, "jsonb text is decoded"
    assert out["rows"][1]["rates"] == {"cpa": 20.0}
    assert out["rows"][1]["value"] is None, "the untagged bucket is kept"
    assert out["rows"][0]["relative"] == "worse"


def test_facet_by_goal_refuses_an_unknown_dimension(monkeypatch):
    calls = _stub(monkeypatch, [])
    with pytest.raises(ValueError, match="unknown dimension"):
        _run(metrics.facet_by_goal("renegade", date(2026, 9, 1),
                                   date(2026, 9, 28), "headline"))
    assert calls == [], "refused before any SQL"


# ---------------------------------------------------------------------------
# entity_outcome
# ---------------------------------------------------------------------------

def test_entity_outcome_is_the_funnel_row_after_against_before(monkeypatch):
    row = {"settled_through": date(2026, 9, 28), "level": "campaign",
           "entity_key": "c1", "entity_name": "Become an Agent",
           "cur_m": json.dumps({"spend": 700, "rates": {"cpa": 35.0}}),
           "pri_m": {"spend": 600, "rates": {"cpa": 30.0}},
           "pct": json.dumps({"cpa": 16.67}),
           "evidence_level": "directional", "after_settled_days": 14}
    calls = _stub(monkeypatch, [row])
    out = _run(metrics.entity_outcome(
        "renegade", "campaign", "c1", date(2026, 9, 1), date(2026, 9, 14),
        date(2026, 9, 15), date(2026, 9, 28)))

    sql, params = calls[0]
    assert "ads.settled_through(%s::uuid, now())" in sql
    assert "ads.entity_outcome(" in sql and "e.settled" in sql
    assert "%s::text, %s::uuid" in sql
    assert params == (BRAND["id"], BRAND["id"], "campaign", "c1",
                      date(2026, 9, 1), date(2026, 9, 14),
                      date(2026, 9, 15), date(2026, 9, 28))

    assert out["available"] is True
    assert out["settled_through"] == date(2026, 9, 28)
    r = out["row"]
    assert "settled_through" not in r
    assert r["cur_m"]["rates"]["cpa"] == 35.0
    assert r["pct"] == {"cpa": 16.67}
    assert r["after_settled_days"] == 14


def test_entity_outcome_with_no_row_still_says_where_the_edge_is(monkeypatch):
    _stub(monkeypatch, [{"settled_through": date(2026, 9, 28),
                         "level": None, "entity_key": None}])
    out = _run(metrics.entity_outcome(
        "renegade", "ad", "a9", date(2026, 9, 1), date(2026, 9, 14),
        date(2026, 9, 15), date(2026, 9, 28)))
    assert out["row"] is None
    assert out["settled_through"] == date(2026, 9, 28)


def test_entity_outcome_refuses_adset(monkeypatch):
    _stub(monkeypatch, [])
    with pytest.raises(ValueError, match="ad_group"):
        _run(metrics.entity_outcome(
            "renegade", "adset", "x", date(2026, 9, 1), date(2026, 9, 14),
            date(2026, 9, 15), date(2026, 9, 28)))


# ---------------------------------------------------------------------------
# arm_compare
# ---------------------------------------------------------------------------

def test_arm_compare_casts_the_decision_spend_and_decodes(monkeypatch):
    row = {"settled_through": date(2026, 9, 28),
           "control_m": json.dumps({"spend": 600, "conversions": 20,
                                    "rates": {"cpa": 30.0}}),
           "challenger_m": {"spend": 550, "conversions": 22,
                            "rates": {"cpa": 25.0}},
           "effect_pct": json.dumps({"cpa": -16.67, "link_ctr": 5.0}),
           "evidence_level": "moderate", "spend_sufficient": True,
           "settled_days": 14}
    calls = _stub(monkeypatch, [row])
    out = _run(metrics.arm_compare("renegade", "a1", "a2",
                                   date(2026, 9, 15), date(2026, 9, 28),
                                   decision_spend=500))
    sql, params = calls[0]
    assert "ads.arm_compare(" in sql
    assert "%s::uuid, %s::uuid, %s::uuid" in sql
    assert "e.settled, %s::numeric" in sql, "the spend bar is cast to numeric"
    assert params == (BRAND["id"], BRAND["id"], "a1", "a2",
                      date(2026, 9, 15), date(2026, 9, 28), 500)
    r = out["row"]
    assert r["control_m"]["rates"]["cpa"] == 30.0
    assert r["challenger_m"]["conversions"] == 22
    assert r["effect_pct"]["cpa"] == -16.67
    assert r["spend_sufficient"] is True
    assert out["settled_through"] == date(2026, 9, 28)


def test_arm_compare_without_a_bar_passes_null(monkeypatch):
    calls = _stub(monkeypatch, [{"settled_through": None, "control_m": {},
                                 "challenger_m": {}, "effect_pct": {},
                                 "evidence_level": "insufficient",
                                 "spend_sufficient": None,
                                 "settled_days": None}])
    out = _run(metrics.arm_compare("renegade", "a1", "a2",
                                   date(2026, 9, 15), date(2026, 9, 28)))
    assert calls[0][1][-1] is None
    assert out["row"]["spend_sufficient"] is None


# ---------------------------------------------------------------------------
# The wrappers select and decode. They never compute.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["goal_impact", "facet_by_goal",
                                  "entity_outcome", "arm_compare"])
def test_the_wrapper_does_no_arithmetic(name):
    src = textwrap.dedent(inspect.getsource(getattr(metrics, name)))
    tree = ast.parse(src)
    ops = [n for n in ast.walk(tree)
           if isinstance(n, (ast.BinOp, ast.AugAssign))]
    assert not ops, (f"metrics.{name} does arithmetic "
                     f"({ast.unparse(ops[0])}); the figure belongs in 018")
