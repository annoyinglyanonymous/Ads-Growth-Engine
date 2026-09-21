---
name: ads-brief
description: Write the recurring brief for a brand — what ran, what moved, what is waiting on a signature — from a dated fact pack in which every number is quoted from another verb. Use when asked for a weekly brief, a recap, a Monday update, the state of an account, or "what do I need to know".
---

# Writing the brief

```
python -m intel brief --brand renegade [--days 28] [--until 2026-09-14]
```

One verb, one window, one JSON object. It calls the other verbs and copies
their numbers out. **It computes nothing**, and neither do you.

## What the brief is for

It is a recurring read, not a report. A report is written once, admired, and
filed. This is read every week by the one person who can act, and its job is to
end with the two or three signatures only she can give — sign an angle, alias a
handle, conclude a test. Those signatures are what the next brief reads from.
An angle signed this week is an angle with a number beside it next week.

So the measure of a brief is not how much it explained. It is whether next
week's brief can say more than this one did. A brief that ends with nothing for
her to sign has not been efficient; it has spent a week of data and bought
nothing with it.

## What comes back

| Key | What it holds |
|---|---|
| `facts` | The numbers, in sections. Every section carries a `from`. |
| `readings` | The rules that fired, each citing pointers into `facts`. |
| `waiting_on_you` | The signatures. `angles_unsigned`, `experiments_unconcluded`, and the rest. |
| `proposals` | Unfiled, each with the command that would file it. |
| `gaps` | Questions this system cannot answer, and why. |
| `degraded`, `provisional` | What ran short, and whether to trust any of it. |
| `markdown` | The whole thing, laid out already. |

**`waiting_on_you` is dated on purpose.** An item that has sat for three weeks
should read as three weeks old, not as new business. Do not quietly re-raise it
as though this were the first time; say how long it has been waiting. That is
the only pressure this system is allowed to apply.

**`markdown` is scaffolding, not the brief.** Every figure in it is already
quoted, which means the only way to break the rule is to add one of your own.
Rewrite the prose freely — it is deliberately flat, and she is not reading a
table dump. Leave the figures exactly as they are.

## Read `facts.trust` before any other key

The brief runs `intel status` for you and pins the result at `facts.trust`.
Read it first, every time, for the reason `ads-intel` gives: **a stale import
looks exactly like a quiet week.** Spend falls, conversions fall, and the
numbers are perfectly consistent with each other and with nothing that
happened.

If `facts.trust.healthy` is false, that is your opening paragraph — not a
footnote under the numbers, not a caveat at the end. `stale_import` will have
fired; name the problems from `facts.trust.problems`, then say that everything
below is provisional. The brief has already said so: `provisional` is true and
`degraded` lists the sections that ran short. Repeat it in your own words
anyway. Nobody reads a boolean.

Two more trust rules outrank the performance sections when they fire.
`no_conversion_definition` means every conversion count and every CPA below is
zero or null as a configuration state, not as a result. `match_rate_low` means
approved assets carry tracked urls no imported ad matches, so the angle
attribution under them is incomplete before you start.

An answer computed over data that stopped arriving three weeks ago is worse
than no answer, because it will be believed.

## The one rule: you quote, you never compute

**Every figure in your prose must appear verbatim somewhere in `facts`.** If
you cannot point at the key it came from, you may not write it.

Each section of `facts` carries a `from` key naming the function behind it —
`ads.window_metrics`, `ads.compare`, `ads.cpa_bridge`, `ads.fatigue`,
`ads.angle_coverage`, `ads.experiment_result`, `ads.settled_through`. That key
is not decoration. It is how a reader gets from your sentence back to
`migrations/003_ads_metrics.sql` and checks you. A number with no `from` behind
it is unsourced, and an unsourced number in a brief is worse than a gap,
because a gap is visible.

Adding up a column the brief already totalled is computing. So is taking a
percentage of two numbers that are both in `facts` — the share is a third
number and it is not there. So is rounding a CPA to "about $90". Read the
total. If the number you want does not exist, that is a missing verb: say which
one, and check `gaps` before you assume nobody thought of it.

The code holds this rule more tightly than you can. A rule in
`intel/readings.py` never types a number — `_cite` takes a JSON pointer,
returns what it found, and the rule interpolates that (`intel/readings.py:10`).
`tests/test_readings.py` is to walk every emitted reading and assert that each
numeric literal in `says` resolves verbatim at one of its pointers
(`intel/readings.py:15`). Write to the same standard by hand.

Two definitions of CPA is the exact failure this layer exists to prevent, and
the one you did in your head is the one nobody can audit.

## Facts, readings, proposals — three kinds of statement

They are three different things and they must stay apart in your prose, not
just in the JSON.

- **A fact is what a function returned.** It lives in `facts`. It has a `from`.
  You quote it and you do not soften it.
- **A reading is one of the named rules in `intel/readings.py`.** It arrives
  with `says`, a `section`, an `author` of `rule:<name>`, and `cites` — one
  entry per number, each carrying `pointer`, `verb`, `field` and `value`. You
  may report a reading. **You may not invent one.** If the pattern you noticed
  is not a rule in that file, it is you having an opinion — which is allowed,
  and must be written as one, in your own voice, not dressed as a finding.
- **A proposal is something nobody has agreed to.** Everything in `proposals`
  is unfiled and stays unfiled. Say **"not filed"** in those words, and give
  the command that would file it.

Three mechanics of a reading that change how you type it out:

**A reading with a `subject` is a predicate, not a sentence.** `says` on a
fatigue or angle rule begins mid-clause — *"shows 4 of 5 fatigue symptoms"*,
*"took 41% of tagged spend and returned 12%"*. Put `subject.name` in front of
it. Do not paraphrase the rest.

**`rule` names the branch that fired, and the branch is the finding.**
`structure_change_unknown` and `structure_change_first` are opposite states of
the world. So are `rate_dominates` and `mix_dominates`. Report which one.

**`provisional` is per reading as well as top level**, and `rule_failed` is a
defect in a rule rather than a finding about the ads — say so plainly and move
on.

**A reading states what fired, never what it means** (`intel/readings.py:34`).
"shows 4 of 5 fatigue symptoms", not "is fatigued". "has the lowest CPA within
OFFSITE_CONVERSIONS", not "is the winner". Keep that discipline in your own
sentences: the numbers are facts, your reading of them is an opinion, and
performance is evidence about what happened rather than a verdict on what to
do.

## Writing each section

**Lead with what moved, not with what is large.** `facts.spend` is context.
`facts.movement` is the brief.

**Structure changes come before creative.** If `structure_change_first` fired,
an edit landed in the window, and most of the time the answer to a CPA move is
an edit rather than worn-out creative. If `structure_change_unknown` fired,
whether anything changed is *unknown*, not known to be nothing — never report
the second as the first.

**Say how much of the move the split covers.** `split_does_not_cover` fires
when `attributable_share` is under 0.8, because ads that converted in only one
window have no rate/mix decomposition at all. A partial split presented as a
whole one is a clean-looking answer to a question you did not finish.

**`spend_share_pct` against `conversion_share_pct` is the finding.** Spend
alone is a budget fact and she set the budget. `angle_overweight` fires at
fifteen points of gap; report it as it is phrased and do not compute the gap
yourself.

**Qualify every angle share with `untagged_spend`.** Untagged ads are not in
the angle numbers, so each share is a share of what is left. An ad that spent
$4,000 with no angle distorts the whole section, and `inheritable_ads` counts
the ones tagging can resolve with no judgement at all — see `ads-tagging`.

**Never rank angles across optimization goals.** `rank_within_goal` is NULL
wherever `comparable_on_cost` is false, and that is the point — it is NULL so
that you cannot sort on it by accident (`intel/angles.py:221`). **If it is
NULL, say the comparison is unavailable and say why.** Do not rank them anyway
and add a caveat; a ranked list with a caveat under it is still a ranked list,
and the list is what gets quoted back to you.

**Report `suppressed_unconfident` as a count, never as findings**
(`intel/metrics.py:203`). `fatigue_suppressed` phrases it for you. Listing
those rows is the failure — a 3-of-5 on $30 of spend is arithmetic, not a
finding.

**Say "daily frequency", never "frequency".** Meta's daily frequency is
impressions ÷ *that day's* reach and sits near 1.0–1.3. Cumulative frequency —
the number people mean by "they've seen it four times" — is not in this
database at all. **Never sum reach**; `reach_best_day` is the only honest reach
number here.

**A null CPA is undefined, not zero.** It means no conversions, not a free
lead. Never render it as 0, never rank on it, never let it sort to the top.

**Repeat `unsettled_days` and the `caveat` string** (`intel/context.py:63`).
Meta restates attributed conversions for about three days, and an insights date
is a day in the *account's* timezone. A decline at the end of an unsettled
window is the most common false alarm in this system, and it is almost always a
day that has not finished.

**If `mixed_currency` is true, do not print a money total.** The accounts do
not all report in one currency, so no money total in the brief is a number. Say
that, and report what does not depend on it.

**Experiments: no winner column, and you do not supply one.**
`experiment_not_readable` means an arm is below the floor it registered, so any
effect on it is arithmetic — say that before quoting anything from it. See
`ads-experiments`.

## What the brief cannot do

It cannot approve, launch, conclude, file anything, apply a migration, or pull
from Meta. No verb exists, the schema constraints refuse it
(`angle_active_is_signed`, `migrations/004_ads_taxonomy.sql:115`), and this is
deliberate rather than unfinished.

**`bank_is_inert` is the reading that outranks everything else.** It fires when
`facts.angles.rows` is empty and `waiting_on_you.angles_unsigned` is not: the
bank holds proposals and no signed angles. When you see it, **that is the
headline** — above spend, above CPA, above whatever moved. `ads.angle_coverage`
filters on `status = 'active'`, nothing is active, so the angle section is not
reporting that the angles did badly. It is reporting that there are none. Write
the difference. An empty table read as a weak result is how a person concludes
that angles do not work here, when the truth is that nobody has signed one.

The same shape applies to `awaiting_conclusion`: until a conclusion is written,
the test is not prior art, and `coverage` will propose the same question again.

## `gaps` is not an apology

Each entry is a `question`, a `why`, and a `what_would_answer_it`
(`intel/gaps.py:27`). The third field is the one to read: it separates "nobody
built this" from "the inputs cannot support it", which are very different
answers to the same silence. Some gaps are open because no one has got to them.
Some are closed on purpose — there is no significance test outside a registered
experiment, and that is the experiment table's whole mechanism, not an
oversight.

**Read it before you claim the data is silent about something.** There is no
revenue in this database at all, so there is no ROAS and every metric in the
brief is a cost-per. "We don't see much creative fatigue" and "fatigue cannot
be measured below the spend floor" are different sentences, and only one is
true.

Turn a relevant gap into one clause where it belongs — beside the number it
limits, not in a list at the end. And after a few weeks, the two or three
questions she keeps asking are the ones worth building. That is what this list
is for.

## Finishing

Close with counts, then what needs her, then the link. A closing that works:

*"28 days to 14 September: $18,420 across 41 ads, 214 conversions, CPA $86.07,
no unsettled days in the window. Nine rules fired and one of them is the brief:
`bank_is_inert` — the bank holds two proposed angles and none signed, so the
angle section is empty because there is nothing to fill it, not because the
angles failed. Nine ads carrying no angle spent $4,180 of that $18,420, and six
of them can be tagged with no judgement at all. Waiting on you: sign
`no-earnout` or reject it, decide whether "zero broker fees" is the same angle
as `no-broker-fees`, and conclude MA-012, which stopped on 2 September and has
waited eighteen days. I have filed none of those and cannot. The dashboard is
at http://127.0.0.1:8001."*

That is the shape. Counts first, because they are the part you are certain of.
Then the small number of things that cannot move without her, each one a
sentence she could act on without opening anything. Then the link, because she
will want to look.

What to do about any of it is hers.
