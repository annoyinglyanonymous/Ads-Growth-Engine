"""The only thing in this package that talks to Meta, and the only thing that
holds the token.

    async with GraphClient.from_settings() as graph:
        async for ad in graph.ads("act_123"):
            ...

READ ONLY. Every method here is a GET. The System User token behind it carries
ads_read, so a write would be refused by Meta as well as absent from this file,
and that is the intended belt and braces.

THE TOKEN GOES IN A HEADER, NEVER IN A URL
Graph accepts `Authorization: Bearer <token>` and also accepts
`?access_token=<token>`, and the second one is how most examples are written.
It is the wrong choice here for one reason: the moment the token is part of a
URL it is one `log.info("GET %s", request.url)` away from `logs/engine.log`,
which CLAUDE.md sends a person to when they want to know what just happened. A
credential in a file somebody is told to read is a credential that has been
disclosed. With the token in a header there is no URL in this process that
carries it, and `_scrub` covers the case where Meta echoes one back inside an
error message.

`paging.next` IS NEVER FOLLOWED, FOR THE SAME REASON
Meta returns a ready-made URL for the next page and that URL embeds the access
token, because it is meant to be fetched by whatever fetched the first page.
Following it would undo the paragraph above on page two of every pull. So the
next request is rebuilt from `paging.cursors.after` instead, and `paging.next`
is read for exactly one bit of information, whether another page exists, and
never requested, never stored and never logged. That bit matters: Meta returns
a cursor on the LAST page as well, so a loop that stopped only when the cursor
disappeared would fetch empty pages for ever.

WHAT EACH FAILURE MEANS, BECAUSE THEY ARE NOT THE SAME FAILURE
  190           the token is invalid or expired. Nothing retries it: every
                account will fail the same way, and a personal token dying at
                sixty days is the expected cause, so the exception names the
                environment variable to fix rather than the account.
  4/17/32/613/  a rate limit, which is a wait and not an error. Meta says how
  80000/80004   long in `estimated_time_to_regain_access` when it knows;
                otherwise 60 seconds, doubling, three attempts, then the pull
                fails honestly rather than hammering an account into a longer
                block.
  100/200/272   this account is not readable by this token: not assigned to
                the System User, permissions removed, or an act_id that is not
                theirs. Raised as its own type so a run over several accounts
                carries on with the next one. One brand's revoked assignment
                must not cost the other brand its import.
  2             Meta's own "please retry later". Five seconds, doubling, three
                attempts. Retried where no other GraphError is, because a
                structure pull paginates for minutes and one blip near the end
                used to throw away the entire account's import.
  anything else GraphError, unretried, with the code in the message.

THE USAGE HEADER IS A WARNING SHOT AND IT IS WORTH TAKING
`x-business-use-case-usage` reports how much of the hour's budget this account
has spent, as percentages. At 100 Meta blocks the account for as much as an
hour, which costs the whole pull; at 90 there is still time to stop. So a
response whose counters have reached USAGE_PAUSE_AT makes the client sleep
before the next request rather than after the refusal.

`sleep` IS INJECTED, AND THAT IS WHAT MAKES ANY OF THIS TESTABLE
A back-off tested for real is a test that takes three minutes and gets
deleted. The tests pass a recorder in its place and assert on the waits.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, datetime, timezone

import httpx

from config import settings
from log import get as _get_logger

#: Same file every other seam writes to. A pull is long, unwatched and the kind
#: of thing that is asked about afterwards ("why is Tuesday missing"), which is
#: exactly what log.py exists for.
log = _get_logger("meta_ads.client")


# --------------------------------------------------------------------------
# What can go wrong
# --------------------------------------------------------------------------

class MetaError(RuntimeError):
    """Anything this client refuses to carry on past.

    One base so a caller that genuinely wants "the pull failed" can catch
    that, and five subclasses because the interesting cases each call for a
    different response: a caller that cannot tell them apart will treat a
    revoked account assignment as a dead token and stop importing the brand
    that was fine.
    """


class NotConfigured(MetaError):
    """No token. Not a fault: an install nobody has given one to yet."""


class TokenInvalid(MetaError):
    """Code 190. Every account will fail identically, so nothing retries."""


class AccountUnavailable(MetaError):
    """Codes 100/200/272. This account, not this run. Try the next one."""


class RateLimited(MetaError):
    """Throttled, and still throttled after the last back-off."""


class GraphError(MetaError):
    """A Graph error with no special handling. Carries the code."""

    def __init__(self, message: str, *, code: int = 0, subcode: int = 0):
        super().__init__(message)
        self.code = code
        self.subcode = subcode


#: Invalid or expired token. Never retried: see the module header.
TOKEN_CODES = frozenset({190})

#: Rate limiting, under its several names. 4 is the app-level limit, 17 the
#: user-level one, 32 the page-level one, 613 the generic "calls to this API
#: have exceeded the rate limit", and the 80000 series the per-business-use-case
#: limits that the usage header counts against.
THROTTLE_CODES = frozenset({4, 17, 32, 613, 80000, 80004})

#: The account cannot be read with this token. 100 is "unsupported get request",
#: which is what an unassigned act_id returns, 200 is a permissions refusal and
#: 272 is the ads-specific one.
ACCOUNT_CODES = frozenset({100, 200, 272})

#: Meta having a moment. Code 2 is "API Service", and its own message is
#: literally "An unexpected error has occurred. Please retry your request
#: later."
#:
#: CODE 1 IS DELIBERATELY NOT IN HERE. Meta documents it as "API Unknown" and
#: it is retryable about as often as it is a permanently malformed request.
#: test_an_unknown_code_is_a_graph_error_carrying_the_code asserts it reaches
#: the caller on the first attempt, which was a decision somebody made before
#: this set existed. The evidence for retrying is specific to code 2 (run 218,
#: below), so the set is too -- widening it to "errors that felt transient"
#: would turn a fast, clear failure into three slow ones.
#:
#: Retried, where every other GraphError is not, because of what one of these
#: costs on a large account. A structure pull of act_153704749222533 paginates
#: for EIGHT MINUTES through two hundred campaigns; run 218 spent all of it and
#: then hit a code 2 near the end, and the whole account's import was thrown
#: away -- not a page, all of it. Unretried, this is a scheduled job that
#: imports nothing on a random subset of nights and says only "unexpected
#: error", which is the invisible failure scripts/sync.py exists to prevent.
#:
#: Still bounded by TRIES. If Meta is genuinely down, three attempts and a
#: clear message beats a job that never returns.
TRANSIENT_CODES = frozenset({2})

#: Transient errors clear in seconds. A rate limit needs BACKOFF_SECONDS
#: because the usage budget has to refill, but waiting a minute on a blip
#: would turn every one of them into a minute of dead time mid-pagination.
TRANSIENT_BACKOFF_SECONDS = 5.0

#: "Please reduce the amount of data you're asking for, then retry your
#: request." Code 1 again -- but this one is an instruction, not weather, and
#: the difference is why TRANSIENT_CODES does not contain 1: retrying THIS
#: unchanged fails identically for ever, and run 220 is what that looks like.
#:
#: An ads page carries a full nested creative per row, so its weight depends on
#: the ads rather than on the count, and PAGE_SIZE cannot be chosen correctly in
#: advance. act_153704749222533 serves page one at 100 happily and then refuses
#: somewhere deeper in two hundred campaigns' worth. So the size is not
#: configured, it is discovered: halve on refusal, keep the smaller size for the
#: rest of the pull, and never sleep -- nothing here is throttled and the next
#: request is genuinely different from the last.
REDUCE_MARKER = "reduce the amount of data"

#: Four halvings from 100 reaches 6. Below that the page is not the problem and
#: more halving only turns one clear failure into a slower one.
MAX_REDUCTIONS = 4

#: A page of one still has to work. If Meta refuses this, the account has a
#: single ad whose creative it cannot serve, which is a fact to report rather
#: than a size to keep shrinking.
MIN_PAGE_SIZE = 5

#: Per-account budget spent this hour, as percentages, in a JSON header.
USAGE_HEADER = "x-business-use-case-usage"

#: The three counters Meta reports, any one of which can block the account.
USAGE_COUNTERS = ("call_count", "total_cputime", "total_time")

#: Stop at 90 rather than at 100. At 100 the account is already blocked and the
#: remaining pages of this pull are lost; at 90 a pause costs minutes.
USAGE_PAUSE_AT = 90

#: First wait when Meta does not say how long to wait for.
BACKOFF_SECONDS = 60.0

#: Attempts, not retries: three requests, two waits (60s then 120s).
TRIES = 3

#: Rows per page. Small enough that an ads page carrying full creatives does
#: not time out, large enough that a month of daily insights is not fifty round
#: trips against the rate limit this file is careful about.
PAGE_SIZE = 100

_ACCOUNT_ID = re.compile(r"^act_[0-9]+$")

#: The shape of a Meta user access token, and the query parameter that carries
#: one. Both are scrubbed out of anything that reaches a log or an exception
#: message, because `paging.next` is not the only place Meta hands a token
#: back: several error messages quote the request URL.
_TOKEN_SHAPE = re.compile(r"EAA[A-Za-z0-9]{20,}")
_TOKEN_PARAM = re.compile(r"(?i)(access_token=)[^&\s\"']+")


# --------------------------------------------------------------------------
# Fields
#
# Spelled out rather than defaulted. Graph returns a handful of fields when
# asked for none, and the missing ones do not error, they are simply absent,
# which surfaces months later as a column that has always been null.
# --------------------------------------------------------------------------

ACCOUNT_FIELDS = ("id,account_id,name,currency,timezone_name,"
                  "account_status,business_name")

CAMPAIGN_FIELDS = ("id,name,status,effective_status,objective,buying_type,"
                   "daily_budget,lifetime_budget,spend_cap,start_time,"
                   "stop_time,created_time,updated_time")

ADSET_FIELDS = ("id,name,campaign_id,status,effective_status,"
                "optimization_goal,billing_event,bid_strategy,daily_budget,"
                "lifetime_budget,start_time,end_time,created_time,"
                "updated_time")

#: The creative is requested as a subfield block because that is the only way
#: to get `url_tags` and `asset_feed_spec` in the same call as the ad. Asking
#: for a bare `creative` returns its id and nothing else, which is one extra
#: request per ad against the rate limit, and url_tags is where most accounts
#: keep their utm values, so an import without it finds ad after ad with no utm
#: and concludes the account is untagged. See parse._with_tags.
AD_FIELDS = (
    "id,name,adset_id,campaign_id,status,effective_status,created_time,"
    "updated_time,creative{id,name,title,body,object_story_id,"
    "effective_object_story_id,object_story_spec,asset_feed_spec,url_tags,"
    "object_url,image_url,thumbnail_url,video_id,call_to_action_type}")

#: The three ranking fields are the reason this list is not shorter: they are
#: Meta's own grading of the ad against its competition, they exist nowhere
#: else, and they are what the review stage reads as evidence rather than
#: opinion.
INSIGHT_FIELDS = (
    "ad_id,ad_name,adset_id,campaign_id,date_start,date_stop,impressions,"
    "reach,frequency,clicks,inline_link_clicks,ctr,inline_link_click_ctr,"
    "cpc,cpm,spend,actions,cost_per_action_type,quality_ranking,"
    "engagement_rate_ranking,conversion_rate_ranking")


# --------------------------------------------------------------------------
# Small pure helpers
# --------------------------------------------------------------------------

def _scrub(text: str) -> str:
    """Anything token-shaped, replaced before it can be printed or logged."""
    return _TOKEN_SHAPE.sub("<token>",
                            _TOKEN_PARAM.sub(r"\1<token>", text or ""))


def _account_path(act_id: str, edge: str = "") -> str:
    """'act_123/ads', having refused anything that is not an account id.

    A ValueError and not a silent pass: act_id reaches here from a row she
    typed, it is interpolated into a URL path, and a value containing a slash
    would address a different node entirely. 042 puts the same check on the
    column; this is the half that runs before the request.
    """
    if not _ACCOUNT_ID.match(str(act_id or "")):
        raise ValueError(
            f"not a Meta ad account id: {act_id!r} (expected 'act_<digits>')")
    return f"{act_id}/{edge}" if edge else str(act_id)


def _unix(value: datetime | date | int | float) -> int:
    """A watermark as the Unix seconds `updated_since` wants.

    A naive datetime is read as UTC rather than as local time. Reading it as
    local time would move the watermark by the operator's offset, and the
    symptom is ads edited inside that window silently missing from the pull.
    datetime is tested before date because it is a subclass of it.
    """
    if isinstance(value, datetime):
        moment = (value if value.tzinfo
                  else value.replace(tzinfo=timezone.utc))
        return int(moment.timestamp())
    if isinstance(value, date):
        return int(datetime(value.year, value.month, value.day,
                            tzinfo=timezone.utc).timestamp())
    return int(value)


def _day(value: date | str) -> str:
    """'2026-09-15', whether a date or a string was handed over."""
    if isinstance(value, date):
        return value.isoformat()[:10]
    return str(value)[:10]


def _usage(headers) -> list[dict]:
    """The usage entries out of the header, or nothing.

    Nothing is a normal answer and not a failure: the header is absent on a
    cached response and on several error paths. A malformed one is also
    nothing, because a client that raised while parsing a courtesy header
    would fail a pull over a field it does not need.
    """
    try:
        parsed = json.loads(headers.get(USAGE_HEADER) or "{}")
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, dict):
        return []
    out: list[dict] = []
    for entries in parsed.values():
        for entry in entries or []:
            if isinstance(entry, dict):
                out.append(entry)
    return out


def _peak(usage: list[dict]) -> int:
    """The highest of the three counters across every entry.

    The highest and not the average: any one of them at 100 blocks the
    account, so an average would report a comfortable 40 while the CPU counter
    was about to cost the pull an hour.
    """
    peak = 0
    for entry in usage:
        for counter in USAGE_COUNTERS:
            try:
                peak = max(peak, int(entry.get(counter) or 0))
            except (TypeError, ValueError):
                continue
    return peak


def _estimate(usage: list[dict], error: dict | None) -> float | None:
    """Meta's own "wait this long", in seconds, or None if it did not say.

    Reported in MINUTES, in the usage header, and occasionally inside the
    error body as well. Taking Meta's number over the local back-off matters
    because the local one is a guess: retrying after 60 seconds against a
    block Meta has costed at 15 minutes spends the two remaining attempts
    learning what the header already said.
    """
    minutes = [entry.get("estimated_time_to_regain_access") for entry in usage]
    data = (error or {}).get("error_data")
    if isinstance(data, dict):
        minutes.append(data.get("estimated_time_to_regain_access"))
    for value in minutes:
        try:
            seconds = float(value) * 60.0
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    return None


def _int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# The client
# --------------------------------------------------------------------------

class GraphClient:
    """One account-reading session against one pinned Graph version."""

    def __init__(self, token: str, version: str, *,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 base: str | None = None,
                 timeout: float | None = None):
        """Refuses an empty token here, before any request exists.

        At construction and not at request time, because "not configured" is a
        state of the install rather than of a call: the CLI prints one sentence
        and exits 1, and /ads shows a card. Discovering it on the first request
        instead would mean a meta_pulls row opened for a pull that never had a
        chance, and the same sentence arriving as a failure.
        """
        if not (token or "").strip():
            raise NotConfigured(
                "META_ACCESS_TOKEN is not set, so there is nothing to pull "
                "with. It wants a Business Manager System User token carrying "
                "ads_read; a personal token expires after sixty days and the "
                "pull then fails with code 190 while the last import stays on "
                "screen. docs/RUNBOOK.md covers creating and rotating one.")

        self.version = version
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=(f"{(base or settings.meta_graph_base).rstrip('/')}"
                      f"/{version}"),
            timeout=(settings.meta_timeout if timeout is None else timeout),
            transport=transport,
            headers={
                # The token lives here and nowhere else in this process. See
                # the module header.
                "Authorization": f"Bearer {token.strip()}",
                "Accept": "application/json",
            },
        )

    @classmethod
    def from_settings(cls, **kwargs) -> "GraphClient":
        """The configured client, or NotConfigured naming the variable."""
        return cls(settings.meta_access_token, settings.meta_api_version,
                   **kwargs)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "GraphClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ---- the endpoints ---------------------------------------------------

    async def account(self, act_id: str) -> dict:
        """The account itself: currency and timezone, mainly.

        Both are copied onto the rows that depend on them rather than joined to
        at read time. An insights `date` is a day in the ACCOUNT's timezone and
        spend is in the ACCOUNT's currency, so a report that assumed UTC and
        dollars would be wrong by a day at one end of every window and wrong by
        an exchange rate throughout.
        """
        return await self._get(_account_path(act_id),
                               {"fields": ACCOUNT_FIELDS})

    async def campaigns(self, act_id: str) -> AsyncIterator[dict]:
        """Meta's campaigns in this account, every page."""
        async for node in self._paged(_account_path(act_id, "campaigns"),
                                      {"fields": CAMPAIGN_FIELDS}):
            yield node

    async def adsets(self, act_id: str) -> AsyncIterator[dict]:
        """Ad sets, whose optimization_goal decides how to read a CPL."""
        async for node in self._paged(_account_path(act_id, "adsets"),
                                      {"fields": ADSET_FIELDS}):
            yield node

    async def ads(self, act_id: str, *,
                  updated_since: datetime | date | int | None = None
                  ) -> AsyncIterator[dict]:
        """Ads with their creatives, optionally only those edited since.

        `updated_since` is what makes a daily pull cheap: an account with two
        thousand ads has a handful that changed yesterday, and asking for all
        of them with full creatives is the request most likely to hit the rate
        limit this module is careful about. The watermark is applied HERE and
        not to campaigns or ad sets, because an ad edited this morning
        routinely belongs to a parent last touched in June. The caller upserts
        the chain for whatever it imports, which is the same reasoning 042
        gives for that chain carrying no foreign keys.
        """
        params: dict = {"fields": AD_FIELDS}
        if updated_since is not None:
            params["updated_since"] = _unix(updated_since)
        async for node in self._paged(_account_path(act_id, "ads"), params):
            yield node

    async def insights(self, act_id: str, *, since: date | str,
                       until: date | str) -> AsyncIterator[dict]:
        """One row per ad per day over the window.

        `level=ad` and `time_increment=1` are the whole point and neither has a
        safe default: without the level Meta aggregates to the account and the
        numbers cannot be attached to any copy, and without the increment it
        returns one lifetime total per ad, which cannot answer "what changed
        after the copy changed" and cannot be re-cut for a window.
        """
        params = {
            "level": "ad",
            "time_increment": 1,
            "fields": INSIGHT_FIELDS,
            "time_range": json.dumps({"since": _day(since),
                                      "until": _day(until)}),
        }
        async for node in self._paged(_account_path(act_id, "insights"),
                                      params):
            yield node

    # ---- paging ----------------------------------------------------------

    async def _paged(self, path: str, params: dict) -> AsyncIterator[dict]:
        """Every node across every page, rebuilt from the cursor each time.

        Three stopping conditions and each is load-bearing. No `paging.next`
        means Meta is saying this was the last page, and it is the only honest
        signal: the cursor is returned on the last page too, so cursor-only
        paging fetches empty pages until something else stops it. No cursor
        means there is nothing to rebuild the request from, and following
        `paging.next` to get one is the thing this file will not do. Empty
        `data` means the next request would be the same page again, which is
        how a cursor loop becomes infinite.
        """
        params = dict(params)
        params.setdefault("limit", PAGE_SIZE)
        page = 0
        while True:
            payload = await self._get(path, params)
            page += 1
            data = payload.get("data")
            data = data if isinstance(data, list) else []
            for node in data:
                if isinstance(node, dict):
                    yield node

            paging = payload.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not data or not paging.get("next") or not after:
                log.info("meta %s: %s page(s) read", path, page)
                return
            # The cursor only. `paging.next` is a complete URL with the access
            # token in its query string and it is never requested.
            params["after"] = after

    # ---- one request -----------------------------------------------------

    async def _get(self, path: str, params: dict) -> dict:
        """One GET, with the back-off and the error vocabulary applied."""
        wait = BACKOFF_SECONDS
        transient_wait = TRANSIENT_BACKOFF_SECONDS
        last = ""
        # A page-size reduction is not a failed attempt: the request that comes
        # next is a different, smaller one, and charging it against TRIES would
        # spend the budget for the rate limit and the blip on discovering a
        # size. Counted separately and bounded by MAX_REDUCTIONS.
        reductions = 0
        attempt = 0
        while True:
            attempt += 1
            response = await self._http.get(path, params=params)
            usage = _usage(response.headers)

            try:
                body = response.json()
            except ValueError:
                body = None
            payload = body if isinstance(body, dict) else {}
            error = payload.get("error")
            error = error if isinstance(error, dict) else None

            if error is None and response.status_code < 400:
                if not isinstance(body, dict):
                    # A 200 that is not an object is not an empty page, it is
                    # something else answering. Returning {} here would import
                    # nothing and report success.
                    raise GraphError(
                        f"{path}: HTTP {response.status_code} but the body "
                        f"was not a JSON object")
                await self._pause_if_spent(usage, path)
                return payload

            code = _int(error.get("code") if error else None)
            subcode = _int(error.get("error_subcode") if error else None)
            message = _scrub(str((error or {}).get("message")
                                 or response.text[:300]))
            log.warning("meta %s: HTTP %s code %s/%s: %s", path,
                        response.status_code, code, subcode, message)

            if code in TOKEN_CODES:
                raise TokenInvalid(
                    f"Meta refused the token (code {code}"
                    f"{f'/{subcode}' if subcode else ''}): {message}. "
                    f"META_ACCESS_TOKEN needs replacing, with a System User "
                    f"token carrying ads_read rather than a personal one. "
                    f"Nothing was retried: every account would fail the same "
                    f"way.")

            if code in ACCOUNT_CODES:
                raise AccountUnavailable(
                    f"Meta will not read {path} with this token "
                    f"(code {code}): {message}. Usually the account is not "
                    f"assigned to the System User. Other accounts are "
                    f"unaffected and the pull should carry on with them.")

            if code in THROTTLE_CODES:
                last = message
                if attempt < TRIES:
                    pause = _estimate(usage, error) or wait
                    log.info("meta %s: rate limited (code %s), attempt %s of "
                             "%s, waiting %.0fs", path, code, attempt, TRIES,
                             pause)
                    await self._sleep(pause)
                    wait *= 2
                    continue
                break

            # Checked before TRANSIENT_CODES and before the generic raise,
            # because it is the only branch that can make the SAME request
            # succeed by changing it. params is mutated rather than copied, so
            # _paged's remaining pages inherit the size that worked.
            limit = _int(params.get("limit")) or PAGE_SIZE
            if (REDUCE_MARKER in message.lower()
                    and reductions < MAX_REDUCTIONS
                    and limit > MIN_PAGE_SIZE):
                reductions += 1
                attempt -= 1
                params["limit"] = max(MIN_PAGE_SIZE, limit // 2)
                log.info("meta %s: too much data at limit %s, retrying at %s "
                         "(reduction %s of %s)", path, limit, params["limit"],
                         reductions, MAX_REDUCTIONS)
                continue

            if code in TRANSIENT_CODES:
                if attempt < TRIES:
                    log.info("meta %s: transient (code %s), attempt %s of %s, "
                             "waiting %.0fs", path, code, attempt, TRIES,
                             transient_wait)
                    await self._sleep(transient_wait)
                    transient_wait *= 2
                    continue
                # Says how many attempts it took, so a reader can tell "Meta
                # blipped once" from "Meta has been down for a minute".
                raise GraphError(
                    f"Meta kept failing {path} (code {code}"
                    f"{f'/{subcode}' if subcode else ''}) after {TRIES} "
                    f"attempts: {message}",
                    code=code, subcode=subcode)

            raise GraphError(
                f"Meta refused {path} (code {code}"
                f"{f'/{subcode}' if subcode else ''}): {message}",
                code=code, subcode=subcode)

        raise RateLimited(
            f"Meta is still rate limiting {path} after {TRIES} attempts: "
            f"{last}. The pull stops here rather than pushing the account into "
            f"a longer block; whatever was imported before this stays.")

    async def _pause_if_spent(self, usage: list[dict], path: str) -> None:
        """Wait before the next request when the hour's budget is nearly out."""
        peak = _peak(usage)
        if peak < USAGE_PAUSE_AT:
            return
        pause = _estimate(usage, None) or BACKOFF_SECONDS
        log.warning("meta %s: business use case usage at %s%%, pausing %.0fs "
                    "before the next request", path, peak, pause)
        await self._sleep(pause)
