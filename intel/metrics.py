"""Performance verbs: overview, compare, why, fatigue, ad, trend.

READ ONLY. This module imports fetch_all/fetch_one and nothing else that
touches the database. It does not import db_owner, and tests/test_read_only.py
fails if that ever changes.

It also does no arithmetic. Every rate, delta and effect in here came out of a
function in migrations/003_ads_metrics.sql -- this module selects, labels and
orders. The moment a ratio is computed in Python, there are two definitions of
that ratio and the dashboard and the agent can disagree about it.
"""

from __future__ import annotations

from datetime import date

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
          left join ads.ad_facet fa   on fa.ad_key  = w.entity_key
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
    rows = await fetch_all(
        """
        select ad_key, entity_name, spend_current, spend_prior,
               conv_current, conv_prior, cpa_current, cpa_prior,
               rate_effect, mix_effect, total_effect, reason
          from ads.cpa_bridge(%s, %s, %s)
         limit %s
        """,
        (f["brand_id"], f["since"], f["until"], limit),
    )
    totals = await fetch_one(
        """
        select sum(total_effect)                                   as cpa_change,
               sum(rate_effect)                                    as rate_effect,
               sum(mix_effect)                                     as mix_effect,
               sum(total_effect) filter (where reason = 'attributable') as attributable,
               count(*) filter (where reason <> 'attributable')     as unattributable_ads
          from ads.cpa_bridge(%s, %s, %s)
        """,
        (f["brand_id"], f["since"], f["until"]),
    )
    changes = await _changes(f["brand_id"], f["since"], f["until"])
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

    rows = await fetch_all(
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
    suppressed = await fetch_one(
        "select count(*) as n from ads.fatigue(%s, %s, %s::int, %s::numeric) "
        "where not confident and score > 0",
        (b["id"], asof, window_days, min_spend),
    )
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
    head = await fetch_one(
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
    )
    if not head:
        raise ValueError(f"no ad with ad_key {ad_key}")

    copy = await fetch_one(
        "select copy_hash, texts, first_headline, first_body "
        "from ads.ad_copy where ad_key = %s", (ad_key,))
    facet = await fetch_one(
        """
        select fa.angle_id, ang.slug as angle_slug, ang.name as angle_name,
               ang.family, fa.hook, fa.offer, fa.audience, fa.source,
               fa.confidence, fa.rationale, fa.tagged_by, fa.copy_hash
          from ads.ad_facet fa
          left join ads.angle ang on ang.id = fa.angle_id
         where fa.ad_key = %s
        """, (ad_key,))
    review = await fetch_one(
        "select overall, angle_observed, reviewed_at, reviewed_by "
        "from ads.ad_review_latest where ad_key = %s", (ad_key,))
    series = await fetch_all(
        """
        select day, impressions, clicks, link_clicks, spend, conversions,
               landing_page_views, frequency_this_day, reach_this_day
          from ads.fact_ad_day
         where ad_key = %s
         order by day desc
         limit %s
        """, (ad_key, days))

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
