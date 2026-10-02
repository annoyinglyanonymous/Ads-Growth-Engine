"""Format a fact pack for a sentence, not for a table.

WHY THIS EXISTS

The 2026-09-20 brief opened: "You spent 15747.79 in the seven days to 20
September and got 380 leads, at 41.4416 each". Every figure in it was correct
and none of them were written the way a person writes a number. She has said so
about this page already -- "more professional text, not this json format".

WHY NOT JUST TELL THE MODEL TO ROUND

Two reasons, and the second is the one that matters.

A prompt that says "write money as $41.44" is asking the model to round, and
the same prompt has to say "quote figures exactly as they appear; do not round,
total, average, or work out a percentage that is not already there" -- because
that is the rule that stops it deriving numbers. Those two instructions argue
with each other and the model gets to pick.

And it would disagree with the page. `charts.money` prints cents below a
thousand and drops them above it, so the card for this week says "$15,748"
while a model told to write dollars-and-cents says "$15,747.79". CLAUDE.md is
explicit about why that matters: "how the dashboard card and your sentence come
to disagree, and whichever one the reader is looking at is the one they act on."

So the numbers are formatted BEFORE the model sees them, by the same functions
the templates call. The model quotes what it is given, which is what it was
always told to do, and the sentence and the card cannot drift apart because
they came out of the same `charts.money`.

WHAT IT DOES NOT TOUCH

The archive. `summarise()` formats the COPY it hands over; `doc["facts"]` keeps
full precision, because a brief whose numbers have been rounded for reading is
a brief nobody can check afterwards -- and checking them afterwards is the only
way anyone finds out whether the readings were any good.

WHY AN EXPLICIT LIST

Because `cpa_change` is dollars and `attributable_share` is a percentage and no
amount of substring matching on "change" or "share" gets that right every time.
A key this list does not name is left exactly as it is: unformatted is a number
that looks raw, mis-formatted is a number that looks wrong. The list was
written against the keys a real brief actually contains, not guessed.
"""

from __future__ import annotations

from decimal import Decimal

# The one presentation import in intel/, and deliberate. The alternative is a
# second implementation of "how this account writes a dollar", which is the
# thing that drifts.
import charts

#: Amounts of money, including the CPA deltas -- rate_effect and mix_effect are
#: "this many dollars of the move", not shares.
MONEY = frozenset({
    "spend", "total_spend", "tagged_spend", "untagged_spend",
    "inheritable_spend", "stale_tag_spend",
    "spend_current", "spend_prior", "spend_recent",
    "cpa", "cpa_current", "cpa_prior", "cpa_change",
    "cpc", "cpm", "cost_per_link_click",
    "rate_effect", "mix_effect", "total_effect",
    "attributable_effect", "unattributable_effect",
})

#: Already expressed as a percentage in the database -- link_ctr 0.3271 means
#: 0.33%, which is what charts.pct prints and what the tiles show.
PERCENT = frozenset({
    "ctr", "link_ctr", "conversion_rate", "lp_view_rate",
    "attributable_share",
})

#: Ratios that are neither money nor a percentage. Daily frequency sits near
#: 1.0-1.3 and reads as noise at full precision.
DECIMALS = {"daily_frequency_avg": 2, "score": 2}

_NUMERIC = (int, float, Decimal)


def for_prose(obj):
    """Return a copy with known metric keys rendered the way the page renders them.

    Recurses through dicts and lists. Anything not named above is returned
    untouched, including counts, ids, dates and strings.
    """
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if _is_number(value):
                if key in MONEY:
                    out[key] = charts.money(value)
                    continue
                if key in PERCENT:
                    out[key] = charts.pct(value)
                    continue
                if key in DECIMALS:
                    out[key] = charts.num(value, DECIMALS[key])
                    continue
            out[key] = for_prose(value)
        return out
    if isinstance(obj, list):
        return [for_prose(v) for v in obj]
    return obj


def _is_number(v) -> bool:
    """True for a number, and for the strings a Decimal becomes in transit.

    A pack that has been through json.dumps(default=str) -- which is how the
    brief is archived and re-read -- carries "15747.79" rather than
    Decimal("15747.79"), and it should format the same either way.
    """
    if isinstance(v, bool):
        return False
    if isinstance(v, _NUMERIC):
        return True
    if isinstance(v, str):
        try:
            float(v)
        except ValueError:
            return False
        return True
    return False
