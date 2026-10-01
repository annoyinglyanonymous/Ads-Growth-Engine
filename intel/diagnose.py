"""A deterministic analyst: what the funnel figures allow a triage to say.

READ ONLY, AND NOT EVEN THAT. No database, no files, no clock. Every input is a
row ads.funnel_change (migration 017) already returned, and every output is a
selection from those rows plus a fixed sentence.

WHY THIS EXISTS

The model writing the campaign triage is good at sentences and bad at holding
a line. Left alone it read "two ads carry the spend" as "they are tiring" and
"use this other video" -- the jump the operator called out. So the line is
drawn here, before the model sees anything:

- which funnel PATTERN the figures show, on which windows, on what evidence;
- which recommendation TYPES (monitor < test < act) that evidence allows;
- how much CONFIDENCE it carries;
- which ad is the CONTROL for any test, and whether the incumbent is protected;
- whether concentration is a problem or just where the results are;
- which RATINGS the campaign's figures allow;
- and what NOT to do yet.

The model chooses inside those bounds, and creative.py clamps anything outside
them with the helpers at the bottom of this file.

PATTERNS ARE HYPOTHESES

A pattern is "the legs we look at moved this way", nothing more. Each is read
on two window pairs, last 7 vs prior 7 and last 14 vs prior 14, and records
which of the two it held in. A leg whose percentage is NULL is UNKNOWN, not
false: the pattern is not asserted, and the leg is listed in
`unreadable_legs` so the gap is visible instead of silent.

NOTHING HERE COMPUTES

CLAUDE.md's first rule. This module compares SQL figures with the constants
below, counts with len() and orders with tuple positions. There is no + - * /
anywhere in it, and tests/test_diagnose.py walks the AST to keep it that way.
A threshold that needs arithmetic to apply is a missing SQL column -- name it.
"""

from __future__ import annotations

# ---------------------------------------------------------------- bands --
# Percent changes, as ads.funnel_change prints them (-22.41 means down 22.41%).

#: Inside this band a rate is "steady". Ten percent either way is what a week
#: of ordinary auction noise moves a rate on this account's volumes.
STABLE_LO = -10.0
STABLE_HI = 10.0
#: At or past this a rate has MOVED. Wider than the steady band on purpose:
#: between the two is a wobble, and a wobble is neither steady nor a finding.
MOVED_UP = 15.0
MOVED_DOWN = -15.0
#: Daily frequency up this much counts. Daily frequency sits near 1.0-1.3, so
#: a small percent is a real change in how often the same people see the ad.
FREQ_UP = 8.0
#: Spend up this much before "more money, dearer results" is read as
#: diminishing returns rather than a normal week.
SPEND_UP = 25.0

# ------------------------------------------------------------ rating rule --

#: CPA up this much over 7 days is grounds for red -- if it persists.
RED_CPA_UP = 25.0
#: ...and "persists" means the 14-day comparison shows at least this much.
#: A 7-day move the 14-day comparison does not share is a wobble.
PERSIST_CPA_UP = 10.0
#: Zero conversions on this many times the entity's own prior CPA. Below it,
#: zero is what bad luck looks like (Poisson P(0) at 3x is about 5%).
ZERO_CONV_BASELINE_X = 3.0
#: Green needs this many conversions in the last 28 days; fewer is noise.
GREEN_MIN_CONVERSIONS = 20
#: An ad group carrying at least this share of its campaign's spend is
#: material: its zero-conversion spend can turn the campaign red.
MATERIAL_SHARE_PCT = 25.0

# ------------------------------------------------------------- structure --

#: Under this share of its parent's spend an entity is small enough that a
#: steady CPA reads as room to scale rather than as the whole account.
LOW_SHARE_PCT = 20.0
#: The top two ads carrying at least this share of a campaign is concentrated.
#: Concentrated is a description, not a problem: see concentration().
CONCENTRATED_PCT = 60.0
#: Per arm, before a test is decided. Carried in the pack as test_floor.
MIN_CONVERSIONS_PER_ARM = 10

# ---------------------------------------------------------- orderings --
# Ordered weakest first. Comparisons go through .index(), never arithmetic.

#: ads.funnel_change's evidence_level, in order.
EVIDENCE = ("insufficient", "directional", "moderate", "strong")
#: How sure an issue may claim to be.
CONFIDENCE = ("low", "medium", "high")
#: What an issue may recommend. Monitor < test < act.
TYPES = ("monitor", "test", "act")
#: How high an issue may rank.
PRIORITY = ("monitor", "secondary", "primary")
#: The ratings a triage may give. Same tuple creative.RATINGS holds.
RATINGS = ("red", "yellow", "green")

#: The window pairs read, and what a finding calls them.
P7 = "last_7_vs_prior_7"
P14 = "last_14_vs_prior_14"
P28 = "last_28_vs_prior_28"
WINDOWS = ((P7, "7v7"), (P14, "14v14"))

#: Level order in the findings list: the campaign speaks first.
LEVELS = ("campaign", "ad_group", "ad")

#: Most specific first. A post-click drop names its stage; "spend is up and
#: so is CPA" names none, so it ranks below every stage pattern.
SPECIFICITY = ("post_click", "clickier_poorer_intent", "possible_fatigue",
               "audience_saturation", "auction_cost", "creative_engagement",
               "diminishing_returns", "no_conversions_spend",
               "scaling_opportunity", "steady", "insufficient_evidence")

#: What a test of each pattern may change. Empty means nothing in the account
#: is the cause, so there is nothing to test: auction cost is the market.
TEST_VARIABLES = {
    "auction_cost": [],
    "creative_engagement": ["creative"],
    "post_click": ["landing_page", "form", "offer", "tracking"],
    "possible_fatigue": ["creative"],
    "audience_saturation": ["audience", "creative"],
    "clickier_poorer_intent": ["creative", "form"],
    "diminishing_returns": ["budget"],
    "scaling_opportunity": ["budget"],
    "no_conversions_spend": ["tracking", "audience", "creative"],
    "steady": [],
    "insufficient_evidence": [],
}

#: Patterns whose remedy is a direct change rather than a test.
ACTIONABLE = ("diminishing_returns", "scaling_opportunity",
              "no_conversions_spend")

#: The SQL primary_driver each pattern expects. Agreement raises confidence.
EXPECTED_DRIVER = {
    "auction_cost": "auction_cost",
    "audience_saturation": "auction_cost",
    "creative_engagement": "click_through",
    "possible_fatigue": "click_through",
    "post_click": "post_click",
    "clickier_poorer_intent": "post_click",
}

#: Patterns that count against an ad as the control. post_click and
#: auction_cost are deliberately absent: neither is the ad's doing.
DETERIORATING = ("creative_engagement", "possible_fatigue",
                 "clickier_poorer_intent")

#: The legs each pattern reads: (pct key, test). Tests are in _leg().
LEGS = {
    "auction_cost": (("cpm", "up"), ("link_ctr", "steady"),
                     ("conversion_rate", "steady")),
    "creative_engagement": (("link_ctr", "down"), ("cpm", "steady"),
                            ("conversion_rate", "steady")),
    "post_click": (("link_ctr", "steady"), ("conversion_rate", "down")),
    "possible_fatigue": (("link_ctr", "down"), ("daily_frequency", "freq_up")),
    "audience_saturation": (("cpm", "up"), ("daily_frequency", "freq_up")),
    "clickier_poorer_intent": (("link_ctr", "up"),
                               ("conversion_rate", "down")),
    "diminishing_returns": (("spend", "spend_up"), ("cpa", "up"),
                            ("cpm", "inside"), ("link_ctr", "inside"),
                            ("conversion_rate", "inside")),
}

#: Ad-level only: daily frequency exists only on an ad row.
AD_ONLY = ("possible_fatigue", "audience_saturation")

#: When the narrower pattern holds on an entity, the broader one says nothing
#: new and only spends one of the four finding slots.
SUBSUMES = {"possible_fatigue": "creative_engagement",
            "audience_saturation": "auction_cost"}

MAX_FINDINGS = 4


# ------------------------------------------------------------- readers --

def _rows(entity) -> dict:
    return (entity or {}).get("rows") or {}


def _row(entity, pair):
    return _rows(entity).get(pair)


def _pct(row, key):
    if not row:
        return None
    return (row.get("pct") or {}).get(key)


def _cur(row, key):
    if not row:
        return None
    return (row.get("cur_m") or {}).get(key)


def _rate(row, key):
    if not row:
        return None
    return ((row.get("cur_m") or {}).get("rates") or {}).get(key)


def _ev(ev) -> int:
    """Position in EVIDENCE; NULL reads as insufficient."""
    return EVIDENCE.index(ev) if ev in EVIDENCE else 0


def _at_least(ev, floor: str) -> bool:
    return _ev(ev) >= EVIDENCE.index(floor)


def _spend(entity):
    for pair in (P7, P14):
        v = _cur(_row(entity, pair), "spend")
        if v is not None:
            return v
    return 0


def _conversions(entity):
    return _cur(_row(entity, P7), "conversions") or 0


def _key(entity):
    if entity.get("key") is not None:
        return str(entity.get("key"))
    for row in _rows(entity).values():
        if row and row.get("entity_key") is not None:
            return str(row.get("entity_key"))
    return entity.get("name")


def _name(entity):
    if entity.get("name"):
        return entity.get("name")
    for row in _rows(entity).values():
        if row and row.get("entity_name"):
            return row.get("entity_name")
    return None


def _by_spend(entities) -> list:
    return sorted(entities or [], key=lambda e: float(_spend(e) or 0),
                  reverse=True)


# ---------------------------------------------------------------- legs --

def _leg(v, test: str):
    """True or False, or None when the percentage is NULL (unknown)."""
    if v is None:
        return None
    if test == "up":
        return v >= MOVED_UP
    if test == "down":
        return v <= MOVED_DOWN
    if test == "steady":
        return STABLE_LO <= v <= STABLE_HI
    if test == "freq_up":
        return v >= FREQ_UP
    if test == "spend_up":
        return v >= SPEND_UP
    if test == "inside":
        return MOVED_DOWN < v < MOVED_UP
    raise ValueError(test)


def _window(row, pattern: str) -> tuple:
    """("held" | "failed" | "unknown", [unknown pct keys]) for one row."""
    if pattern == "possible_fatigue" and row.get("fatigue_consistent") is True:
        return "held", []
    unknown, failed = [], False
    for metric, test in LEGS[pattern]:
        ok = _leg(_pct(row, metric), test)
        if ok is None:
            unknown.append(metric)
        elif not ok:
            failed = True
    if failed:
        return "failed", []
    if unknown:
        return "unknown", unknown
    return "held", []


def _legs(entity, metrics) -> dict:
    out = {}
    for metric in metrics:
        for pair, suffix in ((P7, "7"), (P14, "14")):
            v = _pct(_row(entity, pair), metric)
            if v is not None:
                out[f"{metric}_pct_{suffix}"] = v
    return out


def _held(entity, pattern: str, unreadable: list, level: str) -> list:
    held = []
    for pair, label in WINDOWS:
        row = _row(entity, pair)
        if not row:
            continue
        state, unknown = _window(row, pattern)
        if state == "held":
            held.append(label)
        elif state == "unknown":
            unreadable.append({"level": level, "entity": _name(entity),
                               "pattern": pattern, "window": label,
                               "legs": unknown})
    return held


def _zero_conversion_windows(entity) -> list:
    held = []
    for pair, label in WINDOWS:
        row = _row(entity, pair)
        if not row:
            continue
        stbc = row.get("spend_to_baseline_cpa")
        if (_cur(row, "conversions") == 0 and stbc is not None
                and stbc >= ZERO_CONV_BASELINE_X):
            held.append(label)
    return held


def _steady_cpa_windows(entity) -> list:
    held = []
    for pair, label in WINDOWS:
        v = _pct(_row(entity, pair), "cpa")
        if v is not None and v <= STABLE_HI:
            held.append(label)
    return held


def _row_for(entity, label):
    for pair, lab in WINDOWS:
        if lab == label:
            return _row(entity, pair)
    return None


def _best_evidence(entity, held_in):
    best = None
    for label in held_in:
        ev = (_row_for(entity, label) or {}).get("evidence_level")
        if best is None or _ev(ev) > _ev(best):
            best = ev
    return best or "insufficient"


# ---------------------------------------------------- confidence, types --

def confidence(evidence, held_in, driver_agrees: bool) -> str:
    """low | medium | high, from the evidence, both windows, and the driver.

    Directional evidence is never more than low: five conversions can move a
    rate by luck alone. Moderate needs one corroboration (both windows, or the
    SQL driver naming the same stage); strong needs both for high.
    """
    both = "7v7" in (held_in or []) and "14v14" in (held_in or [])
    if evidence == "moderate":
        return "medium" if both or driver_agrees else "low"
    if evidence == "strong":
        return "high" if both and driver_agrees else "medium"
    return "low"


def allowed(pattern: str, evidence, held_in) -> list:
    """The recommendation types this finding may carry, weakest first."""
    tv = TEST_VARIABLES.get(pattern) or []
    if not _at_least(evidence, "directional"):
        return ["monitor"]
    out = ["monitor"]
    if tv:
        out.append("test")
    if not _at_least(evidence, "moderate"):
        return out
    both = "7v7" in (held_in or []) and "14v14" in (held_in or [])
    if pattern in ACTIONABLE or (evidence == "strong" and both and tv):
        out.append("act")
    return out


# ------------------------------------------------------------ findings --

def _finding(entity, level, pattern, held_in, metrics, extra=None) -> dict:
    row7 = _row(entity, P7) or {}
    evidence = _best_evidence(entity, held_in)
    driver = row7.get("primary_driver")
    expected = EXPECTED_DRIVER.get(pattern)
    legs = _legs(entity, metrics)
    legs.update(extra or {})
    return {
        "id": None, "level": level, "entity": _name(entity),
        "pattern": pattern, "held_in": held_in, "legs": legs,
        "driver_7": driver, "evidence": evidence,
        "confidence": confidence(evidence, held_in,
                                 expected is not None and driver == expected),
        "allowed": allowed(pattern, evidence, held_in),
        "test_variables": list(TEST_VARIABLES[pattern]),
        "_key": _key(entity),
        "_has_14": bool(_row(entity, P14)),
    }


def _entity_findings(entity, level: str, unreadable: list) -> list:
    """Every pattern that held on one entity, in either window."""
    found = []
    for pattern, legs in LEGS.items():
        if pattern in AD_ONLY and level != "ad":
            continue
        held = _held(entity, pattern, unreadable, level)
        if held:
            found.append(_finding(entity, level, pattern, held,
                                  [m for m, _ in legs]))
    held = _zero_conversion_windows(entity)
    if held:
        extra = {}
        for pair, suffix in ((P7, "7"), (P14, "14")):
            row = _row(entity, pair)
            if row:
                extra[f"conversions_{suffix}"] = _cur(row, "conversions")
                extra[f"spend_to_baseline_cpa_{suffix}"] = row.get(
                    "spend_to_baseline_cpa")
        found.append(_finding(entity, level, "no_conversions_spend", held,
                              [], extra))
    if level != "ad":
        held = _steady_cpa_windows(entity)
        share = (_row(entity, P7) or {}).get("parent_spend_share_pct")
        if (held == ["7v7", "14v14"] and share is not None
                and share < LOW_SHARE_PCT
                and _at_least(_best_evidence(entity, held), "moderate")):
            found.append(_finding(entity, level, "scaling_opportunity", held,
                                  ["cpa"], {"parent_spend_share_pct": share}))
    patterns = [f["pattern"] for f in found]
    return [f for f in found
            if not any(SUBSUMES.get(p) == f["pattern"] for p in patterns)]


def _fallback(camp) -> dict:
    row7 = _row(camp, P7) or {}
    if _at_least(row7.get("evidence_level"), "directional"):
        held = [label for pair, label in WINDOWS
                if _row(camp, pair)
                and (_pct(_row(camp, pair), "cpa") is None
                     or STABLE_LO <= _pct(_row(camp, pair), "cpa") <= STABLE_HI)]
        f = _finding(camp, "campaign", "steady", held or ["7v7"], ["cpa"])
    else:
        f = _finding(camp, "campaign", "insufficient_evidence", ["7v7"],
                     ["cpa"])
    f["evidence"] = row7.get("evidence_level") or "insufficient"
    f["confidence"] = confidence(f["evidence"], f["held_in"], False)
    f["allowed"] = allowed(f["pattern"], f["evidence"], f["held_in"])
    return f


def _same_rows(a, b) -> bool:
    """An ad group whose figures ARE its campaign's (the one-ad-group case)."""
    for pair, _ in WINDOWS:
        ra, rb = _row(a, pair), _row(b, pair)
        if bool(ra) != bool(rb):
            return False
        if ra and (ra.get("cur_m") != rb.get("cur_m")
                   or ra.get("pri_m") != rb.get("pri_m")
                   or ra.get("pct") != rb.get("pct")):
            return False
    return True


def _public(f: dict) -> dict:
    return {k: v for k, v in f.items() if not k.startswith("_")}


# ------------------------------------------------------------- control --

def _deteriorated(entity, findings) -> tuple:
    """(bool, why) for one ad: has its own funnel gone backwards?"""
    for f in findings or []:
        if f["pattern"] in DETERIORATING and _at_least(f["evidence"],
                                                       "moderate"):
            return True, (f'shows {f["pattern"].replace("_", " ")} on '
                          f'{f["evidence"]} evidence')
    c7, c14 = _pct(_row(entity, P7), "cpa"), _pct(_row(entity, P14), "cpa")
    ev = _best_evidence(entity, ["7v7", "14v14"])
    if (c7 is not None and c14 is not None and c7 >= MOVED_UP
            and c14 >= MOVED_UP and _at_least(ev, "moderate")):
        return True, (f"CPA is up {c7}% over 7 days and {c14}% over 14, on "
                      f"{ev} evidence")
    return False, None


def _findings_of(ad_findings, entity) -> list:
    if not ad_findings:
        return []
    return ad_findings.get(_key(entity)) or ad_findings.get(_name(entity)) or []


def control_for(group, ads, ad_findings) -> dict | None:
    """The ad a test is measured against.

    group: the ad group entity the test runs in, or None.
    ads: ad entities (C3 shape); filtered to the group's when it has a key.
    ad_findings: {ad key (or name): [findings for that ad]}, uncapped.

    The incumbent is the ad with the most conversions in the last 7 days
    (ties on spend). It stays the control -- protected -- unless its own funnel
    has deteriorated. A post_click or auction_cost move is not the ad's doing
    and never counts against it.
    """
    pool = list(ads or [])
    if group is not None:
        gkey = _key(group)
        mine = [a for a in pool
                if str((_row(a, P7) or _row(a, P14) or {}).get("ad_group_key"))
                == gkey]
        if mine:
            pool = mine
    pool = [a for a in pool if _row(a, P7)]
    if not pool:
        return None
    ranked = sorted(pool, key=lambda a: (float(_conversions(a)),
                                         float(_spend(a) or 0)),
                    reverse=True)
    incumbent = ranked[0]
    bad, why_bad = _deteriorated(incumbent, _findings_of(ad_findings,
                                                         incumbent))
    inc_name = _name(incumbent)
    gname = _name(group) if group is not None else (
        (_row(incumbent, P7) or {}).get("ad_group_name"))
    if not bad:
        return {"ad_group": gname, "ad": inc_name, "incumbent": inc_name,
                "incumbent_deteriorated": False, "protected": True,
                "why": (f'"{inc_name}" has the most conversions in the last 7 '
                        f"days ({_conversions(incumbent)}) and its own funnel "
                        f"has not deteriorated, so it stays the control.")}
    control = None
    for a in ranked:
        if a is incumbent:
            continue
        if not _deteriorated(a, _findings_of(ad_findings, a))[0]:
            control = a
            break
    if control is None:
        return {"ad_group": gname, "ad": None, "incumbent": inc_name,
                "incumbent_deteriorated": True, "protected": False,
                "why": (f'"{inc_name}" has the most conversions but '
                        f"{why_bad}, and no other ad here has held up.")}
    return {"ad_group": gname, "ad": _name(control), "incumbent": inc_name,
            "incumbent_deteriorated": True, "protected": False,
            "why": (f'"{inc_name}" has the most conversions but {why_bad}; '
                    f'"{_name(control)}" is the next by conversions in the '
                    f"last 7 days ({_conversions(control)}) whose funnel has "
                    f"not deteriorated.")}


# -------------------------------------------------------- concentration --

def _group_of(ad, groups):
    gkey = str((_row(ad, P7) or {}).get("ad_group_key"))
    for g in groups or []:
        if _key(g) == gkey:
            return g
    return None


def concentration(spread, ads, ad_findings, groups=None) -> dict | None:
    """Is spend concentrated, and if so is that where the results are?

    spread: the 016 campaign_concentration row. groups: the campaign's ad
    groups, so each top ad is compared with ITS ad group's CPA under the same
    optimization goal. Concentrated-and-efficient is not an issue: it is the
    account working, and not_yet says so.
    """
    if not spread or spread.get("top_two_ads_share_pct") is None:
        return None
    share = spread.get("top_two_ads_share_pct")
    concentrated = share >= CONCENTRATED_PCT
    top = [a for a in _by_spend(ads) if _row(a, P7)][:2]
    problems = []
    for a in top:
        g = _group_of(a, groups)
        arow, grow = _row(a, P7) or {}, _row(g, P7) if g else None
        acpa, gcpa = _rate(arow, "cpa"), _rate(grow, "cpa")
        if grow is None:
            problems.append(f'"{_name(a)}" has no ad group figures to '
                            f"compare with")
        elif arow.get("optimization_goal") != grow.get("optimization_goal"):
            problems.append(f'"{_name(a)}" is not under one goal with its '
                            f"ad group")
        elif acpa is None or gcpa is None or acpa > gcpa:
            problems.append(f'"{_name(a)}" costs more per result than its '
                            f"ad group")
        elif _deteriorated(a, _findings_of(ad_findings, a))[0]:
            problems.append(f'"{_name(a)}" has deteriorated')
    efficient = bool(top) and not problems
    if not concentrated:
        reason = (f"The top two ads carry {share}% of the spend, under the "
                  f"{CONCENTRATED_PCT:g}% this calls concentrated.")
    elif efficient:
        reason = (f"The top two ads carry {share}% of the spend, and each "
                  f"converts at or under its ad group's cost with neither "
                  f"deteriorated: concentrated where the results are.")
    else:
        reason = (f"The top two ads carry {share}% of the spend, and "
                  f"{'; '.join(problems) or 'there are no ad figures'}.")
    return {"concentrated": concentrated, "efficient": efficient,
            "reason": reason, "_top": [_name(a) for a in top]}


# ---------------------------------------------------------------- not yet --

def not_yet_seeds(findings, conc) -> list:
    """Fixed sentences, built only from names and SQL figures."""
    out = []
    if conc and conc.get("concentrated") and conc.get("efficient"):
        names = conc.get("_top") or []
        if len(names) == 2:
            out.append(f'Reducing delivery to "{names[0]}" and "{names[1]}" '
                       f"only because they carry most of the spend: both "
                       f"convert at or under their ad group's cost and "
                       f"neither has deteriorated.")
        elif names:
            out.append(f'Reducing delivery to "{names[0]}" only because it '
                       f"carries most of the spend: it converts at or under "
                       f"its ad group's cost and has not deteriorated.")
    patterns = [f["pattern"] for f in findings]
    if "auction_cost" in patterns:
        out.append("Replacing the creative: the move is in the auction "
                   "(CPM), not in the ad.")
    if "post_click" in patterns:
        out.append("Replacing ads that still earn their clicks: the drop is "
                   "after the click.")
    for f in findings:
        if f["held_in"] == ["7v7"] and f.get("_has_14") and f["pattern"] not in (
                "steady", "insufficient_evidence"):
            # A campaign-level finding is "this campaign": the card is
            # already the campaign's, and its full name read as a second,
            # different warning beside the model's own "this campaign" line.
            where = ("this campaign" if f.get("level") == "campaign"
                     else f'"{f["entity"]}"')
            s = (f"Acting on the 7-day move in {where}: the 14-day "
                 f"comparison does not show it yet.")
            if s not in out:
                out.append(s)
    return out


# ------------------------------------------------------------ campaign --

def diagnose_campaign(camp, groups, ads, spread=None) -> dict:
    """The C3 diagnosis for one campaign.

    camp, each of groups and each of ads: {"name", "key", "goal", "status",
    "rows": {pair: ads.funnel_change row or None}}. spread: 016's row or None.

    Besides the C3 keys the result carries "unreadable_legs": every pattern a
    NULL percentage stopped from being read, so "not found" and "could not be
    read" stay different things.
    """
    unreadable: list = []
    all_findings = [*_entity_findings(camp, "campaign", unreadable)]
    for order, g in enumerate(_by_spend(groups)):
        if _same_rows(g, camp):
            continue
        for f in _entity_findings(g, "ad_group", unreadable):
            f["_order"] = order
            all_findings.append(f)
    ad_findings: dict = {}
    for order, a in enumerate(_by_spend(ads)):
        mine = _entity_findings(a, "ad", unreadable)
        for f in mine:
            f["_order"] = order
        ad_findings[_key(a)] = mine
        all_findings.extend(mine)

    if not all_findings and _row(camp, P7):
        all_findings = [_fallback(camp)]
    # Level, then the entity's place by spend, then the most specific pattern.
    ordered = sorted(all_findings, key=lambda f: (
        LEVELS.index(f["level"]), f.get("_order", 0),
        SPECIFICITY.index(f["pattern"])))
    shown = ordered[:MAX_FINDINGS]
    for i, f in enumerate(shown, start=1):
        f["id"] = f"d{i}"

    top_group = (_by_spend(groups) or [None])[0]
    control = control_for(top_group, ads, ad_findings)
    conc = concentration(spread, ads, ad_findings, groups)

    real = [f for f in shown
            if f["pattern"] not in ("steady", "insufficient_evidence")]
    if any(_at_least(f["evidence"], "moderate") for f in real):
        max_priority = "primary"
    elif any(_at_least(f["evidence"], "directional") for f in real):
        max_priority = "secondary"
    else:
        max_priority = "monitor"

    return {
        "evidence": (_row(camp, P7) or {}).get("evidence_level"),
        "findings": [_public(f) for f in shown],
        "control": control,
        "concentration": _public(conc) if conc else None,
        "max_priority": max_priority,
        "not_yet": not_yet_seeds(shown, conc),
        "unreadable_legs": unreadable,
    }


# ---------------------------------------------------------- rating rule --

def _money(v) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _signed(v) -> str:
    try:
        return f"{float(v):+.2f}%"
    except (TypeError, ValueError):
        return str(v)


def _ladder(entry, key):
    for rung in entry.get("ladder") or []:
        if rung.get("key") == key:
            return rung
    return {}


def _funnel_cpa_pct(entry):
    for m in (entry.get("funnel") or {}).get("metrics") or []:
        if m.get("metric") == "cpa":
            return m.get("pct_change")
    return None


def _rating_view(entry) -> dict:
    """The figures rating_rule reads, from entry["rows"] when present and
    from the C4 pack fields (funnel, pct_14, evidence_14, ladder) otherwise."""
    rows = entry.get("rows") or {}
    r7, r14, r28 = rows.get(P7), rows.get(P14), rows.get(P28)
    funnel = entry.get("funnel") or {}
    v = {
        "ev7": r7.get("evidence_level") if r7 else funnel.get("evidence"),
        "cpa7": _pct(r7, "cpa") if r7 else _funnel_cpa_pct(entry),
        "ev14": r14.get("evidence_level") if r14 else entry.get("evidence_14"),
        "cpa14": (_pct(r14, "cpa") if r14
                  else (entry.get("pct_14") or {}).get("cpa")),
        "conv7": (_cur(r7, "conversions") if r7
                  else _ladder(entry, "last_7").get("conversions")),
        "spend7": (_cur(r7, "spend") if r7
                   else _ladder(entry, "last_7").get("spend")),
        "stbc": (r7.get("spend_to_baseline_cpa") if r7
                 else funnel.get("spend_to_baseline_cpa")),
        "conv28": (_cur(r28, "conversions") if r28
                   else _ladder(entry, "last_28").get("conversions")),
    }
    seen = []
    for row in rows.values():
        if row:
            seen.extend([(row.get("cur_m") or {}).get("conversions"),
                         (row.get("pri_m") or {}).get("conversions")])
    seen.extend(r.get("conversions") for r in entry.get("ladder") or [])
    seen.append(entry.get("conversions"))
    v["converting"] = any(c is not None and c > 0 for c in seen)
    return v


def _group_rows(entry) -> list:
    out = []
    for g in entry.get("groups_rows") or []:
        rows = g.get("rows") if isinstance(g.get("rows"), dict) else g
        row = (rows or {}).get(P7)
        if row:
            out.append(row)
    return out


def rating_rule(entry) -> dict:
    """{"allowed": [...], "reasons": [...]} for one pack campaign.

    INPUT SHAPE. entry is the C4 pack campaign entry with "diagnosis"
    attached, plus, when the caller has them:
      entry["rows"]        {pair name: campaign-level funnel_change row or None}
                           -- last_7_vs_prior_7, last_14_vs_prior_14 and
                           last_28_vs_prior_28 are read;
      entry["groups_rows"] [{pair name: ad_group row}, ...] or C3 entities
                           ({"name", "rows": {...}}); only 7v7 is read.
    Without "rows" it falls back to the pack's own copies of the same SQL
    figures: funnel.evidence / funnel.metrics[cpa].pct_change /
    funnel.spend_to_baseline_cpa (7v7), pct_14.cpa and evidence_14 (14v14),
    and the ladder's last_7 and last_28 conversions.

    Red is about cost only. Fatigue, concentration and rank in goal never
    make a campaign red: they are reasons to look, not evidence of loss.
    """
    if entry.get("running") is False:
        return {"allowed": ["yellow", "green"],
                "reasons": ["not running in the last 7 settled days"]}
    v = _rating_view(entry)
    if not v["converting"]:
        return {"allowed": ["yellow"],
                "reasons": ["cost could not be judged: no conversions in "
                            "any window"]}

    red = []
    if (v["conv7"] == 0 and v["stbc"] is not None
            and v["stbc"] >= ZERO_CONV_BASELINE_X):
        red.append(f"no conversions in the last 7 days on "
                   f"{_money(v['spend7'])}, {v['stbc']}x the campaign's prior "
                   f"CPA")
    for row in _group_rows(entry):
        share, stbc = row.get("parent_spend_share_pct"), row.get(
            "spend_to_baseline_cpa")
        if (_cur(row, "conversions") == 0 and stbc is not None
                and stbc >= ZERO_CONV_BASELINE_X and share is not None
                and share >= MATERIAL_SHARE_PCT):
            red.append(f'"{row.get("entity_name")}" has no conversions in '
                       f"the last 7 days on {_money(_cur(row, 'spend'))}, "
                       f"{stbc}x its prior CPA, at {share}% of the "
                       f"campaign's spend")
    cpa7, cpa14 = v["cpa7"], v["cpa14"]
    persists = (cpa7 is not None and cpa14 is not None
                and cpa7 >= RED_CPA_UP and cpa14 >= PERSIST_CPA_UP)
    rise = (f"CPA {_signed(cpa7)} over 7 days and {_signed(cpa14)} over 14, "
            f"on {v['ev7']} evidence")
    if persists and v["ev7"] == "strong":
        red.append(rise)
    if red:
        return {"allowed": ["red"], "reasons": red}
    if persists and v["ev7"] == "moderate":
        return {"allowed": ["red", "yellow"], "reasons": [rise]}

    blockers = []
    if v["conv28"] is None or v["conv28"] < GREEN_MIN_CONVERSIONS:
        blockers.append(f"{v['conv28'] or 0} conversions in the last 28 "
                        f"days, under {GREEN_MIN_CONVERSIONS}")
    if not _at_least(v["ev14"], "moderate"):
        blockers.append(f"14-day evidence is {v['ev14'] or 'insufficient'}")
    if cpa7 is None or cpa14 is None:
        blockers.append("no CPA change to read in both windows")
    elif cpa7 > STABLE_HI or cpa14 > STABLE_HI:
        blockers.append(f"CPA {_signed(cpa7)} over 7 days and "
                        f"{_signed(cpa14)} over 14")
    for f in (entry.get("diagnosis") or {}).get("findings") or []:
        if (f.get("pattern") not in ("steady", "scaling_opportunity",
                                     "insufficient_evidence")
                and _at_least(f.get("evidence"), "moderate")):
            blockers.append(f'{f.get("pattern", "").replace("_", " ")} in '
                            f'"{f.get("entity")}" on {f.get("evidence")} '
                            f"evidence")
            break
    if not blockers:
        return {"allowed": ["yellow", "green"],
                "reasons": [f"CPA {_signed(cpa7)} over 7 days and "
                            f"{_signed(cpa14)} over 14, on {v['conv28']} "
                            f"conversions in the last 28 days"]}
    return {"allowed": ["yellow"],
            "reasons": [f"not green: {'; '.join(blockers)}"]}


# ---------------------------------------------------------------- clamps --

def clamp_rating(rating, rule):
    """The model's rating if the rule allows it, else the nearest allowed."""
    ok = (rule or {}).get("allowed") or list(RATINGS)
    if rating in ok or rating is None:
        return rating
    return "yellow" if "yellow" in ok else ok[0]


def clamp_type(asked, allowed) -> str:
    """asked if allowed; else the highest allowed type below it; else the
    lowest allowed. Never moves a recommendation UP."""
    ok = [t for t in TYPES if t in (allowed or [])]
    if not ok:
        return "monitor"
    if asked in ok:
        return asked
    if asked in TYPES:
        below = [t for t in ok if TYPES.index(t) <= TYPES.index(asked)]
        if below:
            return below[-1]
    return ok[0]


def clamp_confidence(asked, cap) -> str:
    """The lower of the two. An unknown value reads as low."""
    a = asked if asked in CONFIDENCE else "low"
    c = cap if cap in CONFIDENCE else "low"
    return a if CONFIDENCE.index(a) <= CONFIDENCE.index(c) else c


def clamp_priority(asked, max_priority) -> str:
    """The lower of the two. An unknown value reads as monitor."""
    a = asked if asked in PRIORITY else "monitor"
    c = max_priority if max_priority in PRIORITY else "monitor"
    return a if PRIORITY.index(a) <= PRIORITY.index(c) else c
