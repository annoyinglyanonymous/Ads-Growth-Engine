# Ads Growth Engine

This repo reads. `growth-engine` writes. Neither migrates the other's schema.

That sentence is the whole boundary, and most of what follows is a consequence of it.

One clarification, because it looks like an exception and is not: `intel live`
makes live Meta API calls. It reads. It persists nothing — not to Meta, not to
`public.*`, not to `ads.*`. The *import*, meaning anything that puts Meta data
in a table, is still growth-engine's and only growth-engine's.

---

## What this is

The intelligence half of the ads system. `Desktop\growth-engine` imports Meta's
data into `public.meta_*` and produces the copy; this repo derives every rate
and comparison from that data, holds the few facts that are ours rather than
Meta's, and answers questions about them.

```
  growth-engine                         Ads Growth Engine (here)
  ─────────────                         ────────────────────────
  owns public.*                         owns ads.*
   • meta_ads/ importer      ────────▶   SELECT on a named list of public.*
   • copy production                     INSERT on nothing in public
   • the claim gate, approvals           • metrics: views + functions
   • KB + brand files                    • the angle bank and creative tags
  ledger: public.schema_migrations       • experiment memory
  files 001…046                          • the dashboard, on 127.0.0.1:8001
                                         • python -m intel
            │                           ledger: ads.schema_migrations, 001…006
            └─────────── one Supabase ───┘
```

One database, two schemas, two roles. A separate database would have been
tidier and is impossible: the value here is joining ad performance to
`campaign_assets`, `campaign_angles` and `ad_reviews`, and Postgres cannot join
across databases without a copy — and a copy is the staleness the whole design
avoids.

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
```

`127.0.0.1`, never `localhost` — on Windows `localhost` resolves `::1` first and
uvicorn bound to `127.0.0.1` is not listening there, which presents as a refused
connection against an app that is plainly running.

Every verb prints JSON to stdout and one sentence to stderr on failure. There is
one write verb, `intel record`, and it takes a path rather than stdin.

## Warehoused vs live

Almost everything here is **warehoused**: growth-engine imports daily rows into
`public.meta_*` and this repo derives every rate from them. That is what makes
"this week against the last six months" one query instead of a hundred API
calls, and it is the only thing that holds up across two brands.

The cost is staleness. Two things manage it:

- `ads.settled_through()` — Meta restates attributed conversions for ~3 days and
  an insights date is a day in the *account's* timezone. Every verb reports
  `unsettled_days` against this, so a partial day never reads as a decline.
- **The scheduled pull** — `growth-engine\scripts\sync.py`, twice daily via
  Task Scheduler. Before it existed, the staleness window was not 24 hours, it
  was "whenever anyone remembered", which is worse because it is invisible.

**`intel live` is the one live call**, and the point of it is the *diff*, not
the list. It reports ads running now that no pull has seen, ads the warehouse
still believes are active, and budgets or optimisation goals that moved since
the last import. That seam is where warehoused analysis quietly goes wrong and
neither half can see it alone.

It deliberately returns **no spend, no CPA, no conversions**. Live insights are
the slow, rate-limited call, and a live number beside a warehoused one in the
same answer invites comparing two things measured differently. Structure is
live; performance is warehoused; say which is which.

Nothing here pulls history. That is `growth-engine`:

```
cd ..\growth-engine
python scripts\sync.py                     # what the scheduler runs
python -m meta_ads --pull --brand renegade  # one brand, by hand
```

A pull takes minutes against a rate limit, which is why no page in either repo
fires one.
