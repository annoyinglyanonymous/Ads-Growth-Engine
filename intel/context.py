"""Shared reads: which brand, and how far back the numbers have settled.

Read-only. Imports fetch_all/fetch_one and nothing that can write.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta

from db import fetch_all, fetch_one


class UnknownBrand(ValueError):
    pass


# ------------------------------------------------------- one request --
# A page like /suggestions runs a dozen verbs for one brand, and each verb
# resolves the brand (and its settled edge) for itself: nine identical
# `ads.brand` lookups per render, ~0.3s of pooler round trip apiece. Inside
# `one_request()` those lookups are shared -- the first caller's query is the
# answer for every other caller IN THAT REQUEST, including callers running
# concurrently with it. Outside one, brand() and settled_through() query every
# time, exactly as before.
#
# Deliberately per request and never across requests: a brand row is
# config-like, but a module-level cache would outlive an import or a rename,
# and the settled edge moves with now().

_request_memo: ContextVar[dict | None] = ContextVar("intel_request_memo",
                                                     default=None)


@contextmanager
def one_request() -> Iterator[None]:
    """Share brand() and settled_through() answers until the block exits.

    Re-entrant: a verb that opens one inside a page that already has one
    joins the page's. Tasks started inside the block inherit it (contextvars
    are copied at task creation, and the memo dict is shared by reference).
    """
    if _request_memo.get() is not None:
        yield
        return
    token = _request_memo.set({})
    try:
        yield
    finally:
        _request_memo.reset(token)


async def _memo(key: tuple, make: Callable[[], Awaitable]):
    memo = _request_memo.get()
    if memo is None:
        return await make()
    fut = memo.get(key)
    if fut is None:
        fut = memo[key] = asyncio.ensure_future(make())
    # Shielded: one caller being cancelled must not cancel the lookup the
    # other callers are waiting on.
    return await asyncio.shield(fut)


async def gather_in_order(*aws: Awaitable) -> list:
    """Run independent awaitables concurrently; fail as sequential code would.

    asyncio.gather's default raises whichever failure happens FIRST IN TIME,
    and TaskGroup wraps failures in an ExceptionGroup -- either would change
    what a caller catching UnknownBrand/ValueError sees. This waits for all of
    them and raises the failure of the EARLIEST ARGUMENT, which is the one
    `a = await x; b = await y` would have raised. The later ones are reads, so
    having run them anyway has no effect beyond the query.
    """
    results = await asyncio.gather(*aws, return_exceptions=True)
    for r in results:
        if isinstance(r, BaseException):
            raise r
    return results


async def brand(slug: str) -> dict:
    """-> {id, slug, name}. Raises rather than returning None.

    A verb that silently returns [] for a misspelt brand reads as "this brand
    has no ads", which is the one outcome nobody investigates. growth-engine's
    meta_ads/__main__.py takes the same stance on an empty account list.
    """
    # ads.brand, not public.brands: ads_reader holds no grant in public, so
    # the direct read died with "permission denied for table brands" on the
    # first query of every verb. See migrations/008_ads_brand_seam.sql.
    # A copy, so one caller editing its row cannot reach another's.
    return dict(await _memo(("brand", slug), lambda: _brand(slug)))


async def _brand(slug: str) -> dict:
    row = await fetch_one(
        "select id, slug, name from ads.brand where slug = %s", (slug,))
    if not row:
        known = await fetch_all("select slug from ads.brand order by slug")
        names = ", ".join(r["slug"] for r in known) or "none"
        raise UnknownBrand(f"no brand {slug!r}. Known brands: {names}")
    return row


async def settled_through(brand_id: str) -> date | None:
    """The last day whose numbers have stopped moving, or None.

    None means the brand has no active ad account -- which is a different
    problem from "the data is fresh", and the caller has to be able to tell
    them apart.
    """
    return await _memo(("settled_through", str(brand_id)),
                       lambda: _settled_through(brand_id))


async def _settled_through(brand_id: str) -> date | None:
    row = await fetch_one(
        "select ads.settled_through(%s, now()) as d", (brand_id,))
    return row["d"] if row else None


async def latest_day(brand_slug: str) -> date | None:
    """The most recent day this brand actually has numbers for.

    NOT the settled edge, and not today. `settled_through` is the honest end
    for an agent reporting a rate -- it is what `window()` defaults to, and
    every verb still uses it. This is the honest end for a READER looking at a
    dashboard: somebody who has just imported and sees a window stopping three
    days back reads it as the import having done nothing.

    And not today either. Today is routinely a day that has not started in the
    account's timezone, so ending a window there appends an empty day and every
    chart falls off a cliff at the right-hand edge.

    One definition, because two would drift. The page renders this window and
    scripts/suggest.py writes its prose against it; if they disagreed, every
    published suggestion would arrive already carrying a "written against a
    different window" caveat, and a caveat that always fires is wallpaper.

    None when the brand has no facts at all, which puts the caller back on
    `window()`'s settled default rather than inventing a date.
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


def window(days: int, until: date | None, settled: date | None) -> tuple[date, date, int]:
    """-> (since, until, unsettled_days).

    `until` defaults to the settled edge rather than to today, so the default
    answer is the honest one. Asking explicitly for a window that runs past the
    edge is allowed -- sometimes you do want to see today -- but the number of
    unsettled days comes back with it and every verb reports it.
    """
    if days < 1:
        raise ValueError(f"--days must be at least 1, got {days}")
    end = until or settled or date.today()
    unsettled = max(0, (end - settled).days) if settled else 0
    return end - timedelta(days=days - 1), end, unsettled


def caveat(unsettled: int, settled: date | None) -> str | None:
    """One sentence for the agent to repeat, or None when the window is clean."""
    if settled is None:
        return ("No active ad account for this brand, so nothing can be said "
                "about how settled these numbers are.")
    if unsettled > 0:
        return (f"The last {unsettled} day(s) of this window are not settled: "
                f"Meta restates attributed conversions for about three days, "
                f"and the account's own day may not have closed. Treat any "
                f"decline at the end of this window as provisional.")
    return None
