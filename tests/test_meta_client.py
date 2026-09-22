"""meta_ads.client against httpx.MockTransport, with no network and no waits.

WHY THIS IS TESTABLE AT ALL
Two seams are injected for exactly this: `transport`, so every request is
answered by a committed fixture rather than by an account somebody has to own,
and `sleep`, so a back-off that would take three minutes of real time is a list
of numbers to assert on. A back-off tested for real is a test that gets
deleted, and then the retry path is the one piece of this client that only ever
runs in production.

THE TWO PROPERTIES WORTH FAILING A BUILD OVER
1. The next page is rebuilt from `paging.cursors.after`. Meta also returns
   `paging.next`, a ready-made URL with the access token in its query string,
   and following it is both the obvious implementation and a credential leak
   into logs/engine.log. ads_page_1.json carries a token-shaped value inside
   its `next` for that reason, and the tests below assert that string never
   reaches a request or a log line.
2. The last page still has a cursor. ads_page_2.json has `cursors.after` and no
   `next`, which is what Meta actually returns, and a loop that stopped only
   when the cursor vanished would page for ever over empty responses.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

import log as logmod
from config import settings
from meta_ads import client as graph_client
from meta_ads.client import (MAX_REDUCTIONS, TRIES, AccountUnavailable,
                             GraphClient, GraphError, NotConfigured,
                             RateLimited, TokenInvalid)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meta"

#: Token-shaped so the scrubbing and the "never in a URL" assertions are
#: testing the real pattern, and marked fake so scripts/scan_secrets.py reads
#: it as the fixture it is.
TOKEN = "EAAfakeTokenNotARealCredential0123456789"
VERSION = "v26.0"
BASE = "https://graph.facebook.test"
ACCOUNT = "act_1000000000001"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _usage_header(percent: int, *, regain: int | None = None) -> dict:
    entry = {"type": "ads_management", "call_count": percent,
             "total_cputime": 3, "total_time": 4}
    if regain is not None:
        entry["estimated_time_to_regain_access"] = regain
    return {"x-business-use-case-usage": json.dumps({"1000000000001": [entry]})}


class Recorder:
    """A MockTransport handler that answers in order and keeps the requests.

    An unexpected extra request raises rather than returning a default: a
    paging bug shows up as one request too many, and a transport that quietly
    answered it would turn an infinite loop into a passing test.
    """

    def __init__(self, *responses):
        self.queued = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.queued:
            raise AssertionError(
                f"unexpected request {len(self.requests)} to {request.url}")
        return self.queued.pop(0)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def param(self, index: int, name: str) -> str | None:
        return self.requests[index].url.params.get(name)


class Sleeps:
    """Stands in for asyncio.sleep and records what it was asked to wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _run(recorder: Recorder, sleeps: Sleeps, work, *, token: str = TOKEN):
    async def main():
        async with GraphClient(token, VERSION, transport=recorder.transport,
                               sleep=sleeps, base=BASE) as graph:
            return await work(graph)
    return asyncio.run(main())


def _collect(method, *args, **kwargs):
    async def work(graph):
        return [node async for node in getattr(graph, method)(*args, **kwargs)]
    return work


def _one(method, *args, **kwargs):
    async def work(graph):
        return await getattr(graph, method)(*args, **kwargs)
    return work


# --------------------------------------------------------------------------
# Not configured
# --------------------------------------------------------------------------

@pytest.mark.parametrize("token", ["", "   ", None])
def test_an_empty_token_refuses_before_any_request_is_attempted(token):
    """The refusal is at construction, so nothing opens a pull row first.

    A transport that raises proves the point: if a request were attempted the
    test would fail with the wrong exception.
    """
    def explode(request):                                  # pragma: no cover
        raise AssertionError("a request was made with no token")

    with pytest.raises(NotConfigured) as caught:
        GraphClient(token, VERSION, transport=httpx.MockTransport(explode))

    assert "META_ACCESS_TOKEN" in str(caught.value)


def test_not_configured_is_a_state_of_the_install_not_a_crash():
    """Same stance as handoff_webhook_url: catchable, named, and one sentence
    a person can act on."""
    assert issubclass(NotConfigured, graph_client.MetaError)


# --------------------------------------------------------------------------
# Paging
# --------------------------------------------------------------------------

def test_the_second_page_is_rebuilt_from_the_cursor_and_next_is_not_followed():
    recorder = Recorder(
        httpx.Response(200, json=_fixture("ads_page_1.json")),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    ads = _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert [ad["id"] for ad in ads] == ["120210000000000001",
                                        "120210000000000002",
                                        "120210000000000003",
                                        "120210000000000004"]
    assert len(recorder.requests) == 2

    cursor = _fixture("ads_page_1.json")["paging"]["cursors"]["after"]
    assert recorder.param(0, "after") is None
    assert recorder.param(1, "after") == cursor

    # The ready-made next URL was read for its existence and nothing else.
    next_url = _fixture("ads_page_1.json")["paging"]["next"]
    assert TOKEN in next_url, "the fixture is meant to carry a token"
    for request in recorder.requests:
        assert str(request.url) != next_url
        assert "access_token" not in str(request.url)
        assert TOKEN not in str(request.url)


def test_a_cursor_on_the_last_page_does_not_start_another_request():
    """ads_page_2.json has cursors.after and no next, which is what Meta
    returns. Stopping on the cursor alone would page for ever."""
    page_two = _fixture("ads_page_2.json")
    assert page_two["paging"]["cursors"]["after"]
    assert "next" not in page_two["paging"]

    recorder = Recorder(httpx.Response(200, json=page_two))
    ads = _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert len(ads) == 1
    assert len(recorder.requests) == 1


def test_an_empty_page_ends_the_walk_even_with_a_cursor_and_a_next():
    """The third stop condition: the same page again is how a cursor loop
    becomes infinite."""
    recorder = Recorder(httpx.Response(200, json={
        "data": [],
        "paging": {"cursors": {"after": "X"},
                   "next": f"{BASE}/{VERSION}/x?access_token={TOKEN}"}}))

    assert _run(recorder, Sleeps(), _collect("ads", ACCOUNT)) == []
    assert len(recorder.requests) == 1


# --------------------------------------------------------------------------
# The request itself
# --------------------------------------------------------------------------

def test_the_token_travels_in_a_header_and_never_in_the_url():
    recorder = Recorder(httpx.Response(200, json={"id": ACCOUNT,
                                                  "currency": "USD"}))
    _run(recorder, Sleeps(), _one("account", ACCOUNT))

    request = recorder.requests[0]
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(request.url)
    assert request.url.path == f"/{VERSION}/{ACCOUNT}"


def test_the_ad_request_asks_for_the_creative_subfields_and_the_watermark():
    """url_tags and asset_feed_spec only arrive inside a creative{...} block.

    Asking for a bare `creative` returns its id, which is one extra request
    per ad against the rate limit, and without url_tags a tagged account reads
    as untagged.
    """
    recorder = Recorder(httpx.Response(200, json={"data": []}))
    _run(recorder, Sleeps(),
         _collect("ads", ACCOUNT, updated_since=1757894400))

    fields = recorder.param(0, "fields")
    assert "creative{" in fields
    assert "url_tags" in fields
    assert "asset_feed_spec" in fields
    assert "object_story_spec" in fields
    assert recorder.param(0, "updated_since") == "1757894400"


def test_a_naive_watermark_is_read_as_utc_rather_than_local_time():
    """Reading it as local time moves the watermark by the operator's offset,
    and the symptom is ads edited inside that window missing from the pull."""
    from datetime import datetime, timezone

    naive = datetime(2026, 9, 15, 0, 0, 0)
    aware = datetime(2026, 9, 15, 0, 0, 0, tzinfo=timezone.utc)

    assert graph_client._unix(naive) == graph_client._unix(aware)


def test_insights_are_asked_for_per_ad_per_day():
    """Neither of these has a safe default: without level=ad Meta aggregates
    to the account, and without time_increment=1 it returns one lifetime total
    that cannot be re-cut for a window."""
    from datetime import date

    recorder = Recorder(httpx.Response(200,
                                       json=_fixture("insights_page.json")))
    rows = _run(recorder, Sleeps(),
                _collect("insights", ACCOUNT, since=date(2026, 9, 1),
                         until="2026-09-14"))

    assert len(rows) == 2
    assert recorder.param(0, "level") == "ad"
    assert recorder.param(0, "time_increment") == "1"
    assert json.loads(recorder.param(0, "time_range")) == {
        "since": "2026-09-01", "until": "2026-09-14"}
    for field in ("quality_ranking", "engagement_rate_ranking",
                  "conversion_rate_ranking", "actions", "spend"):
        assert field in recorder.param(0, "fields")


def test_an_account_id_that_is_not_one_is_refused_before_the_request():
    """act_id is interpolated into a URL path, and a value with a slash in it
    would address a different node entirely."""
    recorder = Recorder()

    with pytest.raises(ValueError):
        _run(recorder, Sleeps(), _one("account", "123/me"))
    assert recorder.requests == []


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------

def test_code_190_names_the_environment_variable_and_is_never_retried():
    """Every account would fail identically, so a retry only spends time.

    The message names META_ACCESS_TOKEN because the fix is a new token, not a
    different account and not a later run.
    """
    recorder = Recorder(httpx.Response(401, json=_fixture("error_190.json")))
    sleeps = Sleeps()

    with pytest.raises(TokenInvalid) as caught:
        _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert "META_ACCESS_TOKEN" in str(caught.value)
    assert len(recorder.requests) == 1
    assert sleeps.waits == []


@pytest.mark.parametrize("code", [100, 200, 272])
def test_an_unreadable_account_raises_its_own_type_so_the_next_one_can_run(
        code):
    """One brand's revoked assignment must not cost the other its import."""
    recorder = Recorder(httpx.Response(400, json={"error": {
        "message": "Unsupported get request", "code": code,
        "type": "GraphMethodException"}}))

    with pytest.raises(AccountUnavailable):
        _run(recorder, Sleeps(), _collect("ads", ACCOUNT))
    assert len(recorder.requests) == 1


def test_a_rate_limit_waits_as_long_as_meta_said_and_then_carries_on():
    """estimated_time_to_regain_access is in minutes and it beats the guess.

    Retrying after 60 seconds against a block Meta has costed at two minutes
    spends the remaining attempts learning what the header already said.
    """
    recorder = Recorder(
        httpx.Response(429, json=_fixture("error_17.json"),
                       headers=_usage_header(100, regain=2)),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    sleeps = Sleeps()
    ads = _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == [120.0]
    assert [ad["id"] for ad in ads] == ["120210000000000004"]


def test_without_an_estimate_the_wait_starts_at_sixty_and_doubles():
    recorder = Recorder(
        httpx.Response(429, json=_fixture("error_17.json")),
        httpx.Response(429, json=_fixture("error_17.json")),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    sleeps = Sleeps()
    _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == [60.0, 120.0]


def test_three_refusals_fail_the_pull_rather_than_hammering_the_account():
    """Carrying on past the third attempt pushes the account into a longer
    block, which costs the next pull as well as this one."""
    recorder = Recorder(*[httpx.Response(429, json=_fixture("error_17.json"))
                          for _ in range(3)])
    sleeps = Sleeps()

    with pytest.raises(RateLimited):
        _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert len(recorder.requests) == 3
    assert sleeps.waits == [60.0, 120.0]


def test_an_unknown_code_is_a_graph_error_carrying_the_code():
    recorder = Recorder(httpx.Response(500, json={"error": {
        "message": "An unknown error occurred", "code": 1,
        "type": "OAuthException"}}))

    with pytest.raises(GraphError) as caught:
        _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert caught.value.code == 1
    assert len(recorder.requests) == 1


def test_a_200_that_is_not_a_json_object_is_a_failure_not_an_empty_import():
    """Returning {} here would import nothing and report success, which is the
    one outcome nobody would investigate."""
    recorder = Recorder(httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(GraphError):
        _run(recorder, Sleeps(), _collect("ads", ACCOUNT))


# --------------------------------------------------------------------------
# The usage header
# --------------------------------------------------------------------------

def test_the_client_pauses_when_the_hour_s_budget_is_nearly_spent():
    """At 100 Meta blocks the account and the rest of the pull is lost; at 90
    a pause costs minutes."""
    recorder = Recorder(
        httpx.Response(200, json=_fixture("ads_page_1.json"),
                       headers=_usage_header(92)),
        httpx.Response(200, json=_fixture("ads_page_2.json"),
                       headers=_usage_header(12)),
    )
    sleeps = Sleeps()
    _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == [graph_client.BACKOFF_SECONDS]


def test_a_comfortable_usage_header_costs_nothing():
    recorder = Recorder(httpx.Response(200, json=_fixture("ads_page_2.json"),
                                       headers=_usage_header(12)))
    sleeps = Sleeps()
    _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == []


def test_any_one_counter_at_the_threshold_is_enough():
    """An average would report a comfortable 40 while the CPU counter was
    about to cost the pull an hour."""
    assert graph_client._peak([{"call_count": 10, "total_cputime": 95,
                                "total_time": 4}]) == 95


def test_a_malformed_usage_header_is_ignored_rather_than_fatal():
    """A pull must not fail over a courtesy header it does not need."""
    recorder = Recorder(httpx.Response(
        200, json=_fixture("ads_page_2.json"),
        headers={"x-business-use-case-usage": "not json at all"}))

    assert len(_run(recorder, Sleeps(), _collect("ads", ACCOUNT))) == 1


# --------------------------------------------------------------------------
# The token never reaches the log
# --------------------------------------------------------------------------

@pytest.fixture
def engine_log(tmp_path, monkeypatch):
    """Point the 'engine' logger at a file this test can read back.

    log.get() configures one handler on first use and caches that, so the
    module has to be reset for the redirect to take. Same dance as
    tests/test_log.py, and it is put back afterwards.
    """
    def reset():
        engine = logging.getLogger("engine")
        for handler in list(engine.handlers):
            engine.removeHandler(handler)
            handler.close()
        logmod._configured = False

    path = tmp_path / "engine.log"
    monkeypatch.setattr(settings, "log_file", str(path))
    reset()
    logmod.get("meta_ads.client")
    try:
        yield path
    finally:
        for handler in logging.getLogger("engine").handlers:
            handler.flush()
        reset()


def _log_text(path: Path) -> str:
    for handler in logging.getLogger("engine").handlers:
        handler.flush()
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_the_token_never_appears_in_the_log_however_the_request_went(
        engine_log, caplog):
    """CLAUDE.md sends a person to logs/engine.log to find out what happened.

    A credential in a file somebody is told to read has been disclosed, so
    this walks the paths that log: a successful paged pull whose `paging.next`
    carries the token, a rate limit, a pause, and an error message in which
    Meta has echoed the request URL back.
    """
    engine = logging.getLogger("engine")
    engine.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger="engine")
    try:
        recorder = Recorder(
            httpx.Response(200, json=_fixture("ads_page_1.json"),
                           headers=_usage_header(95)),
            httpx.Response(429, json=_fixture("error_17.json")),
            httpx.Response(200, json=_fixture("ads_page_2.json")),
        )
        _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

        echoed = Recorder(httpx.Response(400, json={"error": {
            "message": (f"Invalid request: GET /{VERSION}/{ACCOUNT}/ads"
                        f"?access_token={TOKEN}&limit=100"),
            "code": 2500, "type": "OAuthException"}}))
        with pytest.raises(GraphError) as caught:
            _run(echoed, Sleeps(), _collect("ads", ACCOUNT))
    finally:
        engine.removeHandler(caplog.handler)

    # The exception a person will read, and the file they will open.
    assert TOKEN not in str(caught.value)
    assert "access_token=<token>" in str(caught.value)

    written = _log_text(engine_log)
    assert written, "nothing was logged, so this test proved nothing"
    assert TOKEN not in written
    assert TOKEN not in caplog.text
    assert "access_token=" not in written.replace("access_token=<token>", "")


def test_the_scrubber_catches_a_token_shape_on_its_own():
    """Not only the access_token= spelling: Meta returns bare tokens inside
    `paging.next` and inside some error messages."""
    assert TOKEN not in graph_client._scrub(f"paging.next={TOKEN}&x=1")
    assert graph_client._scrub("nothing here") == "nothing here"


# ---------------------------------------------------------------------------
# Transient errors. Meta says "retry later"; the client used not to.
# ---------------------------------------------------------------------------

def _transient(code: int = 2) -> dict:
    return {"error": {"message": "An unexpected error has occurred. "
                                 "Please retry your request later.",
                      "type": "OAuthException", "code": code}}


def test_a_transient_error_is_retried_rather_than_losing_the_pull():
    """Code 2 used to raise on the spot, and on a large account that threw
    away everything paginated so far.

    Observed on act_153704749222533: a structure pull ran for eight minutes
    through two hundred campaigns, hit one code 2 near the end, and imported
    nothing at all. Meta's own message for this code asks you to retry.
    """
    recorder = Recorder(
        httpx.Response(500, json=_transient(2)),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    sleeps = Sleeps()
    ads = _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert [ad["id"] for ad in ads] == ["120210000000000004"]
    assert sleeps.waits == [5.0], (
        "a transient blip should wait seconds, not the rate limiter's minute")


def test_a_transient_error_backs_off_but_stays_bounded():
    """Three attempts and a clear message, not an unbounded loop -- if Meta is
    genuinely down, a job that never returns is worse than one that fails."""
    recorder = Recorder(*[httpx.Response(500, json=_transient(2))
                          for _ in range(3)])
    sleeps = Sleeps()
    with pytest.raises(GraphError) as exc:
        _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == [5.0, 10.0]
    assert "after 3 attempts" in str(exc.value), (
        "the message must distinguish one blip from sustained downtime")


def test_a_permission_refusal_is_still_never_retried():
    """The new retry path must not swallow the errors that are answers.

    A 200 is a fact about the account, not weather: retrying it three times
    delays a message somebody needs to act on.
    """
    recorder = Recorder(
        httpx.Response(400, json={"error": {"message": "not granted",
                                            "type": "OAuthException",
                                            "code": 200}}),
    )
    sleeps = Sleeps()
    with pytest.raises(AccountUnavailable):
        _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == []


def test_code_1_is_still_not_retried():
    """Guards the boundary of TRANSIENT_CODES from the obvious "well, 1 looks
    transient too" edit.

    Meta documents code 1 as "API Unknown", which is a permanently malformed
    request about as often as it is weather. Retrying it turns one fast, clear
    failure into three slow ones. The evidence for retrying was specific to
    code 2 and so is the set.
    """
    recorder = Recorder(httpx.Response(500, json=_transient(1)))
    sleeps = Sleeps()
    with pytest.raises(GraphError):
        _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert sleeps.waits == []
    assert len(recorder.requests) == 1


# ---------------------------------------------------------------------------
# "Please reduce the amount of data." A size to discover, not weather.
# ---------------------------------------------------------------------------

def _too_much() -> dict:
    return {"error": {"message": "Please reduce the amount of data you're "
                                 "asking for, then retry your request",
                      "type": "OAuthException", "code": 1}}


def test_too_much_data_halves_the_page_and_retries():
    """Observed on act_153704749222533: page one serves at 100 and something
    deeper in two hundred campaigns does not. The weight of an ads page is a
    property of the creatives on it, so the size cannot be chosen in advance --
    only discovered."""
    recorder = Recorder(
        httpx.Response(400, json=_too_much()),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    sleeps = Sleeps()
    ads = _run(recorder, sleeps, _collect("ads", ACCOUNT))

    assert [ad["id"] for ad in ads] == ["120210000000000004"]
    assert recorder.param(0, "limit") == "100"
    assert recorder.param(1, "limit") == "50", "the retry must be smaller"
    assert sleeps.waits == [], "nothing is throttled; sleeping here is dead time"


def test_the_smaller_page_size_is_kept_for_the_rest_of_the_pull():
    """Re-learning the size on every page would pay the refusal once per page
    for the whole account."""
    recorder = Recorder(
        httpx.Response(400, json=_too_much()),
        httpx.Response(200, json=_fixture("ads_page_1.json")),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert recorder.param(1, "limit") == "50"
    assert recorder.param(2, "limit") == "50", (
        "page two went back to the size that had already been refused")


def test_reductions_do_not_spend_the_retry_budget():
    """A reduction is a different request, not a failed attempt. Charging it
    against TRIES would leave nothing for the rate limit that follows it."""
    recorder = Recorder(
        httpx.Response(400, json=_too_much()),
        httpx.Response(400, json=_too_much()),
        httpx.Response(400, json=_too_much()),
        httpx.Response(200, json=_fixture("ads_page_2.json")),
    )
    ads = _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert [ad["id"] for ad in ads] == ["120210000000000004"]
    assert [recorder.param(i, "limit") for i in range(4)] == \
        ["100", "50", "25", "12"]


def test_halving_stops_rather_than_shrinking_for_ever():
    """Below MIN_PAGE_SIZE the page is not the problem, and more halving turns
    one clear failure into a slower one."""
    recorder = Recorder(*[httpx.Response(400, json=_too_much())
                          for _ in range(8)])
    with pytest.raises(GraphError):
        _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert len(recorder.requests) <= 1 + MAX_REDUCTIONS + TRIES


# ---------------------------------------------------------------------------
# Two passes over /ads: the cheap one for status, the heavy one for creatives.
#
# The structure phase has never once completed on act_153704749222533, and the
# reason is the shape of the request rather than the network: 207 of its 711
# ads are ACTIVE and 465 have never spent a cent, so an unrestricted crawl
# expands creatives for hundreds of ads nobody will read. These assert that the
# two calls really are different requests, because a "cheap" pass that quietly
# carries AD_FIELDS is just the expensive one again.
# ---------------------------------------------------------------------------

def test_the_cheap_pass_asks_for_no_creative():
    from meta_ads.client import AD_FIELDS, AD_STATUS_FIELDS

    assert "creative" in AD_FIELDS, "the heavy pass must still fetch creatives"
    assert "creative" not in AD_STATUS_FIELDS, (
        "AD_STATUS_FIELDS carries a creative expansion, which is the entire "
        "cost the cheap pass exists to avoid")
    assert "effective_status" in AD_STATUS_FIELDS, (
        "the cheap pass exists to correct effective_status; it has to ask "
        "for it")


def test_the_cheap_pass_sends_the_light_field_set():
    from meta_ads.client import AD_STATUS_FIELDS

    recorder = Recorder(httpx.Response(200, json={"data": [], "paging": {}}))
    _run(recorder, Sleeps(),
         _collect("ads", ACCOUNT, fields=AD_STATUS_FIELDS))

    assert recorder.param(0, "fields") == AD_STATUS_FIELDS
    assert recorder.param(0, "effective_status") is None


def test_the_heavy_pass_restricts_to_the_statuses_it_was_given():
    """Meta wants a JSON array here. A bare repeated parameter is accepted and
    ignored, which would silently crawl the whole account again."""
    recorder = Recorder(httpx.Response(200, json={"data": [], "paging": {}}))
    _run(recorder, Sleeps(),
         _collect("ads", ACCOUNT, effective_status=("ACTIVE", "WITH_ISSUES")))

    sent = recorder.param(0, "effective_status")
    assert sent == '["ACTIVE", "WITH_ISSUES"]', sent
    assert "creative" in (recorder.param(0, "fields") or "")


def test_an_unfiltered_call_still_sends_no_status_filter():
    """The default has to stay "everything", so a caller that does not know
    about the filter cannot accidentally import only the live ads."""
    recorder = Recorder(httpx.Response(200, json={"data": [], "paging": {}}))
    _run(recorder, Sleeps(), _collect("ads", ACCOUNT))

    assert recorder.param(0, "effective_status") is None


def test_the_active_set_keeps_the_ads_worth_reading():
    """WITH_ISSUES and DISAPPROVED are ads somebody intended to run and whose
    copy is current -- a disapproved ad is one you are MORE likely to go and
    read. ARCHIVED and the paused states are not in the set: their copy cannot
    change, and whatever is in the warehouse for them is already correct."""
    from meta_ads.pull import ACTIVE_ENOUGH

    assert "ACTIVE" in ACTIVE_ENOUGH
    assert "WITH_ISSUES" in ACTIVE_ENOUGH
    assert "DISAPPROVED" in ACTIVE_ENOUGH
    for dead in ("ARCHIVED", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED"):
        assert dead not in ACTIVE_ENOUGH, (
            f"{dead} ads are re-fetched with full creatives for no gain")
