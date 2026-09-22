"""The brief summary fits in a command line, and says how it was shortened.

`claude -p` takes the question as one argv element, and Windows caps a command
line at 32,767 characters. The brief pack is ~52,000 as produced. A spawn that
failed on length would surface as "the session returned nothing", which is the
kind of error that gets debugged in the wrong place for an afternoon.

None of these need a database.
"""

from __future__ import annotations

import json
from pathlib import Path

import ui

ROOT = Path(__file__).resolve().parent.parent


def test_short_lists_are_left_alone():
    pack = {"a": [1, 2, 3], "b": {"rows": list(range(8))}, "c": "x", "d": 4}
    assert ui._compact(pack, keep=8) == pack


def test_long_lists_are_cut_and_say_so():
    rows = [{"i": i} for i in range(30)]
    out = ui._compact({"creative": {"rows": rows}}, keep=8)
    kept = out["creative"]["rows"]
    assert kept[:8] == rows[:8], "the head is the part worth reading"
    assert len(kept) == 9
    assert "22 more rows" in kept[-1], kept[-1]
    assert "run the verb" in kept[-1]


def test_keep_zero_omits_lists_entirely_but_still_counts_them():
    out = ui._compact({"x": {"rows": [1] * 40}, "y": 2}, keep=0)
    assert out["y"] == 2
    assert "40 rows omitted" in out["x"]["rows"]


def test_compaction_is_recursive():
    nested = {"outer": [{"inner": list(range(20))} for _ in range(20)]}
    out = ui._compact(nested, keep=3)
    assert len(out["outer"]) == 4
    assert len(out["outer"][0]["inner"]) == 4


def test_a_pack_the_size_of_the_real_brief_fits_the_budget():
    """Shaped like the measured pack: two big row lists and some scalars.
    52,463 chars as produced; the endpoint tries keep=8, then 3, then 0."""
    # Shaped like a real ads.fatigue row -- the measured pack was 52,463
    # chars, and creative.rows alone was 27,675 of it. A thin fixture here
    # would pass the budget without proving the compaction is what did it.
    rates = {"cpa": 35.94, "cpc": 5.58, "cpm": 61.04, "ctr": 1.85,
             "link_ctr": 1.10, "conversion_rate": 15.52,
             "cost_per_link_click": 5.58, "lp_view_rate": 62.1}
    row = {"ad_key": "2cf2f232-caab-4696-e7c7-25c5a862f170",
           "entity_name": "Become an Agent | Start Franchise | Multi Color | Video",
           "optimization_goal": "LEAD_GENERATION",
           "recent_since": "2026-09-08", "recent_until": "2026-09-21",
           "days_observed": 14, "spend_recent": 1436.05,
           "impressions_recent": 23541, "confident": True,
           "link_ctr_decline": False, "cpm_rise": True, "cpa_rise": True,
           "frequency_rise": False, "ranking_drop": False, "score": 2,
           "recent": dict(rates), "prior": dict(rates)}
    pack = {"facts": {"creative": {"rows": [dict(row) for _ in range(30)]},
                      "movement": {"rows": [dict(row) for _ in range(25)]},
                      "spend": {"total": 60163.0}}}
    raw = len(json.dumps(pack))
    assert raw > 40000, f"fixture is thinner than the real pack: {raw}"
    for keep in (8, 3, 0):
        text = json.dumps(ui._compact(pack, keep))
        if len(text) <= ui.PROMPT_BUDGET - 3000:
            break
    else:
        raise AssertionError("no compaction level fit the budget")
    assert keep == 8, "the real brief should fit at the first, most useful level"


def test_the_budget_leaves_room_for_the_rest_of_the_command_line():
    """chat.SYSTEM, the allowed-tool list and the flags ride alongside the
    question in the same 32,767 characters."""
    import chat
    fixed = len(chat.SYSTEM) + sum(len(t) for t in chat._allowed()) \
        + sum(len(t) for t in chat._denied()) + 200
    assert ui.PROMPT_BUDGET + fixed < 32767, (ui.PROMPT_BUDGET, fixed)


def test_both_ask_endpoints_are_posts():
    src = (ROOT / "ui.py").read_text(encoding="utf-8")
    assert '@router.post("/brief/summary.json")' in src
    assert '@router.post("/ad/{ad_key}/analyse.json")' in src
    assert '@router.get("/brief/summary.json")' not in src


def test_the_pages_use_the_generic_ask_handler():
    """One script, driven by two attributes. A page-specific block for each
    button is how the two drift apart in their error handling."""
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert 'querySelectorAll("[data-ask-url]")' in js
    assert 'getElementById("analyse-ad")' not in js, "the old ad-only block is still there"
    for tpl in ("ad.html", "brief.html"):
        html = (ROOT / "templates" / tpl).read_text(encoding="utf-8")
        assert "data-ask-url=" in html and "data-ask-target=" in html, tpl
