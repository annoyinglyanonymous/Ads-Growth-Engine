"""The creative pack: what the account is currently saying, and what it cost.

READ ONLY. Nothing here writes, files or concludes.

WHY THIS IS A VERB AND NOT A PAGE HELPER

It began in ui.py, which was wrong the moment anything other than a page needed
it. `scripts/suggest.py` builds this after every import to publish a suggestion,
and ui.py renders the same pack underneath that suggestion as its evidence.
ui.py's own rule is that every page calls the same function the agent calls, and
a pack that lives in the web layer cannot be one of those.

GROUPED BY OPTIMIZATION GOAL, AND THAT IS THE WHOLE POINT

This account runs four. A single "best and worst ads" list would rank every
OFFSITE_CONVERSIONS ad a failure and every LEAD_GENERATION ad a success -- and a
reader asked about the COPY would then explain that difference in terms of the
writing. The grouping is what stops a measurement artefact being read as a
creative finding.

NOTHING HERE COMPUTES

Rows arrive from ads.window_metrics with their rates already calculated; this
selects and orders them. `ads_run` is a len(), which CLAUDE.md's first rule
permits -- counting the rows a function returned is not computing a rate.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from db import fetch_all, fetch_one

from . import context, metrics

ROOT = Path(__file__).resolve().parent.parent

#: Where scripts/suggest.py publishes. One file per brand per day, beside the
#: briefs, for the reason scripts/brief.py gives about its own archive: a live
#: page silently rewrites its own past opinion every time Meta restates, so the
#: only way to ask what it said last week is to have kept a copy.
SUGGESTION_DIR = ROOT / "suggestions"

#: How many ads per optimization goal go into the pack, at each end.
#:
#: Three, not ten. The pack carries a headline and a body per ad, so the cost of
#: another row is real -- and a model handed forty ads writes about the account
#: rather than about the writing, which is the one thing this page is for.
PER_GOAL = 3

#: Rows per creative dimension in the pack. Ten, by spend.
DIMENSION_ROWS = 10

#: The rate every goal here is ranked on. One metric, because the ads inside a
#: single optimization goal ARE comparable on it -- that is what makes the
#: grouping worth doing.
RANK_RATE = "cpa"


#: Per campaign card: how many ad groups and how many of its ads ride along.
#: By spend, so the rows shown are the ones carrying the campaign's money.
CAMPAIGN_AD_GROUPS = 3
CAMPAIGN_ADS = 2

#: How much of each leading ad's body a campaign card carries.
CAMPAIGN_BODY_CHARS = 160


async def _ad_campaigns(ad_keys: list[str]) -> dict:
    """ad_key -> campaign_key, for the ads asked about."""
    if not ad_keys:
        return {}
    rows = await fetch_all(
        "select ad_key, campaign_key from ads.ad "
        " where ad_key = any(%s::uuid[])", (ad_keys,))
    return {str(r["ad_key"]): str(r["campaign_key"]) for r in rows}


async def _campaigns(brand: str, days: int, until, ad_rows: list) -> tuple:
    """One entry per campaign that ran in the window, by spend.

    A CAMPAIGN HAS NO SINGLE GOAL, and this is built around that.
    ads.window_metrics returns a NULL optimization_goal at campaign level
    because one campaign can hold ad groups optimising for different events,
    and a campaign CPA across two of them is the non-comparable number
    CLAUDE.md warns about. So the campaign's own cost per result is only
    carried when every ad group in it shares one goal, and the evidence that
    IS comparable -- where each ad group sits among the ad groups optimising
    for the same thing -- is carried per ad group.

    `cpa_rank_in_goal` is an ORDERING of rates ads.window_metrics already
    computed, 1 being the cheapest. It is not a rate and nothing is divided to
    make it; it is `goals` from creative_pack, one level up.

    Returns (campaigns, the ad rows each campaign leads with), the second so
    the caller can fetch their copy in the one query it already makes.
    """
    camp = await metrics.overview(brand, days, until, level="campaign",
                                  limit=200)
    grp = await metrics.overview(brand, days, until, level="ad_group",
                                 limit=1000)
    camp_rows = camp.get("rows") or []
    grp_rows = grp.get("rows") or []
    if not camp_rows:
        return [], []

    group_keys = [str(r["entity_key"]) for r in grp_rows if r.get("entity_key")]
    group_campaign: dict = {}
    if group_keys:
        for r in await fetch_all(
                "select ad_group_key, campaign_key, effective_status "
                "  from ads.ad_group where ad_group_key = any(%s::uuid[])",
                (group_keys,)):
            group_campaign[str(r["ad_group_key"])] = r

    camp_keys = [str(r["entity_key"]) for r in camp_rows if r.get("entity_key")]
    camp_meta = {str(r["campaign_key"]): r for r in await fetch_all(
        "select campaign_key, effective_status, objective from ads.campaign "
        " where campaign_key = any(%s::uuid[])", (camp_keys,))}

    ad_campaign = await _ad_campaigns(
        [str(r["entity_key"]) for r in ad_rows if r.get("entity_key")])

    # Rank every ad group among the ad groups sharing its goal.
    by_goal: dict = {}
    for r in grp_rows:
        if _rate(r) is not None:
            by_goal.setdefault(r.get("optimization_goal") or "(none)",
                               []).append(r)
    rank: dict = {}
    ranked_in: dict = {}
    for goal, rows in by_goal.items():
        rows.sort(key=_rate)
        for i, r in enumerate(rows, 1):
            rank[str(r["entity_key"])] = i
        ranked_in[goal] = len(rows)

    groups_of: dict = {}
    for r in grp_rows:
        meta = group_campaign.get(str(r.get("entity_key"))) or {}
        if meta.get("campaign_key"):
            groups_of.setdefault(str(meta["campaign_key"]), []).append((r, meta))

    ads_of: dict = {}
    for r in ad_rows:      # already ordered by spend, desc
        ck = ad_campaign.get(str(r.get("entity_key")))
        if ck:
            ads_of.setdefault(ck, []).append(r)

    out, leading = [], []
    for c in camp_rows:
        key = str(c.get("entity_key"))
        groups = groups_of.get(key, [])
        goals = sorted({(g.get("optimization_goal") or "(none)")
                        for g, _ in groups})
        ads = ads_of.get(key, [])
        lead = ads[:CAMPAIGN_ADS]
        leading.extend(lead)
        meta = camp_meta.get(key) or {}
        out.append({
            "_key": key,
            "campaign": c.get("entity_name"),
            "status": meta.get("effective_status"),
            "objective": meta.get("objective"),
            "optimization_goals": goals,
            "spend": c.get("spend"),
            "conversions": c.get("conversions"),
            # Withheld across goals, for the reason window_metrics withholds
            # the goal itself at this level.
            "cpa": _rate(c) if len(goals) == 1 else None,
            "link_ctr": _rate(c, "link_ctr"),
            "ads_run": len(ads),
            "ads_with_no_conversions": sum(1 for a in ads
                                           if _rate(a) is None),
            "ad_groups": [{
                "ad_group": g.get("entity_name"),
                "optimization_goal": g.get("optimization_goal"),
                "status": m.get("effective_status"),
                "spend": g.get("spend"),
                "conversions": g.get("conversions"),
                "cpa": _rate(g),
                "cpa_rank_in_goal": rank.get(str(g.get("entity_key"))),
                "ad_groups_ranked_in_goal":
                    ranked_in.get(g.get("optimization_goal") or "(none)"),
            } for g, m in groups[:CAMPAIGN_AD_GROUPS]],
            "more_ad_groups": max(0, len(groups) - CAMPAIGN_AD_GROUPS),
            "leading_ads": lead,
            "tiring_ads": [],
        })
    return out, leading


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


async def creative_pack(brand: str, days: int, until=None) -> dict:
    """What the account is currently saying, and what it cost, by goal.

    GROUPED BY optimization_goal, AND THAT IS THE WHOLE POINT. This account
    runs four goals; a single "best and worst ads" list would rank every
    OFFSITE_CONVERSIONS ad a failure and every LEAD_GENERATION ad a success,
    and a model asked about the COPY would then explain that difference in
    terms of the writing. Grouping is what stops the feature inventing a
    creative finding out of a measurement artefact.

    Nothing is totalled or averaged here. Rows arrive from ads.window_metrics
    with their rates already computed, and this selects and orders them.
    """
    # Defaulted HERE rather than by each caller, so the page and the publisher
    # cannot disagree about which window the suggestion is about.
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
                             key=lambda kv: -len(kv[1])):
        ranked = [r for r in rows if _rate(r) is not None]
        ranked.sort(key=_rate)
        # `rankable` is false when NO ad under this goal converted -- THRUPLAY
        # is the live case, 16 ads and not one conversion. The ads are still
        # worth reading for their copy; the ranking is what has to be withheld.
        best = ranked[:PER_GOAL]
        # max(), so the two ends cannot overlap. With five ranked ads,
        # ranked[-3:] would return rows 2, 3 and 4 -- and rows 2 and 3 are
        # already in `best`, so the same ad would be presented to the model as
        # both the cheapest and the dearest under one goal. It would then
        # explain, in terms of the copy, why an ad beats itself.
        worst = list(reversed(ranked[max(PER_GOAL, len(ranked) - PER_GOAL):]))
        fallback = sorted(rows, key=lambda r: float(r.get("spend") or 0),
                          reverse=True)[:PER_GOAL] if not ranked else []
        picked = best + worst + fallback
        chosen.extend(picked)
        goals.append({
            "optimization_goal": goal,
            "ads_run": len(rows),
            "rankable_on_cost": bool(ranked),
            "ads_with_no_conversions": len(rows) - len(ranked),
            "ranked_on": RANK_RATE if ranked else None,
            "cheapest": best,
            "dearest": worst,
            "by_spend_only": fallback,
        })

    # ---------------------------------------------------------- campaigns --
    # The unit the triage is written about. Built BEFORE the copy fetch so the
    # ads each campaign leads with ride the same single query as the rest.
    campaigns, campaign_ads = await _campaigns(brand, days, until,
                                               d.get("rows") or [])
    chosen.extend(campaign_ads)

    keys = list({str(r["entity_key"]) for r in chosen if r.get("entity_key")})
    copy_by_key: dict = {}
    if keys:
        for row in await fetch_all(
            """
            select c.ad_key, c.first_headline, c.first_body,
                   c.headlines, c.bodies, d.cta,
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
                     limit 1) as cta_text
              from ads.ad_copy c
              join ads.ad d on d.ad_key = c.ad_key
             where c.ad_key = any(%s::uuid[])
            """, (keys,)):
            copy_by_key[str(row["ad_key"])] = row

    def _shape(row: dict) -> dict:
        """One ad as the model sees it: what it says, and what that cost."""
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

    def _lead(row: dict) -> dict:
        """An ad as a campaign card needs it: the opening, not the essay.

        The full body is what the `goals` block is for. Twenty-odd campaigns of
        full bodies would take the prompt past the command-line budget, and
        what a reader stops or scrolls on is the first line anyway.
        """
        s = _shape(row)
        body = (s.get("body") or "").strip()
        s["body"] = body[:CAMPAIGN_BODY_CHARS] + (
            "…" if len(body) > CAMPAIGN_BODY_CHARS else "")
        s.pop("wordings_on_this_ad", None)
        s.pop("format", None)
        return s

    for c in campaigns:
        c["leading_ads"] = [_lead(r) for r in c["leading_ads"]]

    # ads.facet_performance, not a group-by written here. It is the function
    # that already knows a dimension is not rankable when its rows ran under
    # more than one optimization goal, and `comparable_on_cost` is that answer.
    # Passing it through unchanged is how the model learns what it may not say.
    #
    # FOUR DIMENSIONS, where this used to read one. hook, offer and audience
    # returned a single NULL bucket until scripts/tag.py filled ads.ad_facet,
    # so sending them was sending nothing. They are the dimensions that make
    # this a creative analysis rather than a list of ads: "callout hooks carry
    # $19,070 across 60 ads" is a statement about the writing, where "dynamic
    # format carries $14,856" is a statement about the ad builder.
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
        # Capped by SPEND, which the ORDER BY has already applied.
        #
        # `audience` is the reason. hook and offer are closed vocabularies --
        # eight and seven values -- but audience is free text, and a labelling
        # pass reading two hundred ads produces "agency owners", "p&c agency
        # owners" and "insurance agency owners" for what a reader would call
        # one audience. Thirty-one values came back, with a long tail carrying
        # almost no money, and sending all of them pushed the pack from 16k to
        # 31k characters -- past the budget, which would have made compact()
        # cut the per-ad COPY instead. The copy is the material; the tail of
        # the audience list is not.
        # TRIMMED PER ROW, not just per dimension. A facet_performance row is
        # ~500 characters, and ~380 of them are eight rates where the model
        # reads one, plus the full optimization_goals array where the COUNT is
        # what decides whether a comparison is allowed. Four dimensions of
        # untrimmed rows took the pack from 16k to 31k -- past the budget, so
        # compact() would have cut the per-ad COPY instead. The copy is the
        # material; seven unread rates are not.
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
        if len(rows) > DIMENSION_ROWS:
            dimensions[dim].append({
                "value": f"... {len(rows) - DIMENSION_ROWS} more {dim} value(s) "
                         f"with less spend, not shown"})
    formats = dimensions["format"]

    # TWO DIFFERENT COVERAGES, and conflating them cost a whole analysis.
    #
    # ads.untagged_spend counts an ad as tagged when facet_effective resolves
    # an ANGLE for it (013:575, `fe.angle_id is not null`). ads.angle is empty
    # on this database, so every one of the 243 facets scripts/tag.py filed
    # carries a null angle and the function correctly reports 100% untagged.
    #
    # The pack used to hand that straight to the model beside a `dimensions`
    # block full of real hook and offer numbers. The model resolved the
    # contradiction the wrong way and wrote "every ad is untagged, so nothing
    # can be said about which kind of writing carries the money" -- suppressing
    # the analysis the tagging pass existed to enable.
    #
    # So both are reported, each saying which question it answers.
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

    fatigue = await metrics.fatigue(brand, days, until, 100.0, False)

    # Which campaign each tiring ad belongs to, so a card can say its own ads
    # are wearing out. Only confident rows reach here (include_unconfident is
    # False above), so a name on a card is a symptom with enough spend to read.
    by_campaign = {c["_key"]: c for c in campaigns}
    ad_campaign = await _ad_campaigns(
        [str(r["ad_key"]) for r in (fatigue.get("rows") or [])
         if r.get("ad_key")])
    for r in fatigue.get("rows") or []:
        c = by_campaign.get(ad_campaign.get(str(r.get("ad_key"))))
        # score 0 is a confident read of NO symptoms, not a tiring ad.
        if c is not None and (r.get("score") or 0) > 0:
            c["tiring_ads"].append({"ad": r.get("entity_name"),
                                    "symptoms_of_5": r.get("score")})
    for c in campaigns:
        del c["_key"]

    return {
        "verb": "creative_pack",
        "brand": d["brand"], "since": d["since"], "until": d["until"],
        "days": d["days"], "settled_through": d["settled_through"],
        "unsettled_days": d["unsettled_days"],
        "campaigns": campaigns,
        "goals": goals,
        "formats": formats,
        "dimensions": dimensions,
        "untagged": untagged,
        "tiring": [{"ad": r.get("entity_name"),
                    "optimization_goal": r.get("optimization_goal"),
                    "score": r.get("score"),
                    "confident": r.get("confident"),
                    "spend_recent": r.get("spend_recent")}
                   for r in (fatigue.get("rows") or [])[:6]],
    }


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
        if len(node) > keep:
            head.append(f"... {len(node) - keep} more rows not shown; run the "
                        f"verb for all of them")
        return head
    return node


#: What a triage reply may say about a campaign. Closed, because the page
#: colours a card by it and an invented value would render as no colour at all.
RATINGS = ("red", "yellow", "green")

SUGGESTIONS_PROMPT = """\
You are a senior direct-response media buyer reviewing one advertiser's live
Meta campaigns. Your reader owns the business and writes the ads. They want to
know, campaign by campaign, where to look first and what is worth changing
this week.

Brand: {brand}
Window: {since} to {until} ({days} days). Settled through {settled}; the last
{unsettled} day(s) can still move.

THE ACCOUNT, campaign by campaign, then by optimization goal, with the copy
and what it cost:

{facts}

`campaigns` is the unit you are rating. Each one carries its ad groups (with
the optimization goal each one optimises for), the ads that carry its spend
with their copy, and any of its ads showing confident fatigue.

`dimensions` is the same spend cut four ways -- by the HOOK the copy opens
with, the OFFER it asks for, the AUDIENCE it addresses, and the ad FORMAT.
Those labels were read off the copy itself. Use them when a campaign's copy
repeats a kind of writing the account has funded heavily, or never tried.

WHAT YOU MAY AND MAY NOT COMPARE

Ads and ad groups inside ONE optimization goal are comparable on cost per
result. Across different goals they are not, and saying "this beats that"
across two goals is the single worst mistake available here --
LEAD_GENERATION and OFFSITE_CONVERSIONS are not measuring the same event.

So judge each campaign ONLY against ad groups with the same optimization goal.
`cpa_rank_in_goal` is where an ad group sits among `ad_groups_ranked_in_goal`
ad groups sharing its goal, 1 being the cheapest per result. A campaign's own
`cpa` is null when its ad groups span two goals; that is deliberate, do not
reconstruct it.

Where a goal in `goals` has `rankable_on_cost: false`, nothing under it
converted, so there is no cost ranking to read. An engagement or video-view
campaign is often built not to convert: rate it on its copy and fatigue, and
say that cost could not be judged.

Every row in `dimensions` carries `comparable_on_cost`. Where it is false,
those rows ran under two or more optimization goals and you may NOT say one
hook, offer, audience or format beats another on cost.

Only the CURRENT wording of each ad is stored. An ad edited since it ran
carries today's words against older spend, so do not claim a specific line
CAUSED a result.

`untagged` reports TWO coverages and they are not the same question.
`angle_attribution` is about angles, which this brand has none of, so it reads
0 and always will -- it is NOT evidence that the hook, offer and audience
labels are missing. `creative_labels` is the one that says whether the
`dimensions` block has anything behind it.

THE RATING. One per campaign, every campaign in `campaigns`, exactly once.

  red     Critical: look at this first. It is spending and its ad groups sit
          at the expensive end of their goal, or it spent with no conversions
          under a goal that converts elsewhere in the account, or the ads
          carrying its money show confident fatigue.
  yellow  Some changes worth making. Middle of its goal; or too few
          conversions to read (a handful is noise); or one ad tiring; or copy
          with a weakness you can name and fix; or cost could not be judged.
  green   No change suggested. Its ad groups sit at the cheap end of their
          goal on enough conversions to mean something, nothing is tiring,
          and the copy has no obvious weakness. Leave it alone.

Weigh spend: a dear ad group on a few dollars is a yellow, not a red.

REPLY WITH JSON AND NOTHING ELSE. The first character is [ and the last is ].
No markdown fence, no preamble, no commentary. One object per campaign, in
the order `campaigns` lists them:

[
  {{"campaign": "<the campaign name exactly as given>",
    "rating": "red" | "yellow" | "green",
    "why": "<one plain sentence: the verdict, naming the goal it was judged
             in and the one figure that decided it>",
    "problems": [
      {{"problem": "<what is wrong, in plain words, with the evidence: the
                    ad or ad group it is on and the figures that show it>",
        "fix": "<how to fix it: the concrete change worth trying, written so
                somebody could make it today>",
        "continues": "<the id of the previous problem this is, or null>"}}
    ],
    "resolved": [
      {{"id": "<the id of a previous problem for THIS campaign>",
        "change": "<the id of the entry in changes_since_last_review for THIS
                   campaign that shows it gone>",
        "evidence": "<in plain words, how that change settles the problem>"}}
    ]}}
]

WHAT THE LAST REVIEW SAID. {previous_note}

`previous_problems` maps each campaign to the problems that review raised,
each with an `id`. Account for every one of them, for its own campaign:

  - Still there? Raise it again in `problems` and put its id in `continues`.
    Rewrite it against today's figures; keep the id. Same underlying problem
    means same id, even if you would now word it differently.
  - Gone? List its id under `resolved`, citing in `change` the entry in
    `changes_since_last_review` that shows it -- and only for its own
    campaign. That list was computed by comparing the account then and now,
    and it is the ONLY evidence a resolution may rest on.
    No evidence, no resolution. A wording problem is resolved only by a "wording changed"
    entry for that ad; a spend leader changing does not change what an ad
    says. A campaign with no entries resolves nothing.
  - A problem you would not raise today but cannot show is gone: leave it
    out of both. The page shows it as no longer mentioned, not as fixed.

A problem not in `previous_problems` gets "continues": null.

`resolved` is [] when nothing was resolved, and always [] when there was no
previous review.

`problems`: two or three for red, one to three for yellow, [] for green. One
problem per entry, and every problem carries its own fix -- a problem with no
fix is a complaint, and a fix with no problem is a guess.

A good fix is specific enough to act on today: a first line to cut, in
quotation marks, with the replacement written out in full; an offer to state
plainly, with the wording; an ad that is tiring, with what to refresh it
against. Quote the actual copy you are reacting to, and name ads as they are
named in the account.

Write for somebody reading a card, not a spreadsheet. Money to the cent as it
would be printed ("$97.08", not "97.0816"), rates to two decimals ("1.04%
link CTR"), ranks as "11th of 11". No line breaks and no "\\n" inside a
string.

Every figure you write must appear in the facts above, rounded only for
printing. Do not add, divide or average anything.

Suggest, never instruct. "Worth trying" and "the cheaper ad groups in this
goal tend to", never "pause this", "kill that", "this is your winner" or
"this ad underperforms". The rating says where to look, not what to decide; a
person chooses what to change.

If the numbers do not support a rating either way, give yellow and say why.
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
You are rating advertising campaigns for the person who runs them. You return
JSON and nothing else: the first character of your reply is [ and the last is
]. No preamble, no markdown fence, no trailing commentary. A reply that does
not parse is discarded and the work is wasted.

Every figure you write must appear in the facts you were given. Do not add,
divide or average anything. Never compare cost per result across two
optimization goals.
"""


def _clean(text) -> str:
    """One line of card text. The model quotes ad bodies, and a body with a
    paragraph break arrives as a literal backslash-n more often than not."""
    if not text:
        return ""
    s = str(text).replace("\\n", " ").replace("\n", " ")
    return " ".join(s.split())


def _problems(r: dict) -> list:
    """[{problem, fix}], from the reply's `problems`.

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
# WHAT HAPPENED TO YESTERDAY'S PROBLEMS.
#
# Every run is written from scratch, so without this a fixed problem simply
# stops being mentioned -- and so does a problem the model merely decided not
# to raise this time, which on the same data happens (two campaigns went from
# green to yellow between two runs over an identical window). The reader
# cannot tell those apart, and "it disappeared" is not "it was fixed".
#
# So each problem carries an id and the date it was first raised, the next run
# is handed the last run's problems, and every one of them ends up in exactly
# one of three places: still open (same id, same first-raised date), resolved
# (with the evidence the model cites), or no longer mentioned (neither --
# shown as such, never as fixed).

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
    """campaign name -> [{id, problem, fix, open_since}] from a published file.

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
        items = t.get("problems")
        if items is None:
            items = [{"problem": None, "fix": x} for x in t.get("changes") or []]
        rows = []
        for pi, p in enumerate(items):
            if not isinstance(p, dict) or not (p.get("problem") or p.get("fix")):
                continue
            rows.append({
                "id": p.get("id") or f"p{tag}-{ci}-{pi}",
                # An old file stored fixes only; the fix is then the nearest
                # thing to a statement of the problem there is.
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
        return ("There is no previous review, so `previous_problems` is empty: "
                "every problem is new and `resolved` is always [].")
    note = (f"The last review was published {str(prev.get('published_at'))[:10]}"
            f" against the window ending {prev.get('until')}.")
    if not data_changed:
        note += (" The facts have NOT changed since then, so nothing can have "
                 "been resolved: carry forward every previous problem that "
                 "still applies, and leave `resolved` empty.")
    return note


# ------------------------------------------------------- what changed since --
# A RESOLUTION HAS TO POINT AT SOMETHING THAT ACTUALLY CHANGED.
#
# Left to itself the model resolved three problems on the first run of the
# carry-forward, and two were wrong: one because a different ad had become a
# spend leader (the shared wording was still running), one because it misread
# wording that had not changed at all. The import had re-read no ad copy that
# day, so no wording problem could honestly have been fixed.
#
# So the changes are computed here, by comparing the last review's stored
# facts with today's, and a resolution is only accepted if it cites one of
# them for its own campaign. Each change says who made it:
#
#   you      the account was changed -- wording, an ad paused or switched on,
#            an ad added or gone, the campaign's status
#   numbers  nothing was touched; the figures moved -- a tiring ad no longer
#            tiring, an ad group's rank in its goal, a new spend leader
#
# The page keeps the two apart, because "fixed" and "no longer showing" are
# different claims and only the first one is a result of somebody's work.

#: Cap per campaign. A campaign with forty changed ads gets the first twelve;
#: the model needs enough to match against, not the full audit.
CHANGES_PER_CAMPAIGN = 12


async def snapshot(brand: str, campaign_names: list) -> dict:
    """campaign name -> {ad_key: {ad, copy_hash, status}}, as the account is now.

    Stored in the published file so the NEXT run can see which ads' wording or
    status moved. copy_hash is ads.ad_copy's own md5 over every text field, so
    a changed headline, body or button changes it; a hash of None is an ad the
    import has never read copy for.
    """
    if not campaign_names:
        return {}
    b = await context.brand(brand)
    rows = await fetch_all(
        """
        select c.name as campaign, a.ad_key, a.name as ad,
               a.effective_status as status, cp.copy_hash
          from ads.ad a
          join ads.campaign c   on c.campaign_key = a.campaign_key
          left join ads.ad_copy cp on cp.ad_key = a.ad_key
         where c.brand_id = %s and c.name = any(%s::text[])
        """, (b["id"], list(campaign_names)))
    out: dict = {}
    for r in rows:
        out.setdefault(r["campaign"], {})[str(r["ad_key"])] = {
            "ad": r["ad"], "status": r["status"], "copy_hash": r["copy_hash"]}
    return out


def _rank(g: dict) -> str | None:
    r, n = g.get("cpa_rank_in_goal"), g.get("ad_groups_ranked_in_goal")
    return f"{r} of {n}" if r and n else None


def changes_since(prev: dict | None, campaigns: list, snap: dict) -> dict:
    """campaign name -> [{id, by, what}]: what moved since the last review.

    `prev` is the last published file: its `pack.campaigns` (the facts it was
    written from) and, when it has one, its `snapshot`. A file from before
    snapshots existed is compared on its leading ads' wording only, and the
    change says so.
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

        def add(by: str, what: str) -> None:
            items.append({"id": f"c{len(items) + 1}", "by": by, "what": what})

        if was is None:
            out[name] = [{"id": "c1", "by": "numbers",
                          "what": "not in the last review's window"}]
            continue

        if (was.get("status") or None) != (c.get("status") or None):
            add("you", f"campaign status {was.get('status')} -> {c.get('status')}")

        if prev_snap is not None:
            old, new = prev_snap.get(name) or {}, snap.get(name) or {}
            for k, a in new.items():
                o = old.get(k)
                if o is None:
                    add("you", f'ad added: "{a["ad"]}"')
                    continue
                if o.get("copy_hash") and a.get("copy_hash") \
                        and o["copy_hash"] != a["copy_hash"]:
                    add("you", f'wording changed on "{a["ad"]}"')
                if (o.get("status") == "ACTIVE") != (a.get("status") == "ACTIVE"):
                    add("you", f'"{a["ad"]}" {o.get("status")} -> {a.get("status")}')
            for k, o in old.items():
                if k not in new:
                    add("you", f'ad gone from the campaign: "{o["ad"]}"')
        else:
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
                                   f'on the leading ads only)')

        t_old = {t.get("ad") for t in was.get("tiring_ads") or []}
        t_new = {t.get("ad") for t in c.get("tiring_ads") or []}
        for ad in sorted(t_old - t_new):
            add("numbers", f'"{ad}" no longer tiring')
        for ad in sorted(t_new - t_old):
            add("numbers", f'"{ad}" now tiring')

        g_old = {g.get("ad_group"): g for g in was.get("ad_groups") or []}
        for g in c.get("ad_groups") or []:
            o = g_old.get(g.get("ad_group"))
            if o and _rank(o) != _rank(g):
                add("numbers", f'ad group "{g.get("ad_group")}" rank in '
                               f'{g.get("optimization_goal")}: {_rank(o)} -> {_rank(g)}')

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


def for_the_prompt(previous: dict) -> dict:
    """previous_problems trimmed to what the model needs to match against."""
    return {name: [{"id": p["id"], "problem": (p["problem"] or "")[:300]}
                   for p in rows]
            for name, rows in previous.items()}


def triage_from(rows: list, campaigns: list, previous: dict | None = None,
                data_changed: bool = True, today: date | None = None,
                changes: dict | None = None) -> list:
    """The model's reply, checked against the campaigns it was asked about.

    Returns one entry per campaign in `campaigns`, in that order. A campaign
    the reply named wrongly, rated outside RATINGS, or left out comes back
    with rating None and says so, rather than being dropped: a card that
    silently vanished reads as a campaign nobody ran.

    The campaign's own figures are attached from the PACK, never from the
    reply, so the numbers on a card are the function's and not the model's
    retelling of them.

    THE LAST RUN'S PROBLEMS are accounted for here, not trusted to the reply:

      - `continues` is honoured only for an id the SAME campaign had. Anything
        else is a new problem, whatever the reply claimed.
      - `resolved` is honoured only for such an id, only with evidence, and
        never when `data_changed` is false -- the same facts cannot show a
        problem gone. A refused resolution falls to "no longer mentioned".
      - With `changes` (changes_since), a resolution must also cite the id
        of a change listed for ITS campaign. It is then labelled by who made
        that change -- "you" (the account was edited) or "numbers" (the
        figures moved) -- and a campaign with no changes resolves nothing.
      - A previous problem neither continued nor resolved is kept as
        `dropped`, so the page can say it was not confirmed fixed.
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

        out.append({
            "campaign": name,
            "rating": rating if rating in RATINGS else None,
            "why": (_clean(r.get("why")) or
                    "The reply did not rate this campaign."),
            "problems": problems,
            "resolved": resolved,
            "dropped": dropped,
            "status": c.get("status"),
            "optimization_goals": c.get("optimization_goals"),
            "spend": c.get("spend"),
            "conversions": c.get("conversions"),
            "cpa": c.get("cpa"),
        })
    return out
