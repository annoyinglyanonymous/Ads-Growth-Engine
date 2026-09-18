"""The scheduled Meta pull. One command for Task Scheduler to run.

    python scripts/sync.py                      # every brand with an account
    python scripts/sync.py --brand renegade     # just one
    python scripts/sync.py --dry-run            # say what it would pull

WHY THIS EXISTS

`python -m meta_ads --pull` was always a command somebody ran, which means the
staleness window was not 24 hours -- it was "whenever anyone remembered". That is
worse than a nightly job in the one way that matters: it is invisible. A nightly
job that fails shows up as a failed task and a stale-data warning; a pull nobody
ran looks exactly like a quiet week.

WHAT IT ADDS OVER THE BARE COMMAND

  * Both brands in one run, and one brand failing does not stop the other.
    pull.run_pull already isolates per ACCOUNT; this isolates per brand, so a
    revoked assignment on Agency Height cannot cost Renegade its import.
  * A lock, so two runs cannot overlap. A pull takes minutes against a rate
    limiter, and Task Scheduler will happily start a second one on top of the
    first -- which would burn the usage budget racing itself.
  * A log, appended, with the summary counts. When somebody asks "did it run",
    the answer should not require a database query.
  * An exit code Task Scheduler can show: 0 all good, 1 something failed, 2 a
    run was already in progress.

WHAT IT DOES NOT DO

Backfills. `--since` is deliberately absent: a 13-month backfill is chunked, run
oldest-first and watched between chunks, and none of that belongs on a timer.
Run those by hand with `python -m meta_ads --pull --since ... --until ...`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Imported at module scope, BEFORE asyncio.run() builds a loop -- not inside the
# coroutine. Importing db is what installs WindowsSelectorEventLoopPolicy
# (db.py:22), and psycopg cannot run async on the ProactorEventLoop Windows
# gives you by default. Import it late and the policy arrives after the loop
# already exists, and every query dies as a 30-second PoolTimeout that looks
# exactly like an unreachable database.
import identity  # noqa: E402
from db_meta import fetch_all, pool  # noqa: E402
from meta_ads import pull as meta_pull  # noqa: E402

LOCK = PROJECT_ROOT / ".sync.lock"
LOG_DIR = PROJECT_ROOT / "logs"

#: A pull of a normal window is minutes. Two hours means the holder died without
#: cleaning up -- a machine that slept, a terminal closed mid-run -- and refusing
#: to ever run again because of it is a worse failure than the overlap the lock
#: prevents.
STALE_LOCK_AFTER = timedelta(hours=2)


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  {message}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with (LOG_DIR / "sync.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # A log that cannot be written is not a reason to skip the pull.
        pass


def take_lock() -> bool:
    if LOCK.exists():
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                LOCK.stat().st_mtime, timezone.utc)
        except OSError:
            age = timedelta(0)
        if age < STALE_LOCK_AFTER:
            log(f"another sync has been running for {age} -- doing nothing. "
                f"If that is wrong, delete {LOCK.name}.")
            return False
        log(f"breaking a stale lock ({age} old)")
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}",
                    encoding="utf-8")
    return True


async def brands_to_pull(only: str | None) -> list[str]:
    rows = await fetch_all(
        "select distinct b.slug from public.brands b "
        "join public.meta_ad_accounts a on a.brand_id = b.id "
        "where a.active order by b.slug")
    slugs = [r["slug"] for r in rows]
    if only:
        if only not in slugs:
            log(f"{only!r} has no active ad account registered; nothing to pull")
            return []
        return [only]
    return slugs


async def run(only: str | None, dry_run: bool) -> int:
    # The pool is opened by the FastAPI lifespan, so a CLI entry point has to
    # open it itself -- meta_ads/__main__.py:79 does the same. Without it every
    # query raises PoolClosed, which reads as a database problem and is not one.
    await pool.open()
    try:
        return await _run(only, dry_run)
    finally:
        await pool.close()


async def _run(only: str | None, dry_run: bool) -> int:
    slugs = await brands_to_pull(only)
    if not slugs:
        # Not a crash and not a success. meta_ads/__main__.py takes the same
        # line: printing nothing and exiting 0 reads as "imported everything,
        # all good", which is the one outcome nobody investigates.
        log("no brand has an active ad account -- register one with "
            "`python -m meta_ads --add-account act_<id> --brand <slug>`")
        return 1

    if dry_run:
        log(f"would pull: {', '.join(slugs)}")
        return 0

    failed = False
    for slug in slugs:
        try:
            summaries = await meta_pull.run_pull(
                brand_slug=slug, started_by=identity.cli_operator())
        except Exception as exc:
            # Per BRAND isolation. run_pull already isolates per account, but a
            # missing token or an invalid one propagates out of it by design --
            # and that should not cost the other brand its import.
            failed = True
            log(f"{slug}: FAILED  {type(exc).__name__}: {exc}")
            continue

        for s in summaries:
            if s.get("ok"):
                ins = s.get("insights") or {}
                skipped = ins.get("skipped_unknown_ad") or 0
                extra = f", {skipped} skipped (no parent ad)" if skipped else ""
                log(f"{slug} {s.get('act_id')}: ok  "
                    f"{json.dumps(s.get('structure') or {})} "
                    f"{ins.get('rows', 0)} insight rows{extra}")
            else:
                failed = True
                log(f"{slug} {s.get('act_id')}: FAILED  {s.get('error')}")

    return 1 if failed else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand", help="one brand; default is every brand with an "
                                   "active account")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    if a.dry_run:
        return asyncio.run(run(a.brand, True))

    if not take_lock():
        return 2

    log(f"sync starting ({a.brand or 'all brands'})")
    try:
        code = asyncio.run(run(a.brand, False))
    except Exception as exc:
        log(f"sync ABORTED  {type(exc).__name__}: {exc}")
        code = 1
    finally:
        LOCK.unlink(missing_ok=True)

    log(f"sync finished, exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
