---
name: meta-ads
description: Write stage 7 Meta ad copy through the engine boundary. Reads the approved concepts and the governed input with `engine context --stage meta_ads`, writes an A and a B variant per concept to the copywriting standard this file sets, and records them as drafts with `engine record`. Re-run QA afterwards. Use when approved concepts need ad copy. Never approves.
---

# Meta ads (stage 7)

You write these. Read `CLAUDE.md` first.

```
python -m engine context "<campaign>" --stage meta_ads
python -m engine record  "<campaign>" --kind meta_ads --context <id> --json -
```

`upstream` is every approved concept, each with its **`concept_id`**. You must
cite that id on every ad — it is how the row gets its `concept_id`, which 013
makes NOT NULL.

If `context` refuses: either the brief did not ask for `meta_ads`, or she has
approved no concepts. Both are messages for her, not problems to work around.

## What to write

**Two ads per approved concept: one variant A, one variant B.** They are the
same concept argued two ways, not two concepts. Make B a genuinely different
execution of the same idea — a different opening move, not the same headline
reworded.

**An ad is three things, and only three**: a Heading (`headline`), a Body
(`primary_text`) and a CTA (`cta_label`). All three are required and `record`
refuses a payload missing any of them, by name.

`description` is gone as of 2026-09-15, by her decision. Do not send it — an
unknown key is refused rather than dropped. Meta still has the field and the
house guide still describes it, so this is a choice about what this engine
produces rather than a claim about what Meta accepts.

Only the Heading and the Body have character limits, 40 and 125. `record`
reports the limit and the actual length when one is over. **Fix it by
tightening the phrasing, not by truncating** — a heading cut mid-word is
worse than a shorter one that reads. The CTA has no limit; use one of the
approved CTAs for the campaign's decision weight, which the house style
lists.

### The shape

```json
{
  "ads": [
    {
      "concept_id": "the id from upstream, exactly",
      "variant": "A",
      "headline": "string",
      "primary_text": "string",
      "cta_label": "string"
    },
    ...
  ]
}
```

## How the copy has to read

Her instruction, 2026-09-15, after reading the Outreach101 ads:

> It should write ads like a senior ads copywriter and should give Headline,
> Body and CTA.

The three fields were already the shape by then. This section is about what
goes in them, and it is the standard every ad is held to -- first drafts and
revisions alike.

What she rejected, so you recognise it when you write it:

> heading: Your book, priced on what it does
> body: You built the retention. A blanket multiple prices it like every
> other book. Book a free consultation.

That reads like a note between strategists. The heading is a riddle, the
body leans on the concept card to make sense, and nothing in it tells an
agency owner what happens if they tap. **The ad is read by a stranger, on a
phone, in about two seconds, with the concept nowhere in sight.** Every line
has to work on its own.

### Heading (40 characters)

- **A hook, not a label.** It names the reader's situation, the decision in
  front of them, or the one thing about this offer that is different. It is
  the reason to stop scrolling.
- **Plain words an agency owner would say out loud.** If the owner would
  have to read it twice, it is a puzzle, not a heading.
- **Concrete beats clever.** A number from `assertable`, a situation, a real
  consequence. The house guide's test holds here: it fails if any buyer could
  say it to any owner.
- **A complete thought.** A fragment that needs the body to finish it loses
  the reader who only reads headings, which is most of them.

The house guide's "Headline: 8 to 12 words" describes the line on the image.
The field you write is the Facebook headline under it, capped at 40, so
aim for four to seven words that carry one idea.

### Body (125 characters)

- **Three moves, in order: name the reader or their situation, give the one
  concrete reason to believe, tell them the next step.** The house guide
  asks for the first and the last. A senior copywriter never skips the
  middle.
- **The reason to believe is drawn from `assertable`, in words a reader
  understands.** "Priced on retention, revenue, growth and book mix, not a
  blanket multiple" is a reason. "Retention, revenue, book mix" on its own
  is a list of nouns.
- **Complete sentences, spoken to "you". One idea per ad.** If a second
  sentence starts a second idea, that is variant B or a different concept.
- **End with a CTA sentence that agrees with the button.** The
  `cta_consistency` check looks for the campaign CTA's words somewhere in
  the text. Write the closing line so it passes honestly, not by accident.
- **125 characters is a discipline, not permission to write in shorthand.**
  If it does not fit as sentences, cut the idea, never the grammar.

### CTA

- The campaign's `primary_cta`, verbatim, unless the concept clearly calls
  for `secondary_cta`. Both arrive with `brief`. Never invent a third.
- No pressure language and no exclamation marks. The house guide's CTA table
  is the whole list of what is allowed.

### The read-through, before you record

Read the heading, body and CTA together as one ad, out loud if it helps, and
ask:

1. Would a senior copywriter at an agency put their name on this as a
   finished ad, not a draft?
2. From these three lines alone, does a stranger know who it is for, why it
   is worth believing, and what to do next?
3. Is there a hook, or only a statement?
4. Does it say something only Renegade can say to this reader?
5. Is the concept **behind** the ad rather than **in** it? The concept is the
   argument; the ad is what the argument sounds like when you say it to the
   owner. Concept wording leaking into the copy is the most common failure
   here.

If the answer to any of those is no, it is not finished. Rewrite it before
you record it, because she reads what you record.

### Variant B is a different opening move

Same concept, argued a different way: a different hook, a different first
line, a different way into the same idea. Not the same heading reworded, and
not a second concept.

## The slot is the part that bites quietly

A `campaign_assets` row is identified by
`(campaign, channel, asset_type, variant, position)`. **`concept_id` is not
in that tuple**, so two ads that share a variant letter are the same slot
however different their concepts are — the partial unique index treats them
as two *versions of one ad*, so approving the second silently supersedes the
first and she never sees it happen.

**So the variant letter has to be unique across the whole campaign, not per
concept.** If you are writing ads for one concept, that is A and B and there
is nothing to think about. If you have been given more than one concept,
keep going through the alphabet — C and D for the second, E and F for the
third — rather than restarting at A. Eight concepts written as A/B each is
sixteen ads in two slots, fourteen of them unreachable: that happened on
Outreach test 1, 2026-09-11.

`position` is null for ads. Only email sequences use it.

**`record` enforces this now, and it did not before.** The check used to key
on `(concept_id, variant)` — the shape you think in, not the shape the index
enforces — so A/B per concept passed and the rows were lost on the way in.
It keys on the letter alone now, names the entry you collided with, and
accepts any letter A–Z. If you get that refusal, the fix is always to keep
walking the alphabet rather than to merge two concepts into one ad.

## Afterwards

**Re-run QA. Always.** New copy carries no QA result, and un-QA'd copy cannot
be approved, so skipping this hands her a page of buttons that refuse:

```
python -m validation.asset_qa "<campaign>"
```

Read the findings before reporting. A **blocker** is deterministic and
correct — fix the copy, never argue with it or suggest an override. A
**warning** is a truthful finding that needs her judgement, so surface it in
plain language. When a blocker fires on prohibited wording, **say which list
caught it**: brand exclusions (standing, at `/exclusions`) or the brief's
`do_not_mention` (one-off). She needs to know whether to argue with the brand
or with the brief.

## Then get it read, before you report

QA answers "is this allowed". Nothing in it answers "is this any good", and
**"How the copy has to read" above is the standard that had nobody checking
it** until this step existed. The review is what enforces that section. Run it
on every ad you wrote:

```
python -m review context --asset <campaign_asset_id>
python -m review record --kind ad_review --context <id> --json .engine/inbox/review-<id>.json
```

**Run it in a fresh subagent, one per ad**, whose entire instruction is
`ad-review/SKILL.md` and that one `context` command. Two reasons, and the
second is the real one:

- The reader gets the **rating view** -- the scoring rules, the benchmarks in
  full and what she has said about this brand's copy before, all of which
  `brand.py` keeps out of your prompt on purpose, because a writer told how it
  will be scored writes for the score.
- It arrives with **none of your drafting context**. You know why you chose
  that heading, and a writer grading its own work grades the intention rather
  than the words on the page. A stranger on a phone does not have the reason.

**Below 8 is not finished.** Rewrite the ad against the read-through above,
record the new version, **re-run QA on it** (a new version carries no QA
result, and un-QA'd copy cannot be approved), and review that. Rule 4 decides
what the review hands back either way: under 8 it gives two whole options, and
at 8 or above two line-level tightenings and no rewrite, because a rewrite
throws away wording she has already read. `record` refuses a second review of
the same words and names the existing row; that is the rule working, not an
error to route around. Change the copy, then review it.

**Say the score when you report.** A score you did not mention is a reader she
does not know she had.

## Finishing

Counts, then the scores, then anything carrying a finding, then the link:

> Six Meta ads for Outreach101, an A and a B for each of her three concepts.
> Reviewed: five score 8 or above. Ad B on "Keep the book" came back at 6.4
> for a heading that was a label rather than a hook, so it was rewritten and
> re-recorded as v2, which scores 8.4.
> QA is clean on five. Ad B on "Keep the book" carries a warning: the primary
> text has no call to action, which is a real finding rather than a limit
> problem, so it is worth reading before you approve it.
> http://127.0.0.1:8000/campaigns/<id>

Keep the two apart when you say them. The findings are facts produced by code
with no false positives; the scores are one reader's opinion. She reads both
on the asset card and she should hear them in the same shape.

## There is no other path

The generator this replaced is deleted. This file is the only place the
ground rules above are written down, so editing it is how they change --
there is no Python constant to keep in step any more.
