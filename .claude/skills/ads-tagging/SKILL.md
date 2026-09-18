---
name: ads-tagging
description: Tag Meta ads with the angle, hook and offer their copy is using, and propose new angles for the bank. Use when asked to tag ads, classify creative, map reviewer angle handles, or add an angle to the vocabulary.
---

# Tagging ads

Angle-level performance is only as good as the tagging behind it. An untagged ad
that spent $4,000 distorts every angle number on the page.

## Drain the free wins first

```
python -m intel queue --brand renegade
```

`inheritable: true` means the ad came through the engine and its angle is
knowable with **no judgement at all** — `campaign_asset_id` resolves through
`creative_concepts` to an approved `campaign_angles` row. `campaign_angle_name`
is the answer. File those before reading a single line of copy.

This is what `tracking.py` has been paying into since migration 025: every
`utm_content` of the form `ad-a-v5` stamped at approval was buying this join.

## Then the ones that need reading

For the rest, ranked by spend:

1. `python -m intel ad --key <ad_key>` for the full copy.
2. `python -m intel angles --brand X` for the bank and each angle's definition.
   **Tag against the definition, not against the name** — "cash upfront" and
   "valuation" both sound financial and are different promises.
3. Check `python -m intel candidates --brand X`. If a reviewer already wrote a
   handle for this ad, that is evidence about what it is doing.

File one:

```json
{
  "brand": "renegade",
  "ad_key": "...",
  "copy_hash": "...",
  "angle_slug": "cash-upfront",
  "hook": "callout",
  "offer": "valuation",
  "confidence": "inferred",
  "rationale": "Opens by naming P&C agency owners, then leads on 90% cash at close."
}
```

```
python -m intel record --kind ad_facet --json .intel/tag.json
```

`copy_hash` comes from `intel ad` and must be the current one. The tag is keyed
on `(ad_key, copy_hash)`, so rewriting the copy retires the tag — a tag that
survived a rewrite would describe an ad that no longer exists.

**`confidence`:** `stated` only when the copy says it outright. Anything you
read into it is `inferred`. A rationale is required either way, and it should
quote or paraphrase the actual line that decided you.

**You never overwrite an operator tag.** The writer refuses it. A person looked
at that ad and decided; you did not.

## Proposing an angle

Only when an ad genuinely does not fit anything in the bank — not because a
narrower name would be tidier. A bank with forty angles answers nothing.

```json
{
  "brand": "renegade",
  "family": "buyer",
  "slug": "no-earnout",
  "name": "No earnout",
  "definition": "The promise is that the full price is paid at close, with no portion contingent on the agency's performance afterwards."
}
```

```
python -m intel record --kind angle_proposal --json .intel/angle.json
```

It lands as `proposed` and is excluded from coverage until a person activates
it. The `definition` is the part that matters: it is what the next tagging pass
reads to tell this angle from the one beside it. "Cash upfront" is a name, not a
definition.

## Mapping a reviewer's handle

`intel candidates` lists handles reviewers wrote that the bank does not map yet.
**You do not file the mapping.** Deciding that "zero broker fees" and "no broker
fees" are the same angle is a decision about vocabulary. Propose it, name the
evidence, and let a person file the alias.
