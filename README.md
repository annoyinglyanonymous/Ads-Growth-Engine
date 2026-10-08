# Ads Growth Engine

Everything Meta is here. This repo imports Meta ad data into `public.meta_*`, derives every rate from it, holds the angle bank and experiment record, and answers questions about all of it — through a dashboard and through a CLI the agent uses.

It was built as one half of a pair and now runs alone, which is worth knowing before you go looking for data: `public.campaigns`, `campaign_angles`, `campaign_assets` and `ad_reviews` are empty and stay empty, so the angle bank starts empty and the automatic tagging path is unavailable. The full list under **What is structurally empty** describes what is intentionally absent and what to do instead.

**Status: importing.** Migrations 001–014 are applied. The first structure pull has run and `public.meta_ads` holds real rows. Two things are still missing before a number appears on the dashboard, and both are step 2 and step 3 below: the insights phase has not completed, and `ads.conversion_definition` is empty, which makes every conversion count and every CPA null by construction.

---

## What has to happen, in order

### 1. Get real data in — nothing below works without it

**This runs here.** It used to run elsewhere, and that is the line of this file most likely to be remembered wrong.

**a. Make the token.** Business Manager → System User → assign the ad account(s) → generate a token with **`ads_read` and nothing else**. Not `ads_management`; this system never writes to an ad account and the token should not be able to. A System User token and not a personal one — a personal token expires at 60 days and the failure is silent, which means the scheduled pull stops importing and nothing says why (`config.py`, on `meta_access_token`).

Put it in **this repo's** `.env` as `META_ACCESS_TOKEN=`. The key is in `.env.example` and absent from the live `.env`.

**A note on scale, learned the hard way.** A large account will refuse the first `/ads` request with "Please reduce the amount of data you're asking for". That is handled — `client.py` halves the page size and keeps the smaller one — but the pull takes minutes and the insights window is chunked into 7-day spans for the same reason. Do not shorten either without reading why they are there.

**b. Migration 046 must exist BEFORE the first pull. It already does** — `046_meta_change_log.sql` is applied in the ledger. Left here because it is the one step whose cost is unrecoverable if it is ever skipped on a fresh install.

`046` captures budget, status and optimisation-goal edits by trigger. `store.upsert_campaigns` / `upsert_adsets` / `upsert_ad` are `on conflict do update`, so without it the old value is overwritten with no trace — and **Meta will not tell you what an ad set's budget was last Tuesday.** Every pull that runs before 046 exists destroys a day of explanation that nothing recovers. Most of the time "why did CPA go up" is answered by a budget change, not by creative fatigue.

**c. Register the accounts.**

```bash
python -m meta_ads --add-account act_XXXXXXXX --brand brand-a --label "..."
python -m meta_ads --add-account act_YYYYYYYY --brand brand-b --label "..."
python -m meta_ads --list-accounts
```

Confirm `currency` and `timezone_name` come back after the first pull. The timezone is load-bearing: an insights date is a day in the *account's* zone, and `ads.settled_through()` reads it.

**d. First pull, then reconcile.**

```bash
python -m meta_ads --pull --brand brand-a
```

Then, against Ads Manager, on a **closed** window (last full week, account timezone), per campaign:

- **Spend must match to the cent.** If it does not, the cause is the date boundary, not the importer.
- **Leads probably will not match, and that is expected.** `parse.LEAD_ACTION_TYPES` names three action types and your account may report under a custom conversion. **Do not fix this by editing that constant and re-pulling.** `ads.conversion_definition` (migration 002 here) makes "what counts as a lead" a row you change once, re-read across all history, with no replay and no rate limit. That is what 042 kept the whole `actions` array for.

**e. Backfill 13 months** — a full year plus year-over-year headroom — in **monthly chunks, oldest first, one account at a time**:

```bash
python -m meta_ads --pull --brand brand-a --since 2025-09-01 --until 2025-09-30
```

Oldest first matters: the watermark is now `max(until)`, so an out-of-order backfill cannot walk it backwards, but chunking in order also keeps a failure to one month. Check `meta_pulls` between chunks. **Only after step (d) reconciles** — a 13-month backfill of numbers you have not validated is 13 months of work to redo.

### 2. Apply this repo's migrations

```bash
python ads_migrate.py --status     # what is pending
python ads_migrate.py --print 006  # read one before it runs
python ads_migrate.py --apply      # a PERSON runs this; the agent cannot
```

`--apply` is denied in `.claude/settings.json`. That is deliberate: the database is shared and several sessions may run against it, so "never migrate unprompted" is a property of the tooling rather than a rule somebody remembers.

The first run needs a role that can `create schema` and `create role` — in Postgres that is `postgres`. Set `ADS_MIGRATE_URL` to the postgres URL for that run, or paste the files into the SQL editor; each carries its own `BEGIN/COMMIT`, so neither path is privileged.

**007 depends on the ledger's change log migration.** If it is not applied, 007 creates nothing and says so, and the reporting layer reports the change history as unavailable rather than reporting that nothing changed.

### 3. Create the two roles and verify — do not skip this

`006_ads_roles.sql` creates `ads_owner` and `ads_reader` as `NOLOGIN`. Give them passwords by hand and put the URLs in `.env`:

```sql
alter role ads_owner  with login password '...';   -- ADS_DATABASE_URL
alter role ads_reader with login password '...';   -- ADS_DATABASE_URL_RO
```

Then, **connected as `ads_reader`**:

```sql
select count(*) from ads.fact_ad_day;   -- must be a NUMBER
insert into ads.angle (...) values (...);  -- must fail: permission denied
```

**If the count is 0 while postgres sees rows, it is the RLS policies, not the import.** Every `public.meta_*` table has row-level security enabled with zero policies, and the app connects as a role that bypasses RLS. `ads_reader` does not. RLS on with no policy returns **zero rows, not an error** — every metric reads `$0.00` and it looks exactly like a broken importer. The migration adds the policies; this check is how you know they took.

### 3b. Put the pull on a schedule

Until this runs, the staleness window is "whenever anyone remembers", which is worse than a nightly job because it is invisible.

```bash
python scripts\sync.py --dry-run
powershell -ExecutionPolicy Bypass -File scripts\register_sync_task.ps1
```

Twice daily, 06:00 and 18:00. Twice rather than once because Meta restates conversions for ~3 days so the pull re-reads a rolling window anyway — a second run costs one extra window, not a second import — and it halves the age of the answer somebody gets at 5pm.

`sync.py` takes a lock so two runs cannot overlap, isolates brands so one failing does not cost the other its import, appends to `logs\sync.log`, and exits 0 / 1 / 2 (fine / something failed / already running) for Task Scheduler to show.

Backfills are deliberately not on the timer. They are chunked, oldest-first and watched between chunks.

### 3c. Optionally, the live token

Copy `META_ACCESS_TOKEN` into this repo's `.env` as well. It powers exactly one verb, `intel live`, which asks Meta what is running right now and writes nothing down. Everything else reads Postgres, so skipping this leaves the system fully working minus the "right now" question — and `intel status` reports which side is configured, so a rotation done in one repo and forgotten in the other is visible.

### 4. Define what counts as a conversion

Until `ads.conversion_definition` has rows for a brand, every conversion count and every CPA is zero or null — honestly, but uselessly. `intel status` says so.

### 5. Seed the angle bank

```bash
python scripts/seed_angles.py --brand brand-a
```

It **prints and does not file**. The approved vocabulary is promoted from the warehouse rather than invented — the system joins on the name, so a parallel vocabulary would mean every future campaign arrives untagged despite having carried its angle all along.

Then file the ones that should exist, and activate them in the UI. An angle stays `proposed` until a person signs it.

---

## Running it

```bash
python -m intel status --brand brand-a    # always start here
python -m main                             # dashboard, 127.0.0.1:8001
python -m pytest
```

`127.0.0.1`, never `localhost` — on Windows `localhost` resolves `::1` first and uvicorn bound to `127.0.0.1` is not listening there, which looks like a refused connection against an app that is plainly running. Port 8001 because another local service owns 8000.

### The verbs

`overview` · `compare` · `why` · `fatigue` · `ad` · `trend` · `angles` · `candidates` · `queue` · `coverage` · `versus` · `experiments` · `experiment` · `status` · `live`, and one writer: `record`.

All but `live` read the warehouse. `live` makes two structure calls to Meta and returns what is running now plus **where the warehouse has drifted from it** — ads no pull has seen, ads it still believes are active, budgets and optimisation goals that moved since the last import. No spend and no CPA: those are warehoused, and mixing a live number with a warehoused one in the same answer compares two things measured differently.

Every one prints JSON to stdout and one sentence to stderr on failure.

### The screens

| | |
|---|---|
| `/` | spend, conversions, CPA, CTR, CPM with window-over-window movement |
| `/why` | the CPA bridge: rate effect vs mix effect, beside account changes |
| `/creative` | fatigue, five named symptoms per ad |
| `/angles` | CPA by angle, what has never run, the untagged queue |
| `/experiments` | what was tested, and what would have counted as an answer |
| `/ad/{key}` | one ad: copy, tags, review, daily series |

---

## Things that will bite you

**Zero rows is the failure mode, not an error.** See step 3.

**The last three days are not final.** Meta restates attributed conversions. Every verb reports `unsettled_days` and a `caveat`; the dashboard shows a banner. A decline measured across a partial day is the most common false alarm here.

**Daily frequency is not cumulative frequency.** Meta's `frequency` is impressions ÷ *that day's* reach, so it sits near 1.0–1.3. The number people mean by "they have seen it four times" is not in this database, because daily reach cannot be summed. `ads.fatigue` detects acceleration and says so.

**Reach cannot be summed.** `window_metrics` returns `reach_best_day`.

**CPA is not comparable across optimisation goals.** An ad group optimising link clicks and one optimising conversions are different animals. `optimization_goal` is carried down to the daily fact for this reason.

**A tag is keyed on `(ad_key, copy_hash)`.** Rewrite the copy and the tag is gone, deliberately.

**Meta's adset is `ad_group` here.** The word "adset" appears nowhere in the `ads` schema, so Google slots in later as a union branch rather than a migration.

---

## What this repo cannot do

Approve an angle. Launch or conclude an experiment. Apply a migration. Write anything at all to the landing tables. Pull from Meta.

The first three are decisions and carry a person's name. The fourth is enforced by the database rather than by convention — the role that writes data holds `SELECT` on a named list of tables and `INSERT` on none of them.

The fifth is narrower than it used to be. No page pulls **in-process**: the dashboard's Refresh button spawns `scripts\sync.py --phase insights` as a detached subprocess and polls the warehouse for progress, so the credential that writes data never enters the web process and a closed tab cannot orphan a `running` row. A full pull still takes minutes against a rate limit, which is why the button is scoped to the insights phase.

The guarantee is not that the agent lacks a password. It is that **no code path writes a decision.**

---

**Topics:** `python`, `meta-ads`, `marketing-analytics`, `llm`, `automation`, `warehouse`, `ads-growth`
