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

#: The rate every goal here is ranked on. One metric, because the ads inside a
#: single optimization goal ARE comparable on it -- that is what makes the
#: grouping worth doing.
RANK_RATE = "cpa"


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

    keys = [str(r["entity_key"]) for r in chosen if r.get("entity_key")]
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

    # ads.facet_performance, not a group-by written here. It is the function
    # that already knows a dimension is not rankable when its rows ran under
    # more than one optimization goal, and `comparable_on_cost` is that answer.
    # Passing it through unchanged is how the model learns what it may not say.
    formats = await fetch_all(
        """
        select value, ads_run, spend, conversions, rates,
               optimization_goals, optimization_goal_count,
               comparable_on_cost, spend_sufficient, rank_within_goal
          from ads.facet_performance(%s::uuid, %s, %s, 'format')
        """, (d["brand_id"], d["since"], d["until"]))

    untagged = await fetch_one(
        """
        select total_spend, tagged_spend, untagged_ads, untagged_spend,
               stale_tag_ads, stale_tag_spend
          from ads.untagged_spend(%s::uuid, %s, %s)
        """, (d["brand_id"], d["since"], d["until"]))

    fatigue = await metrics.fatigue(brand, days, until, 100.0, False)

    return {
        "verb": "creative_pack",
        "brand": d["brand"], "since": d["since"], "until": d["until"],
        "days": d["days"], "settled_through": d["settled_through"],
        "unsettled_days": d["unsettled_days"],
        "goals": goals,
        "formats": formats,
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


SUGGESTIONS_PROMPT = """\
You are a direct-response copywriter looking at one advertiser's live Meta ads.
Say what is worth writing next. Your reader owns the business and writes the
ads; they want ideas they can act on this week.

Brand: {brand}
Window: {since} to {until} ({days} days). Settled through {settled}; the last
{unsettled} day(s) can still move.

WHAT IS RUNNING, grouped by optimization goal, with the copy and what it cost:

{facts}

WHAT YOU MAY AND MAY NOT COMPARE

Ads inside ONE optimization goal are comparable on cost per result. Ads under
different goals are not, and saying "this beats that" across two goals is the
single worst mistake available here -- LEAD_GENERATION and OFFSITE_CONVERSIONS
are not measuring the same event. Name the goal you are talking about.

Where a goal has `rankable_on_cost: false`, nothing under it converted, so
there is no cost ranking to read. Its ads are there for their copy only.

The `formats` block carries `comparable_on_cost`. Where that is false, the
formats ran under different goals and you may NOT say one format beats
another -- say they have not been compared on equal terms, and move on.

Only the CURRENT wording of each ad is stored. An ad edited since it ran carries
today's words against older spend, so do not claim a specific line CAUSED a
result. Say what the winning ads have in common and what is worth trying.

WRITE IT LIKE THIS

Six to nine plain sentences, one or two paragraphs. No headings, no bullets, no
markdown, no preamble and no closing offer.

Quote the actual copy you are reacting to, in quotation marks, so the reader can
find the ad. Name ads as they are named in the account.

Lead with what the cheapest ads in a goal have in common as WRITING -- the
first line, what it promises, who it addresses, what it asks for -- not their
spend. Then the dearest, and what they do differently. Then two or three
concrete things worth writing next: a hook to try, an offer to state more
plainly, a first line to cut. Be specific enough to write from.

Suggest, never instruct. "Worth trying" and "the cheapest ones tend to", never
"pause this", "kill that", "this is your winner" or "this ad underperforms".
Nothing here decides anything; a person chooses what to make.

If the copy does not support a conclusion, say that rather than reaching.
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
