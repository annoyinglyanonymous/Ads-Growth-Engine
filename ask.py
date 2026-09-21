"""The ask box: a question becomes one named verb, or it becomes nothing.

WHAT THIS IS NOT. It is not a model, and it does not phrase an opinion. It
routes a sentence to one of the read verbs already on this page's menu and
hands back that verb's own output. Every number it shows was computed by a
function in migrations/003_ads_metrics.sql, which is the rule the whole repo
rests on: nothing here derives a rate, so nothing here can disagree with the
card sitting two inches above it.

THE ALLOWLIST IS THE SECURITY MODEL. There is no free-text query path to the
database. A question that matches nothing is refused with the list of what it
could have asked instead, which is the honest answer and also the useful one.
This mirrors CLAUDE.md's stance on the agent -- "Don't give it unrestricted
SQL initially" -- for the same reason: a named verb can be audited, and a
string that reaches a cursor cannot.

FIVE VERBS ARE ABSENT, FOR TWO DIFFERENT REASONS.

Two are kept out on purpose, and would be a mistake to add:

  record  writes. This surface is read-only, and a text box that can write is
          a text box that eventually writes something nobody signed.

  live    calls Meta. It is two structure requests, not a pull, but it is
          still a rate-limited external call and this box invites typing. A
          page that can spend the rate limit one keystroke at a time will,
          and the failure lands on the scheduled sync rather than here. Ask
          `python -m intel live --brand X` in a terminal, where the cost of
          the call is visible to the person paying it.

Three are absent because a sentence cannot supply what they need, which is a
weaker reason and one worth revisiting if somebody keeps asking for them:

  ad         needs an ad_key -- a uuid, which nobody types into a box. Reached
             by clicking through from a table instead.
  experiment needs the exact name of one experiment.
  versus     needs TWO angle slugs, and picking them out of a sentence
             reliably is the whole problem. It is also shadowed: any question
             naming an angle hits the `angles` rule first, because that rule
             sits above the `versus|vs` token in the compare rule.

This docstring said "TWO VERBS ARE DELIBERATELY ABSENT" while five were,
which read as a complete list and was not one.
"""

from __future__ import annotations

import re

from intel import angles as angles_mod
from intel import brief as brief_mod
from intel import experiments as exp_mod
from intel import health, metrics

#: Every verb the box can reach, with the shape of its call. Nothing routes
#: anywhere that is not in this dict, and adding a row is the only way to
#: widen the surface -- which is the point of writing it as data.
VERBS = {
    "overview":    "what ran and what it cost",
    "compare":     "this window against the one before it",
    "why":         "the CPA bridge: rate effect vs mix effect",
    "fatigue":     "which creatives are tiring",
    "trend":       "one metric over time",
    "angles":      "how each angle is doing",
    "coverage":    "what has never been tested",
    "queue":       "ads with no creative tag",
    "candidates":  "reviewer handles not yet mapped to the bank",
    "experiments": "what has been tested before",
    "status":      "whether any of this is worth reading",
    "brief":       "the recurring read, assembled from all of the above",
}

#: Matched in order, so the specific patterns sit above the general ones.
#: "why did cpa move" must not be caught by the `cpa` in the overview rule.
#: `\w*` on the stems rather than bare words, because people type plurals and
#: participles. `\bangle\b` does not match "angles" and `\bfatigu\b` matches
#: nothing at all -- both shipped broken until the routing tests were written.
ROUTES: list[tuple[str, str]] = [
    (r"\b(why|bridge|mix effect|rate effect|what moved|what changed)\b", "why"),
    (r"\b(fatigu\w*|tired|tiring|worn out|burn(ed|ing)? out|stale creative)\b", "fatigue"),
    (r"\b(coverage|never (run|tested)|untested|what should we test|gaps?)\b", "coverage"),
    (r"\b(untagged|queue|needs? a tag|no tag)\b", "queue"),
    (r"\b(candidates?|unmapped|handles?)\b", "candidates"),
    (r"\b(experiments?|test(ed|s)? before|prior test)\b", "experiments"),
    (r"\b(angles?|messaging|positioning)\b", "angles"),
    (r"\b(status|health|stale|fresh|last import|up to date|settled)\b", "status"),
    (r"\b(compare|versus|vs\.?|last (week|month|period)|prior)\b", "compare"),
    (r"\b(trends?|over time|by week|history)\b", "trend"),
    (r"\b(overview|spend|cost|cpa|cpl|ctr|cpm|summary|how (are|is) we|performance)\b", "overview"),
]

#: Longest needle first: "link ctr" has to be tested before "ctr", or every
#: request for link CTR quietly becomes a chart of all clicks -- which is the
#: exact confusion the rest of this repo spends comments warning about.
_METRIC_WORDS: list[tuple[str, str]] = [
    ("conversion rate", "conversion_rate"), ("conversion_rate", "conversion_rate"),
    ("link ctr", "link_ctr"), ("link_ctr", "link_ctr"),
    ("spend", "spend"), ("cpm", "cpm"), ("cpc", "cpc"),
    ("cpa", "cpa"), ("ctr", "ctr"),
]


class Unroutable(ValueError):
    """The question matched no verb. Carries the menu, not just a refusal."""


def route(question: str) -> tuple[str, dict]:
    """-> (verb, kwargs). Raises Unroutable with something useful to read."""
    q = (question or "").strip().lower()
    if not q:
        raise Unroutable("Ask about spend, CPA, fatigue, angles or experiments.")

    verb = next((v for pattern, v in ROUTES if re.search(pattern, q)), None)
    if verb is None:
        raise Unroutable(
            "I only know a fixed set of verbs, and that matched none of them: "
            + ", ".join(sorted(VERBS)) + "."
        )

    args: dict = {}
    # "last 14 days", "14d", "over 90 days". Anything outside a sane window is
    # clamped rather than refused -- the question was clear, the number wasn't.
    if m := re.search(r"\b(\d{1,3})\s*(?:d\b|day)", q):
        args["days"] = max(1, min(365, int(m.group(1))))
    if verb == "trend":
        args["metric"] = next((m for needle, m in _METRIC_WORDS if needle in q), "cpa")
    return verb, args


async def answer(question: str, brand: str) -> dict:
    """Run the routed verb. Read-only: none of these touch the owner pool."""
    verb, args = route(question)
    days = args.get("days")

    if verb == "brief":
        d = await brief_mod.brief(brand, days or 28, None)
    elif verb == "overview":
        d = await metrics.overview(brand, days or 28, None, "campaign", limit=12)
    elif verb == "compare":
        d = await metrics.compare(brand, days or 14, None, "ad", limit=20)
    elif verb == "why":
        d = await metrics.why(brand, days or 7, None, limit=12)
    elif verb == "fatigue":
        d = await metrics.fatigue(brand, days or 7, None, 100.0, False)
    elif verb == "trend":
        # Keywords, not positions: trend()'s 4th parameter is `until`, a date.
        # Passing the bucket size there positionally typechecks and then asks
        # the database for a window ending on the 7th of nothing.
        d = await metrics.trend(brand, metric=args.get("metric", "cpa"),
                                days=days or 90, bucket=7)
    elif verb == "angles":
        d = await angles_mod.angles(brand, days or 90, None)
    elif verb == "coverage":
        d = await angles_mod.coverage(brand, None, days or 90, None)
    elif verb == "queue":
        d = await angles_mod.queue(brand, days or 90, None, limit=20)
    elif verb == "candidates":
        d = await angles_mod.candidates(brand)
    elif verb == "experiments":
        d = await exp_mod.experiments(brand)
    elif verb == "status":
        d = await health.status(brand)
    else:  # pragma: no cover - VERBS and the branches above are one list
        raise Unroutable(f"{verb} is listed but not wired up.")

    return {"verb": verb, "says": VERBS[verb], "args": args, "result": d}
