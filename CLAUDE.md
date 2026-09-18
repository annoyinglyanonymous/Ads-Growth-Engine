# Ads Growth Engine

Everything Meta is here. `growth-engine` writes the copy and approves it.

That sentence is the whole boundary, and most of what follows is a consequence
of it. It replaced an older one -- "this repo reads, growth-engine writes" --
which stopped being true when the importer moved in. The old line described
the credentials; this one describes the job, and a boundary people can restate
from memory is the only kind that survives.

The read-only guarantee did not go away, it got smaller and more exact. It is
no longer "nothing in this folder can write". It is:

    intel/, ui.py, ask.py   read ads.* through db.py, which has no cursor
    intel/record.py         writes ads.* proposals, and only those
    meta_ads/               writes public.meta_*, and nothing else

Three pools, one per job, each scoped by a test rather than by a promise
(tests/test_read_only.py). A read verb still cannot write, and now the thing
stopping it is a named list instead of the absence of a folder.

---

## What this is

The ads system. This repo imports Meta's data into `public.meta_*`, derives
every rate and comparison from it, holds the few facts that are ours rather
than Meta's, and answers questions about them. `growth-engine` produces the
copy those ads run, approves it, and holds the brand knowledge.

```
  Ads Growth Engine (here)                    growth-engine
  ────────────────────────                    ─────────────
  imports and owns public.meta_*              owns the rest of public.*
  owns ads.*                                   • copy production
   • meta_ads/ -- the Graph importer   ◀────    • the claim gate, approvals
   • metrics: views + functions                 • KB + brand files
   • the angle bank and creative tags           • the meta-ads SKILL, which
   • experiment memory                            writes the copy and shares
   • the dashboard, on 127.0.0.1:8001             a name with nothing else
   • python -m intel, python -m meta_ads
  ledgers: ads.schema_migrations 001…009      ledger: public.schema_migrations
           (public.meta_* DDL still sits              files 001…046
            in growth-engine's 042 and 046,
            applied, and is not re-homed)
            │                                        │
            └──────────── one Supabase ──────────────┘
```

One database, three schemas' worth of concerns, three roles. A separate
database would have been tidier and is impossible: the value here is joining ad
performance to `campaign_assets`, `campaign_angles` and `ad_reviews`, and
Postgres cannot join across databases without a copy — and a copy is the
staleness the whole design avoids.

**Two things share a name and are unrelated.** `meta_ads/` is the Python
package that imports; `meta_ads` is also a CHANNEL in growth-engine, where a
deliverable runs on `email`, `meta_ads` or `video`. And `.claude/skills/
meta-ads/` over there writes ad copy — it is not this importer, it was moved
here once by mistake and moved straight back.

**`tracking.py` is a deliberate copy.** growth-engine stamps utm values onto
approved assets with it; `meta_ads.parse` reads them back off an ad here. Those
two halves are one convention and nothing else makes them agree, so the match
test round-trips through both real modules — and `tests/test_tracking_is_a_
faithful_copy.py` fails when the copies drift. Never edit this copy; copy
growth-engine's over it.

## The rules

**You never conclude anything.** You may propose an angle, propose an
experiment and file a creative tag. You may not set `ads.angle.status` to
`active`, you may not write `ads.experiment.conclusion`, and there is no verb
that tries. The schema enforces it (`angle_active_is_signed`,
`experiment_conclusion_attributed`) and the write shapes do not accept those
fields. This mirrors growth-engine's `auth.assert_can_approve`: an agent may
tighten and may not loosen.

**You never compute a rate, a delta or a share yourself.** Every number comes
out of a function in `migrations/003_ads_metrics.sql`. If a number you need is
not in a verb's output, that is a missing verb — say so, and say which one.
Doing the arithmetic in your head is how the dashboard card and your sentence
come to disagree, and whichever one the reader is looking at is the one they
act on.

**You never apply a migration.** `ads_migrate.py --apply` is denied in
`.claude/settings.json`. Prepare the DDL, run `--print`, hand it over. This
database is shared and several sessions run against it.

**Facts and opinions are labelled.** The numbers a function returns are facts.
Your reading of them is an opinion. Performance is evidence about what happened,
not a verdict on what to do. growth-engine's `review/context.py:121` says this
better and the skills quote it verbatim.

## Things that will bite you

**Zero rows is the failure mode, not an error.** Every `public.meta_*` table has
RLS on with no policy. `ads_reader` is `NOBYPASSRLS`, so without the policies in
`006_ads_roles.sql` every query succeeds and returns nothing, and every metric
reads `$0.00`. If the dashboard is empty, check `006` before you check the
importer.

**The last three days are not final.** Meta restates attributed conversions.
`ads.settled_through()` is the honest edge; every verb reports `unsettled_days`
and you must repeat it. "CPA rose this week" measured across a partial day is
the most common false alarm in this system.

**Reach cannot be summed and frequency is not what you think.** Daily reach is
deduplicated within the day, so a window sum counts the same person repeatedly —
`ads.window_metrics` returns `reach_best_day`, not a total. Daily `frequency` is
impressions ÷ *that day's* reach, so it sits near 1.0–1.3; cumulative frequency
is not in this database at all. Say "daily frequency", never "frequency".

**CPL is not comparable across optimization goals.** An ad group optimising
`LINK_CLICKS` and one optimising `OFFSITE_CONVERSIONS` are different animals
(042 says so). `optimization_goal` is carried down to the fact for exactly this
reason — segment on it or say you didn't.

**Meta's adset is called `ad_group` here.** The word "adset" appears nowhere in
the `ads` schema. Neutral level vocabulary is account / campaign / ad_group / ad,
so Google slots in as a union branch rather than a migration.

**A tag is keyed on `(ad_key, copy_hash)`.** Rewrite the copy and the tag is
gone, deliberately — a tag that survives a rewrite describes an ad that no
longer exists.

## Running it

```
python -m intel status --brand renegade      # start here; says what is stale
python -m intel live   --brand renegade      # what is running RIGHT NOW
python -m intel overview --brand renegade --days 28
python -m intel why --brand renegade --days 7
python -m intel fatigue --brand renegade
python -m intel coverage --brand renegade

python -m main                               # the dashboard, 127.0.0.1:8001
python ads_migrate.py --status

python -m meta_ads --list-accounts           # the import half, also here now
python -m meta_ads --add-account act_XXXX --brand renegade
python -m meta_ads --pull --brand renegade   # minutes, against a rate limit
python scripts/sync.py                       # what Task Scheduler runs
```

`127.0.0.1`, never `localhost` — on Windows `localhost` resolves `::1` first and
uvicorn bound to `127.0.0.1` is not listening there, which presents as a refused
connection against an app that is plainly running.

Every verb prints JSON to stdout and one sentence to stderr on failure. There is
one write verb, `intel record`, and it takes a path rather than stdin.

## Warehoused vs live

Almost everything here is **warehoused**: `meta_ads/` imports daily rows into
`public.meta_*` and the rest of this repo derives every rate from them. That is what makes
"this week against the last six months" one query instead of a hundred API
calls, and it is the only thing that holds up across two brands.

The cost is staleness. Two things manage it:

- `ads.settled_through()` — Meta restates attributed conversions for ~3 days and
  an insights date is a day in the *account's* timezone. Every verb reports
  `unsettled_days` against this, so a partial day never reads as a decline.
- **The scheduled pull** — `scripts\sync.py`, twice daily via Task Scheduler.
  Before it existed, the staleness window was not 24 hours, it was "whenever
  anyone remembered", which is worse because it is invisible. **It is not
  registered yet**, so today that window is still "whenever anyone remembered".

**`intel live` is the one live call**, and the point of it is the *diff*, not
the list. It reports ads running now that no pull has seen, ads the warehouse
still believes are active, and budgets or optimisation goals that moved since
the last import. That seam is where warehoused analysis quietly goes wrong and
neither half can see it alone.

It deliberately returns **no spend, no CPA, no conversions**. Live insights are
the slow, rate-limited call, and a live number beside a warehoused one in the
same answer invites comparing two things measured differently. Structure is
live; performance is warehoused; say which is which.

Pulling history is now this repo's job too — it used to be growth-engine's, and
that is the one line of this file most likely to be remembered wrong:

```
python scripts\sync.py                      # what the scheduler runs
python -m meta_ads --pull --brand renegade   # one brand, by hand
```

A pull takes minutes against a rate limit, which is why **no page in either
repo fires one** and why `ask.py` refuses to route to `live`. A browser that
gave up half way through leaves a `running` row nothing ever closes.
