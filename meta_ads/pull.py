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

from datetime import date, datetime, timedelta, timezone

from meta_ads import parse, store
from meta_ads.client import GraphClient, MetaError, TokenInvalid

#: On a first pull there is no watermark. Meta restates the last few days of
#: a window as attribution catches up, so even a re-run pulls a short
#: lookback rather than exactly "since the last run" -- see 042's comment on
#: why `meta_ad_insights` upserts on (ad_id, date) instead of only inserting.
INSIGHTS_LOOKBACK_DAYS = 30
INSIGHTS_RESTATEMENT_DAYS = 3


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
             "matched": 0}
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
        updated_since = watermark["finished_at"] if watermark else None

        candidates = await store.candidates_for_match(brand_id)
        async for ad in graph.ads(act_id, updated_since=updated_since):
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


async def _pull_insights(graph: GraphClient, account: dict, *,
                         brand_id: str, started_by: str, api_version: str,
                         since: date | None, until: date | None) -> dict:
    act_id = account["act_id"]
    today = datetime.now(timezone.utc).date()
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
        rows = []
        async for raw in graph.insights(act_id, since=window_since,
                                        until=window_until):
            rows.append(parse.insight_row(raw, currency=account.get(
                "currency")))
        # A dict since 046, not a bare count: an orphaned insights row -- an ad
        # that spent in the window and has since been deleted, so /ads no longer
        # returns it -- is skipped rather than aborting the account's whole
        # batch on the foreign key. The skip lands in meta_pulls.counts where
        # somebody can see it; a silently smaller number would read as a quiet
        # week.
        counts.update(await store.upsert_insights(
            rows, brand_id=brand_id, account_id=act_id, pull_id=run_id))
        await store.finish_pull(run_id, status="ok", counts=counts)
        return counts
    except Exception as exc:
        await store.finish_pull(
            run_id, status="failed", counts=counts, error=str(exc))
        raise


async def run_pull(*, brand_slug: str, started_by: str,
                   since: date | None = None,
                   until: date | None = None) -> list[dict]:
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
            try:
                summary["structure"] = await _pull_structure(
                    graph, account, brand_id=brand_id,
                    started_by=started_by, api_version=graph.version)
                summary["insights"] = await _pull_insights(
                    graph, account, brand_id=brand_id,
                    started_by=started_by, api_version=graph.version,
                    since=since, until=until)
            except TokenInvalid:
                raise
            except MetaError as exc:
                summary["ok"] = False
                summary["error"] = f"{type(exc).__name__}: {exc}"
            results.append(summary)
    return results
