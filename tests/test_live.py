"""The live Graph reader. No network: httpx.MockTransport stands in for Meta.

The security properties are the reason this file exists. They were copied from
growth-engine's client.py rather than reasoned out again, and a copy is exactly
the kind of thing that drifts -- so each one is pinned here.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from intel import graph
from intel.live import _budget


def _client(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def _collect(g, act="act_1", **kw):
    return [row async for row in g.ads(act, **kw)]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def g(monkeypatch):
    """A Graph whose transport is a fake, with a token that must never leak."""
    def make(handler):
        client = graph.Graph(token="EAAtestSECRETtokenvalue1234567890")
        transport = _client(handler)

        async def _enter(self):
            self._client = httpx.AsyncClient(
                transport=transport, timeout=1.0,
                headers={"Authorization": f"Bearer {self._token}"})
            return self

        monkeypatch.setattr(graph.Graph, "__aenter__", _enter)
        return client
    return make


# ------------------------------------------------------------------ security

def test_the_token_goes_in_a_header_and_never_in_the_url(g):
    """One log line away from disclosure otherwise.

    growth-engine's client.py:12-21 is explicit: the moment the token is part
    of a URL it is one `log.info("GET %s", request.url)` from a file somebody
    is told to read.
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"data": []})

    async def go():
        async with g(handler) as client:
            await _collect(client)

    run(go())
    assert "EAAtest" not in seen["url"]
    assert "access_token" not in seen["url"]
    assert seen["auth"] == "Bearer EAAtestSECRETtokenvalue1234567890"


def test_paging_next_is_never_followed(g):
    """That URL embeds the token. The cursor is what gets reused."""
    urls = []
    pages = [
        {"data": [{"id": "1"}],
         "paging": {"next": "https://graph.facebook.com/x?access_token=EAAleak",
                    "cursors": {"after": "CURSOR2"}}},
        {"data": [{"id": "2"}], "paging": {}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=pages[len(urls) - 1])

    async def go():
        async with g(handler) as client:
            return await _collect(client)

    rows = run(go())
    assert [r["id"] for r in rows] == ["1", "2"]
    assert len(urls) == 2
    assert "access_token" not in urls[1]
    assert "after=CURSOR2" in urls[1]


def test_scrub_redacts_tokens_from_anything_raised_or_logged():
    assert "EAA" not in graph._scrub("failed with EAAabcdefghijklmnopqrstuvwxyz12345")
    assert "[redacted]" in graph._scrub("?access_token=secretvalue&x=1")


def test_an_error_body_carrying_a_token_is_scrubbed_before_it_is_raised(g):
    def handler(request):
        return httpx.Response(400, json={"error": {
            "code": 100,
            "message": "bad call with access_token=EAAsecretleak123"}})

    async def go():
        async with g(handler) as client:
            await _collect(client)

    with pytest.raises(graph.GraphError) as exc:
        run(go())
    assert "EAAsecretleak123" not in str(exc.value)


# ----------------------------------------------------------------- behaviour

def test_a_revoked_token_is_named_and_not_retried(g):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {
            "code": 190, "message": "Session revoked"}})

    async def go():
        async with g(handler) as client:
            await _collect(client)

    with pytest.raises(graph.TokenInvalid):
        run(go())
    # Retrying a revoked token just spends the rate limit learning it twice.
    assert len(calls) == 1


def test_throttling_retries_once_then_says_the_warehouse_is_fine(g, monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(400, json={"error": {
            "code": 17, "message": "User request limit reached"}})

    async def go():
        client = g(handler)
        client._sleep = lambda _s: asyncio.sleep(0)
        async with client as c:
            await _collect(c)

    with pytest.raises(graph.RateLimited) as exc:
        run(go())
    assert len(calls) == graph.TRIES
    # The distinction that stops somebody concluding the whole system is down.
    assert "warehouse is not affected" in str(exc.value)


def test_active_only_filters_server_side(g):
    seen = {}

    def handler(request):
        # The PARAMS, not the raw url -- effective_status is also one of the
        # requested fields, so a substring check on the url passes either way
        # and proves nothing.
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": []})

    async def go(active):
        async with g(handler) as client:
            await _collect(client, active_only=active)

    run(go(True))
    assert seen["params"].get("effective_status") == '["ACTIVE"]'

    run(go(False))
    assert "effective_status" not in seen["params"]


def test_pagination_stops_rather_than_becoming_an_import(g):
    """A live question should not silently turn into a full structure pull."""
    def handler(request):
        return httpx.Response(200, json={
            "data": [{"id": "x"}],
            "paging": {"next": "https://graph.facebook.com/next",
                       "cursors": {"after": "MORE"}}})

    async def go():
        async with g(handler) as client:
            return await _collect(client)

    rows = run(go())
    assert len(rows) == 10, "should stop at the page cap, not loop forever"


def test_no_token_refuses_at_construction_with_a_usable_sentence():
    with pytest.raises(graph.NotConfigured) as exc:
        graph.Graph(token="   ")
    message = str(exc.value)
    assert "META_ACCESS_TOKEN" in message
    assert "ads_read" in message
    # An install without it is not broken, and the message has to say so or
    # somebody will go looking for a fault that is not there.
    assert "works without it" in message


# -------------------------------------------------------------------- budget

def test_budgets_are_converted_from_minor_units():
    """Meta reports cents. A budget of 12000 beside a spend of 120.00 is the
    kind of thing somebody acts on before they notice."""
    assert _budget("12000") == 120.0
    assert _budget(5000) == 50.0
    assert _budget(None) is None
    assert _budget("") is None
    assert _budget("not a number") is None
