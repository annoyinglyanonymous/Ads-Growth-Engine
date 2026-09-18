"""meta_ads.store against the real database.

The upsert semantics matter more than any single value: 042's own comment
says a pull is idempotent because every structure table keys on the
platform's own id, and matching an imported ad back to `campaign_assets` is
the one thing this module does that `meta_ads.parse` cannot prove alone
(tests/test_meta_match.py already proves `match_asset` itself; this proves
the SQL around it wires the same round trip end to end).

ISOLATION
Fake `act_id`/ad/campaign ids so nothing here can collide with a real
account, and a full campaign chain built and torn down per test, the same
pattern tests/test_handoff.py uses and for the same reason: campaign_assets'
concept fkey has no ON DELETE CASCADE, so children are removed in dependency
order before the campaign itself.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import tracking
from db_meta import cursor, fetch_all, fetch_one
from meta_ads import store

DESTINATION = "https://renegadeinsurance.com/franchise"
BRAND_SLUG = "renegade"


async def _brand_id() -> str:
    row = await fetch_one(
        "select id from public.brands where slug = %s", (BRAND_SLUG,))
    return str(row["id"])


async def _build_chain(cur, suffix: str) -> tuple[str, str]:
    """A campaign with one approved meta_ads asset carrying a tracked_url.

    Returns (campaign_id, tracked_url).
    """
    await cur.execute(
        "insert into public.campaigns "
        "  (brand_id, product_id, name, status, objective, target_audience, "
        "   customer_problem, offer, primary_benefit, primary_cta, "
        "   primary_kpi, channels, created_by) "
        "select p.brand_id, p.id, %s, 'review', 'o', 'a', 'c', 'f', 'b', "
        "       'cta', 'kpi', array['meta_ads'], 'test' "
        "from public.products p where p.slug = 'franchise-program' "
        "returning id",
        (f"ZZ meta_ads store test {suffix}",))
    campaign_id = str((await cur.fetchone())["id"])

    await cur.execute(
        "insert into public.campaign_strategies "
        "  (campaign_id, version_number, status, core_message, approved_by, "
        "   approved_at) "
        "values (%s, 1, 'approved', 'm', 'test', now()) returning id",
        (campaign_id,))
    strategy_id = str((await cur.fetchone())["id"])

    await cur.execute(
        "insert into public.campaign_angles "
        "  (campaign_id, strategy_id, name, hypothesis, status, decided_by, "
        "   decided_at) "
        "values (%s, %s, 'n', 'h', 'approved', 'test', now()) returning id",
        (campaign_id, strategy_id))
    angle_id = str((await cur.fetchone())["id"])

    await cur.execute(
        "insert into public.creative_concepts "
        "  (campaign_id, angle_id, idea, hook, status, decided_by, "
        "   decided_at) "
        "values (%s, %s, 'i', 'h', 'approved', 'test', now()) returning id",
        (campaign_id, angle_id))
    concept_id = str((await cur.fetchone())["id"])

    tracked_url = tracking.tracked_url(
        DESTINATION, campaign_name=f"ZZ meta_ads store test {suffix}",
        channel="meta_ads", asset_type="meta_ad", variant="A",
        position=None, version=1)

    await cur.execute(
        "insert into public.campaign_assets "
        "  (campaign_id, concept_id, channel, asset_type, variant, "
        "   content, version_number, status, knowledge_snapshot, "
        "   approved_by, approved_at, tracked_url) "
        "values (%s, %s, 'meta_ads', 'meta_ad', 'A', %s, 1, 'approved', %s, "
        "        'test', now(), %s)",
        (campaign_id, concept_id,
         json.dumps({"headline": "h", "primary_text": "b",
                    "cta_label": "Learn More"}),
         json.dumps({"kb_chunk_ids": [], "claim_ids": [], "rule_ids": []}),
         tracked_url))

    return campaign_id, tracked_url


async def _drop_chain(cur, campaign_id: str) -> None:
    for table in ("campaign_assets", "creative_concepts", "campaign_angles",
                 "campaign_strategies"):
        await cur.execute(
            f"delete from public.{table} where campaign_id = %s",
            (campaign_id,))
    await cur.execute(
        "delete from public.campaigns where id = %s", (campaign_id,))


async def _drop_meta_rows(act_id: str) -> None:
    async with cursor() as cur:
        await cur.execute(
            "delete from public.meta_ad_insights where account_id = %s",
            (act_id,))
        await cur.execute(
            "delete from public.meta_ad_texts where ad_id in "
            "  (select id from public.meta_ads where account_id = %s)",
            (act_id,))
        await cur.execute(
            "delete from public.meta_ads where account_id = %s", (act_id,))
        await cur.execute(
            "delete from public.meta_adsets where account_id = %s",
            (act_id,))
        await cur.execute(
            "delete from public.meta_campaigns where account_id = %s",
            (act_id,))
        await cur.execute(
            "delete from public.meta_pulls where account_id = %s", (act_id,))
        await cur.execute(
            "delete from public.meta_ad_accounts where act_id = %s",
            (act_id,))


def _fake_act_id() -> str:
    """act_<digits>, guaranteed not to collide with a real account.

    Digits only: 042's own check constraint is `act_[0-9]+`, and a hex
    suffix (which can carry a-f) fails it exactly as a wrong real id would.
    """
    return "act_9" + str(uuid.uuid4().int)[:12]


async def _ensure_account(brand_id: str, act_id: str) -> dict:
    """`account_id` on every structure/insights table is a real foreign key
    to `meta_ad_accounts.act_id` (only that one, per 042's own comment) --
    so any test that upserts a campaign, ad set, ad or insight row needs the
    account to exist first."""
    return await store.add_account(
        brand_id=brand_id, act_id=act_id, label="Test account",
        added_by="cli:test")


# --------------------------------------------------------------------------
# Accounts
# --------------------------------------------------------------------------

@pytest.mark.dbtest
def test_adding_an_account_twice_reactivates_rather_than_fails(run_db):
    act_id = _fake_act_id()

    async def scenario():
        brand_id = await _brand_id()
        try:
            first = await store.add_account(
                brand_id=brand_id, act_id=act_id, label="First label",
                added_by="cli:test")
            second = await store.add_account(
                brand_id=brand_id, act_id=act_id, label=None,
                added_by="cli:test")
            return first, second
        finally:
            await _drop_meta_rows(act_id)

    first, second = run_db(scenario())
    assert first["act_id"] == second["act_id"] == act_id
    assert second["active"] is True
    # label=None on the second call does not blank out what was already
    # there -- coalesce(excluded.label, ...) in store.add_account.
    assert second["label"] == "First label"


@pytest.mark.dbtest
def test_accounts_for_filters_by_brand(run_db):
    act_id = _fake_act_id()

    async def scenario():
        brand_id = await _brand_id()
        try:
            await store.add_account(
                brand_id=brand_id, act_id=act_id, label="Isolation test",
                added_by="cli:test")
            mine = await store.accounts_for(brand_id)
            other = await fetch_one(
                "select id from public.brands where slug = 'agencyheight'")
            elsewhere = await store.accounts_for(str(other["id"]))
            return mine, elsewhere
        finally:
            await _drop_meta_rows(act_id)

    mine, elsewhere = run_db(scenario())
    assert act_id in [a["act_id"] for a in mine]
    assert act_id not in [a["act_id"] for a in elsewhere]


# --------------------------------------------------------------------------
# Structure: campaigns, adsets, idempotency
# --------------------------------------------------------------------------

@pytest.mark.dbtest
def test_upserting_the_same_campaign_twice_updates_rather_than_duplicates(
        run_db):
    act_id = _fake_act_id()
    campaign = {"id": "1002" + act_id[4:], "name": "First name",
               "status": "ACTIVE", "effective_status": "ACTIVE",
               "objective": "LEADS", "buying_type": "AUCTION",
               "daily_budget": None, "lifetime_budget": None,
               "spend_cap": None, "start_time": None, "stop_time": None,
               "created_time": None, "updated_time": None}

    async def scenario():
        brand_id = await _brand_id()
        try:
            await _ensure_account(brand_id, act_id)
            await store.upsert_campaigns(
                [campaign], brand_id=brand_id, account_id=act_id)
            renamed = {**campaign, "name": "Renamed"}
            await store.upsert_campaigns(
                [renamed], brand_id=brand_id, account_id=act_id)
            rows = await fetch_all(
                "select id, name from public.meta_campaigns "
                "where account_id = %s", (act_id,))
            return rows
        finally:
            await _drop_meta_rows(act_id)

    rows = run_db(scenario())
    assert len(rows) == 1, "a second upsert must update, not duplicate"
    assert rows[0]["name"] == "Renamed"


# --------------------------------------------------------------------------
# The join back to our own copy
# --------------------------------------------------------------------------

@pytest.mark.dbtest
def test_upsert_ad_matches_the_asset_its_link_is_tagged_with(run_db):
    """The round trip this whole importer exists for: an ad imported from
    Meta, whose destination link carries the utm values `tracking.py`
    stamped on our own approved copy, ends up pointing at that row."""
    act_id = _fake_act_id()
    suffix = uuid.uuid4().hex[:8]
    ad_id = "2001" + act_id[4:]

    async def scenario():
        brand_id = await _brand_id()
        campaign_id = None
        try:
            await _ensure_account(brand_id, act_id)
            async with cursor() as cur:
                campaign_id, tracked_url = await _build_chain(cur, suffix)

            raw_ad = {
                "id": ad_id, "adset_id": "3001" + act_id[4:],
                "campaign_id": "4001" + act_id[4:], "name": "Test ad",
                "status": "ACTIVE", "effective_status": "ACTIVE",
                "creative": {
                    "id": "5001" + act_id[4:],
                    "call_to_action_type": "LEARN_MORE",
                    "object_story_spec": {
                        "link_data": {
                            "link": tracked_url,
                            "name": "A headline",
                            "message": "A body",
                            "call_to_action": {
                                "type": "LEARN_MORE",
                                "value": {"link": tracked_url},
                            },
                        },
                    },
                },
            }

            candidates = await store.candidates_for_match(brand_id)
            result = await store.upsert_ad(
                raw_ad, brand_id=brand_id, account_id=act_id,
                candidates=candidates)

            stored = await fetch_one(
                "select campaign_asset_id, matched_at, utm_content "
                "from public.meta_ads where id = %s", (ad_id,))
            texts = await fetch_all(
                "select field, ordinal, text from public.meta_ad_texts "
                "where ad_id = %s order by field, ordinal", (ad_id,))
            asset = await fetch_one(
                "select id from public.campaign_assets "
                "where campaign_id = %s", (campaign_id,))
            return result, stored, texts, asset
        finally:
            await _drop_meta_rows(act_id)
            if campaign_id:
                async with cursor() as cur:
                    await _drop_chain(cur, campaign_id)

    result, stored, texts, asset = run_db(scenario())
    # headline, body and the cta type off the button -- creative_texts()
    # parses all three from link_data.
    assert result == {"matched": True, "texts": 3}
    assert stored["campaign_asset_id"] == asset["id"]
    assert stored["matched_at"] is not None
    assert {(t["field"], t["text"]) for t in texts} >= {
        ("headline", "A headline"), ("body", "A body")}


@pytest.mark.dbtest
def test_an_ad_with_no_matching_asset_is_imported_unmatched(run_db):
    """None is the ordinary answer, not a failure -- most ads in a real
    account predate this system or were written elsewhere."""
    act_id = _fake_act_id()
    ad_id = "2002" + act_id[4:]

    async def scenario():
        brand_id = await _brand_id()
        try:
            await _ensure_account(brand_id, act_id)
            raw_ad = {
                "id": ad_id, "adset_id": "3002" + act_id[4:],
                "campaign_id": "4002" + act_id[4:], "name": "Unmatched ad",
                "status": "ACTIVE", "effective_status": "ACTIVE",
                "creative": {
                    "id": "5002" + act_id[4:],
                    "object_story_spec": {
                        "link_data": {
                            "link": "https://example.com/no-utm-here",
                            "name": "Headline", "message": "Body",
                        },
                    },
                },
            }
            candidates = await store.candidates_for_match(brand_id)
            result = await store.upsert_ad(
                raw_ad, brand_id=brand_id, account_id=act_id,
                candidates=candidates)
            stored = await fetch_one(
                "select campaign_asset_id, matched_at "
                "from public.meta_ads where id = %s", (ad_id,))
            return result, stored
        finally:
            await _drop_meta_rows(act_id)

    result, stored = run_db(scenario())
    assert result["matched"] is False
    assert stored["campaign_asset_id"] is None
    assert stored["matched_at"] is None


@pytest.mark.dbtest
def test_replace_ad_texts_replaces_wholesale_not_merges(run_db):
    act_id = _fake_act_id()
    ad_id = "2003" + act_id[4:]

    async def scenario():
        brand_id = await _brand_id()
        try:
            await _ensure_account(brand_id, act_id)
            base = {"id": ad_id, "adset_id": "3003" + act_id[4:],
                   "campaign_id": "4003" + act_id[4:], "name": "n",
                   "status": "ACTIVE", "effective_status": "ACTIVE"}
            first = {**base, "creative": {
                "id": "5003" + act_id[4:],
                "object_story_spec": {"link_data": {
                    "name": "Old headline", "message": "Old body",
                    "child_attachments": [{"name": "Card one"}]}}}}
            await store.upsert_ad(first, brand_id=brand_id,
                                  account_id=act_id, candidates=[])

            second = {**base, "creative": {
                "id": "5003" + act_id[4:],
                "object_story_spec": {"link_data": {
                    "name": "New headline", "message": "New body"}}}}
            await store.upsert_ad(second, brand_id=brand_id,
                                  account_id=act_id, candidates=[])

            return await fetch_all(
                "select field, text from public.meta_ad_texts "
                "where ad_id = %s order by field", (ad_id,))
        finally:
            await _drop_meta_rows(act_id)

    texts = run_db(scenario())
    values = {(t["field"], t["text"]) for t in texts}
    assert values == {("headline", "New headline"), ("body", "New body")}
    assert not any(t["text"] == "Card one" for t in texts), (
        "the first creative's carousel card must not survive a replace")


# --------------------------------------------------------------------------
# Insights
# --------------------------------------------------------------------------

@pytest.mark.dbtest
def test_insights_upsert_on_ad_id_and_date_not_insert_twice(run_db):
    act_id = _fake_act_id()
    ad_id = "2004" + act_id[4:]
    day = date(2026, 9, 1)
    row = {
        "ad_id": ad_id, "date": day, "impressions": 100, "reach": 90,
        "frequency": None, "clicks": 5, "inline_link_clicks": 4,
        "ctr": None, "inline_link_click_ctr": None, "cpc": None,
        "cpm": None, "spend": None, "currency": "USD", "leads": 0,
        "landing_page_views": None, "booked": 0, "cost_per_lead": None,
        "actions": None, "cost_per_action": None, "quality_ranking": None,
        "engagement_rate_ranking": None, "conversion_rate_ranking": None,
        "raw": {"ad_id": ad_id},
    }

    async def scenario():
        brand_id = await _brand_id()
        try:
            await _ensure_account(brand_id, act_id)
            # meta_ad_insights.ad_id is a real foreign key to meta_ads.id.
            await store.upsert_ad(
                {"id": ad_id, "adset_id": "3004" + act_id[4:],
                 "campaign_id": "4004" + act_id[4:], "name": "n",
                 "status": "ACTIVE", "effective_status": "ACTIVE",
                 "creative": {}},
                brand_id=brand_id, account_id=act_id, candidates=[])
            run_id = await store.start_pull(
                brand_id=brand_id, account_id=act_id, kind="insights",
                since=day, until=day, started_by="cli:test",
                api_version="v26.0")
            await store.upsert_insights(
                [row], brand_id=brand_id, account_id=act_id, pull_id=run_id)
            restated = {**row, "impressions": 150}
            await store.upsert_insights(
                [restated], brand_id=brand_id, account_id=act_id,
                pull_id=run_id)
            return await fetch_all(
                "select ad_id, date, impressions from "
                "public.meta_ad_insights where ad_id = %s", (ad_id,))
        finally:
            await _drop_meta_rows(act_id)

    rows = run_db(scenario())
    assert len(rows) == 1, "one day, one ad, one row -- restated, not added"
    assert rows[0]["impressions"] == 150


# --------------------------------------------------------------------------
# Pull bookkeeping
# --------------------------------------------------------------------------

@pytest.mark.dbtest
def test_a_pull_lifecycle_from_running_to_ok_is_the_watermark(run_db):
    act_id = _fake_act_id()

    async def scenario():
        brand_id = await _brand_id()
        try:
            run_id = await store.start_pull(
                brand_id=brand_id, account_id=act_id, kind="structure",
                since=None, until=None, started_by="cli:test",
                api_version="v26.0")
            running = await fetch_one(
                "select status, finished_at from public.meta_pulls "
                "where run_id = %s", (run_id,))
            await store.finish_pull(run_id, status="ok",
                                    counts={"campaigns": 1})
            finished = await fetch_one(
                "select status, finished_at, counts from public.meta_pulls "
                "where run_id = %s", (run_id,))
            watermark = await store.last_successful_pull(act_id, "structure")
            return running, finished, watermark
        finally:
            await _drop_meta_rows(act_id)

    running, finished, watermark = run_db(scenario())
    assert running["status"] == "running" and running["finished_at"] is None
    assert finished["status"] == "ok" and finished["finished_at"] is not None
    assert finished["counts"] == {"campaigns": 1}
    assert watermark is not None


@pytest.mark.dbtest
def test_a_failed_pull_records_the_error_and_is_not_a_watermark(run_db):
    act_id = _fake_act_id()

    async def scenario():
        brand_id = await _brand_id()
        try:
            run_id = await store.start_pull(
                brand_id=brand_id, account_id=act_id, kind="insights",
                since=date(2026, 9, 1), until=date(2026, 9, 2),
                started_by="cli:test", api_version="v26.0")
            await store.finish_pull(run_id, status="failed", counts={},
                                    error="TokenInvalid: expired")
            row = await fetch_one(
                "select status, error from public.meta_pulls "
                "where run_id = %s", (run_id,))
            watermark = await store.last_successful_pull(act_id, "insights")
            return row, watermark
        finally:
            await _drop_meta_rows(act_id)

    row, watermark = run_db(scenario())
    assert row["status"] == "failed"
    assert "TokenInvalid" in row["error"]
    assert watermark is None, "a failed run must not become the watermark"


def test_an_orphaned_insights_row_does_not_cost_the_account_its_import(run_db):
    """The failure 046 exists to prevent.

    meta_ad_insights.ad_id is a hard foreign key and the whole batch runs on one
    pooled connection, so before the pre-filter a single parentless row aborted
    every insights row for the account and reported the run as `failed`.

    It is the normal case, not an edge one: GET /act_X/ads excludes deleted and
    archived ads by default, while /insights still reports the spend they made
    while they were alive. Any ad that ran in the window and has since been
    deleted arrives here with no parent.
    """
    act_id = _fake_act_id()
    known_ad = "5006" + act_id[4:]
    deleted_ad = "5007" + act_id[4:]
    day = date(2026, 9, 10)

    def _row(ad_id: str, impressions: int) -> dict:
        return {
            "ad_id": ad_id, "date": day, "impressions": impressions,
            "reach": impressions, "frequency": 1.0, "clicks": 3,
            "inline_link_clicks": 2, "ctr": 3.0, "inline_link_click_ctr": 2.0,
            "cpc": 1.0, "cpm": 10.0, "spend": 3.0, "currency": "USD",
            "leads": 0, "landing_page_views": 1, "booked": 0,
            "cost_per_lead": None, "actions": None, "cost_per_action": None,
            "quality_ranking": None, "engagement_rate_ranking": None,
            "conversion_rate_ranking": None, "raw": {"ad_id": ad_id},
        }

    async def scenario():
        brand_id = await _brand_id()
        try:
            await _ensure_account(brand_id, act_id)
            # Only ONE of the two ads exists. The other is the ad that was
            # deleted in Ads Manager after it had already spent.
            await store.upsert_ad(
                {"id": known_ad, "adset_id": "3006" + act_id[4:],
                 "campaign_id": "4006" + act_id[4:], "name": "still here",
                 "status": "ACTIVE", "effective_status": "ACTIVE",
                 "creative": {}},
                brand_id=brand_id, account_id=act_id, candidates=[])
            run_id = await store.start_pull(
                brand_id=brand_id, account_id=act_id, kind="insights",
                since=day, until=day, started_by="cli:test",
                api_version="v26.0")
            summary = await store.upsert_insights(
                [_row(known_ad, 100), _row(deleted_ad, 250)],
                brand_id=brand_id, account_id=act_id, pull_id=run_id)
            stored = await fetch_all(
                "select ad_id, impressions from public.meta_ad_insights "
                "where account_id = %s", (act_id,))
            return summary, stored
        finally:
            await _drop_meta_rows(act_id)

    summary, stored = run_db(scenario())

    # The survivor is written. Before the fix this list was empty.
    assert len(stored) == 1
    assert stored[0]["ad_id"] == known_ad
    assert stored[0]["impressions"] == 100

    # And the loss is reported rather than silently absorbed -- a quietly
    # smaller number reads as a quiet week.
    assert summary["rows"] == 1
    assert summary["skipped_unknown_ad"] == 1
    assert deleted_ad in summary["skipped_ad_ids"]
