"""The IMPORT pool. Connects as postgres and writes public.meta_*.

The third connection module here, and the only one that can write outside the
`ads` schema. One per job:

    db.py        ads_reader   read ads.*           intel/, ui.py, ask.py
    db_owner.py  ads_owner    write ads.*          ads_migrate.py, record.py
    db_meta.py   postgres     write public.meta_*  meta_ads/ only

Exactly one package imports this: meta_ads/. That is the same property
db_owner.py has and it is checked the same way, in tests/test_read_only.py --
because the whole point of moving the importer into this repo was to put the
ads code in one place, NOT to give the read verbs a way to write.

This file arrived verbatim from growth-engine/db.py. The importer used to run
there, against this same credential; nothing about the connection changed, only
which folder opens it.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from config import settings

# Windows defaults to ProactorEventLoop, which has no add_reader(), so psycopg
# refuses to run async on it: "Psycopg cannot use the 'ProactorEventLoop' to
# run in async mode". The pool hides that as a PoolTimeout after 30s, which
# looks like a network problem and is not one.
#
# Set before the pool is constructed, and before uvicorn creates its loop --
# importing this module is what installs the policy.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

pool = AsyncConnectionPool(
    conninfo=settings.database_url,
    min_size=settings.pool_min_size,
    max_size=settings.pool_max_size,
    kwargs={"row_factory": dict_row},
    # Validate a connection before handing it to a request.
    #
    # Observed: after the server sat idle, the first /context returned
    # "psycopg.OperationalError: consuming input failed: server closed the
    # connection unexpectedly" from require_brand -- the pool's very first
    # query. Supabase's pooler drops idle server connections, and without a
    # check the pool cheerfully lends out the dead socket. The failure looks
    # like a bug in whatever query happened to run first, which is how twenty
    # minutes went into reading a refactor that was fine.
    #
    # Costs one round trip per checkout. Worth it: the alternative is that
    # every caller has to retry, and a generator half way through writing an
    # asset pack is the worst place to discover that.
    check=AsyncConnectionPool.check_connection,
    # Retire connections before the pooler does it for us.
    max_idle=settings.pool_max_idle,
    # Opened explicitly in the lifespan rather than at import, so importing
    # this module never blocks on the network.
    open=False,
)


@asynccontextmanager
async def cursor() -> AsyncIterator:
    async with pool.connection() as conn, conn.cursor() as cur:
        yield cur


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    async with cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    async with cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()
