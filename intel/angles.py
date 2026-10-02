"""Creative verbs: angles, candidates, queue, coverage, versus.

READ ONLY -- fetch_all/fetch_one, never db_owner. Tagging happens through
`intel record`, which is the one write verb and lives in record.py.
"""

from __future__ import annotations

from datetime import date

from db import fetch_all, fetch_one

from . import context
from .metrics import _frame


async def angles(slug: str, days: int = 90, until: date | None = None,
                 product: str | None = None, min_spend: float = 250.0) -> dict:
    """The bank, with what each angle actually did in the window."""
    f = await _frame(slug, days, until)
    rows = await fetch_all(
        """
        select angle_id, family, angle_slug, angle_name, definition, ads_run,
               tagged_ads, inherited_ads, stale_tag_ads, spend,
               conversions, cpa, first_run, last_run, state
          from ads.angle_coverage(%s::uuid, %s, %s, %s::text, %s::numeric)
        """,
        (f["brand_id"], f["since"], f["until"], product, min_spend),
    )
    proposed = await fetch_all(
        """
        select id, family, slug, name, definition, added_by, created_at
          from ads.angle
         where brand_id = %s and status = 'proposed'
         order by created_at desc
        """,
        (f["brand_id"],),
    )
    return {
        "verb": "angles", **f, "product": product, "min_spend": min_spend,
        "row_count": len(rows), "rows": rows,
        # Surfaced separately, never mixed into the ranked list: a proposed
        # angle is something the agent suggested and nobody has signed. Listing
        # it beside tested angles would read as part of the vocabulary.
        "awaiting_approval": proposed,
        "stale_tag_ads": sum((r["stale_tag_ads"] or 0) for r in rows),
        "note": "cpa is only comparable between angles whose ads ran under the "
                "same optimization_goal. Check before ranking on it.",
        # Two things a reader cannot see in a spend column and should not have
        # to ask for. inherited_ads is how much of an angle's number nobody
        # judged; stale_tag_ads is spend MISSING from it because the only tag
        # those ads carry describes wording they no longer run.
        "attribution_note": "inherited_ads were read off the campaign_asset "
                            "chain, not judged by anyone. stale_tag_ads are "
                            "absent from spend entirely: ads.ad_copy holds "
                            "only the current wording (002:249), so copy that "
                            "was rewritten cannot be attributed to what it "
                            "said at the time.",
    }


async def candidates(slug: str) -> dict:
    """Angle handles a reviewer wrote that the bank does not map yet.

    The queue a person drains. This is how the vocabulary grows from what the
    ads actually said, rather than from a list somebody wrote in advance.
    """
    b = await context.brand(slug)
    rows = await fetch_all(
        """
        select observed, reviews, first_seen, last_seen, meta_ad_ids
          from ads.angle_candidate
         where brand_id = %s
         order by reviews desc, last_seen desc
        """,
        (b["id"],),
    )
    return {"verb": "candidates", "brand": b["slug"],
            "row_count": len(rows), "rows": rows,
            "note": "Mapping one of these is a decision about vocabulary, so "
                    "it is filed as an alias by a person, not by a tagging "
                    "pass. Propose the mapping; do not assume it."}


async def queue(slug: str, days: int = 90, until: date | None = None,
                limit: int = 50) -> dict:
    """Ads that spent money and carry no tag, most expensive first.

    Ranked by spend because tagging effort should follow the money: an
    untagged ad that spent $4,000 is distorting every angle number, and one
    that spent $12 is not.

    `inheritable` is the important column. An ad whose campaign_asset_id
    resolves through creative_concepts to a campaign angle can be tagged with
    no judgement at all -- that is door one, and it should be drained before
    anybody reads copy and forms an opinion.
    """
    f = await _frame(slug, days, until)
    rows = await fetch_all(
        """
        select w.entity_key as ad_key, w.entity_name, w.spend, w.conversions,
               w.impressions, d.format, d.effective_status,
               c.copy_hash, c.first_headline, c.first_body,
               inh.angle_id     as inheritable_angle_id,
               inh.campaign_angle_name,
               (inh.ad_key is not null) as inheritable,
               rv.angle_observed
          from ads.window_metrics(%s, %s, %s, 'ad') w
          join ads.ad d      on d.ad_key = w.entity_key
          left join ads.ad_copy c on c.ad_key = w.entity_key
          left join ads.inherited_facet inh on inh.ad_key = w.entity_key
          left join ads.ad_review_latest rv on rv.ad_key = w.entity_key
         where not exists (
             select 1 from ads.ad_facet fa
              where fa.ad_key = w.entity_key
                and fa.copy_hash = c.copy_hash)
         order by w.spend desc nulls last
         limit %s
        """,
        (f["brand_id"], f["since"], f["until"], limit),
    )
    untagged_spend = sum((r["spend"] or 0) for r in rows)
    return {"verb": "queue", **f, "row_count": len(rows), "rows": rows,
            "untagged_spend_in_window": untagged_spend,
            "inheritable_count": sum(1 for r in rows if r["inheritable"])}


async def coverage(slug: str, product: str | None = None, days: int = 365,
                   until: date | None = None, min_spend: float = 250.0) -> dict:
    """What we have never run. The set difference the bank exists for."""
    f = await _frame(slug, days, until)
    rows = await fetch_all(
        "select angle_id, family, angle_slug, angle_name, definition, ads_run, "
        "       tagged_ads, inherited_ads, stale_tag_ads, spend, "
        "       conversions, cpa, first_run, last_run, state "
        # Cast for the same reason as ads.fatigue: a bare NULL product arrives
        # as `unknown` and a float min_spend as double precision, and neither
        # resolves against the declared (uuid, date, date, text, numeric).
        "  from ads.angle_coverage(%s::uuid, %s, %s, %s::text, %s::numeric)",
        (f["brand_id"], f["since"], f["until"], product, min_spend),
    )
    by_state: dict[str, list] = {"never_run": [], "under_spent": [], "tested": []}
    for r in rows:
        by_state[r["state"]].append(r)

    tried = await fetch_all(
        """
        select e.name, e.question, e.hypothesis, e.conclusion, e.concluded_at,
               a.slug as angle_slug
          from ads.experiment e
          left join ads.experiment_arm arm on arm.experiment_id = e.id
          left join ads.angle a on a.id = arm.angle_id
         where e.brand_id = %s and a.id is not null
         order by e.created_at desc
        """,
        (f["brand_id"],),
    )
    return {
        "verb": "coverage", **f, "product": product,
        "never_run": by_state["never_run"],
        "under_spent": by_state["under_spent"],
        "tested": by_state["tested"],
        # So a proposal can say "we tested that in March and it was
        # inconclusive" instead of proposing it for the seventh time.
        "prior_experiments": tried,
    }


async def versus(slug: str, a: str, b: str, days: int = 30,
                 until: date | None = None) -> dict:
    """Two angles over identical windows.

    Both sides carry their own spend-sufficiency flag and their own
    optimization_goal spread. Two angles that ran under different goals are not
    comparable on CPA however clean the numbers look, and this returns the
    evidence for that judgement rather than making it.
    """
    f = await _frame(slug, days, until)
    out = {}
    for slug_ in (a, b):
        angle = await fetch_one(
            "select id, slug, name, family, definition from ads.angle "
            " where brand_id = %s and slug = %s", (f["brand_id"], slug_))
        if not angle:
            raise ValueError(
                f"no angle {slug_!r} for {slug}. Run `intel angles --brand "
                f"{slug}` to see the bank.")
        agg = await fetch_one(
            """
            select count(distinct f.ad_key)::int as ads,
                   sum(f.spend) as spend, sum(f.impressions)::bigint as impressions,
                   sum(f.clicks)::bigint as clicks,
                   sum(f.link_clicks)::bigint as link_clicks,
                   sum(f.conversions)::bigint as conversions,
                   ads.rate(sum(f.impressions)::bigint, sum(f.clicks)::bigint,
                            sum(f.link_clicks)::bigint, sum(f.spend),
                            sum(f.conversions)::bigint,
                            sum(f.landing_page_views)::bigint) as rates
              from ads.fact_ad_day f
              join ads.facet_effective fa on fa.ad_key = f.ad_key
             where f.brand_id = %s and fa.angle_id = %s
               and f.day between %s and %s
            """,
            (f["brand_id"], angle["id"], f["since"], f["until"]),
        )
        goals = await fetch_all(
            """
            select g.optimization_goal, sum(f.spend) as spend
              from ads.fact_ad_day f
              join ads.facet_effective fa on fa.ad_key = f.ad_key
              join ads.ad d on d.ad_key = f.ad_key
              left join ads.ad_group g on g.ad_group_key = d.ad_group_key
             where f.brand_id = %s and fa.angle_id = %s
               and f.day between %s and %s
             group by g.optimization_goal
            """,
            (f["brand_id"], angle["id"], f["since"], f["until"]),
        )
        out[slug_] = {"angle": angle, **(agg or {}),
                      "optimization_goals": goals,
                      "comparable_on_cost": len(goals) <= 1}

    same_goal = (
        {g["optimization_goal"] for g in out[a]["optimization_goals"]}
        == {g["optimization_goal"] for g in out[b]["optimization_goals"]}
    )
    return {"verb": "versus", **f, "a": a, "b": b, "sides": out,
            "same_optimization_goal": same_goal,
            "note": None if same_goal else
                    "These angles ran under different optimization goals. A CPA "
                    "difference between them is at least partly a difference "
                    "between the goals, not between the copy (042)."}


async def label_coverage(slug: str, days: int = 90,
                         until: date | None = None) -> dict:
    """Two coverages, and which question each one answers.

    `angle_attribution` is ads.untagged_spend: spend whose ad resolves to an
    ANGLE. On a brand whose angle bank has no members it is zero by
    construction, and it says nothing about the copy.

    `creative_labels` counts the ads that spent and carry a hook or an offer,
    which scripts/tag.py files. That is the one that says whether the copy has
    been read. Printing only the first made a page announce "100% untagged"
    beside a hook-and-offer table full of real numbers, which is how the
    suggestion prompt once concluded nothing could be said about the writing.

    Same SQL as intel/brief.py's creative_labels section, so the two pages
    cannot count a label differently.
    """
    f = await _frame(slug, days, until)
    labels = await fetch_one(
        "select count(*)::int                                     as ads, "
        "       count(*) filter (where fe.hook is not null)::int   as with_a_hook, "
        "       count(*) filter (where fe.offer is not null)::int  as with_an_offer "
        "  from (select distinct f.ad_key "
        "          from ads.fact_ad_day f "
        "         where f.brand_id = %s::uuid "
        "           and f.day between %s and %s) spent "
        "  left join ads.facet_effective fe on fe.ad_key = spent.ad_key",
        (f["brand_id"], f["since"], f["until"]))
    angle = await fetch_one(
        "select total_spend, tagged_spend, untagged_spend, untagged_ads "
        "  from ads.untagged_spend(%s::uuid, %s, %s)",
        (f["brand_id"], f["since"], f["until"]))
    return {"verb": "label_coverage", **f,
            "creative_labels": labels or {}, "angle_attribution": angle or {}}
