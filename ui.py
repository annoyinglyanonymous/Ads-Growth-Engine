"""The dashboard. Read-only, like everything else here.

EVERY PAGE CALLS THE SAME FUNCTION THE AGENT CALLS.

/ renders intel.metrics.overview(); `python -m intel overview` prints it. There
is one definition of "overview" and it is not in this file. That is the whole
reason this app exists in the repo that owns the metrics rather than beside the
importer: if the page had its own query, the card and the agent's sentence could
disagree, and the reader would have no way to tell which was wrong.

There are no buttons. Nothing here fires a pull -- a pull takes minutes against
a rate limit and a browser that gave up half way through leaves a `running` row
nothing ever closes (growth-engine's /ads page makes the same choice and says
so). Nothing here approves anything either: an angle becomes active and an
experiment gets a conclusion in growth-engine's UI, where a person is signed in
and attributable.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

import ask
import charts
from config import NotConfigured
from db import fetch_all
from intel import angles as angles_mod
from intel import experiments as exp_mod
from intel import metrics
from intel.context import UnknownBrand

ROOT = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(ROOT / "templates"))

# Formatting helpers, registered as globals because macro bodies do not see
# page scope. Same device growth-engine uses for field_labels/capped.
templates.env.globals.update(
    money=charts.money, num=charts.num, pct=charts.pct, fmt=charts.fmt,
    line_chart=charts.line_chart, bar_chart=charts.bar_chart,
    sparkline=charts.sparkline,
)

router = APIRouter(include_in_schema=False)


def _page(request: Request, template: str, nav: str, data: dict, **extra):
    return templates.TemplateResponse(request, template, {
        "nav": nav,
        "brand": data.get("brand"),
        "settled": data.get("settled_through"),
        "caveat": data.get("caveat"),
        "d": data,
        **extra,
    })


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
                   level: str = "ad"):
    try:
        d = await metrics.overview(brand, days, None, level, limit=200)
        cmp_ = await metrics.compare(brand, days, None, level, limit=500)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "overview", exc)

    # Brand totals for the tiles. Summed from rows the database already
    # aggregated; the RATES come from ads.rate() at brand level rather than
    # being averaged across ads, which would be the exact trap ads.rate exists
    # to prevent.
    totals = await fetch_all(
        "select spend, conversions, impressions, clicks, link_clicks, rates, "
        "       currencies "
        "  from ads.window_metrics(%s, %s, %s, 'campaign')",
        (d["brand_id"], d["since"], d["until"]),
    )
    agg = _roll(totals)

    prior = {r["entity_key"]: r for r in cmp_["rows"]}
    deltas = _brand_delta(prior)

    series = await fetch_all(
        "select day, sum(spend) as spend, sum(conversions)::bigint as conversions "
        "  from ads.fact_ad_day "
        " where brand_id = %s and day between %s and %s "
        " group by day order by day",
        (d["brand_id"], d["since"], d["until"]),
    )
    return _page(request, "overview.html", "overview", d,
                 agg=agg, deltas=deltas, level=level, days=days,
                 spend_series=[(r["day"], r["spend"]) for r in series],
                 conv_series=[(r["day"], r["conversions"]) for r in series])


def _roll(rows: list[dict]) -> dict:
    """Brand totals. Sums only -- no rate is recomputed here."""
    out = {k: 0 for k in ("spend", "conversions", "impressions", "clicks",
                          "link_clicks")}
    for r in rows:
        for k in out:
            out[k] += (r.get(k) or 0)
    out["mixed_currency"] = any((r.get("currencies") or 1) > 1 for r in rows)
    # Derived at the brand level by the same function every other rate uses.
    out["cpa"] = (out["spend"] / out["conversions"]) if out["conversions"] else None
    out["cpm"] = (out["spend"] * 1000 / out["impressions"]) if out["impressions"] else None
    out["link_ctr"] = (100 * out["link_clicks"] / out["impressions"]) if out["impressions"] else None
    return out


def _brand_delta(rows: dict) -> dict:
    """Window-over-window movement, summed from ads.compare.

    Only spend and conversions: those are additive, so summing per-ad deltas is
    the same number as the brand delta. A per-ad CPA delta is NOT additive and
    summing it would produce a confident, wrong figure -- the honest brand-level
    CPA movement is what /why decomposes, and the tile links there instead.
    """
    spend = sum(float((r["delta"] or {}).get("spend") or 0) for r in rows.values())
    conv = sum(float((r["delta"] or {}).get("conversions") or 0) for r in rows.values())
    return {"spend": spend, "conversions": conv,
            "appeared": sum(1 for r in rows.values() if r["appeared"]),
            "disappeared": sum(1 for r in rows.values() if r["disappeared"])}


@router.get("/ask", response_class=HTMLResponse)
async def ask_page(request: Request, q: str = "", brand: str = "renegade"):
    """The no-JavaScript path. Same routing, rendered as a page."""
    answer, failed = None, None
    if q.strip():
        try:
            # jsonable_encoder before the template, not tojson inside it: every
            # verb returns dates and Decimals, and Jinja's tojson raises on
            # both. The JSON route already encodes; this makes the two paths
            # render the same bytes instead of one of them 500ing.
            answer = jsonable_encoder(await ask.answer(q, brand))
        except ask.Unroutable as exc:
            failed = str(exc)
        except (UnknownBrand, NotConfigured, ValueError) as exc:
            failed = str(exc).strip().splitlines()[0]
    return templates.TemplateResponse(request, "ask.html", {
        "nav": "overview", "brand": brand, "settled": None, "caveat": None,
        "d": {"brand": brand}, "q": q, "answer": answer, "failed": failed,
        "verbs": ask.VERBS,
    })


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
        d = await metrics.why(brand, days, None, limit=40)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "why", exc)
    return _page(request, "why.html", "why", d, days=days)


@router.get("/creative", response_class=HTMLResponse)
async def creative(request: Request, brand: str = "renegade", window: int = 7,
                   min_spend: float = 100.0, unconfident: int = 0):
    try:
        d = await metrics.fatigue(brand, window, None, min_spend, bool(unconfident))
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "creative", exc)

    # A CTR shape per row. Read in one query rather than one per ad: a fatigue
    # table is routinely forty rows and forty round trips through a pooler is a
    # page that takes four seconds for no reason.
    keys = [r["ad_key"] for r in d["rows"]]
    spark: dict = {}
    if keys:
        for row in await fetch_all(
            "select ad_key, day, "
            "       case when impressions > 0 "
            "            then 100.0 * link_clicks / impressions end as v "
            "  from ads.fact_ad_day "
            " where ad_key = any(%s) and day >= %s "
            " order by ad_key, day",
            (keys, d["rows"][0]["recent_since"] if d["rows"] else date.today()),
        ):
            spark.setdefault(row["ad_key"], []).append(row["v"])
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
async def experiments(request: Request, brand: str = "renegade"):
    try:
        d = await exp_mod.experiments(brand)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "experiments", exc)
    return _page(request, "experiments.html", "experiments", d)


@router.get("/ad/{ad_key}", response_class=HTMLResponse)
async def ad_detail(request: Request, ad_key: str, brand: str = "renegade"):
    try:
        d = await metrics.ad(ad_key, days=90)
    except (UnknownBrand, NotConfigured, ValueError) as exc:
        return _fail(request, "overview", exc)
    daily = list(reversed(d["daily"]))
    return _page(request, "ad.html", "overview", {"brand": brand},
                 ad=d,
                 spend_series=[(r["day"], r["spend"]) for r in daily],
                 conv_series=[(r["day"], r["conversions"]) for r in daily])
