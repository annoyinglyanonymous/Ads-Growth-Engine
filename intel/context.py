"""Shared reads: which brand, and how far back the numbers have settled.

Read-only. Imports fetch_all/fetch_one and nothing that can write.
"""

from __future__ import annotations

from datetime import date, timedelta

from db import fetch_all, fetch_one


class UnknownBrand(ValueError):
    pass


async def brand(slug: str) -> dict:
    """-> {id, slug, name}. Raises rather than returning None.

    A verb that silently returns [] for a misspelt brand reads as "this brand
    has no ads", which is the one outcome nobody investigates. growth-engine's
    meta_ads/__main__.py takes the same stance on an empty account list.
    """
    # ads.brand, not public.brands: ads_reader holds no grant in public, so
    # the direct read died with "permission denied for table brands" on the
    # first query of every verb. See migrations/008_ads_brand_seam.sql.
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
    row = await fetch_one(
        "select ads.settled_through(%s, now()) as d", (brand_id,))
    return row["d"] if row else None


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
