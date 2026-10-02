---
name: ads-intel
description: Answer questions about Meta ads performance for Renegade and Agency Height — what changed, why CPA moved, which creatives are fatiguing, which angles work, what to test next. Use whenever the question is about ad spend, CPA, CPL, CTR, creative performance, angles, or experiments.
---

# Reading the ads

You have a read-only CLI over a metrics layer. Your job is to **interpret**
numbers, never to produce them.

## The rule that matters most

**You never compute a rate, a delta, a share or an average yourself.**

Every number comes out of a SQL function. If the number you want is not in a
verb's output, that is a missing verb — say which one is missing and stop. Do
not divide spend by conversions to "check". Two definitions of CPA is exactly
the failure this layer exists to prevent, and the one you computed in your head
is the one nobody can audit.

Adding up a column the tool already totalled is also computing. Read the total.

## Start here, every time

```
python -m intel status --brand renegade
```

It tells you whether anything is worth reading: when the last import succeeded,
whether a pull failed, whether conversions are even defined, and which database
credential is in use. **A stale import looks exactly like a quiet week.** If
`healthy` is false, lead with that — an answer computed over data that stopped
arriving three weeks ago is worse than no answer, because it will be believed.

## The verbs

| Question | Verb |
|---|---|
| What ran and what did it cost? | `overview --brand X --days 28 [--level ad\|ad_group\|campaign]` |
| What changed vs last period? | `compare --brand X --days 14` |
| **Why did CPA move?** | `why --brand X --days 7` |
| Which creatives are tiring? | `fatigue --brand X [--window 7] [--min-spend 100]` |
| Everything about one ad | `ad --key <ad_key>` |
| A metric over time | `trend --brand X --metric cpa --days 90 --bucket 7` |
| How is each angle doing? | `angles --brand X --days 90` |
| What have we never tested? | `coverage --brand X [--product carrier-access]` |
| Angle A vs angle B | `versus --brand X --a cash-upfront --b no-broker-fees` |
| Which ads have no tag? | `queue --brand X` |
| Handles with no angle yet | `candidates --brand X` |
| What have we tested before? | `experiments --brand X [--open] [--angle slug]` |
| One test's result | `experiment --brand X --name "..."` |
| **What is running right now?** | `live --brand X` |

Brands are `renegade` and `agencyheight`.

## Warehoused vs live — know which you are holding

Everything except `live` reads a Postgres warehouse this repo fills itself --
`meta_ads/` is the importer and `scripts/sync.py` is what the scheduler runs.
That is what makes "this week against the last six months" one query rather than
a hundred API calls. The cost is that it is hours old, and `intel status` says
how many.

**`live` is the only verb that talks to Meta**, and it returns structure only —
which ads are running, at what budget. **No spend, no CPA, no conversions.**
That is deliberate: live insights are the slow, rate-limited call, and a live
number sitting beside a warehoused one in the same sentence is two things
measured differently being compared. If you quote a number, it is warehoused.
If you say what is running, that can be live. Never blur them.

Reach for `live` when:

- the question is literally about now — "is that still running", "did she pause
  it", "what is the budget on that ad set"
- `status` says the last import is stale and someone is about to act on a number
- you are about to recommend pausing or scaling something, and it would be
  embarrassing if it had already been paused

**The useful part of `live` is the diff, not the list.** It returns:

- `not_in_warehouse` — running now, no pull has seen it. Its spend is in none of
  your numbers.
- `warehouse_thinks_active_but_is_not` — every "active ads" count you quote is
  overstated by this many.
- `changed_since_import` — a budget or optimisation goal that moved. **A changed
  `optimization_goal` is the serious one:** the ad group's CPA stops being
  comparable to its own history and no chart will mention it.

If `warehouse_agrees` is true, say so and move on — that is a fact worth one
clause, not a paragraph.

Do not call `live` reflexively. It costs a rate-limited round trip and most
questions are historical, where the warehouse is not just adequate but correct.

## Reading the output honestly

**`unsettled_days` and `caveat`.** Meta restates attributed conversions for
about three days, and an insights date is a day in the *account's* timezone. If
`caveat` is set, repeat it. A decline at the end of an unsettled window is the
most common false alarm in this system — it is usually just a day that has not
finished.

**`confident` on fatigue rows.** False means the ad is below the spend or
impression floor. A score of 3/5 on $30 of spend is arithmetically correct and
means nothing. Report unconfident rows as a count, not as findings.

**Say "daily frequency", never "frequency".** Meta's daily frequency is
impressions ÷ *that day's* reach, so it sits near 1.0–1.3. It is not cumulative
frequency, which is the number people mean by "they've seen it four times" and
which is **not in this database at all**. `fatigue` detects acceleration. Claiming
saturation from it is a claim the data cannot support.

**Never sum reach.** Daily reach is deduplicated within the day. `reach_best_day`
is the best single day, and that is the only honest reach number here.

**CPA is not comparable across optimization goals.** An ad group optimising
`LINK_CLICKS` and one optimising `OFFSITE_CONVERSIONS` are different animals
however similar the copy. `optimization_goal` is on every row. `versus` returns
`same_optimization_goal` — if it is false, say so before comparing.

**A null rate is undefined, not zero.** `cpa: null` means no conversions, not a
free lead. Never render it as 0 and never rank on it.

**`mixed_currency`.** If true, the totals add different currencies together.
Say so instead of reporting the total.

## Answering "why did CPA go up"

Use `why`. It splits the change into:

- **rate effect** — this creative got more expensive
- **mix effect** — budget moved toward a creative that already was

Those are different problems with different fixes, so say which one dominates.
`total_effect` sums across all rows to the brand change; `rate_effect` and
`mix_effect` are null where an ad converted in only one window, and
`unattributable_ads` counts those. Do not present the split as covering
everything when it does not.

`why` also returns `structure_changes` — budget, status and goal edits. **Check
these before blaming the creative.** Most of the time the answer is that the
budget tripled or the ad group was paused, not that the copy wore out. If
`structure_changes.available` is false, say that the change history is not
available rather than saying nothing changed.

## Answering "what should we test next"

1. `coverage` — what has never run, and what ran on too little spend to read.
2. Check `prior_experiments` in the same output. **If an angle was tested
   before, say so and say what happened.** Proposing "let's test no broker fees"
   for the seventh time is the specific failure the experiment table exists to
   prevent.
3. Ground the suggestion in what the account actually ran -- the ad's own copy
   (`intel ad --key`), its angle if it carries one, and what has been tested
   before. There is no separate claims library in this database; an angle you
   cannot substantiate from the copy and the numbers is not a proposal.

To file one, see the `ads-experiments` skill. You may propose. You may not
launch, and you may not conclude.

## What you cannot do

- Approve an angle, launch an experiment, or record a conclusion. No verb
  exists; the schema constraints refuse it; this is deliberate.
- Apply a migration. `ads_migrate.py --apply` is denied.
- Pull from Meta. That is `python -m meta_ads --pull --brand X`, here, and it
  takes minutes against a rate limit -- run by a person or by the scheduled
  task, never from a verb or a page.

## Labelling

The numbers are **facts**. Your reading of them is an **opinion**. Performance is
**evidence about what happened**, not a verdict about what to do. Keep the three
apart and never let one borrow another's authority — a copy review scoring 8/10
is an opinion about writing, not a reason to raise a budget.
