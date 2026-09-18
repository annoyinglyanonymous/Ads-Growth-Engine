"""Shared fixtures. Mainly: one event loop and one open pool for all dbtests.

THE BUG THIS FIXES
Each dbtest helper used to do its own `asyncio.run(pool.open() ... pool.close())`.
db.pool is a module-level singleton, and a closed psycopg pool cannot be
reopened, so the FIRST dbtest to finish poisoned every one after it:

    PoolClosed: pool has already been opened/closed and cannot be reused

That was caught by a broad `except Exception: pytest.skip(f"no live database:
{exc}")`, which reported a perfectly reachable database as unreachable. Nine
tests said "skipped -- no live database" while the database was up, and three
of them had been quietly not running since they were written. A green suite that
is green because it skipped is worse than a red one.

So two changes, and the second matters more than the first:

  * one loop, one pool, opened once per session. A psycopg async pool binds to
    the loop it was opened on, so a session-wide pool needs a session-wide
    loop -- which is why this is not simply a session-scoped `pool.open()`.

  * the skip is narrow. Only genuine connection failures skip; anything else
    propagates and fails the test. A skip means "cannot reach the database",
    and it now only ever means that.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import PoolTimeout

# THE SUITE MUST NOT WRITE TO THE OPERATOR'S LOG.
#
# log.get() configures one RotatingFileHandler at settings.log_file on first
# use, and the tests exercise the same seams the app does -- so a run
# interleaved lines like "agent failed: angles for Outreach101 -- timed out"
# and "released request req-1 on shutdown" into logs/engine.log, from fixtures,
# about campaigns and requests that do not exist. CLAUDE.md sends a person
# reading "what just happened" to that file, and on 2026-09-11 those lines
# were read as evidence of a second runner draining the live queue.
#
# Redirected rather than silenced: a test that logs is still worth reading,
# just not there. Set at import, before any test module imports ui or runner
# and triggers that first log.get().
import config

config.settings.log_file = str(
    Path(tempfile.gettempdir()) / "mge-tests" / "engine.log")

# Imported before any loop is created: importing db is what installs
# WindowsSelectorEventLoopPolicy, and psycopg refuses to run async on the
# ProactorEventLoop that Windows would otherwise hand us.
# The IMPORT pool -- the only one the dbtests here need, because the only
# dbtests are the importer's. The read pool in db.py opens itself.
import db_meta as db                                            # noqa: E402

#: Failures that mean "the database is not reachable" and nothing else.
#: PoolClosed is deliberately ABSENT -- it is a bug in the harness, not a
#: network condition, and it must fail loudly rather than masquerade as one.
CONNECTION_ERRORS = (psycopg.OperationalError, PoolTimeout, OSError)


@pytest.fixture(scope="session")
def db_loop():
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        loop.close()


@pytest.fixture(scope="session")
def run_db(db_loop):
    """-> run(coro): await one coroutine against a live pool, or skip.

    The pool is opened lazily on the first call, so a suite with no dbtests
    selected never touches the network.
    """
    state = {"opened": False}

    def run(coro):
        if not state["opened"]:
            try:
                db_loop.run_until_complete(
                    db.pool.open(wait=True, timeout=15))
            except CONNECTION_ERRORS as exc:
                coro.close()          # never awaited; do not leak a warning
                pytest.skip(
                    f"no live database: {type(exc).__name__}: {exc}"[:160])
            state["opened"] = True
        return db_loop.run_until_complete(coro)

    try:
        yield run
    finally:
        if state["opened"]:
            db_loop.run_until_complete(db.pool.close())
