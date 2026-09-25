"""Publish a creative suggestion. What the pipeline runs after it imports.

    python scripts/suggest.py                    # every brand with an account
    python scripts/suggest.py --brand renegade   # just one
    python scripts/suggest.py --dry-run          # build the pack, ask nobody
    python scripts/suggest.py --force            # republish today's

WHY THIS IS NOT A BUTTON

It was one, for about an hour. A suggestion somebody has to remember to ask for
is a suggestion nobody asks for -- which is the same failure the scheduled pull
exists to fix, one layer up: before `scripts/sync.py` the staleness window was
not 24 hours, it was "whenever anyone remembered". A button on a page is that
sentence again, wearing a nicer hat.

So the answer is written when the numbers change, not when somebody thinks to
ask, and it is already on the page when the page is opened.

WHY A FILE AND NOT A TABLE

`scripts/brief.py` makes this argument first and it holds here: a live page
silently rewrites its own past opinion every time Meta restates, so the only
way to ask "what did it say last week, and what did it say it against" is to
have kept a copy. The pack that produced each suggestion is stored beside it.

It also needs no migration. `ads_migrate.py --apply` is denied, this database is
shared, and a feature that cannot ship without a person running DDL is a feature
that does not ship this week.

ONE FILE PER BRAND PER DAY. Re-running replaces today's rather than appending:
a second import on the same afternoon should improve the suggestion, not leave
two of them and no way to tell which the page will show. `--force` is only
needed to overwrite a file that is already there; the scheduler passes it,
because the second pull of the day is exactly the case that should update.

Exit codes, for n8n to branch on:
    0  published
    1  something failed -- read logs/suggest.log
    2  a run was already in progress
    3  nothing to publish for (no active account, or no ads spent)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Imported at module scope, BEFORE asyncio.run() builds a loop. Importing db is
# what installs WindowsSelectorEventLoopPolicy, and psycopg cannot run async on
# the ProactorEventLoop Windows gives you by default -- scripts/sync.py carries
# the same note for the same reason.
import chat  # noqa: E402
import db  # noqa: E402
from intel import creative as creative_mod  # noqa: E402
from intel.context import UnknownBrand  # noqa: E402

LOCK = PROJECT_ROOT / ".suggest.lock"
LOG_DIR = PROJECT_ROOT / "logs"

#: A suggestion is one model call of about a minute. Ten minutes means the
#: holder died without cleaning up, and refusing to ever run again because of it
#: is a worse failure than the overlap the lock prevents.
STALE_LOCK_AFTER = timedelta(minutes=10)

#: Which sections of the creative pack reach the model.
#:
#: Everything except the frame (brand, window, settled edge), which is
#: interpolated into the prompt separately. A section in the pack and not here
#: is work done and thrown away one line before it would have been used.
SENT_TO_THE_MODEL = ("campaigns", "goals", "dimensions", "untagged", "tiring")

#: Sent whole, never through compact(). The reply is one rating per campaign,
#: and a campaign list cut to three rows is twenty campaigns with no card.
NEVER_COMPACTED = ("campaigns", "previous_problems", "changes_since_last_review")

#: The triage prompt's ceiling. Not creative.PROMPT_BUDGET: that one is the
#: argv limit, and chat.classify sends its prompt on stdin, which has none.
#: This is about cost and attention -- the other sections shrink first.
TRIAGE_BUDGET = 64000

#: The window the suggestion reads. Four weeks, matching /suggestions and the
#: brief: long enough that a single bad day does not rewrite the advice, short
#: enough that it is about what the account is running now.
DAYS = 28


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  {message}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with (LOG_DIR / "suggest.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        # A log that cannot be written is not a reason to skip the work.
        pass


def take_lock() -> bool:
    if LOCK.exists():
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                LOCK.stat().st_mtime, timezone.utc)
        except OSError:
            age = timedelta(0)
        if age < STALE_LOCK_AFTER:
            log(f"another suggest has been running for {age} -- doing nothing. "
                f"If that is wrong, delete {LOCK.name}.")
            return False
        log(f"breaking a stale lock ({age} old)")
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}",
                    encoding="utf-8")
    return True


async def brands() -> list[str]:
    """Every brand with an active ad account, by slug."""
    rows = await db.fetch_all(
        "select distinct b.slug from ads.brand b "
        "  join ads.ad_account a on a.brand_id = b.id "
        " where a.active order by b.slug")
    return [r["slug"] for r in rows]


async def publish(slug: str, dry_run: bool, force: bool) -> int:
    pack = await creative_mod.creative_pack(slug, DAYS, None)

    if not pack.get("goals"):
        # Not a crash and not a success. An account that spent nothing has
        # nothing to say about its copy, and publishing an empty suggestion
        # would put a confident paragraph on the page about no ads at all.
        log(f"{slug}: no ads spent in the window -- nothing to suggest about")
        return 3

    out = creative_mod.publish_path(slug, date.today())
    if out.exists() and not force and not dry_run:
        log(f"{slug}: {out.name} already exists; --force to replace it")
        return 0

    ads = sum(len(g["cheapest"]) + len(g["dearest"]) + len(g["by_spend_only"])
              for g in pack["goals"])
    log(f"{slug}: {len(pack['goals'])} goal(s), {ads} ad(s) of copy, "
        f"window {pack['since']} to {pack['until']}")

    # `dimensions`, not `formats`. They overlap -- format is one of the four --
    # but picking the old key sent the model the ad-builder cut and withheld
    # hook, offer and audience, which are the ones that make this a creative
    # analysis. It said so: "the hook and offer cut was not in what I was
    # given", while the pack had it and the publisher dropped it on the way
    # past. Named explicitly rather than `pack.keys()` so a new section has to
    # be sent deliberately, but the test below asserts none is forgotten.
    facts = {k: pack[k] for k in SENT_TO_THE_MODEL}

    # The last review, so each of its problems can be carried, resolved or
    # reported as no longer mentioned. "Changed" is judged on the campaign
    # facts the model reads, not the window end: a structure pull can change
    # an ad's wording without moving the insights window.
    prev = creative_mod.previous_suggestion(slug, date.today())
    previous = creative_mod.previous_problems(prev)
    # What actually moved, computed rather than judged: the only evidence a
    # resolution may cite (creative.changes_since explains why).
    snap = await creative_mod.snapshot(
        slug, [c["campaign"] for c in pack["campaigns"]])
    changes = creative_mod.changes_since(
        prev, json.loads(json.dumps(pack["campaigns"], default=str)), snap)
    data_changed = not prev or bool(changes)
    facts["changes_since_last_review"] = changes
    facts["previous_problems"] = creative_mod.for_the_prompt(previous)
    note = creative_mod.previous_note(prev, data_changed)
    if prev:
        by = [ch["by"] for v in changes.values() for ch in v]
        log(f"{slug}: comparing against {prev['file']} "
            f"({sum(len(v) for v in previous.values())} problem(s)); "
            f"{by.count('you')} change(s) to the account, "
            f"{by.count('numbers')} in the numbers"
            + ("" if prev.get("snapshot") is not None else
               "; no snapshot in it, so wording is compared on leading ads only"))

    question = build_question(pack, facts, note)
    log(f"{slug}: {len(pack['campaigns'])} campaign(s), prompt "
        f"{len(question)} chars")

    if dry_run:
        log(f"{slug}: --dry-run, so nobody was asked")
        return 0

    started = datetime.now(timezone.utc)
    try:
        # 24 campaigns of JSON is several minutes of writing, not one.
        rows = await chat.classify(question, system=creative_mod.TRIAGE_SYSTEM,
                                   timeout=600)
    except (ValueError, chat.ChatUnavailable) as exc:
        log(f"{slug}: FAILED  {exc}")
        return 1
    triage = creative_mod.triage_from(rows, pack["campaigns"], previous,
                                      data_changed, date.today(),
                                      changes if prev else None)
    flagged = creative_mod.check_figures(triage, facts)
    if flagged:
        log(f"{slug}: {flagged} figure(s) on the cards appear nowhere in the "
            f"facts; flagged on their cards")
    moved = [t for t in triage if t.get("rating_adjusted_from")]
    if moved:
        log(f"{slug}: {len(moved)} rating(s) moved by the rating rule")
    unrated = sum(1 for t in triage if t["rating"] is None)
    if unrated == len(triage):
        log(f"{slug}: FAILED  the reply rated none of the "
            f"{len(triage)} campaign(s)")
        return 1
    if unrated:
        log(f"{slug}: {unrated} campaign(s) came back unrated; published "
            f"with them marked")

    doc = {
        "verb": "suggest",
        "brand": slug,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": str(pack["since"]),
        "until": str(pack["until"]),
        "days": pack["days"],
        "settled_through": str(pack["settled_through"]),
        "unsettled_days": pack["unsettled_days"],
        # One entry per campaign, red / yellow / green. `suggestion` -- the
        # prose this used to publish -- is no longer written; the page still
        # reads it off older files.
        "triage": triage,
        "previous_file": prev.get("file") if prev else None,
        "data_changed": data_changed,
        # The account as it stood, so the next run can tell which ads'
        # wording or status moved. Not sent to the model.
        "snapshot": snap,
        "duration_ms": int((datetime.now(timezone.utc) - started)
                           .total_seconds() * 1000),
        # The pack rides along. A suggestion without the numbers it was written
        # from cannot be checked later, and checking it later is the only way
        # anyone finds out whether these were any good.
        "pack": facts,
    }
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    counts = {r: sum(1 for t in triage if t["rating"] == r)
              for r in creative_mod.RATINGS}
    log(f"{slug}: published {out.name} ({counts['red']} red, "
        f"{counts['yellow']} yellow, {counts['green']} green, "
        f"{doc['duration_ms'] / 1000:.0f}s)")
    if prev:
        n = {k: sum(len(t[k]) for t in triage) for k in ("resolved", "dropped")}
        still = sum(1 for t in triage for p in t["problems"]
                    if p["status"] == "still_open")
        log(f"{slug}: since {prev['file']}: {n['resolved']} resolved, "
            f"{still} still open, {n['dropped']} no longer mentioned")
    return 0


def build_question(pack: dict, facts: dict, previous_note: str = "") -> str:
    """The prompt, shrunk until it fits the command line.

    Only the sections outside NEVER_COMPACTED shrink. The campaign list goes
    whole because the reply is one rating per campaign; the per-goal ad lists
    and the dimensions are supporting evidence and can lose their tails.
    """
    whole = {k: facts[k] for k in NEVER_COMPACTED if k in facts}
    rest = {k: v for k, v in facts.items() if k not in whole}
    question = ""
    for keep in (8, 3, 0):
        question = creative_mod.SUGGESTIONS_PROMPT.format(
            brand=pack["brand"], since=pack["since"], until=pack["until"],
            days=pack["days"], settled=pack["settled_through"],
            unsettled=pack["unsettled_days"],
            previous_note=previous_note or creative_mod.previous_note(None, True),
            facts=json.dumps({**whole, **creative_mod.compact(rest, keep)},
                             separators=(",", ":"), ensure_ascii=False,
                             default=str))
        if len(question) <= TRIAGE_BUDGET:
            break
    return question


async def run(only: str | None, dry_run: bool, force: bool) -> int:
    await db.open_read()
    try:
        slugs = [only] if only else await brands()
        if not slugs:
            log("no brand has an active ad account -- nothing to suggest for")
            return 3
        worst = 0
        for slug in slugs:
            try:
                code = await publish(slug, dry_run, force)
            except UnknownBrand as exc:
                log(f"{slug}: {exc}")
                code = 1
            except Exception as exc:
                # Per BRAND isolation, for the reason sync.py isolates: one
                # brand's model call failing must not cost the other its
                # suggestion.
                log(f"{slug}: FAILED  {type(exc).__name__}: {exc}")
                code = 1
            worst = max(worst, code) if code != 3 else worst
        return worst
    finally:
        await db.close_read()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand", help="one brand; default is every brand with an "
                                   "active account")
    p.add_argument("--dry-run", action="store_true",
                   help="build the pack and print its size; ask nobody")
    p.add_argument("--force", action="store_true",
                   help="replace today's file if it is already there")
    a = p.parse_args()

    if not chat.available() and not a.dry_run:
        log("the `claude` command is not on PATH, so nothing can be written. "
            "The pack still builds: try --dry-run.")
        return 1

    if a.dry_run:
        return asyncio.run(run(a.brand, True, a.force))

    if not take_lock():
        return 2

    log(f"suggest starting ({a.brand or 'all brands'})")
    try:
        code = asyncio.run(run(a.brand, False, a.force))
    except Exception as exc:
        log(f"suggest ABORTED  {type(exc).__name__}: {exc}")
        code = 1
    finally:
        LOCK.unlink(missing_ok=True)

    log(f"suggest finished, exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
