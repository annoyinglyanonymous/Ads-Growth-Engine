"""status -- is anything here worth reading?

Run this first. Every other verb will happily answer a question about data that
stopped arriving three weeks ago, because a stale number and a fresh number are
the same shape.

THE FAILURE THIS VERB EXISTS FOR: a Meta System User token has been revoked or
has expired. The symptom is not an error anybody sees -- growth-engine's pulls
start failing on their own schedule, the last successful import stays on screen,
and every dashboard keeps showing the numbers it showed yesterday. config.py:263
makes the same point about personal tokens expiring at 60 days: the failure is
silence.

It also reports WHICH DATABASE CREDENTIAL is in use. Running as the owner while
believing you are read-only is worse than having no second role, because the
belief is what makes the write unreviewed.
"""

from __future__ import annotations

from datetime import date, timedelta

from config import settings
from db import fetch_all, fetch_one

from . import context, graph


def _credential() -> dict:
    """Which role this process is actually connected as, by inspection.

    Parsed from the url rather than asked of the database, deliberately: this
    has to be answerable when the connection is the thing that is broken.

    THE USERNAME IS NOT THE ROLE NAME. Supabase's Supavisor pooler wants the
    tenant carried on the username -- `ads_reader.ogfrtrvbmyrazvmibcmi` -- and
    hands the connection to Postgres as plain `ads_reader`. Comparing the whole
    string reported "they may be running with write privileges" on a correctly
    configured install, on every single run. A check that cries wolf is worse
    than no check at all: the day it is finally right, it reads identically to
    the hundred days it was wrong, and by then nobody is looking.
    """
    raw = (settings.ads_database_url_ro or "").strip()
    configured = bool(raw)
    user = None
    if configured and "://" in raw:
        rest = raw.split("://", 1)[1]
        if "@" in rest:
            user = rest.split("@", 1)[0].split(":", 1)[0]
    # Everything left of the first dot. A Postgres role name cannot contain one
    # unless it was created quoted, and none of ours were.
    role = user.split(".", 1)[0] if user else None
    return {
        "read_only_url_configured": configured,
        "connected_as": user,
        "role": role,
        "expected": "ads_reader",
        "ok": role == "ads_reader",
        "note": None if role == "ads_reader" else
                "The read verbs are not connected as ads_reader. They may be "
                "running with write privileges. See .env.example and "
                "migrations/006_ads_roles.sql.",
    }


async def status(slug: str, stale_after_days: int = 2) -> dict:
    b = await context.brand(slug)
    settled = await context.settled_through(b["id"])

    accounts = await fetch_all(
        """
        select a.platform_account_id, a.label, a.currency, a.timezone_name,
               a.active,
               (select max(p.finished_at) from ads.pull p
                 where p.platform_account_id = a.platform_account_id
                   and p.kind = 'insights' and p.status = 'ok') as last_insights_ok,
               (select max(p.until) from ads.pull p
                 where p.platform_account_id = a.platform_account_id
                   and p.kind = 'insights' and p.status = 'ok') as insights_through,
               (select max(p.finished_at) from ads.pull p
                 where p.platform_account_id = a.platform_account_id
                   and p.kind = 'structure' and p.status = 'ok') as last_structure_ok
          from ads.ad_account a
         where a.brand_id = %s
         order by a.active desc, a.platform_account_id
        """,
        (b["id"],),
    )

    failures = await fetch_all(
        """
        select run_id, platform_account_id, kind, status, error,
               started_at, finished_at
          from ads.pull
         where brand_id = %s and status <> 'ok'
         order by started_at desc
         limit 10
        """,
        (b["id"],),
    )

    coverage = await fetch_one(
        """
        select min(day) as first_day, max(day) as last_day,
               count(*)::bigint as ad_days,
               count(distinct ad_key)::bigint as ads,
               sum(spend) as spend
          from ads.fact_ad_day
         where brand_id = %s
        """,
        (b["id"],),
    )

    conversions_defined = await fetch_one(
        "select count(*)::int as n from ads.conversion_definition where brand_id = %s",
        (b["id"],))

    # An insights row whose parent ad is missing cannot exist -- the FK forbids
    # it -- so this counts the opposite and more useful thing: ads that spent
    # nothing at all, which is normal, and ads with no insights rows in a window
    # they should have, which is not.
    untagged = await fetch_one(
        """
        select count(*)::int as n
          from ads.ad d
         where d.brand_id = %s
           and not exists (select 1 from ads.ad_facet fa where fa.ad_key = d.ad_key)
        """,
        (b["id"],))

    unmapped = await fetch_one(
        "select count(*)::int as n from ads.angle_candidate where brand_id = %s",
        (b["id"],))

    today = date.today()
    problems = []
    if not accounts:
        problems.append(
            f"No ad account is registered for {slug}. Run, here: "
            f"python -m meta_ads --add-account act_XXXX --brand {slug}")
    for a in accounts:
        if not a["active"]:
            continue
        if a["last_insights_ok"] is None:
            problems.append(
                f"{a['platform_account_id']} has never completed an insights "
                f"pull. Run, here: python -m meta_ads --pull "
                f"--brand {slug}")
        elif (today - a["last_insights_ok"].date()).days > stale_after_days:
            problems.append(
                f"{a['platform_account_id']} last imported insights "
                f"{a['last_insights_ok'].date()}, more than {stale_after_days} "
                f"day(s) ago. A revoked or expired token looks exactly like "
                f"this and reports nothing.")
        if a["timezone_name"] is None:
            problems.append(
                f"{a['platform_account_id']} has no timezone_name, so "
                f"ads.settled_through falls back to UTC for it and the window "
                f"edges are wrong by up to a day. It is filled by a structure "
                f"pull.")
    if failures:
        problems.append(
            f"{len(failures)} import run(s) did not succeed; the most recent "
            f"was {failures[0]['kind']} on {failures[0]['started_at']}.")
    if (conversions_defined or {}).get("n", 0) == 0:
        problems.append(
            f"No conversion is defined for {slug}, so every conversion count "
            f"and every CPA in this schema is zero or null. Add rows to "
            f"ads.conversion_definition -- see migration 002.")

    cred = _credential()
    # The url says which role was ASKED for; current_user says which one was
    # granted. They diverge when a pooler rewrites the login or a role is
    # renamed under a connection string nobody updated -- and that divergence
    # is exactly the "running as owner while believing you are read-only" case
    # this docstring opens with. Free to ask here, because the connection is
    # already open. Guarded, because a credential report that raises is the one
    # thing this verb must never do.
    try:
        cred["confirmed_as"] = (await fetch_one("select current_user as u"))["u"]
    except Exception:
        cred["confirmed_as"] = None
    if cred["confirmed_as"] and cred["confirmed_as"] != cred["expected"]:
        cred["ok"] = False
        cred["note"] = (
            f"The database reports this connection as {cred['confirmed_as']}, "
            f"not ads_reader. Whatever the url claims, this is the truth.")
    if not cred["ok"]:
        problems.append(cred["note"])

    # Not a problem when unset -- thirteen of the fourteen read verbs never
    # touch it. Reported either way so that a token rotation done in
    # growth-engine and forgotten here is visible as a fact rather than
    # discovered the next time somebody asks what is running.
    live_ready = graph.configured()

    return {
        "verb": "status", "brand": b["slug"], "brand_id": str(b["id"]),
        "credential": cred,
        "live_reads": {
            "configured": live_ready,
            "note": None if live_ready else
                    "META_ACCESS_TOKEN is not set here, so `intel live` cannot "
                    "report what is running right now. Every other verb reads "
                    "Postgres and is unaffected.",
        },
        "settled_through": settled,
        "accounts": accounts,
        "recent_failures": failures,
        "coverage": coverage,
        "conversion_definitions": (conversions_defined or {}).get("n", 0),
        "untagged_ads": (untagged or {}).get("n", 0),
        "unmapped_angle_handles": (unmapped or {}).get("n", 0),
        "healthy": not problems,
        "problems": problems,
    }
