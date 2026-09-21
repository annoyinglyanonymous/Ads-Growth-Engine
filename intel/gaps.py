"""The questions this system cannot answer, and why — as data, not prose.

Every brief ships this list. Three reasons it is a module and not a paragraph
in a skill file:

1. **An empty section and an unanswerable question look identical.** A brief
   that says nothing about revenue reads as "revenue was unremarkable". A brief
   that says "there is no revenue in this database and here is what would put
   it there" reads as what is actually true. Silence is the failure mode; this
   is the fix.

2. **The backlog belongs next to the code that lacks it.** `qualified_conversions`
   has been computed on every read of `ads.fact_ad_day` since 002 and selected
   by nothing. That is not a decision anybody made, it is a thing nobody
   noticed, and it went unnoticed because the gap lived in somebody's head
   rather than in the repo.

3. **It is the honest version of a roadmap.** After a few weeks of briefs, the
   two or three questions she keeps asking are the ones worth building. Guessing
   which six to build in advance is how a schema acquires another
   `qualified_conversions`.

This file states what is missing. It never apologises for it, and a gap being
listed here is not a promise to close it — several of these are things the data
genuinely cannot support and never will, and saying so plainly is the point.

Each entry is (question, why, what_would_answer_it). `what_would_answer_it`
names the concrete change, so a reader can tell "nobody built it" apart from
"it is not derivable", which are very different answers to the same silence.
"""

from __future__ import annotations

#: Ordered roughly by how often somebody asks.
GAPS: tuple[tuple[str, str, str], ...] = (
    (
        "Which angle makes money, and what is our ROAS?",
        "There is no revenue in this database. meta_ads/client.py:202 requests "
        "`actions` and `cost_per_action_type` and never `action_values` or "
        "`purchase_roas`, ads.conversion_definition counts events and has no "
        "value column, and ads.rate takes no revenue argument. Every metric "
        "here is a cost-per.",
        "Add action_values to INSIGHT_FIELDS, a value column to "
        "ads.conversion_definition, and roas to ads.rate. Needs a growth-engine "
        "migration for the public.meta_ad_insights column and a re-pull.",
    ),
    (
        "What did a qualified lead cost, as opposed to a lead?",
        "ads.fact_ad_day computes qualified_conversions and booked_conversions "
        "on every read (002:293-295) from the three-rung counts_as, and no "
        "function has ever selected them. The distinction 002:62-65 says the "
        "whole CRM connection exists for is computed and thrown away.",
        "Surface both in ads.window_metrics and ads.facet_performance, and give "
        "ads.rate a cost_per_qualified. No re-pull, no new API surface — the "
        "numbers are already in the view.",
    ),
    (
        "Are we saturating the audience? What is the marginal return on more spend?",
        "Not derivable from what Meta sends. Daily reach is deduplicated within "
        "the day so it cannot be summed (ads.window_metrics returns "
        "reach_best_day, not a total), and cumulative frequency is not in this "
        "database at all. ads-intel/SKILL.md:109 already says claiming "
        "saturation from daily frequency is a claim the data cannot support.",
        "Nothing available. This one is closed, not pending — a spend-vs-CPA "
        "curve built on daily reach would be a confident answer to a question "
        "the inputs cannot address.",
    ),
    (
        "Is this CPA difference real, or is it noise?",
        "There is no statistical inference anywhere outside a registered "
        "experiment — no confidence interval, no p-value, no power calculation. "
        "That is deliberate. ads.experiment_result tests against a "
        "minimum_effect_pct registered BEFORE anyone looked at a number, and "
        "005:26-32 argues that pre-registration is the entire mechanism against "
        "reading a 12% difference on $80 of spend as a result.",
        "Register an experiment. A significance figure on a slice chosen after "
        "seeing it would quietly undo the thing the experiment table exists to "
        "protect, so this gap stays open on purpose.",
    ),
    (
        "Which placement, age or device is working?",
        "The importer sends no `breakdowns` parameter (client.py:441-446), so "
        "publisher_platform, placement, age, gender, region and device are not "
        "in the warehouse in any form.",
        "A separate public.meta_ad_insights_breakdown table and a second pull "
        "phase. Never a widening of meta_ad_insights — that would break the "
        "(ad_id, date) primary key that makes a restatement a rewrite instead "
        "of a duplicate.",
    ),
    (
        "Did the budget change cause the CPA change?",
        "007 gives a timestamped list of budget, status and optimisation-goal "
        "edits, and `why` prints it beside the bridge — but nothing joins a "
        "change to the performance around it. 007:16-20 argues this is the most "
        "common cause of a CPA move, and the analysis stops at two tables that "
        "do not talk to each other.",
        "A before/after window around each change event in ads.cpa_bridge, and "
        "a budget_changed_in_window flag per ad.",
    ),
    (
        "Do our ads always decay by day twelve?",
        "Nothing keys on days-since-launch. ads.fatigue compares fixed calendar "
        "windows, so a three-week-old ad and a three-day-old one are compared "
        "on the same dates rather than at the same age. ads.ad.first_seen_at "
        "exists and is used as a fact, never as a cohort axis.",
        "ads.decay(brand_id, ad_keys, max_age_days), bucketing metrics by age "
        "rather than by date.",
    ),
    (
        "How does this brand compare to the other one?",
        "Every verb takes one --brand and every SQL function takes a single "
        "scalar p_brand_id. No verb, function or page shows two brands side by "
        "side. Angle families are global by construction (004:26-34) and the "
        "comparison the schema was designed for has never been built.",
        "ads.family_across_brands(since, until). Note the mixed-currency guard "
        "applies with more force across brands than within one.",
    ),
    (
        "Which wording was running when that spend happened?",
        "public.meta_ad_texts is not effective-dated. ads.ad_copy groups by "
        "ad_id (002:249), so the warehouse holds exactly one wording per ad — "
        "the current one. Spend that ran under a previous wording is attributed "
        "to the current wording's angle, and the old text is simply gone.",
        "Effective-dating the text rows at import, which only helps from the "
        "day it ships. History cannot be recovered; Meta does not keep it "
        "either. ads.angle_coverage returns stale_tag_ads so the size of the "
        "effect is at least visible.",
    ),
    (
        "Did anything spike yesterday?",
        "intel status checks IMPORT health — stale pulls, a silent token, a "
        "missing conversion definition, the wrong credential. Nothing watches "
        "the numbers themselves. There is no threshold breach and no outlier "
        "verb.",
        "ads.alert(brand_id, asof) in ads.fatigue's style: named, "
        "pre-registered conditions you can read out loud, never a weighted "
        "score and never a model's judgement.",
    ),
)


def register() -> list[dict]:
    """The gaps, as the brief carries them."""
    return [{"question": q, "why": why, "what_would_answer_it": fix}
            for q, why, fix in GAPS]
