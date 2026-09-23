"""Write imported Meta rows to the database. The only thing here that runs SQL.

`meta_ads.client` fetches Graph API pages; `meta_ads.parse` turns one into a
row shape; this module upserts. Kept as its own seam so the question "does
this table replace or merge?" lives in one place, and so `parse` stays
testable without a database -- see `meta_ads/__init__.py` for the split.

EVERY STRUCTURE UPSERT KEYS ON THE PLATFORM'S OWN ID, ON PURPOSE
042's own comment: `on conflict (id) do update` is what makes a pull
idempotent -- a page fetched twice, or re-fetched after a retry, produces the
same rows rather than a growing history. `meta_ad_texts` is the one
exception: it is replaced wholesale per ad (delete, then insert), never
merged, because an ad's texts are a set, not rows with an identity of their
own.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from db_meta import cursor, fetch_all, fetch_one
from meta_ads import parse

async def brand_by_slug(slug: str) -> dict | None:
    """`{id, slug, name}` or None. Returns rather than raises: the CLI wants
    a one-sentence ValueError, `pull` wants its own BrandNotFound and the UI
    wants a flash, and each of those is a worse message when it has to be
    translated from somebody else's exception."""
    return await fetch_one(
        "select id, slug, name from public.brands where slug = %s", (slug,))


# --------------------------------------------------------------------------
# Accounts -- added explicitly by the CLI, never upserted from a pull
# --------------------------------------------------------------------------


async def add_account(*, brand_id: str, act_id: str, label: str | None,
                      added_by: str) -> dict:
    """Register (or reactivate) one ad account under a brand.

    Re-adding an account that was previously deactivated REVIVES it rather
    than failing on the unique index, the same stance `exclusions.add` takes
    on a retired phrase -- repeating the command is what somebody means.
    """
    async with cursor() as cur:
        await cur.execute(
            "insert into public.meta_ad_accounts "
            "  (brand_id, act_id, label, added_by) "
            "values (%s, %s, %s, %s) "
            "on conflict (act_id) do update "
            "   set active = true, "
            "       label = coalesce(excluded.label, "
            "                        public.meta_ad_accounts.label) "
            "returning id, brand_id, act_id, label, active",
            (brand_id, act_id, label, added_by))
        return await cur.fetchone()


async def deactivate_account(*, act_id: str) -> dict | None:
    """Stop pulling one account. Returns the row, or None if it is not known.

    A flag rather than a delete, for the same reason `add_account` revives
    instead of failing: the account's imported rows stay joinable and the
    decision is reversible by re-adding it.

    WHY THIS VERB EXISTS. An account the token cannot read fails every pull in
    under a second, and the cost is not the wasted call. `run_pull` isolates
    per account, so the import still succeeds -- but the run's exit code is 1,
    `intel status` reports `healthy: false`, and the dashboard's import card
    goes amber, all on behalf of an account nobody reads. A scheduled task that
    reports failure on every single run is a task nobody checks, which is the
    invisible staleness scripts/sync.py exists to prevent.

    It does NOT touch the account's history. Deactivating act_9105140029692
    leaves its `meta_pulls` rows where they are; `intel status` still lists the
    failures under `recent_failures` for anybody asking why it stopped.
    """
    async with cursor() as cur:
        await cur.execute(
            "update public.meta_ad_accounts set active = false "
            " where act_id = %s "
            "returning id, brand_id, act_id, label, active",
            (act_id,))
        return await cur.fetchone()


async def accounts_for(brand_id: str | None = None, *,
                       active_only: bool = True) -> list[dict]:
    return await fetch_all(
        "select a.id, a.act_id, a.label, a.currency, a.timezone_name, "
        "       a.active, b.slug as brand_slug "
        "from public.meta_ad_accounts a "
        "join public.brands b on b.id = a.brand_id "
        "where (%s::uuid is null or a.brand_id = %s) "
        "  and (%s = false or a.active) "
        "order by b.slug, a.act_id",
        (brand_id, brand_id, active_only))


async def set_account_meta(act_id: str, *, currency: str | None,
                           timezone_name: str | None,
                           raw: dict | None) -> None:
    """Currency and timezone, read back from the account itself.

    Every spend figure and every insights date depends on these, and neither
    is known until the first structure pull asks Meta for it -- see
    `meta_ads.client.account` and 042's comment on why both are copied onto
    dependent rows rather than joined to at read time.
    """
    async with cursor() as cur:
        await cur.execute(
            "update public.meta_ad_accounts "
            "set currency = coalesce(%s, currency), "
            "    timezone_name = coalesce(%s, timezone_name), "
            "    raw = coalesce(%s::jsonb, raw) "
            "where act_id = %s",
            (currency, timezone_name,
             json.dumps(raw) if raw is not None else None, act_id))


# --------------------------------------------------------------------------
# Structure: campaigns, adsets, ads and their texts
# --------------------------------------------------------------------------

async def upsert_campaigns(rows: list[dict], *, brand_id: str,
                           account_id: str) -> int:
    if not rows:
        return 0
    async with cursor() as cur:
        for row in rows:
            await cur.execute(
                "insert into public.meta_campaigns "
                "  (id, account_id, brand_id, name, status, "
                "   effective_status, objective, buying_type, daily_budget, "
                "   lifetime_budget, spend_cap, start_time, stop_time, "
                "   created_time, updated_time, raw, pulled_at) "
                "values (%(id)s, %(account_id)s, %(brand_id)s, %(name)s, "
                "        %(status)s, %(effective_status)s, %(objective)s, "
                "        %(buying_type)s, %(daily_budget)s, "
                "        %(lifetime_budget)s, %(spend_cap)s, "
                "        %(start_time)s, %(stop_time)s, %(created_time)s, "
                "        %(updated_time)s, %(raw)s::jsonb, now()) "
                "on conflict (id) do update set "
                "  name = excluded.name, status = excluded.status, "
                "  effective_status = excluded.effective_status, "
                "  objective = excluded.objective, "
                "  buying_type = excluded.buying_type, "
                "  daily_budget = excluded.daily_budget, "
                "  lifetime_budget = excluded.lifetime_budget, "
                "  spend_cap = excluded.spend_cap, "
                "  start_time = excluded.start_time, "
                "  stop_time = excluded.stop_time, "
                "  created_time = excluded.created_time, "
                "  updated_time = excluded.updated_time, "
                "  raw = excluded.raw, pulled_at = now()",
                {
                    "id": row.get("id"), "account_id": account_id,
                    "brand_id": brand_id, "name": row.get("name"),
                    "status": row.get("status"),
                    "effective_status": row.get("effective_status"),
                    "objective": row.get("objective"),
                    "buying_type": row.get("buying_type"),
                    "daily_budget": row.get("daily_budget"),
                    "lifetime_budget": row.get("lifetime_budget"),
                    "spend_cap": row.get("spend_cap"),
                    "start_time": row.get("start_time"),
                    "stop_time": row.get("stop_time"),
                    "created_time": row.get("created_time"),
                    "updated_time": row.get("updated_time"),
                    "raw": json.dumps(row),
                })
    return len(rows)


async def upsert_adsets(rows: list[dict], *, brand_id: str,
                        account_id: str) -> int:
    if not rows:
        return 0
    async with cursor() as cur:
        for row in rows:
            await cur.execute(
                "insert into public.meta_adsets "
                "  (id, campaign_id, account_id, brand_id, name, status, "
                "   effective_status, optimization_goal, billing_event, "
                "   bid_strategy, daily_budget, lifetime_budget, "
                "   start_time, end_time, created_time, updated_time, raw, "
                "   pulled_at) "
                "values (%(id)s, %(campaign_id)s, %(account_id)s, "
                "        %(brand_id)s, %(name)s, %(status)s, "
                "        %(effective_status)s, %(optimization_goal)s, "
                "        %(billing_event)s, %(bid_strategy)s, "
                "        %(daily_budget)s, %(lifetime_budget)s, "
                "        %(start_time)s, %(end_time)s, %(created_time)s, "
                "        %(updated_time)s, %(raw)s::jsonb, now()) "
                "on conflict (id) do update set "
                "  campaign_id = excluded.campaign_id, "
                "  name = excluded.name, status = excluded.status, "
                "  effective_status = excluded.effective_status, "
                "  optimization_goal = excluded.optimization_goal, "
                "  billing_event = excluded.billing_event, "
                "  bid_strategy = excluded.bid_strategy, "
                "  daily_budget = excluded.daily_budget, "
                "  lifetime_budget = excluded.lifetime_budget, "
                "  start_time = excluded.start_time, "
                "  end_time = excluded.end_time, "
                "  created_time = excluded.created_time, "
                "  updated_time = excluded.updated_time, "
                "  raw = excluded.raw, pulled_at = now()",
                {
                    "id": row.get("id"),
                    "campaign_id": row.get("campaign_id"),
                    "account_id": account_id, "brand_id": brand_id,
                    "name": row.get("name"), "status": row.get("status"),
                    "effective_status": row.get("effective_status"),
                    "optimization_goal": row.get("optimization_goal"),
                    "billing_event": row.get("billing_event"),
                    "bid_strategy": row.get("bid_strategy"),
                    "daily_budget": row.get("daily_budget"),
                    "lifetime_budget": row.get("lifetime_budget"),
                    "start_time": row.get("start_time"),
                    "end_time": row.get("end_time"),
                    "created_time": row.get("created_time"),
                    "updated_time": row.get("updated_time"),
                    "raw": json.dumps(row),
                })
    return len(rows)


async def candidates_for_match(brand_id: str) -> list[dict]:
    """Every `campaign_assets` row an imported ad could match to.

    Scoped to `channel = 'meta_ads'` and a stamped `tracked_url` -- an asset
    with neither can never be the target of `parse.match_asset`, so there is
    no reason to hand it over as a candidate. Shape matches exactly what
    `meta_ads.parse.match_asset` and `tests/test_meta_match.py` expect:
    `id`, `tracked_url`, `status`, `version_number`, `approved_at`.
    """
    return await fetch_all(
        "select ca.id, ca.tracked_url, ca.status, ca.version_number, "
        "       ca.approved_at, ca.updated_at, ca.created_at "
        "from public.campaign_assets ca "
        "join public.campaigns c on c.id = ca.campaign_id "
        "where c.brand_id = %s and ca.channel = 'meta_ads' "
        "  and ca.tracked_url is not null",
        (brand_id,))


async def set_ad_statuses(rows: list[dict]) -> int:
    """Update effective_status and status on ads we already have. Nothing else.

    Deliberately an UPDATE and not an upsert. These rows come from the cheap
    pass, which asks for no creative and no name, so inserting from them would
    create an ad with almost every column null -- and that row would then look
    to every reader like an ad whose copy we failed to import, rather than one
    we have not fetched yet. An ad this pass has never seen is picked up by the
    heavy pass when it is active, or is genuinely not worth a creative fetch.

    `updated_at` is deliberately not touched: this is a correction to what we
    already knew, not evidence that the ad changed.
    """
    if not rows:
        return 0
    n = 0
    async with cursor() as cur:
        for row in rows:
            if not row.get("id"):
                continue
            await cur.execute(
                "update public.meta_ads "
                "   set effective_status = %s, status = %s, pulled_at = now() "
                " where id = %s",
                (row.get("effective_status"), row.get("status"), row["id"]))
            n += cur.rowcount or 0
    return n


async def upsert_ad(raw: dict, *, brand_id: str, account_id: str,
                    candidates: list[dict]) -> dict:
    """One ad: the row, its texts, and the join back to our own copy.

    `candidates` is fetched once per pull (`candidates_for_match`) and passed
    in rather than queried per ad -- an account with two thousand ads must
    not become two thousand queries against `campaign_assets`.

    Returns `{"matched": bool, "texts": int}` so a caller summing pull
    counts does not have to re-run the parse to find out.
    """
    creative = raw.get("creative") or {}
    texts = parse.creative_texts(creative)
    urls = parse.link_urls(creative)
    pairs = [parse.parse_utm(url) for url in urls]
    match = parse.match_asset(pairs, candidates)

    utm_campaign, utm_content = next(
        (p for p in pairs if p and p[1]), (None, None))
    link_url = urls[0] if urls else None

    async with cursor() as cur:
        await cur.execute(
            "insert into public.meta_ads "
            "  (id, adset_id, campaign_id, account_id, brand_id, name, "
            "   status, effective_status, creative_id, "
            "   call_to_action_type, link_url, url_tags, object_story_id, "
            "   is_dynamic, needs_page_scope, image_url, thumbnail_url, "
            "   video_id, utm_campaign, utm_content, campaign_asset_id, "
            "   matched_at, raw, pulled_at, first_seen_at, last_seen_at) "
            "values "
            "  (%(id)s, %(adset_id)s, %(campaign_id)s, %(account_id)s, "
            "   %(brand_id)s, %(name)s, %(status)s, %(effective_status)s, "
            "   %(creative_id)s, %(call_to_action_type)s, %(link_url)s, "
            "   %(url_tags)s, %(object_story_id)s, %(is_dynamic)s, "
            "   %(needs_page_scope)s, %(image_url)s, %(thumbnail_url)s, "
            "   %(video_id)s, %(utm_campaign)s, %(utm_content)s, "
            "   %(campaign_asset_id)s, %(matched_at)s, %(raw)s::jsonb, "
            "   now(), now(), now()) "
            "on conflict (id) do update set "
            "  adset_id = excluded.adset_id, "
            "  campaign_id = excluded.campaign_id, "
            "  name = excluded.name, status = excluded.status, "
            "  effective_status = excluded.effective_status, "
            "  creative_id = excluded.creative_id, "
            "  call_to_action_type = excluded.call_to_action_type, "
            "  link_url = excluded.link_url, url_tags = excluded.url_tags, "
            "  object_story_id = excluded.object_story_id, "
            "  is_dynamic = excluded.is_dynamic, "
            "  needs_page_scope = excluded.needs_page_scope, "
            "  image_url = excluded.image_url, "
            "  thumbnail_url = excluded.thumbnail_url, "
            "  video_id = excluded.video_id, "
            "  utm_campaign = excluded.utm_campaign, "
            "  utm_content = excluded.utm_content, "
            "  campaign_asset_id = excluded.campaign_asset_id, "
            "  matched_at = excluded.matched_at, raw = excluded.raw, "
            "  pulled_at = now(), last_seen_at = now()",
            {
                "id": raw.get("id"), "adset_id": raw.get("adset_id"),
                "campaign_id": raw.get("campaign_id"),
                "account_id": account_id, "brand_id": brand_id,
                "name": raw.get("name"), "status": raw.get("status"),
                "effective_status": raw.get("effective_status"),
                "creative_id": creative.get("id"),
                "call_to_action_type": creative.get("call_to_action_type"),
                "link_url": link_url,
                "url_tags": creative.get("url_tags"),
                "object_story_id": (creative.get("effective_object_story_id")
                                    or creative.get("object_story_id")),
                "is_dynamic": texts.is_dynamic,
                "needs_page_scope": texts.needs_page_scope,
                "image_url": creative.get("image_url"),
                "thumbnail_url": creative.get("thumbnail_url"),
                "video_id": creative.get("video_id"),
                "utm_campaign": utm_campaign, "utm_content": utm_content,
                "campaign_asset_id": match["id"] if match else None,
                "matched_at": (datetime.now(timezone.utc)
                              if match else None),
                "raw": json.dumps(raw),
            })
        # Inside the SAME transaction as the ad row above.
        #
        # These were two transactions until 046. A crash, a pooler drop or a
        # rate-limit abort between them left an ad row updated and its copy
        # still the PREVIOUS pull's -- and copy identity is what every creative
        # conclusion hangs on. ad_reviews keys on text_hash and ads.ad_facet
        # keys on copy_hash, so a stale text set silently attaches a review and
        # a tag to wording that is not running.
        #
        # The cursor is passed down rather than reopened, because `db.cursor()`
        # takes a connection from the pool and a second one is a second
        # transaction however tidy it looks.
        await replace_ad_texts(raw.get("id"), texts.texts, cur=cur)

    return {"matched": match is not None, "texts": len(texts.texts)}


async def replace_ad_texts(ad_id: str,
                           texts: tuple[tuple[str, int, str], ...],
                           *, cur=None) -> None:
    """Delete, then insert. A merge would leave a stale ordinal behind the
    first time an advertiser removed a carousel card.

    `cur` is the caller's cursor, so this runs inside the caller's transaction.
    Called without one it opens its own, which keeps the delete-then-insert
    atomic on its own terms -- but `upsert_ad` always passes one, because the
    ad and its copy have to move together or not at all.
    """
    async def _run(c) -> None:
        await c.execute(
            "delete from public.meta_ad_texts where ad_id = %s", (ad_id,))
        for field, ordinal, text in texts:
            await c.execute(
                "insert into public.meta_ad_texts "
                "  (ad_id, field, ordinal, text) "
                "values (%s, %s, %s, %s)",
                (ad_id, field, ordinal, text))

    if cur is not None:
        await _run(cur)
        return
    async with cursor() as own:
        await _run(own)


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------

async def upsert_insights(rows: list[dict], *, brand_id: str,
                          account_id: str, pull_id: int) -> dict:
    """Write a window of daily rows, skipping any whose ad we do not have.

    WHY THE PRE-FILTER EXISTS, AND WHY IT IS NOT AN EDGE CASE

    `meta_ad_insights.ad_id` has a hard foreign key to `meta_ads(id)`, and this
    whole batch runs on one pooled connection -- so before this filter, ONE row
    with no parent aborted every insights row for the account and reported the
    run as `failed`.

    That is the normal case on a first pull, not a rare one. `GET /act_X/ads`
    excludes archived and deleted ads by default, while `/insights` happily
    reports the spend those ads made while they were alive. Any ad that ran in
    the window and has since been deleted arrives here as an orphan. The
    symptom is an import that looks completely broken.

    The foreign key stays. 042's chain-has-no-foreign-keys reasoning is about
    WATERMARKED STRUCTURE, where a pull legitimately skips an unchanged parent;
    this key is what makes every join in the metrics layer total, and dropping
    it would silently orphan spend instead of naming it.

    Returns a summary rather than a count, because "wrote 900 of 940, skipped
    40 with no parent ad" is the sentence that tells somebody the account has
    deleted ads that still spent -- and `meta_pulls.counts` is where it lands.
    """
    if not rows:
        return {"rows": 0, "skipped_unknown_ad": 0, "skipped_ad_ids": []}

    wanted = {r["ad_id"] for r in rows if r.get("ad_id")}
    known: set[str] = set()
    if wanted:
        for found in await fetch_all(
            "select id from public.meta_ads where id = any(%s)",
            (list(wanted),),
        ):
            known.add(found["id"])

    missing = sorted(wanted - known)
    rows = [r for r in rows if r.get("ad_id") in known]
    if not rows:
        return {"rows": 0, "skipped_unknown_ad": len(missing),
                "skipped_ad_ids": missing[:20]}

    async with cursor() as cur:
        for row in rows:
            await cur.execute(
                "insert into public.meta_ad_insights "
                "  (ad_id, date, brand_id, account_id, impressions, reach, "
                "   frequency, clicks, inline_link_clicks, ctr, "
                "   inline_link_click_ctr, cpc, cpm, spend, currency, "
                "   leads, landing_page_views, booked, cost_per_lead, "
                "   actions, cost_per_action, quality_ranking, "
                "   engagement_rate_ranking, conversion_rate_ranking, raw, "
                "   pull_id, pulled_at) "
                "values "
                "  (%(ad_id)s, %(date)s, %(brand_id)s, %(account_id)s, "
                "   %(impressions)s, %(reach)s, %(frequency)s, %(clicks)s, "
                "   %(inline_link_clicks)s, %(ctr)s, "
                "   %(inline_link_click_ctr)s, %(cpc)s, %(cpm)s, %(spend)s, "
                "   %(currency)s, %(leads)s, %(landing_page_views)s, "
                "   %(booked)s, %(cost_per_lead)s, %(actions)s::jsonb, "
                "   %(cost_per_action)s::jsonb, %(quality_ranking)s, "
                "   %(engagement_rate_ranking)s, "
                "   %(conversion_rate_ranking)s, %(raw)s::jsonb, "
                "   %(pull_id)s, now()) "
                "on conflict (ad_id, date) do update set "
                "  impressions = excluded.impressions, reach = excluded.reach, "
                "  frequency = excluded.frequency, clicks = excluded.clicks, "
                "  inline_link_clicks = excluded.inline_link_clicks, "
                "  ctr = excluded.ctr, "
                "  inline_link_click_ctr = excluded.inline_link_click_ctr, "
                "  cpc = excluded.cpc, cpm = excluded.cpm, "
                "  spend = excluded.spend, currency = excluded.currency, "
                "  leads = excluded.leads, "
                "  landing_page_views = excluded.landing_page_views, "
                "  booked = excluded.booked, "
                "  cost_per_lead = excluded.cost_per_lead, "
                "  actions = excluded.actions, "
                "  cost_per_action = excluded.cost_per_action, "
                "  quality_ranking = excluded.quality_ranking, "
                "  engagement_rate_ranking = excluded.engagement_rate_ranking, "
                "  conversion_rate_ranking = excluded.conversion_rate_ranking, "
                "  raw = excluded.raw, pull_id = excluded.pull_id, "
                "  pulled_at = now()",
                {
                    **row, "brand_id": brand_id, "account_id": account_id,
                    "pull_id": pull_id,
                    "actions": (json.dumps(row.get("actions"))
                               if row.get("actions") is not None else None),
                    "cost_per_action": (
                        json.dumps(row.get("cost_per_action"))
                        if row.get("cost_per_action") is not None else None),
                    "raw": json.dumps(row.get("raw")),
                })
    return {"rows": len(rows), "skipped_unknown_ad": len(missing),
            "skipped_ad_ids": missing[:20]}


# --------------------------------------------------------------------------
# Pull bookkeeping
# --------------------------------------------------------------------------

async def start_pull(*, brand_id: str, account_id: str, kind: str,
                     since: date | None, until: date | None,
                     started_by: str, api_version: str) -> int:
    row = await fetch_one(
        "insert into public.meta_pulls "
        "  (brand_id, account_id, kind, since, until, status, started_by, "
        "   api_version) "
        "values (%s, %s, %s, %s, %s, 'running', %s, %s) "
        "returning run_id",
        (brand_id, account_id, kind, since, until, started_by, api_version))
    return row["run_id"]


async def finish_pull(run_id: int, *, status: str, counts: dict,
                      error: str | None = None) -> None:
    async with cursor() as cur:
        await cur.execute(
            "update public.meta_pulls "
            "set status = %s, counts = %s::jsonb, error = %s, "
            "    finished_at = now() "
            "where run_id = %s",
            (status, json.dumps(counts), error, run_id))


async def close_stale_runs(older_than_hours: int = 2) -> list[dict]:
    """Close `running` rows that nothing is going to finish. Returns them.

    A pull writes its row at the start and closes it at the end, so a process
    that dies in between -- a terminal shut, a machine slept, a dropped
    database connection eighteen minutes in -- leaves a row that says `running`
    for ever. Two have said so since 2026-09-21.

    The cost is not the row. `intel status` reports them as recent failures,
    the dashboard's import card reads the newest as a pull in flight, and
    /refresh/status has to distinguish "running" from "stalled" by age because
    of them. They make a healthy account look permanently mid-import.

    TWO HOURS, matching scripts/sync.py's STALE_LOCK_AFTER. A pull of a normal
    window is minutes; past the window that lock would already have been broken
    as dead, and a row outliving its own lock is by definition abandoned.

    Marked `failed`, not deleted. What happened is that a run started and did
    not finish, which is a failure and is worth being able to count -- deleting
    it would make the ledger say the run never happened.
    """
    async with cursor() as cur:
        await cur.execute(
            "update public.meta_pulls "
            "   set status = 'failed', finished_at = now(), "
            "       error = %s "
            " where status = 'running' "
            "   and started_at < now() - make_interval(hours => %s) "
            "returning run_id, account_id, kind, started_at",
            (f"Closed by --close-stale-runs: still 'running' more than "
             f"{older_than_hours}h after it started, so the process that "
             f"opened it is gone. Whatever it imported before dying was "
             f"committed as it went and is still there.",
             older_than_hours))
        return await cur.fetchall()


async def recent_pulls(brand_id: str, limit: int = 6) -> list[dict]:
    """The last few runs, whatever they did. A failed run is the one worth
    seeing, so this does not filter on status."""
    return await fetch_all(
        "select run_id, account_id, kind, since, until, status, counts, "
        "       error, started_by, started_at, finished_at "
        "from public.meta_pulls where brand_id = %s "
        "order by started_at desc limit %s",
        (brand_id, limit))


async def overview_rows(brand_id: str, *, days: int = 30,
                        limit: int = 200) -> list[dict]:
    """Every imported ad for a brand, with its recent numbers and its latest
    review, biggest spender first.

    One query with two lateral joins rather than a fetch per ad: an account
    with two thousand ads would otherwise be two thousand round trips to
    render one page.

    cost_per_lead is computed here as spend/leads over the WINDOW, which is
    not the same as averaging the per-day `cost_per_lead` column -- an
    average of daily ratios weights a day with one lead as heavily as a day
    with fifty. NULL when there are no leads, for 042's reason: a zero reads
    as a free lead and sorts to the top of a cheapest-CPL column.
    """
    return await fetch_all(
        "select a.id, a.name, a.status, a.effective_status, a.is_dynamic, "
        "       a.needs_page_scope, a.campaign_asset_id, a.last_seen_at, "
        "       a.thumbnail_url, "
        "       mc.name as meta_campaign_name, "
        "       c.name as campaign_name, ca.variant, "
        "       perf.impressions, perf.clicks, perf.spend, perf.leads, "
        "       perf.days, "
        "       case when coalesce(perf.leads, 0) > 0 "
        "            then round(perf.spend / perf.leads, 2) end "
        "         as cost_per_lead, "
        "       r.overall, r.angle_observed, r.created_at as reviewed_at "
        "from public.meta_ads a "
        "left join public.meta_campaigns mc on mc.id = a.campaign_id "
        "left join public.campaign_assets ca on ca.id = a.campaign_asset_id "
        "left join public.campaigns c on c.id = ca.campaign_id "
        "left join lateral ( "
        "    select sum(i.impressions) as impressions, "
        "           sum(i.clicks) as clicks, sum(i.spend) as spend, "
        "           sum(i.leads) as leads, count(*) as days "
        "    from public.meta_ad_insights i "
        "    where i.ad_id = a.id "
        "      and i.date >= current_date - make_interval(days => %s) "
        ") perf on true "
        "left join lateral ( "
        "    select overall, angle_observed, created_at "
        "    from public.ad_reviews rr where rr.meta_ad_id = a.id "
        "    order by rr.created_at desc limit 1 "
        ") r on true "
        "where a.brand_id = %s "
        "order by coalesce(perf.spend, 0) desc, a.last_seen_at desc "
        "limit %s",
        (days, brand_id, limit))


async def last_successful_pull(account_id: str, kind: str) -> dict | None:
    """The watermark: the `ok` run of this kind that reached furthest forward.

    ORDERED BY `until`, NOT BY `finished_at`, and the difference is not cosmetic.

    `_pull_insights` takes this row's `until` minus the restatement days as the
    next window's start. Ordering by when a run FINISHED means a backfill run
    newest-first ends with its OLDEST chunk as the watermark -- so the next
    ordinary `--pull` asks for thirteen months through today, every night,
    against the rate limiter that is already the slowest part of the import.

    `until desc` asks the question that was always meant: how far forward have
    we successfully read? A backfill of older months then cannot walk the
    watermark backwards, whatever order its chunks happen to finish in.

    nulls last because a structure run has no window -- `until` is null on every
    one of them -- and Postgres sorts nulls FIRST under `desc`. Without it this
    would hand back a dateless structure row and quietly reset the insights
    window to the 30-day default on every pull.
    """
    return await fetch_one(
        "select since, until, finished_at from public.meta_pulls "
        "where account_id = %s and kind = %s and status = 'ok' "
        "order by until desc nulls last, finished_at desc limit 1",
        (account_id, kind))
