"""The creative pack: what the account is currently saying, and what it cost.

READ ONLY. Nothing here writes, files or concludes.

WHY THIS IS A VERB AND NOT A PAGE HELPER

It began in ui.py, which was wrong the moment anything other than a page needed
it. `scripts/suggest.py` builds this after every import to publish a suggestion,
and ui.py renders the same pack underneath that suggestion as its evidence.
ui.py's own rule is that every page calls the same function the agent calls, and
a pack that lives in the web layer cannot be one of those.

TWO HALVES

`campaigns`, `challenger_pool` and `windows` are the campaign analysis: one
round trip to metrics.funnel_ladder (migration 017), read across the named
window pairs, with intel/diagnose.py deciding what the figures allow before a
model is asked anything. `goals`, `formats`, `tiring` and `dimensions` are the
creative evidence the /suggestions page renders under the triage; only
`dimensions` of those reaches the model.

GROUPED BY OPTIMIZATION GOAL, AND THAT IS THE WHOLE POINT

This account runs four. A single "best and worst ads" list would rank every
OFFSITE_CONVERSIONS ad a failure and every LEAD_GENERATION ad a success -- and a
reader asked about the COPY would then explain that difference in terms of the
writing. The grouping is what stops a measurement artefact being read as a
creative finding. The challenger pool is grouped the same way.

NOTHING HERE COMPUTES

Rows arrive from ads.window_metrics and ads.funnel_change with their rates,
changes, shares and evidence levels already calculated; this selects, orders
and reshapes them. Counting the rows a function returned is a len(), which
CLAUDE.md's first rule permits. tests/test_suggestions.py walks this module's
AST for arithmetic outside two named helpers (an age in hours and a rounding
tolerance), so the rule is held by a test rather than a promise.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from psycopg import errors as _pg_errors

from db import fetch_all, fetch_one

from . import context, diagnose, metrics

ROOT = Path(__file__).resolve().parent.parent

#: Where scripts/suggest.py publishes. One file per brand per day, beside the
#: briefs, for the reason scripts/brief.py gives about its own archive: a live
#: page silently rewrites its own past opinion every time Meta restates, so the
#: only way to ask what it said last week is to have kept a copy.
SUGGESTION_DIR = ROOT / "suggestions"

#: How many ads per optimization goal go into the page's `goals` block, at
#: each end.
PER_GOAL = 3

#: Rows per creative dimension in the pack. Ten, by spend.
DIMENSION_ROWS = 10

#: The rate every goal here is ranked on. One metric, because the ads inside a
#: single optimization goal ARE comparable on it -- that is what makes the
#: grouping worth doing.
RANK_RATE = "cpa"

#: Per campaign: how many ad groups and how many of its ads ride along. The
#: ads are the control, the incumbent and the top spender -- one ad when they
#: are the same one, which is the usual case.
CAMPAIGN_AD_GROUPS = 3
CAMPAIGN_ADS = 3

#: How much of an ad's body a campaign entry carries as its `opening`. The
#: first line is what a reader stops or scrolls on, and twenty-odd campaigns
#: of longer openings is budget the diagnosis needs.
CAMPAIGN_BODY_CHARS = 120

#: Converting ads per optimization goal in the challenger pool.
CHALLENGERS_PER_GOAL = 3


# ------------------------------------------------------------ rating rule --
# WHICH RATINGS THE NUMBERS ALLOW, decided by intel/diagnose.py and not by the
# model. Left to the model alone, two runs over the SAME window disagreed. So
# the figures set the bounds, the model chooses within them and writes the
# reasons, and a rating outside the bounds is moved to the nearest allowed one
# -- and the card says it was. Re-exported here because scripts and tests have
# always reached them through this module.

rating_rule = diagnose.rating_rule
clamp_rating = diagnose.clamp_rating
GREEN_MIN_CONVERSIONS = diagnose.GREEN_MIN_CONVERSIONS


async def _concentration(camp: dict) -> dict:
    """campaign_key -> the spend-spread figures ads.campaign_concentration
    returns, or {} when migration 016 has not been applied.

    The function is the only place these totals may come from (CLAUDE.md's
    first rule), and until it exists the pack goes without them rather than
    computing them here.
    """
    try:
        rows = await fetch_all(
            """
            select campaign_key, ads_with_spend, top_ad_spend,
                   top_two_ads_spend, top_ad_share_pct, top_two_ads_share_pct,
                   ads_with_no_conversions, spend_on_ads_with_no_conversions,
                   share_on_ads_with_no_conversions_pct
              from ads.campaign_concentration(%s::uuid, %s, %s)
            """, (camp.get("brand_id"), camp.get("since"), camp.get("until")))
    except _pg_errors.UndefinedFunction:
        return {}
    return {str(r["campaign_key"]): {k: v for k, v in r.items()
                                     if k != "campaign_key"}
            for r in rows}


def _rate(row: dict, name: str = RANK_RATE):
    """One rate out of the `rates` object a SQL function returned.

    Reading a value out of a function's output is not computing one. Nothing
    here divides, sums or averages -- CLAUDE.md's first rule, and the reason
    this returns None rather than 0 for a missing rate: a null CPA is
    undefined, not zero, and must never be ranked.
    """
    value = (row.get("rates") or {}).get(name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------- the funnel ladder --
# Every figure below is READ from an ads.funnel_change row (C1): cur_m and
# pri_m for the window figures, pct for the changes, the cpa_share_* columns
# for the decomposition. A rung of the ladder is one side of one named pair,
# so "last 7" and "prior 7" are the two sides of last_7_vs_prior_7 and never
# a sum of days done here.

PAIR_UNSETTLED = "unsettled_vs_last_7"
P7, P14, P28 = diagnose.P7, diagnose.P14, diagnose.P28
PAIR_BEFORE = "last_7_vs_before"

#: Where an attribute that is the same on every pair (name, goal, status,
#: parent keys) is looked up first.
PAIR_ORDER = (P7, P14, P28, PAIR_BEFORE, PAIR_UNSETTLED)

#: The ladder, short end first: (key, label, pair, side, provisional).
RUNGS = (
    ("unsettled", "Unsettled days", PAIR_UNSETTLED, "cur", True),
    ("last_7", "Last 7 days", P7, "cur", False),
    ("prior_7", "Prior 7 days", P7, "pri", False),
    ("last_14", "Last 14 days", P14, "cur", False),
    ("prior_14", "Prior 14 days", P14, "pri", False),
    ("last_28", "Last 28 days", P28, "cur", False),
    ("before", "Before the last 7 days", PAIR_BEFORE, "pri", False),
)

#: A rung's columns: (column, rate name in `rates`, or None for a count).
RUNG_FIGURES = (("spend", None), ("conversions", None), ("cpa", "cpa"),
                ("cpm", "cpm"), ("link_ctr", "link_ctr"),
                ("cvr", "conversion_rate"), ("daily_frequency", None))

#: The 7v7 funnel the page draws: (metric, rate in cur_m/pri_m, pct key).
#: `frequency` is daily frequency, ad level only, so it drops out at campaign
#: level rather than printing as an empty row.
FUNNEL_METRICS = (("cpm", "cpm", "cpm"), ("link_ctr", "link_ctr", "link_ctr"),
                  ("cpc", "cost_per_link_click", "cost_per_link_click"),
                  ("cvr", "conversion_rate", "conversion_rate"),
                  ("cpa", "cpa", "cpa"),
                  ("frequency", None, "daily_frequency"))

#: The percentage changes the model reads. impressions, link_clicks,
#: spend_per_active_day and cost_per_link_click are left in the row: each is
#: one of these restated, and four of them on 25 campaigns is budget.
PCT_KEYS = ("spend", "conversions", "cpm", "link_ctr", "conversion_rate",
            "cpa", "daily_frequency")


def _side(row, side: str) -> dict:
    return (row or {}).get(f"{side}_m") or {}


def _first(rows: dict, field: str):
    for pair in PAIR_ORDER:
        r = rows.get(pair)
        if r and r.get(field) is not None:
            return r.get(field)
    return None


def _spend_of(rows: dict, pair: str):
    return _side(rows.get(pair), "cur").get("spend") or 0


def _spent(rows: dict, pair: str) -> bool:
    return float(_spend_of(rows, pair)) > 0


def _index(ladder: dict) -> dict:
    """level -> entity key -> {pair: row}. Grouping, not arithmetic."""
    idx: dict = {}
    for pair, p in (ladder.get("pairs") or {}).items():
        for r in p.get("rows") or []:
            idx.setdefault(r.get("level"), {}).setdefault(
                str(r.get("entity_key")), {})[pair] = r
    return idx


def _entity(key: str, rows: dict) -> dict:
    """The C3 entity shape intel/diagnose.py reads."""
    return {"name": _first(rows, "entity_name"), "key": key,
            "goal": _first(rows, "optimization_goal"),
            "status": _first(rows, "entity_status"), "rows": rows}


def _windows(ladder: dict) -> dict:
    return {pair: {k: p.get(k) for k in ("cur_since", "cur_until",
                                         "pri_since", "pri_until")}
            for pair, p in (ladder.get("pairs") or {}).items()}


def _ladder(rows: dict, windows: dict, withhold_cpa: bool) -> list:
    """The rungs, each read off one side of one pair. A rung whose pair has
    no row for this entity, or whose side is empty, is left out."""
    out = []
    for key, label, pair, side, provisional in RUNGS:
        m = _side(rows.get(pair), side)
        if not m:
            continue
        w = windows.get(pair) or {}
        rung = {"key": key, "label": label,
                "since": w.get(f"{side}_since"), "until": w.get(f"{side}_until"),
                "provisional": provisional}
        for column, rate in RUNG_FIGURES:
            rung[column] = ((m.get("rates") or {}).get(rate) if rate
                            else m.get(column))
        if withhold_cpa:
            # A campaign spanning two optimization goals has a CPA that
            # blends two events. The ad groups carry the comparable ones.
            rung["cpa"] = None
        out.append(rung)
    return out


def _pct_view(row) -> dict | None:
    pct = (row or {}).get("pct") or {}
    out = {k: pct[k] for k in PCT_KEYS if pct.get(k) is not None}
    return out or None


#: The four moves the collapsed card shows (contracts2 D4 `movement`).
MOVEMENT_KEYS = ("cpa", "link_ctr", "cost_per_link_click", "conversion_rate")


def _movement(row7, row14) -> dict:
    """{"7v7": {...}, "14v14": {...}}: 017's pct for MOVEMENT_KEYS, each
    window present only when it has at least one of them. Selected, never
    computed -- a missing key is a pct 017 left null."""
    out = {}
    for label, row in (("7v7", row7), ("14v14", row14)):
        pct = (row or {}).get("pct") or {}
        moves = {k: pct[k] for k in MOVEMENT_KEYS if pct.get(k) is not None}
        if moves:
            out[label] = moves
    return out


def _shares(row) -> dict | None:
    if not row:
        return None
    shares = {"auction_cost": row.get("cpa_share_cpm_pct"),
              "click_through": row.get("cpa_share_link_ctr_pct"),
              "post_click": row.get("cpa_share_conversion_rate_pct")}
    return shares if any(v is not None for v in shares.values()) else None


def _funnel(row, w: dict) -> dict | None:
    """The 7v7 campaign row reshaped for the page. Reshaping, not arithmetic:
    every value is a column of the row."""
    if not row:
        return None
    cur, pri, pct = _side(row, "cur"), _side(row, "pri"), row.get("pct") or {}
    rows = []
    for metric, rate, pct_key in FUNNEL_METRICS:
        c = ((cur.get("rates") or {}).get(rate) if rate
             else cur.get("daily_frequency"))
        p = ((pri.get("rates") or {}).get(rate) if rate
             else pri.get("daily_frequency"))
        change = pct.get(pct_key)
        if c is None and p is None and change is None:
            continue
        rows.append({"metric": metric, "cur": c, "prior": p,
                     "pct_change": change})
    return {"cur": {"since": w.get("cur_since"), "until": w.get("cur_until")},
            "prior": {"since": w.get("pri_since"), "until": w.get("pri_until")},
            "metrics": rows, "shares": _shares(row),
            "primary_driver": row.get("primary_driver"),
            "evidence": row.get("evidence_level"),
            "spend_to_baseline_cpa": row.get("spend_to_baseline_cpa"),
            "fatigue_consistent": row.get("fatigue_consistent")}


def _test_floor(rows: dict, windows: dict) -> dict:
    """What each arm of a test must reach before it is decided.

    The baseline is the entity's own prior-window CPA, longest window first:
    28 days is the steadiest read of "what a result usually costs here".
    decision_spend_per_arm is 017's column (baseline x its test multiple), so
    nothing is multiplied here.
    """
    for pair in (P28, P14, P7):
        r = rows.get(pair)
        if r and r.get("baseline_cpa") is not None:
            w = windows.get(pair) or {}
            return {"baseline_cpa": r.get("baseline_cpa"),
                    "decision_spend_per_arm": r.get("decision_spend_per_arm"),
                    "min_conversions_per_arm": diagnose.MIN_CONVERSIONS_PER_ARM,
                    "baseline_window": [w.get("pri_since"), w.get("pri_until")]}
    return {"baseline_cpa": None, "decision_spend_per_arm": None,
            "min_conversions_per_arm": diagnose.MIN_CONVERSIONS_PER_ARM,
            "baseline_window": None}


def _deteriorated(entity: dict) -> bool:
    """Has this ad's own funnel gone backwards? diagnose's rule, not a new
    one: the same test that decides whether an incumbent stays the control
    decides whether an ad may be offered as a challenger."""
    found = diagnose._entity_findings(entity, "ad", [])
    return diagnose._deteriorated(entity, found)[0]


def _goal_ranks(groups: dict) -> tuple:
    """(ad group key -> cpa rank in its goal, goal -> how many were ranked).

    An ORDERING of the CPAs 017 returned for the last 28 settled days, 1 the
    cheapest, among the ad groups optimising for the same event. Not a rate.
    """
    by_goal: dict = {}
    for key, rows in groups.items():
        r28 = rows.get(P28)
        cpa = _rate(_side(r28, "cur"))
        if cpa is not None:
            by_goal.setdefault((r28 or {}).get("optimization_goal") or "(none)",
                               []).append((cpa, key))
    rank, ranked_in = {}, {}
    for goal, items in by_goal.items():
        items.sort()
        for i, (_, key) in enumerate(items, 1):
            rank[key] = i
        ranked_in[goal] = len(items)
    return rank, ranked_in


def _ad_view(e: dict, roles: list, with_copy: bool, many_groups: bool) -> dict:
    r7 = e["rows"].get(P7) or {}
    m = _side(r7, "cur")
    view = {"name": e["name"], "role": ", ".join(roles)}
    if many_groups:
        view["ad_group"] = r7.get("ad_group_name")
    view.update({
        "spend": m.get("spend"),
        "share_pct": r7.get("parent_spend_share_pct"),
        "spend_rank_in_parent": r7.get("spend_rank_in_parent"),
        "conversions": m.get("conversions"),
        "cpa": _rate(m),
        "link_ctr": _rate(m, "link_ctr"),
        "fatigue_pattern": r7.get("fatigue_pattern"),
        "hook": None, "offer": None, "format": None,
        # Kept on the pack (and the published card) so the outcomes ledger
        # can measure the control by key; never sent (AD_NOT_SENT).
        "ad_key": e["key"],
        "_key": e["key"], "_with_copy": with_copy})
    return view


def _where_ads(ads: list, control: dict | None, many_groups: bool) -> list:
    """The control, the incumbent and the top spender -- one ad when they are
    the same, which is the usual case. Copy rides on the control and on one
    other ad only: the control is what a test keeps, and one other is enough
    to contrast it with."""
    by_spend = sorted([a for a in ads if a["rows"].get(P7)],
                      key=lambda a: float(_spend_of(a["rows"], P7)),
                      reverse=True)
    picked: list = []        # [(entity, [roles])]

    def pick(name, role):
        if not name:
            return
        for e, roles in picked:
            if e["name"] == name:
                roles.append(role)
                return
        for e in by_spend:
            if e["name"] == name:
                picked.append((e, [role]))
                return

    control = control or {}
    pick(control.get("ad"), "control")
    pick(control.get("incumbent"), "incumbent")
    if by_spend:
        pick(by_spend[0]["name"], "top spender")
    out, copied_other = [], False
    for e, roles in picked[:CAMPAIGN_ADS]:
        is_control = "control" in roles
        with_copy = is_control or not copied_other
        if with_copy and not is_control:
            copied_other = True
        out.append(_ad_view(e, roles, with_copy, many_groups))
    return out


def _challenger_pool(idx: dict) -> list:
    """Per optimization goal, the cheapest converting ads that have not
    deteriorated, over the last 28 settled days. What a test may borrow.

    Grouped by goal because a challenger from another goal is the cross-goal
    comparison the whole pack exists to prevent. `rankable_on_cost` is false
    when nothing under the goal converted: there is then nothing to borrow.
    """
    camp_names = {k: _first(rows, "entity_name")
                  for k, rows in (idx.get("campaign") or {}).items()}
    by_goal: dict = {}
    for key, rows in (idx.get("ad") or {}).items():
        r28 = rows.get(P28)
        m = _side(r28, "cur")
        if not m:
            continue
        slot = by_goal.setdefault(r28.get("optimization_goal") or "(none)", [])
        cpa = _rate(m)
        if not m.get("conversions") or cpa is None:
            continue
        e = _entity(key, rows)
        if _deteriorated(e):
            continue
        slot.append((cpa, e, m, r28))
    out = []
    for goal in sorted(by_goal):
        items = sorted(by_goal[goal], key=lambda t: t[0])
        out.append({
            "optimization_goal": goal,
            "rankable_on_cost": bool(items),
            "ranked_on": "cpa, last 28 settled days" if items else None,
            "ads": [{"ad": e["name"],
                     "from_campaign": camp_names.get(str(r28.get("campaign_key"))),
                     "status": e["status"],
                     "conversions": m.get("conversions"), "cpa": cpa,
                     "hook": None, "offer": None,
                     "headline": None, "opening": None,
                     "_key": e["key"], "_with_copy": True}
                    for cpa, e, m, r28 in items[:CHALLENGERS_PER_GOAL]],
        })
    return out


#: A campaign that spent nothing in the last 7 settled days: no change to
#: read, so no findings, and a rating that cannot be red.
def short_diagnosis() -> dict:
    return {"findings": [], "max_priority": "monitor", "not_yet": []}


async def _campaigns(ladder: dict, brand_id, families: dict | None = None
                     ) -> tuple:
    """(campaigns, challenger_pool, windows) from one funnel_ladder answer.

    A campaign is in the pack when it spent in the last 28 settled days, in
    order of that spend. One that spent nothing in the last 7 gets the short
    form: its 28-day figures and a rating rule that cannot be red.

    A CAMPAIGN HAS NO SINGLE GOAL, and this is built around that. Its own CPA
    is carried only when every ad group in it shares one goal; the evidence
    that IS comparable -- where each ad group sits among the ad groups
    optimising for the same thing -- is carried per ad group.
    """
    idx = _index(ladder)
    windows = _windows(ladder)
    pool = _challenger_pool(idx)
    camps = idx.get("campaign") or {}
    keys = [k for k, rows in camps.items()
            if _spent(rows, P28) or _spent(rows, P7)]
    keys.sort(key=lambda k: (float(_spend_of(camps[k], P28)),
                             float(_spend_of(camps[k], P7))), reverse=True)
    if not keys:
        return [], pool, windows

    meta = {str(r["campaign_key"]): r for r in await fetch_all(
        "select campaign_key, effective_status, objective from ads.campaign "
        " where campaign_key = any(%s::uuid[])", (keys,))}

    w7 = windows.get(P7) or {}
    spread = await _concentration({"brand_id": brand_id,
                                   "since": w7.get("cur_since"),
                                   "until": w7.get("cur_until")}) if w7 else {}

    groups = idx.get("ad_group") or {}
    rank, ranked_in = _goal_ranks(groups)
    groups_of: dict = {}
    for k, rows in groups.items():
        groups_of.setdefault(str(_first(rows, "campaign_key")), []).append(
            _entity(k, rows))
    ads_of: dict = {}
    for k, rows in (idx.get("ad") or {}).items():
        ads_of.setdefault(str(_first(rows, "campaign_key")), []).append(
            _entity(k, rows))

    out = []
    for key in keys:
        rows = camps[key]
        m28 = _side(rows.get(P28), "cur")
        mine = sorted(groups_of.get(key, []),
                      key=lambda g: (float(_spend_of(g["rows"], P7)),
                                     float(_spend_of(g["rows"], P28))),
                      reverse=True)
        goals = sorted({g["goal"] or "(none)" for g in mine}) or (
            [_first(rows, "optimization_goal")]
            if _first(rows, "optimization_goal") else [])
        one_goal = len(goals) == 1
        info = meta.get(key) or {}
        base = {"campaign": _first(rows, "entity_name"),
                "campaign_key": key,
                "status": info.get("effective_status")
                or _first(rows, "entity_status"),
                "objective": info.get("objective"),
                "optimization_goals": goals,
                "spend": m28.get("spend"),
                "conversions": m28.get("conversions"),
                "cpa": _rate(m28) if one_goal else None,
                "link_ctr": _rate(m28, "link_ctr"),
                # Typical ranges for this kind of campaign (019), or None:
                # context beside its own windows, read by diagnose and
                # rating_rule. See _families.
                "family": (families or {}).get(key)}

        if not _spent(rows, P7):
            entry = {**base, "running": False,
                     "diagnosis": short_diagnosis()}
            entry["rating_rule"] = rating_rule(entry)
            out.append(entry)
            continue

        ads = ads_of.get(key, [])
        camp = _entity(key, rows)
        diag = diagnose.diagnose_campaign(camp, mine, ads, spread.get(key),
                                          base["family"])
        row7, row14 = rows.get(P7), rows.get(P14)
        many = len(mine) > 1
        spending = [g for g in mine if _spent(g["rows"], P7)]

        ad_groups, where_groups = [], []
        for g in mine[:CAMPAIGN_AD_GROUPS]:
            g7 = g["rows"].get(P7) or {}
            gm = _side(g7, "cur")
            goal = g["goal"] or "(none)"
            item = {"ad_group": g["name"], "optimization_goal": g["goal"],
                    "status": g["status"]}
            if many:
                item.update({"spend": gm.get("spend"),
                             "conversions": gm.get("conversions"),
                             "cpa": _rate(gm),
                             "share_pct": g7.get("parent_spend_share_pct"),
                             "spend_rank_in_parent":
                                 g7.get("spend_rank_in_parent")})
            else:
                # Its figures ARE the campaign's: say so once rather than
                # repeating every number.
                item["same_as_campaign"] = True
            item.update({
                "cpa_rank_in_goal": rank.get(g["key"]),
                "ad_groups_ranked_in_goal": ranked_in.get(goal)
                if rank.get(g["key"]) else None,
                "test_floor": _test_floor(g["rows"], windows)})
            ad_groups.append(item)
            where_groups.append({
                "name": g["name"], "ad_group_key": g["key"], "goal": g["goal"],
                "spend": gm.get("spend"), "share_pct":
                    g7.get("parent_spend_share_pct"),
                "cpa": _rate(gm),
                "rank": g7.get("spend_rank_in_parent"),
                "of": len(spending), "same_as_campaign": not many})

        entry = {
            **base, "running": True,
            "ladder": _ladder(rows, windows, not one_goal),
            "pct_7": _pct_view(row7),
            "cpa_shares_7": _shares(row7),
            "driver_7": (row7 or {}).get("primary_driver"),
            "evidence_7": (row7 or {}).get("evidence_level"),
            "pct_14": _pct_view(row14),
            "driver_14": (row14 or {}).get("primary_driver"),
            "evidence_14": (row14 or {}).get("evidence_level"),
            "spend_to_baseline_cpa": (row7 or {}).get("spend_to_baseline_cpa"),
            "funnel": _funnel(row7, w7),
            # The page's movement strip: 017's pct, selected, never computed.
            "movement": _movement(row7, row14),
            # Names -> keys for every ad group and ad 017 returned for this
            # campaign, so a published issue can be registered in the
            # outcomes ledger by key. Page and ledger only (NOT_SENT).
            "entity_keys": {
                "ad_groups": {g["name"]: g["key"] for g in mine if g["name"]},
                "ads": {a["name"]: {"key": a["key"],
                                    "ad_group": _first(a["rows"],
                                                       "ad_group_name")}
                        for a in ads if a["name"]}},
            "ad_groups": ad_groups,
            "more_ad_groups": len(mine[CAMPAIGN_AD_GROUPS:]),
            "where": {"ad_groups": where_groups,
                      "ads": _where_ads(ads, diag.get("control"), many)},
            # How the campaign's spend is spread over the last 7 settled
            # days, from ads.campaign_concentration (016). None until that
            # migration is applied -- and then the prompt tells the model to
            # put a total it does not have into words.
            "spend_spread": spread.get(key),
            "diagnosis": diag,
            # Read by rating_rule, then dropped: they are the raw C1 rows,
            # several kilobytes a campaign, and every figure in them that
            # matters is already above.
            "rows": rows, "groups_rows": mine,
        }
        entry["rating_rule"] = rating_rule(entry)
        del entry["rows"], entry["groups_rows"]
        out.append(entry)
    return out, pool, windows


# ------------------------------------------------------- impact (018) --
# BUSINESS IMPACT is dollars, and dollars may be ordered across goals (016's
# argument): a dollar spent beyond last week's rate is a dollar whatever event
# bought it. A CPA or a contribution share is never compared across goals.
# Every figure is a column ads.goal_impact returned.

#: The pair impact is read on, and the fields a campaign carries.
IMPACT_PAIR = P7
IMPACT_FIELDS = ("excess_cost", "impact_score", "contribution_share_pct")

#: The facet dimensions read per goal for the page and the model. `format`
#: stays in `dimensions`; angle and family are empty on this brand.
GOAL_DIMENSIONS = ("hook", "offer", "audience")


async def _impact(brand: str, campaigns: list) -> tuple:
    """(available, why, rows): attaches `impact` to every campaign in place.

    Matched by campaign_key, then by name. A campaign spanning two goals has
    no campaign row in 018 (its CPA blends two events), so it carries
    impact None -- as does every campaign before 018 is applied.
    """
    try:
        res = await metrics.goal_impact(brand, IMPACT_PAIR)
    except ValueError as exc:
        res = {"available": False, "why": str(exc)}
    rows = (res.get("rows") or []) if res.get("available") else []
    camp_rows = [r for r in rows if r.get("level") == "campaign"]
    by_key = {str(r.get("campaign_key")): r for r in camp_rows
              if r.get("campaign_key") is not None}
    by_name = {r.get("campaign_name"): r for r in camp_rows}
    for c in campaigns:
        r = by_key.get(str(c.get("campaign_key"))) or by_name.get(
            c.get("campaign"))
        c["impact"] = ({"goal": r.get("optimization_goal"),
                        **{k: r.get(k) for k in IMPACT_FIELDS},
                        "evidence": r.get("evidence_level")} if r else None)
    return bool(res.get("available")), res.get("why"), rows


# ------------------------------------------------- family bands (019) --
# WHAT IS NORMAL FOR THIS KIND OF CAMPAIGN IN THIS ACCOUNT: per campaign
# family (hiring, B-roll, M&A, franchise, other), audience temperature and
# optimization goal, the p25-p75 of 26 weeks of campaign-weeks, from
# ads.campaign_vs_family. Context only: each campaign is still judged against
# its own earlier windows first, and a band is never read across goals (the
# SQL keys every band by goal). Every figure is a column 019 returned.

#: The pair the family comparison is read on: this week, as the ladder's.
FAMILY_PAIR = P7
#: The metrics a family band carries, and the fields each one keeps.
FAMILY_METRICS = ("cpa", "cpm", "link_ctr", "cost_per_link_click",
                  "conversion_rate")
FAMILY_BAND_FIELDS = ("cur", "p25", "p50", "p75", "position", "percentile")


async def _call_019(name: str, *args) -> dict:
    """metrics.<name>(*args), or "not available" -- before 019 is applied,
    and before intel/metrics.py has the wrapper at all."""
    fn = getattr(metrics, name, None)
    if fn is None:
        return {"available": False, "why": f"metrics.{name} does not exist"}
    try:
        return await fn(*args) or {"available": False}
    except ValueError as exc:
        return {"available": False, "why": str(exc)}


def family_of(rows: list) -> dict | None:
    """One campaign's `family` from its campaign_vs_family rows (one per
    metric). The header is the CPA row's -- its sample is the conversion-gated
    one diagnose's floor is about -- else the first row's."""
    if not rows:
        return None
    head = next((r for r in rows if r.get("metric") == "cpa"), rows[0])
    return {"family": head.get("family"), "audience": head.get("audience"),
            "goal": head.get("optimization_goal"),
            "band_level": head.get("band_level"),
            "campaign_weeks": head.get("campaign_weeks"),
            "metrics": {r.get("metric"): {k: r.get(k)
                                          for k in FAMILY_BAND_FIELDS}
                        for r in rows if r.get("metric") in FAMILY_METRICS}}


async def _families(brand: str) -> dict:
    """{campaign_key: family} from metrics.campaign_vs_family, grouped by
    campaign_key. {} before 019: every campaign then carries family None."""
    res = await _call_019("campaign_vs_family", brand, FAMILY_PAIR)
    if not res.get("available"):
        return {}
    by_key: dict = {}
    for r in res.get("rows") or []:
        if r.get("campaign_key") is not None:
            by_key.setdefault(str(r.get("campaign_key")), []).append(r)
    return {k: family_of(v) for k, v in by_key.items()}


async def _family_bands(brand: str) -> list | None:
    """The account's typical ranges for the page: metrics.family_baseline's
    rows across every audience of a family (audience null). None before 019."""
    res = await _call_019("family_baseline", brand)
    if not res.get("available"):
        return None
    return [r for r in res.get("rows") or [] if r.get("audience") is None]


#: How `family` is sent: per metric [p25, p75, position, percentile]. p50 and
#: cur stay on the page (cur is the ladder's last_7 again); the prompt names
#: the columns in this order and tests/test_suggestions.py holds the two.
FAMILY_COLUMNS = ("p25", "p75", "position", "percentile")


def family_sent(family) -> dict | None:
    """`family` as the model reads it, or None when there is no band."""
    if not isinstance(family, dict) or family.get("band_level") is None:
        return None
    bands = {m: [b.get(k) for k in FAMILY_COLUMNS]
             for m, b in (family.get("metrics") or {}).items()
             if isinstance(b, dict) and b.get("p25") is not None}
    if not bands:
        return None
    return _dense({"family": family.get("family"),
                   "audience": family.get("audience"),
                   "goal": family.get("goal"),
                   "band_level": family.get("band_level"),
                   "campaign_weeks": family.get("campaign_weeks"),
                   "bands": bands})


async def _dimensions_by_goal(brand: str, windows: dict | None) -> dict | None:
    """{goal: {"hook": [rows], "offer": [...], "audience": [...]}} from
    ads.facet_by_goal over the last 28 settled days (the last_28 rung's
    dates), or None before 018 is applied or without 017's windows.

    Rows keep facet_by_goal's order, capped at DIMENSION_ROWS per goal and
    dimension. `relative` is within the goal only, which is the point.
    """
    w = (windows or {}).get(P28) or {}
    since, until = w.get("cur_since"), w.get("cur_until")
    if not (since and until):
        return None
    out: dict = {}
    for dim in GOAL_DIMENSIONS:
        try:
            res = await metrics.facet_by_goal(brand, since, until, dim)
        except ValueError:
            return None
        if not res.get("available"):
            return None
        for r in res.get("rows") or []:
            slot = out.setdefault(r.get("optimization_goal") or "(none)",
                                  {d: [] for d in GOAL_DIMENSIONS})[dim]
            if len(slot) < DIMENSION_ROWS:
                slot.append(r)
    return out


#: The facet_by_goal columns the model reads.
GOAL_DIMENSION_SENT = ("value", "spend", "conversions", "cpa", "relative",
                       "relative_pct", "evidence_level", "spend_share_pct")


def dimensions_by_goal_view(dbg: dict | None) -> dict | None:
    """dimensions_by_goal as the model is sent it: per row the value, spend,
    conversions, CPA (from `rates`), the relative label and pct, evidence and
    spend share. Selected, never computed."""
    if not dbg:
        return None
    out = {}
    for goal, dims in dbg.items():
        out[goal] = {}
        for dim, rows in (dims or {}).items():
            view = []
            for r in rows or []:
                v = {k: r.get(k) for k in GOAL_DIMENSION_SENT if k != "cpa"}
                v["cpa"] = (r.get("rates") or {}).get("cpa")
                view.append({k: v[k] for k in GOAL_DIMENSION_SENT
                             if v.get(k) is not None or k == "value"})
            if view:
                out[goal][dim] = view
    return out


def _fill_copy(item: dict, copy_by_key: dict) -> None:
    """Tags and, where the item was chosen to carry it, the copy."""
    key = item.pop("_key", None)
    with_copy = item.pop("_with_copy", False)
    row = copy_by_key.get(str(key)) or {}
    for k in ("hook", "offer", "format"):
        if k in item:
            item[k] = row.get(k)
    if with_copy:
        body = " ".join(str(row.get("first_body") or "").replace(
            "\\n", " ").split())
        cut = body[:CAMPAIGN_BODY_CHARS]
        item["headline"] = row.get("first_headline")
        item["opening"] = f"{cut}…" if len(body) > len(cut) else (body or None)


async def creative_pack(brand: str, days: int, until=None) -> dict:
    """What the account is currently saying, what it cost, and what changed.

    GROUPED BY optimization_goal, AND THAT IS THE WHOLE POINT. This account
    runs four goals; a single "best and worst ads" list would rank every
    OFFSITE_CONVERSIONS ad a failure and every LEAD_GENERATION ad a success,
    and a model asked about the COPY would then explain that difference in
    terms of the writing.

    `funnel_available` is False when migration 017 is not applied. The page
    still renders (it builds this pack live) and scripts/suggest.py refuses
    to publish: an analysis without the window ladder is the one the
    operator rejected.
    """
    # Defaulted HERE rather than by each caller, so the page and the publisher
    # cannot disagree about which window the evidence is about.
    if until is None:
        until = await context.latest_day(brand)
    d = await metrics.overview(brand, days, until, level="ad", limit=500)

    by_goal: dict = {}
    for row in d.get("rows") or []:
        by_goal.setdefault(row.get("optimization_goal") or "(none)", []).append(row)

    # The copy, for the ads that actually make the cut. Fetched after the
    # selection rather than before it, so a 500-ad account does not carry 500
    # bodies through a page render to throw 494 of them away.
    chosen: list = []
    goals: list = []
    for goal, rows in sorted(by_goal.items(),
                             key=lambda kv: len(kv[1]), reverse=True):
        ranked = [r for r in rows if _rate(r) is not None]
        ranked.sort(key=_rate)
        # `rankable` is false when NO ad under this goal converted -- THRUPLAY
        # is the live case, 16 ads and not one conversion. The ads are still
        # worth reading for their copy; the ranking is what has to be withheld.
        best = ranked[:PER_GOAL]
        # The dearest end starts after the cheapest one, so the two cannot
        # overlap: with five ranked ads a naive ranked[-3:] would hand the
        # model the same ad as both the cheapest and the dearest.
        worst = list(reversed(ranked[PER_GOAL:][-PER_GOAL:]))
        fallback = sorted(rows, key=lambda r: float(r.get("spend") or 0),
                          reverse=True)[:PER_GOAL] if not ranked else []
        picked = best + worst + fallback
        chosen.extend(picked)
        goals.append({
            "optimization_goal": goal,
            "ads_run": len(rows),
            "rankable_on_cost": bool(ranked),
            "ads_with_no_conversions": len([r for r in rows
                                            if _rate(r) is None]),
            "ranked_on": RANK_RATE if ranked else None,
            "cheapest": best,
            "dearest": worst,
            "by_spend_only": fallback,
        })

    # ---------------------------------------------------------- campaigns --
    # One round trip: every level, every pair. Built BEFORE the copy fetch so
    # the ads each campaign and the challenger pool name ride the same query.
    ladder = await metrics.funnel_ladder(brand)
    if ladder.get("available"):
        # Before the campaigns are built: diagnose and rating_rule read each
        # campaign's family band. {} (every family None) before 019.
        families = await _families(brand)
        campaigns, pool, windows = await _campaigns(ladder, d["brand_id"],
                                                    families)
        windows = {"settled_through": ladder.get("settled_through"),
                   **windows}
    else:
        campaigns, pool, windows = [], [], None
    # Impact and the per-goal dimensions need 018 (and 017's windows). Both
    # degrade to absent rather than failing the pack: no impact order, no
    # scorecard measurement, the old `dimensions` table.
    if ladder.get("available"):
        impact_ok, impact_why, impact_rows = await _impact(brand, campaigns)
        dims_by_goal = await _dimensions_by_goal(brand, windows)
        family_bands = await _family_bands(brand)
    else:
        impact_ok, impact_why, impact_rows = False, ladder.get("why"), []
        dims_by_goal = None
        family_bands = None
    for c in campaigns:
        for a in (c.get("where") or {}).get("ads") or []:
            chosen.append({"entity_key": a.get("_key")})
    for g in pool:
        for a in g["ads"]:
            chosen.append({"entity_key": a.get("_key")})

    keys = list({str(r["entity_key"]) for r in chosen if r.get("entity_key")})
    copy_by_key: dict = {}
    if keys:
        for row in await fetch_all(
            """
            select c.ad_key, c.first_headline, c.first_body,
                   c.headlines, c.bodies, d.cta, d.format,
                   -- THE BUTTON LIVES IN TWO PLACES, and for most of this
                   -- account it is not the obvious one. store.upsert_ad reads
                   -- creative.call_to_action_type into ads.ad.cta, which is
                   -- null on 588 of 713 ads here -- a DYNAMIC ad carries its
                   -- calls to action inside asset_feed_spec instead, and
                   -- parse.creative_texts already lifts those out as `cta`
                   -- rows. Reading only the column would have shown "--" for
                   -- every ad in the format that carries most of the spend.
                   (select t ->> 'text'
                      from jsonb_array_elements(c.texts) t
                     where t ->> 'field' = 'cta'
                     order by (t ->> 'ordinal')::int
                     limit 1) as cta_text,
                   -- The labels scripts/tag.py filed, for the campaign
                   -- entries and the challenger pool.
                   (select fe.hook from ads.facet_effective fe
                     where fe.ad_key = c.ad_key and fe.hook is not null
                     limit 1) as hook,
                   (select fe.offer from ads.facet_effective fe
                     where fe.ad_key = c.ad_key and fe.offer is not null
                     limit 1) as offer
              from ads.ad_copy c
              join ads.ad d on d.ad_key = c.ad_key
             where c.ad_key = any(%s::uuid[])
            """, (keys,)):
            copy_by_key[str(row["ad_key"])] = row

    def _shape(row: dict) -> dict:
        """One ad as the page shows it: what it says, and what that cost."""
        copy = copy_by_key.get(str(row.get("entity_key"))) or {}
        return {
            "ad": row.get("entity_name"),
            "format": row.get("format"),
            "status": row.get("effective_status"),
            "spend": row.get("spend"),
            "conversions": row.get("conversions"),
            RANK_RATE: _rate(row),
            "link_ctr": _rate(row, "link_ctr"),
            "headline": copy.get("first_headline"),
            "body": copy.get("first_body"),
            "cta_button": copy.get("cta_text") or copy.get("cta"),
            "wordings_on_this_ad": copy.get("headlines"),
        }

    for g in goals:
        for key in ("cheapest", "dearest", "by_spend_only"):
            g[key] = [_shape(r) for r in g[key]]

    for c in campaigns:
        for a in (c.get("where") or {}).get("ads") or []:
            _fill_copy(a, copy_by_key)
    for g in pool:
        for a in g["ads"]:
            _fill_copy(a, copy_by_key)

    # ads.facet_performance, not a group-by written here. It is the function
    # that already knows a dimension is not rankable when its rows ran under
    # more than one optimization goal, and `comparable_on_cost` is that answer.
    # Passing it through unchanged is how the model learns what it may not say.
    #
    # `angle` and `family` are still empty -- ads.angle has no rows -- so they
    # are left out rather than sent as a NULL bucket that costs prompt budget
    # to say nothing.
    dimensions = {}
    for dim in ("hook", "offer", "audience", "format"):
        rows = await fetch_all(
            """
            select value, ads_run, spend, conversions, rates,
                   optimization_goals, optimization_goal_count,
                   comparable_on_cost, spend_sufficient, rank_within_goal
              from ads.facet_performance(%s::uuid, %s, %s, %s)
             order by spend desc nulls last
            """, (d["brand_id"], d["since"], d["until"], dim))
        # Capped by SPEND, which the ORDER BY has already applied, and
        # TRIMMED PER ROW: a facet_performance row is ~500 characters, ~380 of
        # them eight rates where the model reads two plus the goal array where
        # the COUNT is what decides whether a comparison is allowed. `audience`
        # is free text with a long tail carrying almost no money.
        dimensions[dim] = [{
            "value": r["value"],
            "ads_run": r["ads_run"],
            "spend": r["spend"],
            "conversions": r["conversions"],
            "cpa": (r["rates"] or {}).get("cpa"),
            "link_ctr": (r["rates"] or {}).get("link_ctr"),
            "optimization_goal_count": r["optimization_goal_count"],
            "comparable_on_cost": r["comparable_on_cost"],
        } for r in rows[:DIMENSION_ROWS]]
        if rows[DIMENSION_ROWS:]:
            dimensions[dim].append({
                "value": f"... {len(rows[DIMENSION_ROWS:])} more {dim} "
                         f"value(s) with less spend, not shown"})
    formats = dimensions["format"]

    # TWO DIFFERENT COVERAGES, and conflating them cost a whole analysis.
    #
    # ads.untagged_spend counts an ad as tagged when facet_effective resolves
    # an ANGLE for it (013:575, `fe.angle_id is not null`). ads.angle is empty
    # on this database, so every facet scripts/tag.py filed carries a null
    # angle and the function correctly reports 100% untagged. Handed straight
    # to the model beside a `dimensions` block full of real hook and offer
    # numbers, it resolved the contradiction the wrong way and wrote "every ad
    # is untagged" -- suppressing the analysis the tagging pass existed to
    # enable. So both are reported, each saying which question it answers.
    angle_coverage = await fetch_one(
        """
        select total_spend, tagged_spend, untagged_ads, untagged_spend,
               stale_tag_ads, stale_tag_spend
          from ads.untagged_spend(%s::uuid, %s, %s)
        """, (d["brand_id"], d["since"], d["until"]))

    facet_coverage = await fetch_one(
        """
        select count(*)::int                                     as ads,
               count(*) filter (where fe.ad_key is not null)::int as with_a_facet,
               count(*) filter (where fe.hook is not null)::int   as with_a_hook,
               count(*) filter (where fe.offer is not null)::int  as with_an_offer
          from (select distinct f.ad_key
                  from ads.fact_ad_day f
                 where f.brand_id = %s::uuid
                   and f.day between %s and %s) spent
          left join ads.facet_effective fe on fe.ad_key = spent.ad_key
        """, (d["brand_id"], d["since"], d["until"]))

    untagged = {
        "angle_attribution": {
            **(angle_coverage or {}),
            "means": "spend whose ad resolves to an ANGLE. ads.angle is empty "
                     "on this brand, so this is 0 by construction and says "
                     "nothing about hook, offer or audience.",
        },
        "creative_labels": {
            **(facet_coverage or {}),
            "means": "ads that have spent and carry a hook/offer/audience "
                     "label. This is what the `dimensions` block is built "
                     "from.",
        },
    }

    # PAGE ONLY. The 28-day fatigue read is what the page's "wearing out"
    # panel shows. It no longer reaches the model or the rating: "1 of 5
    # symptoms over 28 days" turned ten campaigns red on its own. The triage
    # reads 017's per-window fatigue legs through intel/diagnose.py instead.
    fatigue = await metrics.fatigue(brand, days, until, 100.0, False)

    return {
        "verb": "creative_pack",
        "brand": d["brand"], "since": d["since"], "until": d["until"],
        "days": d["days"], "settled_through": d["settled_through"],
        "unsettled_days": d["unsettled_days"],
        "funnel_available": bool(ladder.get("available")),
        "funnel_why": ladder.get("why"),
        "campaigns": campaigns,
        "challenger_pool": pool,
        "windows": windows,
        "goals": goals,
        "formats": formats,
        "dimensions": dimensions,
        # Per optimization goal, from ads.facet_by_goal (018), over the last
        # 28 settled days. None before 018: the page and the model fall back
        # to `dimensions`.
        "dimensions_by_goal": dims_by_goal,
        # ads.goal_impact's rows for last 7 vs prior 7: goal rows and
        # single-goal campaign rows. Page and account_summary only.
        "impact_available": impact_ok,
        "impact_why": None if impact_ok else impact_why,
        "goal_impact": impact_rows,
        # ads.family_baseline's rows with audience null: the typical range
        # per campaign family and goal, for the page. None before 019.
        "family_bands": family_bands,
        "untagged": untagged,
        "tiring": [{"ad": r.get("entity_name"),
                    "optimization_goal": r.get("optimization_goal"),
                    "score": r.get("score"),
                    "confident": r.get("confident"),
                    "spend_recent": r.get("spend_recent")}
                   for r in (fatigue.get("rows") or [])[:6]],
    }


# ------------------------------------------------------------ prompt tiers --
# WHAT THE MODEL IS SENT is a compact VIEW of each pack campaign, and the
# pack itself is untouched: triage_from clamps against the full diagnosis and
# the page draws from the full entry. The view removes repetition, not facts:
#
#   - a ladder rung is [spend, conversions, cpa, cpm, link_ctr, cvr]
#     (LADDER_COLUMNS), because `windows` already gives every date once;
#   - the campaign's own 28-day spend, conversions and cpa are the last_28
#     rung, so they are not repeated beside it;
#   - a campaign-level finding drops its entity (the campaign), its legs (the
#     same numbers as pct_7 and pct_14) and its driver (driver_7);
#   - the control drops what restates itself: the incumbent when it is the
#     control, the reason when it is protected;
#   - nulls, empty lists, ACTIVE statuses and "none" fatigue patterns go.
#
# THE CAMPAIGN LIST IS NEVER CUT, so when the prompt is over budget the
# campaigns themselves get shorter. Three tiers, each a strict subset of the
# one before; `diagnosis` and `rating_rule` are in all three, because they are
# what the reply is clamped against.
#
#   A  the whole view
#   B  drops the `before` and `prior_14` rungs, and the copy on every ad but
#      the control
#   C  keeps last_7, prior_7 and last_28, pct_7 and pct_14, the control (with
#      its copy), each ad group's test floor, diagnosis, rating_rule, impact
#      and family

TIERS = ("A", "B", "C")
#: The columns of a ladder rung as the model is sent it. The prompt names
#: them in this order; tests/test_suggestions.py holds the two together.
LADDER_COLUMNS = ("spend", "conversions", "cpa", "cpm", "link_ctr", "cvr")
#: Never sent: the page draws them, and the model has the same figures as
#: `ladder`, `pct_7` and `cpa_shares_7`.
#: `movement` is pct_7/pct_14 again for the page's strip; the keys are for
#: the outcomes ledger.
NOT_SENT = ("funnel", "objective", "spend", "conversions", "cpa", "link_ctr",
            "movement", "campaign_key", "entity_keys")
#: What a short-form campaign drops on the way to the model.
SHORT_NOT_SENT = ("campaign_key", "entity_keys", "movement")
#: The impact fields the model reads: dollars and the share of the goal's
#: change. impact_score is the page's sort key, not a figure to quote.
IMPACT_SENT = ("goal", "excess_cost", "contribution_share_pct", "evidence")
TIER_B_DROPS = ("before", "prior_14")
TIER_C_RUNGS = ("last_7", "prior_7", "last_28")
#: Tier C keeps the 7-day driver and both evidence levels besides the plan's
#: list: ~100 characters, and without them question 3 (why) has nothing to
#: read but the diagnosis.
TIER_C_KEYS = ("campaign", "status", "running", "optimization_goals",
               "ladder", "pct_7", "pct_14", "cpa_shares_7", "driver_7",
               "evidence_7", "evidence_14", "ad_groups", "where", "diagnosis",
               "rating_rule", "impact", "family")
COPY_FIELDS = ("headline", "opening")
#: Fields of an ad the page shows and the model does without: its place by
#: spend is the list's order, its format is in `dimensions`, and its key is
#: for the outcomes ledger.
AD_NOT_SENT = ("spend_rank_in_parent", "format", "ad_key")


def _dense(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != []}


def diagnosis_view(diag: dict | None, many_groups: bool = True) -> dict | None:
    """The diagnosis as the model is sent it: every finding, the control,
    the concentration, max_priority and not_yet, without the repetition."""
    if not diag:
        return diag
    findings = []
    for f in diag.get("findings") or []:
        v = {k: f.get(k) for k in ("id", "level", "entity", "pattern",
                                   "held_in", "legs", "driver_7", "evidence",
                                   "confidence", "allowed", "test_variables")}
        if f.get("level") == "campaign":
            # A family finding's legs are which bands it sits outside, not
            # pct_7 again, so they stay.
            drop = (("entity", "driver_7")
                    if f.get("pattern") in diagnose.CONTEXTUAL
                    else ("entity", "legs", "driver_7"))
            for k in drop:
                v.pop(k)
        findings.append(_dense(v))
    out = {"findings": findings}
    control = diag.get("control")
    if control:
        cv = {"ad": control.get("ad"),
              "incumbent_deteriorated": control.get("incumbent_deteriorated")}
        if control.get("incumbent") != control.get("ad"):
            cv["incumbent"] = control.get("incumbent")
        if control.get("incumbent_deteriorated"):
            cv["why"] = control.get("why")
        if many_groups:
            cv["ad_group"] = control.get("ad_group")
        out["control"] = _dense(cv)
    # `unreadable_legs` stays on the stored diagnosis and never reaches the
    # model: it lists every pattern a missing figure stopped, per ad and per
    # window, and on 2026-10-01 it was ~72% of a 250k-char prompt while giving
    # the reader nothing to act on. A pattern that could not be read is simply
    # not among the findings.
    out.update(_dense({"concentration": diag.get("concentration"),
                       "max_priority": diag.get("max_priority"),
                       "not_yet": diag.get("not_yet")}))
    return out


def _ad_sent(a: dict, with_copy: bool) -> dict:
    v = {k: x for k, x in a.items() if k not in AD_NOT_SENT}
    if v.get("fatigue_pattern") in ("none", "insufficient"):
        v.pop("fatigue_pattern")
    if not with_copy:
        for k in COPY_FIELDS:
            v.pop(k, None)
    return _dense(v)


def _group_sent(g: dict) -> dict:
    v = dict(g)
    if v.get("status") == "ACTIVE":
        v.pop("status")
    if v.get("same_as_campaign"):
        # The campaign's only ad group: its name and goal are the campaign's
        # to all intents, and on this account the name is ~90 characters.
        v.pop("ad_group", None)
        v.pop("optimization_goal", None)
    if isinstance(v.get("test_floor"), dict):
        v["test_floor"] = _dense({k: x for k, x in v["test_floor"].items()
                                  if k != "baseline_window"})
    return _dense(v)


def _impact_sent(impact) -> dict | None:
    if not isinstance(impact, dict):
        return None
    return _dense({k: impact.get(k) for k in IMPACT_SENT}) or None


def campaign_tier(c: dict, tier: str = "A") -> dict:
    """One pack campaign as the model is sent it, at tier A, B or C.

    A short-form campaign (not running in the last 7 settled days) is
    already short and goes as it is.
    """
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIERS}")
    if c.get("running") is False:
        out = {k: v for k, v in c.items() if k not in SHORT_NOT_SENT}
        if "impact" in out:
            out["impact"] = _impact_sent(out["impact"])
            if out["impact"] is None:
                out.pop("impact")
        # Not running: no week to set against a band, and no issues to raise.
        out.pop("family", None)
        return out
    many = len(c.get("ad_groups") or []) > 1
    out = {k: v for k, v in c.items() if k not in NOT_SENT}
    out["impact"] = _impact_sent(c.get("impact"))
    out["family"] = family_sent(c.get("family"))
    if out.get("status") == "ACTIVE":
        out.pop("status")
    if not out.get("more_ad_groups"):
        out.pop("more_ad_groups", None)

    ladder = {}
    for r in c.get("ladder") or []:
        if tier != "A" and r.get("key") in TIER_B_DROPS:
            continue
        if tier == "C" and r.get("key") not in TIER_C_RUNGS:
            continue
        ladder[r.get("key")] = [r.get(k) for k in LADDER_COLUMNS]
    out["ladder"] = ladder

    ads = []
    for a in (c.get("where") or {}).get("ads") or []:
        control = "control" in (a.get("role") or "")
        if tier == "C" and not control:
            continue
        ads.append(_ad_sent(a, tier == "A" or control))
    out["where"] = {"ads": ads}

    groups = [_group_sent(g) for g in c.get("ad_groups") or []]
    if tier == "C":
        groups = [_dense({"ad_group": g.get("ad_group"),
                          "same_as_campaign": g.get("same_as_campaign"),
                          "test_floor": g.get("test_floor")}) for g in groups]
    out["ad_groups"] = groups
    out["diagnosis"] = diagnosis_view(c.get("diagnosis"), many)
    out = {k: v for k, v in out.items() if v is not None}
    if tier == "C":
        return {k: out[k] for k in TIER_C_KEYS if k in out}
    return out


#: The question reaches the session as ONE COMMAND-LINE ARGUMENT, and Windows
#: caps a command line at 32,767 characters. chat.SYSTEM and the tool lists
#: take a few thousand of those, so the question itself stops here. The brief's
#: pack is ~52,000 characters as produced, almost all of it two row lists, so
#: it cannot be sent whole and a spawn that fails on length would read as "the
#: session returned nothing".
PROMPT_BUDGET = 24000


def compact(node, keep: int = 8):
    """Shorten every list past `keep` rows, and say how many were dropped.

    Structure-agnostic on purpose: the brief's shape is intel/brief.py's to
    change, and a compaction that named sections would break the day a section
    was renamed. Rows are already ordered by whatever matters (spend, effect,
    score) so the head is the part worth reading; the note at the end tells the
    session the rest exists and which way to get it.
    """
    if isinstance(node, dict):
        return {k: compact(v, keep) for k, v in node.items()}
    if isinstance(node, list):
        if keep <= 0:
            return f"[{len(node)} rows omitted; run the verb for them]"
        head = [compact(x, keep) for x in node[:keep]]
        if node[keep:]:
            head.append(f"... {len(node[keep:])} more rows not shown; run the "
                        f"verb for all of them")
        return head
    return node


#: What a triage reply may say about a campaign. Closed, because the page
#: colours a card by it and an invented value would render as no colour at all.
RATINGS = diagnose.RATINGS

SUGGESTIONS_PROMPT = """\
You are a senior direct-response media buyer reviewing one advertiser's live
Meta campaigns. Your reader owns the business and writes the ads. They want to
know, campaign by campaign, what changed, where it changed, the most likely
reason, and what -- if anything -- the evidence justifies doing about it.

Brand: {brand}
Settled through {settled}; the {unsettled} day(s) after it can still move.
`windows` gives the exact dates of every comparison; every one ends at the
settled edge. `dimensions_by_goal` covers the last 28 settled days (the
current side of `windows.last_28_vs_prior_28`); `dimensions`, sent only when
`dimensions_by_goal` is not, covers {since} to {until} ({days} days).

THE ACCOUNT:

{facts}

`campaigns` is the unit you are rating. A running campaign carries a `ladder`
of windows, its 7- and 14-day changes, its ad groups, the ads that matter in
it (`where.ads`: the control, the incumbent and the top spender), how its
spend is spread (`spend_spread`), its `impact`, a `diagnosis` and a
`rating_rule`. `impact` (when present) is the last 7 settled days against the
7 before, within the campaign's own optimization goal: `excess_cost` is the
dollars spent beyond what this week's conversions cost at last week's rate
(negative is a saving) and `contribution_share_pct` is its share of its
goal's CPA change. Dollars may be compared across campaigns; a CPA may not.
A campaign whose ad groups span two goals has no `impact`.
`family` (when present) is CONTEXT: what is normal for this kind of campaign
in this account, over the last 26 weeks, under the same optimization goal.
`family.family` is the kind (franchise, m_and_a = "M&A / book roll", b_roll =
"B-roll", hiring, other; write the words, not the key), `audience` is
retargeting or prospecting when known, and `campaign_weeks` is how many
campaign-weeks stand behind the bands (`band_level` "family_audience" when
the band is that audience's, "family" when it is every audience's). Each of
`bands` is [p25, p75, position, percentile] for this week's figure: p25 to
p75 is the typical range, `position` says whether this week sits below,
within or above it, and `percentile` is the share of past campaign-weeks at
or under this week's figure. Judge the campaign against its OWN earlier
windows first; the typical range only says whether that result is usual for
its kind. Quote the range as given ("CPA $44.67 sits above the typical
$28.10 to $36.40 for M&A / book roll campaigns on this goal"), call it the
"typical range", never a "benchmark" or a target, and never set one goal's
range against another goal's campaign. Each `ladder` entry is [spend, conversions, cpa, cpm, link_ctr, cvr] for that
window; `windows` has its dates. A campaign with "running": false spent
nothing in the last 7 settled days and carries its 28-day figures: rate it,
raise no issues, and say it is not running.

ANSWER FOUR QUESTIONS, IN ORDER.

1. WHAT CHANGED. Read the `ladder` from the short end. `unsettled` is
   provisional -- Meta is still restating it -- and is never the basis for
   anything. `last_7` against `prior_7` is the change, and `pct_7` gives each
   move in percent; `last_14` against `prior_14`, with `pct_14`, says whether
   it persisted. `last_28` and `before` are the campaign's own baseline. A
   7-day move the 14-day comparison does not share is a wobble, not a trend.
   There are no target CPAs in this account: judge each campaign against its
   own earlier windows, never against a number of your own.

2. WHERE. Campaign, then ad group, then ad. `ad_groups` and `where.ads` give
   each one's share of its parent's spend (`share_pct`). An ad group with
   "same_as_campaign": true is the campaign's only one and its figures are
   the campaign's.
   Concentration is not a problem on its own: when
   `diagnosis.concentration.efficient` is true the spend sits where the
   results are, and that is the account working, not an issue.

3. WHY. `cpa_shares_7` splits the 7-day CPA change between auction_cost
   (CPM), click_through (link CTR) and post_click (conversion rate); they sum
   to 100 and `driver_7` names the largest. `diagnosis.findings` names the
   pattern each finding's figures fit, on which windows (`held_in`) and on
   what evidence; a finding with no `entity` is about the campaign itself:

     auction_cost            CPM up, link CTR and CVR steady. The auction got
                             dearer; do not propose replacing the creative.
     creative_engagement     link CTR down, CPM and CVR steady.
     post_click              link CTR steady, CVR down. The fall is after the
                             click; do not replace ads still earning clicks.
     possible_fatigue        link CTR down as daily frequency rises, on an ad.
     audience_saturation     CPM up as daily frequency rises.
     clickier_poorer_intent  link CTR up, CVR down: more clicks, worse ones.
     diminishing_returns     more spend, dearer results, no stage moving much.
     scaling_opportunity     CPA steady in both windows on a small share.
     no_conversions_spend    no conversions on several times its own CPA.
     outside_family_norm     this week's CPA above, or link CTR or CVR below,
                             the typical range for its kind (`legs` says
                             which). Context: monitor, or test the stage it
                             names; never act on it alone.
     steady                 nothing moved past the steady band.
     insufficient_evidence   too few conversions to read a move.

   Every pattern is a HYPOTHESIS. Your `diagnosis` names the pattern, what
   else could explain the same figures, and what would tell the two apart.
   Say "daily frequency", never "frequency": it is impressions per person per
   day, and cumulative frequency is not in these facts.

   CAUSAL WORDING. A metric never "caused" anything. Write "lower link CTR
   appears to be the largest contributor" and put the contribution evidence
   underneath ("click_through: 64% of the 7-day CPA change"). Never write
   "caused", "causes", "because of", "due to", "is driving" or "drove":
   those words are flagged on the card.

4. WHAT IS JUSTIFIED. Each finding's `allowed` lists the recommendation types
   its evidence supports, weakest first: monitor, test, act.
     act      the evidence supports a direct change; phrase it "the evidence
              supports ...".
     test     worth testing; always with a `test_plan`.
     monitor  worth watching; say what would change the recommendation and in
              which window.
   Never recommend more than `allowed`: a type above it is moved down and the
   card says so. The same holds for confidence (never above the finding's
   `confidence`) and priority (never above `diagnosis.max_priority`).

A TEST PLAN
  NAME BOTH SIDES BY WHAT IS TESTED, not just by an ad. The reader has to
  know, from the two lines alone, which version is which:
    form test      control "the current lead form (on "<the control ad>")",
                   challenger "the same form with one fewer question"
    offer test     control "the current offer (<the offer, in plain words>)
                   on "<the control ad>"", challenger "the same ad with
                   <the new offer>"
    creative test  control "the control ad "<the control ad>"", challenger
                   the challenger ad by name, or the new creative's brief
  control        `diagnosis.control.ad`, named as above; it keeps running
                 unchanged. When `incumbent_deteriorated` is true, say that
                 the ad with the most conversions is not the control, and why.
  variable       ONE of the finding's `test_variables`.
  challenger     ONE thing: an existing ad or wording from `challenger_pool`
                 under the SAME optimization goal, quoted; or a new opening
                 line on a hook from `dimensions_by_goal` that the account
                 funded and this campaign has not tried, written out in full.
  challenger_kind  for a creative test, say which of the two it is:
                 "existing_asset"  an ad already in the account. Set
                                   `challenger_ad` to its name EXACTLY as
                                   `challenger_pool` (same optimization goal)
                                   or this campaign's `where.ads` gives it.
                                   Use it when the pool has a same-goal ad
                                   that fits the variable. A name that is not
                                   there is relabelled new_creative.
                 "new_creative"    nothing in the account fits, so it has to
                                   be made. `challenger_ad` is null and
                                   `brief` is {{format, angle_or_hook,
                                   opening_line, built_from}}: `built_from`
                                   names the ad, hook or angle it extends and
                                   the evidence for it, read from
                                   `dimensions_by_goal` ("the problem hook is
                                   better than this goal's CPA").
                 For a test of anything but the creative, challenger_kind,
                 challenger_ad and brief are null.
  hold_constant  audience, offer, landing page and optimization event --
                 whichever is not the variable.
  evaluate_on    cost per lead, link CTR and conversion rate. Qualified cost
                 per lead is not tracked in this account; say so.
  setup          REQUIRED. One plain line on how to set it up in Ads Manager:
                 "Duplicate the ad in the same ad set, attach the shorter form
                 to the copy, and add the tag below to its URL parameters." or
                 "Use Meta's A/B Test tool on this ad set, with the new form
                 as version B, and add the tag below to the new ad's URL
                 parameters." A test with no setup is marked incomplete. The
                 tag itself is added for you; do not write one.
  decide_when    the ad group's `test_floor.decision_spend_per_arm` and
                 `test_floor.min_conversions_per_arm`, written for a person:
                 "each version has spent $276.57 and has 10 leads".
                 Say "each version", quote the figures, do not compute new
                 ones. Never on early link CTR alone.

NEVER WRITE A FIELD NAME. Nothing on the card may say decision_spend_per_arm,
min_conversions_per_arm, test_floor, cpa_rank_in_goal, impact_score or any
other key from these facts: say what it means ("the spend each version needs
before you decide", "third-cheapest of five ad groups on this goal"). A field
name on a card is flagged.

WHAT YOU MAY AND MAY NOT COMPARE

Ads and ad groups inside ONE optimization goal are comparable on cost per
result. Across different goals they are not, and saying "this beats that"
across two goals is the single worst mistake available here --
LEAD_GENERATION and OFFSITE_CONVERSIONS are not measuring the same event. A
campaign's `cpa` (and its ladder's) is null when its ad groups span two goals;
that is deliberate, do not reconstruct it.

`cpa_rank_in_goal` is where an ad group sits among `ad_groups_ranked_in_goal`
ad groups sharing its goal over the last 28 settled days, 1 the cheapest. It
is context only: the verdict is the campaign against its own earlier windows.

`challenger_pool` is grouped by optimization goal. Where a goal has
`rankable_on_cost: false`, nothing under it converted and it has nothing to
lend. An engagement or video-view campaign is often built not to convert: say
that cost could not be judged.

`dimensions_by_goal` gives, per optimization goal, each hook, offer and
audience label's spend, conversions and CPA against that goal's own CPA:
`relative` is "baseline" (the goal's largest-spend label), "better",
"similar" (within 10%) or "worse", with `relative_pct`, and `evidence_level`
says how many conversions stand behind it ("directional" is few). RELATIVE
IS WITHIN ITS GOAL ONLY: a "better" hook under LEAD_GENERATION says nothing
about OFFSITE_CONVERSIONS. A null `value` is the untagged ads. Read a
challenger's hook inside the challenger's own goal.

When only `dimensions` is sent, every row carries `comparable_on_cost`. Where
it is false, those rows ran under two or more optimization goals and you may
NOT say one hook, offer, audience or format beats another on cost.

Only the CURRENT wording of each ad is stored. An ad edited since it ran
carries today's words against older spend, so do not claim a specific line
CAUSED a result.

`untagged` reports TWO coverages and they are not the same question.
`angle_attribution` is about angles, which this brand has none of, so it reads
0 and always will -- it is NOT evidence that the hook, offer and audience
labels are missing. `creative_labels` is the one that says whether the
`dimensions` block has anything behind it.

THE RATING. One per campaign, every campaign in `campaigns`, exactly once.

  red     Critical: look at this first. CPA rose materially against the
          campaign's own earlier windows, on enough volume, and the rise
          persists in the 14-day comparison; or it spent several times its own
          baseline CPA in the last 7 settled days with no conversions.
  yellow  Worth a look. A move that has not persisted, evidence that is only
          directional, too few conversions to read, or cost that cannot be
          judged.
  green   No change suggested. Steady or improving on enough conversions.

A single fatigue symptom on one ad is not a red. Neither is concentration,
nor a week outside the typical range for its kind -- though a CPA above that
range keeps a campaign from green.
`rating_rule.allowed` lists the ratings the campaign's figures permit and
`reasons` says why. Choose within `allowed`; a rating outside it is changed to
the nearest allowed one and the card says so.

ISSUES. At most one primary (only when `diagnosis.max_priority` is
"primary"), two secondary and two monitor; a green usually has none. Each
issue names the finding it rests on in `basis` (its `id`, e.g. "d1"). An issue
with no finding behind it can only be a monitor at low confidence.
Every issue carries its own recommendation -- an observation with no
recommendation is a complaint, and a recommendation with no observation is a
guess. SIX FIELDS ARE MANDATORY on every issue: `observation`, `evidence`,
`diagnosis`, `recommendation` (its `action` AND its `expected_outcome`),
`confidence` (its `level` AND its `reason`), and a `test_plan` (with its
`setup`) whenever the type is "test". An issue missing any of them is marked
incomplete and shown
as a monitor, whatever it asked for.

`not_yet`: what NOT to do yet. `diagnosis.not_yet` is already on the card;
add only what it does not say, one line each, and do not restate its lines.

WHAT WAS RECOMMENDED BEFORE, AND WHAT BECAME OF IT. `followups` maps a
campaign to the act and test recommendations earlier reviews made that are
still in play: each with its `status` (open: not seen in the account yet;
implemented / measuring: the account shows it was done, on `implemented_on`,
matched `match` -- strong, likely, or set by a person), and when measured its
`result` and `settled_days`. A result is a reading of the figures, not a
verdict. Do not propose a second test in an ad group whose test is
implemented or measuring: report that test's status instead ("the challenger
launched on 2026-10-03 is measuring, 4 settled days; nothing new to stack on
it"). An open one you would still recommend is raised again with its id in
`continues`.

THE SUMMARY. Each campaign's `summary` is ONE line of at most 25 words, the
collapsed card's whole text, in this form: "CPA +22%. Lower CTR appears to be
the largest contributor. Test new creative. Medium-high confidence." -- the
move, where the change came from, what is justified, and how sure. Only
figures from the facts. `account_note` is ONE sentence for the whole account:
where the reader should start today.

REPLY WITH JSON AND NOTHING ELSE. The first character is {{ and the last is }}.
No markdown fence, no preamble, no commentary. `campaigns` holds one object
per campaign, in the order `campaigns` lists them:

{{"account_note": "<one sentence: where to start today>",
 "campaigns": [
 {{"campaign": "<the campaign name exactly as given>",
  "rating": "red" | "yellow" | "green",
  "summary": "<one line, at most 25 words, as above>",
  "issues": [
   {{"priority": "primary" | "secondary" | "monitor",
    "basis": "<the id of the diagnosis finding this rests on>",
    "observation": "<what changed, where, in plain words>",
    "evidence": "<the figures that show it, from the facts>",
    "diagnosis": "<the likely pattern, what else could explain it, what would tell them apart>",
    "recommendation": {{"type": "act" | "test" | "monitor", "action": "<...>",
                       "expected_outcome": "<what should move, and where to see it>"}},
    "test_plan": {{"variable": "<one of the finding's test_variables>",
                  "control": "<what is tested, as it runs now, and on which ad>",
                  "challenger": "<the one thing that differs, quoted or written in full>",
                  "setup": "<one plain line: how to set it up in Ads Manager>",
                  "challenger_kind": "existing_asset" | "new_creative" | null,
                  "challenger_ad": "<exact ad name, for existing_asset>" | null,
                  "brief": {{"format": "...", "angle_or_hook": "...",
                            "opening_line": "<written in full>",
                            "built_from": "<the ad, hook or angle it extends, and the evidence>"}} | null,
                  "hold_constant": ["..."], "evaluate_on": ["..."],
                  "decide_when": "<each version has spent $X and has N leads>"}} | null,
    "confidence": {{"level": "low" | "medium" | "high", "reason": "<why>"}},
    "continues": "<the id of the previous issue this is, or null>"}}
  ],
  "not_yet": ["<one line each>"],
  "resolved": [
   {{"id": "<the id of a previous issue for THIS campaign>",
    "change": "<the id of the entry in changes_since_last_review for THIS campaign that shows it gone>",
    "evidence": "<in plain words, how that change settles it>"}}
  ]}}
 ]}}

`test_plan` is null unless the recommendation type is "test".

WHAT THE LAST REVIEW SAID. {previous_note}

`previous_issues` maps each campaign to the issues that review raised, each
with an `id` and, where it had one, the `entity` and `pattern` it was about.
Account for every one of them, for its own campaign:

  - Still there? Raise it again in `issues` and put its id in `continues`.
    Rewrite it against today's figures; keep the id. Same underlying issue
    means same id, even if you would now word it differently.
  - Gone? List its id under `resolved`, citing in `change` the entry in
    `changes_since_last_review` that shows it -- and only for its own
    campaign. That list was computed by comparing the account then and now,
    and it is the ONLY evidence a resolution may rest on.
    No evidence, no resolution. A pattern is resolved by an entry saying it
    no longer holds on that entity, or by a change made to that entity. A
    wording issue is resolved only by a "wording changed" entry for that ad;
    a spend leader changing does not change what an ad says. A campaign with
    no entries resolves nothing.
  - An issue you would not raise today but cannot show is gone: leave it out
    of both. The page shows it as no longer mentioned, not as fixed.

An issue not in `previous_issues` gets "continues": null. `resolved` is []
when nothing was resolved, and always [] when there was no previous review.

HOW TO WRITE IT

One or two sentences per field. Write for somebody reading a card, not a
spreadsheet: money to the cent as it would be printed ("$97.08", not
"97.0816"), rates to two decimals ("1.04% link CTR"), changes as the pct
fields give them ("link CTR down 22.41% on the prior 7 days"), ranks as "11th
of 11". No line breaks and no "\\n" inside a string. Quote the actual copy
you are reacting to in quotation marks, and name ads as they are named in the
account.

Every figure you write must appear in the facts above, rounded only for
printing. Do not add, subtract, divide or average anything. A campaign's
`spend_spread` already holds its top ads' combined spend and share and what
went to ads that converted nothing -- use those. When a total or share you
want is not in the facts, say it in words ("almost all of its spend", "most
of the money") instead of working it out: a figure on a card that is not in
the facts is flagged on the card as unverified.

Suggest, never instruct. "The evidence supports", "worth testing" and "worth
watching", never "pause this", "kill that", "this is your winner" or "this ad
underperforms". The rating says where to look, not what to decide; a person
chooses what to change.

If the figures do not support a rating either way, give yellow and say why.
"""


def latest_suggestion(slug: str) -> dict | None:
    """The most recently published suggestion for this brand, or None.

    Filename order is date order -- `YYYY-MM-DD-<brand>.json` -- so the last
    name is the newest without reading any of them. A directory that does not
    exist yet is a brand nothing has published for, which is a normal answer on
    a fresh install and not a failure.
    """
    try:
        files = sorted(SUGGESTION_DIR.glob(f"*-{slug}.json"))
    except OSError:
        return None
    if not files:
        return None
    try:
        doc = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    doc["file"] = files[-1].name
    return doc


def publish_path(slug: str, on: date) -> Path:
    return SUGGESTION_DIR / f"{on.isoformat()}-{slug}.json"


def age_hours(doc: dict | None) -> float | None:
    """How old a published suggestion is, in hours, or None.

    The page shows this because the prose is dated and the evidence under it is
    live. When an import has landed since the suggestion was written, the two
    disagree -- and a reader has to be told which half moved.
    """
    if not doc or not doc.get("published_at"):
        return None
    try:
        written = datetime.fromisoformat(str(doc["published_at"]))
    except ValueError:
        return None
    if written.tzinfo is None:
        written = written.replace(tzinfo=timezone.utc)
    return round((datetime.now(timezone.utc) - written).total_seconds() / 3600, 1)


#: What the triage session is told on top of the question. It returns JSON for
#: a page to colour, so chat.SYSTEM -- plain sentences for a browser -- is the
#: wrong instruction, and chat.CLASSIFY_SYSTEM is about labelling copy.
TRIAGE_SYSTEM = """\
You are reviewing advertising campaigns for the person who runs them. You
return JSON and nothing else: one object, the first character of your reply
is { and the last is }. No preamble, no markdown fence, no trailing
commentary. A reply that does not parse is discarded and the work is wasted.

Every figure you write must appear in the facts you were given. Do not add,
subtract, divide or average anything. Never compare cost per result across
two optimization goals. Never choose a recommendation type, a test variable
or a confidence above what the diagnosis you were given allows. A metric
never "caused" anything: say it "appears to be the largest contributor".
"""


def _clean(text) -> str:
    """One line of card text. The model quotes ad bodies, and a body with a
    paragraph break arrives as a literal backslash-n more often than not."""
    if not text:
        return ""
    s = str(text).replace("\\n", " ").replace("\n", " ")
    return " ".join(s.split())


def _problems(r: dict) -> list:
    """[{problem, fix}], from an OLD-shape reply's `problems`.

    Falls back to `changes`, the flat list the first version of the prompt
    asked for, so a file published before this change still renders -- as
    fixes with no stated problem, which is what it was.
    """
    out = []
    if isinstance(r.get("problems"), list):
        for p in r["problems"]:
            if isinstance(p, dict):
                problem, fix = _clean(p.get("problem")), _clean(p.get("fix"))
                if problem or fix:
                    out.append({"problem": problem or None, "fix": fix or None})
    elif isinstance(r.get("changes"), list):
        out = [{"problem": None, "fix": _clean(x)}
               for x in r["changes"] if _clean(x)]
    return out


# ------------------------------------------------------------ the last run --
# WHAT HAPPENED TO YESTERDAY'S ISSUES.
#
# Every run is written from scratch, so without this a fixed issue simply
# stops being mentioned -- and so does one the model merely decided not to
# raise this time, which on the same data happens. The reader cannot tell
# those apart, and "it disappeared" is not "it was fixed".
#
# So each issue carries an id and the date it was first raised, the next run
# is handed the last run's issues, and every one of them ends up in exactly
# one of three places: still open (same id, same first-raised date), resolved
# (with the change the model cites), or no longer mentioned (neither -- shown
# as such, never as fixed). Files from before the analyst report carried
# `problems` (or `changes`), and are read the same way.

def previous_suggestion(slug: str, before: date) -> dict | None:
    """The newest published suggestion dated BEFORE `before`, or None.

    Before, not on: re-running today's publish must compare against
    yesterday's, not against the file it is about to overwrite.
    """
    try:
        files = sorted(f for f in SUGGESTION_DIR.glob(f"*-{slug}.json")
                       if f.name[:10] < before.isoformat())
    except OSError:
        return None
    for f in reversed(files):
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if doc.get("triage"):
            doc["file"] = f.name
            return doc
    return None


def previous_problems(doc: dict | None) -> dict:
    """campaign name -> [{id, problem, fix, open_since, ...}] from a file.

    A shape-2 entry's issues read as problem = observation and fix =
    recommendation.action, plus the entity and pattern of the finding it
    rested on, so the next run can match a pattern that stopped holding.

    A file written before problems had ids gets them assigned here, from its
    own date and position, so the chain can start from it. Its `open_since`
    is its publish date: the earliest the problem is known to have existed.
    """
    if not doc:
        return {}
    day = str(doc.get("published_at") or doc.get("file") or "")[:10]
    tag = day[5:7] + day[8:10]
    out: dict = {}
    for ci, t in enumerate(doc.get("triage") or []):
        rows = []
        if isinstance(t.get("issues"), list):
            findings = {f.get("id"): f for f in
                        ((t.get("diagnosis") or {}).get("findings") or [])
                        if isinstance(f, dict)}
            for pi, p in enumerate(t["issues"]):
                if not isinstance(p, dict):
                    continue
                rec = p.get("recommendation") if isinstance(
                    p.get("recommendation"), dict) else {}
                if not (p.get("observation") or rec.get("action")):
                    continue
                f = findings.get(p.get("basis")) or {}
                rows.append({
                    "id": p.get("id") or f"i{tag}-{ci}-{pi}",
                    "problem": p.get("observation") or rec.get("action"),
                    "fix": rec.get("action"),
                    "open_since": p.get("open_since") or day,
                    "entity": p.get("entity") or f.get("entity"),
                    "pattern": p.get("pattern") or f.get("pattern"),
                })
        else:
            items = t.get("problems")
            if items is None:
                items = [{"problem": None, "fix": x}
                         for x in t.get("changes") or []]
            for pi, p in enumerate(items):
                if not isinstance(p, dict) or not (p.get("problem")
                                                   or p.get("fix")):
                    continue
                rows.append({
                    "id": p.get("id") or f"p{tag}-{ci}-{pi}",
                    # An old file stored fixes only; the fix is then the
                    # nearest thing to a statement of the problem there is.
                    "problem": p.get("problem") or p.get("fix"),
                    "fix": p.get("fix"),
                    "open_since": p.get("open_since") or day,
                })
        if rows:
            out[t.get("campaign")] = rows
    return out


def previous_note(prev: dict | None, data_changed: bool) -> str:
    """The sentence the prompt carries about the last review."""
    if not prev:
        return ("There is no previous review, so `previous_issues` is empty: "
                "every issue is new and `resolved` is always [].")
    note = (f"The last review was published {str(prev.get('published_at'))[:10]}"
            f" against the window ending {prev.get('until')}.")
    if not data_changed:
        note += (" The facts have NOT changed since then, so nothing can have "
                 "been resolved: carry forward every previous issue that "
                 "still applies, and leave `resolved` empty.")
    return note


# ------------------------------------------------------- what changed since --
# A RESOLUTION HAS TO POINT AT SOMETHING THAT ACTUALLY CHANGED.
#
# Left to itself the model resolved three problems on the first run of the
# carry-forward, and two were wrong. So the changes are computed here, by
# comparing the last review's stored facts with today's, and a resolution is
# only accepted if it cites one of them for its own campaign. Each change says
# who made it:
#
#   you      the account was changed -- wording, an ad paused or switched on,
#            an ad added or gone, the campaign's status
#   numbers  nothing was touched; the figures moved -- a diagnosis finding
#            that appeared or stopped holding, an ad group's rank in its goal,
#            and (old files) a tiring ad or a new spend leader

#: Cap per campaign. A campaign with forty changed ads gets the first twelve;
#: the model needs enough to match against, not the full audit.
CHANGES_PER_CAMPAIGN = 12

#: Fallbacks, not findings: neither appearing nor going away is news.
_NOT_A_FINDING = ("steady", "insufficient_evidence")


def _text(value):
    """A snapshot value as the published file will hold it. suggest.py writes
    with default=str, so a budget read back from the last file is a string;
    storing it as one here keeps today's side comparable with it."""
    return None if value is None else str(value)


async def snapshot(brand: str, campaign_names: list) -> dict:
    """The account's structure now, per campaign name (snapshot v2):

        {"_campaign":  {"status", "daily_budget"},
         "_ad_groups": {ad_group_key: {"name", "daily_budget",
                                       "optimization_goal"}},
         ad_key:       {"ad", "status", "copy_hash", "ad_group",
                        "ad_group_key", "first_spend_day", "utm_content"}}

    Stored in the published file so the NEXT run can see which ads' wording or
    status moved, which ads are new and when they first spent, and which
    budgets changed (changes_since). copy_hash is ads.ad_copy's own md5 over
    every text field, so a changed headline, body or button changes it; a
    hash of None is an ad the import has never read copy for.
    `first_spend_day` is the earliest day ads.fact_ad_day has spend for the
    ad -- min(day), selected, not computed -- and None for an ad that has
    not spent. `utm_content` is ads.ad.utm_content, read off the ad's URL by
    meta_ads/parse.py -- the tag a test plan asks for ("EXP-<issue id>"), so
    the ledger can find the new ad by it. Keys starting "_" are never ad keys (an ad key is a uuid).
    Snapshots written before v2 hold only {ad_key: {ad, status, copy_hash}}.
    """
    if not campaign_names:
        return {}
    b = await context.brand(brand)
    names = list(campaign_names)
    groups = await fetch_all(
        """
        select c.name as campaign, c.effective_status as campaign_status,
               c.daily_budget as campaign_budget,
               g.ad_group_key, g.name as ad_group, g.daily_budget,
               g.optimization_goal
          from ads.campaign c
          left join ads.ad_group g on g.campaign_key = c.campaign_key
         where c.brand_id = %s and c.name = any(%s::text[])
        """, (b["id"], names))
    rows = await fetch_all(
        """
        with camp as (
            select campaign_key, name
              from ads.campaign
             where brand_id = %s and name = any(%s::text[])
        ), spent as (
            select f.ad_key, min(f.day) as first_spend_day
              from ads.fact_ad_day f
              join ads.ad a  on a.ad_key = f.ad_key
              join camp c    on c.campaign_key = a.campaign_key
             where f.brand_id = %s and f.spend > 0
             group by f.ad_key
        )
        select c.name as campaign, a.ad_key, a.name as ad,
               a.effective_status as status, cp.copy_hash,
               a.ad_group_key, g.name as ad_group, s.first_spend_day,
               a.utm_content
          from ads.ad a
          join camp c              on c.campaign_key = a.campaign_key
          left join ads.ad_group g on g.ad_group_key = a.ad_group_key
          left join ads.ad_copy cp on cp.ad_key = a.ad_key
          left join spent s        on s.ad_key = a.ad_key
        """, (b["id"], names, b["id"]))
    out: dict = {}
    for r in groups:
        camp = out.setdefault(r["campaign"], {})
        camp.setdefault("_campaign", {
            "status": r["campaign_status"],
            "daily_budget": _text(r["campaign_budget"])})
        slot = camp.setdefault("_ad_groups", {})
        if r["ad_group_key"] is not None:
            slot[str(r["ad_group_key"])] = {
                "name": r["ad_group"],
                "daily_budget": _text(r["daily_budget"]),
                "optimization_goal": r["optimization_goal"]}
    for r in rows:
        out.setdefault(r["campaign"], {})[str(r["ad_key"])] = {
            "ad": r["ad"], "status": r["status"], "copy_hash": r["copy_hash"],
            "ad_group": r["ad_group"],
            "ad_group_key": _text(r["ad_group_key"]),
            "first_spend_day": _text(r["first_spend_day"]),
            "utm_content": r.get("utm_content")}
    return out


def _ads_of(snap_campaign: dict) -> dict:
    """The ad entries of one campaign's snapshot, without the "_" keys."""
    return {k: v for k, v in (snap_campaign or {}).items()
            if not str(k).startswith("_") and isinstance(v, dict)}


def _rank(g: dict) -> str | None:
    r, n = g.get("cpa_rank_in_goal"), g.get("ad_groups_ranked_in_goal")
    return f"{r} of {n}" if r and n else None


def _found(c: dict) -> set:
    # A campaign-level finding is sent without its entity (diagnosis_view),
    # so the stored pack and today's full one are matched on the campaign.
    return {(f.get("entity") or c.get("campaign"), f.get("pattern"))
            for f in (c.get("diagnosis") or {}).get("findings") or []
            if isinstance(f, dict) and f.get("pattern") not in _NOT_A_FINDING}


#: The structured kinds a "you" change carries (contracts D2). intel/outcomes.py
#: matches recommendations against these, never against the `what` text.
YOU_KINDS = ("ad_added", "wording_changed", "status_changed", "ad_gone",
             "budget_changed", "campaign_status")


def _ad_fields(key, a: dict) -> dict:
    """The ad-level fields of a structured "you" change. An old snapshot
    without ad_group / first_spend_day / utm_content gives None for them."""
    return {"ad": a.get("ad"), "ad_key": key,
            "ad_group": a.get("ad_group"),
            "ad_group_key": a.get("ad_group_key"),
            "first_spend_day": a.get("first_spend_day"),
            "utm_content": a.get("utm_content")}


def changes_since(prev: dict | None, campaigns: list, snap: dict) -> dict:
    """campaign name -> [{id, by, what, ...}]: what moved since the last review.

    `prev` is the last published file: its `pack.campaigns` (the facts it was
    written from) and, when it has one, its `snapshot`. A file from before
    snapshots existed is compared on its leading ads' wording only, and the
    change says so. A field only one side carries (an old file's tiring ads, a
    new one's diagnosis) is not compared: its absence is a change of shape,
    not of the account.

    Every "you" item also carries `kind` (YOU_KINDS) and, where they apply,
    "ad", "ad_key", "ad_group", "ad_group_key", "status_from", "status_to",
    "first_spend_day", "utm_content" -- and for a budget, "level"
    (campaign | ad_group), "budget_from", "budget_to". Budgets are compared only when both
    snapshots are v2 (they carry "_campaign" / "_ad_groups"); a v1 snapshot
    on either side is read for ads only.
    """
    if not prev:
        return {}
    before = {c.get("campaign"): c
              for c in (prev.get("pack") or {}).get("campaigns") or []}
    prev_snap = prev.get("snapshot")
    out: dict = {}
    for c in campaigns:
        name = c.get("campaign")
        was = before.get(name)
        items: list = []

        def add(by: str, what: str, **fields) -> None:
            items.append({"id": f"c{len(items) + 1}", "by": by, "what": what,
                          **fields})

        if was is None:
            out[name] = [{"id": "c1", "by": "numbers",
                          "what": "not in the last review's window"}]
            continue

        if (was.get("status") or None) != (c.get("status") or None):
            add("you", f"campaign status {was.get('status')} -> {c.get('status')}",
                kind="campaign_status", status_from=was.get("status"),
                status_to=c.get("status"))

        if prev_snap is not None:
            s_old, s_new = prev_snap.get(name) or {}, snap.get(name) or {}
            old, new = _ads_of(s_old), _ads_of(s_new)
            for k, a in new.items():
                o = old.get(k)
                if o is None:
                    add("you", f'ad added: "{a["ad"]}"', kind="ad_added",
                        **_ad_fields(k, a), status_from=None,
                        status_to=a.get("status"))
                    continue
                if o.get("copy_hash") and a.get("copy_hash") \
                        and o["copy_hash"] != a["copy_hash"]:
                    add("you", f'wording changed on "{a["ad"]}"',
                        kind="wording_changed", **_ad_fields(k, a))
                if (o.get("status") == "ACTIVE") != (a.get("status") == "ACTIVE"):
                    add("you", f'"{a["ad"]}" {o.get("status")} -> {a.get("status")}',
                        kind="status_changed", **_ad_fields(k, a),
                        status_from=o.get("status"), status_to=a.get("status"))
            for k, o in old.items():
                if k not in new:
                    add("you", f'ad gone from the campaign: "{o["ad"]}"',
                        kind="ad_gone", **_ad_fields(k, o),
                        status_from=o.get("status"), status_to=None)
            # Budgets: only when both sides are v2 snapshots.
            if "_campaign" in s_old and "_campaign" in s_new:
                b_old = (s_old.get("_campaign") or {}).get("daily_budget")
                b_new = (s_new.get("_campaign") or {}).get("daily_budget")
                if b_old != b_new:
                    add("you", f"daily budget {b_old} -> {b_new} on the campaign",
                        kind="budget_changed", level="campaign", ad=None,
                        ad_key=None, ad_group=None, ad_group_key=None,
                        budget_from=b_old, budget_to=b_new)
            if "_ad_groups" in s_old and "_ad_groups" in s_new:
                g_was = s_old.get("_ad_groups") or {}
                for gk, g in (s_new.get("_ad_groups") or {}).items():
                    o = g_was.get(gk)
                    if o is None or o.get("daily_budget") == g.get("daily_budget"):
                        continue
                    add("you", f'daily budget {o.get("daily_budget")} -> '
                               f'{g.get("daily_budget")} on ad group "{g.get("name")}"',
                        kind="budget_changed", level="ad_group", ad=None,
                        ad_key=None, ad_group=g.get("name"), ad_group_key=gk,
                        budget_from=o.get("daily_budget"),
                        budget_to=g.get("daily_budget"))
        elif "leading_ads" in was and "leading_ads" in c:
            # No snapshot in the last file: the leading ads' copy is all there
            # is to compare, and only for ads that lead in both.
            old = {a.get("ad"): a for a in was.get("leading_ads") or []}
            for a in c.get("leading_ads") or []:
                o = old.get(a.get("ad"))
                if not o:
                    continue
                for f in ("headline", "body", "cta_button"):
                    if (o.get(f) or "") != (a.get(f) or ""):
                        add("you", f'{f} changed on "{a.get("ad")}" (compared '
                                   f'on the leading ads only)',
                            kind="wording_changed", ad=a.get("ad"),
                            ad_key=None, ad_group=None, ad_group_key=None,
                            first_spend_day=None, utm_content=None)

        if "diagnosis" in was and "diagnosis" in c:
            f_old, f_new = _found(was), _found(c)

            def on(entity):
                return ("the campaign" if entity == name
                        else f'"{entity}"')

            for entity, pattern in sorted(f_old.difference(f_new), key=str):
                add("numbers", f"{pattern} no longer found on {on(entity)}")
            for entity, pattern in sorted(f_new.difference(f_old), key=str):
                add("numbers", f"{pattern} now found on {on(entity)}")

        if "tiring_ads" in was and "tiring_ads" in c:
            t_old = {t.get("ad") for t in was.get("tiring_ads") or []}
            t_new = {t.get("ad") for t in c.get("tiring_ads") or []}
            for ad in sorted(t_old.difference(t_new)):
                add("numbers", f'"{ad}" no longer tiring')
            for ad in sorted(t_new.difference(t_old)):
                add("numbers", f'"{ad}" now tiring')

        g_old = {g.get("ad_group"): g for g in was.get("ad_groups") or []}
        one = (len(was.get("ad_groups") or []) == 1
               and len(c.get("ad_groups") or []) == 1)
        for g in c.get("ad_groups") or []:
            # A campaign's only ad group is sent without its name
            # (campaign_tier), so one-against-one is matched by position.
            o = (was["ad_groups"][0] if one
                 else g_old.get(g.get("ad_group")))
            if o and _rank(o) and _rank(g) and _rank(o) != _rank(g):
                add("numbers", f'ad group "{g.get("ad_group")}" rank in '
                               f'{g.get("optimization_goal")}: {_rank(o)} -> {_rank(g)}')

        if "leading_ads" in was and "leading_ads" in c:
            lead_old = [a.get("ad") for a in was.get("leading_ads") or []]
            lead_new = [a.get("ad") for a in c.get("leading_ads") or []]
            if set(lead_old) != set(lead_new):
                add("numbers", f"spend leaders {lead_old} -> {lead_new}")

        if items:
            # The account's own changes first, so the cap never cuts one of
            # them for a rank that moved.
            items.sort(key=lambda x: x["by"] != "you")
            for i, x in enumerate(items[:CHANGES_PER_CAMPAIGN], 1):
                x["id"] = f"c{i}"
            out[name] = items[:CHANGES_PER_CAMPAIGN]
    return out


#: How much of a previous issue's observation the prompt carries. Enough to
#: match it, not enough to read it as a fact about today.
PREVIOUS_CHARS = 140


def for_the_prompt(previous: dict, names=None) -> dict:
    """previous_problems trimmed to what the model needs to match against,
    and only for the campaigns in this run: an issue for a campaign the model
    is not rating is one it cannot account for."""
    out = {}
    for name, rows in previous.items():
        if not rows or (names is not None and name not in names):
            continue
        # The entity only when it is not the campaign itself, which the key
        # already names.
        out[name] = [_dense({"id": p["id"],
                             "entity": p.get("entity")
                             if p.get("entity") != name else None,
                             "pattern": p.get("pattern"),
                             "observation": (p["problem"] or "")[:PREVIOUS_CHARS]})
                     for p in rows]
    return out


# ----------------------------------------------------------------- clamps --
# THE REPLY CHOOSES INSIDE THE DIAGNOSIS, AND THIS IS WHERE THAT IS HELD.
#
# Each issue names the finding it rests on (`basis`). Its recommendation type,
# its confidence and its priority are moved down to what that finding allows,
# and each move is recorded so the card can say whose call it was. An issue
# naming no real finding is a monitor at low confidence: an observation with
# no figures behind it is not grounds for a test.

#: How many issues of each priority a campaign may carry.
ISSUE_CAPS = {"primary": 1, "secondary": 2, "monitor": 2}

#: What a test holds constant unless it says otherwise -- minus the variable.
HOLD_CONSTANT = ("audience", "offer", "landing page", "optimization event")
EVALUATE_ON = ("cost per lead", "link CTR", "conversion rate")


def _norm(s) -> str:
    return "_".join(str(s or "").lower().replace("-", " ").split())


def _variable_ok(variable, test_variables) -> bool:
    v = _norm(variable)
    return bool(v) and any(tv in v for tv in test_variables or [])


#: The two kinds of creative challenger (plan section 5).
CHALLENGER_KINDS = ("existing_asset", "new_creative")
BRIEF_FIELDS = ("format", "angle_or_hook", "opening_line", "built_from")


def _brief(value) -> dict | None:
    if not isinstance(value, dict):
        return None
    out = {k: _clean(value.get(k)) or None for k in BRIEF_FIELDS}
    return out if any(out.values()) else None


def _challenger(tp: dict, variable: str, assets) -> dict:
    """challenger_kind, challenger_ad and brief for a test plan, checked.

    Only a creative test has a challenger kind. "existing_asset" stands only
    when `challenger_ad` is exactly the name of an ad the model was shown as
    available (the same-goal challenger_pool, or this campaign's where.ads);
    otherwise the plan is relabelled "new_creative" and says it was. A plan
    that names no kind gets one from the same test.
    """
    if "creative" not in _norm(variable):
        return {"challenger_kind": None, "challenger_ad": None, "brief": None}
    asked = _clean(tp.get("challenger_kind")).lower() or None
    ad = _clean(tp.get("challenger_ad")) or None
    brief = _brief(tp.get("brief"))
    # The control is never its own challenger: a test of an ad against
    # itself measures nothing.
    found = bool(ad) and ad in assets and ad != _clean(tp.get("control"))
    if asked == "new_creative":
        out = {"challenger_kind": "new_creative", "challenger_ad": None,
               "brief": brief}
    elif found:
        out = {"challenger_kind": "existing_asset", "challenger_ad": ad,
               "brief": None}
    else:
        out = {"challenger_kind": "new_creative", "challenger_ad": None,
               "brief": brief}
    if asked and asked != out["challenger_kind"]:
        out["challenger_kind_adjusted_from"] = asked
    return out


def _missing(x: dict, rec: dict, conf: dict, needs_plan: bool) -> list:
    """The mandatory fields an issue left empty (plan section 4)."""
    missing = [k for k in ("observation", "evidence", "diagnosis")
               if not _clean(x.get(k))]
    if not _clean(rec.get("action")):
        missing.append("action")
    if not _clean(rec.get("expected_outcome")):
        missing.append("expected_outcome")
    if not _clean(conf.get("level")):
        missing.append("confidence_level")
    if not _clean(conf.get("reason")):
        missing.append("confidence_reason")
    if needs_plan:
        tp = x.get("test_plan")
        if not isinstance(tp, dict):
            missing.append("test_plan")
        elif not _clean(tp.get("setup")):
            # How to set it up in Ads Manager: a test the reader cannot
            # build is a test nobody runs.
            missing.append("setup")
    return missing


def _issue_from(x: dict, findings: dict, max_priority: str,
                assets=frozenset()) -> dict | None:
    """One reply issue, clamped against the finding its `basis` names.

    `assets`: the ad names a creative test may call an existing asset."""
    rec = x.get("recommendation") if isinstance(x.get("recommendation"),
                                                dict) else {}
    observation, action = _clean(x.get("observation")), _clean(rec.get("action"))
    if not (observation or action):
        return None
    basis = str(x.get("basis") or "").strip()
    f = findings.get(basis)
    allowed = (f or {}).get("allowed") or ["monitor"]
    cap_conf = (f or {}).get("confidence") or "low"
    cap_prio = max_priority if f else "monitor"
    tvs = (f or {}).get("test_variables") or []

    out: dict = {"priority": None, "basis": basis or None,
                 "entity": (f or {}).get("entity"),
                 "pattern": (f or {}).get("pattern"),
                 "observation": observation or None,
                 "evidence": _clean(x.get("evidence")) or None,
                 "diagnosis": _clean(x.get("diagnosis")) or None}
    if f is None:
        out["basis_unknown"] = True

    asked = _clean(rec.get("type")).lower() or None
    kind = diagnose.clamp_type(asked, allowed)
    if asked and asked != kind:
        out["type_adjusted_from"] = asked

    tp = x.get("test_plan") if isinstance(x.get("test_plan"), dict) else None
    conf = x.get("confidence") if isinstance(x.get("confidence"), dict) else {}
    missing = _missing(x, rec, conf, kind == "test")
    plan = None
    if kind == "test":
        variable = _clean((tp or {}).get("variable"))
        control = _clean((tp or {}).get("control"))
        challenger = _clean((tp or {}).get("challenger"))
        if tp is None:
            why = "no test plan came with it"
        elif not _variable_ok(variable, tvs):
            why = (f'"{variable or "no variable"}" is not one of the '
                   f"variables this finding allows testing "
                   f'({", ".join(tvs) or "none"})')
        elif not (control and challenger):
            why = "the plan did not name both a control and a challenger"
        else:
            why = None
            hold = [_clean(h) for h in tp.get("hold_constant") or []
                    if _clean(h)] if isinstance(tp.get("hold_constant"),
                                                list) else []
            ev = [_clean(e) for e in tp.get("evaluate_on") or []
                  if _clean(e)] if isinstance(tp.get("evaluate_on"),
                                              list) else []
            plan = {"variable": variable, "control": control,
                    "challenger": challenger,
                    "setup": _clean(tp.get("setup")) or None,
                    "hold_constant": hold or [
                        h for h in HOLD_CONSTANT
                        if _norm(h) not in _norm(variable)],
                    "evaluate_on": ev or list(EVALUATE_ON),
                    "decide_when": _clean(tp.get("decide_when")) or None}
            plan.update(_challenger(tp, variable, assets))
        if why:
            out["test_plan_dropped"] = why
            out.setdefault("type_adjusted_from", asked or "test")
            kind = "monitor"
    elif tp is not None and asked == "test":
        out["test_plan_dropped"] = (f"the evidence supports {kind}, "
                                    f"not a test")

    if missing:
        # Incomplete: shown as a monitor, whatever it asked for, and the
        # card names what was missing.
        out["incomplete"] = missing
        if kind != "monitor":
            out.setdefault("type_adjusted_from", asked or kind)
            if plan is not None:
                out["test_plan_dropped"] = ("the issue is incomplete ("
                                            + ", ".join(missing) + ")")
            plan = None
            kind = "monitor"

    out["recommendation"] = {"type": kind, "action": action or None,
                             "expected_outcome":
                                 _clean(rec.get("expected_outcome")) or None}
    out["test_plan"] = plan

    asked_c = _clean(conf.get("level")).lower() or None
    level = diagnose.clamp_confidence(asked_c, cap_conf)
    if asked_c and asked_c != level:
        out["confidence_adjusted_from"] = asked_c
    out["confidence"] = {"level": level,
                         "reason": _clean(conf.get("reason")) or None}

    asked_p = _clean(x.get("priority")).lower() or None
    prio = diagnose.clamp_priority(asked_p, cap_prio)
    if asked_p and asked_p != prio:
        out["priority_adjusted_from"] = asked_p
    out["priority"] = prio
    out["_continues"] = str(x.get("continues") or "").strip()
    return out


def _capped(issues: list) -> tuple:
    """(issues within ISSUE_CAPS, how many were trimmed). One primary: a
    second is demoted to secondary, and anything past a cap is dropped."""
    seen_primary = False
    for i in issues:
        if i["priority"] == "primary":
            if seen_primary:
                i.setdefault("priority_adjusted_from", "primary")
                i["priority"] = "secondary"
            seen_primary = True
    kept = []
    for i in issues:
        same = [k for k in kept if k["priority"] == i["priority"]]
        if len(same) < ISSUE_CAPS.get(i["priority"], 0):
            kept.append(i)
    # Primary first; Python's sort keeps the reply's order within a priority.
    kept.sort(key=lambda i: diagnose.PRIORITY.index(i["priority"]),
              reverse=True)
    return kept, len(issues[len(kept):])


def _assets(c: dict, pool: list | None) -> frozenset:
    """The ad names a creative test may call an existing asset: the
    challenger pool under this campaign's own goal(s), and the ads its card
    shows. Names cleaned the way a reply's are, and nothing else."""
    goals = set(c.get("optimization_goals") or [])
    names = {_clean(a.get("name") or a.get("ad"))
             for a in (c.get("where") or {}).get("ads") or []
             if isinstance(a, dict)}
    for g in pool or []:
        if not isinstance(g, dict):
            continue
        if goals and g.get("optimization_goal") not in goals:
            continue
        names |= {_clean(a.get("ad")) for a in g.get("ads") or []
                  if isinstance(a, dict)}
    return frozenset(n for n in names if n)


def _attach_keys(i: dict, f: dict | None, c: dict) -> None:
    """The entity key, level and ad group an issue is about, and for a test
    its control's key and decision spend -- from the pack, so the outcomes
    ledger can register and measure it by key. Lookups by name; nothing
    computed. A name the pack does not know leaves None (outcomes then looks
    the key up by name in SQL)."""
    keys = c.get("entity_keys") or {}
    ads, groups = keys.get("ads") or {}, keys.get("ad_groups") or {}
    level = (f or {}).get("level")
    entity = i.get("entity")
    control = (c.get("diagnosis") or {}).get("control") or {}
    only = c.get("ad_groups") or []
    single = (only[0].get("ad_group") if len(only) == 1
              and isinstance(only[0], dict) else None)
    if level == "campaign":
        key, group = c.get("campaign_key"), control.get("ad_group") or single
    elif level == "ad_group":
        key, group = groups.get(entity), entity
    elif level == "ad":
        a = ads.get(entity) or {}
        key, group = a.get("key"), a.get("ad_group")
    else:
        key, group = None, None
    i["entity_level"] = level
    i["entity_key"] = str(key) if key else None
    plan = i.get("test_plan")
    if isinstance(plan, dict):
        ctl = ads.get(plan.get("control")) or {}
        plan["control_ad_key"] = str(ctl["key"]) if ctl.get("key") else None
        group = ctl.get("ad_group") or group
        floor = {}
        for g in only:
            if isinstance(g, dict) and (g.get("ad_group") == group
                                        or len(only) == 1):
                floor = g.get("test_floor") or {}
                break
        plan["decision_spend_per_arm"] = floor.get("decision_spend_per_arm")
    i["ad_group"] = group


def experiment_tag(issue_id: str) -> str:
    """The utm_content tag for a test issue: "EXP-" + its id."""
    return f"EXP-{issue_id}"


def _report(r: dict, c: dict, before: dict, used: set, tag: str, ci: int,
            today: date, pool: list | None = None) -> dict:
    """The shape-2 body of one campaign's entry: issues and not_yet."""
    diag = c.get("diagnosis") or {}
    findings = {f.get("id"): f for f in diag.get("findings") or []
                if isinstance(f, dict)}
    raw = r.get("issues") if isinstance(r.get("issues"), list) else []
    assets = _assets(c, pool)
    issues = [i for i in (_issue_from(x, findings,
                                      diag.get("max_priority") or "monitor",
                                      assets)
                          for x in raw if isinstance(x, dict)) if i]
    for i in issues:
        _attach_keys(i, findings.get(i.get("basis")), c)
    issues, trimmed = _capped(issues)
    for pi, i in enumerate(issues):
        prior = before.get(i.pop("_continues"))
        if prior and prior["id"] not in used:
            used.add(prior["id"])
            i.update({"id": prior["id"], "status": "still_open",
                      "open_since": prior["open_since"]})
        else:
            i.update({"id": f"i{tag}-{ci}-{pi}", "status": "new",
                      "open_since": today.isoformat()})
        if isinstance(i.get("test_plan"), dict):
            # Derived from the issue's FINAL id, never written by the model:
            # a still-open issue keeps its id, so its tag is the same run to
            # run. The person adds utm_content=<tag> to the new ad's URL
            # parameters; meta_ads/parse.py reads it back into
            # ads.ad.utm_content, which ads.experiment_result matches arms on.
            i["test_plan"]["tag"] = experiment_tag(i["id"])

    # One line per thing not to do. Two lines saying the same thing differ in
    # their reason, not their subject, so they are matched on the part before
    # the colon ("Acting on the 7-day move in this campaign"); the diagnosis's
    # line comes first and wins.
    not_yet: list = []
    seen: set = set()
    mine = r.get("not_yet") if isinstance(r.get("not_yet"), list) else []
    for s in [*(diag.get("not_yet") or []), *mine]:
        s = _clean(s)
        key = s.split(":", 1)[0].strip().lower() if s else ""
        if s and key not in seen:
            seen.add(key)
            not_yet.append(s)
    summary = _clean(r.get("summary"))
    headline = _clean(r.get("headline")) or _clean(r.get("why")) or summary
    headline = headline or "The reply did not rate this campaign."
    return {"headline": headline,
            # The collapsed card's one line. `headline` / `why` stay as its
            # aliases, so an older template still has a sentence to show.
            "summary": summary or headline,
            "issues": issues, "issues_trimmed": trimmed, "not_yet": not_yet}


def _legacy(r: dict, before: dict, used: set, tag: str, ci: int,
            today: date) -> dict:
    """The old-shape body: problem + fix pairs, as files before shape 2."""
    problems = []
    raw = r.get("problems") if isinstance(r.get("problems"), list) else []
    for pi, (p, clean) in enumerate(zip(
            [x for x in raw if isinstance(x, dict)
             and (_clean(x.get("problem")) or _clean(x.get("fix")))],
            _problems(r))):
        prior = before.get(str(p.get("continues") or "").strip())
        if prior and prior["id"] not in used:
            used.add(prior["id"])
            problems.append({**clean, "id": prior["id"],
                             "status": "still_open",
                             "open_since": prior["open_since"]})
        else:
            problems.append({**clean, "id": f"p{tag}-{ci}-{pi}",
                             "status": "new",
                             "open_since": today.isoformat()})
    if not raw:
        # An old-shape reply: fixes only, nothing to chain.
        problems = [{**p, "id": f"p{tag}-{ci}-{pi}", "status": "new",
                     "open_since": today.isoformat()}
                    for pi, p in enumerate(_problems(r))]
    return {"why": (_clean(r.get("why")) or
                    "The reply did not rate this campaign."),
            "problems": problems}


def triage_from(rows: list, campaigns: list, previous: dict | None = None,
                data_changed: bool = True, today: date | None = None,
                changes: dict | None = None,
                pool: list | None = None) -> list:
    """The model's reply, checked against the campaigns it was asked about.

    Returns one entry per campaign in `campaigns`, in that order. A campaign
    the reply named wrongly, rated outside RATINGS, or left out comes back
    with rating None and says so, rather than being dropped: a card that
    silently vanished reads as a campaign nobody ran.

    TWO REPLY SHAPES. A reply carrying `issues` -- or any campaign the pack
    diagnosed, when the reply says nothing about it -- becomes a shape-2
    entry (contracts C5): each issue clamped against the finding its `basis`
    names (_issue_from), capped at one primary, two secondary and two
    monitor, with `not_yet` merged onto the diagnosis's own. A reply carrying
    `problems` or `changes` is the old shape and is read as before.

    The campaign's own figures are attached from the PACK, never from the
    reply, so the numbers on a card are the function's and not the model's
    retelling of them.

    THE LAST RUN'S ISSUES are accounted for here, not trusted to the reply:

      - `continues` is honoured only for an id the SAME campaign had. Anything
        else is a new issue, whatever the reply claimed.
      - `resolved` is honoured only for such an id, only with evidence, and
        never when `data_changed` is false -- the same facts cannot show an
        issue gone. A refused resolution falls to "no longer mentioned".
      - With `changes` (changes_since), a resolution must also cite the id
        of a change listed for ITS campaign. It is then labelled by who made
        that change, and a campaign with no changes resolves nothing.
      - A previous issue neither continued nor resolved is kept as
        `dropped`, so the page can say it was not confirmed fixed.

    ROUND 2 (contracts2 D4) on every shape-2 entry: `bucket` (bucket_of the
    clamped issues), `priority_rank`, the pack's `impact` and `movement`,
    `summary`, empty `causal_flags` and `jargon_flags` for causal_language
    and jargon_language to fill, and on each issue the six-field check
    (`incomplete`, clamped to monitor; a test plan with no `setup` counts as
    missing it), the creative challenger's kind checked against `pool` (the
    pack's challenger_pool) and the campaign's ads, the keys the outcomes
    ledger registers it by, and on a test plan its `tag` ("EXP-" + the
    issue's final id, after carry-forward). The order is still the pack's;
    sort_triage is the published order.
    """
    previous = previous or {}
    today = today or date.today()
    tag = f"{today:%m%d}"
    by_name: dict = {}
    for r in rows:
        if isinstance(r, dict) and r.get("campaign"):
            by_name.setdefault(str(r["campaign"]).strip(), r)

    out = []
    for ci, c in enumerate(campaigns):
        name = c.get("campaign")
        r = by_name.get(str(name or "").strip()) or {}
        rating = str(r.get("rating") or "").strip().lower()
        before = {p["id"]: p for p in previous.get(name) or []}
        used: set = set()

        shape2 = isinstance(r.get("issues"), list) or (
            "problems" not in r and "changes" not in r and "diagnosis" in c)
        body = (_report(r, c, before, used, tag, ci, today, pool) if shape2
                else _legacy(r, before, used, tag, ci, today))

        resolved = []
        if data_changed:
            for x in r.get("resolved") if isinstance(r.get("resolved"), list) else []:
                if not isinstance(x, dict):
                    continue
                prior = before.get(str(x.get("id") or "").strip())
                evidence = _clean(x.get("evidence"))
                if not (prior and evidence and prior["id"] not in used):
                    continue
                change = None
                if changes is not None:
                    listed = {ch["id"]: ch for ch in changes.get(name) or []}
                    change = listed.get(str(x.get("change") or "").strip())
                    if change is None:
                        continue      # no real change behind it: dropped
                used.add(prior["id"])
                resolved.append({"id": prior["id"],
                                 "problem": prior["problem"],
                                 "open_since": prior["open_since"],
                                 "evidence": evidence,
                                 "by": change["by"] if change else None,
                                 "change": change["what"] if change else None})

        dropped = [{"id": p["id"], "problem": p["problem"],
                    "open_since": p["open_since"]}
                   for pid, p in before.items() if pid not in used]

        asked = rating if rating in RATINGS else None
        final = clamp_rating(asked, c.get("rating_rule"))
        entry = {
            "campaign": name,
            "rating": final,
            # Kept when the rule moved it, so the card can say whose rating
            # this is.
            "rating_adjusted_from": asked if final != asked else None,
            "rating_reasons": (c.get("rating_rule") or {}).get("reasons") or [],
            **body,
            "resolved": resolved,
            "dropped": dropped,
            "status": c.get("status"),
            "optimization_goals": c.get("optimization_goals"),
            "spend": c.get("spend"),
            "conversions": c.get("conversions"),
            "cpa": c.get("cpa"),
        }
        if shape2:
            bucket = bucket_of(entry["issues"])
            impact = c.get("impact") if isinstance(c.get("impact"),
                                                   dict) else None
            entry.update({
                "shape": 2,
                "why": entry["headline"],
                # Derived from the clamped issues; a `bucket` in the reply
                # is never read.
                "bucket": bucket,
                "priority_rank": priority_rank(bucket, impact),
                "impact": impact,
                "movement": c.get("movement") or {},
                "causal_flags": [],
                "jargon_flags": [],
                "campaign_key": c.get("campaign_key"),
                "running": c.get("running"),
                "ladder": c.get("ladder"),
                "funnel": c.get("funnel"),
                "where": c.get("where"),
                "spend_spread": c.get("spend_spread"),
                "diagnosis": c.get("diagnosis"),
            })
        out.append(entry)
    return out


# ---------------------------------------------------------------- buckets --
# ACT / TEST / MONITOR / NO CHANGE, decided after the clamps and never by the
# model: a campaign's bucket is the strongest recommendation type among its
# issues. Priority is the bucket plus 018's impact_score, by comparison only.

BUCKETS = ("act", "test", "monitor", "no_change")

#: impact_score is excess dollars x an evidence weight (018), so these are
#: dollars of evidence-weighted excess spend over the last 7 settled days.
#: Priority 1: anything the evidence supports acting on, and a test whose
#: campaign is costing at least PRIORITY_1_IMPACT. Priority 2: every other
#: test (a test is something to do, even without 018's figure), and a monitor
#: costing at least PRIORITY_2_IMPACT. Everything else is 3. Set against the
#: live account on 2026-10-01, where a week's excess on one campaign runs from
#: a few dollars to several hundred; revisit once 018 has a few weeks of rows.
PRIORITY_1_IMPACT = 250
PRIORITY_2_IMPACT = 100


def bucket_of(issues: list) -> str:
    types = {(i.get("recommendation") or {}).get("type")
             for i in issues or [] if isinstance(i, dict)}
    for b in ("act", "test", "monitor"):
        if b in types:
            return b
    return "no_change"


def _score(impact) -> float | None:
    v = (impact or {}).get("impact_score") if isinstance(impact, dict) else None
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def priority_rank(bucket: str, impact: dict | None) -> int:
    score = _score(impact)
    if bucket == "act" or (bucket == "test" and score is not None
                           and score >= PRIORITY_1_IMPACT):
        return 1
    if bucket == "test" or (bucket == "monitor" and score is not None
                            and score >= PRIORITY_2_IMPACT):
        return 2
    return 3


def sort_triage(triage: list) -> list:
    """The published order: act, test, monitor, no change; within a bucket
    by impact_score, highest first, a campaign with none last. Stable, so
    equal scores keep the pack's spend order. Entries without a bucket (old
    shapes) go last in their own order."""
    def key(t):
        b = t.get("bucket")
        score = _score(t.get("impact"))
        return (BUCKETS.index(b) if b in BUCKETS else len(BUCKETS),
                score is None, -score if score is not None else 0)
    return sorted(triage, key=key)


# ---------------------------------------------------------- figure check --
# EVERY FIGURE ON A CARD MUST BE ONE THE MODEL WAS GIVEN.
#
# The prompt says so and the model mostly obeys, but "mostly" put
# "$2,395.75 of the campaign's spend is behind one piece of writing" on a
# card: two ads' spend, added up by the model, a number no function returned.
# A figure that appears nowhere in the facts, even allowing for rounding to
# print, is flagged on the card rather than removed -- the sentence around it
# may still be right, and hiding the check would make the card look more
# certain than it is.

import re as _re

#: $1,234.56 / 1,234 / 97.08 / 14.99% / 3rd -- the forms a card prints.
_FIGURE = _re.compile(r"(?<![\w.])\$?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(%|st|nd|rd|th)?(?![\w])")

#: Straight or curly double quotes, and curly single quotes. Straight single
#: quotes are left alone: they are apostrophes far more often than quotes.
_QUOTED = _re.compile(r'"[^"]*"|“[^”]*”|‘[^’]*’')

_BOUND = _re.compile(r"\b(?:under|over|below|above|at least|at most|"
                     r"less than|more than|fewer than|up to)\s+\$?[\d,]+(?:\.\d+)?%?",
                     _re.I)

#: Small whole numbers are counts and ranks that appear everywhere; checking
#: them proves nothing and flags nothing useful.
_FIGURE_MIN_CHECKED = 10

#: Fact sections the MODEL wrote, last time. A figure the last review stated
#: is not a fact because the last review stated it -- that would let one run's
#: invented number vouch for the next run's repetition of it.
MODEL_WRITTEN = ("previous_issues", "previous_problems")

#: Keys whose string values are names of things in the account. Names are
#: full of dates, state codes and "20 Years", none of them claims.
_NAME_KEYS = ("campaign", "from_campaign", "ad", "ad_group", "name", "entity",
              "incumbent")


def _fact_numbers(node, out: set | None = None) -> set:
    """Every number in the facts: numeric values, numeric strings, and the
    figures quoted inside text (ad copy says "$38M" and "31 years")."""
    out = set() if out is None else out
    if isinstance(node, dict):
        for v in node.values():
            _fact_numbers(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _fact_numbers(v, out)
    elif isinstance(node, bool) or node is None:
        pass
    elif isinstance(node, (int, float)):
        out.add(float(node))
    else:
        text = str(node)
        try:
            out.add(float(text))
        except ValueError:
            for m in _FIGURE.finditer(text):
                out.add(float(m.group(1).replace(",", "") + (m.group(2) or "")))
    return out


def _names(node, out: set | None = None) -> set:
    out = set() if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if k in _NAME_KEYS and isinstance(v, str) and len(v.strip()) > 2:
                out.add(v)
            else:
                _names(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _names(v, out)
    return out


def _matches(x: float, decimals: int, known: set) -> bool:
    """Is `x`, printed to `decimals` places, some known figure rounded?"""
    tol = 0.5 * 10 ** -decimals + 1e-9
    return any(abs(v - x) <= tol for v in known)


def unverified_figures(text: str, known: set) -> list[str]:
    """The figures in `text` that no fact accounts for, as printed."""
    bad = []
    for m in _FIGURE.finditer(text or ""):
        whole, frac = m.group(1).replace(",", ""), m.group(2) or ""
        x = float(whole + frac)
        if not frac and x < _FIGURE_MIN_CHECKED:
            continue
        if 1900 <= x <= 2100 and not frac:
            continue                    # a year in a date or a campaign name
        decimals = len(frac[1:])
        if not _matches(x, decimals, known):
            bad.append(m.group(0))
    return bad


def _card_texts(t: dict) -> tuple:
    """(texts checked with threshold phrases exempt, texts checked strictly).

    test_plan.control and .challenger are not checked at all: they are the
    ad's own copy or proposed copy. decide_when is checked strictly -- a test
    floor is a figure the facts carry, so "at least $500" there is a claim,
    not a threshold the sentence compares against.
    """
    loose = [t.get("headline") or t.get("why") or ""]
    if t.get("summary") and t.get("summary") != loose[0]:
        loose.append(t["summary"])
    strict = []
    for p in t.get("problems") or []:
        loose += [p.get("problem") or "", p.get("fix") or ""]
    for p in t.get("issues") or []:
        rec = p.get("recommendation") or {}
        loose += [p.get("observation") or "", p.get("evidence") or "",
                  p.get("diagnosis") or "", rec.get("action") or "",
                  rec.get("expected_outcome") or "",
                  (p.get("confidence") or {}).get("reason") or ""]
        plan = p.get("test_plan") or {}
        loose.append(plan.get("setup") or "")
        strict.append(plan.get("decide_when") or "")
        # A brief's opening line is proposed copy, like the challenger; its
        # `built_from` is the evidence for it and is checked.
        loose.append((plan.get("brief") or {}).get("built_from") or "")
    loose += [s for s in t.get("not_yet") or [] if isinstance(s, str)]
    for x in t.get("resolved") or []:
        loose.append(x.get("evidence") or "")
    return loose, strict


def check_figures(triage: list, facts: dict) -> int:
    """Flag, on each card, figures its text states that the facts do not
    contain. Returns how many were flagged.

    Known figures are the facts the model was sent, minus what a model wrote
    (MODEL_WRITTEN), plus the size of every negative one: "down 22.41%" is
    the -22.41 the facts hold. Every name in the account -- campaigns, ad
    groups, ads -- is cut out of the text first, longest first.
    """
    known, names = _known(facts)
    flagged: list = []
    for t in triage:
        mine = sorted(names | _names({"campaign": t.get("campaign"),
                                      "where": t.get("where"),
                                      "diagnosis": t.get("diagnosis")}),
                      key=len, reverse=True)
        loose, strict = _card_texts(t)
        bad: list = []
        for s, exempt in [*[(x, True) for x in loose],
                          *[(x, False) for x in strict]]:
            for f in _unverified_in(s, exempt, mine, known):
                if f not in bad:
                    bad.append(f)
        t["unverified_figures"] = bad
        flagged.extend(bad)
    return len(flagged)


def _known(facts: dict) -> tuple:
    """(known figures, account names) of the facts as sent, minus what a
    model wrote. A negative figure's size counts: "down 22.41%"."""
    given = {k: v for k, v in (facts or {}).items() if k not in MODEL_WRITTEN}
    known = _fact_numbers(given)
    known = known | {abs(v) for v in known if v < 0}
    return known, _names(given)


def _unverified_in(s: str, exempt: bool, names: list, known: set) -> list:
    for n in names:
        s = s.replace(n, " ")
    # Quoted text is ad copy: either the account's own, which is in the facts
    # already, or wording the review PROPOSES, which is a suggestion and not
    # a claim about the data.
    s = _QUOTED.sub(" ", s)
    if exempt:
        # "under $300", "more than 20": a threshold the sentence compares
        # against, not a figure it claims the data holds.
        s = _BOUND.sub(" ", s)
    return unverified_figures(s, known)


def check_note(note: str | None, facts: dict) -> list:
    """The figures the reply's account_note states that the facts do not
    contain -- check_figures' rule, for the one account-level sentence."""
    if not note:
        return []
    known, names = _known(facts)
    out: list = []
    for f in _unverified_in(note, True, sorted(names, key=len, reverse=True),
                            known):
        if f not in out:
            out.append(f)
    return out


# ------------------------------------------------------- causal wording --
# A METRIC NEVER CAUSED ANYTHING. The prompt says "appears to be the largest
# contributor"; this flags, on the card, the sentences that said otherwise.
# Flagged, not rewritten: the sentence may still be right, and the flag says
# it claimed more than a decomposition can show.

CAUSAL = _re.compile(r"\b(caused|causes|because of|due to|is driving|drove)\b",
                     _re.I)


def causal_phrases(text) -> list:
    """The causal phrases in `text`, lower-cased, each once, in order."""
    out: list = []
    for m in CAUSAL.finditer(str(text or "")):
        p = m.group(1).lower()
        if p not in out:
            out.append(p)
    return out


def causal_language(triage: list) -> int:
    """Set each entry's `causal_flags` from its summary, headline and each
    issue's observation and evidence. Returns how many were flagged."""
    n = 0
    for t in triage:
        texts = [t.get("summary"), t.get("headline")]
        for p in t.get("issues") or []:
            if isinstance(p, dict):
                texts += [p.get("observation"), p.get("evidence")]
        flags: list = []
        for s in texts:
            for p in causal_phrases(s):
                if p not in flags:
                    flags.append(p)
        t["causal_flags"] = flags
        n += len(flags)
    return n


# -------------------------------------------------------- field names --
# A CARD NEVER SAYS decision_spend_per_arm. The reader owns the business; a
# key out of the facts is the review talking to itself. Flagged, not
# rewritten, like causal wording.

#: A snake_case token: two or more lower-case words joined by underscores.
JARGON = _re.compile(r"(?<![\w])[a-z]+(?:_[a-z0-9]+)+(?![\w])")

#: Field names flagged even when this run's facts do not carry them.
JARGON_KEYS = frozenset({
    "decision_spend_per_arm", "min_conversions_per_arm", "test_floor",
    "cpa_rank_in_goal", "spend_to_baseline_cpa", "impact_score",
    "parent_spend_share_pct"})


def _fact_keys(node, out: set | None = None) -> set:
    """Every snake_case key anywhere in the facts."""
    out = set() if out is None else out
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and "_" in k:
                out.add(k)
            _fact_keys(v, out)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _fact_keys(v, out)
    return out


def _jargon_texts(t: dict) -> list:
    texts = [t.get("summary"), t.get("headline")]
    texts += [s for s in t.get("not_yet") or [] if isinstance(s, str)]
    for p in t.get("issues") or []:
        if not isinstance(p, dict):
            continue
        rec = p.get("recommendation") or {}
        texts += [p.get("observation"), p.get("evidence"), p.get("diagnosis"),
                  rec.get("action"), rec.get("expected_outcome"),
                  (p.get("confidence") or {}).get("reason")]
        plan = p.get("test_plan") or {}
        texts += [plan.get("control"), plan.get("challenger"),
                  plan.get("setup"), plan.get("decide_when")]
    return [s for s in texts if isinstance(s, str) and s]


def jargon_language(triage: list, facts: dict | None = None) -> int:
    """Set each entry's `jargon_flags`: the field names its text writes
    (snake_case tokens that are a key in `facts` or in JARGON_KEYS), each
    once, in order. Account names and quoted text are cut out first, so an
    ad called "ad_a_v5" or a quoted line is not jargon. Returns the count."""
    keys = JARGON_KEYS | _fact_keys(facts or {})
    names = sorted(_names(facts or {}), key=len, reverse=True)
    n = 0
    for t in triage:
        mine = sorted(set(names) | _names({"campaign": t.get("campaign"),
                                           "where": t.get("where")}),
                      key=len, reverse=True)
        flags: list = []
        for s in _jargon_texts(t):
            for name in mine:
                s = s.replace(name, " ")
            s = _QUOTED.sub(" ", s)
            for m in JARGON.finditer(s):
                w = m.group(0)
                if w in keys and w not in flags:
                    flags.append(w)
        t["jargon_flags"] = flags
        n += len(flags)
    return n


# -------------------------------------------------------- the reply shape --

def parse_reply(reply) -> tuple:
    """(campaign rows, account_note) from either reply shape.

    The prompt asks for {"account_note": str, "campaigns": [...]}. A bare
    array -- what the prompt asked for before round 2, and what a model
    sometimes still sends -- is read as the campaign list, with the note
    taken from an `account_note` on its first element if there is one.
    """
    if isinstance(reply, dict):
        rows = reply.get("campaigns")
        rows = rows if isinstance(rows, list) else []
        note = reply.get("account_note")
    elif isinstance(reply, list):
        rows = reply
        first = reply[0] if reply and isinstance(reply[0], dict) else {}
        note = first.get("account_note")
    else:
        raise ValueError(f"expected a JSON object or array, got "
                         f"{type(reply).__name__}")
    return rows, (_clean(note) or None)


# ------------------------------------------------------ what matters today --
# THE ACCOUNT SUMMARY is deterministic: every figure in it is a column of
# ads.goal_impact (018), and every count is a len() over the published
# triage. The one model-written sentence (`account_note`) rides beside it,
# labelled as the review's and checked like a card.

#: The share of a goal's CPA rise the named campaigns must reach together.
CONTRIBUTORS_REACH = 60
#: Patterns read as creative-side deterioration, in the order they are named.
CREATIVE_SIDE = ("creative_engagement", "possible_fatigue",
                 "audience_saturation", "clickier_poorer_intent")


def _words(goal) -> str:
    return str(goal or "(no goal)").replace("_", " ").capitalize()


def _printed(value, places: int = 2) -> str:
    """A SQL percentage printed without its sign ("up 18.20%"): formatting,
    not arithmetic -- the sign is the word beside it."""
    return f"{float(value):.{places}f}".lstrip("-") + "%"


def _ev_at_least(ev, floor: str) -> bool:
    return (ev in diagnose.EVIDENCE
            and diagnose.EVIDENCE.index(ev) >= diagnose.EVIDENCE.index(floor))


def _rank_key(r: dict):
    rank = r.get("contribution_rank")
    return (rank is None, rank if rank is not None else 0)


def _goal_summary(g: dict, camps: list) -> dict:
    goal, cpa, ev = g.get("optimization_goal"), g.get("cpa_pct"), \
        g.get("evidence_level")
    lines, contributors = [], []
    if cpa is not None:
        rose, fell = float(cpa) > 0, float(cpa) < 0
        if not (rose or fell):
            text = f"{_words(goal)}: CPA unchanged on the prior 7 days"
        else:
            text = (f"{_words(goal)}: CPA {'up' if rose else 'down'} "
                    f"{_printed(cpa)} on the prior 7 days")
        if rose and _ev_at_least(ev, "directional"):
            reached = None
            for i, r in enumerate(sorted(camps, key=_rank_key), 1):
                contributors.append({
                    "campaign": r.get("campaign_name"),
                    "contribution_share_pct": r.get("contribution_share_pct"),
                    "cumulative_share_pct": r.get("cumulative_share_pct")})
                cum = r.get("cumulative_share_pct")
                if cum is not None and float(cum) >= CONTRIBUTORS_REACH:
                    reached = (i, cum)
                    break
            if reached:
                n, cum = reached
                text += (f"; {n} campaign{'s' if n != 1 else ''} "
                         f"account{'' if n != 1 else 's'} for "
                         f"{_printed(cum, 1)} of that rise")
            else:
                contributors = []
        lines.append(text + ".")
    return {"goal": goal, "cpa_pct": cpa, "evidence": ev,
            "contributors": contributors, "lines": lines}


def account_summary(pack: dict, triage: list, note: str | None = None) -> dict:
    """"What matters today" (contracts2 D4 `account_summary`).

    Per goal, from 018's goal row: its CPA move on the prior 7 days and,
    when it rose on at least directional evidence, how many campaigns -- the
    first whose cumulative_share_pct reaches CONTRIBUTORS_REACH -- account
    for it. Then, over the act and test campaigns, how many show
    creative-side deterioration and how many post-click (by the patterns of
    their act/test issues), and how many campaigns have issues with low
    current impact (monitor bucket, or a test whose impact_score is at or
    below PRIORITY_2_IMPACT; never an act). Lines are f-strings of SQL values and len() counts.
    """
    rows = (pack.get("goal_impact") or []) if pack.get("impact_available") else []
    goals = []
    for g in rows:
        if g.get("level") != "goal":
            continue
        camps = [r for r in rows if r.get("level") == "campaign"
                 and r.get("optimization_goal") == g.get("optimization_goal")]
        goals.append(_goal_summary(g, camps))

    acting = [t for t in triage if t.get("bucket") in ("act", "test")]
    seen: list = []
    creative_n = post_n = 0
    for t in acting:
        patterns = {p.get("pattern") for p in t.get("issues") or []
                    if (p.get("recommendation") or {}).get("type")
                    in ("act", "test")}
        mine = [x for x in CREATIVE_SIDE if x in patterns]
        seen += [x for x in mine if x not in seen]
        creative_n += 1 if mine else 0
        post_n += 1 if "post_click" in patterns else 0
    lines = []
    parts = []
    if creative_n:
        ordered = [x for x in CREATIVE_SIDE if x in seen]
        parts.append(f"{creative_n} show{'s' if creative_n == 1 else ''} "
                     f"creative-side deterioration ({' / '.join(ordered)})")
    if post_n:
        parts.append(f"{post_n} post-click" if parts else
                     f"{post_n} show{'s' if post_n == 1 else ''} post-click "
                     f"deterioration")
    if parts:
        lines.append(", ".join(parts) + ".")
    low = len([t for t in triage if t.get("issues") and (
        t.get("bucket") == "monitor"
        # An act campaign is never "low impact": the evidence already
        # supports a change there, whatever its dollars say.
        or (t.get("bucket") == "test"
            and _score(t.get("impact")) is not None
            and _score(t.get("impact")) <= PRIORITY_2_IMPACT))])
    if low:
        lines.append(f"{low} {'has' if low == 1 else 'have'} issues with low "
                     f"current impact.")
    return {"goals": goals, "lines": lines, "note": note}


# ----------------------------------------------------------- outcome tags --

OUTCOME_TAG = ("status", "implemented_on", "match")


def attach_outcomes(triage: list, ledger: dict | None) -> int:
    """Put each issue's ledger reading on it as `outcome` (D4): status,
    implemented_on, match, settled_days and result. Returns how many."""
    entries = (ledger or {}).get("entries") or {}
    n = 0
    for t in triage:
        for p in t.get("issues") or []:
            e = entries.get(str(p.get("id"))) if isinstance(p, dict) else None
            if not e:
                continue
            m = e.get("measurement") or {}
            p["outcome"] = {**{k: e.get(k) for k in OUTCOME_TAG},
                            "settled_days": m.get("settled_days"),
                            "result": m.get("result")}
            n += 1
    return n


#: What the model is told about each recommendation still in play.
FOLLOWUP_FIELDS = ("id", "type", "kind", "variable", "challenger_ad",
                   "ad_group", "status", "implemented_on", "match")


def followups(ledger: dict | None, campaigns: list, for_campaign) -> dict:
    """campaign -> the ledger entries still in play (open, implemented,
    measuring), as the prompt's `followups`: no model-written text, only the
    ledger's own fields and, once measured, the result and settled days.
    `for_campaign` is outcomes.for_campaign."""
    out = {}
    for c in campaigns:
        name = c.get("campaign")
        rows = []
        for e in for_campaign(ledger or {}, name):
            m = e.get("measurement") or {}
            rows.append(_dense({**{k: e.get(k) for k in FOLLOWUP_FIELDS},
                                "settled_days": m.get("settled_days"),
                                "result": m.get("result")}))
        if rows:
            out[name] = rows
    return out
