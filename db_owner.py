"""The WRITE pool. Connects as ads_owner.

Exactly one module imports this: intel/record.py, which files proposals into
ads.* -- a facet, a proposed angle, a proposed experiment. Nothing here can
reach public.*: the role holds SELECT there and nothing else, so an INSERT
into public fails at the database rather than at code review.

What this pool deliberately cannot do either, by the shape of the schema
rather than by the shape of the role:

  * set ads.angle.status = 'active'      -- constraint angle_active_is_signed
                                            requires approved_by, and the
                                            writer hard-codes 'proposed'
  * set ads.experiment.conclusion        -- not in any accepted write shape
  * apply a migration                    -- ads_migrate.py --apply is denied
                                            in .claude/settings.json

The guarantee this repo makes is not "the agent has no password". It is that
no code path writes a decision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

import db  # installs the Windows event-loop policy; see db.py
from config import settings

_owner: AsyncConnectionPool | None = None


def owner_pool() -> AsyncConnectionPool:
    global _owner
    if _owner is None:
        _owner = db._pool(settings.write_url)
    return _owner


async def close_owner() -> None:
    if _owner is not None:
        await _owner.close()


@asynccontextmanager
async def cursor() -> AsyncIterator:
    pool = owner_pool()
    if pool.closed:
        await pool.open()
    async with pool.connection() as conn, conn.cursor() as cur:
        yield cur
