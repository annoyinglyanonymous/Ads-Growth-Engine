---
name: ads-experiments
description: Propose ad experiments and read their results without naming a winner. Use when asked what to test next, whether an angle has been tested, or how a running test is doing.
---

# Experiments

## Before proposing anything, check what was already tried

```
python -m intel coverage --brand renegade
python -m intel experiments --brand renegade --angle no-broker-fees
```

`coverage` returns `prior_experiments` alongside `never_run`. **If an angle was
tested before, say so and say what happened.** Proposing "let's test no broker
fees" for the seventh time is the specific failure this table exists to prevent,
and it is the fastest way to lose the operator's trust in every other suggestion.

An angle in `under_spent` was not tested. It was glanced at.

## Proposing one

```json
{
  "brand": "renegade",
  "name": "MA-015 cash upfront vs no broker fees",
  "question": "Does leading on liquidity beat leading on cost avoidance for agency owners?",
  "hypothesis": "Cash upfront produces a lower CPA than no broker fees, because liquidity is the more urgent problem for an owner already considering an exit.",
  "primary_metric": "cpa",
  "minimum_effect_pct": 20,
  "minimum_spend_per_arm": 1500,
  "arms": [
    {"label": "cash upfront",   "angle_slug": "cash-upfront"},
    {"label": "no broker fees", "angle_slug": "no-broker-fees"}
  ]
}
```

```
python -m intel record --kind experiment --json .intel/exp.json
```

**The two thresholds are the point.** `minimum_effect_pct` and
`minimum_spend_per_arm` are registered *before* any number exists, and that is
the whole mechanism against reading a 12% difference on $80 of spend as a
result. Decide what would convince you while you still do not know the answer.

Set them honestly: an effect smaller than the noise on your volume is not worth
running, and a spend floor you will not actually reach means the test can never
conclude. Base the floor on what the brand actually spends per week.

Each arm needs **exactly one** membership rule. Two rules means an ad could be
in the arm by one and out by the other.

A proposal lands with no `started_on`. **You cannot launch it** — the launch
happens in Ads Manager, and you were not there.

## Reading a result

```
python -m intel experiment --brand renegade --name "MA-015 ..."
```

- `spend_sufficient` false on any arm → **the effect is arithmetic, not a
  result.** Say that first, before the numbers.
- `effect_exceeds_minimum` reports whether the *registered* threshold was
  cleared. It is not a verdict.
- `improvement` carries direction, so you do not have to remember that lower is
  better for CPA and higher is better for CTR.
- `unsettled_days` > 0 → the window reaches into days Meta is still restating.

**There is no winner column and you do not supply one.** Report what the numbers
say and what was registered as convincing. Naming a winner is a decision, it has
a person's name on it, and there is no verb that writes it.

You may say: *"Arm B came in 23% below on CPA, past the 20% registered, and both
arms cleared the $1,500 floor."* That is the finding. What to do about it is
hers.
