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
     "cta_text": "Get offer" if k.startswith("a") else None}
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


def _pack(monkeypatch, rows=None, formats=None):
    """Run _creative_pack with every database call stubbed."""
    rows = ROWS if rows is None else rows
    formats = FORMATS if formats is None else formats

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
        return COPY

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
             "unsettled_days", "formats"}
    missing = set(pack) - frame - set(suggest.SENT_TO_THE_MODEL)
    assert not missing, (
        f"the pack carries {sorted(missing)} and the publisher does not send "
        f"it. Either send it or stop building it.")


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


def test_a_card_carries_its_copy_and_its_tiring_ads(monkeypatch):
    pack = _pack(monkeypatch)
    x = pack["campaigns"][0]
    assert x["leading_ads"] and x["leading_ads"][0]["headline"]
    assert len(x["leading_ads"][0]["body"]) <= creative.CAMPAIGN_BODY_CHARS + 1
    assert [t["ad"] for t in x["tiring_ads"]] == ["Become an Agent"]
    assert "_key" not in x


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


def test_the_prompt_pairs_each_problem_with_a_fix():
    p = creative.SUGGESTIONS_PROMPT
    assert '"problem"' in p and '"fix"' in p
    assert "every problem carries its own fix" in p


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

def _rc(groups, conversions=100, tiring=(), lead=("L",)):
    return {"campaign": "C", "conversions": conversions,
            "ad_groups": [{"ad_group": f"G{i}", "optimization_goal": "LEAD_GENERATION",
                           "spend": sp, "conversions": cv,
                           "cpa_rank_in_goal": r, "ad_groups_ranked_in_goal": n}
                          for i, (sp, cv, r, n) in enumerate(groups)],
            "leading_ads": [{"ad": a} for a in lead],
            "tiring_ads": [{"ad": t} for t in tiring]}


def test_dear_and_well_funded_is_red_whatever_the_model_says():
    rule = creative.rating_rule(_rc([(3542, 60, 11, 11)]))
    assert rule["allowed"] == ["red"]
    assert creative.clamp_rating("yellow", rule) == "red"


def test_green_needs_the_cheap_third_enough_conversions_and_nothing_tiring():
    ok = creative.rating_rule(_rc([(2233, 135, 1, 11)]))
    assert "green" in ok["allowed"]
    assert "green" not in creative.rating_rule(
        _rc([(2233, 135, 1, 11)], conversions=6))["allowed"]
    assert "green" not in creative.rating_rule(
        _rc([(2233, 135, 1, 11)], tiring=["X"]))["allowed"]
    assert "green" not in creative.rating_rule(
        _rc([(2233, 135, 6, 11)]))["allowed"]
    assert creative.clamp_rating("green", creative.rating_rule(
        _rc([(2233, 135, 6, 11)]))) == "yellow"


def test_a_few_dollars_at_the_dear_end_is_not_red():
    rule = creative.rating_rule(_rc([(120, 2, 11, 11)]))
    assert "red" not in rule["allowed"]
    assert creative.clamp_rating("red", rule) == "yellow"


def test_spending_with_nothing_where_the_goal_converts_is_red():
    rule = creative.rating_rule(_rc([(836, 0, None, 5)], conversions=0))
    assert rule["allowed"] == ["red"]


def test_a_goal_that_never_converts_is_not_red_on_cost():
    """THRUPLAY: nothing in the goal converted, so there is no ranking at all
    (ad_groups_ranked_in_goal is None) and no cost grounds for red."""
    rule = creative.rating_rule(_rc([(733, 0, None, None)], conversions=0))
    assert rule["allowed"] == ["yellow"]


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
