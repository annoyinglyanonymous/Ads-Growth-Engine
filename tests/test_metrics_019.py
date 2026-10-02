"""The 019 wrappers in intel/metrics.py, against a stubbed fetch.

No database: each test replaces metrics.fetch_all and context.brand, then
checks what the wrapper asked for (every argument cast to 019's declared
type, one round trip) and what it made of the answer (selected, never
computed).
"""

from __future__ import annotations

import ast
import asyncio
import inspect
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

BAND_ROW = {"family": "m_and_a", "audience": None,
            "optimization_goal": "LEAD_GENERATION", "metric": "cpa",
            "p25": 28.0, "p50": 31.5, "p75": 36.0, "campaign_weeks": 40,
            "campaigns": 3, "since": date(2026, 3, 24),
            "until": date(2026, 9, 21), "currency": "USD", "ordinality": 1}

AUDIENCE_BAND_ROW = {**BAND_ROW, "audience": "prospecting", "p25": 27.0,
                     "campaign_weeks": 22, "campaigns": 2, "ordinality": 2}

VS_ROW = {"campaign_key": "c1", "campaign_name": "Renegade | M&A | Open Audience",
          "family": "m_and_a", "audience": "prospecting",
          "optimization_goal": "LEAD_GENERATION", "metric": "cpa",
          "cur": 44.67, "p25": 27.0, "p50": 31.5, "p75": 36.0,
          "position": "above", "percentile": 95.45, "campaign_weeks": 22,
          "band_level": "family_audience", "evidence_level": "moderate",
          "ordinality": 1}


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
        raise AssertionError(f"019 wrappers make one round trip: {sql[:80]}")

    monkeypatch.setattr(metrics.context, "brand", fake_brand)
    monkeypatch.setattr(metrics, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(metrics, "fetch_one", no_fetch_one)
    return calls


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# family_baseline
# ---------------------------------------------------------------------------

def test_family_baseline_defaults_to_the_day_before_last_7(monkeypatch):
    before = date(2026, 9, 21)
    calls = _stub(monkeypatch, [{"before_day": before, **BAND_ROW},
                                {"before_day": before, **AUDIENCE_BAND_ROW}])
    out = _run(metrics.family_baseline("renegade"))

    assert len(calls) == 1, "one round trip"
    sql, params = calls[0]
    assert "coalesce(%s::date" in sql
    assert "ads.analysis_windows(%s::uuid, now())" in sql
    assert "w.pri_until" in sql and "w.pair = %s::text" in sql
    assert "ads.family_baseline(" in sql and "%s::uuid, ok.before_day" in sql
    assert params == (None, BRAND["id"], "last_7_vs_prior_7", BRAND["id"])

    assert out["available"] is True
    assert out["before"] == before
    assert [r["audience"] for r in out["rows"]] == [None, "prospecting"]
    for row in out["rows"]:
        assert "ordinality" not in row and "before_day" not in row
    # Every figure is the function's, passed through untouched.
    assert out["rows"][0]["p25"] == 28.0
    assert out["rows"][1]["campaign_weeks"] == 22


def test_family_baseline_passes_an_explicit_day(monkeypatch):
    calls = _stub(monkeypatch, [{"before_day": date(2026, 8, 31), **BAND_ROW}])
    out = _run(metrics.family_baseline("renegade", before=date(2026, 8, 31)))
    assert calls[0][1][0] == date(2026, 8, 31)
    assert out["before"] == date(2026, 8, 31)
    assert len(out["rows"]) == 1


def test_family_baseline_with_no_band_keeps_the_day(monkeypatch):
    empty = {k: None for k in BAND_ROW}
    _stub(monkeypatch, [{"before_day": date(2026, 9, 21), **empty}])
    out = _run(metrics.family_baseline("renegade"))
    assert out["available"] is True
    assert out["rows"] == []
    assert out["before"] == date(2026, 9, 21)


def test_family_baseline_with_no_anchor_says_so(monkeypatch):
    _stub(monkeypatch, [])
    out = _run(metrics.family_baseline("renegade"))
    assert out == {"available": True, "brand": "renegade",
                   "before": None, "rows": []}


# ---------------------------------------------------------------------------
# campaign_vs_family
# ---------------------------------------------------------------------------

def test_campaign_vs_family_takes_its_window_from_analysis_windows(monkeypatch):
    ctr = {**VS_ROW, "metric": "link_ctr", "cur": 0.9, "p25": 0.8,
           "p50": 1.0, "p75": 1.2, "position": "within",
           "percentile": 31.82, "ordinality": 2}
    calls = _stub(monkeypatch, [{**WINDOW, **VS_ROW}, {**WINDOW, **ctr}])
    out = _run(metrics.campaign_vs_family("renegade"))

    assert len(calls) == 1, "one round trip"
    sql, params = calls[0]
    assert "ads.analysis_windows(%s::uuid, now())" in sql
    assert "ads.campaign_vs_family(" in sql
    assert "w.cur_since, w.cur_until" in sql and "w.settled_edge" in sql
    assert "w.pri_since" not in sql.split("ads.campaign_vs_family(", 1)[1], (
        "the band is anchored on the current window; the prior side is not "
        "an argument")
    assert "w.pair = %s::text" in sql
    assert params == (BRAND["id"], BRAND["id"], "last_7_vs_prior_7")

    assert out["available"] is True
    assert out["pair"] == "last_7_vs_prior_7"
    assert out["settled_through"] == date(2026, 9, 28)
    assert out["windows"]["cur_since"] == date(2026, 9, 22)
    assert "pair" not in out["windows"] and "settled_edge" not in out["windows"]
    assert [r["metric"] for r in out["rows"]] == ["cpa", "link_ctr"]
    for row in out["rows"]:
        assert "ordinality" not in row
        assert "cur_since" not in row and "settled_edge" not in row
    assert out["rows"][0]["position"] == "above"
    assert out["rows"][0]["percentile"] == 95.45
    assert out["rows"][0]["band_level"] == "family_audience"


def test_campaign_vs_family_with_no_campaign_keeps_the_dates(monkeypatch):
    empty = {k: None for k in VS_ROW}
    _stub(monkeypatch, [{**WINDOW, **empty}])
    out = _run(metrics.campaign_vs_family("renegade",
                                          pair="last_14_vs_prior_14"))
    assert out["available"] is True
    assert out["rows"] == []
    assert out["windows"]["cur_days"] == 7


def test_campaign_vs_family_with_no_pair_says_so(monkeypatch):
    _stub(monkeypatch, [])
    out = _run(metrics.campaign_vs_family("renegade"))
    assert out["available"] is True
    assert out["windows"] is None and out["rows"] == []


def test_campaign_vs_family_refuses_an_unknown_pair(monkeypatch):
    calls = _stub(monkeypatch, [])
    with pytest.raises(ValueError, match="unknown pair"):
        _run(metrics.campaign_vs_family("renegade", pair="last_3_vs_prior_3"))
    assert calls == [], "refused before any SQL"


# ---------------------------------------------------------------------------
# Both degrade before 019 is applied, and neither crashes on a refusal.
# ---------------------------------------------------------------------------

CALLS = {
    "family_baseline": lambda: metrics.family_baseline("renegade"),
    "campaign_vs_family": lambda: metrics.campaign_vs_family("renegade"),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_without_019_every_wrapper_is_unavailable(monkeypatch, name):
    _stub(monkeypatch, errors.UndefinedFunction(
        f"function ads.{name} does not exist"))
    out = _run(CALLS[name]())
    assert out == {"available": False,
                   "why": "migration 019 is not applied "
                          "(python ads_migrate.py --status)"}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_a_refusal_from_019_is_one_sentence(monkeypatch, name):
    _stub(monkeypatch, errors.RaiseException(
        "p_weeks must be between 1 and 260, got 0\n"
        "HINT:  something longer"))
    with pytest.raises(ValueError) as exc:
        _run(CALLS[name]())
    assert str(exc.value) == "p_weeks must be between 1 and 260, got 0"


# ---------------------------------------------------------------------------
# The wrappers select. They never compute.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["family_baseline", "campaign_vs_family"])
def test_the_wrapper_does_no_arithmetic(name):
    src = textwrap.dedent(inspect.getsource(getattr(metrics, name)))
    tree = ast.parse(src)
    # `date | None` in a signature is a type, not a sum.
    typing = {id(n) for a in ast.walk(tree) if isinstance(a, ast.arg)
              and a.annotation is not None for n in ast.walk(a.annotation)}
    ops = [n for n in ast.walk(tree)
           if isinstance(n, (ast.BinOp, ast.AugAssign)) and id(n) not in typing]
    assert not ops, (f"metrics.{name} does arithmetic "
                     f"({ast.unparse(ops[0])}); the figure belongs in 019")
