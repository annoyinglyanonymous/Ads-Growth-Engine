"""The scheduled brief. One command for Task Scheduler to run.

    python scripts/brief.py                    # every brand, last settled week
    python scripts/brief.py --brand renegade   # just one
    python scripts/brief.py --dry-run          # say which window it would publish
    python scripts/brief.py --force            # publish an unsettled week anyway

WHY A FILE AND NOT ONLY A PAGE

/brief renders live, and a live page silently rewrites its own past opinion
every time Meta restates. A reading is an opinion pinned to numbers that are
still moving, so the only way to ask "what did it say three weeks ago, and what
did it say it against" is to have kept a copy. The archive is what makes the
readings checkable after the fact, which is the only way anyone finds out
whether they were any good.

THE PUBLISHING GATE, WHICH IS THE POINT OF THE SCHEDULE

The task runs daily and publishes weekly. Each run pins `until` to the last
Sunday and refuses to publish if `ads.settled_through()` has not reached it --
so a given week's brief appears on the first morning that week has actually
settled, usually the Wednesday after it ended.

Two reasons this beats a fixed Monday. A window that ends at the drifting
settled edge makes consecutive briefs overlap or leave a gap, so comparing one
week to the next compares different-length weeks -- which is exactly the
arithmetic artefact `ads.compare` derives its own prior window to avoid. And it
turns `unsettled_days` from a footnote somebody skims into the condition under
which the document exists at all.

`--force` publishes anyway and stamps the result provisional. It exists because
a person asking for this week's numbers on Tuesday is a reasonable thing to
want; it is not what the scheduler runs.

Exit codes, as Task Scheduler shows them: 0 published, 1 something failed,
2 a run was already in progress, 3 nothing was settled enough to publish.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Imported at module scope, BEFORE asyncio.run() builds a loop, for db.py:22's
# reason: importing db installs WindowsSelectorEventLoopPolicy, and psycopg
# cannot run async on the ProactorEventLoop Windows gives you by default. Late
# import means the policy arrives after the loop exists and every query dies as
# a 30-second PoolTimeout that looks exactly like an unreachable database.
import asyncio  # noqa: E402

import db  # noqa: E402
import brief_render  # noqa: E402
from intel import brief as brief_mod  # noqa: E402
from intel import context  # noqa: E402

LOCK = PROJECT_ROOT / ".brief.lock"
LOG_DIR = PROJECT_ROOT / "logs"
BRIEF_DIR = PROJECT_ROOT / "briefs"

#: A brief is a handful of queries, so anything past this means the holder died
#: without cleaning up. Refusing to ever run again because of that is a worse
#: failure than the overlap the lock prevents.
STALE_LOCK_AFTER = timedelta(minutes=30)


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  {message}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with (LOG_DIR / "brief.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # A log that cannot be written is not a reason to skip the brief.
        pass


def take_lock() -> bool:
    if LOCK.exists():
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                LOCK.stat().st_mtime, timezone.utc)
        except OSError:
            age = timedelta(0)
        if age < STALE_LOCK_AFTER:
            log(f"another brief run has been going for {age} -- doing nothing. "
                f"If that is wrong, delete {LOCK.name}.")
            return False
        log(f"breaking a stale lock ({age} old)")
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}",
                    encoding="utf-8")
    return True


def last_sunday(today: date) -> date:
    """The end of the most recently completed Monday-to-Sunday week.

    Pinned rather than derived from the settled edge, so two consecutive briefs
    cover equal, adjacent, non-overlapping weeks and can honestly be read
    against each other.
    """
    return today - timedelta(days=today.isoweekday())


async def brands() -> list[str]:
    rows = await db.fetch_all("select slug from ads.brand order by slug")
    return [r["slug"] for r in rows]


async def publish(slug: str, until: date, days: int, force: bool) -> int:
    settled = None
    try:
        b = await context.brand(slug)
        settled = await context.settled_through(b["id"])
    except Exception as exc:
        log(f"{slug}: cannot read the settled edge ({exc})")

    if settled is None and not force:
        log(f"{slug}: nothing has been imported, so there is no settled edge "
            f"and no week to publish. Register an account and run a pull.")
        return 3
    if settled is not None and settled < until and not force:
        log(f"{slug}: week ending {until} has not settled (settled through "
            f"{settled}); holding. It will publish on the first run after it "
            f"does. --force publishes it stamped provisional.")
        return 3

    doc = await brief_mod.brief(slug, days, until)
    BRIEF_DIR.mkdir(exist_ok=True)
    stem = BRIEF_DIR / f"{until.isoformat()}-{slug}"

    # default=str so a date or a Decimal does not take the run down at the last
    # step -- every verb returns both, and the JSON is the archive.
    stem.with_suffix(".json").write_text(
        json.dumps(doc, indent=2, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(
        brief_render.markdown(doc), encoding="utf-8")

    readings = len(doc.get("readings") or [])
    degraded = len(doc.get("degraded") or [])
    flag = " PROVISIONAL" if doc.get("provisional") else ""
    log(f"{slug}: published {stem.name} -- {readings} reading(s), "
        f"{degraded} section(s) degraded{flag}")
    return 0


async def run(only: str | None, until: date | None, days: int,
              dry_run: bool, force: bool) -> int:
    # The pool is opened by the FastAPI lifespan, so a CLI entry point opens it
    # itself. Without this every query raises PoolClosed, which reads as a
    # database problem and is not one.
    await db.open_read()
    try:
        slugs = [only] if only else await brands()
        if not slugs:
            log("no brand found in ads.brand -- nothing to publish")
            return 1
        end = until or last_sunday(date.today())
        if dry_run:
            log(f"would publish week {end - timedelta(days=days - 1)} .. {end} "
                f"for: {', '.join(slugs)}")
            return 0

        worst = 0
        for slug in slugs:
            try:
                # Per brand, so one brand's failure cannot cost the other its
                # brief -- scripts/sync.py isolates the pull the same way.
                worst = max(worst, await publish(slug, end, days, force))
            except Exception as exc:
                log(f"{slug}: FAILED -- {type(exc).__name__}: {exc}")
                worst = 1
        return worst
    finally:
        await db.close_read()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--brand", default=None)
    p.add_argument("--until", default=None,
                   help="YYYY-MM-DD; defaults to the last completed Sunday")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="publish a week that has not settled, stamped provisional")
    a = p.parse_args()

    until = None
    if a.until:
        try:
            until = date.fromisoformat(a.until)
        except ValueError:
            print(f"not a date: {a.until!r}. Use YYYY-MM-DD.", file=sys.stderr)
            return 1

    if not a.dry_run and not take_lock():
        return 2
    try:
        return asyncio.run(run(a.brand, until, a.days, a.dry_run, a.force))
    finally:
        if not a.dry_run:
            LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
