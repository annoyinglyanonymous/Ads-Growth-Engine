"""Orchestrate one pull: `meta_ads.client` fetches, `meta_ads.store` writes.

    await run_pull(brand_slug="renegade")

Three phases per account, each its own `meta_pulls` row because each fails
for a different reason and for a different audience to read (042's own
comment): **structure** (campaigns, ad sets, ads and their texts, matched
back to `campaign_assets` as it goes), then **insights** (the daily
performance window). There is no separate 'match' pull: matching happens
inside the structure phase, per ad, using candidates fetched once -- see
`meta_ads.store.upsert_ad`. The `match` `kind` migration 042 defines is kept
for a future re-match pass over history (a `tracked_url` stamped after an ad
was already imported); nothing here writes one yet.

ONE ACCOUNT'S FAILURE MUST NOT COST ANOTHER'S IMPORT
`run_pull` catches per account. A revoked System User assignment on one ad
account is the ordinary case `client.AccountUnavailable` names, and a brand
with two accounts must still get the working one.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from meta_ads import client as client_mod
from meta_ads import parse, store
from meta_ads.client import GraphClient, MetaError, TokenInvalid

#: On a first pull there is no watermark. Meta restates the last few days of
#: a window as attribution catches up, so even a re-run pulls a short
#: lookback rather than exactly "since the last run" -- see 042's comment on
#: why `meta_ad_insights` upserts on (ad_id, date) instead of only inserting.
INSIGHTS_LOOKBACK_DAYS = 30
INSIGHTS_RESTATEMENT_DAYS = 3

#: The only ads whose creative is worth fetching.
#:
#: A paused ad's copy does not change, and 465 of act_153704749222533's 711 ads
#: have never spent a cent -- so the unrestricted crawl that has never once
#: succeeded was spending most of itself on ads nobody will ever read. Every ad
#: that delivered in the last 30 days already has its copy in the warehouse;
#: what the failure actually costs is NEW ads, which never arrive, so their
#: insight rows are dropped on the foreign key and counted as
#: `skipped_unknown_ad`.
#:
#: WITH_ISSUES and DISAPPROVED are included on purpose. They are ads somebody
#: intended to run, their copy is current, and an ad that is disapproved is one
#: you are more likely to go and read, not less.
ACTIVE_ENOUGH = ("ACTIVE", "WITH_ISSUES", "DISAPPROVED")

#: Days of insights per request.
#:
#: The window is chunked because `level=ad` with `time_increment=1` makes Meta
#: compute one row per ad per day for the WHOLE time_range before it returns
#: the first page. On an account with four hundred ads a thirty-day range is
#: twelve thousand rows of server-side work, and that is the shape of request
#: that times out -- "Please reduce the amount of data you're asking for" is
#: what act_153704749222533 said to a much cheaper /ads call.
#:
#: The page-size halving in client.py does NOT help here, and that distinction
#: is the reason this constant exists rather than a bigger limit. `limit`
#: governs how many rows come back per page; it does nothing about how many
#: Meta had to compute to answer at all. A too-expensive query needs a smaller
#: WINDOW, not a smaller page.
#:
#: Seven rather than one: a day per request turns a month into thirty round
#: trips against a rate limiter this module is careful about, and a week is
#: comfortably inside what Meta serves for accounts this size.
INSIGHTS_CHUNK_DAYS = 7


def _chunks(since: date, until: date, size: int) -> list[tuple[date, date]]:
    """[since, until] split into contiguous spans of at most `size` days.

    Inclusive at both ends, like Meta's time_range, so a 7-day chunk really is
    seven days and consecutive chunks do not overlap -- an overlap would be
    harmless (the upsert is keyed on (ad_id, date)) but it would pay for the
    same day twice against the rate limit.
    """
    out: list[tuple[date, date]] = []
    start = since
    while start <= until:
        end = min(start + timedelta(days=size - 1), until)
        out.append((start, end))
        start = end + timedelta(days=1)
    return out


class BrandNotFound(ValueError):
    """A brand slug that is not in `public.brands`."""


async def _brand_id(brand_slug: str) -> str:
    row = await store.brand_by_slug(brand_slug)
    if not row:
        raise BrandNotFound(f"no brand {brand_slug!r}")
    return row["id"]


async def _pull_structure(graph: GraphClient, account: dict, *,
                          brand_id: str, started_by: str,
                          api_version: str) -> dict:
    act_id = account["act_id"]
    counts = {"campaigns": 0, "adsets": 0, "ads": 0, "texts": 0,
             "matched": 0, "statuses": 0}
    run_id = await store.start_pull(
        brand_id=brand_id, account_id=act_id, kind="structure",
        since=None, until=None, started_by=started_by,
        api_version=api_version)
    try:
        info = await graph.account(act_id)
        await store.set_account_meta(
            act_id, currency=info.get("currency"),
            timezone_name=info.get("timezone_name"), raw=info)

        campaigns = [row async for row in graph.campaigns(act_id)]
        counts["campaigns"] = await store.upsert_campaigns(
            campaigns, brand_id=brand_id, account_id=act_id)

        adsets = [row async for row in graph.adsets(act_id)]
        counts["adsets"] = await store.upsert_adsets(
            adsets, brand_id=brand_id, account_id=act_id)

        watermark = await store.last_successful_pull(act_id, "structure")
        # STARTED, not finished. A structure run takes minutes, and an ad
        # edited between its start and its finish was listed before the edit
        # and has an updated_time before `finished_at` -- so a watermark at the
        # finish skips that edit on every later run, and the warehouse keeps
        # the old wording for good. Re-reading the few ads edited during the
        # run is the cost of never losing one.
        updated_since = (watermark.get("started_at") or watermark["finished_at"]
                         if watermark else None)

        # PASS ONE, cheap: every ad, id and status only, no creative. This is
        # what keeps a paused ad from sitting in the warehouse marked ACTIVE
        # forever now that the heavy pass below only asks for live ones. It is
        # also the pass that gives the account's true ad count (944 on
        # 2026-09-25), which the old single-pass crawl never lived to report.
        statuses = [row async for row in graph.ads(
            act_id, fields=client_mod.AD_STATUS_FIELDS)]
        counts["statuses"] = await store.set_ad_statuses(statuses)

        # PASS TWO, heavy: creatives, and only for ads worth the expansion.
        # `updated_since` still applies on top -- once one pull succeeds, this
        # narrows again to the handful edited since.
        candidates = await store.candidates_for_match(brand_id)
        async for ad in graph.ads(act_id, updated_since=updated_since,
                                  effective_status=ACTIVE_ENOUGH):
            result = await store.upsert_ad(
                ad, brand_id=brand_id, account_id=act_id,
                candidates=candidates)
            counts["ads"] += 1
            counts["texts"] += result["texts"]
            counts["matched"] += int(result["matched"])

        await store.finish_pull(run_id, status="ok", counts=counts)
        return counts
    except Exception as exc:
        await store.finish_pull(
            run_id, status="failed", counts=counts, error=str(exc))
        raise


def _account_today(account: dict) -> date:
    """Today in the AD ACCOUNT's timezone, which is the only day Meta means.

    An insights date is a day in the account's own timezone (042 says so on
    meta_ad_accounts.timezone_name), and this used to read
    `datetime.now(timezone.utc).date()`. For a US account that asks Meta for a
    day that has not started yet.

    Observed on 2026-09-22 at 05:35 UTC against act_153704749222533
    (America/Los_Angeles, so still 2026-09-21 locally): the window ran to
    2026-09-22, Meta returned NOTHING for it, and the watermark advanced to a
    day with no rows -- so the dashboard reported "insights through
    2026-09-22" over an empty day. One wasted day per request, and a watermark
    a day ahead of the data.

    `ads.settled_through()` already does this correctly in SQL and says in its
    own comment that it reads the account timezone "rather than trusting the
    importer" precisely because of this bug. This makes the importer agree.

    Falls back to UTC, loudly enough to be findable: an account whose
    timezone_name is null has never completed a structure pull, and a missing
    tzdata entry is a packaging problem rather than a reason to import
    nothing.
    """
    name = account.get("timezone_name")
    if not name:
        return datetime.now(timezone.utc).date()
    try:
        return datetime.now(ZoneInfo(name)).date()
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(timezone.utc).date()


async def _pull_insights(graph: GraphClient, account: dict, *,
                         brand_id: str, started_by: str, api_version: str,
                         since: date | None, until: date | None) -> dict:
    act_id = account["act_id"]
    today = _account_today(account)
    window_until = until or today
    if since is not None:
        window_since = since
    else:
        watermark = await store.last_successful_pull(act_id, "insights")
        if watermark and watermark["until"]:
            window_since = watermark["until"] - timedelta(
                days=INSIGHTS_RESTATEMENT_DAYS)
        else:
            window_since = today - timedelta(days=INSIGHTS_LOOKBACK_DAYS)

    counts = {"rows": 0}
    run_id = await store.start_pull(
        brand_id=brand_id, account_id=act_id, kind="insights",
        since=window_since, until=window_until, started_by=started_by,
        api_version=api_version)
    try:
        spans = _chunks(window_since, window_until, INSIGHTS_CHUNK_DAYS)
        for span_since, span_until in spans:
            rows = []
            async for raw in graph.insights(act_id, since=span_since,
                                            until=span_until):
                rows.append(parse.insight_row(raw, currency=account.get(
                    "currency")))
            # Written per chunk, not once at the end. Accumulating the whole
            # window in memory and upserting it in one go means a failure on
            # the last day discards the first twenty-nine -- which is what a
            # partial import should never cost, because meta_ad_insights
            # upserts on (ad_id, date) and a re-run would have resumed.
            #
            # A dict since 046, not a bare count: an orphaned insights row --
            # an ad that spent in the window and has since been deleted, so
            # /ads no longer returns it -- is skipped rather than aborting the
            # account's whole batch on the foreign key. The skip lands in
            # meta_pulls.counts where somebody can see it; a silently smaller
            # number would read as a quiet week.
            written = await store.upsert_insights(
                rows, brand_id=brand_id, account_id=act_id, pull_id=run_id)
            for key, value in written.items():
                if isinstance(value, int):
                    counts[key] = counts.get(key, 0) + value
        counts["chunks"] = len(spans)
        await store.finish_pull(run_id, status="ok", counts=counts)
        return counts
    except Exception as exc:
        await store.finish_pull(
            run_id, status="failed", counts=counts, error=str(exc))
        raise


#: A phase that loses its database connection is run again this many times in
#: all, after a pause long enough for a pooler restart to finish.
DB_ATTEMPTS = 2
DB_RETRY_WAIT_SECONDS = 15


async def run_pull(*, brand_slug: str, started_by: str,
                   since: date | None = None,
                   until: date | None = None,
                   phases: tuple[str, ...] = ("structure", "insights"),
                   ) -> list[dict]:
    """Pull every active account under a brand. Returns one summary per
    account: `{"act_id", "ok", "structure", "insights", "error"}`.

    A `NotConfigured` token refusal is raised, not swallowed -- it means
    nothing can be pulled for any account, which is the caller's one
    sentence to print, not a per-account result to report six times.
    `TokenInvalid` (code 190) is raised too, and for the same reason, once
    it happens on the first account: `client.py`'s own docstring says every
    account fails it identically, so trying the rest would only spend the
    time to learn that again.
    """
    brand_id = await _brand_id(brand_slug)

    results = []
    # The client is built BEFORE the accounts are counted, so a missing
    # token wins over an empty brand. The other order is the bug 042's
    # header warns about: "a command that answered 'no ads' would be
    # reporting a missing token as an empty account."
    async with GraphClient.from_settings() as graph:
        accounts = await store.accounts_for(brand_id, active_only=True)
        for account in accounts:
            summary = {"act_id": account["act_id"], "ok": True,
                       "structure": None, "insights": None, "error": None}
            errors: list[str] = []

            # The two phases are caught SEPARATELY, and that is not tidiness.
            # They use different edges and write different tables, and this
            # file's own header says each "fails for a different reason and
            # for a different audience to read" -- and then, until now, one
            # try block threw away the second whenever the first failed.
            #
            # act_153704749222533 is what that costs. Its /ads edge dies about
            # eight minutes into pagination, reproducibly, so the structure
            # phase never finished and the insights phase -- a different edge,
            # never once attempted -- was skipped four times running. The
            # account had seven hundred ads in the warehouse and not one number
            # against them.
            #
            # Insights after a failed structure is safe by construction:
            # store.upsert_insights skips rows for ads meta_ads does not know
            # about and reports the count, so the worst case is a smaller
            # import that says how much it left out. A stale ad list is a
            # reason to read the numbers carefully, not a reason to have none.
            for phase, run in (
                ("structure", lambda: _pull_structure(
                    graph, account, brand_id=brand_id,
                    started_by=started_by, api_version=graph.version)),
                ("insights", lambda: _pull_insights(
                    graph, account, brand_id=brand_id,
                    started_by=started_by, api_version=graph.version,
                    since=since, until=until)),
            ):
                if phase not in phases:
                    continue
                for attempt in range(1, DB_ATTEMPTS + 1):
                    try:
                        summary[phase] = await run()
                        break
                    except TokenInvalid:
                        # Still raised, still for client.py's reason: every
                        # account and every phase fails a dead token
                        # identically, so carrying on only spends time
                        # learning that again.
                        raise
                    except MetaError as exc:
                        summary["ok"] = False
                        errors.append(f"{phase}: {type(exc).__name__}: {exc}")
                        break
                    except psycopg.OperationalError as exc:
                        # THE DATABASE, NOT META. Supabase dropped the
                        # connection mid-phase on 2026-09-22 and 2026-09-25,
                        # both times a structure run gone long. This used to
                        # propagate out of run_pull, so a blip in structure
                        # cost the brand its INSIGHTS too -- a different edge
                        # and table that never got asked. Every write here is
                        # an upsert or an update, so running the phase again is
                        # safe; the pool's connection check discards the dead
                        # socket and lends a fresh one.
                        if attempt < DB_ATTEMPTS:
                            await asyncio.sleep(DB_RETRY_WAIT_SECONDS)
                            continue
                        summary["ok"] = False
                        errors.append(
                            f"{phase}: database connection lost "
                            f"{DB_ATTEMPTS} times: {type(exc).__name__}: "
                            f"{str(exc).splitlines()[0] if str(exc) else ''}")

            # Kept as one string so existing readers -- scripts/sync.py's log
            # line, __main__'s JSON -- still show something, but named by phase
            # so "which half failed" does not need the log to answer.
            summary["error"] = "; ".join(errors) or None
            results.append(summary)
    return results
