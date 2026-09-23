"""The dashboard. Read-only, like everything else here.

EVERY PAGE CALLS THE SAME FUNCTION THE AGENT CALLS.

/ renders intel.metrics.overview(); `python -m intel overview` prints it. There
is one definition of "overview" and it is not in this file. That is the whole
reason this app exists in the repo that owns the metrics rather than beside the
importer: if the page had its own query, the card and the agent's sentence could
disagree, and the reader would have no way to tell which was wrong.

There is ONE button and it does not pull. /refresh SPAWNS the scheduled pull as
a separate process and returns at once; the page then polls /refresh/status,
which reads `ads.pull` on the read pool like every other query here. This
process never holds the importer's credential and never runs a pull inside a
request -- see the block above the endpoints for why both halves of that matter.

Nothing here approves anything: an angle becomes active and an experiment gets
a conclusion in growth-engine's UI, where a person is signed in and attributable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import ask
import charts
import chat
from config import NotConfigured
from db import fetch_all, fetch_one
from intel import ad_readings as ad_readings_mod
from intel import angles as angles_mod
from intel import brief as brief_mod
from intel import context
from intel import experiments as exp_mod
from intel import propose as propose_mod
from intel import metrics
from intel.context import UnknownBrand

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

# Formatting helpers, registered as globals because macro bodies do not see
# page scope. Same device growth-engine uses for field_labels/capped.
templates.env.globals.update(
    money=charts.money, num=charts.num, pct=charts.pct, fmt=charts.fmt,
    line_chart=charts.line_chart, bar_chart=charts.bar_chart,
    sparkline=charts.sparkline, column_chart=charts.column_chart,
    split_bar=charts.split_bar, effect_bars=charts.effect_bars,
)

router = APIRouter(include_in_schema=False)


def _page(request: Request, template: str, nav: str, data: dict, **extra):
    return templates.TemplateResponse(request, template, {
        "nav": nav,
        "brand": data.get("brand"),
        "settled": data.get("settled_through"),
        "caveat": data.get("caveat"),
        "d": data,
        # Defaults for every page, because base.html reads both and Jinja is
        # StrictUndefined -- a page that does not take `until` would otherwise
        # 500 on the banner rather than simply not showing it. `extra` wins.
        "until": None,
        "today": date.today().isoformat(),
        **extra,
    })


def _day(value: str | None) -> date | None:
    """A query-string date, or None. A bad one is ignored rather than fatal:
    a mistyped URL should fall back to the honest default, not 500."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


async def _latest_day(brand_slug: str) -> date | None:
    """The most recent day this brand actually has numbers for.

    THE PAGE ENDS HERE, NOT AT THE SETTLED EDGE. `context.window` defaults to
    `settled_through` -- today in the account's timezone minus Meta's 3-day
    restatement horizon -- and that remains the default for `python -m intel`,
    where an agent is expected to report `unsettled_days` and a partial day
    quietly read as a decline is the false alarm this system exists to prevent.

    The dashboard is a different reader with a different question. Somebody who
    opens it and presses Refresh is asking what happened yesterday, and being
    shown a window that stops three days back reads as the refresh having done
    nothing. The numbers for those days are imported and real; what moves is
    attributed conversions, and the rail's "Settled through" card says so on
    every screen.

    The LATEST DAY WITH DATA, not today. Today is routinely a day that has not
    started in the account's timezone -- on 2026-09-22 at 05:35 UTC the account
    was still on the 21st -- so ending the window at the calendar date appends
    an empty day and every chart falls off a cliff at the right-hand edge. That
    is the same "reads as a collapse in spend" failure, arrived at from the
    other direction.

    None when the brand has no facts at all, which puts the window back on the
    old default rather than inventing a date.
    """
    row = await fetch_one(
        """
        select max(f.day) as day
          from ads.fact_ad_day f
          join ads.brand b on b.id = f.brand_id
         where b.slug = %s
        """,
        (brand_slug,))
    return (row or {}).get("day")


def _fail(request: Request, nav: str, exc: Exception):
    """One sentence on the page, never a traceback.

    A NotConfigured here almost always means ADS_DATABASE_URL_RO is unset,
    which is a state of the install rather than a fault -- so it renders as a
    page that explains itself, the way growth-engine's /ads renders for a
    missing Meta token.
    """
    return templates.TemplateResponse(request, "error.html", {
        "nav": nav, "brand": None, "settled": None, "caveat": None,
        "error": str(exc).strip().splitlines()[0] if str(exc) else repr(exc),
        "detail": str(exc),
    }, status_code=200)


@router.get("/", response_class=HTMLResponse)
async def overview(request: Request, brand: str = "renegade", days: int = 28,
                   level: str = "ad", until: str | None = None):
    """The window ends at the settled edge unless `until` says otherwise.

    `?until=YYYY-MM-DD` is how you see today. It is not the default, and the
    difference matters most for the metric people most want fresh: today's
    spend row is a few hours old, so it reads as a collapse rather than as a
    day in progress. context.window honours an explicit until and returns
    unsettled_days with it, so the page can show the number AND say what it is.
    """
    try:
        end = _day(until) or await _latest_day(brand)
        d = await metrics.overview(brand, days, end, level, limit=200)
        cmp_ = await metrics.compare(brand, days, end, level, limit=500)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "overview", exc)

    # Brand totals for the tiles. One row, straight from the function -- no
    # summing here and no rate recomputed. Until 011 there was no brand level,
    # so this page summed campaign rows and then divided, which is the trap
    # ads.rate exists to prevent and which 003's first line forbids.
    agg = await fetch_one(
        "select spend, conversions, impressions, clicks, link_clicks, rates, "
        "       currencies "
        "  from ads.window_metrics(%s, %s, %s, 'brand')",
        (d["brand_id"], d["since"], d["until"]),
    ) or {}
    if not agg:
        # No brand row means no ad-days in the window at all. Every field is
        # None rather than 0, because "nothing imported" and "spent nothing"
        # are different answers and the page must not render the first as the
        # second -- money(None) is "--" where money(0) is "$0.00". `rates` is
        # {} rather than None so the template can still reach rates.cpa and get
        # the same "--".
        agg = {k: None for k in ("spend", "conversions", "impressions",
                                 "clicks", "link_clicks", "currencies")}
        # The eight keys ads.rate returns, all null -- not {}. The templates
        # reach rates.cpa and rates.link_ctr directly, and Jinja is configured
        # StrictUndefined, so an empty dict raises where a null renders "--".
        # Mirroring the function's real shape is what keeps the no-data page on
        # the same code path as every other page.
        agg["rates"] = {k: None for k in
                        ("ctr", "link_ctr", "cpm", "cpc", "cost_per_link_click",
                         "cpa", "conversion_rate", "lp_view_rate")}
    agg["mixed_currency"] = (agg.get("currencies") or 1) > 1

    # The brand delta now comes from ads.compare at brand level, which derives
    # its own contiguous prior window. A summed per-ad CPA delta is not the
    # brand CPA delta -- CPA is not additive -- which is why this tile carried
    # no movement before there was a function that returned one.
    brand_cmp = await fetch_one(
        "select current_m, prior_m, delta, pct "
        "  from ads.compare(%s, %s, %s, 'brand')",
        (d["brand_id"], d["since"], d["until"]),
    ) or {}
    deltas = _brand_delta(brand_cmp, {r["entity_key"]: r for r in cmp_["rows"]})

    series = await fetch_all(
        "select day, sum(spend) as spend, sum(conversions)::bigint as conversions "
        "  from ads.fact_ad_day "
        " where brand_id = %s and day between %s and %s "
        " group by day order by day",
        (d["brand_id"], d["since"], d["until"]),
    )
    return _page(request, "overview.html", "overview", d,
                 agg=agg, deltas=deltas, level=level, days=days,
                 until=until,
                 spend_series=[(r["day"], r["spend"]) for r in series],
                 conv_series=[(r["day"], r["conversions"]) for r in series])


def _brand_delta(brand_cmp: dict, per_ad: dict) -> dict:
    """Window-over-window movement, read from ads.compare at brand level.

    Every figure here is lifted out of the function's own `delta`/`pct` jsonb --
    including CPA, which this page could not show before 011 added a brand
    level. Summing per-ad CPA deltas would have produced a confident, wrong
    figure, because CPA is not additive; that is why the tile was blank rather
    than approximate.

    per_ad is still read, for two counts that are only meaningful per entity:
    an ad that launched or stopped this window. Those are counted, never summed.
    """
    delta = brand_cmp.get("delta") or {}
    pct = brand_cmp.get("pct") or {}
    return {"spend": delta.get("spend"),
            "conversions": delta.get("conversions"),
            "cpa": delta.get("cpa"),
            "link_ctr": delta.get("link_ctr"),
            "spend_pct": pct.get("spend"),
            "conversions_pct": pct.get("conversions"),
            "cpa_pct": pct.get("cpa"),
            "appeared": sum(1 for r in per_ad.values() if r["appeared"]),
            "disappeared": sum(1 for r in per_ad.values() if r["disappeared"])}


@router.get("/brief", response_class=HTMLResponse)
async def brief_page(request: Request, brand: str = "renegade", days: int = 28,
                     product: str | None = None):
    """The recurring read, rendered live.

    Live rather than from the archive in briefs/, so the page and
    `python -m intel brief` are the same function on the same window -- ui.py's
    rule. scripts/brief.py writes the dated copy, and that copy exists because
    a live page silently rewrites its own past opinion every time Meta
    restates; the two are different jobs and both are wanted.
    """
    try:
        d = await brief_mod.brief(brand, days, None, product)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "brief", exc)
    return _page(request, "brief.html", "brief", d, days=days, product=product,
                 prior_since=d["since"] - timedelta(days=days))


#: WHERE THE PRIOR WINDOW STARTS, AND WHY THIS IS A PROXY
#
# The brief withholds a window-over-window delta when the window before this
# one is not comparably covered -- on this account the import began
# 2026-08-22, so a 28-day comparison sets 28 days of data against roughly one
# and reports "spend +3,191.52%", which is a fact about the importer and not
# about the advertising.
#
# Deciding that needs to know how much of the prior window HAS data, and
# `ads.compare` does not say: it returns current_m, prior_m, delta, pct,
# appeared and disappeared, and nothing about the prior window's bounds or its
# coverage. THAT IS A MISSING VERB -- ads.compare wants prior_since,
# prior_until and prior_days_with_data -- and until it returns them the page
# can only compare this start date against trust.coverage.first_day, which is
# right for a brand with one account and wrong for a brand whose accounts
# began importing on different days.
#
# Subtracting `days` from a window bound is date arithmetic on the window, not
# a metric: no rate, delta or share is being computed here, and the comparison
# the template makes with it is between two dates.


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request, q: str = "", brand: str = "renegade"):
    """A question, answered twice: in prose, and in the verb's own JSON.

    The prose comes from a Claude Code session (chat.py) that runs the read
    verbs itself. The JSON below it is the router's answer to the same
    question, and it is not redundant -- it is how a figure in the prose gets
    checked against the verb that produced it. Two accounts of the same
    numbers would normally be the thing to avoid; here one of them is the
    receipt for the other.

    The prose half is slow (tens of seconds) and costs real money per
    question, so it is attempted only when a question was asked, and its
    failure never costs the routed JSON.
    """
    answer, failed = None, None
    reply, reply_error = None, None

    if q.strip():
        try:
            out = await chat.answer(q, brand)
            if out.get("ok"):
                reply = out
            else:
                reply_error = out.get("error")
        except chat.ChatUnavailable as exc:
            reply_error = str(exc)
        except Exception as exc:  # pragma: no cover - defensive
            # A failure in the prose half must not take the routed answer down
            # with it: the JSON is the part that is always correct.
            reply_error = f"{type(exc).__name__}: {str(exc)[:200]}"

    if q.strip():
        try:
            # jsonable_encoder before the template, not tojson inside it: every
            # verb returns dates and Decimals, and Jinja's tojson raises on
            # both. The JSON route already encodes; this makes the two paths
            # render the same bytes instead of one of them 500ing.
            answer = jsonable_encoder(await ask.answer(q, brand))
        except ask.Unroutable as exc:
            # Only surfaced when the prose half did NOT answer. The router
            # matches a regex against a fixed verb list and fails on most
            # naturally-worded questions -- "which is the best performing ad
            # today" matches nothing -- while the session answers them fine.
            # Showing both put a red error directly above a correct answer.
            failed = None if reply else str(exc)
        except (UnknownBrand, NotConfigured, ValueError) as exc:
            failed = str(exc).strip().splitlines()[0]
    return templates.TemplateResponse(request, "ask.html", {
        "nav": "overview", "brand": brand, "settled": None, "caveat": None,
        "d": {"brand": brand}, "q": q, "answer": answer, "failed": failed,
        "reply": reply, "reply_error": reply_error,
        "verbs": ask.VERBS,
        "until": None, "today": date.today().isoformat(),
    })


@router.get("/ask/prose.json")
async def ask_prose(q: str = "", brand: str = "renegade"):
    """The chatbot, for the widget on the overview.

    Separate from /ask.json rather than folded into it, because the two have
    opposite costs. /ask.json routes a regex and returns in about a second for
    nothing; this spawns a Claude Code session, takes the better part of two
    minutes and costs real money. A box that invites typing should not spend
    that on every keystroke without the page saying so, and app.js fires this
    one second and renders it as it arrives.

    Errors come back 200 with an `error` key, like /ask.json: a failure here
    must not blank the routed answer the widget already drew.
    """
    try:
        return JSONResponse(jsonable_encoder(await chat.answer(q, brand)))
    except chat.ChatUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc)})
    except Exception as exc:  # pragma: no cover - defensive
        return JSONResponse({"ok": False,
                             "error": f"{type(exc).__name__}: {str(exc)[:200]}"})


@router.get("/ask.json")
async def ask_json(q: str = "", brand: str = "renegade"):
    """What app.js calls so the answer lands in place instead of a new page.

    Errors come back 200 with an `error` key rather than as a status code: a
    question that matched no verb is a fact about the question, not a fault in
    the server, and the box has to be able to say so in its own log.
    """
    try:
        return JSONResponse(jsonable_encoder(await ask.answer(q, brand)))
    except ask.Unroutable as exc:
        return JSONResponse({"error": str(exc), "verbs": sorted(ask.VERBS)})
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return JSONResponse({"error": str(exc).strip().splitlines()[0]})


@router.get("/why", response_class=HTMLResponse)
async def why(request: Request, brand: str = "renegade", days: int = 7):
    try:
        d = await metrics.why(brand, days, await _latest_day(brand), limit=40)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "why", exc)
    return _page(request, "why.html", "why", d, days=days)


@router.get("/creative", response_class=HTMLResponse)
async def creative(request: Request, brand: str = "renegade", window: int = 7,
                   min_spend: float = 100.0, unconfident: int = 0):
    try:
        d = await metrics.fatigue(brand, window, await _latest_day(brand),
                                  min_spend, bool(unconfident))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "creative", exc)

    # A CTR shape per row. Read in one query rather than one per ad: a fatigue
    # table is routinely forty rows and forty round trips through a pooler is a
    # page that takes four seconds for no reason.
    keys = [r["ad_key"] for r in d["rows"]]
    spark: dict = {}
    if keys:
        # ads.ad_daily_rates, not an inline division. This query used to carry
        # `100.0 * link_clicks / impressions` -- a second definition of link_ctr
        # in the one file whose docstring promises every page calls the same
        # function the agent calls. The sparkline and the Link CTR beside it are
        # now the same number by construction.
        since = d["rows"][0]["recent_since"] if d["rows"] else date.today()
        until = d["rows"][0]["recent_until"] if d["rows"] else date.today()
        for row in await fetch_all(
            "select ad_key, day, rates "
            "  from ads.ad_daily_rates(%s::uuid[], %s, %s)",
            (keys, since, until),
        ):
            spark.setdefault(row["ad_key"], []).append(
                (row["rates"] or {}).get("link_ctr"))
    return _page(request, "creative.html", "creative", d,
                 window=window, min_spend=min_spend,
                 unconfident=unconfident, spark=spark)


@router.get("/angles", response_class=HTMLResponse)
async def angles(request: Request, brand: str = "renegade", days: int = 90,
                 product: str | None = None):
    try:
        d = await angles_mod.coverage(brand, product, days, None)
        cand = await angles_mod.candidates(brand)
        q = await angles_mod.queue(brand, days, None, limit=25)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "angles", exc)
    tested = d["tested"] + d["under_spent"]
    return _page(request, "angles.html", "angles", d, days=days,
                 candidates=cand["rows"], queue=q,
                 bars=[(r["angle_name"], r["cpa"]) for r in tested])


@router.get("/experiments", response_class=HTMLResponse)
async def experiments(request: Request, brand: str = "renegade",
                      days: int = 14):
    """The log, and underneath it the ideas nobody has filed yet.

    Proposals share this page rather than getting their own because they are
    the same subject at two stages, and because an empty log with no next
    step reads as a dead feature. They are clearly below the log and clearly
    unfiled: filing one is `intel record`, run by a person.
    """
    try:
        d = await exp_mod.experiments(brand)
        ideas = await propose_mod.propose(brand, days)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "experiments", exc)
    # The settled edge comes from `ideas`, not from `d`. experiments() is not
    # windowed and returns no settled_through, so _page() read None and the
    # rail rendered "No active ad account for this brand" -- which is false,
    # contradicts every other page, and is the one sentence on this site that
    # must never be wrong by accident.
    return _page(request, "experiments.html", "experiments", d,
                 ideas=ideas, days=days,
                 settled=ideas.get("settled_through"),
                 caveat=ideas.get("caveat"))


@router.get("/ad/{ad_key}", response_class=HTMLResponse)
async def ad_detail(request: Request, ad_key: str, brand: str = "renegade",
                    days: int = 14):
    try:
        d = await metrics.ad(ad_key, days=90)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "overview", exc)

    # The panel is best-effort and the page is not. Every sentence in it comes
    # from a verb that can fail on its own -- a brand with no active account
    # has no settled edge, and ads.fatigue returns nothing for an ad that ran
    # in only one of the two windows. None of that is a reason to lose the copy,
    # the chart and the daily table, which are what somebody came here for.
    # The settled edge only, not the readings: the panel they used to feed is
    # gone, and building a fact pack on every page load to render nothing is a
    # round trip nobody asked for. /ad/<key>/analyse.json builds it on demand.
    settled = None
    try:
        settled = await context.settled_through((await context.brand(brand))["id"])
    except (UnknownBrand, NotConfigured, ValueError):
        pass

    daily = list(reversed(d["daily"]))
    return _page(request, "ad.html", "overview", {"brand": brand},
                 ad=d, days=days, settled=settled,
                 spend_series=[(r["day"], r["spend"]) for r in daily],
                 conv_series=[(r["day"], r["conversions"]) for r in daily])


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


async def _creative_pack(brand: str, days: int, until=None) -> dict:
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


@router.get("/suggestions", response_class=HTMLResponse)
async def suggestions(request: Request, brand: str = "renegade",
                      days: int = 28):
    """The inputs, on load. The suggestion itself costs a model call and waits
    for the button."""
    try:
        pack = await _creative_pack(brand, days, await _latest_day(brand))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "suggestions", exc)
    return _page(request, "suggestions.html", "suggestions", pack, days=days,
                 settled=pack.get("settled_through"))


@router.post("/suggestions.json")
async def suggestions_json(brand: str = "renegade", days: int = 28):
    """Creative suggestions, written by a Claude Code session.

    POST for the reason /refresh and /brief/summary.json are: it costs a model
    call and the better part of a minute, and a GET that spends is one browser
    prefetch away from spending on its own.
    """
    try:
        pack = await _creative_pack(brand, days, await _latest_day(brand))
    except UnknownBrand as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except (NotConfigured, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    facts = {k: pack[k] for k in ("goals", "formats", "untagged", "tiring")}
    question = None
    for keep in (8, 3, 0):
        question = SUGGESTIONS_PROMPT.format(
            brand=pack["brand"], since=pack["since"], until=pack["until"],
            days=pack["days"], settled=pack["settled_through"],
            unsettled=pack["unsettled_days"],
            facts=json.dumps(jsonable_encoder(_compact(facts, keep)),
                             indent=1, ensure_ascii=False))
        if len(question) <= PROMPT_BUDGET:
            break

    try:
        out = await chat.answer(question, brand)
    except chat.ChatUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse(jsonable_encoder(out))


#: What the session is asked. The facts come WITH the question rather than
#: being left for it to fetch, for two reasons. It is faster -- a verb round
#: trip per number turns a slow endpoint into a very slow one -- and it is
#: tighter: the numbers in front of it are the ones ads.fatigue, ads.cpa_bridge
#: and ads.ad produced, so the figures it quotes are sourced whether or not it
#: chooses to go and check them. chat.SYSTEM still forbids it inventing one.
ANALYSE_PROMPT = """\
Analyse this Meta ad and say what is worth knowing about it.

Ad: {name}
Brand: {brand}
ad_key: {ad_key}

Here are its facts and its copy, already read from the verbs. Use these
numbers. You may run a verb to check something or to answer a question these
do not cover, but you do not need to re-read what is already here.

{facts}

What the deterministic rules already noticed, for your reference -- do not
just repeat these back:

{readings}

Say what stands out about this ad, what it probably means, and what would be
worth trying. Four or five sentences. Be specific to this ad and its copy
rather than general about advertising. If the numbers do not support a
conclusion, say that instead of reaching for one.
"""


#: The question reaches the session as ONE COMMAND-LINE ARGUMENT, and Windows
#: caps a command line at 32,767 characters. chat.SYSTEM and the tool lists
#: take a few thousand of those, so the question itself stops here. The brief's
#: pack is ~52,000 characters as produced, almost all of it two row lists, so
#: it cannot be sent whole and a spawn that fails on length would read as "the
#: session returned nothing".
PROMPT_BUDGET = 24000


def _compact(node, keep: int = 8):
    """Shorten every list past `keep` rows, and say how many were dropped.

    Structure-agnostic on purpose: the brief's shape is intel/brief.py's to
    change, and a compaction that named sections would break the day a section
    was renamed. Rows are already ordered by whatever matters (spend, effect,
    score) so the head is the part worth reading; the note at the end tells the
    session the rest exists and which way to get it.
    """
    if isinstance(node, dict):
        return {k: _compact(v, keep) for k, v in node.items()}
    if isinstance(node, list):
        if keep <= 0:
            return f"[{len(node)} rows omitted; run the verb for them]"
        head = [_compact(x, keep) for x in node[:keep]]
        if len(node) > keep:
            head.append(f"... {len(node) - keep} more rows not shown; run the "
                        f"verb for all of them")
        return head
    return node


BRIEF_SUMMARY_PROMPT = """\
You are explaining this brand's Meta ads for the window below to the person who
owns the business. They will not read the tables. They want to know what
happened, why, and what is worth doing about it -- in their words, not the
dashboard's.

Brand: {brand}
Window: {since} to {until} ({days} days). Settled through {settled}; the last
{unsettled} day(s) can still move as Meta restates conversions.

THE FACTS, already read from the verbs (row lists shortened -- run a verb only
if you need something that is not here):

{facts}

WHAT THE RULES ALREADY FOUND -- build on these, do not repeat them back:

{readings}

WRITE IT LIKE THIS

Eight to ten plain sentences in one or two paragraphs. No headings, no bullet
points, no markdown, no opening line about what you are about to do and no
closing offer.

Lead with the result: what was spent, what it produced, what each result cost,
and whether each of those is up or down against the window before -- if the
facts carry the prior window, say the direction in words ("up from", "down
from") and quote both figures; if they do not, say this is one window with
nothing to compare it to.

Then say why the cost per result moved, naming the one or two ads that drove
most of it by their names as given. Put rate effect and mix effect into plain
words every time: the rate effect is the ads themselves getting cheaper or
dearer, the mix effect is money shifting toward cheaper or dearer ads. Do not
use either term without its plain phrase beside it.

Then what is tiring: the one or two ads worth refreshing first, and what the
symptom is in ordinary language -- "costs more per thousand views than a
fortnight ago", "fewer of the people who see it click" -- never the field
name. Say whether the spend behind that reading is enough to trust.

If parts of the brief are empty because nothing has been tagged or filed, say
so once, in one sentence, and name what filing them would unlock. Then move on.

Close with what is worth looking at next -- two or three concrete things,
taken from the readings and from waiting_on_you. Offer them as things to look
at, not as decisions: nothing here approves, pauses or concludes anything.

VOCABULARY

Say "cost per lead" (or "cost per conversion" if the goal is not leads) and put
"CPA" in brackets the first time only. Say "cost per thousand views" for CPM,
"the share of people who clicked" for link CTR, "how often the same person saw
it in a day" for daily frequency. Refer to ads by their names. Quote figures
exactly as they appear in the facts; do not round, total, average, or work out
a percentage that is not already there. If conversions in the last few days
are part of a decline, say in one clause that those days are not final.
"""


@router.post("/brief/summary.json")
async def brief_summary(brand: str = "renegade", days: int = 28,
                        product: str | None = None):
    """The brief, explained in sentences by a Claude Code session.

    Same shape and the same reasons as /ad/<key>/analyse.json: POST because it
    spends a model call; facts handed over rather than fetched because that is
    faster and every figure is then one a verb produced; readings included so
    the session builds on what the rules found rather than rediscovering it.
    """
    try:
        d = await brief_mod.brief(brand, days, None, product)
    except UnknownBrand as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except (NotConfigured, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    # gaps is the standing roadmap, not this window; waiting_on_you is small
    # and is the answer to "so what do I do", so it rides along with facts.
    payload = {"facts": d.get("facts"), "waiting_on_you": d.get("waiting_on_you"),
               "degraded": d.get("degraded")}
    readings = [r["says"] for r in d.get("readings") or []]

    question = None
    for keep in (8, 3, 0):
        question = BRIEF_SUMMARY_PROMPT.format(
            brand=d.get("brand") or brand, since=d.get("since"),
            until=d.get("until"), days=d.get("days") or days,
            settled=d.get("settled_through"), unsettled=d.get("unsettled_days"),
            facts=json.dumps(jsonable_encoder(_compact(payload, keep)),
                             indent=1, ensure_ascii=False),
            readings=json.dumps(readings, indent=1, ensure_ascii=False))
        if len(question) <= PROMPT_BUDGET:
            break

    try:
        out = await chat.answer(question, brand)
    except chat.ChatUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse(jsonable_encoder(out))


@router.post("/ad/{ad_key}/analyse.json")
async def analyse_ad(ad_key: str, brand: str = "renegade", days: int = 14):
    """A few sentences about one ad, written by a Claude Code session.

    POST rather than GET, and for the same reason /refresh is: this costs a
    model call and the better part of a minute. A GET that spends is one
    browser prefetch away from spending on its own.
    """
    try:
        reading = await ad_readings_mod.ad_reading(
            brand, ad_key, days, await _latest_day(brand))
        full = await metrics.ad(ad_key, days=days)
    except UnknownBrand as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except (NotConfigured, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    facts = dict(reading["facts"])
    facts["copy"] = full.get("copy")

    question = ANALYSE_PROMPT.format(
        name=(full.get("ad") or {}).get("name") or ad_key,
        brand=brand, ad_key=ad_key,
        facts=json.dumps(jsonable_encoder(facts), indent=2, ensure_ascii=False),
        readings=json.dumps(
            [r["says"] for r in reading["readings"]], indent=2,
            ensure_ascii=False) or "[]")

    try:
        out = await chat.answer(question, brand)
    except chat.ChatUnavailable as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    return JSONResponse(jsonable_encoder(out))


# ---------------------------------------------------------------------------
# Refresh.
#
# The only endpoint here that causes a write, and it still does not write: it
# starts scripts/sync.py -- the same wrapper Task Scheduler runs -- as its own
# process and returns immediately.
#
# SPAWNED, NOT IMPORTED, and that distinction is the whole design. Importing
# meta_ads (or scripts.sync, which imports it at module scope) would open
# db_meta -- the postgres pool that can write public.* -- inside the web
# process. tests/test_read_only.py allows exactly {meta_ads, scripts/sync.py,
# tests/conftest.py} to do that, and the page is deliberately not on the list:
# db.py offering no cursor is what makes "this app cannot mutate" a fact a test
# can check rather than a habit somebody keeps. A subprocess keeps that
# credential in a process that exits when the pull is done.
#
# DETACHED, so the run outlives the tab. A pull awaited inside the request is
# cancelled when the client disconnects -- and start_pull has already written a
# `running` row by then, so finish_pull never runs and nothing ever closes it.
# ads.pull holds two such rows already (222 and 250) from interrupted CLI runs.
# A button that minted one per closed tab would make them the normal case, and
# every one of them reads as a pull that is still going.
#
# INSIGHTS ONLY. The two phases use different Graph edges and fail separately.
# Structure has never once completed on act_153704749222533 -- its /ads edge
# dies five to nine minutes into pagination -- while insights over the default
# ~4-day window is a single call. The button is wired to the half that works.
# The scheduled task still runs both, because structure is not meant to stay
# broken.
# ---------------------------------------------------------------------------

SYNC_SCRIPT = ROOT / "scripts" / "sync.py"

#: scripts/sync.bat exists for Task Scheduler, which has no PATH and no venv to
#: inherit. This process has both -- sys.executable IS the venv interpreter --
#: so it calls sync.py directly. That also sidesteps running a .bat through
#: Popen on Windows, which needs cmd.exe in the middle and re-parses arguments
#: on the way past.
SYNC_PYTHON = sys.executable

#: Duplicated from scripts/sync.py rather than imported, because importing that
#: module is exactly the thing the block above refuses to do. Two copies of a
#: filename and a timedelta is much the cheaper of the two mistakes -- and
#: tests/test_refresh.py asserts the copies agree.
SYNC_LOCK = ROOT / ".sync.lock"
SYNC_LOCK_STALE_AFTER = timedelta(hours=2)


def _lock_age() -> timedelta | None:
    """How long the sync lock has been held, or None if it is not held."""
    try:
        held = datetime.fromtimestamp(SYNC_LOCK.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    return datetime.now(timezone.utc) - held


def _spawn_detached(cmd: list[str]) -> None:
    """Start `cmd` in its own process group and stop caring about it.

    stdout and stderr go to the void on purpose: sync.py writes logs/sync.log
    itself, and a pipe nobody reads fills its buffer and blocks the child
    somewhere in the middle of a pull.
    """
    extra: dict = {}
    if sys.platform == "win32":
        extra["creationflags"] = (subprocess.DETACHED_PROCESS
                                  | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        extra["start_new_session"] = True
    subprocess.Popen(
        cmd, cwd=str(ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **extra,
    )


@router.post("/refresh")
async def refresh(brand: str = "renegade"):
    """Start an insights pull. 202 if it started, 409 if one is already going."""
    try:
        b = await context.brand(brand)
    except UnknownBrand as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except NotConfigured as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    age = _lock_age()
    if age is not None and age < SYNC_LOCK_STALE_AFTER:
        # sync.py refuses the overlap too, and exits 2. Checked here as well so
        # the page can say so at once, rather than reporting a start for a run
        # that is about to decline to begin. Belt and braces, deliberately: the
        # thing being protected is a shared rate limit.
        minutes = int(age.total_seconds() // 60)
        return JSONResponse({
            "ok": False, "running": True,
            "error": f"A sync has been running for {minutes} minute(s). "
                     f"Nothing started.",
        }, status_code=409)

    if not SYNC_SCRIPT.is_file():
        return JSONResponse({
            "ok": False,
            "error": f"Cannot find the sync script at {SYNC_SCRIPT}.",
        }, status_code=500)

    try:
        _spawn_detached([SYNC_PYTHON, str(SYNC_SCRIPT),
                         "--brand", b["slug"], "--phase", "insights"])
    except OSError as exc:
        return JSONResponse({
            "ok": False,
            "error": f"Could not start the pull: {type(exc).__name__}: {exc}",
        }, status_code=500)

    return JSONResponse({
        "ok": True, "started": True, "brand": b["slug"], "phase": "insights",
    }, status_code=202)


@router.get("/refresh/status")
async def refresh_status(brand: str = "renegade"):
    """Import freshness, read from ads.pull. Cheap enough to poll.

    Note what this reports and `settled` does not. The settled edge is a fact
    about META -- it restates attributed conversions for about three days. This
    is a fact about US: when the importer last succeeded and how far it got.
    The page has always shown the first and never the second, which is why
    "is this current?" had no answer short of a terminal.
    """
    try:
        b = await context.brand(brand)
        settled = await context.settled_through(b["id"])
    except UnknownBrand as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except NotConfigured as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=503)

    # PER ACCOUNT, because that is how the import already succeeds and fails.
    #
    # The first version of this read took the single most recent ads.pull row
    # for the brand and let it colour the card. That is wrong in the exact way
    # meta_ads/pull.py exists to prevent: run_pull catches per account so that
    # "a revoked assignment on Agency Height cannot cost Renegade its import",
    # and then the page threw that isolation away. act_153704749222533 imported
    # cleanly, act_9105140029692 failed a permission check a second later, and
    # the card reported the import as failed -- naming an account whose numbers
    # nobody is looking at, about a pull that worked.
    #
    # A brand is behind when EVERY account is behind. One account failing is a
    # different sentence, and it needs the account's name in it.
    rows = await fetch_all(
        """
        select a.platform_account_id, a.label,
               p.run_id, p.status, p.error, p.started_at, p.finished_at
          from ads.ad_account a
          left join lateral (
              select run_id, status, error, started_at, finished_at
                from ads.pull
               where platform_account_id = a.platform_account_id
                 and kind = 'insights'
               order by started_at desc
               limit 1
          ) p on true
         where a.brand_id = %s and a.active
         order by a.platform_account_id
        """,
        (b["id"],))

    watermark = await fetch_one(
        """
        select max(finished_at) as last_insights_ok,
               max(until)       as insights_through
          from ads.pull
         where brand_id = %s and kind = 'insights' and status = 'ok'
        """,
        (b["id"],))

    now = datetime.now(timezone.utc)
    last_ok = (watermark or {}).get("last_insights_ok")
    age_hours = round((now - last_ok).total_seconds() / 3600, 1) if last_ok else None

    # 'running' and 'stalled' are the same row and a different fact, and the
    # button depends on telling them apart. Rows 222 and 250 have said
    # `running` since the interrupted pulls that opened them, and a page that
    # read status alone would disable its own refresh button forever on the
    # strength of a run that ended days ago. Past the stale-lock window it is
    # not a pull in progress, it is a row nobody closed.
    accounts = []
    any_running = any_stalled = False
    n_ok = n_failed = 0
    for r in rows:
        status = r["status"]
        if status == "running":
            started = r["started_at"]
            if started and (now - started) < SYNC_LOCK_STALE_AFTER:
                any_running = True
            else:
                any_stalled = True
                status = "stalled"
        elif status == "ok":
            n_ok += 1
        elif status == "failed":
            n_failed += 1
        accounts.append({
            "platform_account_id": r["platform_account_id"],
            "label": r["label"],
            "status": status,
            "error": r["error"],
            "run_id": r["run_id"],
            "started_at": r["started_at"],
            "finished_at": r["finished_at"],
        })

    held = _lock_age()
    return JSONResponse(jsonable_encoder({
        "ok": True,
        "brand": b["slug"],
        "running": any_running or (held is not None
                                   and held < SYNC_LOCK_STALE_AFTER),
        "stalled": any_stalled,
        "last_insights_ok": last_ok,
        "insights_through": (watermark or {}).get("insights_through"),
        "age_hours": age_hours,
        "settled_through": settled,
        "accounts": accounts,
        "accounts_ok": n_ok,
        "accounts_failed": n_failed,
    }))
