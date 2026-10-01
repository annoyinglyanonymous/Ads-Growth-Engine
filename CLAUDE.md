# Ads Growth Engine

This repo is the whole ads system. It imports Meta's data into `public.meta_*`,
derives every rate and comparison from it, holds the few facts that are ours
rather than Meta's, and answers questions about them.

**It was designed as one half of a pair, and the other half is not in use.**
That matters more than it sounds, because the schema still carries the joins,
the seam views and the vocabulary of a two-repo system, and several tables that
a sibling repo would have filled are empty and will stay empty. A session that
does not know this spends its time looking for data that was never going to be
there. The list is in **What is structurally empty** below; read it before
concluding that something is broken.

---

## What this is

```
  Ads Growth Engine
  ─────────────────
  imports and owns public.meta_*
  owns ads.*
   • meta_ads/ -- the Graph importer
   • metrics: views + functions in migrations/
   • the angle bank and creative tags
   • experiment memory
   • the dashboard, on 127.0.0.1:8001
   • python -m intel, python -m meta_ads

  ledger: ads.schema_migrations, files 001…018, all applied
```

**The `public.meta_*` DDL is not in this repo.** Those tables exist and are
applied; their `create table` statements live in migration files this repo does
not contain, and they were never re-homed. `ads_migrate.py` manages
`ads.schema_migrations` only. So a change to `public.meta_ads` or
`public.meta_ad_insights` has no source file here to edit — write new DDL and
say plainly that it is new, rather than hunting for an original that is not
present.

## The read-only guarantee

It is not "nothing in this folder can write". It is:

```
intel/, ui.py, ask.py   read ads.* through db.py, which has no cursor
intel/record.py         writes ads.* proposals, and only those
meta_ads/               writes public.meta_*, and nothing else
```

Three pools, one per job, each scoped by a test rather than by a promise
(`tests/test_read_only.py`). A read verb cannot write, and the thing stopping it
is a named list rather than good intentions.

The dashboard's Refresh button does not widen that list: it spawns
`scripts\sync.py` as a separate process, so the credential that writes
`public.meta_*` never enters the web process.

## What is structurally empty, and why

These are not bugs and there is nothing here to fix. They are the shape of a
system whose other half is not running. Verified 2026-09-22 by reading as
`postgres`, which sees through RLS:

```
public.campaigns          0      public.campaign_assets   0
public.campaign_angles    0      public.ad_reviews        0
```

Consequences, in the order they will confuse you:

- **`ads.angle` is empty, so no ad can be tagged.** A tag (`ads.ad_facet`) may
  point at an `angle_id`, and there are no angles to point at. All 711 imported
  ads read "Not tagged", and every angle number on the site is therefore empty.
  Fill the bank with `intel record --kind angle_proposal` — see **Approving an
  angle**. A facet can also be filed with no `angle_slug` at all, carrying only
  hook, offer and audience.
- **`scripts/seed_angles.py` has nothing to do.** It promotes approved rows out
  of `public.campaign_angles`. With that table empty it prints
  `0 angle(s) to consider` and stops, correctly.
- **`ads.campaign_angle_approved` and `ads.inherited_facet` return nothing.**
  They are seam views over the empty tables. Nothing breaks; the *automatic*
  tagging path is simply unavailable, and tags have to be filed by hand.
- **The asset match never succeeds.** `store.upsert_ad` matches each ad against
  `candidates_for_match(brand_id)`, which reads `campaign_assets`. With zero
  candidates, `campaign_asset_id` stays null on every ad — and that is true
  whether or not the structure pull succeeds. Do not read a null
  `campaign_asset_id` as evidence about the importer.
- **`tracking.py` is inert.** It defines the utm convention `meta_ads.parse`
  reads back off an ad. Nothing here calls it outside the tests, and nothing is
  currently stamping those values onto ads, so the convention has no live
  writer. Keep it: it is the definition the parser is written against, and
  `tests/test_meta_match.py` round-trips through it.

**Zero rows is the failure mode, not an error**, and this section is why that
rule matters twice over. Every `public.meta_*` table has RLS on with no policy;
`ads_reader` is `NOBYPASSRLS`, so without the policies in `006_ads_roles.sql`
every query succeeds and returns nothing. `campaign_angles`, `campaigns`,
`brands` and `ad_reviews` have RLS enabled too. So an empty result may mean
"there are none" or "you cannot see them", and the two are indistinguishable
from the read pool. When it matters, check as a role that can see through RLS
before reporting an absence.

## The rules

**You never conclude anything.** You may propose an angle, propose an
experiment and file a creative tag. You may not set `ads.angle.status` to
`active`, you may not write `ads.experiment.conclusion`, and there is no verb
that tries. The schema enforces it (`angle_active_is_signed`,
`experiment_conclusion_attributed`) and the write shapes do not accept those
fields. An agent may tighten and may not loosen.

**You never compute a rate, a delta or a share yourself.** Every number comes
out of a function in `migrations/003_ads_metrics.sql`. If a number you need is
not in a verb's output, that is a missing verb — say so, and say which one.
Doing the arithmetic in your head is how the dashboard card and your sentence
come to disagree, and whichever one the reader is looking at is the one they
act on.

**You never apply a migration.** `ads_migrate.py --apply` is denied in
`.claude/settings.json`, as are direct `psql` and `python -c`. Prepare the DDL,
run `--print`, hand it over. This database is shared and several sessions run
against it.

**Facts and opinions are labelled.** The numbers a function returns are facts.
Your reading of them is an opinion. Performance is evidence about what
happened, not a verdict on what to do. `intel/readings.py` and
`intel/ad_readings.py` are the mechanical form of this: a rule may only state a
figure it can cite, and the tests fail any sentence that states one it cannot.

## Approving an angle

The one step with no verb behind it, deliberately.

`intel record --kind angle_proposal` files an angle with `status` hard-coded to
`'proposed'` — a SQL literal no caller can supply. Every angle number filters
on `status = 'active'`, and:

```sql
constraint angle_active_is_signed
    check (status <> 'active' or approved_by is not null)
```

So an angle joins the working vocabulary only when a person signs it, and the
signing is a statement run by that person, against the database, under their
own name:

```sql
update ads.angle
   set status      = 'active',
       approved_by = '<your name>',
       approved_at = now(),
       updated_at  = now()
 where brand_id = (select id from ads.brand where slug = 'renegade')
   and slug     = '<angle-slug>'
   and status   = 'proposed';
```

`and status = 'proposed'` is not decoration: without it the statement will
happily re-activate a retired angle, and a retired angle coming back is the kind
of change nobody goes looking for.

This is a decision, not a task. Do not add a verb, a flag or an endpoint that
performs it, and do not run it on somebody's behalf — the column is called
`approved_by` and it has to be true.

## Starting and concluding an experiment

The same stance as an angle, for the same reason. `intel record --kind
experiment` files a proposal with no `started_on`, and `ads.experiment_result`
(005) refuses to compute anything for it: an unlaunched experiment has no
window, and zeros would read as "we tested it and nothing happened". The
comments that say `started_on` "is set in the UI" mean the sibling app that is
not in use — there is no UI for it here, and no verb.

A launch is a thing that happened in Ads Manager, so the person who launched
it records it, once the new ad is live and carries its arm's `utm_content`:

```sql
update ads.experiment
   set started_on = date '<YYYY-MM-DD, the day the new ad went live>'
 where brand_id = (select id from ads.brand where slug = 'renegade')
   and name     = '<experiment name, as intel experiments lists it>'
   and started_on is null;
```

`and started_on is null` does for a launch what `and status = 'proposed'`
does for an angle: without it the statement quietly moves the window of a
test that is already being read.

Concluding is the one write an agent may never make
(`experiment_conclusion_attributed`): `conclusion`, `concluded_by` and
`concluded_at` are set together, by a person, after reading
`python -m intel experiment --brand renegade --name "<name>"`.

`minimum_effect_pct` and `minimum_spend_per_arm` are fixed when a proposal is
filed. The five filed on 2026-09-28 from the campaign triage carry 20% and
$500 per arm, which an agent chose to satisfy the shape; if those are not the
bar you want, re-file under a new name rather than editing — nothing edits a
filed proposal.

## Things that will bite you

**The last three days are not final.** Meta restates attributed conversions.
`ads.settled_through()` is the honest edge; every verb reports `unsettled_days`
and you must repeat it. "CPA rose this week" measured across a partial day is
the most common false alarm in this system.

The dashboard no longer stops at that edge. Overview, Why CPA moved and Fatigue
end their windows at the latest day that has data (`ui._latest_day`), because a
reader who presses Refresh and sees a window three days back reads it as the
refresh having done nothing. `python -m intel` still defaults to the settled
edge and still reports `unsettled_days` — the agent is held to the old contract,
the dashboard reader is not, and the rail's "Settled through" card says which
days can still move.

**Reach cannot be summed and frequency is not what you think.** Daily reach is
deduplicated within the day, so a window sum counts the same person repeatedly —
`ads.window_metrics` returns `reach_best_day`, not a total. Daily `frequency` is
impressions ÷ *that day's* reach, so it sits near 1.0–1.3; cumulative frequency
is not in this database at all. Say "daily frequency", never "frequency".

**CPL is not comparable across optimization goals.** An ad group optimising
`LINK_CLICKS` and one optimising `OFFSITE_CONVERSIONS` are different animals.
`optimization_goal` is carried down to the fact for exactly this reason —
segment on it or say you didn't.

**Meta's adset is called `ad_group` here.** The word "adset" appears nowhere in
the `ads` schema. Neutral level vocabulary is account / campaign / ad_group / ad,
so Google slots in as a union branch rather than a migration.

**A tag is keyed on `(ad_key, copy_hash)`.** Rewrite the copy and the tag is
gone, deliberately — a tag that survives a rewrite describes an ad that no
longer exists. `/ad/<key>` reads `ads.ad_facet` rather than
`ads.facet_effective` for this reason: the effective view drops a stale tag by
construction, which would make the warning impossible to show.

**`"ads": 0, "texts": 0` on a structure run is not a failure.** The structure
phase on `act_153704749222533` now succeeds (`intel status` reports
`last_structure_ok`), because `_pull_structure` is two passes. Pass one is cheap:
every ad, id and status only, no creative — that is the `statuses` count (944),
and it is what keeps a paused ad from sitting in the warehouse marked ACTIVE.
Pass two fetches creatives only for `ACTIVE_ENOUGH` ads (ACTIVE, WITH_ISSUES,
DISAPPROVED) with `updated_since` set to the *started* time of the last
successful structure run. So `"ads": 0` means no live ad was edited since the
last run, and the copy in the warehouse is still current.

The history still shows in the data. For its first days the unrestricted crawl
died five to nine minutes into `/ads` pagination (Meta codes 1 and 2), and the
ads exist because each one was upserted as it paginated, so the failed runs
left their rows behind — which is why `first_seen_at` on every ad is 2026-09-20
or later rather than the ad's real age.

**A dropped database connection costs one phase one retry, not the brand.**
Supabase closed the connection mid-structure on 2026-09-22 and 2026-09-25.
`run_pull` now catches `psycopg.OperationalError` per phase, waits, and runs
that phase again (`DB_ATTEMPTS = 2`; every write is an upsert, so a re-run is
safe), and insights still runs when structure fails.

## Running it

```
python -m intel status --brand renegade      # start here; says what is stale
python -m intel live   --brand renegade      # what is running RIGHT NOW
python -m intel overview --brand renegade --days 28
python -m intel why --brand renegade --days 7
python -m intel funnel --brand renegade --days 7   # 7, 14 or 28; needs 017
python -m intel fatigue --brand renegade
python -m intel coverage --brand renegade
python -m intel ad-readings --brand renegade --key <ad_key>

python -m main                               # the dashboard, 127.0.0.1:8001
python ads_migrate.py --status

python -m meta_ads --list-accounts
python -m meta_ads --add-account act_XXXX --brand renegade
python -m meta_ads --deactivate-account act_XXXX
python -m meta_ads --pull --brand renegade   # minutes, against a rate limit
python -m meta_ads --pull --brand renegade --phase insights   # seconds
python scripts/sync.py                       # what Task Scheduler runs
```

`127.0.0.1`, never `localhost` — on Windows `localhost` resolves `::1` first and
uvicorn bound to `127.0.0.1` is not listening there, which presents as a refused
connection against an app that is plainly running.

Every verb prints JSON to stdout and one sentence to stderr on failure. There is
one write verb, `intel record`, and it takes a path rather than stdin. Its three
shapes are `ad_facet`, `angle_proposal` and `experiment` (`intel/shapes.py`) —
a governance question should be answerable by reading one list.

**An inactive account still fails every run.** An account the token cannot read
fails in under a second, and the cost is not the wasted call: `run_pull`
isolates per account so the import still succeeds, but the run exits 1, `intel
status` reports `healthy: false`, and the dashboard's import card goes amber on
behalf of an account nobody reads. A scheduled task that reports failure on
every run is a task nobody checks. Deactivate it. The test account
`act_9105140029692` was exactly this, and is deactivated (`active: false`).

## Warehoused vs live

Almost everything here is **warehoused**: `meta_ads/` imports daily rows into
`public.meta_*` and the rest of this repo derives every rate from them. That is
what makes "this week against the last six months" one query instead of a
hundred API calls, and it is the only thing that holds up across two brands.

The cost is staleness. Three things manage it:

- `ads.settled_through()` — Meta restates attributed conversions for ~3 days and
  an insights date is a day in the *account's* timezone. Every verb reports
  `unsettled_days` against this, so a partial day never reads as a decline.
  `meta_ads.pull._account_today()` now takes the window end from the same
  clock, so the importer no longer asks for a day that has not started.
- **The scheduled pull** — `scripts\sync.py`, twice daily via Task Scheduler.
  Before it existed, the staleness window was not 24 hours, it was "whenever
  anyone remembered", which is worse because it is invisible. It is registered
  as the Windows task `GrowthEngine-MetaSync`, daily at 1:45 AM and 1:45 PM,
  running `scripts\sync.bat`. Its logon mode is **Interactive only**, so it
  runs only while this PC is on and the user is logged in — which is why
  `logs/sync.log` has nothing between 25 and 28 Sep. A gap in that log is the
  machine, not the importer. Check it with `schtasks /query /tn
  GrowthEngine-MetaSync /v /fo list`.
- **The Refresh button**, which shortens that window without closing it: a
  button somebody has to think to press is still "whenever anyone remembered".

**`intel live` is the one live call**, and the point of it is the *diff*, not
the list. It reports ads running now that no pull has seen, ads the warehouse
still believes are active, and budgets or optimisation goals that moved since
the last import. That seam is where warehoused analysis quietly goes wrong.

It deliberately returns **no spend, no CPA, no conversions**. Live insights are
the slow, rate-limited call, and a live number beside a warehoused one in the
same answer invites comparing two things measured differently. Structure is
live; performance is warehoused; say which is which.

A pull takes minutes against a rate limit, which is why **no page pulls
in-process** and why `ask.py` refuses to route to `live`. The dashboard's
Refresh button is the one page that starts a pull, and it starts it outside this
process: `POST /refresh?brand=<slug>` spawns `scripts\sync.py --brand <slug>
--phase insights` as a detached subprocess and returns 202 straight away, and
`GET /refresh/status?brand=<slug>` reads `ads.pull` on the read pool to report
when the last insights pull succeeded, how far it got, and whether one is in
flight or failed. It calls `sync.py` with `sys.executable` rather than going
through `sync.bat`: the .bat exists to find the venv for a scheduled task that
inherits no PATH, and the dashboard is already running in that venv. It also
keeps a `.bat` out of `Popen`, which on Windows needs `cmd.exe` in the middle
and re-parses the arguments on the way past.

Detached is the point rather than an implementation detail. A browser that gave
up half way through leaves a `running` row nothing ever closes, so a pull must
not live inside the request that started it — closing a tab is not allowed to
orphan a row. Overlap is refused twice: `scripts/sync.py` takes `.sync.lock`
before it does anything, and the endpoint checks that same lock before it
spawns, so the button cannot race the scheduler or itself.

The button is scoped to `--phase insights` because insights is one Graph call
over a ~4-day window, while the structure phase takes minutes (it was scoped
when structure had never once succeeded, and a button wired to the phase that
always fails is a button that looks broken). Unscoped is still the default for
the scheduled run.

## The ad page asks a model

`/ad/<key>` has one button, top right. `POST /ad/<key>/analyse.json` builds the
ad's fact pack, attaches its copy, and hands both to a short-lived Claude Code
session through `chat.answer()`. The facts go *with* the question rather than
being left for the session to fetch: it is faster, and every figure in front of
the model came out of `ads.fatigue`, `ads.cpa_bridge` and `ads.ad`, so what it
quotes is sourced whether or not it goes and checks.

POST rather than GET, for the reason `/refresh` is: it costs a model call and
the better part of a minute, and a GET that spends is one browser prefetch away
from spending on its own. `chat.py` shells out to `claude -p` and imports no
provider library; the allowlist in `chat.READ_VERBS` is the security model, and
`record` and `live` are deliberately absent from it.

## /suggestions is a campaign triage, written after each import

Nobody presses anything. `scripts/sync.py` runs `scripts/suggest.py --force`
after a successful import (`_run_after`, alongside `tag.py` and `brief.py`,
with 1200s to the model call's 900), because a suggestion somebody has to
remember to ask for is one nobody asks for. A failed import skips it: an
analysis of numbers that did not land is worse than none.

**It needs migration 017** (applied 2026-10-01). `intel/creative.py`'s pack reads
`metrics.funnel_ladder` -- one round trip over `ads.analysis_windows` and
`ads.funnel_change`, every level, every window pair. Until 017 is applied the
pack carries `funnel_available: false`, the /suggestions page still renders
(it builds the pack live), and `suggest.py` refuses to publish: it logs
"migration 017 is not applied" and exits 1, so the page keeps the last file.
A half-analysis is the one-window read this replaced.

**The window pairs**, all ending at the settled edge: `unsettled_vs_last_7`
(the tail, shown as provisional and never a basis), `last_7_vs_prior_7`,
`last_14_vs_prior_14`, `last_28_vs_prior_28` and `last_7_vs_before`. Each
campaign's `ladder` is read off their sides -- unsettled, last 7, prior 7,
last 14, prior 14, last 28, before -- with every figure taken from 017's
`cur_m`/`pri_m`, never summed here. **There is no target CPL:** each campaign
is judged against its own earlier windows.

The model is asked four questions per campaign, in order: **what changed**
(the ladder from the short end; a 7-day move the 14-day comparison does not
share is a wobble), **where** (campaign, ad group, ad, and whether
concentration is efficient), **why** (017's CPA decomposition into auction
cost, click-through and post-click, and `intel/diagnose.py`'s pattern, always
as a hypothesis) and **what is justified** (act, test or monitor). Each issue
is observation / evidence / diagnosis / recommendation / expected outcome /
confidence, a test carries control vs challenger with a decide-when rule from
the ad group's `test_floor`, and every report says what not to do yet.

**The diagnosis bounds the reply.** `intel/diagnose.py` is a pure module (no
db, no arithmetic) that reads the rows before the model does: which pattern
held in which window on what evidence, which types it `allowed`, its
confidence, the control ad (the incumbent stays unless its own funnel
deteriorated), and `max_priority`. `creative.triage_from` clamps each issue
against the finding its `basis` names -- type, confidence and priority move
down, a test plan on a variable the finding does not allow becomes a monitor,
an issue naming no finding is a low-confidence monitor, one primary at most
-- and records each move (`type_adjusted_from`, `test_plan_dropped`, ...) so
the card says whose call it was. The rating is clamped the same way:
`rating_rule` (diagnose's, re-exported by creative) allows red only for a CPA
rise that persists in the 14-day comparison on enough evidence, or
zero-conversion spend at several times the entity's own baseline. A single
fatigue symptom, concentration and rank in goal never make a campaign red.

**What the model is sent is a view, and it shrinks by tier.**
`creative.campaign_tier` strips repetition (dates live in `windows`, a rung
is an array, a campaign-level finding drops what pct_7 already says) and,
over `TRIAGE_BUDGET`, drops rungs and non-control copy (B) and then all but
the essentials (C); `diagnosis` and `rating_rule` are in every tier. The
campaign list is never cut. If even tier C is over, the prompt goes anyway and
the log says WARNING. `goals`, `tiring`, `formats` and `goal_impact` are
page-only; `movement`, `campaign_key`, `entity_keys` and each ad's `ad_key`
ride on the pack for the page and the ledger and are never sent.
`TRIAGE_BUDGET` is 140k: measured 2026-10-01 before 018, tier A was 118,242
chars (the prompt text alone is ~18k); re-measure with `--dry-run` once 018 is
applied and the ledger has a week of followups.

**The reply is one object**, `{"account_note": str, "campaigns": [...]}`.
`chat.classify(expect="any")` takes it, and `creative.parse_reply` still reads
a bare array (the round-1 shape) so a reply in the old form is not wasted.
`chat.classify` defaults to `expect="list"`, which is what `scripts/tag.py`
needs, unchanged.

**Buckets, not colours.** After the clamps, each campaign's `bucket` is the
strongest recommendation type among its issues -- act > test > monitor, none
is `no_change` -- computed by `creative.bucket_of`; a bucket in the reply is
never read. The rating still exists and is still clamped, but the page groups
by bucket. `creative.sort_triage` is the published order: act, test,
monitor, no change, and within a bucket by `impact.impact_score` (None last,
stable). `priority_rank` is comparisons only: 1 for act, or a test with
impact_score >= `PRIORITY_1_IMPACT` (250); 2 for any other test, or a monitor
>= `PRIORITY_2_IMPACT` (100); else 3. The thresholds are evidence-weighted
dollars and were set by eye on one week of the live account -- revisit once
018 has rows.

**Impact needs 018.** The pack attaches each campaign's `impact` from
`metrics.goal_impact(slug, "last_7_vs_prior_7")`'s campaign row (matched by
campaign_key, then name): goal, `excess_cost` (dollars beyond what this
week's conversions cost at last week's rate), `impact_score`,
`contribution_share_pct` and evidence. Dollars may be ordered across goals; a
CPA never is. A campaign spanning two goals has no campaign row and no
impact. `movement` is 017's pct for CPA, link CTR, CPC and CVR, 7v7 and
14v14, for the collapsed card's strip. Before 018, `impact_available` is
false, every impact is None, and the page and pack degrade (no impact order,
the old `dimensions` table) rather than crash.

**What matters today.** `creative.account_summary(pack, triage, note)` is
deterministic: per goal, its CPA move from 018's goal row and -- when it rose
on at least directional evidence -- how many campaigns (the first whose
`cumulative_share_pct` reaches 60) account for it; then, over act and test
campaigns, how many show creative-side deterioration (creative_engagement,
possible_fatigue, audience_saturation, clickier_poorer_intent) and how many
post-click; then how many have issues with low current impact. Every line is
an f-string of SQL values and `len` counts. The reply's one `account_note`
sits beside it, figure-checked (`check_note`) and causal-checked.

**Each card has a `summary`** -- one line, at most ~25 words, "CPA +22%.
Lower CTR appears to be the largest contributor. Test new creative.
Medium-high confidence." -- checked by `check_figures` like every other
field; `headline`/`why` stay as aliases. **Causal wording is disciplined:** a
metric never "caused" anything. `creative.causal_language` runs
`\b(caused|causes|because of|due to|is driving|drove)\b` over each card's
summary, headline, observations and evidence and sets `causal_flags`; the log
counts them. Flagged, not rewritten.

**Every issue carries six fields** -- observation, evidence, diagnosis,
recommendation (action and expected outcome), confidence (level and reason),
and a test plan when the type is test. One missing any is `incomplete: [...]`
and clamped to monitor.

**Existing asset vs new creative.** A creative test's plan carries
`challenger_kind`. `existing_asset` stands only when `challenger_ad` is
exactly the name of an ad in `challenger_pool` under the campaign's own
goal, or in its `where.ads`; otherwise `triage_from` relabels it
`new_creative` and records `challenger_kind_adjusted_from`. `new_creative`
carries a `brief` {format, angle_or_hook, opening_line, built_from}; the
opening line is proposed copy and is not figure-checked, `built_from` is the
evidence and is. A test of anything but the creative has no kind.

**Dimensions by goal.** `dimensions_by_goal` (018's `facet_by_goal` for hook,
offer and audience over the last 28 settled days, grouped by goal, each label
`baseline / better / similar / worse` against its own goal's CPA) is on the
pack for the page's goal tabs and is sent to the model INSTEAD of
`dimensions` when present (`suggest.model_facts`); without 018 the old
cross-goal `dimensions` is sent. Never both.

Issues carry ids across runs, so each one from the last review comes back
still open, resolved, or no longer mentioned. A resolution is accepted only if
it cites a change from `changes_since`, which is computed, not judged: what
moved between the last file's `snapshot` (per-ad `copy_hash` and status) and
today, labelled `"you"` (the account was edited) or `"numbers"` (a diagnosis
finding appeared or stopped holding, a rank moved). One with no real change
behind it is dropped, and the page says it was not confirmed fixed.
`check_figures` then flags, on its card, any figure the card states that the
facts sent do not contain -- the last review's own text does not count as a
fact.

Output is `suggestions/YYYY-MM-DD-<brand>.json` -- one per brand per day,
gitignored, `"shape": 2`, with the facts as sent stored beside it so it can be
checked later. Older files (`problems` or `changes`) still render.
`templates/_triage.html` is the one template allowed red, amber and green: a
user-granted exception, scoped by name in
`tests/test_charts_and_templates.py`. Everything else stays under the rule.
The published file also carries `account_summary`, `impact_available` and
`outcomes` (`{"available", "measurement_available", "scorecard"}`); each act
or test issue carries an `outcome` tag from the ledger.

## Outcomes are auto-detected

Was a recommendation carried out, and what followed? `intel/outcomes.py`
answers it from the account's own changes, with no button and no migration
beyond 018's read functions. **A measured result is a reading of SQL figures,
not a conclusion:** nothing here writes `ads.experiment.conclusion`, and the
page labels the scorecard "a reading, not a conclusion".

**The ledger is a file**, `outcomes/<brand>.json` (gitignored, like
`suggestions/`). `scripts/suggest.py` loads it before the prompt (each
campaign's open / implemented / measuring entries go to the model as
`followups` -- status, dates, match, result, never the old wording -- with
the rule "do not propose a second test in an ad group whose test is
measuring"), and after the review: `update_ledger` registers every published
act or test issue by its id (ids persist across runs), applies the overrides,
matches this run's `changes_since` "you" items, and expires what nobody has
mentioned for 30 days; `measure` reads what was implemented; the file is
saved after the review is on disk. Each step degrades alone: a bad overrides
file is logged and ignored, a failed measurement keeps the ledger as
matched, and only a failed registration drops `outcomes` from the doc.

**Matching rules** (`outcomes._match_one`, deterministic, each recording
`matched_by` and `match`):

- **creative test**: an `ad_added` in the control's ad group on or after the
  recommendation. `strong` when the new ad's name is `challenger_ad` (or is
  quoted in the challenger), else `likely`. Implemented on the new ad's
  first spend day (the snapshot's `first_spend_day`, from SQL). A new ad
  implements one test, strong matches placed first.
- **delivery cut** (act on one ad): that ad goes non-ACTIVE or is gone;
  implemented on the review date that saw it.
- **creative refresh**: `wording_changed` on that ad.
- **budget**: `budget_changed` on the campaign (CBO: strong for a campaign
  issue, likely for an ad group one) or on the named ad group.
- **Not detectable**: landing page, form, offer and tracking changes are not
  visible in Meta's structure data. Those entries are `not_detectable` until
  a person says otherwise. Anything no rule recognises stays `open` with the
  reason "no rule detects this kind of change automatically".

**Overrides** are the one manual surface, a file edit:
`outcomes/overrides.json`, `{"<issue id>": {"implemented_on": "YYYY-MM-DD"}
| {"not_implemented": true}}` (an implemented override may also carry
`challenger_ad_key`). An override always wins and is labelled "set by a
person". Invalid JSON is logged and ignored for that run, never silently
treated as applied.

**Measurement** (018's `arm_compare` and `entity_outcome`, so nothing until
018 is applied -- entries then wait as `implemented` with the reason): a
creative test compares control against challenger from the implementation
day to the settled edge, against the ad group's `decision_spend_per_arm`;
everything else compares the 14 settled days before implementation with the
implementation day to the settled edge, on the entity the issue named (an ad
whose delivery was cut is read on its ad group). Classified by comparison
only: `too_early` under 7 settled days, on insufficient evidence, or with an
arm under its decision spend; `improved` at a CPA effect of -10% or better,
`worse` at +10% or worse, `neutral` otherwise. Re-read each run until 28
settled days, then frozen. The scorecard (`outcomes.scorecard`, last 90
days) is counts by `len`: made, implemented, and each result, by kind.
