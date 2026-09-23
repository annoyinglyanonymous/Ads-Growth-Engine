"""The tagging pass. Labels what each ad ARGUES, so angles become countable.

    python scripts/tag.py                    # every brand with an account
    python scripts/tag.py --brand renegade   # just one
    python scripts/tag.py --dry-run          # build the batches, ask nobody
    python scripts/tag.py --limit 40         # one batch, to see what it does

WHY THIS EXISTS

Meta knows an ad spent $4,984 and returned 166 leads. It has no idea what the
ad SAID. Without that, you can compare ads and never compare ideas: "this ad
costs $30 a lead" is available, "the franchise pitch costs $30 and the
valuation pitch $52" is not, and the second is the one that changes what gets
written next.

`ads.facet_performance(brand, since, until, <dimension>)` already slices spend
by hook, offer, audience and angle. It is written, granted and tested. Its
input is `ads.ad_facet`, and that table has been empty since the schema was
created -- so every creative dimension on the site resolves to one NULL bucket.
This fills it.

WHY A PASS AND NOT A PERSON

The schema expected one. `ads.ad_facet.source` is checked against
('inherited', 'tagged', 'operator') -- three ways a tag arrives and only the
last is somebody typing. intel/record.py's upsert already carries

    -- A person's tag is never overwritten by a pass.
    where ads.ad_facet.source <> 'operator'

so the guard was written before the thing it guards against. Nothing here can
touch a tag a person filed, and `source` is a SQL literal in record.py rather
than a field this can supply.

WHY NOT READ THE AD NAMES

They are unreliable and it is not close. The account names ads
"Message | Style | Format", but the leading segment is as often a format or a
person: 'Static' leads 48 ads, 'Tweet' 2, 'David' 1, 'Lily' 1, 'GIF' 1. The
argument is in the body -- "Own the book you've been building", "No broker. No
earnouts. Cash at close." -- which is why this reads the copy.

WHAT IT COSTS, AND WHY THAT FALLS

The first run pays for every ad that has spent. After that it selects on
`(ad_key, copy_hash)` having no facet, so a run only sees ads that are new or
whose copy changed -- a handful on an ordinary day. The expensive run happens
once, the same shape as the importer's `updated_since`.

Exit codes, for the scheduler to branch on:
    0  filed, or nothing needed filing
    1  something failed -- read logs/tag.log
    2  a run was already in progress
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

# Imported at module scope, before any loop exists: importing db installs
# WindowsSelectorEventLoopPolicy, and psycopg cannot run async without it.
import chat  # noqa: E402
import db  # noqa: E402
import identity  # noqa: E402
from intel.context import UnknownBrand  # noqa: E402
from intel.creative import PROMPT_BUDGET  # noqa: E402
from intel.shapes import ShapeError, validate  # noqa: E402

LOCK = PROJECT_ROOT / ".tag.lock"
LOG_DIR = PROJECT_ROOT / "logs"
TAG_DIR = PROJECT_ROOT / "tags"

#: A pass is several model calls of about a minute. Thirty minutes means the
#: holder died without cleaning up.
STALE_LOCK_AFTER = timedelta(minutes=30)

#: Most ads per model call, and a CEILING rather than the batch size.
#:
#: Forty is small enough that losing a batch to an unparseable reply is cheap.
#: But a count alone is not a budget: this account's bodies average ~200
#: characters and forty fit comfortably in 17,000, while an account writing
#: 400-character bodies puts the same forty at 26,500 -- past the budget, where
#: the spawn fails and the pass reports "the session returned nothing" about a
#: prompt that was never sent.
#:
#: So batches are filled by MEASURING, and this is only the cap.
BATCH = 40

#: How far back an ad must have spent to be worth labelling. Everything else
#: is paused or never delivered -- 243 of this account's 945 ads have spent in
#: the last month, and they carry essentially all of the money.
WINDOW_DAYS = 28


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  {message}"
    print(line, flush=True)
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with (LOG_DIR / "tag.log").open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def take_lock() -> bool:
    if LOCK.exists():
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                LOCK.stat().st_mtime, timezone.utc)
        except OSError:
            age = timedelta(0)
        if age < STALE_LOCK_AFTER:
            log(f"another tagging pass has been running for {age} -- doing "
                f"nothing. If that is wrong, delete {LOCK.name}.")
            return False
        log(f"breaking a stale lock ({age} old)")
        LOCK.unlink(missing_ok=True)
    LOCK.write_text(f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}",
                    encoding="utf-8")
    return True


async def brands(only: str | None) -> list[str]:
    rows = await db.fetch_all(
        "select distinct b.slug from ads.brand b "
        "  join ads.ad_account a on a.brand_id = b.id "
        " where a.active order by b.slug")
    slugs = [r["slug"] for r in rows]
    if only:
        return [only] if only in slugs else []
    return slugs


async def vocabulary() -> tuple[list[dict], list[dict]]:
    """The seeded hook and offer lists, with the sentences that separate them.

    Read from the database rather than hard-coded, because `hook` and `offer`
    are foreign keys: a value this pass invents is a row record.py cannot file.
    The definitions go to the model too -- "contrast" and "pattern_interrupt"
    are not self-explanatory, and the seeded sentence is exactly the
    discrimination a labeller needs.
    """
    hooks = await db.fetch_all(
        "select slug, definition from ads.hook order by slug")
    offers = await db.fetch_all(
        "select slug, definition from ads.offer order by slug")
    return hooks, offers


def dedupe(ads: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """One representative per distinct wording, and the map back to the rest.

    THE AUDIT FINDING THIS EXISTS FOR. 135 of the first run's 243 ads shared
    byte-identical headline and body with at least one other -- the account
    re-runs the same copy under different creative names. Labelling each ad
    separately asked the model the same question up to seven times, and it
    answered differently: 20 of 48 duplicate groups disagreed on hook, audience
    or confidence, across 30% of tagged spend. "Sales stall when you're buried
    in servicing." came back `stat`/`none` on one ad and `story`/`call` on
    another.

    No prompt can fix that; it is variance, and asking once is the only way to
    remove it. Labelling the wording rather than the ad also removes 56% of the
    model calls, which is the same saving arriving as a side effect.

    Keyed on copy_hash, which ads.ad_copy already computes as the md5 of every
    field:ordinal:text in order -- so two ads share a key only if their copy is
    identical, not merely similar.
    """
    groups: dict[str, list[dict]] = {}
    for ad in ads:
        groups.setdefault(ad["copy_hash"], []).append(ad)
    # The first by ad_key, so a re-run picks the same representative and the
    # rationale quotes the same ad as last time.
    reps = [sorted(g, key=lambda a: str(a["ad_key"]))[0]
            for g in groups.values()]
    return sorted(reps, key=lambda a: str(a["ad_key"])), groups


async def needs_tagging(slug: str, limit: int | None,
                        retag: bool = False) -> list[dict]:
    """Ads that have spent, carry copy, and have no facet for that copy.

    Keyed on (ad_key, copy_hash), which is what makes the pass cheap after its
    first run AND correct when copy changes: rewrite an ad and its old tag
    stops matching, so it comes back round to be relabelled. A tag that
    survived a rewrite would describe an ad that no longer exists.
    """
    rows = await db.fetch_all(
        """
        select c.ad_key, c.copy_hash, d.name, d.format, d.cta,
               c.first_headline, c.first_body
          from ads.ad_copy c
          join ads.ad d on d.ad_key = c.ad_key
          join ads.brand b on b.id = d.brand_id
         where b.slug = %s
           and exists (select 1 from ads.fact_ad_day f
                        where f.ad_key = c.ad_key
                          and f.day >= (select max(day) - %s
                                          from ads.fact_ad_day))
           and (%s or not exists (select 1 from ads.ad_facet fa
                                   where fa.ad_key = c.ad_key
                                     and fa.copy_hash = c.copy_hash))
         order by c.ad_key
        """,
        (slug, WINDOW_DAYS - 1, retag))
    return rows[:limit] if limit else rows


#: The closed audience list, replacing "taken from the copy itself".
#:
#: Free text produced 31 values for what the audit found to be 6 audiences --
#: "agency owners" and "p&c agency owners" split one audience roughly in half,
#: and appeared WITHIN identical copy. Every per-audience number was fragmented
#: across two to nine synonyms, which is worse than no audience at all: it
#: looks like a finding.
AUDIENCES = ("p&c agency owners", "agency sellers", "captive agents",
             "insurance salespeople", "licensed insurance producers",
             "franchise buyers")

#: How much of a long body to show, at each end.
HEAD, TAIL = 420, 220


def _head_and_tail(body: str) -> str:
    if len(body) <= HEAD + TAIL:
        return body
    return f"{body[:HEAD]} [...] {body[-TAIL:]}"


def build_prompt(slug: str, hooks: list[dict], offers: list[dict],
                 ads: list[dict]) -> str:
    def _vocab(rows):
        return "\n".join(f"  {r['slug']}: {r['definition']}" for r in rows)

    lines = []
    for a in ads:
        lines.append(json.dumps({
            "ad_key": str(a["ad_key"]),
            "name": a.get("name"),
            "headline": a.get("first_headline"),
            # HEAD AND TAIL, not the first 400 characters.
            #
            # Three ads were labelled `offer: none` because the ask sat past
            # the cut -- and the model said so in its own rationale ("the
            # truncated body makes no ask beyond the CTA"), while the unseen
            # tail read "Book the call and we'll build the roadmap." A body
            # that gets cut loses its close, which is exactly the part that
            # carries the offer.
            "body": _head_and_tail(a.get("first_body") or ""),
            "cta_button": a.get("cta"),
        }, ensure_ascii=False))

    return f"""\
Label each of these {slug} ads by what its copy ARGUES.

Each entry is one distinct WORDING, which may run under several ad names. You
are labelling the words.

HOOK -- how the first line opens. Exactly one of:
{_vocab(hooks)}

OFFER -- what the copy asks the reader to do. Exactly one of:
{_vocab(offers)}

Two things those definitions do not settle, and both went wrong on the first
run:

  `stat` needs a FIGURE in the opening -- a number, a percentage, an amount, a
  year, a count. "31 years", "over 11 acquisitions", "$2-3M". A general
  assertion, an opinion, or a sentence containing "should" is NOT a stat. Of 91
  ads labelled `stat` last time, four opened with a number. A claim with no
  figure in it goes to whichever hook above describes a claim -- and to null if
  there is none, which is a gap worth seeing rather than a stat worth doubting.

  `callout` needs the first line to NAME the audience -- "captive agents",
  "P&C agency owners". Addressing the reader as "you" is not naming them.

  `demo` and `testimonial` describe what the creative SHOWS and whose voice it
  speaks in, and you are given text only. Use `demo` only where the copy itself
  walks through the product working; a list of features is not a demo. Use
  `testimonial` only where the copy is written in a customer's first person.

Judge the hook from the FIRST sentence of the body, or from the headline when
there is no body. If none of the hooks above fits that sentence, return null
-- a
missing hook is a gap that can be seen, a wrong one is a number somebody
compares against.

AUDIENCE -- exactly one of:
{chr(10).join("  " + a for a in AUDIENCES)}
null when the copy addresses none of them clearly. Do not invent a value, do
not add a qualifier, and do not translate: Spanish copy addressing agency
owners is still "p&c agency owners". A noun phrase lifted from the copy
("business builders", "book of business") is not an audience.

CONFIDENCE -- about the AUDIENCE and the OFFER, not the hook. "stated" only
when the copy contains the audience words and the offer literally ("As a P&C
agency owner", "Book a free consultation"). If you are reading either from
"your agency", from "you", or from tone, it is "inferred". When in doubt,
"inferred". migrations/004 is explicit that "the agent writes 'inferred' for
anything it decides".

RATIONALE -- one short sentence quoting the words that decided it. This is
stored and read back by a person checking your work, so quote rather than
describe. If you write "opens", the words you quote must be the literal first
words of the headline or body; otherwise say "later in the body".

Judge the COPY, not the ad's name. The names here carry formats and people
("Static", "UGC", "David", "GIF"), not arguments.

Return a JSON array with one object per ad, every ad_key exactly as given:

[{{"ad_key":"...","hook":"question","offer":"valuation","audience":"p&c agency owners","confidence":"inferred","rationale":"Opens \\"do you know what it's worth?\\" and offers a free estimate."}}]

THE ADS

{chr(10).join(lines)}
"""


def plan_batches(slug: str, hooks: list[dict], offers: list[dict],
                 ads: list[dict]) -> list[list[dict]]:
    """Split the ads into batches that each FIT, rather than each count 40.

    Measured, not estimated. build_prompt is called on the candidate batch and
    the result is weighed, because the only number that matters is the length
    of the string that becomes one argv element -- and the preamble carries the
    two vocabularies with their definitions, which grow if somebody seeds a
    ninth hook.

    A single ad that will not fit alone is still emitted as its own batch: it
    will fail, and it should fail loudly as one ad rather than silently take
    thirty-nine others with it.
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    for ad in ads:
        trial = current + [ad]
        if len(trial) <= BATCH and (
                len(build_prompt(slug, hooks, offers, trial)) <= PROMPT_BUDGET
                or not current):
            current = trial
            continue
        batches.append(current)
        current = [ad]
    if current:
        batches.append(current)
    return batches


def clean(row: dict, allowed_hooks: set, allowed_offers: set,
          by_key: dict) -> tuple[dict | None, str | None]:
    """One returned row -> a filable payload, or a reason it was dropped.

    Every value is checked against the seeded vocabulary before it goes near
    record.py. hook and offer are foreign keys, so an invented value is a row
    the database refuses -- and a pass that files nothing because one label was
    imagined is worse than one that drops the row and says so.
    """
    key = str(row.get("ad_key") or "")
    ad = by_key.get(key)
    if ad is None:
        return None, f"ad_key {key[:12]!r} was not in the batch"

    hook = row.get("hook") or None
    offer = row.get("offer") or None
    if hook and hook not in allowed_hooks:
        return None, f"hook {hook!r} is not in ads.hook"
    if offer and offer not in allowed_offers:
        return None, f"offer {offer!r} is not in ads.offer"

    confidence = row.get("confidence")
    if confidence not in ("stated", "inferred"):
        return None, f"confidence {confidence!r} is not stated|inferred"

    rationale = (row.get("rationale") or "").strip()
    if not rationale:
        return None, "no rationale, and an unexplained tag cannot be checked"

    if not (hook or offer or row.get("audience")):
        # A facet with no angle, no hook, no offer and no audience describes
        # nothing. Filing it would only make the ad stop appearing in this
        # pass's own selection next time.
        return None, "nothing was labelled"

    payload = {
        "ad_key": key,
        # The hash the copy had when it was READ. If the ad is rewritten
        # between the read and the write, this row belongs to the wording that
        # was labelled, not to the new one.
        "copy_hash": ad["copy_hash"],
        "brand": ad["brand"],
        "confidence": confidence,
        "rationale": rationale[:500],
    }
    for field, value in (("hook", hook), ("offer", offer),
                         ("audience", row.get("audience"))):
        if value:
            payload[field] = str(value).strip()[:80]
    try:
        validate("ad_facet", payload)
    except ShapeError as exc:
        return None, str(exc)[:120]
    return payload, None


async def run_brand(slug: str, dry_run: bool, limit: int | None,
                    retag: bool = False) -> int:
    hooks, offers = await vocabulary()
    ads = await needs_tagging(slug, limit, retag)
    if not ads:
        log(f"{slug}: every ad that has spent already carries a tag for its "
            f"current copy -- nothing to do")
        return 0

    for a in ads:
        a["brand"] = slug

    # One question per distinct wording. The answer is fanned back out to every
    # ad that shares it, so ads with identical copy cannot end up with
    # different labels -- which 20 of 48 duplicate groups did on the first run.
    wordings, by_hash = dedupe(ads)
    planned = plan_batches(slug, hooks, offers, wordings)
    log(f"{slug}: {len(ads)} ad(s) need a tag, {len(wordings)} distinct "
        f"wording(s), {len(planned)} batch(es) (cap {BATCH}, sized to fit "
        f"{PROMPT_BUDGET} chars)")

    allowed_hooks = {h["slug"] for h in hooks}
    allowed_offers = {o["slug"] for o in offers}
    by_key = {str(a["ad_key"]): a for a in wordings}

    proposals: list[dict] = []
    dropped: list[dict] = []
    failed_batches = 0

    # `planned`, not a second call on `ads`. Re-planning from the full ad
    # list here asked the model about all 243 -- duplicates included -- and
    # then fanned each answer out again, which is both the cost the dedupe
    # exists to avoid and the inconsistency it exists to prevent.
    for n, batch in enumerate(planned, start=1):
        prompt = build_prompt(slug, hooks, offers, batch)
        if dry_run:
            log(f"{slug}: batch {n}: {len(batch)} ad(s), "
                f"{len(prompt)} chars -- --dry-run, so nobody was asked")
            continue
        try:
            rows = await chat.classify(prompt)
        except Exception as exc:
            # Per BATCH isolation. One batch that would not parse must not cost
            # the other two hundred ads their labels.
            failed_batches += 1
            log(f"{slug}: batch {n} FAILED  {type(exc).__name__}: "
                f"{str(exc)[:160]}")
            continue

        for row in rows if isinstance(rows, list) else []:
            payload, why = clean(row if isinstance(row, dict) else {},
                                 allowed_hooks, allowed_offers, by_key)
            if not payload:
                dropped.append({"row": row, "why": why})
                continue
            # The label belongs to the WORDING, so every ad carrying that
            # wording gets it -- with its own ad_key, and the same copy_hash
            # by definition.
            for twin in by_hash.get(payload["copy_hash"], []):
                proposals.append({**payload, "ad_key": str(twin["ad_key"])})
        log(f"{slug}: batch {n}: {len(rows)} returned, "
            f"{len(proposals)} usable so far")

    if dry_run:
        return 0

    if not proposals:
        log(f"{slug}: nothing usable came back from {failed_batches} failed "
            f"batch(es) and {len(dropped)} dropped row(s)")
        return 1

    # WRITTEN DOWN BEFORE IT IS FILED. record.py takes a path rather than
    # stdin, because "a payload that arrived down a pipe leaves nothing on
    # disk to look at when the row turns out to be wrong". A pass writes two
    # hundred rows at once; the file is what makes any one of them checkable
    # afterwards.
    TAG_DIR.mkdir(exist_ok=True)
    out = TAG_DIR / f"{date.today().isoformat()}-{slug}.json"
    out.write_text(json.dumps({
        "brand": slug,
        "proposed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "considered": len(ads),
        "failed_batches": failed_batches,
        "proposals": proposals,
        "dropped": dropped,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    log(f"{slug}: wrote {out.name} -- {len(proposals)} proposed, "
        f"{len(dropped)} dropped")

    # Imported here, not at module scope, so that a --dry-run runs in a process
    # that has never imported the writer.
    from intel import record as record_mod

    who = identity.cli_operator()
    filed = skipped = errored = 0
    for payload in proposals:
        try:
            res = await record_mod.record_payload("ad_facet", payload, who)
        except Exception as exc:
            errored += 1
            log(f"{slug}: {payload['ad_key'][:12]} FAILED  "
                f"{type(exc).__name__}: {str(exc)[:120]}")
            continue
        if res.get("written"):
            filed += 1
        else:
            # Almost always an operator tag, which is the guard working.
            skipped += 1
    await record_mod.close()

    log(f"{slug}: filed {filed}, left alone {skipped}, errored {errored}")
    return 1 if (errored or failed_batches) else 0


async def run(only: str | None, dry_run: bool, limit: int | None,
              retag: bool = False) -> int:
    await db.open_read()
    try:
        slugs = await brands(only)
        if not slugs:
            log(f"no active ad account for {only or 'any brand'} -- nothing "
                f"to tag")
            return 0
        worst = 0
        for slug in slugs:
            try:
                worst = max(worst, await run_brand(slug, dry_run, limit,
                                                   retag))
            except UnknownBrand as exc:
                log(f"{slug}: {exc}")
                worst = 1
            except Exception as exc:
                log(f"{slug}: FAILED  {type(exc).__name__}: {exc}")
                worst = 1
        return worst
    finally:
        await db.close_read()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand")
    p.add_argument("--dry-run", action="store_true",
                   help="select the ads and build the batches; ask nobody")
    p.add_argument("--retag", action="store_true",
                   help="relabel ads that already carry a tag for their "
                        "current copy. For replacing a pass filed under an "
                        "earlier prompt; a person's tag is still never "
                        "overwritten, record.py refuses that")
    p.add_argument("--limit", type=int, default=None,
                   help="tag at most this many ads, to see what it does "
                        "before paying for all of them")
    a = p.parse_args()

    if a.dry_run:
        return asyncio.run(run(a.brand, True, a.limit, a.retag))

    if not take_lock():
        return 2

    log(f"tagging starting ({a.brand or 'all brands'})")
    try:
        code = asyncio.run(run(a.brand, False, a.limit, a.retag))
    except Exception as exc:
        log(f"tagging ABORTED  {type(exc).__name__}: {exc}")
        code = 1
    finally:
        LOCK.unlink(missing_ok=True)

    log(f"tagging finished, exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
