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
import re
import subprocess
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
from intel import outcomes as outcomes_mod  # noqa: E402
from intel import shapes as shapes_mod  # noqa: E402
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
#: interpolated into the prompt separately, and PAGE_ONLY. A section in the
#: pack and in neither list is work done and thrown away one line before it
#: would have been used; tests/test_suggestions.py checks there is none.
SENT_TO_THE_MODEL = ("campaigns", "challenger_pool", "dimensions",
                     "dimensions_by_goal", "untagged", "windows")

#: Built for the /suggestions page, deliberately not sent. `goals` was the
#: model's per-goal copy list and is replaced by `challenger_pool`; `tiring`
#: is the 28-day "1 of 5 symptoms" read that turned ten campaigns red on its
#: own; `formats` is one of the four `dimensions` already sent.
PAGE_ONLY = ("tiring", "formats", "goals", "goal_impact")

#: The pack's frame and flags: interpolated into the prompt or read by this
#: script, never sent as a section.
FRAME = ("verb", "brand", "since", "until", "days", "settled_through",
         "unsettled_days", "funnel_available", "funnel_why",
         "impact_available", "impact_why")

#: Sent whole, never through compact(). The reply is one rating per campaign,
#: and a campaign list cut to three rows is twenty campaigns with no card; the
#: pool, the windows and the last review are already as short as they get.
NEVER_COMPACTED = ("campaigns", "challenger_pool", "windows",
                   "previous_issues", "changes_since_last_review", "followups")

#: How the prompt shrinks, in order, until it fits: the campaign tier
#: (creative.campaign_tier) and how many rows each other section keeps. The
#: campaigns get shorter before the dimensions lose their rows, because a
#: test plan's challenger hook comes from the dimensions.
STEPS = (("A", 8), ("B", 8), ("B", 3), ("C", 3), ("C", 0))

#: What the log says, and the page keeps the last file, when 017 is missing.
NO_FUNNEL = ("migration 017 is not applied -- apply it (python ads_migrate.py "
             "--print 017) before the analysis can run")

#: The triage prompt's ceiling. Not creative.PROMPT_BUDGET: that one is the
#: argv limit, and chat.classify sends its prompt on stdin, which has none.
#: This is about cost and attention -- the other sections shrink first.
#: 112k, not 64k: the analyst pack carries a window ladder, funnel and
#: findings per campaign (~3k each). Measured 2026-10-01 on 25 campaigns with
#: a 54-issue previous review: tier A ~102k, tier C ~95k. Cutting the carried
#: review instead would cost the page its "still open / resolved" record,
#: which is the part a reader trusts most.
#:
#: 140k since round 2 (buckets, summary, challenger kind, followups). Measured
#: 2026-10-01 with `--dry-run` on the same account BEFORE 018 was applied: the
#: prompt text itself is 17.9k (was ~11k), and tier A is 118,242 chars
#: (campaigns 74.9k, previous_issues 11.4k, changes 4.4k, challenger_pool
#: 4.1k, dimensions 4.0k); tier C is 102,412. Once 018 is applied each running
#: campaign carries `impact` (~120 chars, ~2.5k in all), `dimensions_by_goal`
#: replaces `dimensions` (four goals x three dimensions, at most 8 rows each
#: after compact), and `followups` grows with the ledger -- an estimated
#: 128-135k at tier A. The model has taken 250k; stdin has no length limit.
#: Re-measure with --dry-run once 018 is applied and the ledger has a week.
TRIAGE_BUDGET = 140000

#: Campaigns per model call. One call for all 25 timed out at 900s on
#: 2026-10-01: round 2's reply (a summary, six fields per issue, briefs and
#: followups per campaign) is too long to write in one go. Batches run in
#: parallel, so the wall time is the slowest batch, not the sum, and a batch
#: that fails costs its own campaigns (shown unrated), not the review.
BATCH_SIZE = 7

#: Per batch. Seven campaign reports took well under this in testing; the
#: whole script still sits inside scripts/sync.py's 1200s.
BATCH_TIMEOUT = 720

#: The window `dimensions` and the page's evidence read. Four weeks, matching
#: /suggestions and the brief. The campaign analysis reads 017's named window
#: pairs instead, all ending at the settled edge.
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

    if not pack.get("funnel_available"):
        # REFUSED, not degraded. Without 017 there is no window ladder, no
        # funnel and no diagnosis -- a half-analysis is exactly the one-window
        # read the operator rejected. The page keeps the last file.
        log(f"{slug}: {NO_FUNNEL}"
            + (" (dry run: nothing would have been published)"
               if dry_run else ""))
        return 1

    out = creative_mod.publish_path(slug, date.today())
    if out.exists() and not force and not dry_run:
        log(f"{slug}: {out.name} already exists; --force to replace it")
        return 0

    running = [c for c in pack["campaigns"] if c.get("running")]
    pool = [a for g in pack["challenger_pool"] for a in g["ads"]]
    log(f"{slug}: {len(pack['campaigns'])} campaign(s), {len(running)} "
        f"running in the last 7 settled days; {len(pool)} challenger(s) "
        f"across {len(pack['challenger_pool'])} goal(s); settled through "
        f"{(pack.get('windows') or {}).get('settled_through')}")

    # `dimensions`, not `formats`. They overlap -- format is one of the four --
    # but picking the old key sent the model the ad-builder cut and withheld
    # hook, offer and audience, which are the ones that make this a creative
    # analysis. It said so: "the hook and offer cut was not in what I was
    # given", while the pack had it and the publisher dropped it on the way
    # past. Named explicitly rather than `pack.keys()` so a new section has to
    # be sent deliberately, but the test below asserts none is forgotten.
    facts = model_facts(pack)

    # The last review, so each of its issues can be carried, resolved or
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
    facts["previous_issues"] = creative_mod.for_the_prompt(
        previous, {c["campaign"] for c in pack["campaigns"]})
    note = creative_mod.previous_note(prev, data_changed)

    # The outcomes ledger as the last run left it: what was recommended
    # before and whether the account shows it done. The model reads it as
    # `followups`, so it reports a measuring test instead of stacking another.
    ledger = load_ledger(slug)
    facts["followups"] = creative_mod.followups(
        ledger, pack["campaigns"], outcomes_mod.for_campaign)
    if prev:
        by = [ch["by"] for v in changes.values() for ch in v]
        log(f"{slug}: comparing against {prev['file']} "
            f"({len([p for v in previous.values() for p in v])} issue(s)); "
            f"{by.count('you')} change(s) to the account, "
            f"{by.count('numbers')} in the numbers"
            + ("" if prev.get("snapshot") is not None else
               "; no snapshot in it, so wording is compared on leading ads only"))

    batches = batch_facts(facts)
    asked = [fit(pack, b, note) for b in batches]
    sent = merge_sent([a[1] for a in asked])
    tier = max(a[2] for a in asked)
    log(f"{slug}: {len(batches)} batch(es) of up to {BATCH_SIZE} campaign(s); "
        f"prompts {', '.join(str(len(a[0])) for a in asked)} chars "
        f"(campaign tier {tier} at worst)")

    if dry_run:
        log(f"{slug}: --dry-run, so nobody was asked")
        return 0

    started = datetime.now(timezone.utc)
    replies = await asyncio.gather(
        *[chat.classify(q, system=creative_mod.TRIAGE_SYSTEM,
                        timeout=BATCH_TIMEOUT, expect="any")
          for q, *_ in asked],
        return_exceptions=True)
    rows, account_note, failed = [], None, 0
    for i, reply in enumerate(replies):
        try:
            if isinstance(reply, BaseException):
                raise reply
            got, got_note = creative_mod.parse_reply(reply)
        except (ValueError, chat.ChatUnavailable, Exception) as exc:  # noqa: BLE001
            failed += 1
            log(f"{slug}: batch {i + 1} of {len(asked)} FAILED  "
                f"{type(exc).__name__}: {str(exc)[:300]}")
            continue
        rows += got
        # The first batch holds the highest-impact campaigns (batch_facts),
        # so its note is the one about where to start.
        account_note = account_note or got_note
    if failed == len(asked):
        log(f"{slug}: FAILED  every batch failed")
        return 1
    triage = creative_mod.triage_from(rows, pack["campaigns"], previous,
                                      data_changed, date.today(),
                                      changes if prev else None,
                                      pool=pack.get("challenger_pool"))
    flagged = creative_mod.check_figures(triage, sent)
    if flagged:
        log(f"{slug}: {flagged} figure(s) on the cards appear nowhere in the "
            f"facts; flagged on their cards")
    causal = creative_mod.causal_language(triage)
    jargon = creative_mod.jargon_language(triage, sent)
    if jargon:
        log(f"{slug}: {jargon} field name(s) written on the cards; flagged "
            f"on their cards")
    summary = creative_mod.account_summary(pack, triage, account_note)
    summary["note_unverified_figures"] = creative_mod.check_note(account_note,
                                                                 sent)
    summary["note_causal_flags"] = creative_mod.causal_phrases(account_note)
    if causal or summary["note_causal_flags"]:
        log(f"{slug}: {causal} causal phrase(s) on the cards"
            + (" and in the account note" if summary["note_causal_flags"]
               else "") + "; flagged on their cards")
    if summary["note_unverified_figures"]:
        log(f"{slug}: the account note states "
            f"{summary['note_unverified_figures']}, which the facts do not "
            f"contain; flagged")
    moved = [t for t in triage if t.get("rating_adjusted_from")]
    if moved:
        log(f"{slug}: {len(moved)} rating(s) moved by the rating rule")
    log(f"{slug}: {clamp_summary(triage)}")
    unrated = len([t for t in triage if t["rating"] is None])
    if unrated == len(triage):
        log(f"{slug}: FAILED  the reply rated none of the "
            f"{len(triage)} campaign(s)")
        return 1
    if unrated:
        log(f"{slug}: {unrated} campaign(s) came back unrated; published "
            f"with them marked")

    # The ledger: register this review's act/test issues, match the
    # account's own changes, read what was implemented. Each step degrades
    # on its own: a ledger that cannot be measured is still a ledger.
    ledger, outcomes_ok = await update_outcomes(slug, ledger, triage,
                                                changes if prev else {}, pack)
    creative_mod.attach_outcomes(triage, ledger)
    triage = creative_mod.sort_triage(triage)
    log(f"{slug}: {bucket_summary(triage)}")

    doc = {
        "verb": "suggest",
        # 2: per-campaign analyst reports with `issues` (contracts C5). The
        # page still renders files without it, through the legacy markup.
        "shape": 2,
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
        # "What matters today": 018's goal and campaign rows plus counts,
        # and the reply's one account_note (checked like a card).
        "account_summary": summary,
        "impact_available": bool(pack.get("impact_available")),
        # The outcomes ledger's scorecard (outcomes/<brand>.json). None when
        # the ledger step failed outright; the cards then carry no tags.
        "outcomes": ({"available": True,
                      "measurement_available":
                          bool(pack.get("impact_available")),
                      "scorecard": outcomes_mod.scorecard(ledger,
                                                          date.today())}
                     if outcomes_ok else None),
        "previous_file": prev.get("file") if prev else None,
        "data_changed": data_changed,
        # The account as it stood, so the next run can tell which ads'
        # wording or status moved. Not sent to the model.
        "snapshot": snap,
        "duration_ms": int((datetime.now(timezone.utc) - started)
                           .total_seconds() * 1000),
        # The pack rides along -- as SENT, at the tier that fitted. A
        # suggestion without the numbers it was written from cannot be checked
        # later, and checking it later is the only way anyone finds out
        # whether these were any good.
        "pack": sent,
        "prompt_tier": tier,
    }
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    # After the review is on disk: a ledger that moved ahead of a review
    # that failed to write would describe recommendations nobody saw.
    if outcomes_ok:
        # A launched test becomes an experiment proposal now, from the plan
        # published before the launch. Never fatal: what fails is logged on
        # the entry and tried again next run.
        try:
            file_experiments(slug, ledger, date.today())
        except Exception as exc:  # noqa: BLE001 -- the review is published
            log(f"{slug}: experiments not filed this run: "
                f"{type(exc).__name__}: {exc}")
        try:
            outcomes_mod.save(slug, ledger)
        except OSError as exc:
            log(f"{slug}: the outcomes ledger could not be saved: {exc}")
    counts = {r: len([t for t in triage if t["rating"] == r])
              for r in creative_mod.RATINGS}
    log(f"{slug}: published {out.name} ({counts['red']} red, "
        f"{counts['yellow']} yellow, {counts['green']} green, "
        f"{doc['duration_ms'] / 1000:.0f}s)")
    if prev:
        n = {k: len([x for t in triage for x in t[k]])
             for k in ("resolved", "dropped")}
        still = len([p for t in triage
                     for p in (t.get("issues") or t.get("problems") or [])
                     if p["status"] == "still_open"])
        log(f"{slug}: since {prev['file']}: {n['resolved']} resolved, "
            f"{still} still open, {n['dropped']} no longer mentioned")
    return 0


def model_facts(pack: dict) -> dict:
    """The pack sections the model reads (SENT_TO_THE_MODEL), with the
    creative dimensions in ONE form: `dimensions_by_goal` (018, trimmed by
    creative.dimensions_by_goal_view) when the pack has it, else the older
    cross-goal `dimensions`. Sending both would hand the model the
    cross-goal table the per-goal one exists to replace."""
    facts = {k: pack.get(k) for k in SENT_TO_THE_MODEL}
    by_goal = creative_mod.dimensions_by_goal_view(
        pack.get("dimensions_by_goal"))
    if by_goal:
        facts["dimensions_by_goal"] = by_goal
        facts.pop("dimensions", None)
    else:
        facts.pop("dimensions_by_goal", None)
    return facts


def load_ledger(slug: str) -> dict:
    """outcomes/<brand>.json, or an empty ledger when it is missing or
    unreadable (logged: a ledger silently restarted would read as nothing
    ever having been recommended)."""
    try:
        return outcomes_mod.load(slug)
    except (OSError, ValueError) as exc:
        log(f"{slug}: the outcomes ledger could not be read ({exc}); "
            f"starting from an empty one")
        return outcomes_mod.empty(slug)


async def update_outcomes(slug: str, ledger: dict, triage: list,
                          changes: dict, pack: dict) -> tuple:
    """(ledger, ok): register, match and measure. Overrides that will not
    parse are logged and ignored; a measurement that fails keeps the ledger
    as matched. `ok` is False only when registration itself failed."""
    try:
        overrides = outcomes_mod.load_overrides()
    except ValueError as exc:
        log(f"{slug}: {exc}; overrides ignored this run")
        overrides = {}
    try:
        ledger = outcomes_mod.update_ledger(ledger, triage, changes,
                                            date.today(), overrides)
    except Exception as exc:  # noqa: BLE001 -- the review still publishes
        log(f"{slug}: the outcomes ledger could not be updated: "
            f"{type(exc).__name__}: {exc}")
        return ledger, False
    settled = ((pack.get("windows") or {}).get("settled_through")
               or pack.get("settled_through"))
    try:
        ledger = await outcomes_mod.measure(ledger, slug, settled)
    except Exception as exc:  # noqa: BLE001
        log(f"{slug}: outcomes not measured this run: "
            f"{type(exc).__name__}: {exc}")
    statuses = [e.get("status")
                for e in (ledger.get("entries") or {}).values()]
    if statuses:
        log(f"{slug}: outcomes ledger, {len(statuses)} entr(ies): "
            + ", ".join(f"{statuses.count(s)} {s}"
                        for s in outcomes_mod.STATUSES if statuses.count(s)))
    else:
        log(f"{slug}: outcomes ledger is empty (no act or test issue yet)")
    return ledger, True


#: Seconds for one `intel record` process: a brand lookup and one insert.
RECORD_TIMEOUT = 120

#: Where each filed payload is kept: `intel record` takes a path, and the
#: file stays so "what exactly did it file?" has an answer on disk.
FILED_DIR = outcomes_mod.OUTCOME_DIR / "filed"


def file_experiments(slug: str, ledger: dict, today: date) -> dict:
    """File an experiment proposal for every launched test that has none.

    `python -m intel record --kind experiment --json <path>`, as a separate
    process with this interpreter -- the way /refresh spawns sync.py -- so
    the owner pool (db_owner) never enters this script; intel/record.py is
    the one writer, and it files a PROPOSAL: no started_on, no conclusion.
    The entry gets `experiment` (name, id) when it lands, or
    `experiment_skipped` / `experiment_error` with the reason, and an entry
    left without `experiment` is tried again on the next run. A name that
    already exists is taken as filed (record does not file it twice).
    Returns counts, for the log and the tests.
    """
    n = {"filed": 0, "skipped": 0, "failed": 0}
    for e in (ledger.get("entries") or {}).values():
        if not outcomes_mod.needs_experiment(e):
            continue
        iid = e.get("id")
        payload, why = outcomes_mod.experiment_payload(e, slug)
        if payload is None:
            outcomes_mod.mark_not_filed(e, why, today, skipped=True)
            log(f"{slug}: {iid} launched, experiment not filed: {why}")
            n["skipped"] += 1
            continue
        try:
            shapes_mod.validate("experiment", payload)
            FILED_DIR.mkdir(parents=True, exist_ok=True)
            path = FILED_DIR / f"{slug}-{re.sub(r'[^A-Za-z0-9_-]', '_', str(iid))}.json"
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False,
                                       default=str), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, "-m", "intel", "record", "--kind",
                 "experiment", "--json", str(path)],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=RECORD_TIMEOUT)
            if proc.returncode != 0:
                raise RuntimeError((proc.stderr or proc.stdout or
                                    f"exit {proc.returncode}").strip()
                                   .splitlines()[0])
            result = json.loads(proc.stdout)
        except Exception as exc:  # noqa: BLE001 -- retried next run
            outcomes_mod.mark_not_filed(e, f"{type(exc).__name__}: {exc}", today)
            log(f"{slug}: {iid} launched, filing its experiment FAILED "
                f"({type(exc).__name__}: {str(exc)[:200]}); will retry")
            n["failed"] += 1
            continue
        outcomes_mod.mark_filed(e, result, payload["name"], today)
        log(f"{slug}: {iid} launched {e.get('implemented_on')} "
            f"({e.get('match')}, {e.get('matched_by')}); experiment "
            + ("filed" if result.get("written") else "already on file")
            + f": {payload['name']!r}")
        n["filed"] += 1
    return n


def bucket_summary(triage: list) -> str:
    """One log line: how many campaigns landed in each bucket, and the
    impact order of the ones to act on or test."""
    buckets = [t.get("bucket") for t in triage]
    order = [t["campaign"] for t in triage
             if t.get("bucket") in ("act", "test")
             and (t.get("impact") or {}).get("impact_score") is not None]
    return ("buckets: " + ", ".join(f"{buckets.count(b)} {b}"
                                    for b in creative_mod.BUCKETS)
            + (f"; impact order: {order[:5]}" if order else
               "; no impact figures (018 not applied, or no single-goal "
               "campaign to act on or test)"))


def clamp_summary(triage: list) -> str:
    """One log line: what the reply recommended, and how often the diagnosis
    had to move it. A run where every test became a monitor is a prompt (or a
    diagnosis) worth reading, and only the log shows the trend."""
    issues = [p for t in triage for p in t.get("issues") or []]
    types = [(p.get("recommendation") or {}).get("type") for p in issues]
    moved = {k: len([p for p in issues if p.get(k)])
             for k in ("type_adjusted_from", "confidence_adjusted_from",
                       "priority_adjusted_from", "test_plan_dropped",
                       "basis_unknown")}
    trimmed = len([x for t in triage
                   for x in range(t.get("issues_trimmed") or 0)])
    return (f"{len(issues)} issue(s): {types.count('act')} act, "
            f"{types.count('test')} test, {types.count('monitor')} monitor; "
            f"adjusted: {moved['type_adjusted_from']} type, "
            f"{moved['confidence_adjusted_from']} confidence, "
            f"{moved['priority_adjusted_from']} priority, "
            f"{moved['test_plan_dropped']} test plan(s) dropped, "
            f"{moved['basis_unknown']} with no known basis, "
            f"{trimmed} trimmed over the caps")


def fit(pack: dict, facts: dict, previous_note: str = "") -> tuple:
    """(prompt, the facts as sent, tier, keep): the prompt, shrunk until it
    fits TRIAGE_BUDGET.

    The campaign list is never cut -- the reply is one rating per campaign --
    so it shrinks by tier (creative.campaign_tier) instead, and the sections
    outside NEVER_COMPACTED lose their tails. STEPS is the order. If even the
    last step is over budget the prompt goes anyway and the log says so: an
    over-budget prompt costs attention, a skipped review costs the day.
    """
    whole = {k: facts[k] for k in NEVER_COMPACTED if k in facts}
    rest = {k: v for k, v in facts.items() if k not in whole}
    question, sent, tier, keep = "", {}, None, None
    for tier, keep in STEPS:
        sent = {**whole, **creative_mod.compact(rest, keep)}
        if "campaigns" in whole:
            sent["campaigns"] = [creative_mod.campaign_tier(c, tier)
                                 for c in whole["campaigns"]]
        question = creative_mod.SUGGESTIONS_PROMPT.format(
            brand=pack["brand"], since=pack["since"], until=pack["until"],
            days=pack["days"], settled=pack["settled_through"],
            unsettled=pack["unsettled_days"],
            previous_note=previous_note or creative_mod.previous_note(None, True),
            facts=json.dumps(sent, separators=(",", ":"), ensure_ascii=False,
                             default=str))
        if len(question) <= TRIAGE_BUDGET:
            return question, sent, tier, keep
    log(f"WARNING: the triage prompt is {len(question)} chars, over "
        f"TRIAGE_BUDGET ({TRIAGE_BUDGET}) even at tier {tier} with {keep} "
        f"row(s) per section; sending it anyway")
    return question, sent, tier, keep


def build_question(pack: dict, facts: dict, previous_note: str = "") -> str:
    """The prompt alone, as fit() shrinks it."""
    return fit(pack, facts, previous_note)[0]


#: Sections keyed by campaign name: each batch gets only its own campaigns'.
PER_CAMPAIGN = ("changes_since_last_review", "previous_issues", "followups")


def batch_facts(facts: dict) -> list:
    """The facts split into BATCH_SIZE-campaign batches, highest impact first.

    Every batch carries the shared sections whole (challenger pool,
    dimensions, windows, untagged) and only its own campaigns' entries of the
    per-campaign ones, so a campaign's previous issues and changes travel with
    it. Ordered by impact_score (None last, stable) so the first batch -- whose
    account_note is used -- is the one with the most money at stake.
    """
    camps = list(facts.get("campaigns") or [])
    camps.sort(key=lambda c: ((c.get("impact") or {}).get("impact_score")
                              is None,
                              -((c.get("impact") or {}).get("impact_score")
                                or 0)))
    groups = [camps[i:i + BATCH_SIZE]
              for i in range(0, len(camps), BATCH_SIZE)] or [[]]
    out = []
    for g in groups:
        names = {c.get("campaign") for c in g}
        b = {k: v for k, v in facts.items() if k != "campaigns"}
        b["campaigns"] = g
        for k in PER_CAMPAIGN:
            if isinstance(facts.get(k), dict):
                b[k] = {n: v for n, v in facts[k].items() if n in names}
        out.append(b)
    return out


def merge_sent(sents: list) -> dict:
    """One record of what was sent: the shared sections as the first batch
    sent them, and every batch's campaigns and per-campaign entries. Stored as
    the published `pack` and used by check_figures, so a card is checked
    against the facts the model actually had."""
    if not sents:
        return {}
    merged = dict(sents[0])
    merged["campaigns"] = [c for s in sents for c in s.get("campaigns") or []]
    for k in PER_CAMPAIGN:
        if any(isinstance(s.get(k), dict) for s in sents):
            merged[k] = {n: v for s in sents
                         for n, v in (s.get(k) or {}).items()}
    return merged


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
