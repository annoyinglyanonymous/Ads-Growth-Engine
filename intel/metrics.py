"""Performance verbs: overview, compare, why, fatigue, ad, trend, funnel.

READ ONLY. This module imports fetch_all/fetch_one and nothing else that
touches the database. It does not import db_owner, and tests/test_read_only.py
fails if that ever changes.

It also does no arithmetic. Every rate, delta and effect in here came out of a
function in migrations/003_ads_metrics.sql -- this module selects, labels and
orders. The moment a ratio is computed in Python, there are two definitions of
that ratio and the dashboard and the agent can disagree about it.
"""

from __future__ import annotations

import json
from datetime import date

from psycopg import errors as _pg_errors

from db import fetch_all, fetch_one

from . import context


async def _frame(slug: str, days: int, until: date | None) -> dict:
    """The window every verb reports back, so an answer always says its scope."""
    b = await context.brand(slug)
    settled = await context.settled_through(b["id"])
    since, end, unsettled = context.window(days, until, settled)
    return {
        "brand": b["slug"],
        "brand_id": str(b["id"]),
        "since": since,
        "until": end,
        "days": days,
        "settled_through": settled,
        "unsettled_days": unsettled,
        "caveat": context.caveat(unsettled, settled),
    }


async def overview(slug: str, days: int = 28, until: date | None = None,
                   level: str = "ad", limit: int = 200) -> dict:
    """What ran, what it cost, and what a reviewer made of it."""
    f = await _frame(slug, days, until)
    rows = await fetch_all(
        """
        select w.entity_key, w.platform_id, w.entity_name, w.optimization_goal,
               w.days, w.impressions, w.clicks, w.link_clicks, w.spend,
               w.conversions, w.currency, w.currencies, w.rates,
               d.format, d.effective_status, d.last_seen_at,
               ang.slug as angle_slug, ang.name as angle_name, ang.family,
               fa.source as tag_source, fa.confidence as tag_confidence,
               rv.overall as review_overall, rv.angle_observed
          from ads.window_metrics(%s, %s, %s, %s) w
          left join ads.ad d          on d.ad_key   = w.entity_key
          -- facet_effective, not ad_facet: the latter holds one row per wording
          -- ever tagged, so joining it on ad_key alone fanned this list out --
          -- one ad appearing N times under N angles. It also means an ad the
          -- engine shipped carries its angle here without anyone tagging it.
          left join ads.facet_effective fa on fa.ad_key = w.entity_key
          left join ads.angle ang     on ang.id     = fa.angle_id
          left join ads.ad_review_latest rv on rv.ad_key = w.entity_key
         order by w.spend desc nulls last
         limit %s
        """,
        (f["brand_id"], f["since"], f["until"], level, limit),
    )
    return {"verb": "overview", "level": level, **f,
            "row_count": len(rows), "rows": rows,
            "mixed_currency": any((r.get("currencies") or 1) > 1 for r in rows)}


async def compare(slug: str, days: int = 14, until: date | None = None,
                  level: str = "ad", limit: int = 100) -> dict:
    """This window against the equal window before it."""
    f = await _frame(slug, days, until)
    rows = await fetch_all(
        """
        select entity_key, entity_name, prior_since, prior_until,
               current_m, prior_m, delta, pct, appeared, disappeared
          from ads.compare(%s, %s, %s, %s)
         order by abs(coalesce((delta ->> 'spend')::numeric, 0)) desc
         limit %s
        """,
        (f["brand_id"], f["since"], f["until"], level, limit),
    )
    return {"verb": "compare", "level": level, **f,
            "row_count": len(rows), "rows": rows}


async def why(slug: str, days: int = 7, until: date | None = None,
              limit: int = 25) -> dict:
    """Why the brand's CPA moved: rate effect vs mix effect, per ad.

    total_effect sums across ALL rows to the brand's CPA change. rate_effect
    and mix_effect are NULL for ads that converted in only one of the two
    windows, because the split is undefined there -- the `reason` column names
    which case it is, and `attributable_share` says how much of the move the
    split actually accounts for. Reporting the split as if it covered
    everything would be the interesting half of a lie.
    """
    f = await _frame(slug, days, until)
    # Four independent reads of one window, run concurrently.
    rows, totals, bridge, changes = await context.gather_in_order(fetch_all(
        """
        select ad_key, entity_name, spend_current, spend_prior,
               conv_current, conv_prior, cpa_current, cpa_prior,
               rate_effect, mix_effect, total_effect, reason
          from ads.cpa_bridge(%s, %s, %s)
         limit %s
        """,
        (f["brand_id"], f["since"], f["until"], limit),
    ), fetch_one(
        """
        select sum(total_effect)                                   as cpa_change,
               sum(rate_effect)                                    as rate_effect,
               sum(mix_effect)                                     as mix_effect,
               sum(total_effect) filter (where reason = 'attributable') as attributable,
               count(*) filter (where reason <> 'attributable')     as unattributable_ads
          from ads.cpa_bridge(%s, %s, %s)
        """,
        (f["brand_id"], f["since"], f["until"]),
    # The part of the move the split cannot assign, and how much it covers --
    # from the function the brief reads, so the two pages cannot disagree.
    # Without unattributable_effect, rate + mix does not add up to the net and
    # a reader who checks the arithmetic trusts neither number.
    ), fetch_one(
        """
        select unattributable_effect, attributable_share, attributable_ads
          from ads.cpa_bridge_totals(%s, %s, %s)
        """,
        (f["brand_id"], f["since"], f["until"]),
    ), _changes(f["brand_id"], f["since"], f["until"]))
    totals = {**(totals or {}), **(bridge or {})}
    return {"verb": "why", **f, "totals": totals,
            "row_count": len(rows), "rows": rows,
            "structure_changes": changes}


async def _changes(brand_id: str, since: date, until: date) -> list[dict] | dict:
    """Budget, status and goal edits in the window -- or a note saying why not.

    Depends on public.meta_structure_changes, which is growth-engine's
    migration 046. Until that is applied there is nothing to read, and saying
    so is much better than an empty list: an empty list reads as "nothing
    changed", which is the single most misleading answer this verb can give.
    """
    exists = await fetch_one(
        "select to_regclass('ads.structure_change') is not null as ok")
    if not exists or not exists["ok"]:
        return {"available": False,
                "why": "ads.structure_change is not present, so no budget or "
                       "status edits can be read. It arrives with "
                       "growth-engine's migration 046 plus this repo's 007. "
                       "Until then, 'nothing changed' is unknown, not true."}
    rows = await fetch_all(
        """
        select entity_type, entity_id, entity_name, field,
               old_value, new_value, changed_at
          from ads.structure_change
         where brand_id = %s and changed_at::date between %s and %s
         order by changed_at desc
         limit 100
        """,
        (brand_id, since, until),
    )
    return {"available": True, "row_count": len(rows), "rows": rows}


async def fatigue(slug: str, window_days: int = 7, until: date | None = None,
                  min_spend: float = 100.0,
                  include_unconfident: bool = False) -> dict:
    """Five named symptoms per ad, and their count.

    `frequency_rise` is DAILY frequency accelerating, not cumulative frequency
    -- that number is not in this database. Say "daily frequency" when
    reporting this or the reader will hear "seen four times", which is a
    different and much stronger claim.
    """
    b = await context.brand(slug)
    settled = await context.settled_through(b["id"])
    asof = until or settled or date.today()
    unsettled = max(0, (asof - settled).days) if settled else 0

    listed = fetch_all(
        """
        select ad_key, entity_name, optimization_goal, recent_since, recent_until,
               days_observed, spend_recent, impressions_recent, confident,
               link_ctr_decline, cpm_rise, cpa_rise, frequency_rise,
               ranking_drop, score, recent, prior
          from ads.fatigue(%s, %s, %s::int, %s::numeric)
         where %s or confident
        """,
        (b["id"], asof, window_days, min_spend, include_unconfident),
    )
    # The casts are load-bearing. psycopg sends 7 as smallint and 100.0 as
    # double precision; ads.fatigue declares integer and numeric. Postgres
    # will not widen those while resolving which function you meant, so the
    # call fails with "function ads.fatigue(...) does not exist" -- a message
    # that sends you looking for a missing migration rather than a cast.
    counted = fetch_one(
        "select count(*) as n from ads.fatigue(%s, %s, %s::int, %s::numeric) "
        "where not confident and score > 0",
        (b["id"], asof, window_days, min_spend),
    )
    # Independent reads: one round trip of wall time rather than two.
    # gather_in_order keeps the sequential error semantics (the rows' failure
    # is the one raised).
    rows, suppressed = await context.gather_in_order(listed, counted)
    return {
        "verb": "fatigue", "brand": b["slug"], "brand_id": str(b["id"]),
        "asof": asof, "window_days": window_days, "min_spend": min_spend,
        "settled_through": settled, "unsettled_days": unsettled,
        "caveat": context.caveat(unsettled, settled),
        "frequency_note": "frequency_rise tests DAILY frequency "
                          "(impressions / that day's reach) accelerating. "
                          "Cumulative frequency is not in this database.",
        "row_count": len(rows), "rows": rows,
        "suppressed_unconfident": (suppressed or {}).get("n", 0),
    }


async def ad(ad_key: str, days: int = 90) -> dict:
    """One ad: what it is, what it said, what it did, what we think it is."""
    # Five independent reads keyed on ad_key, run concurrently. A missing ad
    # still raises below; the other four will simply have found nothing.
    head, copy, facet, review, series = await context.gather_in_order(fetch_one(
        """
        select d.ad_key, d.platform, d.platform_ad_id, d.brand_id, d.name,
               d.status, d.effective_status, d.format, d.cta, d.link_url,
               d.utm_campaign, d.utm_content, d.image_url, d.thumbnail_url,
               d.first_seen_at, d.last_seen_at, d.campaign_asset_id,
               g.name as ad_group_name, g.optimization_goal,
               c.name as campaign_name, c.objective
          from ads.ad d
          left join ads.ad_group g on g.ad_group_key = d.ad_group_key
          left join ads.campaign c on c.campaign_key = d.campaign_key
         where d.ad_key = %s
        """,
        (ad_key,),
    ), fetch_one(
        "select copy_hash, texts, first_headline, first_body "
        "from ads.ad_copy where ad_key = %s", (ad_key,)), fetch_one(
        """
        select fa.angle_id, ang.slug as angle_slug, ang.name as angle_name,
               ang.family, fa.hook, fa.offer, fa.audience, fa.source,
               fa.confidence, fa.rationale, fa.tagged_by, fa.copy_hash
          from ads.ad_facet fa
          left join ads.angle ang on ang.id = fa.angle_id
          left join ads.ad_copy c on c.ad_key = fa.ad_key
         where fa.ad_key = %s
         -- Deliberately ads.ad_facet and not ads.facet_effective: this page is
         -- where a stale tag has to be VISIBLE, and facet_effective drops it by
         -- construction, which would make tag_is_stale permanently false.
         --
         -- The ordering is what was missing. An ad carries one facet row per
         -- wording ever tagged, and fetch_one with no ORDER BY returned an
         -- arbitrary one -- so tag_is_stale was a coin flip whenever more than
         -- one existed. Current wording first, then newest.
         order by (fa.copy_hash = c.copy_hash) desc nulls last,
                  fa.created_at desc
         limit 1
        """, (ad_key,)), fetch_one(
        "select overall, angle_observed, reviewed_at, reviewed_by "
        "from ads.ad_review_latest where ad_key = %s", (ad_key,)), fetch_all(
        """
        select day, impressions, clicks, link_clicks, spend, conversions,
               landing_page_views, frequency_this_day, reach_this_day
          from ads.fact_ad_day
         where ad_key = %s
         order by day desc
         limit %s
        """, (ad_key, days)))
    if not head:
        raise ValueError(f"no ad with ad_key {ad_key}")

    # A tag made against different wording than the ad currently runs. The
    # (ad_key, copy_hash) key means this cannot silently happen -- but it can
    # legitimately exist as a stale row, and the reader has to be told.
    stale = bool(facet and copy and facet["copy_hash"] != copy["copy_hash"])
    return {"verb": "ad", "ad": head, "copy": copy, "facet": facet,
            "review": review, "tag_is_stale": stale,
            "daily": series, "day_count": len(series)}


async def trend(slug: str, metric: str = "cpa", days: int = 90,
                until: date | None = None, level: str = "campaign",
                bucket: int = 7) -> dict:
    """One metric over consecutive equal buckets, per entity.

    Buckets rather than raw days because a daily CPA on a small account is
    mostly noise about which day a lead happened to land on.
    """
    allowed = {"cpa", "cpc", "cpm", "ctr", "link_ctr", "conversion_rate",
               "cost_per_link_click", "lp_view_rate"}
    if metric not in allowed:
        raise ValueError(f"unknown metric {metric!r}. Known: "
                         f"{', '.join(sorted(allowed))}")
    if bucket < 1:
        raise ValueError(f"--bucket must be at least 1, got {bucket}")

    f = await _frame(slug, days, until)
    buckets, cursor = [], f["until"]
    while cursor >= f["since"]:
        start = max(f["since"], cursor.fromordinal(cursor.toordinal() - bucket + 1))
        buckets.append((start, cursor))
        cursor = start.fromordinal(start.toordinal() - 1)

    out = []
    for start, end in reversed(buckets):
        rows = await fetch_all(
            "select entity_key, entity_name, spend, conversions, "
            "       (rates ->> %s)::numeric as value "
            "  from ads.window_metrics(%s, %s, %s, %s) "
            " order by spend desc nulls last",
            (metric, f["brand_id"], start, end, level),
        )
        out.append({"since": start, "until": end, "rows": rows})
    return {"verb": "trend", "metric": metric, "level": level,
            "bucket_days": bucket, **f, "buckets": out}


# ---------------------------------------------------------------------------
# The funnel ladder (migration 017).
# ---------------------------------------------------------------------------

#: The only thresholds a caller may override, and the type each is declared
#: with in ads.funnel_change. Named notation with an explicit cast, for the
#: reason fatigue() gives above: psycopg sends 5 as smallint, and Postgres will
#: not widen an argument while resolving which function you meant -- the call
#: fails as "function does not exist", which reads as a missing migration.
#: The allowlist is also what keeps a keyword argument from ever reaching the
#: SQL text as anything but a fixed parameter name.
FUNNEL_THRESHOLDS: dict[str, str] = {
    "p_cpa_flat_pct": "numeric",
    "p_driver_min_share_pct": "numeric",
    "p_conv_directional": "int",
    "p_conv_moderate": "int",
    "p_conv_strong": "int",
    "p_clicks_directional": "int",
    "p_clicks_moderate": "int",
    "p_clicks_strong": "int",
    "p_fatigue_min_spend": "numeric",
    "p_fatigue_min_link_clicks": "int",
    "p_fatigue_freq_rise_pct": "numeric",
    "p_fatigue_ctr_drop_pct": "numeric",
    "p_fatigue_cpc_rise_pct": "numeric",
    "p_cvr_stable_band_pct": "numeric",
    "p_test_cpa_multiple": "numeric",
}

FUNNEL_LEVELS = ("ad", "ad_group", "campaign", "brand")

#: Which pair `intel funnel --days N` reads.
FUNNEL_PAIR_FOR_DAYS = {7: "last_7_vs_prior_7",
                        14: "last_14_vs_prior_14",
                        28: "last_28_vs_prior_28"}

_WINDOW_COLS = ("pair", "cur_since", "cur_until", "pri_since", "pri_until",
                "cur_days", "pri_days", "settled_edge", "unsettled_days")
_JSONB_COLS = ("cur_m", "pri_m", "pct")

FUNNEL_UNAVAILABLE = ("migration 017 is not applied "
                      "(python ads_migrate.py --status)")


def _decoded(value):
    """jsonb as a Python object. psycopg already does this; a stub or a
    driver configured otherwise may hand back the text."""
    if isinstance(value, str):
        return json.loads(value)
    return value


async def funnel_ladder(slug: str, level: str | None = None,
                        pairs: list[str] | None = None,
                        **thresholds) -> dict:
    """Every entity across every named window pair, in one round trip.

    ads.analysis_windows defines the pairs (anchored at the settled edge) and
    ads.funnel_change reads each one; this selects, groups by pair and
    decodes. Nothing here computes a figure -- every rate, change, share and
    evidence level is a column 017 returned.

    -> {"available": True, "brand", "settled_through",
        "pairs": {pair: {cur_since, cur_until, pri_since, pri_until,
                         cur_days, pri_days, unsettled_days, rows: [...]}}}
    or {"available": False, "why": ...} when 017 is not applied.
    """
    if level is not None and level not in FUNNEL_LEVELS:
        raise ValueError(f"unknown level {level!r}. Known: "
                         f"{', '.join(FUNNEL_LEVELS)} (Meta's adset is "
                         f"ad_group here)")
    unknown = sorted(set(thresholds) - set(FUNNEL_THRESHOLDS))
    if unknown:
        raise ValueError(f"unknown funnel threshold(s) {unknown}. Known: "
                         f"{', '.join(FUNNEL_THRESHOLDS)}")

    b = await context.brand(slug)
    # Names come from the allowlist, never from the caller's spelling of them.
    named = [(k, cast) for k, cast in FUNNEL_THRESHOLDS.items()
             if thresholds.get(k) is not None]
    overrides = "".join(f", {k} => %s::{cast}" for k, cast in named)
    wanted = list(pairs) if pairs is not None else None
    params = (b["id"], b["id"], level,
              *[thresholds[k] for k, _ in named],
              wanted, wanted)
    sql = f"""
        select w.pair, w.cur_since, w.cur_until, w.pri_since, w.pri_until,
               w.cur_days, w.pri_days, w.settled_edge, w.unsettled_days,
               fc.*
          from ads.analysis_windows(%s::uuid, now()) with ordinality w
          left join lateral ads.funnel_change(
                    %s::uuid, %s::text,
                    w.cur_since, w.cur_until, w.pri_since, w.pri_until,
                    w.settled_edge{overrides}) with ordinality fc on true
         where (%s::text[] is null or w.pair = any(%s::text[]))
         order by w.ordinality, fc.ordinality
        """
    # left join, not cross join: a pair whose windows hold no entity still
    # reports its dates rather than vanishing from the answer. Ordinality
    # keeps both functions' own row order (pairs short end first; levels
    # brand -> ad, then spend), which a join does not otherwise promise.
    try:
        rows = await fetch_all(sql, params)
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": FUNNEL_UNAVAILABLE}

    out: dict = {"available": True, "brand": b["slug"],
                 "settled_through": None, "pairs": {}}
    for r in rows:
        out["settled_through"] = r.get("settled_edge")
        entry = out["pairs"].setdefault(r["pair"], {
            **{k: r.get(k) for k in _WINDOW_COLS
               if k not in ("pair", "settled_edge")},
            "rows": []})
        if r.get("level") is None:
            continue  # the pair's dates, with no entity in either window
        row = {k: v for k, v in r.items()
               if k not in _WINDOW_COLS and k != "ordinality"}
        for k in _JSONB_COLS:
            row[k] = _decoded(row.get(k))
        entry["rows"].append(row)
    if out["settled_through"] is None:
        # No pair at all -- no data on either side of the edge. Say where the
        # edge is anyway, so "no pairs" is not misread as "no edge".
        out["settled_through"] = await context.settled_through(b["id"])
    return out


async def funnel(slug: str, days: int = 7, until: date | None = None,
                 level: str = "campaign", limit: int = 50) -> dict:
    """What changed through each funnel stage, for one window pair.

    `--days` picks the pair: 7 is the last 7 settled days against the 7
    before, 14 and 28 likewise. The windows always end at the settled edge --
    that is what makes "last 7 vs prior 7" one fixed pair of dates -- so
    `until` is refused rather than quietly ignored.
    """
    if until is not None:
        raise ValueError("funnel windows end at the settled edge "
                         "(ads.analysis_windows); --until is not supported")
    pair = FUNNEL_PAIR_FOR_DAYS.get(days)
    if pair is None:
        raise ValueError(f"--days must be one of "
                         f"{', '.join(map(str, FUNNEL_PAIR_FOR_DAYS))}, "
                         f"got {days}")
    if limit < 1:
        raise ValueError(f"--limit must be at least 1, got {limit}")
    try:
        ladder = await funnel_ladder(slug, level=level, pairs=[pair])
    except _pg_errors.RaiseException as exc:
        # 017's own refusals (no active ad account, bad windows) are one
        # sentence for stderr, not a traceback.
        raise ValueError(str(exc).strip().splitlines()[0]) from None
    if not ladder["available"]:
        raise ValueError(ladder["why"])

    p = ladder["pairs"].get(pair)
    rows = (p or {}).get("rows", [])
    settled = ladder["settled_through"]
    unsettled = (p or {}).get("unsettled_days") or 0
    return {
        "verb": "funnel", "brand": ladder["brand"], "level": level,
        "pair": pair,
        **{k: (p or {}).get(k) for k in ("cur_since", "cur_until",
                                         "pri_since", "pri_until",
                                         "cur_days", "pri_days")},
        "settled_through": settled, "unsettled_days": unsettled,
        "caveat": (context.caveat(unsettled, settled) if p else
                   "One side of this pair holds no data, so there is "
                   "nothing to compare."),
        "frequency_note": "daily_frequency is impressions per person-day "
                          "(ad level only), not cumulative frequency, which "
                          "is not in this database. Say daily frequency.",
        "decomposition_note": "cpa_share_* split the CPA change exactly "
                              "into CPM, link CTR and CVR (conversions per "
                              "link click, including view-through). Signed, "
                              "they sum to 100, and are null when CPA is "
                              "flat or undefined; see `decomposition`.",
        "row_count": len(rows), "truncated": len(rows) > limit,
        "rows": rows[:limit],
    }


# ---------------------------------------------------------------------------
# Impact, facets by goal, and outcome readings (migration 018).
#
# Same stance as funnel_ladder: one round trip each, every argument cast to
# the type 018 declares (psycopg's smallint/double guesses make Postgres
# report "function does not exist"), jsonb decoded, nothing computed. Each
# returns {"available": False, "why": ...} until 018 is applied, so the page
# and the pack degrade rather than crash.
# ---------------------------------------------------------------------------

IMPACT_UNAVAILABLE = ("migration 018 is not applied "
                      "(python ads_migrate.py --status)")

#: The pairs ads.analysis_windows defines (017), short end first.
ANALYSIS_PAIRS = ("unsettled_vs_last_7", "last_7_vs_prior_7",
                  "last_14_vs_prior_14", "last_28_vs_prior_28",
                  "last_7_vs_before")

#: The dimensions ads.facet_by_goal accepts -- the same six as 013's
#: facet_performance, checked here so a typo is one sentence, not a raise.
FACET_DIMENSIONS = ("hook", "offer", "audience", "format", "family", "angle")

_ARM_JSONB = ("control_m", "challenger_m", "effect_pct")


def _refusal(exc: Exception) -> ValueError:
    """018's own refusals (overlapping windows, the same ad on both arms) as
    one sentence, the way funnel() reports 017's."""
    text = str(exc).strip()
    return ValueError(text.splitlines()[0] if text else type(exc).__name__)


async def goal_impact(slug: str, pair: str = "last_7_vs_prior_7") -> dict:
    """Excess dollars per goal and per single-goal campaign, for one pair.

    The windows come from ads.analysis_windows in the same query, so "last 7
    vs prior 7" here is the same fourteen days as everywhere else. Every
    figure -- excess_cost, impact_score, contribution, its shares and its
    rank -- is a column ads.goal_impact returned. Dollars may be ordered
    across goals; a CPA or a contribution may not (see 018's header).

    -> {"available": True, "brand", "pair", "settled_through",
        "windows": {cur_since, cur_until, pri_since, pri_until, cur_days,
                    pri_days, unsettled_days} | None, "rows": [...]}
    """
    if pair not in ANALYSIS_PAIRS:
        raise ValueError(f"unknown pair {pair!r}. Known: "
                         f"{', '.join(ANALYSIS_PAIRS)}")
    b = await context.brand(slug)
    sql = """
        select w.pair, w.cur_since, w.cur_until, w.pri_since, w.pri_until,
               w.cur_days, w.pri_days, w.settled_edge, w.unsettled_days,
               gi.*
          from ads.analysis_windows(%s::uuid, now()) w
          left join lateral ads.goal_impact(
                    %s::uuid,
                    w.cur_since, w.cur_until, w.pri_since, w.pri_until,
                    w.settled_edge) with ordinality gi on true
         where w.pair = %s::text
         order by gi.ordinality
        """
    # left join: a pair whose windows hold no goal still reports its dates.
    # Ordinality keeps 018's own order (goals by spend, goal row first, then
    # its campaigns by contribution), which a join does not otherwise promise.
    try:
        rows = await fetch_all(sql, (b["id"], b["id"], pair))
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": IMPACT_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None

    out: dict = {"available": True, "brand": b["slug"], "pair": pair,
                 "settled_through": None, "windows": None, "rows": []}
    for r in rows:
        if out["windows"] is None:
            out["settled_through"] = r.get("settled_edge")
            out["windows"] = {k: r.get(k) for k in _WINDOW_COLS
                              if k not in ("pair", "settled_edge")}
        if r.get("level") is None:
            continue  # the pair's dates, with no goal in either window
        out["rows"].append({k: v for k, v in r.items()
                            if k not in _WINDOW_COLS and k != "ordinality"})
    return out


async def facet_by_goal(slug: str, since: date, until: date,
                        dimension: str) -> dict:
    """One facet dimension per optimization goal, each value against its own
    goal's CPA. value None is the untagged bucket; nothing crosses goals.

    -> {"available": True, "brand", "dimension", "since", "until",
        "rows": [...]}
    """
    if dimension not in FACET_DIMENSIONS:
        raise ValueError(f"unknown dimension {dimension!r}. Known: "
                         f"{', '.join(FACET_DIMENSIONS)}")
    b = await context.brand(slug)
    try:
        rows = await fetch_all(
            """
            select fg.optimization_goal, fg.value, fg.ads_run, fg.spend,
                   fg.conversions, fg.rates, fg.goal_cpa, fg.relative_pct,
                   fg.relative, fg.evidence_level, fg.spend_share_pct,
                   fg.mixed_currency
              from ads.facet_by_goal(%s::uuid, %s::date, %s::date, %s::text) fg
            """,
            (b["id"], since, until, dimension),
        )
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": IMPACT_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None
    out_rows = []
    for r in rows:
        row = dict(r)
        row["rates"] = _decoded(row.get("rates"))
        out_rows.append(row)
    return {"available": True, "brand": b["slug"], "dimension": dimension,
            "since": since, "until": until, "rows": out_rows}


async def entity_outcome(slug: str, level: str, entity_key: str,
                         before_since: date, before_until: date,
                         after_since: date, after_until: date) -> dict:
    """One entity's 017 funnel row with current = after, prior = before.

    The settled edge is read in the same query and handed to 018, so
    after_settled_days and the evidence cap agree with `settled_through`.

    -> {"available": True, "brand", "settled_through", "row": {...} | None}
    (row None: the entity ran in neither window.)
    """
    if level not in FUNNEL_LEVELS:
        raise ValueError(f"unknown level {level!r}. Known: "
                         f"{', '.join(FUNNEL_LEVELS)} (Meta's adset is "
                         f"ad_group here)")
    b = await context.brand(slug)
    sql = """
        with edge as (select ads.settled_through(%s::uuid, now()) as settled)
        select e.settled as settled_through, eo.*
          from edge e
          left join lateral ads.entity_outcome(
                    %s::uuid, %s::text, %s::uuid,
                    %s::date, %s::date, %s::date, %s::date,
                    e.settled) eo on true
        """
    params = (b["id"], b["id"], level, entity_key,
              before_since, before_until, after_since, after_until)
    try:
        rows = await fetch_all(sql, params)
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": IMPACT_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None

    settled = rows[0].get("settled_through") if rows else None
    row = None
    for r in rows:
        if r.get("level") is None:
            continue  # the edge, with no row for the entity
        row = {k: v for k, v in r.items() if k != "settled_through"}
        for k in _JSONB_COLS:
            row[k] = _decoded(row.get(k))
        break
    return {"available": True, "brand": b["slug"],
            "settled_through": settled, "row": row}


async def arm_compare(slug: str, control_ad_key: str, challenger_ad_key: str,
                      since: date, until: date,
                      decision_spend=None) -> dict:
    """A test's two arms over one window: each arm's figures and rates, the
    challenger's percent effect against the control, evidence from the
    smaller arm, and whether both reached `decision_spend` (None: not asked,
    and spend_sufficient comes back None).

    -> {"available": True, "brand", "settled_through", "row": {...} | None}
    """
    b = await context.brand(slug)
    sql = """
        with edge as (select ads.settled_through(%s::uuid, now()) as settled)
        select e.settled as settled_through, ac.*
          from edge e
          cross join lateral ads.arm_compare(
                    %s::uuid, %s::uuid, %s::uuid,
                    %s::date, %s::date, e.settled, %s::numeric) ac
        """
    params = (b["id"], b["id"], control_ad_key, challenger_ad_key,
              since, until, decision_spend)
    try:
        rows = await fetch_all(sql, params)
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": IMPACT_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None

    if not rows:
        return {"available": True, "brand": b["slug"],
                "settled_through": None, "row": None}
    r = rows[0]
    row = {k: v for k, v in r.items() if k != "settled_through"}
    for k in _ARM_JSONB:
        row[k] = _decoded(row.get(k))
    return {"available": True, "brand": b["slug"],
            "settled_through": r.get("settled_through"), "row": row}


# ---------------------------------------------------------------------------
# Typical ranges by campaign family (migration 019).
#
# Same stance as the 018 wrappers: one round trip each, every argument cast
# to the type 019 declares, nothing computed. A band is context beside the
# campaign's own windows (017), never a target, and never read across goals.
# Each returns {"available": False, "why": ...} until 019 is applied.
# ---------------------------------------------------------------------------

FAMILY_UNAVAILABLE = ("migration 019 is not applied "
                      "(python ads_migrate.py --status)")

#: The pair whose current window family_baseline's default `before` precedes,
#: so the page's bands are the ones campaign_vs_family reads by default.
FAMILY_DEFAULT_PAIR = "last_7_vs_prior_7"


async def family_baseline(slug: str, before: date | None = None) -> dict:
    """Typical ranges per campaign family and goal (audience None) and per
    family, goal and audience: p25 / p50 / p75 of weekly campaign figures
    over the 26 weeks ending at `before`.

    `before` None is the day before last 7 vs prior 7's current window --
    that pair's pri_until, read from ads.analysis_windows in the same query,
    so the bands are the ones campaign_vs_family reads by default.

    -> {"available": True, "brand", "before": date | None, "rows": [...]}
    (before None and no rows: no pair to anchor on.)
    """
    b = await context.brand(slug)
    sql = """
        with arg as (
            select coalesce(%s::date,
                            (select w.pri_until
                               from ads.analysis_windows(%s::uuid, now()) w
                              where w.pair = %s::text)) as before_day
        ),
        ok as (select a.before_day from arg a where a.before_day is not null)
        select ok.before_day, fb.*
          from ok
          left join lateral ads.family_baseline(
                    %s::uuid, ok.before_day) with ordinality fb on true
         order by fb.ordinality
        """
    # `ok` drops a NULL anchor before the function sees it (019 refuses a
    # NULL p_before). left join: an anchor with no band still reports the
    # day. Ordinality keeps 019's own order (family, goal, metric, the
    # all-audience row first).
    try:
        rows = await fetch_all(sql, (before, b["id"], FAMILY_DEFAULT_PAIR,
                                     b["id"]))
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": FAMILY_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None

    out: dict = {"available": True, "brand": b["slug"],
                 "before": None, "rows": []}
    for r in rows:
        out["before"] = r.get("before_day")
        if r.get("metric") is None:
            continue  # the anchor, with no band behind it
        out["rows"].append({k: v for k, v in r.items()
                            if k not in ("before_day", "ordinality")})
    return out


async def campaign_vs_family(slug: str,
                             pair: str = "last_7_vs_prior_7") -> dict:
    """Each single-goal campaign's current window against its family's
    typical range, one row per (campaign, metric): cur, p25 / p50 / p75,
    position, percentile, campaign_weeks, band_level and evidence_level --
    every one a column ads.campaign_vs_family returned. The band ends the
    day before the current window, so a window is never in its own band.

    -> {"available": True, "brand", "pair", "settled_through",
        "windows": {cur_since, cur_until, pri_since, pri_until, cur_days,
                    pri_days, unsettled_days} | None, "rows": [...]}
    """
    if pair not in ANALYSIS_PAIRS:
        raise ValueError(f"unknown pair {pair!r}. Known: "
                         f"{', '.join(ANALYSIS_PAIRS)}")
    b = await context.brand(slug)
    sql = """
        select w.pair, w.cur_since, w.cur_until, w.pri_since, w.pri_until,
               w.cur_days, w.pri_days, w.settled_edge, w.unsettled_days,
               cv.*
          from ads.analysis_windows(%s::uuid, now()) w
          left join lateral ads.campaign_vs_family(
                    %s::uuid,
                    w.cur_since, w.cur_until,
                    w.settled_edge) with ordinality cv on true
         where w.pair = %s::text
         order by cv.ordinality
        """
    # left join: a pair whose current window holds no campaign still reports
    # its dates. Ordinality keeps 019's order (campaigns by current spend,
    # each campaign's metrics in a fixed order).
    try:
        rows = await fetch_all(sql, (b["id"], b["id"], pair))
    except _pg_errors.UndefinedFunction:
        return {"available": False, "why": FAMILY_UNAVAILABLE}
    except _pg_errors.RaiseException as exc:
        raise _refusal(exc) from None

    out: dict = {"available": True, "brand": b["slug"], "pair": pair,
                 "settled_through": None, "windows": None, "rows": []}
    for r in rows:
        if out["windows"] is None:
            out["settled_through"] = r.get("settled_edge")
            out["windows"] = {k: r.get(k) for k in _WINDOW_COLS
                              if k not in ("pair", "settled_edge")}
        if r.get("metric") is None:
            continue  # the pair's dates, with no campaign in the window
        out["rows"].append({k: v for k, v in r.items()
                            if k not in _WINDOW_COLS and k != "ordinality"})
    return out
