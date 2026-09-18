"""The READ pool. Connects as ads_reader and offers no way to write.

This module deliberately exposes no cursor, no execute, no transaction --
only fetch_all and fetch_one. The write pool lives in db_owner.py, which
exactly one module in this repo imports (intel/record.py).

That split is not decoration: tests/test_read_only.py asserts that no other
module imports db_owner, which makes "the read surface cannot mutate" a fact
a test can check rather than a claim a docstring makes. growth-engine proves
the same property about verdicts.py the same way.
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
# refuses to run async on it. The pool hides that as a PoolTimeout after 30s,
# which looks like a network problem and is not one.
#
# Set before any pool is constructed, and before uvicorn creates its loop --
# importing this module is what installs the policy. (growth-engine/db.py:22
# carries the same block for the same reason.)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _pool(conninfo: str) -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=conninfo,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        kwargs={"row_factory": dict_row},
        # Validate a connection before handing it out. Supabase's pooler drops
        # idle server connections on its own schedule and without this check
        # the pool cheerfully lends out the dead socket -- the failure then
        # surfaces as an OperationalError inside whatever query ran first,
        # which reads as a bug in that query.
        check=AsyncConnectionPool.check_connection,
        max_idle=settings.pool_max_idle,
        # Opened explicitly, so importing this module never blocks on the
        # network -- a CLI that only prints --help should not need a database.
        open=False,
    )


#: Lazily constructed so that `settings.read_url` (which raises when
#: ADS_DATABASE_URL_RO is unset) is evaluated at first use and not at import.
_read: AsyncConnectionPool | None = None


def read_pool() -> AsyncConnectionPool:
    global _read
    if _read is None:
        _read = _pool(settings.read_url)
    return _read


async def open_read() -> None:
    await read_pool().open()


async def close_read() -> None:
    if _read is not None:
        await _read.close()


@asynccontextmanager
async def _cursor() -> AsyncIterator:
    pool = read_pool()
    if pool.closed:
        await pool.open()
    async with pool.connection() as conn, conn.cursor() as cur:
        yield cur


async def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    async with _cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


async def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    async with _cursor() as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()
