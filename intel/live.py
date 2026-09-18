"""`intel live` -- what is running right now, and how far the warehouse has drifted.

THE POINT IS THE DIFF, NOT THE LIST.

A list of live ads on its own is not worth an API call: the warehouse has one,
a few hours older. What only a live call can tell you is where the two
DISAGREE -- an ad launched this morning that no pull has seen, a campaign the
warehouse still shows as active that was paused at lunchtime, a budget that
tripled after the last import and is therefore not reflected in any CPA on any
page.

That is the seam where warehoused analysis quietly goes wrong, and it is
invisible from either side alone.

WHAT THIS IS NOT FOR

Numbers. There is no spend here, no CPA, no conversions -- deliberately. Live
insights are the slow, rate-limited call, and a live number sitting beside a
warehoused one in the same answer is an invitation to compare two things that
were measured differently. Structure is live; performance is warehoused; the
verb says which is which.

Nothing here is written down. A live reading is true for a moment and a table
of them would be a worse version of the import.
"""

from __future__ import annotations

from datetime import datetime, timezone

from db import fetch_all

from . import context, graph


def _budget(value) -> float | None:
    """Meta reports budgets in the account's minor unit -- cents for USD.

    Dividing by 100 here rather than in the caller because a budget that reads
    as 12000 next to a spend that reads as 120.00 is the kind of thing somebody
    acts on before they notice.
    """
    if value in (None, ""):
        return None
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


async def live(slug: str, *, active_only: bool = True) -> dict:
    """Live structure for one brand, diffed against what the warehouse holds."""
    b = await context.brand(slug)
    settled = await context.settled_through(b["id"])

    accounts = await fetch_all(
        "select platform_account_id, label, currency, timezone_name "
        "  from ads.ad_account where brand_id = %s and active "
        " order by platform_account_id",
        (b["id"],),
    )
    if not accounts:
        raise ValueError(
            f"no active ad account is registered for {slug}, so there is "
            f"nothing to look at. Register one in growth-engine: "
            f"python -m meta_ads --add-account act_<id> --brand {slug}")

    if not graph.configured():
        raise graph.NotConfigured(
            "META_ACCESS_TOKEN is not set in this repo's .env, so `live` "
            "cannot ask Meta what is running. Every other verb reads Postgres "
            "and is unaffected. See .env.example.")

    # What the warehouse believes, as of its last structure pull.
    warehoused_ads = {
        r["platform_ad_id"]: r
        for r in await fetch_all(
            "select platform_ad_id, name, effective_status, last_seen_at "
            "  from ads.ad where brand_id = %s", (b["id"],))
    }
    warehoused_groups = {
        r["platform_ad_group_id"]: r
        for r in await fetch_all(
            "select platform_ad_group_id, name, effective_status, daily_budget,"
            "       lifetime_budget, optimization_goal "
            "  from ads.ad_group where brand_id = %s", (b["id"],))
    }

    live_ads: list[dict] = []
    live_groups: list[dict] = []
    async with graph.Graph() as g:
        for account in accounts:
            act = account["platform_account_id"]
            async for row in g.ads(act, active_only=active_only):
                row["_account"] = act
                live_ads.append(row)
            async for row in g.adsets(act, active_only=active_only):
                row["_account"] = act
                live_groups.append(row)

    live_ad_ids = {a["id"] for a in live_ads}

    # --- the three disagreements worth naming ------------------------------

    # 1. Live, and the warehouse has never heard of it. Usually an ad launched
    #    since the last structure pull. Its spend is not in any number yet.
    unknown = [
        {"id": a["id"], "name": a.get("name"),
         "account": a["_account"],
         "created_time": a.get("created_time"),
         "campaign_id": a.get("campaign_id")}
        for a in live_ads if a["id"] not in warehoused_ads
    ]

    # 2. The warehouse thinks it is running and it is not. Every "active ads"
    #    count on every page is overstated by this many until the next pull.
    stale_active = [
        {"id": ad_id, "name": row["name"],
         "warehouse_status": row["effective_status"],
         "last_seen_at": row["last_seen_at"]}
        for ad_id, row in warehoused_ads.items()
        if row["effective_status"] == "ACTIVE" and ad_id not in live_ad_ids
    ] if active_only else []

    # 3. A budget or an optimisation goal that moved since the last import.
    #    The goal one matters most: change it and the ad group's CPA stops
    #    being comparable to its own history, which no chart will mention.
    changed = []
    for grp in live_groups:
        known = warehoused_groups.get(grp["id"])
        if not known:
            continue
        for field, live_value, stored in (
            ("daily_budget", _budget(grp.get("daily_budget")),
             float(known["daily_budget"]) if known["daily_budget"] else None),
            ("lifetime_budget", _budget(grp.get("lifetime_budget")),
             float(known["lifetime_budget"]) if known["lifetime_budget"] else None),
            ("optimization_goal", grp.get("optimization_goal"),
             known["optimization_goal"]),
            ("effective_status", grp.get("effective_status"),
             known["effective_status"]),
        ):
            if live_value != stored:
                changed.append({
                    "ad_group_id": grp["id"], "name": grp.get("name"),
                    "field": field, "warehouse": stored, "live": live_value,
                })

    agrees = not (unknown or stale_active or changed)
    return {
        "verb": "live",
        "brand": b["slug"],
        "read_at": datetime.now(timezone.utc),
        "source": "Meta Graph API, live. Structure only -- no spend, no CPA, "
                  "no conversions. Those are warehoused and come from the "
                  "other verbs.",
        "accounts": [a["platform_account_id"] for a in accounts],
        "active_only": active_only,

        "live_ads": len(live_ads),
        "live_ad_groups": len(live_groups),
        "running": [
            {"id": a["id"], "name": a.get("name"),
             "effective_status": a.get("effective_status"),
             "account": a["_account"],
             "in_warehouse": a["id"] in warehoused_ads}
            for a in live_ads
        ],
        "budgets": [
            {"id": g["id"], "name": g.get("name"),
             "daily_budget": _budget(g.get("daily_budget")),
             "lifetime_budget": _budget(g.get("lifetime_budget")),
             "optimization_goal": g.get("optimization_goal")}
            for g in live_groups
        ],

        "warehouse_settled_through": settled,
        "warehouse_agrees": agrees,
        "not_in_warehouse": unknown,
        "warehouse_thinks_active_but_is_not": stale_active,
        "changed_since_import": changed,
        "caveat": None if agrees else (
            f"The warehouse and the ad account disagree in "
            f"{len(unknown) + len(stale_active) + len(changed)} place(s). Any "
            f"warehoused answer about these ads is describing the account as "
            f"it was at the last pull, not as it is now. Say so, or run the "
            f"pull first: python -m meta_ads --pull --brand {slug} in "
            f"growth-engine."),
    }
