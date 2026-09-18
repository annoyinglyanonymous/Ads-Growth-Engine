"""A small, structure-only Meta Graph reader. Live calls, nothing persisted.

WHY THERE IS A SECOND CLIENT AT ALL

growth-engine/meta_ads/client.py is the canonical one and stays canonical: it
handles insights pagination, a proactive usage pause tuned for imports that run
for minutes, and the account-level error taxonomy that lets one revoked
assignment not cost the whole run. None of that is needed to ask "what is
running right now", and importing it would couple two repos through a module
path.

This asks two questions -- which ads are live, and at what budget -- and stops.
Structure edges are the cheap ones; the 2-15 second calls are insights with
breakdowns, which this never makes.

WHAT IT MAY NOT DO, AND WHY THAT IS THE WHOLE BOUNDARY

It writes nothing. Not to Meta, not to public.*, not to ads.*. The import is
still growth-engine's job and the only thing that puts Meta data in a table.
This repo reads -- from Postgres normally, and from the API when the question
is specifically about right now.

THE SECURITY PROPERTIES ARE COPIED DELIBERATELY, NOT CASUALLY

Each of these is here because client.py's docstring explains why it is there:

  * The token goes in an Authorization header and NEVER in a URL. The moment it
    is part of a URL it is one log line away from a file somebody is told to
    read, and a credential in a file somebody is told to read is a credential
    that has been disclosed.
  * paging.next is never followed, because that URL embeds the token. The next
    page is rebuilt from paging.cursors.after.
  * Anything logged or raised goes through _scrub first.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, AsyncIterator

import httpx

from config import settings

log = logging.getLogger(__name__)

#: Pinned, and the same version growth-engine imports with. Two repos reading
#: the same account through two API versions is a difference that shows up as a
#: field quietly missing rather than as an error.
API_VERSION = "v26.0"
BASE = "https://graph.facebook.com"

PAGE_SIZE = 200
TIMEOUT = 20.0

#: Short, because this is an interactive question. An import can afford to wait
#: two minutes for a rate limit to clear; somebody asking "what is live" cannot,
#: and would rather be told to try again.
TRIES = 2
BACKOFF_SECONDS = 3.0

TOKEN_CODES = {190}
THROTTLE_CODES = {4, 17, 32, 613, 80000, 80004}

AD_FIELDS = ("id,name,status,effective_status,adset_id,campaign_id,"
             "created_time,updated_time")
ADSET_FIELDS = ("id,name,status,effective_status,campaign_id,daily_budget,"
                "lifetime_budget,optimization_goal,bid_strategy,"
                "start_time,end_time,updated_time")

_SECRET = re.compile(r"(EAA[A-Za-z0-9]{20,}|access_token=[^&\s]+)")


def _scrub(text: str) -> str:
    return _SECRET.sub("[redacted]", text or "")


class NotConfigured(RuntimeError):
    """No token. A state of the install, not a fault."""


class GraphError(RuntimeError):
    pass


class TokenInvalid(GraphError):
    pass


class RateLimited(GraphError):
    pass


def configured() -> bool:
    return bool((settings.meta_access_token or "").strip())


class Graph:
    """One httpx client, closed by `async with`."""

    def __init__(self, token: str | None = None, *, sleep=asyncio.sleep):
        token = (token if token is not None else settings.meta_access_token) or ""
        self._token = token.strip()
        if not self._token:
            raise NotConfigured(
                "META_ACCESS_TOKEN is not set in this repo's .env, so the live "
                "verb cannot ask Meta anything. It wants the same Business "
                "Manager System User token growth-engine uses, carrying "
                "ads_read and nothing else -- see .env.example. Every other "
                "verb reads Postgres and works without it.")
        self._sleep = sleep
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "Graph":
        self._client = httpx.AsyncClient(
            timeout=TIMEOUT,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def _get(self, path: str, params: dict[str, Any]) -> dict:
        assert self._client is not None, "use `async with Graph() as g`"
        url = f"{BASE}/{API_VERSION}/{path}"
        for attempt in range(1, TRIES + 1):
            response = await self._client.get(url, params=params)
            if response.status_code == 200:
                return response.json()

            try:
                error = (response.json() or {}).get("error") or {}
            except ValueError:
                error = {}
            code = error.get("code")
            message = _scrub(error.get("message") or response.text[:300])

            if code in TOKEN_CODES:
                raise TokenInvalid(
                    f"Meta rejected the token (code {code}): {message}. "
                    f"A System User token does not expire on a timer, so this "
                    f"usually means it was revoked or its account assignment "
                    f"was removed.")
            if code in THROTTLE_CODES and attempt < TRIES:
                log.info("meta throttled (%s), retrying once", code)
                await self._sleep(BACKOFF_SECONDS)
                continue
            if code in THROTTLE_CODES:
                raise RateLimited(
                    f"Meta is rate limiting this account (code {code}). The "
                    f"warehouse is not affected -- only this live call is. "
                    f"Try again in a few minutes.")
            raise GraphError(f"Meta returned {response.status_code}: {message}")
        raise RateLimited("rate limited")

    async def _page(self, path: str, fields: str,
                    extra: dict[str, Any] | None = None
                    ) -> AsyncIterator[dict]:
        params: dict[str, Any] = {"fields": fields, "limit": PAGE_SIZE,
                                  **(extra or {})}
        pages = 0
        while True:
            payload = await self._get(path, params)
            rows = payload.get("data") or []
            for row in rows:
                yield row
            pages += 1

            paging = payload.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            # Three conditions, all load-bearing -- and `next` is checked but
            # never FOLLOWED, because that URL carries the token in a query
            # string. The cursor is what gets reused.
            if not rows or not paging.get("next") or not after:
                log.info("meta %s: %s page(s)", path, pages)
                return
            params["after"] = after
            # A live question should not silently turn into an import. If an
            # account is big enough to need this many pages, the warehouse is
            # the right tool and this one says so.
            if pages >= 10:
                log.info("meta %s: stopping at %s pages", path, pages)
                return

    async def ads(self, act_id: str, *, active_only: bool = True
                  ) -> AsyncIterator[dict]:
        extra: dict[str, Any] = {}
        if active_only:
            # Filtered server-side so a big account does not page through
            # thousands of paused ads to answer a question about the live ones.
            extra["effective_status"] = '["ACTIVE"]'
        async for row in self._page(f"{act_id}/ads", AD_FIELDS, extra):
            yield row

    async def adsets(self, act_id: str, *, active_only: bool = True
                     ) -> AsyncIterator[dict]:
        extra: dict[str, Any] = {}
        if active_only:
            extra["effective_status"] = '["ACTIVE"]'
        async for row in self._page(f"{act_id}/adsets", ADSET_FIELDS, extra):
            yield row
