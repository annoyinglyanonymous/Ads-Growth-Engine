"""Experiment verbs. READ ONLY -- proposals are filed through `intel record`.

Neither verb here names a winner, and neither is capable of it: the result
function reports whether the PRE-REGISTERED effect was cleared and whether each
arm has enough spend to be read. Reading a result and deciding is a person's
job, and 005 has no column to write the decision into from this side.
"""

from __future__ import annotations

from db import fetch_all, fetch_one

from . import context


async def experiments(slug: str, open_only: bool = False,
                      angle: str | None = None) -> dict:
    """Everything we have decided to test, and where each one stands."""
    b = await context.brand(slug)
    rows = await fetch_all(
        """
        select e.id, e.name, e.question, e.hypothesis, e.primary_metric,
               e.minimum_effect_pct, e.minimum_spend_per_arm,
               e.started_on, e.ended_on, e.proposed_by,
               e.conclusion, e.concluded_by, e.concluded_at, e.created_at,
               case when e.conclusion is not null then 'concluded'
                    when e.started_on is null     then 'proposed'
                    when e.ended_on is null       then 'running'
                    else                               'awaiting_conclusion'
               end as state,
               (select jsonb_agg(jsonb_build_object(
                           'label', arm.label,
                           'angle_slug', a.slug,
                           'utm_content', arm.utm_content,
                           'ad_keys', arm.ad_keys)
                       order by arm.label)
                  from ads.experiment_arm arm
                  left join ads.angle a on a.id = arm.angle_id
                 where arm.experiment_id = e.id) as arms
          from ads.experiment e
         where e.brand_id = %s
           and (not %s or e.conclusion is null)
           and (%s::text is null or exists (
                   select 1 from ads.experiment_arm arm
                     join ads.angle a on a.id = arm.angle_id
                    where arm.experiment_id = e.id and a.slug = %s))
         order by e.created_at desc
        """,
        (b["id"], open_only, angle, angle),
    )
    return {"verb": "experiments", "brand": b["slug"], "angle": angle,
            "open_only": open_only, "row_count": len(rows), "rows": rows,
            # The state a proposal sits in until somebody launches it. Worth
            # surfacing because a bank full of 'proposed' experiments that
            # nobody ran is a different problem from a bank full of
            # inconclusive ones.
            "awaiting_launch": sum(1 for r in rows if r["state"] == "proposed")}


async def experiment(slug: str, name: str) -> dict:
    """One experiment, with its computed result per arm."""
    b = await context.brand(slug)
    head = await fetch_one(
        """
        select id, name, question, hypothesis, primary_metric,
               minimum_effect_pct, minimum_spend_per_arm,
               started_on, ended_on, proposed_by,
               conclusion, concluded_by, concluded_at
          from ads.experiment
         where brand_id = %s and name = %s
        """,
        (b["id"], name),
    )
    if not head:
        raise ValueError(
            f"no experiment named {name!r} for {slug}. "
            f"Run `intel experiments --brand {slug}` to list them.")

    arms = await fetch_all(
        """
        select arm.label, a.slug as angle_slug, a.name as angle_name,
               arm.utm_content, arm.ad_keys
          from ads.experiment_arm arm
          left join ads.angle a on a.id = arm.angle_id
         where arm.experiment_id = %s
         order by arm.label
        """,
        (head["id"],),
    )

    if head["started_on"] is None:
        # 005 raises rather than returning zeros for an unlaunched experiment,
        # because zeros read as "we tested it and nothing happened". Catch it
        # here so the verb answers the question that was actually asked --
        # "where is this one" -- instead of failing.
        return {"verb": "experiment", "brand": b["slug"], "experiment": head,
                "arms": arms, "result": None, "state": "proposed",
                "note": "Not launched, so there is no window and no result. "
                        "started_on is set in the UI when the test goes live; "
                        "it is not part of the agent write shape."}

    result = await fetch_all(
        """
        select label, is_baseline, ads_in_arm, spend, impressions, link_clicks,
               conversions, rates, metric_value, spend_sufficient, effect_pct,
               improvement, effect_exceeds_minimum,
               window_since, window_until, unsettled_days
          from ads.experiment_result(%s)
        """,
        (head["id"],),
    )
    insufficient = [r["label"] for r in result if not r["spend_sufficient"]]
    return {
        "verb": "experiment", "brand": b["slug"], "experiment": head,
        "arms": arms, "result": result,
        "state": "concluded" if head["conclusion"] else "running",
        "arms_below_minimum_spend": insufficient,
        "readable": not insufficient,
        "note": None if not insufficient else
                f"Arm(s) {', '.join(insufficient)} have not reached the "
                f"minimum spend registered for this test "
                f"({head['minimum_spend_per_arm']}). The effect shown is "
                f"arithmetic, not a result.",
    }
