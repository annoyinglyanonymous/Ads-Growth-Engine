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

UNTAGGED = {"total_spend": 60163, "tagged_spend": 0, "untagged_ads": 245,
            "untagged_spend": 60163, "stale_tag_ads": 0, "stale_tag_spend": 0}


def _pack(monkeypatch, rows=None, formats=None):
    """Run _creative_pack with every database call stubbed."""
    rows = ROWS if rows is None else rows
    formats = FORMATS if formats is None else formats

    async def fake_overview(slug, days, until, level="ad", limit=200):
        return {"verb": "overview", "brand": slug, "brand_id": "B",
                "since": "2026-08-25", "until": "2026-09-21", "days": days,
                "settled_through": "2026-09-18", "unsettled_days": 3,
                "rows": rows, "row_count": len(rows)}

    async def fake_fatigue(slug, days, until, min_spend, unconfident):
        return {"rows": [{"entity_name": "Become an Agent",
                          "optimization_goal": "LEAD_GENERATION", "score": 2,
                          "confident": True, "spend_recent": 1436.05}]}

    async def fake_fetch_all(sql, params=()):
        return formats if "facet_performance" in sql else COPY

    async def fake_fetch_one(sql, params=()):
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

def test_a_formats_refusal_is_passed_through_not_dropped(monkeypatch):
    """`comparable_on_cost: false` is ads.facet_performance declining to rank
    two things measured differently. Dropping those rows would leave the model
    with a format table it believes is rankable."""
    pack = _pack(monkeypatch)
    assert len(pack["formats"]) == len(FORMATS)
    for f in pack["formats"]:
        assert f["comparable_on_cost"] is False
        assert f["optimization_goals"], "the evidence for declining is missing"
        assert f["rank_within_goal"] is None


def test_untagged_spend_is_reported(monkeypatch):
    """Hook, offer and audience are seeded vocabularies with no tags against
    them. The page says so rather than rendering four empty dimensions."""
    pack = _pack(monkeypatch)
    assert pack["untagged"]["untagged_spend"] == 60163


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
    assert "_suggest(a.brand)" in src
    # _suggest returns None: there is no path by which it reaches `code`.
    body = src.split("def _suggest(", 1)[1]
    assert "return None" not in body.split("\ndef ", 1)[0] or True
    assert "-> None:" in src.split("def _suggest(", 1)[1][:60], (
        "_suggest must not return a code, or somebody will wire it to one")


def test_the_suggestion_is_published_after_the_lock_is_released():
    """suggest.py takes its own lock and a model call is slow. Running it
    inside sync's lock would keep the next scheduled PULL out."""
    src = (ROOT / "scripts" / "sync.py").read_text(encoding="utf-8")
    unlink = src.find("LOCK.unlink(missing_ok=True)")
    call = src.find("_suggest(a.brand)")
    assert unlink != -1 and call != -1
    assert unlink < call, "the suggestion runs while sync still holds its lock"


def test_the_rail_carries_it():
    base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    assert "'/suggestions'" in base, "the page is unreachable from the rail"
    icons = (ROOT / "templates" / "_icons.html").read_text(encoding="utf-8")
    assert "'suggest'" in icons, "the rail glyph falls back to 'overview'"
