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

import functools
import json
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

import ask
import chat
from config import NotConfigured
from db import fetch_all, fetch_one
from intel import ad_readings as ad_readings_mod
from intel import angles as angles_mod
from intel import brief as brief_mod
from intel import context
from intel import creative as creative_mod
from intel import experiments as exp_mod
from intel import propose as propose_mod
from intel import metrics
from intel import outcomes as outcomes_mod
from intel.context import UnknownBrand

ROOT = Path(__file__).resolve().parent

#: The React build (frontend/, `npm run build`). Every page URL serves its
#: index.html and the page fetches its data from the /api twin of that URL.
APP_INDEX = ROOT / "static" / "app" / "index.html"

router = APIRouter(include_in_schema=False)


# ---------------------------------------------------------------------------
# Pages.
#
# A page URL serves the React shell; the shell asks /api/<the same path> for
# its data. ONE URL PER PAGE, as before: /creative?window=14 is still a link
# somebody can send, and the query string goes to the API untouched.
# ---------------------------------------------------------------------------

PAGES = ("/", "/brief", "/why", "/creative", "/angles", "/experiments",
         "/suggestions", "/ask", "/ad/{ad_key}")


async def _shell(ad_key: str | None = None):
    if not APP_INDEX.is_file():
        # Said on the page rather than as a 404: a missing build is a state of
        # the checkout, and the fix is one command.
        return HTMLResponse(
            "<h1>The dashboard has not been built</h1>"
            "<p>Run <code>npm install</code> and <code>npm run build</code> in "
            "<code>frontend/</code>, then reload. "
            "(<code>npm run dev</code> serves it with hot reload on "
            "127.0.0.1:5173.)</p>", status_code=503)
    return FileResponse(APP_INDEX, headers={"Cache-Control": "no-cache"})


for _path in PAGES:
    router.add_api_route(_path, _shell, methods=["GET"],
                         response_class=HTMLResponse)


def _page(nav: str, data: dict, **extra):
    """A page's data: what the template used to be handed, as JSON.

    The keys are the old template context's, so a page reads `d`, `settled`
    and `brand` where it always did. jsonable_encoder because every verb
    returns dates and Decimals.
    """
    return JSONResponse(jsonable_encoder({
        "nav": nav,
        "brand": data.get("brand"),
        "settled": data.get("settled_through"),
        "caveat": data.get("caveat"),
        "d": data,
        "until": None,
        "today": date.today().isoformat(),
        **extra,
    }))


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
    """The dashboard's window end. Defined once, in intel/context.py.

    It used to be defined here, and then scripts/suggest.py needed the same
    window to write prose that matched the tables underneath it -- at which
    point a copy in the web layer was a copy that would drift. The reasoning
    lives with the definition.
    """
    return await context.latest_day(brand_slug)


async def _end_and_brand(brand_slug: str, until: str | None = None):
    """The window end, with the brand and its settled edge looked up beside it.

    Called inside context.one_request(), so the brand and settled-edge answers
    are memoised and every verb the page runs next finds them already there.
    Concurrent rather than one after the other: each is a round trip to
    Supabase, and none needs the others' answer.
    """
    async def warm():
        b = await context.brand(brand_slug)
        await context.settled_through(b["id"])

    end, _ = await context.gather_in_order(
        _day_or_latest(until, brand_slug), warm())
    return end


async def _day_or_latest(until: str | None, brand_slug: str) -> date | None:
    return _day(until) or await _latest_day(brand_slug)


def _fail(nav: str, exc: Exception):
    """One sentence on the page, never a traceback.

    A NotConfigured here almost always means ADS_DATABASE_URL_RO is unset,
    which is a state of the install rather than a fault -- so it renders as a
    page that explains itself, the way growth-engine's /ads renders for a
    missing Meta token. 200, with `error` set: the page draws ErrorPage.
    """
    resp = JSONResponse({
        "nav": nav, "brand": None, "settled": None, "caveat": None,
        "error": str(exc).strip().splitlines()[0] if str(exc) else repr(exc),
        "detail": str(exc),
    }, status_code=200)
    # Never cached: the fix for an error is usually outside this process, and
    # the next load should see it.
    resp.cacheable = False
    return resp


# ---------------------------------------------------------------------------
# THE PAGE CACHE.
#
# A page's data is ten to twenty round trips to Supabase, ~85ms each, and it
# changes only when something writes: an import, a published report or brief,
# a ledger update. So each /api answer is kept, keyed on the page and its
# query string, and served again while the DATA STAMP it was built under still
# holds. The stamp costs one round trip -- ads.pull's row count and latest
# start/finish, the newest file under suggestions/, briefs/ and outcomes/, and
# today's date (the settled edge and "today" move with it).
#
# What the stamp cannot see is a person's own SQL -- signing an angle, setting
# started_on -- so an answer is also dropped after CACHE_TTL_S. Refresh clears
# everything. Per process and in memory: a restart starts cold, which is the
# point of keeping it this simple.
# ---------------------------------------------------------------------------

CACHE_TTL_S = 300
CACHE_MAX = 64
DATA_DIRS = (ROOT / "suggestions", ROOT / "briefs", ROOT / "outcomes")

#: (handler, query) -> (stamp, monotonic time stored, JSON body)
_cache: dict[tuple, tuple[tuple, float, bytes]] = {}


async def _data_stamp() -> tuple:
    row = await fetch_one(
        "select count(*) as runs, "
        "       max(coalesce(finished_at, started_at)) as last_at "
        "  from ads.pull") or {}
    newest = 0.0
    for d in DATA_DIRS:
        try:
            # The directory's own mtime moves when a file is added or removed;
            # each file's moves when it is rewritten in place.
            newest = max([newest, d.stat().st_mtime,
                          *(f.stat().st_mtime for f in d.iterdir())])
        except OSError:
            continue
    return (date.today(), row.get("runs"), row.get("last_at"), newest)


def _cached(handler):
    """Serve `handler`'s JSON from _cache while the data stamp holds.

    functools.wraps keeps the handler's signature, which is what FastAPI reads
    the query parameters from. A stamp that cannot be read means no cache for
    that request, never a stale answer.
    """
    @functools.wraps(handler)
    async def wrapper(**kwargs):
        try:
            stamp = await _data_stamp()
        except Exception:  # noqa: BLE001 -- the handler reports its own failure
            return await handler(**kwargs)
        key = (handler.__name__, tuple(sorted(kwargs.items())))
        hit = _cache.get(key)
        if (hit and hit[0] == stamp
                and time.monotonic() - hit[1] < CACHE_TTL_S):
            return Response(hit[2], media_type="application/json")
        resp = await handler(**kwargs)
        if resp.status_code == 200 and getattr(resp, "cacheable", True):
            if len(_cache) >= CACHE_MAX:
                _cache.pop(next(iter(_cache)))
            _cache[key] = (stamp, time.monotonic(), resp.body)
        return resp
    return wrapper


@router.get("/api/")
@_cached
async def overview(brand: str = "renegade", days: int = 28,
                   level: str = "ad", until: str | None = None):
    """The window ends at the settled edge unless `until` says otherwise.

    `?until=YYYY-MM-DD` is how you see today. It is not the default, and the
    difference matters most for the metric people most want fresh: today's
    spend row is a few hours old, so it reads as a collapse rather than as a
    day in progress. context.window honours an explicit until and returns
    unsettled_days with it, so the page can show the number AND say what it is.
    """
    try:
        with context.one_request():
            end = await _end_and_brand(brand, until)
            # The window is known before any metric is read, so the six reads
            # below run concurrently instead of in a row.
            f = await metrics._frame(brand, days, end)
            win = (f["brand_id"], f["since"], f["until"])
            d, cmp_, agg, brand_cmp, series, first = await context.gather_in_order(
                metrics.overview(brand, days, end, level, limit=200),
                metrics.compare(brand, days, end, level, limit=500),
                # Brand totals for the tiles. One row, straight from the
                # function -- no summing here and no rate recomputed. Until
                # 011 there was no brand level, so this page summed campaign
                # rows and then divided, which is the trap ads.rate exists to
                # prevent and which 003's first line forbids.
                fetch_one(
                    "select spend, conversions, impressions, clicks, link_clicks, rates, "
                    "       currencies "
                    "  from ads.window_metrics(%s, %s, %s, 'brand')", win),
                fetch_one(
                    "select current_m, prior_m, delta, pct "
                    "  from ads.compare(%s, %s, %s, 'brand')", win),
                fetch_all(
                    "select day, sum(spend) as spend, sum(conversions)::bigint as conversions "
                    "  from ads.fact_ad_day "
                    " where brand_id = %s and day between %s and %s "
                    " group by day order by day", win),
                # The first day this brand has any data, so the page can
                # withhold a spend or conversions delta measured against a
                # prior window the import had barely reached -- the proxy the
                # brief uses, and for the same reason: a "+3,191%" that is a
                # fact about the importer is worse than no figure.
                fetch_one(
                    "select min(day) as first_day from ads.fact_ad_day where brand_id = %s",
                    (f["brand_id"],)))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("overview", exc)

    agg = agg or {}
    if not agg:
        # No brand row means no ad-days in the window at all. Every field is
        # None rather than 0, because "nothing imported" and "spent nothing"
        # are different answers and the page must not render the first as the
        # second -- money(None) is "--" where money(0) is "$0.00". `rates` is
        # {} rather than None so the template can still reach rates.cpa and get
        # the same "--".
        agg = {k: None for k in ("spend", "conversions", "impressions",
                                 "clicks", "link_clicks", "currencies")}
        # The eight keys ads.rate returns, all null -- not {}. The page reads
        # rates.cpa and rates.link_ctr directly, and a null renders "--" where
        # a missing key would be a different code path. Mirroring the
        # function's real shape is what keeps the no-data page on the same
        # path as every other page.
        agg["rates"] = {k: None for k in
                        ("ctr", "link_ctr", "cpm", "cpc", "cost_per_link_click",
                         "cpa", "conversion_rate", "lp_view_rate")}
    agg["mixed_currency"] = (agg.get("currencies") or 1) > 1

    # The brand delta now comes from ads.compare at brand level, which derives
    # its own contiguous prior window. A summed per-ad CPA delta is not the
    # brand CPA delta -- CPA is not additive -- which is why this tile carried
    # no movement before there was a function that returned one.
    brand_cmp = brand_cmp or {}
    deltas = _brand_delta(brand_cmp, {r["entity_key"]: r for r in cmp_["rows"]})

    first = first or {}
    return _page("overview", d,
                 agg=agg, deltas=deltas, level=level, days=days,
                 until=until, first_day=first.get("first_day"),
                 prior_since=d["since"] - timedelta(days=days),
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


@router.get("/api/brief")
@_cached
async def brief_page(brand: str = "renegade",
                     days: int | None = None, product: str | None = None):
    """The PUBLISHED weekly brief, rendered whole. Live only when asked.

    This page used to build the brief live, on a 28-day window, and that was
    right while the brief was only numbers. It stopped being right when the
    brief started writing its own prose: scripts/brief.py publishes a 7-day
    brief with a model's summary after every import, and a live 28-day pack
    with that summary on top would print a paragraph about one window above
    tiles about another. The user chose the published brief, whole, so the
    facts, the findings and the summary share one window and one date -- and
    the page shows the same document the archive holds.

    `?days=` (or `?product=`) still builds one live, which is the only way to
    see a window the pipeline did not publish. Overview remains the live view;
    this one is the dated read. With nothing published yet, the page falls
    back to a live 7-day brief and says so.
    """
    live = days is not None or product is not None
    try:
        d = None if live else brief_mod.latest(brand)
        if d is None:
            live = True
            d = await brief_mod.brief(brand, days or 7, None, product)
            d.setdefault("summary", None)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("brief", exc)
    return _page("brief", d, days=d["days"],
                 product=product, live=live,
                 prior_since=d["since"] - timedelta(days=d["days"]))


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


@router.get("/api/ask")
async def ask_page(brand: str = "renegade"):
    """The Ask page's frame: the verb list. The answers are not here.

    The page asks /ask.json and /ask/prose.json itself, the way the overview's
    box does, so a question costs its model call once, from the browser, and
    the routed JSON arrives as the receipt underneath the prose -- one of the
    two accounts of the same numbers is the check on the other.
    """
    return JSONResponse({
        "nav": "overview", "brand": brand, "settled": None, "caveat": None,
        "d": {"brand": brand}, "verbs": ask.VERBS,
        "until": None, "today": date.today().isoformat(),
    })


@router.get("/ask/prose.json")
async def ask_prose(q: str = "", brand: str = "renegade"):
    """The chatbot, for the widget on the overview.

    Separate from /ask.json rather than folded into it, because the two have
    opposite costs. /ask.json routes a regex and returns in about a second for
    nothing; this spawns a Claude Code session, takes the better part of two
    minutes and costs real money. A box that invites typing should not spend
    that on every keystroke without the page saying so, and the ask box fires
    this one second and renders it as it arrives.

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
    """What the ask box calls so the answer lands in place, not on a new page.

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


@router.get("/api/why")
@_cached
async def why(brand: str = "renegade", days: int = 7):
    try:
        with context.one_request():
            d = await metrics.why(brand, days, await _end_and_brand(brand),
                                  limit=40)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("why", exc)
    return _page("why", d, days=days)


@router.get("/api/creative")
@_cached
async def creative(brand: str = "renegade", window: int = 7,
                   min_spend: float = 100.0, unconfident: int = 0):
    try:
        with context.one_request():
            d = await metrics.fatigue(brand, window, await _end_and_brand(brand),
                                      min_spend, bool(unconfident))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("creative", exc)

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
    return _page("creative", d,
                 window=window, min_spend=min_spend,
                 unconfident=unconfident, spark=spark)


@router.get("/api/angles")
@_cached
async def angles(brand: str = "renegade", days: int = 90,
                 product: str | None = None):
    try:
        # Four independent reads, run concurrently and sharing one brand and
        # settled-edge lookup. gather_in_order raises what the sequential
        # awaits raised: the earliest call's failure.
        with context.one_request():
            d, cand, q, labels = await context.gather_in_order(
                angles_mod.coverage(brand, product, days, None),
                angles_mod.candidates(brand),
                angles_mod.queue(brand, days, None, limit=25),
                # Both coverages, so the page can say the copy IS labelled
                # while the angle is not -- the two numbers mean different
                # things.
                angles_mod.label_coverage(brand, days, None))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("angles", exc)
    tested = d["tested"] + d["under_spent"]
    return _page("angles", d, days=days,
                 candidates=cand["rows"], queue=q, labels=labels,
                 bars=[(r["angle_name"], r["cpa"]) for r in tested])


@router.get("/api/experiments")
@_cached
async def experiments(brand: str = "renegade",
                      days: int = 14):
    """The log, and underneath it the tests the report recommends.

    One flow with /suggestions: the report recommends a test and names its
    tag, the person launches the new ad with utm_content=<tag>, the outcomes
    ledger sees the launch and scripts/suggest.py files the experiment as a
    proposal. A proposal whose launch was seen shows the date and the exact
    statement that records started_on -- run by a person, never from here.

    `intel propose`'s ideas render only when the ledger is empty or
    unreadable: until the report has recommended anything, they are the
    only next step this page has.
    """
    try:
        d = await exp_mod.experiments(brand)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("experiments", exc)
    try:
        ledger = outcomes_mod.load(brand)
    except (OSError, ValueError):
        ledger = None
    has_ledger = bool((ledger or {}).get("entries"))
    rows = exp_mod.with_launches(d["rows"], ledger)
    d = {**d, "rows": rows}
    ideas = None
    try:
        if has_ledger:
            b = await context.brand(brand)
            settled = await context.settled_through(b["id"])
            caveat = context.caveat(0, settled)
        else:
            # The settled edge comes from `ideas` here. experiments() is not
            # windowed and returns no settled_through; without one the rail
            # renders "No active ad account for this brand", which is false.
            ideas = await propose_mod.propose(brand, days)
            settled, caveat = ideas.get("settled_through"), ideas.get("caveat")
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("experiments", exc)
    return _page("experiments", d,
                 ideas=ideas, days=days, settled=settled, caveat=caveat,
                 has_ledger=has_ledger,
                 recommended=exp_mod.recommended_tests(ledger),
                 unfiled=exp_mod.launched_unfiled(ledger),
                 launches=[r for r in rows if r.get("launch")])


@router.get("/api/ad/{ad_key}")
@_cached
async def ad_detail(ad_key: str, brand: str = "renegade",
                    days: int = 14):
    # The panel is best-effort and the page is not. Every sentence in it comes
    # from a verb that can fail on its own -- a brand with no active account
    # has no settled edge, and ads.fatigue returns nothing for an ad that ran
    # in only one of the two windows. None of that is a reason to lose the copy,
    # the chart and the daily table, which are what somebody came here for.
    # The settled edge only, not the readings: the panel they used to feed is
    # gone, and building a fact pack on every page load to render nothing is a
    # round trip nobody asked for. /ad/<key>/analyse.json builds it on demand.
    #
    # THE RULES' READINGS COME BACK, and they lead the page. They are free --
    # rules over a fact pack, no model -- so this is the "analysis first" the
    # dashboard now opens every page with; the model's longer read stays behind
    # the one button left on the site. Best-effort like the settled edge above:
    # a fatigue or bridge verb failing is no reason to lose the copy, the
    # charts and the daily table. `totals` is the ad's own window, from the
    # same function every other tile reads.
    #
    # The ad, the settled edge and the readings run concurrently; the last
    # two swallow their own failures.
    async def settled_edge():
        try:
            return await context.settled_through(
                (await context.brand(brand))["id"])
        except (UnknownBrand, NotConfigured, ValueError):
            return None

    async def readings():
        try:
            reading = await ad_readings_mod.ad_reading(brand, ad_key, days)
            f = await metrics._frame(brand, days, None)
            totals = await fetch_one(
                "select spend, conversions, impressions, rates "
                "  from ads.window_metrics(%s::uuid, %s, %s, 'ad') "
                " where entity_key = %s::uuid",
                (f["brand_id"], f["since"], f["until"], ad_key))
            return reading, totals
        except Exception:  # noqa: BLE001 -- the page must render without the panel
            return None, None

    try:
        with context.one_request():
            d, settled, (reading, totals) = await context.gather_in_order(
                metrics.ad(ad_key, days=90), settled_edge(), readings())
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("overview", exc)

    daily = list(reversed(d["daily"]))
    return _page("overview", {"brand": brand},
                 ad=d, days=days, settled=settled, reading=reading, totals=totals,
                 spend_series=[(r["day"], r["spend"]) for r in daily],
                 conv_series=[(r["day"], r["conversions"]) for r in daily])


@router.get("/api/suggestions")
@_cached
async def suggestions(brand: str = "renegade",
                      days: int = 28):
    """The latest published suggestion, and the evidence it was written from.

    THERE IS NO BUTTON, and that is the change this page exists to make. A
    suggestion somebody has to remember to ask for is a suggestion nobody asks
    for -- the same failure the scheduled pull exists to fix, one layer up.
    `scripts/suggest.py` publishes one after every import, so the answer is
    already here when the page is opened.

    The pack is still built live, because it IS the evidence: a stored
    suggestion read against stale numbers is worse than no suggestion. The
    prose is dated and the numbers are current, and the page says so when they
    have drifted apart.
    """
    try:
        # until=None: creative_pack defaults to the latest day with data, the
        # same end scripts/suggest.py wrote its prose against.
        pack = await creative_mod.creative_pack(brand, days, None)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail("suggestions", exc)

    published = creative_mod.latest_suggestion(brand)
    if published:
        # Attached here rather than stored, because it is a fact about NOW and
        # the file is a fact about then. Writing it into the document would
        # freeze an age that only ever gets older.
        published["age_hours"] = creative_mod.age_hours(published)
    return _page("suggestions", pack, days=days,
                 settled=pack.get("settled_through"), published=published)


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

    # The pack, the budget ladder and the prompt all moved to
    # intel/brief.summarise(), because scripts/brief.py publishes this same
    # document on a schedule and could not reach a prompt that lived in a
    # request handler. Two copies of a prompt drift, and the one that drifts
    # silently is the one nobody opens.
    try:
        out = await brief_mod.summarise(d, brand)
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
    # A person asking for fresh numbers should never be shown kept ones. The
    # stamp would catch the pull's ads.pull row anyway; this covers the
    # minutes before it is written.
    _cache.clear()
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
