"""intel/diagnose.py: the deterministic analyst under the campaign triage.

Table-driven. Every row below is shaped like an ads.funnel_change row (C1),
built by the helpers at the top, so a case states only what differs from a
steady week. No database: the module is a pure function of rows.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from intel import diagnose as d

MODULE = Path(d.__file__)
P7, P14, P28 = d.P7, d.P14, d.P28

#: A steady week: every rate inside the steady band.
STEADY = {"cpm": 2.0, "link_ctr": -1.0, "cost_per_link_click": 1.0,
          "conversion_rate": 0.5, "cpa": 1.0, "spend": 3.0}


def row(level="campaign", name="Camp", key=None, pct=None, ev="moderate",
        conv=30, spend=1000.0, cpa=33.33, **over) -> dict:
    """One C1 row. pct entries set to None are stripped, as SQL strips them."""
    p = dict(STEADY)
    if level == "ad":
        p["daily_frequency"] = 1.0
    p.update(pct or {})
    r = {
        "level": level, "entity_key": key or f"k-{name}", "entity_name": name,
        "entity_status": "ACTIVE", "campaign_key": "k-Camp",
        "campaign_name": "Camp", "ad_group_key": "k-G1",
        "ad_group_name": "G1", "parent_key": None,
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "optimization_goal_count": 1, "equal_length": True,
        "cur_unsettled_days": 0,
        "cur_m": {"window_days": 7, "days_active": 7, "spend": spend,
                  "conversions": conv, "rates": {"cpa": cpa}},
        "pri_m": {"window_days": 7, "days_active": 7, "spend": spend,
                  "conversions": conv, "rates": {"cpa": cpa}},
        "pct": {k: v for k, v in p.items() if v is not None},
        "cpa_direction": "flat", "primary_driver": None,
        "spend_to_baseline_cpa": 30.0, "baseline_cpa": 33.0,
        "evidence_level": ev, "parent_spend_share_pct": 50.0,
        "fatigue_consistent": False if level == "ad" else None,
    }
    r.update(over)
    return r


def ent(name="Camp", level="campaign", r7=None, r14=None, r28=None,
        key=None, **kw) -> dict:
    """A C3 entity. r7/r14 are dicts of row() kwargs, or a ready row, or None
    for a missing window."""
    def build(spec):
        if spec is None or "cur_m" in spec:
            return spec
        return row(level=level, name=name, key=key, **spec)
    rows = {P7: build(r7), P14: build(r14)}
    if r28 is not None:
        rows[P28] = build(r28)
    return {"name": name, "key": key or f"k-{name}", "goal":
            "OFFSITE_CONVERSIONS", "status": "ACTIVE", "rows": rows, **kw}


def both(level="campaign", name="Camp", key=None, **spec) -> dict:
    """The same reading in both windows."""
    return ent(name=name, level=level, key=key, r7=dict(spec),
               r14=dict(spec))


def patterns(out) -> list:
    return [f["pattern"] for f in out["findings"]]


# ------------------------------------------------------- one per pattern --

PATTERN_CASES = [
    ("auction_cost", "campaign", {"pct": {"cpm": 20.0, "cpa": 18.0}}),
    ("creative_engagement", "campaign", {"pct": {"link_ctr": -20.0}}),
    ("post_click", "campaign", {"pct": {"conversion_rate": -20.0}}),
    ("possible_fatigue", "ad", {"fatigue_consistent": True}),
    ("possible_fatigue", "ad", {"pct": {"link_ctr": -20.0,
                                        "daily_frequency": 9.0}}),
    ("audience_saturation", "ad", {"pct": {"cpm": 20.0,
                                           "daily_frequency": 9.0}}),
    ("clickier_poorer_intent", "campaign", {"pct": {"link_ctr": 20.0,
                                                    "conversion_rate": -20.0}}),
    ("diminishing_returns", "campaign", {"pct": {"spend": 30.0, "cpa": 20.0}}),
    ("scaling_opportunity", "campaign", {"parent_spend_share_pct": 10.0}),
    ("no_conversions_spend", "campaign", {"conv": 0,
                                          "spend_to_baseline_cpa": 3.5}),
]


@pytest.mark.parametrize("pattern,level,spec", PATTERN_CASES,
                         ids=[c[0] for c in PATTERN_CASES])
def test_each_pattern_is_found(pattern, level, spec):
    e = both(level=level, name="X", **spec)
    if level == "ad":
        out = d.diagnose_campaign(both(), [], [e])
    else:
        out = d.diagnose_campaign(e, [], [])
    f = next(f for f in out["findings"] if f["pattern"] == pattern)
    assert f["held_in"] == ["7v7", "14v14"]
    assert f["level"] == level
    assert f["test_variables"] == d.TEST_VARIABLES[pattern]
    assert f["id"].startswith("d")
    assert set(f) >= {"id", "level", "entity", "pattern", "held_in", "legs",
                      "driver_7", "evidence", "confidence", "allowed",
                      "test_variables"}
    assert not any(k.startswith("_") for k in f)


def test_a_steady_week_falls_back_to_steady():
    out = d.diagnose_campaign(both(), [], [])
    assert patterns(out) == ["steady"]
    assert out["findings"][0]["allowed"] == ["monitor"]
    assert out["max_priority"] == "monitor"


def test_cpm_only_rise_is_the_auction_not_the_creative():
    out = d.diagnose_campaign(both(pct={"cpm": 22.0, "cpa": 20.0},
                                   primary_driver="auction_cost"), [], [])
    assert patterns(out) == ["auction_cost"]
    f = out["findings"][0]
    assert "act" not in f["allowed"] and "test" not in f["allowed"]
    assert any("Replacing the creative" in s for s in out["not_yet"])


def test_cvr_drop_with_ctr_steady_is_post_click_not_creative():
    out = d.diagnose_campaign(both(pct={"conversion_rate": -25.0}), [], [])
    assert "post_click" in patterns(out)
    assert "creative_engagement" not in patterns(out)
    f = out["findings"][0]
    assert "creative" not in f["test_variables"]
    assert any("still earn their clicks" in s for s in out["not_yet"])


def test_a_single_fatigue_leg_is_not_fatigue_and_not_red():
    ad = both(level="ad", name="A", pct={"link_ctr": -16.0,
                                         "daily_frequency": 0.0},
              fatigue_consistent=False)
    camp = both()
    out = d.diagnose_campaign(camp, [], [ad])
    assert "possible_fatigue" not in patterns(out)
    assert "creative_engagement" in patterns(out)
    rule = d.rating_rule({"running": True, "rows": camp["rows"],
                          "diagnosis": out})
    assert "red" not in rule["allowed"]


def test_insufficient_evidence_is_monitor_only_and_low():
    out = d.diagnose_campaign(both(pct={"conversion_rate": -30.0},
                                   ev="insufficient"), [], [])
    f = out["findings"][0]
    assert f["pattern"] == "post_click"
    assert f["allowed"] == ["monitor"]
    assert f["confidence"] == "low"
    out = d.diagnose_campaign(both(ev="insufficient"), [], [])
    assert patterns(out) == ["insufficient_evidence"]
    assert out["findings"][0]["allowed"] == ["monitor"]


def test_a_none_pct_makes_the_leg_unknown_not_false():
    e = ent(r7={"pct": {"cpm": 25.0, "link_ctr": None}}, r14=None)
    out = d.diagnose_campaign(e, [], [])
    assert "auction_cost" not in patterns(out)
    assert {"pattern": "auction_cost", "window": "7v7",
            "legs": ["link_ctr"]}.items() <= next(
        u for u in out["unreadable_legs"]
        if u["pattern"] == "auction_cost").items()


def test_holding_in_both_windows_raises_confidence():
    one = d.diagnose_campaign(ent(r7={"pct": {"link_ctr": -20.0}},
                                  r14={}), [], [])
    two = d.diagnose_campaign(both(pct={"link_ctr": -20.0}), [], [])
    assert one["findings"][0]["held_in"] == ["7v7"]
    assert one["findings"][0]["confidence"] == "low"
    assert two["findings"][0]["confidence"] == "medium"
    assert any("14-day comparison does not show it yet" in s
               for s in one["not_yet"])


def test_findings_are_capped_ordered_and_numbered():
    ads = [both(level="ad", name=f"A{i}", spend=float(100 * i),
                pct={"conversion_rate": -20.0})
           for i in range(1, 7)]
    out = d.diagnose_campaign(both(pct={"conversion_rate": -20.0}), [], ads)
    assert [f["id"] for f in out["findings"]] == ["d1", "d2", "d3", "d4"]
    assert out["findings"][0]["level"] == "campaign"
    assert [f["entity"] for f in out["findings"][1:]] == ["A6", "A5", "A4"]
    assert out["max_priority"] == "primary"


def test_a_single_ad_group_does_not_repeat_the_campaign():
    camp = both(pct={"conversion_rate": -20.0})
    group = {**camp, "name": "G1", "key": "k-G1"}
    out = d.diagnose_campaign(camp, [group], [])
    assert [f["level"] for f in out["findings"]] == ["campaign"]


# ------------------------------------------------------------ rules --

@pytest.mark.parametrize("ev,held,agrees,want", [
    (None, ["7v7"], True, "low"),
    ("insufficient", ["7v7", "14v14"], True, "low"),
    ("directional", ["7v7", "14v14"], True, "low"),
    ("moderate", ["7v7"], False, "low"),
    ("moderate", ["7v7"], True, "medium"),
    ("moderate", ["7v7", "14v14"], False, "medium"),
    ("strong", ["7v7"], True, "medium"),
    ("strong", ["7v7", "14v14"], True, "high"),
])
def test_confidence(ev, held, agrees, want):
    assert d.confidence(ev, held, agrees) == want


@pytest.mark.parametrize("pattern,ev,held,want", [
    ("post_click", "insufficient", ["7v7"], ["monitor"]),
    ("post_click", "directional", ["7v7"], ["monitor", "test"]),
    ("auction_cost", "directional", ["7v7"], ["monitor"]),
    ("post_click", "moderate", ["7v7", "14v14"], ["monitor", "test"]),
    ("post_click", "strong", ["7v7", "14v14"], ["monitor", "test", "act"]),
    ("post_click", "strong", ["7v7"], ["monitor", "test"]),
    ("auction_cost", "strong", ["7v7", "14v14"], ["monitor"]),
    ("no_conversions_spend", "moderate", ["7v7"],
     ["monitor", "test", "act"]),
    ("no_conversions_spend", "directional", ["7v7"], ["monitor", "test"]),
])
def test_allowed(pattern, ev, held, want):
    assert d.allowed(pattern, ev, held) == want


# ------------------------------------------------------------ control --

def _ads_for_control(incumbent_spec):
    return [both(level="ad", name="Inc", conv=40, spend=900.0,
                 **incumbent_spec),
            both(level="ad", name="Next", conv=20, spend=500.0),
            both(level="ad", name="Third", conv=5, spend=100.0)]


def test_incumbent_is_protected_when_the_move_is_post_click():
    ads = _ads_for_control({"pct": {"conversion_rate": -25.0}})
    out = d.diagnose_campaign(both(), [], ads)
    assert "post_click" in patterns(out)
    c = out["control"]
    assert c["ad"] == c["incumbent"] == "Inc"
    assert c["protected"] is True and c["incumbent_deteriorated"] is False
    assert c["why"]


def test_incumbent_is_replaced_as_control_when_fatigued():
    ads = _ads_for_control({"fatigue_consistent": True})
    out = d.diagnose_campaign(both(), [], ads)
    c = out["control"]
    assert c["incumbent"] == "Inc" and c["ad"] == "Next"
    assert c["incumbent_deteriorated"] is True and c["protected"] is False
    assert "Next" in c["why"]


def test_directional_fatigue_does_not_cost_the_incumbent_its_place():
    ads = _ads_for_control({"fatigue_consistent": True, "ev": "directional"})
    c = d.diagnose_campaign(both(), [], ads)["control"]
    assert c["ad"] == "Inc" and c["protected"] is True


# ------------------------------------------------------- concentration --

def test_efficient_concentration_is_not_a_finding_but_a_not_yet():
    group = both(level="ad_group", name="G1", key="k-G1", cpa=40.0)
    ads = [both(level="ad", name="A", spend=600.0, cpa=30.0),
           both(level="ad", name="B", spend=300.0, cpa=35.0),
           both(level="ad", name="C", spend=50.0, cpa=90.0)]
    out = d.diagnose_campaign(both(), [group], ads,
                              spread={"top_two_ads_share_pct": 85.0})
    conc = out["concentration"]
    assert conc["concentrated"] is True and conc["efficient"] is True
    assert set(conc) == {"concentrated", "efficient", "reason"}
    assert patterns(out) == ["steady"]
    assert ('Reducing delivery to "A" and "B" only because they carry most '
            "of the spend") in " ".join(out["not_yet"])


def test_inefficient_concentration_says_why_and_seeds_nothing():
    group = both(level="ad_group", name="G1", key="k-G1", cpa=40.0)
    ads = [both(level="ad", name="A", spend=600.0, cpa=60.0),
           both(level="ad", name="B", spend=300.0, cpa=35.0)]
    out = d.diagnose_campaign(both(), [group], ads,
                              spread={"top_two_ads_share_pct": 85.0})
    assert out["concentration"]["efficient"] is False
    assert '"A"' in out["concentration"]["reason"]
    assert not any("Reducing delivery" in s for s in out["not_yet"])
    assert d.diagnose_campaign(both(), [], [])["concentration"] is None


# --------------------------------------------------------- rating rule --

def _entry(r7=None, r14=None, r28=None, running=True, diagnosis=None, **kw):
    e = ent(r7=r7, r14=r14, r28=r28)
    return {"running": running, "rows": e["rows"],
            "diagnosis": diagnosis or {"findings": []}, **kw}


def test_red_on_a_strong_persistent_cpa_rise():
    rule = d.rating_rule(_entry(r7={"pct": {"cpa": 30.0}, "ev": "strong"},
                                r14={"pct": {"cpa": 12.0}}))
    assert rule["allowed"] == ["red"]
    assert "+30.00%" in rule["reasons"][0]


def test_moderate_persistent_rise_is_red_or_yellow():
    rule = d.rating_rule(_entry(r7={"pct": {"cpa": 30.0}},
                                r14={"pct": {"cpa": 12.0}}))
    assert rule["allowed"] == ["red", "yellow"]


def test_a_rise_that_does_not_persist_is_not_red():
    rule = d.rating_rule(_entry(r7={"pct": {"cpa": 30.0}, "ev": "strong"},
                                r14={"pct": {"cpa": 4.0}}))
    assert "red" not in rule["allowed"]


def test_zero_conversion_spend_at_three_times_baseline_is_red():
    rule = d.rating_rule(_entry(r7={"conv": 0, "spend_to_baseline_cpa": 3.0},
                                r14={}))
    assert rule["allowed"] == ["red"]


def test_a_material_ad_group_with_zero_conversion_spend_is_red():
    g = {P7: row(level="ad_group", name="G1", conv=0,
                 spend_to_baseline_cpa=4.0, parent_spend_share_pct=30.0)}
    small = {P7: row(level="ad_group", name="G2", conv=0,
                     spend_to_baseline_cpa=4.0, parent_spend_share_pct=10.0)}
    assert d.rating_rule(_entry(r7={}, r14={}, groups_rows=[g]))[
        "allowed"] == ["red"]
    assert "red" not in d.rating_rule(_entry(r7={}, r14={},
                                             groups_rows=[small]))["allowed"]


def test_not_running_is_yellow_or_green():
    rule = d.rating_rule({"running": False, "diagnosis": {"findings": []}})
    assert rule == {"allowed": ["yellow", "green"],
                    "reasons": ["not running in the last 7 settled days"]}


def test_a_goal_that_never_converts_is_yellow_only():
    rule = d.rating_rule(_entry(r7={"conv": 0, "spend_to_baseline_cpa": None},
                                r14={"conv": 0}, r28={"conv": 0},
                                conversions=0))
    # row() puts conv in both cur_m and pri_m, so nothing converted anywhere.
    assert rule["allowed"] == ["yellow"]
    assert "cost could not be judged" in rule["reasons"][0]


def test_green_on_steady_cpa_with_enough_conversions():
    rule = d.rating_rule(_entry(r7={}, r14={}, r28={"conv": 60}))
    assert rule["allowed"] == ["yellow", "green"]
    thin = d.rating_rule(_entry(r7={}, r14={}, r28={"conv": 12}))
    assert thin["allowed"] == ["yellow"]
    assert thin["reasons"][0].startswith("not green:")


def test_a_moderate_finding_blocks_green_but_fatigue_never_makes_red():
    diag = {"findings": [{"pattern": "possible_fatigue", "entity": "A",
                          "evidence": "strong"}]}
    rule = d.rating_rule(_entry(r7={}, r14={}, r28={"conv": 60},
                                diagnosis=diag))
    assert rule["allowed"] == ["yellow"]


def test_rating_rule_reads_the_pack_fields_without_rows():
    entry = {"running": True, "conversions": 80,
             "funnel": {"evidence": "strong", "spend_to_baseline_cpa": 20.0,
                        "metrics": [{"metric": "cpa", "pct_change": 40.0}]},
             "pct_14": {"cpa": 15.0}, "evidence_14": "strong",
             "ladder": [{"key": "last_7", "conversions": 20, "spend": 900.0},
                        {"key": "last_28", "conversions": 80}],
             "diagnosis": {"findings": []}}
    assert d.rating_rule(entry)["allowed"] == ["red"]


# ------------------------------------------------------------- clamps --

@pytest.mark.parametrize("asked,ok,want", [
    ("act", ["monitor", "test", "act"], "act"),
    ("act", ["monitor", "test"], "test"),
    ("act", ["monitor"], "monitor"),
    ("test", ["monitor"], "monitor"),
    ("monitor", ["test", "act"], "test"),
    ("bogus", ["monitor", "test"], "monitor"),
    ("act", [], "monitor"),
])
def test_clamp_type(asked, ok, want):
    assert d.clamp_type(asked, ok) == want


def test_clamp_confidence_and_priority():
    assert d.clamp_confidence("high", "medium") == "medium"
    assert d.clamp_confidence("low", "high") == "low"
    assert d.clamp_confidence("sure", "high") == "low"
    assert d.clamp_priority("primary", "secondary") == "secondary"
    assert d.clamp_priority("monitor", "primary") == "monitor"
    assert d.clamp_priority(None, "primary") == "monitor"


def test_clamp_rating_matches_creative():
    assert d.clamp_rating("red", {"allowed": ["yellow", "green"]}) == "yellow"
    assert d.clamp_rating("green", {"allowed": ["red"]}) == "red"
    assert d.clamp_rating("green", {"allowed": ["yellow", "green"]}) == "green"
    assert d.clamp_rating(None, {"allowed": ["red"]}) is None


# ---------------------------------------------------------- the module --

def test_the_module_computes_nothing():
    """Stricter than tests/test_ad_readings.py: no + - * / // % ** at all,
    no augmented assignment, no sum(), and a unary minus only on a literal.
    Every figure diagnose.py touches came from SQL; it compares, it never
    derives."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, (
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                ast.Pow, ast.MatMult)), (
                f"arithmetic at line {node.lineno} of {MODULE.name}")
        assert not isinstance(node, ast.AugAssign), (
            f"augmented assignment at line {node.lineno} of {MODULE.name}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            assert isinstance(node.operand, ast.Constant), (
                f"negation of a value at line {node.lineno} of {MODULE.name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "sum", (
                f"sum() at line {node.lineno} of {MODULE.name}")


def test_the_module_imports_only_the_standard_library():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            assert node.level == 0, "no relative imports: it stands alone"
    for bad in ("db", "db_owner", "psycopg"):
        assert not any(n == bad or n.startswith(f"{bad}.") for n in names)
    assert names <= {"__future__"}, names


def test_a_campaign_level_wobble_is_worded_as_this_campaign():
    f = {"level": "campaign", "entity": "07082025 | Agent | Franchise",
         "pattern": "post_click", "held_in": ["7v7"], "_has_14": True}
    g = {**f, "level": "ad", "entity": "Become an Agent | Static"}
    seeds = d.not_yet_seeds([f, g], None)
    assert ("Acting on the 7-day move in this campaign: the 14-day "
            "comparison does not show it yet.") in seeds
    assert any('"Become an Agent | Static"' in s for s in seeds)
    assert not any("07082025" in s for s in seeds)


# --------------------------------------------------- family context (019) --

def fam(cpa=None, link_ctr=None, conversion_rate=None, weeks=12,
        band_level="family"):
    """A pack campaign's `family` (contracts E2); each metric is a position."""
    metrics = {}
    for m, pos in (("cpa", cpa), ("link_ctr", link_ctr),
                   ("conversion_rate", conversion_rate)):
        metrics[m] = {"cur": 44.67, "p25": 28.1, "p50": 31.2, "p75": 36.4,
                      "position": pos, "percentile": 91.5}
    return {"family": "m_and_a", "audience": None, "goal":
            "OFFSITE_CONVERSIONS", "band_level": band_level,
            "campaign_weeks": weeks, "metrics": metrics}


def _fam_findings(family, ev="moderate"):
    out = d.diagnose_campaign(both(ev=ev), [], [], family=family)
    return out, [f for f in out["findings"]
                 if f["pattern"] == "outside_family_norm"]


def test_cpa_above_its_family_band_is_a_monitor_only_context_finding():
    out, (f,) = _fam_findings(fam(cpa="above", link_ctr="within"))
    assert f["level"] == "campaign" and f["held_in"] == ["7v7"]
    assert f["legs"] == {"cpa": "above"}
    assert f["allowed"] == ["monitor"] and f["test_variables"] == []
    # The campaign's own windows still speak: steady sits beside it.
    assert "steady" in patterns(out)
    assert any("typical range" in s for s in out["not_yet"])


@pytest.mark.parametrize("spec,legs,variables", [
    ({"link_ctr": "below"}, {"link_ctr": "below"}, ["creative"]),
    ({"conversion_rate": "below"}, {"conversion_rate": "below"},
     ["form", "landing_page", "offer"]),
    ({"cpa": "above", "link_ctr": "below", "conversion_rate": "below"},
     {"cpa": "above", "link_ctr": "below", "conversion_rate": "below"},
     ["creative", "form", "landing_page", "offer"]),
])
def test_a_ctr_or_cvr_below_its_band_may_be_tested(spec, legs, variables):
    _, (f,) = _fam_findings(fam(**spec), ev="strong")
    assert f["legs"] == legs
    assert f["test_variables"] == variables
    # Never act, however strong the evidence.
    assert f["allowed"] == ["monitor", "test"]


@pytest.mark.parametrize("family,ev", [
    (None, "moderate"),                                    # before 019
    (fam(cpa="above", band_level=None), "moderate"),       # no band
    (fam(cpa="above", weeks=7), "moderate"),               # thin band
    (fam(cpa="above"), "directional"),                     # thin week
    (fam(cpa="below", link_ctr="above",
         conversion_rate="above"), "strong"),              # the good side
    (fam(cpa="within", link_ctr="within"), "strong"),
    (fam(), "strong"),                                     # positions null
])
def test_no_family_finding_without_a_band_evidence_and_a_bad_side(family, ev):
    _, found = _fam_findings(family, ev=ev)
    assert found == []


def test_a_family_finding_never_makes_a_campaign_primary():
    out, (f,) = _fam_findings(fam(cpa="above", link_ctr="below"),
                              ev="strong")
    assert out["max_priority"] == "secondary"


def test_cpa_above_its_band_blocks_green_and_never_makes_red():
    diag = {"findings": [{"pattern": "outside_family_norm",
                          "evidence": "strong"}]}
    green = _entry(r7={}, r14={}, r28={"conv": 60}, diagnosis=diag,
                   family=fam(link_ctr="below", conversion_rate="below"))
    # CTR and CVR below their bands are context, not a green blocker.
    assert d.rating_rule(green)["allowed"] == ["yellow", "green"]
    above = _entry(r7={}, r14={}, r28={"conv": 60}, diagnosis=diag,
                   family=fam(cpa="above"))
    rule = d.rating_rule(above)
    assert rule["allowed"] == ["yellow"]
    assert ("CPA $44.67 sits above the typical $28.10 to $36.40 for this "
            "kind of campaign") in rule["reasons"][0]
    # No band, no requirement.
    unbanded = _entry(r7={}, r14={}, r28={"conv": 60},
                      family=fam(cpa="above", band_level=None))
    assert d.rating_rule(unbanded)["allowed"] == ["yellow", "green"]
    # And a band never turns a strong persistent rise into anything but red.
    red = _entry(r7={"pct": {"cpa": 30.0}, "ev": "strong"},
                 r14={"pct": {"cpa": 12.0}}, family=fam(cpa="below"))
    assert d.rating_rule(red)["allowed"] == ["red"]
