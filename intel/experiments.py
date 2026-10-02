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
                        "started_on is recorded by the person who launches it, "
                        "with the statement in CLAUDE.md ('Starting and "
                        "concluding an experiment'); it is not part of the "
                        "agent write shape."}

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


# ------------------------------------------- the ledger, beside the log --
# The report recommends a test, the person launches the new ad tagged
# utm_content=EXP-<issue id>, the outcomes ledger (intel/outcomes.py) sees
# the launch and scripts/suggest.py files the experiment as a proposal. These
# two read that ledger for /experiments. Pure: no SQL, no arithmetic, and no
# statement text either -- the confirm-launch SQL is in the template, because
# a read module carries no mutating SQL (tests/test_read_only.py).

#: Ledger statuses that mean the launch has been seen.
LAUNCHED = ("implemented", "measuring", "measured")
#: Ledger statuses of a recommended test nobody has launched yet.
NOT_LAUNCHED = ("open", "not_detectable")
RECOMMENDED_KINDS = ("creative_test", "post_click", "other")


def _entries(ledger: dict | None) -> list:
    return [e for e in ((ledger or {}).get("entries") or {}).values()
            if isinstance(e, dict)]


def with_launches(rows: list, ledger: dict | None) -> list:
    """The log's rows, each proposed one carrying `launch` when the ledger
    entry it was filed from has been seen launched: {issue_id,
    implemented_on, match, matched_by}. Matched by the experiment name the
    ledger recorded at filing. Returns new dicts."""
    by_name = {}
    for e in _entries(ledger):
        name = (e.get("experiment") or {}).get("name")
        if name and e.get("status") in LAUNCHED and e.get("implemented_on"):
            by_name[name] = e
    out = []
    for r in rows or []:
        r = dict(r)
        e = by_name.get(r.get("name")) if r.get("state") == "proposed" else None
        r["launch"] = ({"issue_id": e.get("id"),
                        "implemented_on": e.get("implemented_on"),
                        "match": e.get("match"),
                        "matched_by": e.get("matched_by"),
                        "tag": e.get("tag")} if e else None)
        out.append(r)
    return out


def recommended_tests(ledger: dict | None) -> list:
    """Tests the report recommends that nobody has launched: ledger entries
    of a test kind, open or not detectable, newest recommendation first."""
    rows = [e for e in _entries(ledger)
            if e.get("type") == "test"
            and e.get("kind") in RECOMMENDED_KINDS
            and e.get("status") in NOT_LAUNCHED]
    rows.sort(key=lambda e: (str(e.get("recommended_on") or ""),
                             str(e.get("id"))), reverse=True)
    return [{k: e.get(k) for k in (
        "id", "campaign", "ad_group", "kind", "variable", "control",
        "challenger", "tag", "setup", "recommended_on", "last_mentioned_on",
        "status", "reason")} for e in rows]


def launched_unfiled(ledger: dict | None) -> list:
    """Launched tests whose experiment is not on file yet, with the reason
    the last run gave (it is tried again on the next run)."""
    out = []
    for e in _entries(ledger):
        if e.get("type") == "test" and e.get("kind") in RECOMMENDED_KINDS \
                and e.get("status") in LAUNCHED and not e.get("experiment"):
            why = (e.get("experiment_skipped") or e.get("experiment_error")
                   or {}).get("why")
            out.append({"id": e.get("id"), "campaign": e.get("campaign"),
                        "variable": e.get("variable"),
                        "implemented_on": e.get("implemented_on"),
                        "match": e.get("match"),
                        "matched_by": e.get("matched_by"),
                        "why": why or "not filed yet; the next review files it"})
    out.sort(key=lambda r: (str(r["implemented_on"]), str(r["id"])),
             reverse=True)
    return out
