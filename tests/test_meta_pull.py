"""meta_ads.pull's orchestration: brand resolution, per-account isolation,
and the one failure that must NOT be treated per-account.

`meta_ads.client` is mocked out entirely here (a fake standing in for
`GraphClient`) -- the real Graph contract is `tests/test_meta_client.py`'s
job, and the real upsert semantics are `tests/test_meta_store.py`'s. This
file is only about what `run_pull` does with what the client and the store
hand back: does one account's failure reach the next account, and does a bad
token stop the whole run instead.

ISOLATION FROM THE REAL "renegade" BRAND
`run_pull` acts on every active account under a brand, and this machine's
database is live and shared with another session actively adding real
accounts to it. Running these against "renegade" would risk writing fake
rows onto a real account the moment one exists there. So every test here
gets its own throwaway brand, created and dropped per test.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import pytest

from db_meta import cursor, fetch_all, fetch_one
from meta_ads import pull as meta_pull
from meta_ads import store
from meta_ads.client import AccountUnavailable, NotConfigured, TokenInvalid


class FakeGraph:
    """Stands in for `meta_ads.client.GraphClient`.

    `responses` maps act_id -> either a plain result dict (success, empty
    structure/insights) or an exception instance to raise from `account()`,
    the first call `_pull_structure` makes -- which is all a test needs to
    prove isolation, since what happens after a raised MetaError is not this
    fake's job to simulate.
    """

    def __init__(self, responses: dict):
        self.version = "v26.0"
        self._responses = responses

    @classmethod
    def make(cls, responses: dict):
        def from_settings(**kwargs):
            return cls(responses)
        return from_settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def account(self, act_id):
        result = self._responses.get(act_id)
        if isinstance(result, Exception):
            raise result
        return result or {"currency": "USD", "timezone_name": "UTC"}

    async def campaigns(self, act_id):
        return
        yield  # pragma: no cover -- makes this an async generator

    async def adsets(self, act_id):
        return
        yield  # pragma: no cover

    async def ads(self, act_id, *, updated_since=None, fields=None,
                  effective_status=None):
        """Mirrors the real signature, including the two arguments the
        structure pull now passes.

        `_pull_structure` calls this edge TWICE -- once cheaply for status and
        once for creatives -- so a fake that accepts only the old keywords
        fails with a TypeError inside the phase, which every isolation test
        here then reads as the account having failed. Keeping the signature
        honest is what stops a fake from proving the wrong thing.
        """
        return
        yield  # pragma: no cover

    async def insights(self, act_id, *, since, until):
        return
        yield  # pragma: no cover


async def _brand(slug: str) -> str:
    row = await fetch_one(
        "insert into public.brands (name, slug, description, status) "
        "values (%s, %s, 'test fixture', 'active') "
        "on conflict (slug) do update set slug = excluded.slug "
        "returning id",
        (f"ZZ pull test {slug}", slug))
    return str(row["id"])


async def _drop_brand(slug: str) -> None:
    async with cursor() as cur:
        await cur.execute(
            "delete from public.meta_pulls where brand_id = "
            "  (select id from public.brands where slug = %s)", (slug,))
        await cur.execute(
            "delete from public.meta_ad_accounts where brand_id = "
            "  (select id from public.brands where slug = %s)", (slug,))
        await cur.execute("delete from public.brands where slug = %s",
                          (slug,))


def _fake_act_id() -> str:
    return "act_9" + str(uuid.uuid4().int)[:12]


def _ordered_act_ids() -> tuple[str, str]:
    """Two fake ids whose iteration order is known.

    `store.accounts_for` orders by `act_id` as TEXT, so two ids sharing a
    prefix sort by whatever random digits follow -- which made an earlier
    version of the token test pass or fail depending on the uuid it drew.
    Distinct leading digits make the order a fact rather than a coin toss.
    """
    tail = str(uuid.uuid4().int)[:11]
    return "act_1" + tail, "act_8" + tail


@pytest.mark.dbtest
def test_an_unknown_brand_is_refused_by_name(run_db):
    async def scenario():
        with pytest.raises(meta_pull.BrandNotFound) as caught:
            await meta_pull.run_pull(brand_slug="zz-no-such-brand",
                                     started_by="cli:test")
        return str(caught.value)

    message = run_db(scenario())
    assert "zz-no-such-brand" in message


@pytest.mark.dbtest
def test_a_brand_with_no_active_accounts_pulls_nothing(run_db, monkeypatch):
    slug = f"zz-pull-empty-{uuid.uuid4().hex[:8]}"

    async def scenario():
        await _brand(slug)
        monkeypatch.setattr(
            meta_pull, "GraphClient",
            type("GC", (), {"from_settings": staticmethod(
                FakeGraph.make({}))}))
        try:
            return await meta_pull.run_pull(brand_slug=slug,
                                            started_by="cli:test")
        finally:
            await _drop_brand(slug)

    assert run_db(scenario()) == []


@pytest.mark.dbtest
def test_a_missing_token_beats_an_empty_brand(run_db, monkeypatch):
    """The ordering bug this pins, caught by running the real CLI: with no
    token AND no accounts, `run_pull` counted the accounts first, returned
    [], and the command exited 0 printing `[]`. 042's header names that
    exact failure -- "a command that answered 'no ads' would be reporting a
    missing token as an empty account" -- so the token check has to come
    first even when there is provably nothing to pull.
    """
    slug = f"zz-pull-order-{uuid.uuid4().hex[:8]}"

    async def scenario():
        await _brand(slug)

        def _refuse(**kwargs):
            raise NotConfigured("META_ACCESS_TOKEN is not set")

        monkeypatch.setattr(meta_pull.GraphClient, "from_settings",
                            staticmethod(_refuse))
        try:
            with pytest.raises(NotConfigured):
                await meta_pull.run_pull(brand_slug=slug,
                                         started_by="cli:test")
        finally:
            await _drop_brand(slug)

    run_db(scenario())


@pytest.mark.dbtest
def test_an_empty_token_is_raised_not_reported_per_account(run_db,
                                                           monkeypatch):
    slug = f"zz-pull-noconfig-{uuid.uuid4().hex[:8]}"
    act_id = _fake_act_id()

    async def scenario():
        brand_id = await _brand(slug)
        await store.add_account(brand_id=brand_id, act_id=act_id,
                                label="t", added_by="cli:test")

        def _refuse(**kwargs):
            raise NotConfigured("META_ACCESS_TOKEN is not set")

        monkeypatch.setattr(meta_pull.GraphClient, "from_settings",
                            staticmethod(_refuse))
        try:
            with pytest.raises(NotConfigured):
                await meta_pull.run_pull(brand_slug=slug,
                                         started_by="cli:test")
        finally:
            await _drop_brand(slug)

    run_db(scenario())


@pytest.mark.dbtest
def test_one_accounts_failure_does_not_stop_the_next(run_db, monkeypatch):
    slug = f"zz-pull-isolation-{uuid.uuid4().hex[:8]}"
    bad, good = _fake_act_id(), _fake_act_id()

    async def scenario():
        brand_id = await _brand(slug)
        await store.add_account(brand_id=brand_id, act_id=bad, label="bad",
                                added_by="cli:test")
        await store.add_account(brand_id=brand_id, act_id=good,
                                label="good", added_by="cli:test")
        responses = {bad: AccountUnavailable("permissions removed")}
        monkeypatch.setattr(
            meta_pull, "GraphClient",
            type("GC", (), {"from_settings": staticmethod(
                FakeGraph.make(responses))}))
        try:
            return await meta_pull.run_pull(brand_slug=slug,
                                            started_by="cli:test")
        finally:
            await _drop_brand(slug)

    results = run_db(scenario())
    by_act = {r["act_id"]: r for r in results}
    assert by_act[bad]["ok"] is False
    assert "AccountUnavailable" in by_act[bad]["error"]
    assert by_act[good]["ok"] is True


@pytest.mark.dbtest
def test_an_invalid_token_stops_the_whole_run_not_just_one_account(
        run_db, monkeypatch):
    """042/client.py: every account fails code 190 identically, so the
    second account must never be tried."""
    slug = f"zz-pull-token-{uuid.uuid4().hex[:8]}"
    first, second = _ordered_act_ids()
    attempted: list[str] = []

    async def scenario():
        brand_id = await _brand(slug)
        await store.add_account(brand_id=brand_id, act_id=first, label="1",
                                added_by="cli:test")
        await store.add_account(brand_id=brand_id, act_id=second, label="2",
                                added_by="cli:test")

        class TrackingFakeGraph(FakeGraph):
            async def account(self, act_id):
                attempted.append(act_id)
                return await super().account(act_id)

        responses = {first: TokenInvalid("META_ACCESS_TOKEN is invalid")}
        monkeypatch.setattr(
            meta_pull, "GraphClient",
            type("GC", (), {"from_settings": staticmethod(
                TrackingFakeGraph.make(responses))}))
        try:
            with pytest.raises(TokenInvalid):
                await meta_pull.run_pull(brand_slug=slug,
                                         started_by="cli:test")
        finally:
            await _drop_brand(slug)

    run_db(scenario())
    assert attempted == [first], (
        "a second account must not be tried once the token itself failed")


# ---------------------------------------------------------------------------
# The insights window is chunked, and each chunk is written as it arrives.
# ---------------------------------------------------------------------------

def test_chunks_cover_the_window_exactly():
    """Contiguous, inclusive at both ends, no gaps and no overlaps.

    A gap loses a day silently -- it reads as a day nothing ran. An overlap is
    harmless to correctness (meta_ad_insights upserts on (ad_id, date)) but
    pays for the same day twice against a rate limiter this module is careful
    about.
    """
    from meta_ads.pull import _chunks

    spans = _chunks(date(2026, 8, 22), date(2026, 9, 20), 7)
    assert spans[0][0] == date(2026, 8, 22)
    assert spans[-1][1] == date(2026, 9, 20)
    for earlier, later in zip(spans, spans[1:]):
        assert (later[0] - earlier[1]).days == 1, "gap or overlap between chunks"
    # Every day in the window appears exactly once.
    days = [d for s, u in spans
            for d in [s + timedelta(days=i) for i in range((u - s).days + 1)]]
    assert len(days) == len(set(days)) == 30


def test_a_window_shorter_than_a_chunk_is_one_request():
    from meta_ads.pull import _chunks

    assert _chunks(date(2026, 9, 1), date(2026, 9, 1), 7) == [
        (date(2026, 9, 1), date(2026, 9, 1))]
    assert len(_chunks(date(2026, 9, 1), date(2026, 9, 3), 7)) == 1


def test_chunking_is_why_a_big_window_does_not_go_in_one_request():
    """`level=ad` with `time_increment=1` makes Meta compute one row per ad per
    day for the whole time_range before returning page one. The page-size
    halving in client.py does not help: `limit` governs rows returned, not rows
    computed. A too-expensive query needs a smaller window.
    """
    from meta_ads.pull import _chunks

    # A 13-month backfill is the case that would certainly time out whole.
    spans = _chunks(date(2025, 8, 1), date(2026, 9, 1), 7)
    assert len(spans) > 50
    assert all((u - s).days + 1 <= 7 for s, u in spans)


# ---------------------------------------------------------------------------
# The window ends on the ACCOUNT's day, not on UTC's.
# ---------------------------------------------------------------------------

def test_the_window_ends_on_the_accounts_own_day():
    """An insights date is a day in the ad account's timezone, not in UTC.

    Observed on 2026-09-22 at 05:35 UTC against act_153704749222533
    (America/Los_Angeles, where it was still 2026-09-21): the window ran to
    2026-09-22, Meta returned nothing for a day that had not started, and the
    watermark advanced past the data -- so the dashboard read "insights
    through 2026-09-22" over an empty day.

    Frozen rather than computed from `now()`: a test that recomputes both
    sides passes at every hour including the ones where the bug lives.
    """
    from unittest.mock import patch

    from meta_ads.pull import _account_today

    # 05:35 UTC on the 22nd is 22:35 on the 21st in Los Angeles.
    frozen = datetime(2026, 9, 22, 5, 35, tzinfo=timezone.utc)

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz else frozen

    with patch("meta_ads.pull.datetime", _FrozenDatetime):
        assert _account_today(
            {"timezone_name": "America/Los_Angeles"}) == date(2026, 9, 21)
        assert _account_today(
            {"timezone_name": "Asia/Tokyo"}) == date(2026, 9, 22)
        # No timezone means no structure pull has ever completed for the
        # account. UTC is the honest fallback, and it is what settled_through
        # does with the same missing value.
        assert _account_today({"timezone_name": None}) == date(2026, 9, 22)
        assert _account_today({}) == date(2026, 9, 22)
        # A name tzdata does not know is a packaging problem, not a reason to
        # import nothing.
        assert _account_today({"timezone_name": "Not/AZone"}) == date(2026, 9, 22)


def test_the_importer_and_settled_through_agree_on_the_restatement_horizon():
    """Two copies of the number 3, and nothing else makes them agree.

    `ads.settled_through()` subtracts 3 to declare which days can still move;
    `meta_ads/pull.py` re-reads that many days so the days declared unsettled
    are the days that actually get restated. Raise one without the other and
    the mismatch is silent: days stay flagged provisional that nothing ever
    revisits, or Meta is paid to recompute days that stopped moving.

    Same device as tests/test_tracking_is_a_faithful_copy.py -- the
    duplication is deliberate and this is what keeps it honest.
    """
    import re
    from pathlib import Path

    from meta_ads.pull import INSIGHTS_RESTATEMENT_DAYS

    sql = (Path(__file__).resolve().parent.parent
           / "migrations" / "003_ads_metrics.sql").read_text(encoding="utf-8")
    m = re.search(r"::date\)\s*-\s*(\d+)\)\s*\n\s*from public\.meta_ad_accounts",
                  sql)
    assert m, "could not find the subtraction in ads.settled_through()"
    assert int(m.group(1)) == INSIGHTS_RESTATEMENT_DAYS, (
        f"ads.settled_through() subtracts {m.group(1)} days but "
        f"INSIGHTS_RESTATEMENT_DAYS is {INSIGHTS_RESTATEMENT_DAYS}. The "
        f"importer must re-read at least what the metrics layer calls "
        f"unsettled.")


def test_the_structure_watermark_is_where_the_last_run_started():
    """An ad edited DURING a structure run has an updated_time before the run
    finished. Watermarking at the finish skips it forever; at the start it is
    read again next time."""
    src = (Path(__file__).resolve().parent.parent / "meta_ads" / "pull.py"
           ).read_text(encoding="utf-8")
    body = src.split("async def _pull_structure", 1)[1].split("\nasync def ", 1)[0]
    assert 'watermark.get("started_at")' in body
    store_src = (Path(__file__).resolve().parent.parent / "meta_ads" / "store.py"
                 ).read_text(encoding="utf-8")
    fn = store_src.split("async def last_successful_pull", 1)[1][:2000]
    assert "started_at" in fn


def _stub_run(monkeypatch, structure, insights):
    """run_pull with every dependency stubbed: no database, no Meta."""
    import asyncio as _a

    class GC:
        version = "v0"

        @staticmethod
        def from_settings():
            return GC()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    async def brand_id(slug):
        return "B"

    async def accounts_for(bid, active_only=True):
        return [{"act_id": "act_1"}]

    monkeypatch.setattr(meta_pull, "GraphClient", GC)
    monkeypatch.setattr(meta_pull, "_brand_id", brand_id)
    monkeypatch.setattr(meta_pull.store, "accounts_for", accounts_for)
    monkeypatch.setattr(meta_pull, "_pull_structure", structure)
    monkeypatch.setattr(meta_pull, "_pull_insights", insights)
    monkeypatch.setattr(meta_pull, "DB_RETRY_WAIT_SECONDS", 0)
    return _a.run(meta_pull.run_pull(brand_slug="x", started_by="t"))


def test_a_dropped_database_connection_is_retried(monkeypatch):
    import psycopg
    calls = {"structure": 0}

    async def structure(*a, **k):
        calls["structure"] += 1
        if calls["structure"] == 1:
            raise psycopg.OperationalError("server closed the connection")
        return {"ads": 3}

    async def insights(*a, **k):
        return {"rows": 10}

    [s] = _stub_run(monkeypatch, structure, insights)
    assert calls["structure"] == 2
    assert s["ok"] and s["structure"] == {"ads": 3} and s["insights"] == {"rows": 10}


def test_a_structure_that_keeps_losing_the_database_still_imports_numbers(
        monkeypatch):
    """What happened on 2026-09-25: the structure phase lost its connection
    and the exception left run_pull, so insights -- a different edge -- was
    never attempted and the day's numbers did not land."""
    import psycopg

    async def structure(*a, **k):
        raise psycopg.OperationalError("server closed the connection\nmore")

    async def insights(*a, **k):
        return {"rows": 10}

    [s] = _stub_run(monkeypatch, structure, insights)
    assert s["ok"] is False
    assert s["insights"] == {"rows": 10}
    assert "database connection lost 2 times" in s["error"]
    assert "\n" not in s["error"]
