"""Everything this system is able to say about ONE ad.

intel/readings.py is the same idea at brand scope, and this file deliberately
borrows its machinery rather than re-implementing it: `_at`, `_cite` and
`_reading` are imported from there, so a rule here is checkable in exactly the
way a rule there is. A rule never types a number. It points at one, gets it
back, and interpolates what it got.

WHY A SECOND REGISTRY AND NOT MORE RULES IN readings.py

Every one of that file's rules is brand-scoped and reads the brief's fact pack.
An ad panel is a different reader asking a different question, on a page that
is not the brief, and folding the two together would mean every brand rule had
to learn to be conditional on a subject. Two registries, each answerable by
reading one list, keeps the governance property that made readings.py a
registry in the first place.

WHAT IT MAY SAY, AND WHAT IT MAY NOT

It may report which of the five symptoms fired, how much spend they fired on,
whether the spend was enough to read, what the tag says, whether the tag is
stale, which optimization goal the numbers belong to, how much of the brand's
CPA move this ad accounts for, and what a refresh test would register.

It may not say the ad is bad, should be paused, or has lost. CLAUDE.md:
"Performance is evidence about what happened, not a verdict on what to do."
A verdict carries a person's name and there is no code path here that writes
one -- the same stance readings.py takes on naming a winner.

NOTHING HERE COMPUTES

Every figure comes out of a SQL function via an existing verb. `ad_facts`
selects, filters and labels; it performs no arithmetic, and
tests/test_ad_readings.py walks this module's AST to keep it that way.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

from . import context, metrics
from .propose import FATIGUE_FLOOR, SYMPTOM_METRIC
from .readings import Reading, _at, _cite, _reading

#: Meta's own words for an ad that is delivering. Anything else means the
#: window below is history: worth reading, not worth acting on.
DELIVERING = {"ACTIVE"}

#: The five `ads.fatigue` flags, in the order SYMPTOM_METRIC resolves them, so
#: "which metric would a refresh register" and "which symptoms fired" cannot
#: disagree about precedence.
SYMPTOMS: tuple[str, ...] = tuple(s for s, _ in SYMPTOM_METRIC)


# ---------------------------------------------------------------------------
# The fact pack.
# ---------------------------------------------------------------------------

async def ad_facts(slug: str, ad_key: str, days: int = 14,
                   until: date | None = None) -> dict:
    """Compose one ad's facts out of the verbs that already produce them.

    `min_spend=0` and `unconfident=True` on the fatigue call, deliberately.
    The /creative page filters both, because a list of forty tiring ads sorted
    by a score computed on $30 is noise. This is not a list -- the reader has
    already chosen this ad -- so suppressing its row would answer "is this ad
    tiring?" with silence, and silence reads as no.

    `confident` comes back with the row and a rule says so out loud.
    """
    b = await context.brand(slug)
    settled = await context.settled_through(b["id"])

    d = await metrics.ad(ad_key, days=max(days, 90))
    head = d.get("ad") or {}
    facet = d.get("facet") or {}

    fat = await metrics.fatigue(slug, days, until, 0, True)
    row = next((r for r in (fat.get("rows") or [])
                if str(r.get("ad_key")) == str(ad_key)), None)

    # limit high enough that this ad is in the bridge if the bridge has it at
    # all. The verb orders by effect, so a small mover on a big account falls
    # off a default limit of 25 and its absence would read as "did not move".
    bridge = await metrics.why(slug, days, until, limit=1000)
    brow = next((r for r in (bridge.get("rows") or [])
                 if str(r.get("ad_key")) == str(ad_key)), None)

    fatigue_facts = None
    if row:
        fired = [s for s in SYMPTOMS if row.get(s)]
        fatigue_facts = {
            "score": row.get("score"),
            "confident": row.get("confident"),
            "spend_recent": row.get("spend_recent"),
            "days_observed": row.get("days_observed"),
            "symptoms": fired,
            # Which metric a refresh test would register. Read off
            # SYMPTOM_METRIC rather than chosen here, so this and
            # intel/propose.py cannot drift about the same ad.
            "would_register": next(
                (m for s, m in SYMPTOM_METRIC if row.get(s)), None),
            "meets_floor": (row.get("score") or 0) >= FATIGUE_FLOOR,
        }

    return {
        "verb": "ad_readings",
        "brand": b["slug"],
        "ad_key": ad_key,
        "days": days,
        "settled_through": settled,
        "facts": {
            "ad": {
                "name": head.get("name"),
                "effective_status": head.get("effective_status"),
                "optimization_goal": head.get("optimization_goal"),
                "objective": head.get("objective"),
                "ad_group_name": head.get("ad_group_name"),
                "campaign_name": head.get("campaign_name"),
                "is_tagged": bool(d.get("facet")),
                "tag_is_stale": d.get("tag_is_stale"),
                "angle_slug": facet.get("angle_slug"),
                "angle_name": facet.get("angle_name"),
                "tag_source": facet.get("source"),
                "day_count": d.get("day_count"),
            },
            "fatigue": fatigue_facts,
            "bridge": {
                "total_effect": (brow or {}).get("total_effect"),
                "rate_effect": (brow or {}).get("rate_effect"),
                "mix_effect": (brow or {}).get("mix_effect"),
                "reason": (brow or {}).get("reason"),
                "spend_current": (brow or {}).get("spend_current"),
                "spend_prior": (brow or {}).get("spend_prior"),
            } if brow else None,
        },
    }


# ---------------------------------------------------------------------------
# Delivery. First, because it changes what everything below is about.
# ---------------------------------------------------------------------------

def _not_delivering(f: dict) -> list[Reading]:
    status = _at(f, "/facts/ad/effective_status")
    if not status or status in DELIVERING:
        return []
    return [_reading(
        "not_delivering",
        f"Meta reports this ad as {status}, so everything below is a record "
        f"of what it did rather than something still in progress. A symptom "
        f"on an ad that stopped is history.",
        [_cite(f, "/facts/ad/effective_status", "ad", "effective_status")],
        section="delivery")]


# ---------------------------------------------------------------------------
# Fatigue.
# ---------------------------------------------------------------------------

def _symptoms_fired(f: dict) -> list[Reading]:
    score = _at(f, "/facts/fatigue/score")
    fired = _at(f, "/facts/fatigue/symptoms") or []
    days = _at(f, "/facts/fatigue/days_observed")
    if not score or not fired or days is None:
        return []
    return [_reading(
        "symptoms_fired",
        f"{score} of the 5 named symptoms fired over {days} day(s) of "
        f"delivery: {', '.join(fired)}. That is what ads.fatigue measured "
        f"against the window before it, not a judgement about the creative.",
        [_cite(f, "/facts/fatigue/score", "fatigue", "score"),
         _cite(f, "/facts/fatigue/symptoms", "fatigue", "symptoms"),
         _cite(f, "/facts/fatigue/days_observed", "fatigue", "days_observed")],
        section="fatigue")]


def _not_enough_spend_to_read(f: dict) -> list[Reading]:
    """A score is arithmetically correct at any spend and meaningful at few."""
    if _at(f, "/facts/fatigue/confident") is not False:
        return []
    spend = _at(f, "/facts/fatigue/spend_recent")
    if spend is None:
        return []
    return [_reading(
        "not_enough_spend_to_read",
        f"ads.fatigue reports this ad as not confident: it spent {spend} in "
        f"the window, which is below the floor the function applies before it "
        f"will stand behind a symptom count. The count above is arithmetically "
        f"correct and should not be acted on alone.",
        [_cite(f, "/facts/fatigue/confident", "fatigue", "confident"),
         _cite(f, "/facts/fatigue/spend_recent", "fatigue", "spend_recent")],
        section="fatigue")]


def _below_the_proposal_floor(f: dict) -> list[Reading]:
    score = _at(f, "/facts/fatigue/score")
    if score is None or _at(f, "/facts/fatigue/meets_floor") is not False:
        return []
    return [_reading(
        "below_the_proposal_floor",
        f"With {score} symptom(s) this ad sits below the floor intel propose "
        f"uses before it will suggest a refresh test, because one symptom on "
        f"one window is noise often enough to bury the ads showing three.",
        [_cite(f, "/facts/fatigue/score", "fatigue", "score"),
         _cite(f, "/facts/fatigue/meets_floor", "fatigue", "meets_floor")],
        section="fatigue")]


def _what_a_refresh_would_register(f: dict) -> list[Reading]:
    metric = _at(f, "/facts/fatigue/would_register")
    if not metric or _at(f, "/facts/fatigue/meets_floor") is not True:
        return []
    return [_reading(
        "what_a_refresh_would_register",
        f"A refresh test on this ad would register {metric} as its primary "
        f"metric, taken from the first symptom that fired. It cannot be filed "
        f"until the replacement creative exists, because the second arm has "
        f"to name real ads. What effect would convince you is not suggested "
        f"here and is yours to set before the test runs.",
        [_cite(f, "/facts/fatigue/would_register", "fatigue", "would_register"),
         _cite(f, "/facts/fatigue/symptoms", "fatigue", "symptoms")],
        section="fatigue")]


# ---------------------------------------------------------------------------
# What the numbers may be compared with.
# ---------------------------------------------------------------------------

def _goal_segments_the_comparison(f: dict) -> list[Reading]:
    goal = _at(f, "/facts/ad/optimization_goal")
    if not goal:
        return []
    return [_reading(
        "goal_segments_the_comparison",
        f"This ad's group optimises {goal}. Cost per lead is not comparable "
        f"across optimization goals, so read this ad against other {goal} "
        f"groups or say that you did not.",
        [_cite(f, "/facts/ad/optimization_goal", "ad", "optimization_goal")],
        section="comparability")]


def _moved_the_brand_cpa(f: dict) -> list[Reading]:
    total = _at(f, "/facts/bridge/total_effect")
    reason = _at(f, "/facts/bridge/reason")
    if total is None or not reason:
        return []
    if reason == "attributable":
        rate = _at(f, "/facts/bridge/rate_effect")
        mix = _at(f, "/facts/bridge/mix_effect")
        if rate is None or mix is None:
            return []
        return [_reading(
            "moved_the_brand_cpa",
            f"In the CPA bridge this ad accounts for {total} of the brand's "
            f"move, split {rate} from its own rate and {mix} from how much of "
            f"the spend it took.",
            [_cite(f, "/facts/bridge/total_effect", "why", "total_effect"),
             _cite(f, "/facts/bridge/rate_effect", "why", "rate_effect"),
             _cite(f, "/facts/bridge/mix_effect", "why", "mix_effect")],
            section="movement")]
    return [_reading(
        "moved_the_brand_cpa_unattributable",
        f"In the CPA bridge this ad accounts for {total} of the brand's move, "
        f"and the split into rate and mix is undefined for it -- the bridge "
        f"names the case as {reason}. Reporting a split here would be the "
        f"interesting half of a lie.",
        [_cite(f, "/facts/bridge/total_effect", "why", "total_effect"),
         _cite(f, "/facts/bridge/reason", "why", "reason")],
        section="movement")]


# ---------------------------------------------------------------------------
# The tag, which is what connects this ad to every angle number on the site.
# ---------------------------------------------------------------------------

def _untagged(f: dict) -> list[Reading]:
    if _at(f, "/facts/ad/is_tagged") is not False:
        return []
    return [_reading(
        "untagged",
        "This ad carries no creative tag, so it is invisible to every angle "
        "number on this site: it spends, and no angle gets the credit or the "
        "blame. File one with intel record --kind tag.",
        [_cite(f, "/facts/ad/is_tagged", "ad", "is_tagged")],
        section="tag")]


def _tag_is_stale(f: dict) -> list[Reading]:
    if _at(f, "/facts/ad/tag_is_stale") is not True:
        return []
    return [_reading(
        "tag_is_stale",
        "The tag on this ad was filed against wording it no longer runs. A tag "
        "is keyed on (ad_key, copy_hash) so that a tag surviving a rewrite "
        "would describe an ad that no longer exists; this row is the visible "
        "version of that, and the angle numbers exclude it.",
        [_cite(f, "/facts/ad/tag_is_stale", "ad", "tag_is_stale")],
        section="tag")]


def _tagged_to_angle(f: dict) -> list[Reading]:
    angle = _at(f, "/facts/ad/angle_name")
    source = _at(f, "/facts/ad/tag_source")
    if not angle or _at(f, "/facts/ad/tag_is_stale") is True:
        return []
    return [_reading(
        "tagged_to_angle",
        f"This ad is tagged to the angle {angle}, filed by {source}. Every "
        f"angle number that includes this ad rests on that tag being right.",
        [_cite(f, "/facts/ad/angle_name", "ad", "angle_name"),
         _cite(f, "/facts/ad/tag_source", "ad", "tag_source")],
        section="tag")]


# ---------------------------------------------------------------------------
# The registry. Delivery first: it changes what everything after it is about.
# ---------------------------------------------------------------------------

AD_RULES: tuple[tuple[str, Callable[[dict], list[Reading]]], ...] = (
    ("not_delivering", _not_delivering),
    ("symptoms_fired", _symptoms_fired),
    ("not_enough_spend_to_read", _not_enough_spend_to_read),
    ("below_the_proposal_floor", _below_the_proposal_floor),
    ("what_a_refresh_would_register", _what_a_refresh_would_register),
    ("moved_the_brand_cpa", _moved_the_brand_cpa),
    ("goal_segments_the_comparison", _goal_segments_the_comparison),
    ("untagged", _untagged),
    ("tag_is_stale", _tag_is_stale),
    ("tagged_to_angle", _tagged_to_angle),
)


def read_ad(facts: dict) -> list[Reading]:
    """Fire every rule against one ad's fact pack, in registry order.

    A rule that raises is a bug in that rule and must not take the page down
    with it, for the same reason readings.read gives: a panel missing one
    sentence beats a traceback where the panel was.
    """
    out: list[Reading] = []
    for name, fn in AD_RULES:
        try:
            out.extend(fn(facts) or [])
        except Exception as exc:  # pragma: no cover - defensive
            out.append(_reading(
                "rule_failed",
                f"The rule {name!r} raised {type(exc).__name__} and produced "
                f"nothing. That is a defect in the rule, not a finding about "
                f"this ad.",
                [], section="trust"))
    return out


async def ad_reading(slug: str, ad_key: str, days: int = 14,
                     until: date | None = None) -> dict:
    """The verb: one ad's facts and everything sayable about them."""
    facts = await ad_facts(slug, ad_key, days, until)
    return {**facts, "readings": read_ad(facts)}
