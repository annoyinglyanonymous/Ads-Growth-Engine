"""The suggestions pack never compares two things measured differently.

`/suggestions` hands a model the account's copy next to what it cost, and asks
for creative ideas. The one way that produces confident nonsense is a ranking
that crosses an optimization goal: LEAD_GENERATION and OFFSITE_CONVERSIONS do
not count the same event, so a cost from one read against a cost from the other
is a measurement artefact -- and a model asked about the COPY will then explain
that artefact in terms of the writing.

So the grouping is the feature, and these tests are about the grouping.

None of these need a database: `_creative_pack`'s inputs are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from fastapi.encoders import jsonable_encoder

import ui
from intel import creative

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures shaped like what ads.window_metrics actually returns.
# ---------------------------------------------------------------------------

def _ad(key: str, name: str, goal: str, spend: float,
        conv: int | None, cpa: float | None) -> dict:
    return {
        "entity_key": key, "entity_name": name, "optimization_goal": goal,
        "spend": spend, "conversions": conv, "format": "dynamic",
        "effective_status": "ACTIVE",
        # `rates` arrives as a jsonb object from ads.rate. A null cpa is
        # undefined, not zero, and the key may simply be absent.
        "rates": {"cpa": cpa, "link_ctr": 1.1} if cpa is not None
                 else {"cpa": None, "link_ctr": 0.6},
    }


ROWS = [
    _ad("a1", "Become an Agent | Start Franchise", "LEAD_GENERATION", 4984, 166, 30.02),
    _ad("a2", "20 Years Experience | UGC | Video", "LEAD_GENERATION", 2799, 144, 19.44),
    _ad("a3", "Underselling | Video | UGC", "LEAD_GENERATION", 1436, 12, 119.67),
    _ad("a4", "Captive Agents | Static", "LEAD_GENERATION", 900, None, None),
    _ad("a5", "Sell Agency | Static", "LEAD_GENERATION", 1771, 14, 126.50),
    _ad("a6", "Backend Support | UGC", "LEAD_GENERATION", 1489, 25, 59.56),
    _ad("a7", "Retirement Decade | Video", "LEAD_GENERATION", 1336, 12, 111.33),
    _ad("a8", "Own a P&C Agency | Static", "LEAD_GENERATION", 2494, 150, 16.63),
    # A different goal, and DEARER in absolute terms than everything above.
    # If the pack ever flattens the goals, this row is the one that proves it:
    # it would be reported as the account's worst ad, when it is simply
    # measuring a different event.
    _ad("b1", "A Clean Exit | Static", "OFFSITE_CONVERSIONS", 772, 6, 128.67),
    _ad("b2", "We Buy P&C Agencies", "OFFSITE_CONVERSIONS", 1049, 28, 37.46),
    # A goal where NOTHING converted. THRUPLAY is the live case: 16 ads,
    # $1,032, not one conversion.
    _ad("c1", "Podcast Teaser | Video", "THRUPLAY", 746, 0, None),
    _ad("c2", "GIF | Building a Book", "THRUPLAY", 634, 0, None),
]

FORMATS = [
    {"value": "dynamic", "ads_run": 166, "spend": 14856, "conversions": 355,
     "rates": {"cpa": 41.85}, "optimization_goals": ["LEAD_GENERATION",
     "QUALITY_LEAD", "OFFSITE_CONVERSIONS", "THRUPLAY"],
     "optimization_goal_count": 4, "comparable_on_cost": False,
     "spend_sufficient": True, "rank_within_goal": None},
    {"value": "carousel", "ads_run": 6, "spend": 467, "conversions": 15,
     "rates": {"cpa": 31.17}, "optimization_goals": ["LEAD_GENERATION",
     "QUALITY_LEAD"], "optimization_goal_count": 2,
     "comparable_on_cost": False, "spend_sufficient": True,
     "rank_within_goal": None},
]

COPY = [
    # `cta` is the column on ads.ad; `cta_text` is what parse.creative_texts
    # lifted out of a dynamic ad's asset_feed_spec. Most ads here have only
    # the second, so the pack must prefer it.
    {"ad_key": k, "first_headline": f"Headline for {k}",
     "first_body": f"Body copy for {k}, which is a sentence of some length.",
     "headlines": 2, "bodies": 2,
     "cta": None if k.startswith("a") else "LEARN_MORE",
     "cta_text": "Get offer" if k.startswith("a") else None,
     # What scripts/tag.py filed, read through ads.facet_effective.
     "hook": "callout" if k.startswith("a") else "question",
     "offer": "consultation", "format": "dynamic"}
    # Every key in ROWS. A missing row here is not a harmless gap: _shape()
    # falls back to an empty dict, so the ad reaches the model with no
    # headline and no body -- a creative suggestion about an ad whose copy was
    # silently dropped.
    for k in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8",
              "b1", "b2", "c1", "c2")
]


# Campaign X holds two LEAD_GENERATION ad groups; campaign Y spans two goals,
# which is the case whose campaign-level CPA must be withheld.
def _grp(key, name, goal, spend, conv, cpa):
    return {"entity_key": key, "entity_name": name, "optimization_goal": goal,
            "spend": spend, "conversions": conv,
            "rates": {"cpa": cpa, "link_ctr": 1.0}}


GROUP_ROWS = [
    _grp("g1", "X cheap", "LEAD_GENERATION", 5000, 300, 16.0),
    _grp("g2", "X dear", "LEAD_GENERATION", 2000, 20, 100.0),
    _grp("g3", "Y leads", "LEAD_GENERATION", 1500, 30, 50.0),
    _grp("g4", "Y offsite", "OFFSITE_CONVERSIONS", 1800, 34, 52.0),
    _grp("g5", "Y views", "THRUPLAY", 700, 0, None),
]
GROUP_META = [
    {"ad_group_key": "g1", "campaign_key": "X", "effective_status": "ACTIVE"},
    {"ad_group_key": "g2", "campaign_key": "X", "effective_status": "ACTIVE"},
    {"ad_group_key": "g3", "campaign_key": "Y", "effective_status": "ACTIVE"},
    {"ad_group_key": "g4", "campaign_key": "Y", "effective_status": "ACTIVE"},
    {"ad_group_key": "g5", "campaign_key": "Y", "effective_status": "PAUSED"},
]
CAMPAIGN_ROWS = [
    {"entity_key": "X", "entity_name": "Campaign X", "optimization_goal": None,
     "spend": 7000, "conversions": 320, "rates": {"cpa": 21.9, "link_ctr": 1.2}},
    {"entity_key": "Y", "entity_name": "Campaign Y", "optimization_goal": None,
     "spend": 4000, "conversions": 64, "rates": {"cpa": 62.5, "link_ctr": 0.9}},
]
CAMPAIGN_META = [
    {"campaign_key": "X", "effective_status": "ACTIVE", "objective": "LEADS"},
    {"campaign_key": "Y", "effective_status": "ACTIVE", "objective": "LEADS"},
]
AD_CAMPAIGN = [{"ad_key": k, "campaign_key": "X" if k.startswith("a") else "Y"}
               for k in ("a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8",
                         "b1", "b2", "c1", "c2")]

CONCENTRATION = [
    {"campaign_key": "X", "ads_with_spend": 8, "top_ad_spend": 4984,
     "top_two_ads_spend": 7783, "top_ad_share_pct": 71.2,
     "top_two_ads_share_pct": 99.0, "ads_with_no_conversions": 1,
     "spend_on_ads_with_no_conversions": 900,
     "share_on_ads_with_no_conversions_pct": 12.86}]

UNTAGGED = {"total_spend": 60163, "tagged_spend": 0, "untagged_ads": 245,
            "untagged_spend": 60163, "stale_tag_ads": 0, "stale_tag_spend": 0}


# ---------------------------------------------------------------------------
# The funnel ladder (migration 017), shaped like metrics.funnel_ladder's
# answer (contracts C2). Every entity above gets a row on every pair; the
# 28-day side carries the figures above, so the old tests keep their meaning.
# ---------------------------------------------------------------------------

P7, P14, P28 = "last_7_vs_prior_7", "last_14_vs_prior_14", "last_28_vs_prior_28"
PU, PB = "unsettled_vs_last_7", "last_7_vs_before"

WINDOWS = {
    PU: ("2026-09-19", "2026-09-21", "2026-09-12", "2026-09-18"),
    P7: ("2026-09-12", "2026-09-18", "2026-09-05", "2026-09-11"),
    P14: ("2026-09-05", "2026-09-18", "2026-08-22", "2026-09-04"),
    P28: ("2026-08-22", "2026-09-18", "2026-07-25", "2026-08-21"),
    PB: ("2026-09-12", "2026-09-18", "2026-06-01", "2026-09-11"),
}

#: A steady week: every change inside diagnose's steady band.
STEADY_PCT = {"spend": 3.0, "conversions": 1.0, "cpm": 2.0, "link_ctr": -1.0,
              "cost_per_link_click": 1.0, "conversion_rate": 0.5, "cpa": 1.0}


def _m(spend, conv, cpa, ctr=1.0, cpm=20.0, cvr=5.0, freq=None):
    m = {"window_days": 7, "days_active": 7, "spend": spend,
         "conversions": conv,
         "rates": {"cpa": cpa, "cpm": cpm, "link_ctr": ctr,
                   "conversion_rate": cvr, "cost_per_link_click": 2.0}}
    if freq is not None:
        m["daily_frequency"] = freq
    return m


def _frow(level, key, name, camp, group, goal, cur, pri, pct=None, **over):
    """One ads.funnel_change row (C1)."""
    p = dict(STEADY_PCT)
    if level == "ad":
        p["daily_frequency"] = 1.0
    p.update(pct or {})
    r = {"level": level, "entity_key": key, "entity_name": name,
         "entity_status": "ACTIVE", "campaign_key": camp,
         "campaign_name": f"Campaign {camp}", "ad_group_key": group,
         "ad_group_name": group, "parent_key": group if level == "ad" else camp,
         "optimization_goal": goal,
         "optimization_goal_count": 1 if goal else 3, "equal_length": True,
         "cur_unsettled_days": 0, "cur_m": cur, "pri_m": pri,
         "pct": {k: v for k, v in p.items() if v is not None},
         "cpa_direction": "flat", "decomposition": "cpa_flat",
         "cpa_share_cpm_pct": None, "cpa_share_link_ctr_pct": None,
         "cpa_share_conversion_rate_pct": None, "primary_driver": None,
         "spend_to_baseline_cpa": 4.0, "baseline_cpa": 40.0,
         "decision_spend_per_arm": 200.0, "evidence_level": "moderate",
         "click_evidence_level": "moderate", "parent_spend_share_pct": 50.0,
         "spend_rank_in_parent": 1,
         "fatigue_consistent": False if level == "ad" else None,
         "fatigue_pattern": "none" if level == "ad" else None,
         "appeared": False, "disappeared": False}
    r.update(over)
    return r


def _entities():
    """(level, key, name, campaign, ad group, goal, spend, conv, cpa) for
    every entity in the fixtures above, at the 28-day side."""
    group_of_ad = {**{k: "g1" for k in ("a1", "a2", "a3", "a4")},
                   **{k: "g2" for k in ("a5", "a6", "a7", "a8")},
                   "b1": "g4", "b2": "g4", "c1": "g5", "c2": "g5"}
    camp_of_group = {m["ad_group_key"]: m["campaign_key"] for m in GROUP_META}
    out = [("campaign", c["entity_key"], c["entity_name"], c["entity_key"],
            None, "LEAD_GENERATION" if c["entity_key"] == "X" else None,
            c["spend"], c["conversions"], c["rates"]["cpa"])
           for c in CAMPAIGN_ROWS]
    out += [("ad_group", g["entity_key"], g["entity_name"],
             camp_of_group[g["entity_key"]], g["entity_key"],
             g["optimization_goal"], g["spend"], g["conversions"],
             g["rates"]["cpa"]) for g in GROUP_ROWS]
    out += [("ad", a["entity_key"], a["entity_name"],
             camp_of_group[group_of_ad[a["entity_key"]]],
             group_of_ad[a["entity_key"]], a["optimization_goal"],
             a["spend"], a["conversions"] or 0, a["rates"]["cpa"])
            for a in ROWS]
    return out


def fake_ladder(entities=None, override=None, drop=()):
    """A C2 answer. `override` maps (pair, entity key) -> row kwargs;
    `drop` lists (pair, entity key) rows to leave out."""
    entities = _entities() if entities is None else entities
    override = override or {}
    pairs = {}
    for pair, (cs, cu, ps, pu) in WINDOWS.items():
        rows = []
        for level, key, name, camp, group, goal, spend, conv, cpa in entities:
            if (pair, key) in drop or (pair == PU and level != "campaign"):
                continue
            share = {P7: 4, P14: 2}.get(pair, 1)
            cur = _m(round(spend / share, 2), conv // share, cpa,
                     freq=1.1 if level == "ad" else None)
            pri = _m(round(spend / share, 2), conv // share, cpa,
                     freq=1.1 if level == "ad" else None)
            if pair == PB:
                pri = _m(spend * 3, conv * 3, cpa)
            if pair == PU:
                cur = _m(round(spend / 10, 2), conv // 10, cpa)
            kw = dict(override.get((pair, key)) or {})
            rows.append(_frow(level, key, name, camp, group, goal,
                              kw.pop("cur_m", cur), kw.pop("pri_m", pri),
                              **kw))
        pairs[pair] = {"cur_since": cs, "cur_until": cu, "pri_since": ps,
                       "pri_until": pu, "cur_days": 7, "pri_days": 7,
                       "unsettled_days": 3, "rows": rows}
    return {"available": True, "brand": "renegade",
            "settled_through": "2026-09-18", "pairs": pairs}


def _pack(monkeypatch, rows=None, formats=None, ladder=None, copy=None):
    """Run creative_pack with every database call stubbed."""
    rows = ROWS if rows is None else rows
    formats = FORMATS if formats is None else formats
    ladder = fake_ladder() if ladder is None else ladder

    async def fake_funnel_ladder(slug, level=None, pairs=None, **thresholds):
        assert level is None and pairs is None, (
            "the pack reads every level and every pair in one round trip")
        return ladder

    async def fake_overview(slug, days, until, level="ad", limit=200):
        by_level = {"ad": rows, "campaign": CAMPAIGN_ROWS,
                    "ad_group": GROUP_ROWS}
        return {"verb": "overview", "brand": slug, "brand_id": "B",
                "since": "2026-08-25", "until": "2026-09-21", "days": days,
                "settled_through": "2026-09-18", "unsettled_days": 3,
                "rows": by_level[level], "row_count": len(by_level[level])}

    async def fake_fatigue(slug, days, until, min_spend, unconfident):
        return {"rows": [{"ad_key": "a1", "entity_name": "Become an Agent",
                          "optimization_goal": "LEAD_GENERATION", "score": 2,
                          "confident": True, "spend_recent": 1436.05}]}

    async def fake_fetch_all(sql, params=()):
        # creative_pack calls facet_performance once per dimension now, so the
        # stub answers by SQL rather than by call order.
        if "facet_performance" in sql:
            return formats
        if "campaign_concentration" in sql:
            return CONCENTRATION
        if "from ads.ad_group" in sql:
            return GROUP_META
        if "from ads.campaign" in sql:
            return CAMPAIGN_META
        if "from ads.ad " in sql:
            return AD_CAMPAIGN
        if "from ads.ad_copy" in sql:
            return COPY if copy is None else copy
        # The default used to be COPY, so a query nobody stubbed -- the
        # funnel ladder, say -- quietly came back as ad copy. Fail instead.
        raise AssertionError(f"unstubbed query in creative_pack: {sql[:120]}")

    async def fake_fetch_one(sql, params=()):
        # creative_pack asks two coverage questions now. Answer by SQL rather
        # than by call order, so a reordering does not silently swap them.
        if "facet_effective" in sql:
            return {"ads": 243, "with_a_facet": 243, "with_a_hook": 62,
                    "with_an_offer": 243}
        return UNTAGGED

    async def fake_latest_day(slug):
        # creative_pack defaults its window end to the latest day with data, so
        # the page and scripts/suggest.py describe the same window. Unstubbed,
        # that one call reaches the database and every test here dies on a
        # PoolTimeout that looks like a connection problem and is not one.
        return None

    monkeypatch.setattr(creative.context, "latest_day", fake_latest_day)
    monkeypatch.setattr(creative.metrics, "overview", fake_overview)
    monkeypatch.setattr(creative.metrics, "fatigue", fake_fatigue)
    monkeypatch.setattr(creative.metrics, "funnel_ladder", fake_funnel_ladder)
    monkeypatch.setattr(creative, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(creative, "fetch_one", fake_fetch_one)
    return asyncio.run(creative.creative_pack("renegade", 28, None))


# ---------------------------------------------------------------------------
# The grouping.
# ---------------------------------------------------------------------------

def test_every_goal_gets_its_own_block(monkeypatch):
    pack = _pack(monkeypatch)
    goals = {g["optimization_goal"] for g in pack["goals"]}
    assert goals == {"LEAD_GENERATION", "OFFSITE_CONVERSIONS", "THRUPLAY"}


def test_no_ranking_ever_crosses_an_optimization_goal(monkeypatch):
    """The test this file exists for.

    `b1` costs more per result than every LEAD_GENERATION ad. If the pack
    flattened the goals it would be reported as the account's worst ad, and a
    model would explain that in terms of its copy.
    """
    pack = _pack(monkeypatch)
    for g in pack["goals"]:
        named = [r["ad"] for r in g["cheapest"] + g["dearest"] + g["by_spend_only"]]
        for row in ROWS:
            if row["entity_name"] in named:
                assert row["optimization_goal"] == g["optimization_goal"], (
                    f"{row['entity_name']!r} ({row['optimization_goal']}) "
                    f"appears under {g['optimization_goal']}")


def test_a_goal_that_never_converted_is_not_ranked(monkeypatch):
    """A null CPA is undefined, not zero. THRUPLAY converts nothing, so there
    is no cost ranking to read -- its ads are there for their copy only."""
    pack = _pack(monkeypatch)
    thru = next(g for g in pack["goals"]
                if g["optimization_goal"] == "THRUPLAY")
    assert thru["rankable_on_cost"] is False
    assert thru["ranked_on"] is None
    assert thru["cheapest"] == [] and thru["dearest"] == []
    assert thru["by_spend_only"], "the ads should still be readable for copy"
    assert thru["ads_with_no_conversions"] == 2


def test_an_ad_with_no_conversions_is_counted_not_ranked(monkeypatch):
    """`a4` converted nothing inside a goal that otherwise did. It must be
    excluded from the ranking and reported, not silently dropped."""
    pack = _pack(monkeypatch)
    lg = next(g for g in pack["goals"]
              if g["optimization_goal"] == "LEAD_GENERATION")
    assert lg["ads_with_no_conversions"] == 1  # a4 only
    named = [r["ad"] for r in lg["cheapest"] + lg["dearest"]]
    assert "Captive Agents | Static" not in named


def test_the_cheapest_really_is_the_cheapest(monkeypatch):
    pack = _pack(monkeypatch)
    lg = next(g for g in pack["goals"]
              if g["optimization_goal"] == "LEAD_GENERATION")
    assert lg["cheapest"][0]["ad"] == "Own a P&C Agency | Static"     # 16.63
    assert lg["dearest"][0]["ad"] == "Sell Agency | Static"            # 126.50


def test_the_two_ends_never_share_an_ad(monkeypatch):
    """With four to six ranked ads a naive ranked[-3:] overlaps ranked[:3],
    and the same ad is handed to the model as both the cheapest and the
    dearest under one goal. It would then explain why an ad beats itself."""
    for n in range(1, 9):
        rows = [_ad(f"x{i}", f"Ad {i}", "LEAD_GENERATION", 100 * (i + 1),
                    5, 10.0 * (i + 1)) for i in range(n)]
        pack = _pack(monkeypatch, rows=rows)
        g = pack["goals"][0]
        cheap = {r["ad"] for r in g["cheapest"]}
        dear = {r["ad"] for r in g["dearest"]}
        assert not (cheap & dear), (
            f"{n} ranked ad(s): {sorted(cheap & dear)} is in both ends")
        assert len(cheap) + len(dear) <= n


def test_the_button_falls_back_to_the_parsed_cta(monkeypatch):
    """ads.ad.cta is null on 588 of this account's 713 ads, because a dynamic
    ad keeps its calls to action in asset_feed_spec. parse.creative_texts
    already extracts those; reading only the column shows "--" for every ad in
    the format carrying most of the spend."""
    pack = _pack(monkeypatch)
    lg = next(g for g in pack["goals"]
              if g["optimization_goal"] == "LEAD_GENERATION")
    assert all(r["cta_button"] == "Get offer" for r in lg["cheapest"])
    off = next(g for g in pack["goals"]
               if g["optimization_goal"] == "OFFSITE_CONVERSIONS")
    assert all(r["cta_button"] == "LEARN_MORE" for r in off["cheapest"])


def test_the_copy_rides_along(monkeypatch):
    """A creative suggestion without the creative is a performance report."""
    pack = _pack(monkeypatch)
    lg = next(g for g in pack["goals"]
              if g["optimization_goal"] == "LEAD_GENERATION")
    for row in lg["cheapest"]:
        assert row["headline"], "no headline reached the pack"
        assert row["body"], "no body reached the pack"
        assert row["cta_button"], "no CTA reached the pack"


# ---------------------------------------------------------------------------
# What the pack must not hide.
# ---------------------------------------------------------------------------

def test_a_refusal_to_rank_is_passed_through_not_dropped(monkeypatch):
    """`comparable_on_cost: false` is ads.facet_performance declining to rank
    two things measured differently. Dropping those rows would leave the model
    with a table it believes is rankable.

    The row is TRIMMED but the refusal survives. `optimization_goals` -- the
    array of goal names and their spend -- is no longer carried: it is ~200 of
    a ~500-character row, and what gates the comparison is the COUNT. "Ran
    under 4 optimization goals" is the whole argument for not comparing; which
    four they were does not change it, and four dimensions of untrimmed rows
    pushed the pack past its budget, where compact() would have cut the per-ad
    copy instead.
    """
    pack = _pack(monkeypatch)
    for dim, rows in pack["dimensions"].items():
        for f in rows:
            if f.get("comparable_on_cost") is None:
                continue          # the "... n more" tail row
            assert f["comparable_on_cost"] is False
            assert f["optimization_goal_count"] > 1, (
                f"{dim}: the reason for declining to rank is missing")
    assert "optimization_goals" not in pack["dimensions"]["format"][0], (
        "the goal-name array is back; it costs 200 chars a row and the count "
        "is what gates the comparison")


def test_every_creative_dimension_reaches_the_model(monkeypatch):
    """hook, offer and audience returned one NULL bucket until scripts/tag.py
    filled ads.ad_facet. They are the dimensions that make this a creative
    analysis rather than a list of ads, so a pack carrying only `format` is a
    pack that can only talk about the ad builder."""
    pack = _pack(monkeypatch)
    assert set(pack["dimensions"]) == {"hook", "offer", "audience", "format"}


def test_the_two_coverages_are_reported_separately(monkeypatch):
    """Conflating them cost a whole analysis.

    ads.untagged_spend counts an ad as tagged when an ANGLE resolves for it.
    ads.angle is empty here, so it reports 100% untagged however many hook and
    offer labels exist. Handed to the model beside a full `dimensions` block,
    it resolved the contradiction the wrong way and wrote "every ad is
    untagged, so nothing can be said about which kind of writing carries the
    money" -- suppressing the analysis the tagging pass exists to enable.
    """
    pack = _pack(monkeypatch)
    cov = pack["untagged"]
    assert set(cov) == {"angle_attribution", "creative_labels"}
    for half in cov.values():
        assert half["means"], "a coverage number with no stated question"
    assert "ANGLE" in cov["angle_attribution"]["means"]
    assert "hook" in cov["creative_labels"]["means"]


def test_the_prompt_says_which_coverage_to_read(monkeypatch):
    """The model has to be told that a zero in one is not evidence about the
    other, because on this database one of them is always zero."""
    p = creative.SUGGESTIONS_PROMPT
    assert "angle_attribution" in p and "creative_labels" in p
    assert "NOT evidence" in p


# ---------------------------------------------------------------------------
# It computes nothing.
# ---------------------------------------------------------------------------

def test_a_rate_is_read_never_derived():
    assert creative._rate({"rates": {"cpa": "30.02"}}) == 30.02
    assert creative._rate({"rates": {"cpa": None}}) is None
    assert creative._rate({"rates": {}}) is None
    assert creative._rate({}) is None
    # Never 0.0 for a missing rate: zero is a cost, absence is not.
    assert creative._rate({"rates": {"cpa": None}}) is not 0.0  # noqa: F632


def test_the_pack_totals_nothing(monkeypatch):
    """CLAUDE.md's first rule. The pack may count rows a function returned --
    `ads_run` is a len() -- but it must never add spend or average a rate."""
    pack = _pack(monkeypatch)
    for g in pack["goals"]:
        assert set(g) == {"optimization_goal", "ads_run", "rankable_on_cost",
                          "ads_with_no_conversions", "ranked_on",
                          "cheapest", "dearest", "by_spend_only"}, (
            f"{g['optimization_goal']} grew a field -- if it is a total, it "
            f"is arithmetic this module must not do")


# ---------------------------------------------------------------------------
# The prompt and the endpoint.
# ---------------------------------------------------------------------------

def test_the_prompt_forbids_crossing_a_goal():
    p = creative.SUGGESTIONS_PROMPT.lower()
    assert "optimization goal" in p
    assert "rankable_on_cost" in creative.SUGGESTIONS_PROMPT
    assert "comparable_on_cost" in creative.SUGGESTIONS_PROMPT


def test_the_prompt_asks_for_copy_it_can_be_checked_against():
    assert "quotation marks" in creative.SUGGESTIONS_PROMPT.lower()


def test_the_prompt_bans_verdicts():
    """Same stance tests/test_ad_readings.py enforces on the rules: this page
    suggests and decides nothing."""
    p = creative.SUGGESTIONS_PROMPT.lower()
    for word in ("pause this", "kill that", "winner", "underperform"):
        assert word in p, f"the prompt does not name {word!r} as forbidden"
    assert "suggest, never instruct" in p


def test_a_realistic_pack_fits_the_command_line(monkeypatch):
    """The question reaches the session as one argv element and Windows caps a
    command line at 32,767 characters. Measured against the live account the
    pack is ~16,300 at keep=8; this guards the shape, not the exact number."""
    pack = _pack(monkeypatch)
    facts = {k: pack[k] for k in ("goals", "formats", "untagged", "tiring")}
    text = json.dumps(jsonable_encoder(creative.compact(facts, 8)), indent=1,
                      ensure_ascii=False)
    assert len(text) < creative.PROMPT_BUDGET


def test_the_page_has_no_button_and_no_endpoint_to_press():
    """The change this feature exists to make.

    A suggestion somebody has to remember to ask for is a suggestion nobody
    asks for -- the same failure the scheduled pull exists to fix, one layer
    up. scripts/suggest.py publishes after every import, so the page renders a
    stored answer and there is nothing to click.
    """
    src = (ROOT / "ui.py").read_text(encoding="utf-8")
    assert '@router.get("/suggestions", response_class=HTMLResponse)' in src
    assert "/suggestions.json" not in src, (
        "the on-demand endpoint is still there; a button will grow back")

    html = (ROOT / "templates" / "suggestions.html").read_text(encoding="utf-8")
    assert "data-ask-url=" not in html, "the page still carries an ask button"
    assert not re.search(r"<script", html, re.I), "the page grew a script tag"
    assert not re.search(r"<button", html, re.I), "the page grew a button"


def test_the_page_says_so_when_nothing_has_been_published():
    """An empty page and a broken one look identical unless it says which."""
    html = (ROOT / "templates" / "suggestions.html").read_text(encoding="utf-8")
    assert "No suggestion has been published" in html
    assert "scripts/suggest.py" in html, (
        "the empty state must name the command that fills it")


def test_the_publisher_names_its_exit_codes():
    """n8n branches on these. 3 is "nothing to publish for", which is not a
    failure and must not page anybody."""
    src = (ROOT / "scripts" / "suggest.py").read_text(encoding="utf-8")
    for code in ("0  published", "1  something failed", "2  a run was already",
                 "3  nothing to publish"):
        assert code in src, f"exit code {code!r} is undocumented"


def test_a_failed_suggestion_does_not_fail_the_import():
    """The import either happened or it did not, and that is what a scheduler
    branches on. Folding a model call's failure into the pull's exit code would
    have somebody re-running a pull that worked."""
    src = (ROOT / "scripts" / "sync.py").read_text(encoding="utf-8")
    assert '_run_after("suggest.py"' in src
    # _run_after returns None: there is no path by which any follow-on
    # reaches `code`. It used to be three near-identical functions and is
    # one now, so this covers the tagging pass and the brief as well.
    assert "-> None:" in src.split("def _run_after(", 1)[1][:400], (
        "_run_after must not return a code, or somebody will wire it to one")


def test_the_suggestion_is_published_after_the_lock_is_released():
    """suggest.py takes its own lock and a model call is slow. Running it
    inside sync's lock would keep the next scheduled PULL out."""
    src = (ROOT / "scripts" / "sync.py").read_text(encoding="utf-8")
    unlink = src.find("LOCK.unlink(missing_ok=True)")
    call = src.find('_run_after("suggest.py"')
    assert unlink != -1 and call != -1
    assert unlink < call, "the suggestion runs while sync still holds its lock"


def test_the_rail_carries_it():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "'/suggestions'" in base, "the page is unreachable from the rail"
    icons = (ROOT / "templates" / "_icons.html").read_text(encoding="utf-8")
    assert "'suggest'" in icons, "the rail glyph falls back to 'overview'"


def test_every_pack_section_reaches_the_model(monkeypatch):
    """A section built and then not sent is work thrown away one line before
    it would have been used.

    scripts/suggest.py picked ("goals", "formats", "untagged", "tiring") and
    kept picking it after the pack gained `dimensions`. So hook, offer and
    audience -- the whole point of the tagging pass -- reached the pack and
    stopped there, and the model wrote "the hook and offer cut was not in what
    I was given" about data sitting in the caller's own variable.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "suggest_script", ROOT / "scripts" / "suggest.py")
    suggest = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules["suggest_script"] = suggest
    spec.loader.exec_module(suggest)

    pack = _pack(monkeypatch)
    frame = {"verb", "brand", "since", "until", "days", "settled_through",
             "unsettled_days", "funnel_available", "funnel_why"}
    missing = (set(pack) - frame - set(suggest.SENT_TO_THE_MODEL)
               - set(suggest.PAGE_ONLY))
    assert not missing, (
        f"the pack carries {sorted(missing)} and the publisher does not send "
        f"it. Either send it, name it PAGE_ONLY, or stop building it.")
    assert not set(suggest.PAGE_ONLY) & set(suggest.SENT_TO_THE_MODEL)
    # The 28-day fatigue read and the old per-goal copy list are the page's
    # now; the model reads the funnel ladder and the challenger pool.
    assert {"tiring", "goals"} <= set(suggest.PAGE_ONLY)
    assert {"challenger_pool", "windows"} <= set(suggest.SENT_TO_THE_MODEL)


# ---------------------------------------------------------------------------
# The campaign triage.
# ---------------------------------------------------------------------------

def test_every_campaign_gets_a_card(monkeypatch):
    pack = _pack(monkeypatch)
    assert [c["campaign"] for c in pack["campaigns"]] == ["Campaign X",
                                                          "Campaign Y"]


def test_a_campaign_spanning_two_goals_carries_no_cpa(monkeypatch):
    """window_metrics withholds the goal at campaign level; a campaign CPA
    across LEAD_GENERATION and OFFSITE_CONVERSIONS mixes two events."""
    pack = _pack(monkeypatch)
    x, y = pack["campaigns"]
    assert x["optimization_goals"] == ["LEAD_GENERATION"]
    assert x["cpa"] == 21.9
    assert len(y["optimization_goals"]) == 3
    assert y["cpa"] is None


def test_ad_groups_are_ranked_only_within_their_goal(monkeypatch):
    pack = _pack(monkeypatch)
    groups = {g["ad_group"]: g for c in pack["campaigns"]
              for g in c["ad_groups"]}
    assert groups["X cheap"]["cpa_rank_in_goal"] == 1
    assert groups["X dear"]["cpa_rank_in_goal"] == 3
    assert groups["X cheap"]["ad_groups_ranked_in_goal"] == 3
    # Alone in its goal: dearer than every lead group, and still rank 1.
    assert groups["Y offsite"]["cpa_rank_in_goal"] == 1
    assert groups["Y offsite"]["ad_groups_ranked_in_goal"] == 1
    assert groups["Y views"]["cpa_rank_in_goal"] is None


def test_a_card_carries_the_control_with_its_copy(monkeypatch):
    """The control is what a test keeps running, so it is the ad whose copy
    the model must see. One other ad carries copy for contrast; the rest are
    figures only."""
    pack = _pack(monkeypatch)
    x = pack["campaigns"][0]
    ads = x["where"]["ads"]
    assert len(ads) <= creative.CAMPAIGN_ADS
    control = next(a for a in ads if "control" in a["role"])
    # a1 has the most conversions in the last 7 days and has not deteriorated.
    assert control["name"] == "Become an Agent | Start Franchise"
    assert control["headline"] and control["opening"]
    assert len(control["opening"]) <= creative.CAMPAIGN_BODY_CHARS + 1
    assert control["hook"] == "callout" and control["offer"] == "consultation"
    with_copy = [a for a in ads if a.get("headline")]
    assert len(with_copy) <= 2
    assert not any(k.startswith("_") for a in ads for k in a)
    # No tiring list on a campaign any more: fatigue reaches the triage only
    # through the diagnosis, as 017's per-window legs.
    assert "tiring_ads" not in x and "leading_ads" not in x


def test_the_triage_keeps_every_campaign_and_checks_the_rating():
    camps = [{"campaign": "A", "spend": 10}, {"campaign": "B", "spend": 5},
             {"campaign": "C", "spend": 1}]
    reply = [{"campaign": "A", "rating": "RED", "why": "dear",
              "changes": ["x", ""]},
             {"campaign": "B", "rating": "purple", "why": "?"},
             {"campaign": "Z", "rating": "green", "why": "not asked"}]
    t = creative.triage_from(reply, camps)
    assert [r["campaign"] for r in t] == ["A", "B", "C"]
    assert [r["rating"] for r in t] == ["red", None, None]
    # The old flat `changes` still reads, as fixes with no stated problem.
    assert [(x["problem"], x["fix"]) for x in t[0]["problems"]] == [(None, "x")]
    assert "did not rate" in t[2]["why"]
    # The figures come from the pack, never the reply.
    assert t[0]["spend"] == 10


def test_the_campaign_list_is_never_compacted(monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "suggest_script2", ROOT / "scripts" / "suggest.py")
    suggest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suggest)
    pack = _pack(monkeypatch)
    pack["campaigns"] = pack["campaigns"] * 20      # 40 campaigns
    facts = jsonable_encoder({k: pack[k] for k in suggest.SENT_TO_THE_MODEL})
    q = suggest.build_question(pack, facts)
    assert q.count('"campaign":"Campaign X"') == 20


def test_the_prompt_asks_for_all_three_ratings():
    p = creative.SUGGESTIONS_PROMPT
    for r in creative.RATINGS:
        assert re.search(rf"^  {r} ", p, re.M), f"{r} is not defined"
    assert "exactly once" in p


def test_the_page_renders_the_cards_when_they_exist():
    html = (ROOT / "templates" / "_triage.html").read_text(encoding="utf-8")
    for r in ("red", "yellow", "green"):
        assert f"'{r}'" in html, f"{r} cards are never rendered"
    assert not re.search(r"<script|<button", html, re.I)


def test_every_problem_carries_its_fix():
    camps = [{"campaign": "A"}]
    reply = [{"campaign": "A", "rating": "red",
              "why": "dear.\n\nVery dear.",
              "problems": [
                  {"problem": "Opens on \"As a P&C agency owner,\n\nBut\"",
                   "fix": "Lead with the offer."},
                  {"problem": "", "fix": ""},
                  "not a dict"]}]
    t = creative.triage_from(reply, camps)[0]
    assert t["why"] == "dear. Very dear.", "a literal \n reached the card"
    assert [(x["problem"], x["fix"]) for x in t["problems"]] == [
        ('Opens on "As a P&C agency owner, But"', "Lead with the offer.")]


def test_the_prompt_pairs_each_observation_with_a_recommendation():
    p = creative.SUGGESTIONS_PROMPT
    for key in ('"observation"', '"evidence"', '"diagnosis"',
                '"recommendation"', '"expected_outcome"', '"test_plan"',
                '"confidence"', '"basis"', '"not_yet"', '"headline"'):
        assert key in p, key
    assert "every issue carries its own recommendation" in p.lower()


def test_the_groups_fold_without_a_script():
    """Twenty-four open cards is a wall. Each group is a <details> that
    opens on a click, which needs no script and no button."""
    html = (ROOT / "templates" / "_triage.html").read_text(encoding="utf-8")
    assert '<details class="tgroup' in html and "<summary>" in html
    assert "<details open" not in html, "a group starts open"
    assert "How to fix it" in html


# ---------------------------------------------------------------------------
# What happened to the last review's problems.
# ---------------------------------------------------------------------------

from datetime import date as _date

PREV = {"A": [{"id": "p0923-0-0", "problem": "No headline on the lead ad",
               "fix": "Add one", "open_since": "2026-09-22"},
              {"id": "p0923-0-1", "problem": "Ten ads with no conversions",
               "fix": "Narrow", "open_since": "2026-09-23"},
              {"id": "p0923-0-2", "problem": "Rocket opener",
               "fix": "Cut it", "open_since": "2026-09-23"}],
        "B": [{"id": "p0923-1-0", "problem": "B's own problem",
               "fix": "x", "open_since": "2026-09-23"}]}


def _reply(**over):
    r = {"campaign": "A", "rating": "red", "why": "w",
         "problems": [{"problem": "Still ten ads with nothing", "fix": "Narrow",
                       "continues": "p0923-0-1"},
                      {"problem": "Brand new thing", "fix": "Do y",
                       "continues": None}],
         "resolved": [{"id": "p0923-0-0",
                       "evidence": "the ad now carries the headline 'Free Estimate'"}]}
    r.update(over)
    return [r]


def _t(reply, changed=True):
    return creative.triage_from(reply, [{"campaign": "A"}, {"campaign": "B"}],
                                PREV, changed, _date(2026, 9, 24))


def test_a_continuing_problem_keeps_its_id_and_first_date():
    a = _t(_reply())[0]
    still = [p for p in a["problems"] if p["status"] == "still_open"]
    assert [(p["id"], p["open_since"]) for p in still] == [("p0923-0-1",
                                                           "2026-09-23")]
    new = [p for p in a["problems"] if p["status"] == "new"]
    assert new[0]["open_since"] == "2026-09-24"
    assert new[0]["id"] not in {p["id"] for rows in PREV.values() for p in rows}


def test_a_resolution_needs_evidence_and_is_reported():
    a = _t(_reply())[0]
    assert [x["id"] for x in a["resolved"]] == ["p0923-0-0"]
    assert a["resolved"][0]["problem"] == "No headline on the lead ad"
    no_evidence = _t(_reply(resolved=[{"id": "p0923-0-0", "evidence": ""}]))[0]
    assert no_evidence["resolved"] == []
    assert "p0923-0-0" in [x["id"] for x in no_evidence["dropped"]]


def test_a_silently_dropped_problem_is_not_called_fixed():
    """The failure this exists for: a problem the model simply stopped
    raising must read as 'no longer mentioned', never as resolved."""
    a = _t(_reply())[0]
    assert [x["id"] for x in a["dropped"]] == ["p0923-0-2"]


def test_nothing_resolves_when_the_data_did_not_change():
    a = _t(_reply(), changed=False)[0]
    assert a["resolved"] == []
    assert "p0923-0-0" in [x["id"] for x in a["dropped"]]


def test_an_id_from_another_campaign_is_a_new_problem():
    a = _t(_reply(problems=[{"problem": "p", "fix": "f",
                             "continues": "p0923-1-0"}],
                  resolved=[{"id": "p0923-1-0", "evidence": "gone"}]))[0]
    assert a["problems"][0]["status"] == "new"
    assert a["resolved"] == []
    b = _t(_reply())[1]
    assert [x["id"] for x in b["dropped"]] == ["p0923-1-0"], (
        "B was not in the reply, so its problem is unaccounted for")


def test_an_id_cannot_be_both_continued_and_resolved():
    a = _t(_reply(resolved=[{"id": "p0923-0-1", "evidence": "gone"}]))[0]
    assert a["resolved"] == []
    assert [p["id"] for p in a["problems"]
            if p["status"] == "still_open"] == ["p0923-0-1"]


def test_an_old_file_starts_the_chain():
    """The first files stored fixes only and no ids. They are given ids from
    their own date, so the next run has something to carry forward."""
    old = {"published_at": "2026-09-23T11:35:50+00:00",
           "triage": [{"campaign": "A", "changes": ["Cut the rocket line"]},
                      {"campaign": "B", "problems": []}]}
    prev = creative.previous_problems(old)
    assert prev == {"A": [{"id": "p0923-0-0", "problem": "Cut the rocket line",
                           "fix": "Cut the rocket line",
                           "open_since": "2026-09-23"}]}


def test_the_previous_review_is_the_one_before_today(tmp_path, monkeypatch):
    monkeypatch.setattr(creative, "SUGGESTION_DIR", tmp_path)
    for day in ("2026-09-22", "2026-09-23", "2026-09-24"):
        (tmp_path / f"{day}-renegade.json").write_text(
            json.dumps({"triage": [{"campaign": day}]}), encoding="utf-8")
    prev = creative.previous_suggestion("renegade", _date(2026, 9, 24))
    assert prev["file"] == "2026-09-23-renegade.json"


def test_the_prompt_says_how_to_account_for_the_last_review():
    p = creative.SUGGESTIONS_PROMPT
    for word in ('"continues"', '"resolved"', '"evidence"',
                 "No evidence, no resolution", "{previous_note}"):
        assert word in p, word
    assert "NOT changed" in creative.previous_note({"until": "x"}, False)


# ---------------------------------------------------------------------------
# A resolution has to point at something that actually changed.
# ---------------------------------------------------------------------------

def _camp(name, lead, tiring=(), rank=(3, 11), status="ACTIVE"):
    return {"campaign": name, "status": status,
            "leading_ads": [{"ad": a, "headline": h, "body": b,
                             "cta_button": "LEARN_MORE"} for a, h, b in lead],
            "tiring_ads": [{"ad": t} for t in tiring],
            "ad_groups": [{"ad_group": "G", "optimization_goal": "LEAD_GENERATION",
                           "cpa_rank_in_goal": rank[0],
                           "ad_groups_ranked_in_goal": rank[1]}]}


def test_changes_are_split_into_yours_and_the_numbers():
    prev = {"pack": {"campaigns": [_camp("A", [("X", "h", "b"), ("Y", "h", "b")],
                                         tiring=["X"])]},
            "snapshot": {"A": {"k1": {"ad": "X", "copy_hash": "aaa", "status": "ACTIVE"},
                               "k2": {"ad": "Y", "copy_hash": "bbb", "status": "ACTIVE"}}}}
    now = [_camp("A", [("X", "h", "b"), ("Z", "h", "b")], rank=(8, 11))]
    snap = {"A": {"k1": {"ad": "X", "copy_hash": "ccc", "status": "ACTIVE"},
                  "k2": {"ad": "Y", "copy_hash": "bbb", "status": "PAUSED"},
                  "k3": {"ad": "Z", "copy_hash": "ddd", "status": "ACTIVE"}}}
    ch = creative.changes_since(prev, now, snap)["A"]
    you = [c["what"] for c in ch if c["by"] == "you"]
    nums = [c["what"] for c in ch if c["by"] == "numbers"]
    assert 'wording changed on "X"' in you
    assert '"Y" ACTIVE -> PAUSED' in you
    assert 'ad added: "Z"' in you
    assert '"X" no longer tiring' in nums
    assert any("3 of 11 -> 8 of 11" in w for w in nums)
    assert any(w.startswith("spend leaders") for w in nums)
    assert [c["id"] for c in ch] == [f"c{i}" for i in range(1, len(ch) + 1)]
    assert ch[0]["by"] == "you", "the account's own changes come first"


def test_nothing_changed_means_no_changes():
    c = _camp("A", [("X", "h", "b")])
    prev = {"pack": {"campaigns": [c]},
            "snapshot": {"A": {"k1": {"ad": "X", "copy_hash": "a", "status": "ACTIVE"}}}}
    assert creative.changes_since(prev, [c], prev["snapshot"]) == {}


def test_without_a_snapshot_wording_is_compared_on_the_leading_ads():
    prev = {"pack": {"campaigns": [_camp("A", [("X", "old", "b")])]}}
    ch = creative.changes_since(prev, [_camp("A", [("X", "new", "b")])], {})
    assert ch["A"][0]["by"] == "you"
    assert "leading ads only" in ch["A"][0]["what"]


def test_a_resolution_must_cite_a_listed_change_for_its_campaign():
    """The failure this exists for. On the first carry-forward run the model
    resolved 'the leading ads share a body' because a different ad became a
    spend leader, and 'state the offer' against wording that had not changed.
    Neither cited a real change, so neither may stand."""
    changes = {"A": [{"id": "c1", "by": "numbers", "what": '"X" no longer tiring'},
                     {"id": "c2", "by": "you", "what": 'wording changed on "X"'}],
               "B": [{"id": "c1", "by": "you", "what": "something in B"}]}
    reply = _reply(problems=[], resolved=[
        {"id": "p0923-0-0", "change": "c2", "evidence": "headline added"},
        {"id": "p0923-0-1", "change": "c1", "evidence": "not tiring"},
        {"id": "p0923-0-2", "change": "c9", "evidence": "trust me"}])
    a = creative.triage_from(reply, [{"campaign": "A"}, {"campaign": "B"}],
                             PREV, True, _date(2026, 9, 24), changes)[0]
    got = {x["id"]: x["by"] for x in a["resolved"]}
    assert got == {"p0923-0-0": "you", "p0923-0-1": "numbers"}
    assert [x["id"] for x in a["dropped"]] == ["p0923-0-2"]


def test_a_campaign_with_no_changes_resolves_nothing():
    reply = _reply(problems=[], resolved=[
        {"id": "p0923-0-0", "change": "c1", "evidence": "gone"}])
    a = creative.triage_from(reply, [{"campaign": "A"}], PREV, True,
                             _date(2026, 9, 24), {})[0]
    assert a["resolved"] == []


def test_the_prompt_makes_the_change_list_the_only_evidence():
    p = creative.SUGGESTIONS_PROMPT
    assert "changes_since_last_review" in p
    assert "ONLY evidence" in p
    assert "a spend leader changing does not change what an ad" in p


# ---------------------------------------------------------------------------
# The rating rule: the numbers set the bounds, the model chooses within them.
# ---------------------------------------------------------------------------

def _rating_pack(monkeypatch, override):
    """A pack whose campaign X carries the given funnel overrides."""
    return _pack(monkeypatch, ladder=fake_ladder(override=override))


def test_the_rating_rule_is_diagnoses():
    """One rule, in one place. creative re-exports it so nothing that
    imported it from here broke, and nothing here can drift from it."""
    from intel import diagnose
    assert creative.rating_rule is diagnose.rating_rule
    assert creative.clamp_rating is diagnose.clamp_rating


def test_a_single_fatigue_symptom_on_one_ad_is_not_red(monkeypatch):
    """The 09-28 failure: ten reds came only from "an ad carrying its spend
    is tiring", at one to three of five symptoms. Daily frequency up on the
    ad that carries the campaign is a reason to look, not a red."""
    pack = _rating_pack(monkeypatch, {
        (P7, "a1"): {"pct": {"daily_frequency": 12.0},
                     "fatigue_pattern": "partial"},
        (P14, "a1"): {"pct": {"daily_frequency": 12.0},
                      "fatigue_pattern": "partial"}})
    x = pack["campaigns"][0]
    assert "red" not in x["rating_rule"]["allowed"]
    assert creative.clamp_rating("red", x["rating_rule"]) == "yellow"


def test_a_cpa_rise_that_persists_on_strong_evidence_is_red(monkeypatch):
    rise = {"pct": {"cpa": 40.0, "conversion_rate": -30.0},
            "evidence_level": "strong"}
    pack = _rating_pack(monkeypatch, {
        (P7, "X"): rise, (P14, "X"): {**rise, "pct": {"cpa": 18.0}}})
    x = pack["campaigns"][0]
    assert x["rating_rule"]["allowed"] == ["red"]
    assert creative.clamp_rating("green", x["rating_rule"]) == "red"


def test_a_7_day_rise_the_14_days_do_not_share_is_not_red(monkeypatch):
    """A wobble: the 7-day CPA jumped, the 14-day comparison is flat."""
    pack = _rating_pack(monkeypatch, {
        (P7, "X"): {"pct": {"cpa": 40.0}, "evidence_level": "strong"}})
    x = pack["campaigns"][0]
    assert "red" not in x["rating_rule"]["allowed"]


def test_a_campaign_that_never_converts_cannot_be_judged_on_cost(monkeypatch):
    """THRUPLAY-style: nothing converted in any window, so no rating but
    yellow -- cost could not be judged."""
    ents = [e for e in _entities() if e[1] in ("Y", "g5", "c1", "c2")]
    ents = [(lvl, k, n, c, g, "THRUPLAY", s, 0, None)
            for lvl, k, n, c, g, _, s, _, _ in ents]
    pack = _pack(monkeypatch, ladder=fake_ladder(entities=ents))
    y = pack["campaigns"][0]
    assert y["rating_rule"]["allowed"] == ["yellow"]


def test_a_campaign_not_running_cannot_be_red(monkeypatch):
    pack = _pack(monkeypatch, ladder=fake_ladder(
        drop=[(P7, "Y"), (P14, "Y"), (PU, "Y"), (PB, "Y")]))
    y = next(c for c in pack["campaigns"] if c["campaign"] == "Campaign Y")
    assert y["running"] is False
    assert "red" not in y["rating_rule"]["allowed"]


def test_the_card_says_when_the_rule_moved_a_rating():
    camps = [{"campaign": "A", "rating_rule": {"allowed": ["red"],
                                               "reasons": ["G is 11 of 11"]}}]
    t = creative.triage_from([{"campaign": "A", "rating": "yellow",
                               "why": "w", "problems": []}], camps)[0]
    assert t["rating"] == "red" and t["rating_adjusted_from"] == "yellow"
    assert t["rating_reasons"] == ["G is 11 of 11"]


# ---------------------------------------------------------------------------
# Every figure on a card must be one the model was given.
# ---------------------------------------------------------------------------

def test_a_figure_the_model_added_up_is_flagged():
    """The live case: two ads' spend, $4,878.46 + $3,946.06, printed as
    "$8,824.52 of this campaign's spend" -- a number no function returned."""
    facts = {"campaigns": [{"leading_ads": [{"spend": "4878.46"},
                                            {"spend": "3946.06"}],
                            "cpa": 26.1559, "link_ctr": 0.8596}]}
    triage = [{"campaign": "07082025 | X", "why": "Judged at $26.16 on 0.86% link CTR.",
               "problems": [{"problem": "so $8,824.52 of this campaign's spend "
                                        "rides on one headline",
                             "fix": "Refresh $4,878.46 of it first"}]}]
    assert creative.check_figures(triage, facts) == 1
    assert triage[0]["unverified_figures"] == ["$8,824.52"]


def test_rounding_to_print_is_not_flagged():
    known = creative._fact_numbers({"a": 97.0816, "b": "11351.64", "c": 14.9922})
    assert creative.unverified_figures(
        "$97.08 and $11,351.64 at 14.99% and $11,352 spent", known) == []


def test_figures_quoted_in_ad_copy_count_as_given():
    known = creative._fact_numbers({"body": "$38M in premium volume, 31 years"})
    assert creative.unverified_figures('"$38M" after 31 years', known) == []


def test_small_counts_and_campaign_dates_are_not_checked():
    triage = [{"campaign": "22052026 | Agent | FLGATX", "why":
               "22052026 | Agent | FLGATX has 3 ads, 2 tiring, in 2026.",
               "problems": []}]
    assert creative.check_figures(triage, {}) == 0


def test_figures_in_proposed_ad_copy_are_not_checked():
    """A fix that proposes new wording -- "what the first 90 days look like"
    -- is a suggestion, not a claim about the data."""
    triage = [{"campaign": "A", "why": "w", "problems": [
        {"problem": "p", "fix": 'Try "the first 90 days, and $300 back"'}]}]
    assert creative.check_figures(triage, {}) == 0


def test_a_threshold_is_not_a_claimed_figure():
    triage = [{"campaign": "A", "why": "6 conversions and under $300, fewer "
               "than 20 conversions, more than 15%", "problems": []}]
    assert creative.check_figures(triage, {}) == 0


# ---------------------------------------------------------------------------
# Totals come from ads.campaign_concentration, never from the model.
# ---------------------------------------------------------------------------

def test_a_campaign_carries_its_spend_spread_from_the_function(monkeypatch):
    pack = _pack(monkeypatch)
    x, y = pack["campaigns"]
    assert x["spend_spread"]["top_two_ads_share_pct"] == 99.0
    assert y["spend_spread"] is None, "no row for Y: nothing to invent"


def test_no_migration_means_no_spread_not_a_crash(monkeypatch):
    from psycopg import errors

    async def missing(sql, params=()):
        raise errors.UndefinedFunction("function ads.campaign_concentration does not exist")

    monkeypatch.setattr(creative, "fetch_all", missing)
    assert asyncio.run(creative._concentration({"brand_id": "B"})) == {}


def test_the_prompt_says_to_put_missing_totals_into_words():
    p = creative.SUGGESTIONS_PROMPT
    assert "spend_spread" in p
    assert "say it in words" in p


# ---------------------------------------------------------------------------
# The analyst pack: the ladder, the short form, the pool, the tiers.
# ---------------------------------------------------------------------------

def _suggest():
    """scripts/suggest.py as a module, loaded once."""
    import importlib.util
    import sys as _sys
    if "suggest_script" not in _sys.modules:
        spec = importlib.util.spec_from_file_location(
            "suggest_script", ROOT / "scripts" / "suggest.py")
        mod = importlib.util.module_from_spec(spec)
        _sys.modules["suggest_script"] = mod
        spec.loader.exec_module(mod)
    return _sys.modules["suggest_script"]


#: One sentinel per side of every pair. Each CPA is deliberately NOT spend
#: over conversions, so a rung that computed its CPA instead of reading it
#: would fail.
SENTINEL = {
    (PU, "cur"): _m(1.01, 1, 101.01, ctr=0.11, cpm=11.01, cvr=1.11),
    (P7, "cur"): _m(7.07, 7, 77.07, ctr=0.17, cpm=17.07, cvr=1.17),
    (P7, "pri"): _m(70.7, 70, 7.77, ctr=0.27, cpm=27.07, cvr=2.17),
    (P14, "cur"): _m(14.14, 14, 141.41, ctr=0.14, cpm=14.14, cvr=1.14),
    (P14, "pri"): _m(41.41, 41, 14.14, ctr=0.41, cpm=41.14, cvr=4.14),
    (P28, "cur"): _m(28.28, 28, 282.82, ctr=0.28, cpm=28.28, cvr=2.28),
    (PB, "pri"): _m(99.99, 99, 9.99, ctr=0.99, cpm=99.09, cvr=9.09),
}


def test_the_ladder_is_read_not_derived(monkeypatch):
    """Every rung is one side of one named pair, read off cur_m or pri_m.
    Nothing is summed across days or divided to get a rate."""
    override = {}
    for (pair, side), m in SENTINEL.items():
        override.setdefault((pair, "X"), {})[f"{side}_m"] = m
    pack = _pack(monkeypatch, ladder=fake_ladder(override=override))
    x = next(c for c in pack["campaigns"] if c["campaign"] == "Campaign X")
    assert [r["key"] for r in x["ladder"]] == [
        "unsettled", "last_7", "prior_7", "last_14", "prior_14", "last_28",
        "before"]
    where = {"unsettled": (PU, "cur"), "last_7": (P7, "cur"),
             "prior_7": (P7, "pri"), "last_14": (P14, "cur"),
             "prior_14": (P14, "pri"), "last_28": (P28, "cur"),
             "before": (PB, "pri")}
    for rung in x["ladder"]:
        pair, side = where[rung["key"]]
        m = SENTINEL[(pair, side)]
        assert rung["spend"] == m["spend"] and rung["conversions"] == m["conversions"]
        assert rung["cpa"] == m["rates"]["cpa"], rung["key"]
        assert rung["cpm"] == m["rates"]["cpm"]
        assert rung["link_ctr"] == m["rates"]["link_ctr"]
        assert rung["cvr"] == m["rates"]["conversion_rate"]
        dates = WINDOWS[pair]
        assert (rung["since"], rung["until"]) == (
            dates[:2] if side == "cur" else dates[2:])
        assert rung["provisional"] is (rung["key"] == "unsettled")
    # The 7-day changes are 017's, passed through.
    assert x["pct_7"]["cpa"] == STEADY_PCT["cpa"]
    assert x["funnel"]["metrics"][0]["metric"] == "cpm"
    cpa = next(m for m in x["funnel"]["metrics"] if m["metric"] == "cpa")
    assert (cpa["cur"], cpa["prior"]) == (77.07, 7.77)


def test_the_funnel_carries_017s_decomposition(monkeypatch):
    pack = _pack(monkeypatch, ladder=fake_ladder(override={(P7, "X"): {
        "cpa_share_cpm_pct": 4.1, "cpa_share_link_ctr_pct": 11.2,
        "cpa_share_conversion_rate_pct": 84.7, "primary_driver": "post_click"}}))
    x = pack["campaigns"][0]
    assert x["funnel"]["shares"] == {"auction_cost": 4.1, "click_through": 11.2,
                                     "post_click": 84.7}
    assert x["cpa_shares_7"] == x["funnel"]["shares"]
    assert x["driver_7"] == "post_click"
    # Campaign rows carry no daily frequency, so no empty frequency row.
    assert "frequency" not in [m["metric"] for m in x["funnel"]["metrics"]]


def test_a_campaign_not_running_gets_the_short_form(monkeypatch):
    pack = _pack(monkeypatch, ladder=fake_ladder(
        drop=[(P7, "Y"), (P14, "Y"), (PU, "Y"), (PB, "Y")]))
    y = next(c for c in pack["campaigns"] if c["campaign"] == "Campaign Y")
    assert set(y) == {"campaign", "status", "running", "objective",
                      "optimization_goals", "spend", "conversions", "cpa",
                      "link_ctr", "diagnosis", "rating_rule"}
    assert y["running"] is False
    assert y["diagnosis"] == {"findings": [], "max_priority": "monitor",
                              "not_yet": []}
    assert y["spend"] == 4000          # the 28-day side, read


def test_a_multi_goal_campaign_withholds_cpa_on_its_ladder(monkeypatch):
    pack = _pack(monkeypatch)
    x, y = pack["campaigns"]
    assert all(r["cpa"] is None for r in y["ladder"])
    assert any(r["cpa"] is not None for r in x["ladder"])


def test_the_challenger_pool_never_crosses_a_goal(monkeypatch):
    pack = _pack(monkeypatch)
    goal_of = {r["entity_name"]: r["optimization_goal"] for r in ROWS}
    pool = {g["optimization_goal"]: g for g in pack["challenger_pool"]}
    assert set(pool) == {"LEAD_GENERATION", "OFFSITE_CONVERSIONS", "THRUPLAY"}
    for goal, g in pool.items():
        assert len(g["ads"]) <= creative.CHALLENGERS_PER_GOAL
        for a in g["ads"]:
            assert goal_of[a["ad"]] == goal
            assert a["conversions"] and a["cpa"] is not None
            assert a["headline"], "a challenger without its copy is a name"
            assert not any(k.startswith("_") for k in a)
    assert pool["THRUPLAY"]["rankable_on_cost"] is False
    assert pool["THRUPLAY"]["ads"] == []
    assert [a["ad"] for a in pool["LEAD_GENERATION"]["ads"]] == [
        "Own a P&C Agency | Static", "20 Years Experience | UGC | Video",
        "Become an Agent | Start Franchise"]


def test_a_deteriorated_ad_is_not_offered_as_a_challenger(monkeypatch):
    tired = {"pct": {"link_ctr": -20.0, "daily_frequency": 9.0}}
    pack = _pack(monkeypatch, ladder=fake_ladder(
        override={(P7, "a8"): tired, (P14, "a8"): tired}))
    lead = next(g for g in pack["challenger_pool"]
                if g["optimization_goal"] == "LEAD_GENERATION")
    assert "Own a P&C Agency | Static" not in [a["ad"] for a in lead["ads"]]


def test_each_ad_group_carries_its_test_floor_from_017(monkeypatch):
    from intel import diagnose
    pack = _pack(monkeypatch)
    x = pack["campaigns"][0]
    for g in x["ad_groups"]:
        tf = g["test_floor"]
        assert tf["baseline_cpa"] == 40.0
        assert tf["decision_spend_per_arm"] == 200.0     # read, not 40 x 5
        assert tf["min_conversions_per_arm"] == diagnose.MIN_CONVERSIONS_PER_ARM
        assert tf["baseline_window"] == list(WINDOWS[P28][2:])


def test_one_ad_group_is_marked_same_as_campaign(monkeypatch):
    ents = [e for e in _entities() if e[1] in ("X", "g1", "a1", "a2")]
    pack = _pack(monkeypatch, ladder=fake_ladder(entities=ents))
    g = pack["campaigns"][0]["ad_groups"][0]
    assert g["same_as_campaign"] is True
    assert "spend" not in g and "cpa" not in g
    assert pack["campaigns"][0]["where"]["ad_groups"][0]["same_as_campaign"]


def test_no_funnel_migration_is_a_pack_not_a_crash(monkeypatch):
    """/suggestions builds this pack live on every page view. Before 017 is
    applied it must still render its evidence; the publisher refuses."""
    pack = _pack(monkeypatch, ladder={"available": False,
                                      "why": "migration 017 is not applied"})
    assert pack["funnel_available"] is False
    assert pack["campaigns"] == [] and pack["challenger_pool"] == []
    assert pack["windows"] is None
    assert pack["goals"] and pack["dimensions"]


def test_each_tier_is_shorter_and_keeps_the_diagnosis(monkeypatch):
    # a1 tires, so the control falls to a8 and a1 stays as the incumbent and
    # top spender: two ads, one of them not the control.
    tired = {"pct": {"link_ctr": -20.0, "daily_frequency": 9.0}}
    pack = _pack(monkeypatch, ladder=fake_ladder(
        override={(P7, "a1"): tired, (P14, "a1"): tired}))
    x = pack["campaigns"][0]
    assert x["diagnosis"]["control"]["incumbent_deteriorated"] is True
    a, b, c = (creative.campaign_tier(x, t) for t in creative.TIERS)
    view = creative.diagnosis_view(x["diagnosis"], many_groups=True)
    for v in (a, b, c):
        assert v["diagnosis"] == view
        assert v["rating_rule"] == x["rating_rule"]
        assert not set(creative.NOT_SENT) & set(v)
        for row in v["ladder"].values():
            assert len(row) == len(creative.LADDER_COLUMNS)
    assert list(a["ladder"]) == [r["key"] for r in x["ladder"]]
    assert not {"before", "prior_14"} & set(b["ladder"])
    assert list(c["ladder"]) == ["last_7", "prior_7", "last_28"]
    others = [ad for ad in b["where"]["ads"] if "control" not in ad["role"]]
    assert others and not any("headline" in ad for ad in others)
    assert any("headline" in ad for ad in a["where"]["ads"]
               if "control" not in ad["role"])
    assert [ad["role"] for ad in c["where"]["ads"]] == ["control"]
    assert c["where"]["ads"][0]["headline"]
    assert set(c) <= set(creative.TIER_C_KEYS)
    assert all(set(g) == {"ad_group", "test_floor"} for g in c["ad_groups"])
    sizes = [len(json.dumps(jsonable_encoder(v))) for v in (a, b, c)]
    assert sizes[0] > sizes[1] > sizes[2]


def test_the_ladder_columns_are_the_ones_the_prompt_names():
    named = "[" + ", ".join(creative.LADDER_COLUMNS) + "]"
    assert named in " ".join(creative.SUGGESTIONS_PROMPT.split())


def test_the_diagnosis_view_drops_only_repetition():
    """A campaign-level finding loses its entity, legs and driver (the
    campaign, pct_7/pct_14 and driver_7 say them); an ad's keeps all three.
    The control keeps why only when it matters."""
    diag = {"evidence": "moderate", "findings": [
        {"id": "d1", "level": "campaign", "entity": "Camp", "pattern": "post_click",
         "held_in": ["7v7"], "legs": {"cpa_pct_7": 30.0}, "driver_7": "post_click",
         "evidence": "moderate", "confidence": "low", "allowed": ["monitor"],
         "test_variables": ["form"]},
        {"id": "d2", "level": "ad", "entity": "Ad", "pattern": "possible_fatigue",
         "held_in": ["7v7"], "legs": {"link_ctr_pct_7": -20.0}, "driver_7": None,
         "evidence": "moderate", "confidence": "low", "allowed": ["monitor"],
         "test_variables": ["creative"]}],
        "control": {"ad_group": "G", "ad": "Ad", "incumbent": "Ad",
                    "incumbent_deteriorated": False, "protected": True,
                    "why": "it has the most conversions"},
        "concentration": None, "max_priority": "secondary", "not_yet": [],
        "unreadable_legs": []}
    v = creative.diagnosis_view(diag, many_groups=False)
    assert v["findings"][0] == {"id": "d1", "level": "campaign",
                                "pattern": "post_click", "held_in": ["7v7"],
                                "evidence": "moderate", "confidence": "low",
                                "allowed": ["monitor"],
                                "test_variables": ["form"]}
    assert v["findings"][1]["entity"] == "Ad"
    assert v["findings"][1]["legs"] == {"link_ctr_pct_7": -20.0}
    assert v["control"] == {"ad": "Ad", "incumbent_deteriorated": False}
    assert v["max_priority"] == "secondary"
    assert "concentration" not in v and "not_yet" not in v
def test_a_short_form_campaign_goes_as_it_is():
    short = {"campaign": "Z", "running": False, "spend": 10,
             "diagnosis": {"findings": []}, "rating_rule": {}}
    for t in creative.TIERS:
        assert creative.campaign_tier(short, t) == short


def _big_account(n=25, running=21):
    """A synthetic account the size of the live one: n campaigns, one ad
    group each (24 of the live 25 have one), four ads each, copy of a
    realistic length, and a spread of findings."""
    ents, copy, override, drop = [], [], {}, []
    for i in range(n):
        c, g = f"C{i:02d}", f"G{i:02d}"
        cname = (f"2205{i:02d}26 | Agent | Franchise | Special Ads | Custom List "
                 f"| IAL | GATXFLSCNCMIOHAZIAMOTN | {i:02d}")
        ents.append(("campaign", c, cname, c, None, "LEAD_GENERATION",
                     3000.0 + i, 60, 50.25))
        ents.append(("ad_group", g, f"{cname} | Broad 25-65", c, g,
                     "LEAD_GENERATION", 3000.0 + i, 60, 50.25))
        for j in range(4):
            k = f"{c}a{j}"
            ents.append(("ad", k, f"Own a P&C Agency | Static | v{j} | "
                                  f"Q3 Refresh {i:02d}", c, g,
                         "LEAD_GENERATION", 900.0 - j, 20 - j, 45.5 + j))
            copy.append({"ad_key": k, "first_headline":
                         "Own a P&C agency without building a book first",
                         "first_body": ("You have spent years building "
                                        "someone else's book. ") * 10,
                         "headlines": 3, "bodies": 2, "cta": None,
                         "cta_text": "Learn more", "hook": "callout",
                         "offer": "consultation", "format": "static"})
            if i % 3 == 1 and j == 0:
                tired = {"pct": {"link_ctr": -20.0, "daily_frequency": 9.0}}
                override[(P7, k)] = tired
                override[(P14, k)] = tired
        if i % 3 == 0:
            moved = {"pct": {"conversion_rate": -25.0, "cpa": 30.0},
                     "cpa_share_cpm_pct": 5.0, "cpa_share_link_ctr_pct": 10.0,
                     "cpa_share_conversion_rate_pct": 85.0,
                     "primary_driver": "post_click"}
            override[(P7, c)] = moved
            override[(P14, c)] = moved
            override[(P7, g)] = moved
            override[(P14, g)] = moved
        if i >= running:
            for key in [c, g] + [f"{c}a{j}" for j in range(4)]:
                drop += [(P7, key), (P14, key), (PU, key), (PB, key)]
    return ents, copy, override, drop


def _realistic_formats():
    return [{"value": f"value number {i}", "ads_run": 20 + i,
             "spend": 9000 - i * 500, "conversions": 200 - i * 10,
             "rates": {"cpa": 41.85 + i, "link_ctr": 1.02},
             "optimization_goals": ["LEAD_GENERATION", "QUALITY_LEAD"],
             "optimization_goal_count": 2, "comparable_on_cost": False,
             "spend_sufficient": True, "rank_within_goal": None}
            for i in range(14)]


def _sized_facts(suggest, pack, issues_each=0):
    facts = jsonable_encoder({k: pack[k] for k in suggest.SENT_TO_THE_MODEL})
    prev = {c["campaign"]: [{"id": f"i0930-{ci}-{k}", "open_since": "2026-09-29",
                             "problem": "Conversion rate after the click fell "
                                        "by about a third week on week while "
                                        "CPM and link CTR held roughly level, "
                                        "on the ad that carries the money.",
                             "fix": "x", "entity": c["campaign"],
                             "pattern": "post_click"}
                            for k in range(issues_each)]
            for ci, c in enumerate(pack["campaigns"])}
    facts["changes_since_last_review"] = {
        c["campaign"]: [{"id": f"c{k}", "by": "numbers",
                         "what": "post_click no longer found on the campaign"}
                        for k in range(1, issues_each + 1)]
        for c in pack["campaigns"]} if issues_each else {}
    facts["previous_issues"] = creative.for_the_prompt(
        prev, {c["campaign"] for c in pack["campaigns"]})
    return facts


def test_a_live_sized_account_fits_the_triage_budget(monkeypatch):
    """The 09-28 prompt was 67,971 characters against a 64,000 budget and
    nothing said so. 25 campaigns, 21 of them running (the live count on
    2026-09-28), names as long as the live ones: it fits at tier B or better.

    A previous review carried on top -- two issues and two changes per
    campaign -- does NOT fit at any tier (about 78k at tier C); fit() then
    sends it anyway and logs the warning test_over_budget_is_said_out_loud
    checks. That is an open budget question, not a hidden one.
    """
    suggest = _suggest()
    ents, copy, override, drop = _big_account()
    pack = _pack(monkeypatch, ladder=fake_ladder(ents, override, drop),
                 copy=copy, formats=_realistic_formats())
    assert len(pack["campaigns"]) == 25
    assert len([c for c in pack["campaigns"] if c["running"]]) == 21
    q, sent, tier, keep = suggest.fit(pack, _sized_facts(suggest, pack))
    assert len(q) <= suggest.TRIAGE_BUDGET, (len(q), tier, keep)
    assert tier in ("A", "B"), (len(q), tier, keep)
    assert len(sent["campaigns"]) == 25


def test_over_budget_is_said_out_loud(monkeypatch):
    suggest = _suggest()
    logged = []
    monkeypatch.setattr(suggest, "log", logged.append)
    monkeypatch.setattr(suggest, "TRIAGE_BUDGET", 1000)
    pack = _pack(monkeypatch)
    facts = jsonable_encoder({k: pack[k] for k in suggest.SENT_TO_THE_MODEL})
    q, sent, tier, keep = suggest.fit(pack, facts)
    assert (tier, keep) == suggest.STEPS[-1]
    assert any("WARNING" in m and "TRIAGE_BUDGET" in m for m in logged)
    assert len(sent["campaigns"]) == 2, "the campaign list is never cut"


def test_no_017_means_no_publish(monkeypatch):
    """A half-analysis is the one-window read the operator rejected. Without
    017 the publisher refuses, says which migration, and asks nobody; a dry
    run says the same."""
    suggest = _suggest()
    logged = []
    monkeypatch.setattr(suggest, "log", logged.append)

    async def pack(slug, days, until):
        return {"goals": [{"optimization_goal": "LEAD_GENERATION"}],
                "funnel_available": False, "campaigns": []}

    async def never(*a, **k):
        raise AssertionError("the model was asked without 017")

    monkeypatch.setattr(suggest.creative_mod, "creative_pack", pack)
    monkeypatch.setattr(suggest.chat, "classify", never)
    assert asyncio.run(suggest.publish("renegade", False, True)) == 1
    assert any("migration 017 is not applied" in m
               and "ads_migrate.py --print 017" in m for m in logged)
    logged.clear()
    assert asyncio.run(suggest.publish("renegade", True, False)) == 1
    assert any("migration 017" in m and "dry run" in m for m in logged)


def test_the_model_call_fits_inside_syncs_timeout():
    """sync.py used to give suggest.py 600s, the same as the model call
    inside it -- so a slow reply was killed from outside with no log line."""
    s = (ROOT / "scripts" / "suggest.py").read_text(encoding="utf-8")
    y = (ROOT / "scripts" / "sync.py").read_text(encoding="utf-8")
    assert "timeout=900" in s
    assert '_run_after("suggest.py", a.brand, extra=("--force",), timeout=1200' in y


# ---------------------------------------------------------------------------
# The reply chooses inside the diagnosis.
# ---------------------------------------------------------------------------

def _finding(fid, pattern, allowed, confidence, tvs, entity="Camp A",
             evidence="moderate"):
    return {"id": fid, "level": "campaign", "entity": entity,
            "pattern": pattern, "held_in": ["7v7", "14v14"], "legs": {},
            "driver_7": None, "evidence": evidence, "confidence": confidence,
            "allowed": allowed, "test_variables": tvs}


def _dcamp(name="Camp A", max_priority="primary", findings=None):
    findings = findings if findings is not None else [
        _finding("d1", "post_click", ["monitor", "test"], "medium",
                 ["landing_page", "form", "offer", "tracking"]),
        _finding("d2", "possible_fatigue", ["monitor", "test"], "low",
                 ["creative"], entity="Ad Two", evidence="directional"),
        _finding("d3", "scaling_opportunity", ["monitor", "test", "act"],
                 "high", ["budget"])]
    return {"campaign": name, "running": True, "status": "ACTIVE",
            "spend": 1240.5, "conversions": 14, "cpa": 88.61,
            "ladder": [{"key": "last_7", "spend": 420.0}],
            "funnel": {"metrics": []}, "where": {"ads": [{"name": "Ad One"}]},
            "spend_spread": None,
            "diagnosis": {"evidence": "moderate", "findings": findings,
                          "control": {"ad": "Ad One", "incumbent": "Ad One",
                                      "incumbent_deteriorated": False},
                          "max_priority": max_priority,
                          "not_yet": ["Replacing ads that still earn their "
                                      "clicks: the drop is after the click."]},
            "rating_rule": {"allowed": ["red", "yellow"], "reasons": ["r"]}}


PLAN = {"variable": "landing page", "control": "Ad One",
        "challenger": "the previous landing page",
        "hold_constant": [], "evaluate_on": [],
        "decide_when": "each arm has spent $500.00 and recorded 10 conversions"}


def _issue(basis="d1", kind="test", plan=PLAN, conf="medium",
           prio="primary", **over):
    i = {"priority": prio, "basis": basis, "observation": "CVR fell.",
         "evidence": "CVR down.", "diagnosis": "Post-click.",
         "recommendation": {"type": kind, "action": "Worth testing the page.",
                            "expected_outcome": "CVR recovers."},
         "test_plan": plan, "confidence": {"level": conf, "reason": "both"},
         "continues": None}
    i.update(over)
    return i


def _one(issues, camp=None, **reply):
    r = {"campaign": "Camp A", "rating": "yellow", "headline": "CVR fell.",
         "issues": issues, "not_yet": [], "resolved": [], **reply}
    return creative.triage_from([r], [camp or _dcamp()],
                                today=_date(2026, 10, 1))[0]


def test_a_type_above_its_finding_is_moved_down():
    t = _one([_issue(kind="act")])
    i = t["issues"][0]
    assert i["recommendation"]["type"] == "test"
    assert i["type_adjusted_from"] == "act"
    assert i["test_plan"]["variable"] == "landing page"


def test_a_test_on_a_variable_the_finding_does_not_allow_is_a_monitor():
    t = _one([_issue(plan={**PLAN, "variable": "creative"})])
    i = t["issues"][0]
    assert i["recommendation"]["type"] == "monitor"
    assert i["test_plan"] is None
    assert "creative" in i["test_plan_dropped"]
    assert i["type_adjusted_from"] == "test"


def test_a_test_needs_a_control_and_a_challenger():
    i = _one([_issue(plan={**PLAN, "challenger": ""})])["issues"][0]
    assert i["recommendation"]["type"] == "monitor"
    assert i["test_plan"] is None and i["test_plan_dropped"]
    i = _one([_issue(plan=None)])["issues"][0]
    assert i["recommendation"]["type"] == "monitor" and i["test_plan_dropped"]


def test_a_plan_on_anything_but_a_test_is_not_kept():
    i = _one([_issue(basis="d3", kind="act")])["issues"][0]
    assert i["recommendation"]["type"] == "act"
    assert i["test_plan"] is None


def test_hold_constant_defaults_to_the_other_three():
    i = _one([_issue(plan={**PLAN, "variable": "offer"})])["issues"][0]
    assert i["test_plan"]["hold_constant"] == ["audience", "landing page",
                                               "optimization event"]
    assert i["test_plan"]["evaluate_on"] == ["cost per lead", "link CTR",
                                             "conversion rate"]


def test_confidence_is_capped_at_the_findings():
    i = _one([_issue(basis="d2", plan={**PLAN, "variable": "creative"},
                     conf="high")])["issues"][0]
    assert i["confidence"]["level"] == "low"
    assert i["confidence_adjusted_from"] == "high"


def test_an_issue_with_no_finding_is_a_low_monitor():
    i = _one([_issue(basis="d9", kind="act", conf="high")])["issues"][0]
    assert i["basis_unknown"] is True
    assert i["recommendation"]["type"] == "monitor"
    assert i["confidence"]["level"] == "low"
    assert i["priority"] == "monitor"


def test_priority_is_capped_and_there_is_one_primary():
    t = _one([_issue(), _issue(basis="d3", kind="act"),
              _issue(basis="d3", kind="monitor"),
              _issue(prio="monitor", kind="monitor"),
              _issue(prio="monitor", kind="monitor"),
              _issue(prio="monitor", kind="monitor")])
    prios = [i["priority"] for i in t["issues"]]
    assert prios == ["primary", "secondary", "secondary", "monitor", "monitor"]
    assert t["issues"][1]["priority_adjusted_from"] == "primary"
    assert t["issues_trimmed"] == 1
    low = _one([_issue()], camp=_dcamp(max_priority="secondary"))["issues"][0]
    assert low["priority"] == "secondary"
    assert low["priority_adjusted_from"] == "primary"


def test_not_yet_starts_from_the_diagnosis():
    seed = _dcamp()["diagnosis"]["not_yet"][0]
    t = _one([], not_yet=[seed, "Raising the budget before CVR recovers."])
    assert t["not_yet"] == [seed, "Raising the budget before CVR recovers."]


def test_a_shape_2_card_carries_the_packs_figures_not_the_replys():
    camp = _dcamp()
    t = _one([_issue()], spend=1)
    assert t["shape"] == 2
    assert t["headline"] == t["why"] == "CVR fell."
    for k in ("ladder", "funnel", "where", "diagnosis", "running", "status"):
        assert t[k] == camp[k], k
    assert t["spend"] == 1240.5
    assert t["issues"][0]["id"] == "i1001-0-0"
    assert t["issues"][0]["status"] == "new"
    assert t["issues"][0]["pattern"] == "post_click"


def test_a_diagnosed_campaign_the_reply_skipped_still_gets_a_report():
    t = creative.triage_from([], [_dcamp()])[0]
    assert t["shape"] == 2 and t["rating"] is None
    assert "did not rate" in t["headline"]
    assert t["issues"] == []
    assert t["not_yet"] == _dcamp()["diagnosis"]["not_yet"]


# ---------------------------------------------------------------------------
# The last review's issues, carried forward.
# ---------------------------------------------------------------------------

PREV2 = {"Camp A": [{"id": "i0930-0-0", "problem": "CVR fell",
                     "fix": "test the page", "open_since": "2026-09-29",
                     "entity": "Camp A", "pattern": "post_click"},
                    {"id": "i0930-0-1", "problem": "Ad Two tiring",
                     "fix": "watch", "open_since": "2026-09-30",
                     "entity": "Ad Two", "pattern": "possible_fatigue"},
                    {"id": "i0930-0-2", "problem": "Room to scale",
                     "fix": "x", "open_since": "2026-09-30"}],
         "Camp B": [{"id": "i0930-1-0", "problem": "B's own",
                     "fix": "x", "open_since": "2026-09-30"}]}

CHANGES2 = {"Camp A": [{"id": "c1", "by": "numbers",
                        "what": 'possible_fatigue no longer found on "Ad Two"'}]}


def _t2(issues, resolved=(), changed=True, changes=CHANGES2):
    r = {"campaign": "Camp A", "rating": "yellow", "headline": "h",
         "issues": issues, "resolved": list(resolved)}
    return creative.triage_from([r], [_dcamp(), _dcamp("Camp B")], PREV2,
                                changed, _date(2026, 10, 1), changes)


def test_a_continuing_issue_keeps_its_id_and_first_date():
    a = _t2([_issue(continues="i0930-0-0"), _issue(basis="d3", kind="act")])[0]
    still = [i for i in a["issues"] if i["status"] == "still_open"]
    assert [(i["id"], i["open_since"]) for i in still] == [("i0930-0-0",
                                                           "2026-09-29")]
    new = [i for i in a["issues"] if i["status"] == "new"]
    assert new[0]["open_since"] == "2026-10-01"


def test_an_issue_resolves_only_on_a_listed_change_with_evidence():
    a = _t2([], resolved=[
        {"id": "i0930-0-1", "change": "c1", "evidence": "no longer found"},
        {"id": "i0930-0-2", "change": "c9", "evidence": "trust me"},
        {"id": "i0930-0-0", "change": "c1", "evidence": ""}])[0]
    assert [(x["id"], x["by"]) for x in a["resolved"]] == [("i0930-0-1",
                                                           "numbers")]
    assert sorted(x["id"] for x in a["dropped"]) == ["i0930-0-0", "i0930-0-2"]


def test_a_silently_dropped_issue_is_not_called_fixed():
    a = _t2([_issue(continues="i0930-0-0")])[0]
    assert sorted(x["id"] for x in a["dropped"]) == ["i0930-0-1", "i0930-0-2"]
    assert a["resolved"] == []


def test_an_issue_id_from_another_campaign_is_new():
    a, b = _t2([_issue(continues="i0930-1-0")],
               resolved=[{"id": "i0930-1-0", "change": "c1", "evidence": "x"}])
    assert a["issues"][0]["status"] == "new"
    assert a["resolved"] == []
    assert [x["id"] for x in b["dropped"]] == ["i0930-1-0"]


def test_previous_problems_read_a_shape_2_file():
    doc = {"published_at": "2026-09-30T09:00:00+00:00", "triage": [
        {"campaign": "Camp A", "shape": 2,
         "diagnosis": {"findings": [_finding("d1", "post_click", [], "low", [])]},
         "issues": [{"id": "i0930-0-0", "basis": "d1", "open_since": "2026-09-29",
                     "observation": "CVR fell",
                     "recommendation": {"action": "Worth testing the page."}},
                    {"basis": "d9", "observation": "",
                     "recommendation": {"action": "Watch it."}}]},
        {"campaign": "Old", "problems": [{"problem": "p", "fix": "f"}]}]}
    prev = creative.previous_problems(doc)
    assert prev["Camp A"][0] == {"id": "i0930-0-0", "problem": "CVR fell",
                                 "fix": "Worth testing the page.",
                                 "open_since": "2026-09-29",
                                 "entity": "Camp A", "pattern": "post_click"}
    assert prev["Camp A"][1]["id"] == "i0930-0-1"
    assert prev["Camp A"][1]["problem"] == "Watch it."
    assert prev["Old"] == [{"id": "p0930-1-0", "problem": "p", "fix": "f",
                            "open_since": "2026-09-30"}]


def test_the_prompt_gets_only_this_runs_campaigns_and_short_text():
    long = {"Camp A": [{**PREV2["Camp A"][0], "problem": "x" * 400},
                       {**PREV2["Camp A"][1]}],
            "Gone": [{"id": "i1", "problem": "p", "open_since": "d"}]}
    out = creative.for_the_prompt(long, {"Camp A"})
    assert set(out) == {"Camp A"}
    first, second = out["Camp A"]
    # The campaign itself is the key; its name is not repeated as entity.
    assert set(first) == {"id", "pattern", "observation"}
    assert len(first["observation"]) == creative.PREVIOUS_CHARS
    assert second["entity"] == "Ad Two"


def test_a_finding_that_appears_or_stops_holding_is_a_numbers_change():
    was = _dcamp()
    now = _dcamp(findings=[_finding("d1", "post_click", [], "low", []),
                           _finding("d2", "auction_cost", [], "low", [])])
    ch = creative.changes_since({"pack": {"campaigns": [was]},
                                 "snapshot": {}}, [now], {})["Camp A"]
    what = [c["what"] for c in ch]
    assert 'possible_fatigue no longer found on "Ad Two"' in what
    assert 'scaling_opportunity no longer found on the campaign' in what
    assert 'auction_cost now found on the campaign' in what
    assert all(c["by"] == "numbers" for c in ch)


def test_a_field_only_one_side_has_is_not_a_change():
    """An old file's tiring ads against a new pack without them is a change
    of shape, not of the account -- and must not resolve a fatigue issue."""
    old = {"campaign": "A", "tiring_ads": [{"ad": "X"}],
           "leading_ads": [{"ad": "X"}]}
    new = {"campaign": "A", "diagnosis": {"findings": []}}
    assert creative.changes_since({"pack": {"campaigns": [old]},
                                   "snapshot": {}}, [new], {}) == {}


# ---------------------------------------------------------------------------
# Figures on a shape-2 card.
# ---------------------------------------------------------------------------

def _card(**issue):
    i = {"observation": "", "evidence": "", "diagnosis": "",
         "recommendation": {"action": "", "expected_outcome": ""},
         "test_plan": None, "confidence": {"reason": ""}}
    i.update(issue)
    return [{"campaign": "Camp A", "headline": "h", "issues": [i],
             "not_yet": []}]


def test_every_new_field_is_checked():
    for field in ("observation", "evidence", "diagnosis"):
        t = _card(**{field: "spent $1,234.56"})
        assert creative.check_figures(t, {}) == 1, field
    t = _card(recommendation={"action": "move $1,234.56",
                              "expected_outcome": "CPA near $77.77"})
    assert creative.check_figures(t, {}) == 2
    t = _card(confidence={"reason": "on 55 conversions"})
    assert creative.check_figures(t, {}) == 1
    t = [{"campaign": "A", "headline": "h", "issues": [],
          "not_yet": ["Acting on $4,444.44 of spend"]}]
    assert creative.check_figures(t, {}) == 1


def test_a_fall_is_the_negative_change_the_facts_hold():
    t = _card(evidence="link CTR down 22.41% on the prior 7 days")
    assert creative.check_figures(t, {"pct_7": {"link_ctr": -22.41}}) == 0


def test_what_the_last_review_said_is_not_a_fact():
    facts = {"previous_issues": {"Camp A": [{"observation": "spent $4,321.09"}]},
             "previous_problems": {"Camp A": [{"problem": "$5,555.55"}]}}
    t = _card(evidence="spent $4,321.09 and $5,555.55")
    assert creative.check_figures(t, facts) == 2


def test_decide_when_is_held_to_the_test_floor():
    facts = {"campaigns": [{"ad_groups": [{"test_floor": {
        "decision_spend_per_arm": 500.0, "min_conversions_per_arm": 10}}]}]}
    ok = _card(test_plan={"decide_when": "each arm has spent at least "
                                         "$500.00 with 10 conversions"})
    assert creative.check_figures(ok, facts) == 0
    bad = _card(test_plan={"decide_when": "each arm has spent at least $750.00"})
    assert creative.check_figures(bad, facts) == 1
    # Elsewhere a threshold is still a threshold, not a claim.
    assert creative.check_figures(_card(observation="under $300"), facts) == 0


def test_proposed_copy_in_a_test_plan_is_not_checked():
    t = _card(test_plan={"control": "Ad 2026 | 45 States",
                         "challenger": "Open with 31 years and $38M",
                         "decide_when": ""})
    assert creative.check_figures(t, {}) == 0


def test_every_entity_name_is_cut_before_checking():
    facts = {"campaigns": [{"where": {"ads": [
        {"name": "20 Years Experience | UGC | Video"}]}}],
        "challenger_pool": [{"ads": [{"ad": "Retirement at 65 | 1,500 Agents"}]}]}
    t = _card(observation="20 Years Experience | UGC | Video carries it, and "
                          "Retirement at 65 | 1,500 Agents could challenge it")
    assert creative.check_figures(t, facts) == 0


# ---------------------------------------------------------------------------
# The prompt asks the four questions.
# ---------------------------------------------------------------------------

def test_the_prompt_asks_four_questions_in_order():
    p = creative.SUGGESTIONS_PROMPT
    marks = [p.index(m) for m in ("1. WHAT CHANGED", "2. WHERE", "3. WHY",
                                  "4. WHAT IS JUSTIFIED")]
    assert marks == sorted(marks)
    for phrase in ("HYPOTHESIS", '"daily frequency", never "frequency"',
                   "A single fatigue symptom on one ad is not a red",
                   "Neither is concentration",
                   "is not tracked in this account",
                   "early link CTR", "test_floor.decision_spend_per_arm",
                   "challenger_pool", "SAME optimization goal",
                   "diagnosis.concentration.efficient",
                   "There are no target CPAs", "previous_issues",
                   "the evidence supports a direct change"):
        assert phrase in p, phrase
    for pattern in ("auction_cost", "creative_engagement", "post_click",
                    "possible_fatigue", "audience_saturation",
                    "clickier_poorer_intent", "diminishing_returns",
                    "scaling_opportunity", "no_conversions_spend",
                    "insufficient_evidence"):
        assert re.search(rf"^     {pattern} ", p, re.M), pattern


def test_the_system_prompt_forbids_going_past_the_diagnosis():
    s = creative.TRIAGE_SYSTEM
    assert "Never choose a recommendation type" in s
    assert "first character of your reply is [" in s
    assert "subtract" in s


# ---------------------------------------------------------------------------
# creative.py computes nothing.
# ---------------------------------------------------------------------------

#: The two places arithmetic is the job and not a figure: how many hours old
#: a file is, and the rounding tolerance a printed figure is checked against.
ARITHMETIC_ALLOWED = ("age_hours", "_matches")


def test_creative_does_no_arithmetic_on_data():
    """The crude form of tests/test_diagnose.py's check. `+` is allowed (it
    joins strings and lists here); `- * / // % **` and sum() are not, outside
    ARITHMETIC_ALLOWED."""
    import ast
    tree = ast.parse((ROOT / "intel" / "creative.py").read_text(encoding="utf-8"))
    allowed = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name in ARITHMETIC_ALLOWED:
            allowed |= {id(n) for n in ast.walk(node)}
    for node in ast.walk(tree):
        if id(node) in allowed:
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) \
                and isinstance(node.right, (ast.Constant, ast.JoinedStr)) \
                and isinstance(getattr(node.right, "value", ""), str):
            continue          # ROOT / "suggestions": a path, not a quotient
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, (ast.Sub, ast.Mult, ast.Div,
                                            ast.FloorDiv, ast.Mod, ast.Pow)), (
                f"arithmetic at line {node.lineno} of creative.py")
        if isinstance(node, ast.AugAssign):
            assert not isinstance(node.op, (ast.Sub, ast.Mult, ast.Div,
                                            ast.FloorDiv, ast.Mod, ast.Pow)), (
                f"arithmetic at line {node.lineno} of creative.py")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "sum", (
                f"sum() at line {node.lineno} of creative.py")


def test_a_report_built_from_the_pack_renders(monkeypatch):
    """End to end without a model: the pack, a reply, triage_from, the
    figure check and the template. Every campaign gets its fold, the ladder
    and the test plan reach the page, and nothing on it is a script."""
    pack = _pack(monkeypatch)
    reply = [{"campaign": "Campaign X", "rating": "yellow",
              "headline": "Steady on its own earlier windows.",
              "issues": [_issue(basis="d1", kind="monitor", plan=None,
                                prio="monitor", conf="low")],
              "not_yet": ["Judging the copy on one week."], "resolved": []}]
    triage = creative.triage_from(reply, pack["campaigns"],
                                  today=_date(2026, 10, 1))
    facts = jsonable_encoder({k: pack[k] for k in _suggest().SENT_TO_THE_MODEL})
    creative.check_figures(triage, facts)
    published = jsonable_encoder({"published_at": "2026-10-01T09:00:00",
                                  "until": "2026-09-21", "previous_file": None,
                                  "triage": triage})
    html = ui.templates.env.get_template("_triage.html").render(
        published=published)
    assert html.count('<details class="tcamp">') == 2
    assert "Campaign X" in html and "Campaign Y" in html
    assert "provisional" in html, "the unsettled rung is not marked"
    assert "Judging the copy on one week." in html
    assert not re.search(r"<script|<button", html, re.I)
