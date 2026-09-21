"""propose -- experiment ideas, from what the account has and has not done.

READ ONLY. Nothing here files anything; `intel record --kind experiment` does
that and a person runs it. This module reads the same verbs the dashboard
reads and turns what they say into questions worth answering.

THE ONE RULE THAT SHAPES EVERYTHING HERE

A proposal is about something that has NOT RUN. That sounds like a stylistic
preference and it is not: 005 makes you register `primary_metric`,
`minimum_effect_pct` and `minimum_spend_per_arm` BEFORE any number exists,
because a threshold chosen after reading the window is not a threshold, it is
a description of what you already saw. So "compare these two groups of ads
that both ran last month" is not an experiment however carefully it is
phrased -- the numbers are already in.

Those comparisons are still useful, so they are here, in `observations`, in a
separate list, with `not_an_experiment` on every row and no `how_to_file`.
They tell you where to look. They cannot be pre-registered and this module
will not pretend otherwise.

WHAT IS DELIBERATELY LEFT BLANK

`minimum_effect_pct` and `minimum_spend_per_arm` come back None on every
proposal. intel/brief.py's `_proposals` established this and the reason is
the same one: suggesting a threshold immediately after reading this week's
numbers launders a post-hoc figure into a pre-registration. The metric is
suggested, because which metric is failing is a fact about the symptom. What
would convince you is yours.

FILEABLE IS NOT THE SAME AS GOOD

An arm keyed on `angle_id` can be registered before a single ad exists for it
-- the angle is in the bank, the ads get made after. An arm keyed on `ad_keys`
cannot: the ads have to exist to be named. So a refresh test is a real idea
that is not fileable until you have actually made the replacement, and it says
so rather than being hidden for failing a validation it was never going to
pass.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from db import fetch_all

from . import angles as angles_mod
from . import metrics
from .metrics import _frame

#: How many symptoms before a fatiguing ad is worth a test of its own. Two,
#: not one: a single symptom on one window is noise often enough that
#: proposing a test for each would bury the ones with three.
FATIGUE_FLOOR = 2

#: Which metric a refresh test should register, given what is actually going
#: wrong. Ordered, because an ad can show several and the first one that fires
#: is the one the symptom names -- registering `cpa` for an ad whose
#: complaint is link CTR tests something nobody asked about.
SYMPTOM_METRIC: tuple[tuple[str, str], ...] = (
    ("link_ctr_decline", "link_ctr"),
    ("cpa_rise", "cpa"),
    ("cpm_rise", "cpm"),
    ("frequency_rise", "cpm"),
    ("ranking_drop", "link_ctr"),
)

#: A name like "20 Years Experience | UGC | Video" carries a vocabulary the
#: account already uses. 185 of 201 renegade ads are shaped this way, and the
#: TRAILING segments are reliable -- Static, UGC, Video, Blue Background, GIF.
#: The leading segment is usually the angle and is NOT reliable (Static leads
#: 24 names), which is why nothing here treats position one as an angle.
_SEGMENT = re.compile(r"\s*\|\s*")

#: Segments that describe nothing. Version markers and stray fragments; a
#: "gap" in V2 is not a creative idea.
_NOT_A_STYLE = {"v2", "v3", "v4", "new", "copy", "test", ""}

#: How many DIFFERENT leading segments a name part must appear beside before
#: it counts as a style rather than a word that happens to sit after a pipe.
#: See _vocabulary -- this is the filter that keeps "doug" out and "carousel"
#: in, which no spend threshold could have done in that direction.
STYLE_REUSE = 4

#: A style is under-funded if it carries less than this share of the leader's
#: spend. Generous, because the proposal is "this has never had a fair run",
#: and the cap below is what keeps the list readable rather than the ratio.
GAP_SHARE = 0.25

#: Ideas per source. A list of twenty-four is not a list of ideas, it is a
#: backlog nobody opens -- and every one of these costs money to run.
PER_SOURCE = 4

#: Days of names the vocabulary is read over, regardless of the report window.
VOCAB_DAYS = 90

THRESHOLD_NOTE = (
    "Both thresholds are yours and are deliberately absent. Registering what "
    "would convince you AFTER reading the window is not registering it."
)

HOW_TO_FILE = "python -m intel record --kind experiment --json <path>"


def _normalise(segment: str) -> str:
    """One spelling per style.

    Ad names are typed by hand, so the same style arrives as "Blue
    Background", "blue  background" (two spaces) and "Blue Background - Copy".
    Left alone those are three styles, each with a third of the evidence, and
    all three look under-funded.
    """
    s = re.sub(r"\s+", " ", (segment or "").strip().lower())
    return re.sub(r"\s*-\s*copy$", "", s)


def _proposal(source: str, title: str, question: str, hypothesis: str,
              why: str, arms: list[dict], metric: str, *,
              evidence: list[dict] | None = None,
              prior: list[dict] | None = None) -> dict:
    blocked = [a["blocked_on"] for a in arms if a.get("blocked_on")]
    return {
        "source": source,
        "title": title,
        "question": question,
        "hypothesis": hypothesis,
        "why": why,
        "evidence": evidence or [],
        "arms": arms,
        "would_register": {
            "primary_metric": metric,
            "minimum_effect_pct": None,
            "minimum_spend_per_arm": None,
            "note": THRESHOLD_NOTE,
        },
        "fileable": not blocked,
        "blocked_on": blocked[0] if blocked else None,
        "already_tested": bool(prior),
        "prior_experiments": prior or [],
        "filed": False,
        "how_to_file": HOW_TO_FILE,
    }


# ---------------------------------------------------------------- generators

def _from_fatigue(fat: dict, prior_by_ad: dict) -> list[dict]:
    """A tiring ad is a question: does a new execution recover it?

    Not fileable, and that is honest rather than a defect. Arm B is a creative
    that does not exist yet, so there is no ad_key to name. File it the day the
    replacement goes live -- and note that registering the threshold THEN is
    still before that arm has any numbers, so the mechanism survives.
    """
    out = []
    # Ranked by recent spend, capped: the money decides which tiring ad is
    # worth a person's week, not the symptom count. A 3/5 on $60 is arithmetic.
    rows = sorted((r for r in fat.get("rows") or []
                   if r.get("confident")
                   and (r.get("score") or 0) >= FATIGUE_FLOOR),
                  key=lambda r: float(r.get("spend_recent") or 0), reverse=True)
    for r in rows[:PER_SOURCE]:
        symptoms = [s for s, _ in SYMPTOM_METRIC if r.get(s)]
        metric = next((m for s, m in SYMPTOM_METRIC if r.get(s)), "cpa")
        name = r.get("entity_name") or r.get("ad_key")
        out.append(_proposal(
            "fatigue",
            f"Refresh: {name}",
            f"Does a new execution of this idea recover {metric}, or is the "
            f"idea itself spent?",
            f"A replacement creative on the same audience will beat the "
            f"current one on {metric}. If it does not, the problem is the "
            f"angle rather than the execution, and the next test is a "
            f"different angle.",
            f"{r.get('score')}/5 symptoms on {r.get('spend_recent')} of recent "
            f"spend: {', '.join(symptoms)}. Measured over "
            f"{r.get('days_observed')} day(s) to {r.get('recent_until')}.",
            [{"label": "current", "ad_keys": [r.get("ad_key")]},
             {"label": "replacement",
              "blocked_on": "the replacement creative, which does not exist "
                            "yet -- file this once it is live"}],
            metric,
            evidence=[{"pointer": f"ads.fatigue.score[{r.get('ad_key')}]",
                       "value": r.get("score")},
                      {"pointer": f"ads.fatigue.spend_recent[{r.get('ad_key')}]",
                       "value": r.get("spend_recent")}],
            prior=prior_by_ad.get(r.get("ad_key")),
        ))
    return out


def _vocabulary(rows: list[dict]) -> dict[str, dict]:
    """Which name segments are STYLES, and which are just words.

    A style is a segment reused across many different leading segments. That
    is not a tuning knob, it is what a style IS: something you apply to
    different messages. On renegade it separates cleanly --

        static 52 angles   ugc 40   video 31   blue background 22
        white background 12   gif 8   b-roll 5   tweet 5   carousel 4

    -- from the names and copy fragments that also sit between pipes: doug,
    man, women, "if you own a p&c agency", each appearing beside one or two
    leading segments. A spend floor would not have separated those; "doug"
    had real budget and "carousel" had very little, which is the whole point
    of the proposal below.
    """
    seen: dict[str, dict] = {}
    for r in rows:
        name = r.get("entity_name") or ""
        if "|" not in name:
            continue
        parts = [_normalise(p) for p in _SEGMENT.split(name)]
        head = parts[0]
        for seg in set(parts[1:]):
            if not seg or seg in _NOT_A_STYLE:
                continue
            v = seen.setdefault(seg, {"heads": set(), "ads": 0, "spend": 0.0})
            v["heads"].add(head)
            v["ads"] += 1
            v["spend"] += float(r.get("spend") or 0)
    return {k: v for k, v in seen.items() if len(v["heads"]) >= STYLE_REUSE}


def _from_format_gaps(vocab: dict[str, dict],
                      limit: int) -> tuple[list[dict], list[dict]]:
    """-> (proposals, observations) from the vocabulary in the ad names.

    The proposal is about a style the account has barely funded. The
    observation is about the ones it has funded heavily, which is a
    description of what already ran and therefore not a test.
    """
    if not vocab:
        return [], []
    ranked = sorted(vocab.items(), key=lambda kv: kv[1]["spend"], reverse=True)
    total = sum(v["spend"] for _, v in ranked) or 1.0
    leader, lead = ranked[0]

    # Under-funded relative to the leader, ranked by spend DESCENDING: the
    # best candidate is the style with the most evidence it works and the
    # least budget behind it, not the one nobody has touched at all.
    gaps = [(s, v) for s, v in ranked[1:]
            if v["spend"] < lead["spend"] * GAP_SHARE][:limit]

    proposals = [_proposal(
        "format_gap",
        f"Under-funded: {style} against {leader}",
        f"Does {style} beat {leader} on cost per conversion when it is given "
        f"comparable budget?",
        f"{style} has never had enough spend behind it to be read. Given "
        f"comparable budget it performs at least as well as {leader}.",
        f"{style} runs on {v['ads']} ad(s) across {len(v['heads'])} different "
        f"messages for {v['spend']:.2f} of spend, against {leader} on "
        f"{lead['ads']} ad(s) for {lead['spend']:.2f}. Vocabulary read from "
        f"the ' | ' segments in ad names.",
        [{"label": leader, "blocked_on":
          "ad_keys for the control arm, once you pick which ads are in it"},
         {"label": style, "blocked_on":
          f"new {style} ads, at budget comparable to {leader}"}],
        "cpa",
        evidence=[{"pointer": f"ad_name_segment[{style}].spend",
                   "value": round(v["spend"], 2)},
                  {"pointer": f"ad_name_segment[{leader}].spend",
                   "value": round(lead["spend"], 2)}],
    ) for style, v in gaps]

    observations = [{
        "source": "format_split",
        "says": (f"{leader} carries {lead['spend']:.2f} of {total:.2f} spend "
                 f"across {lead['ads']} ad(s) -- "
                 f"{100 * lead['spend'] / total:.0f}% of the styles this "
                 f"reads. Next: "
                 + ", ".join(f"{s} ({v['spend']:.0f})"
                             for s, v in ranked[1:5]) + "."),
        "evidence": [{"pointer": f"ad_name_segment[{s}].spend",
                      "value": round(v["spend"], 2)} for s, v in ranked[:5]],
        "not_an_experiment":
            "This describes what already ran. Its metric and its thresholds "
            "would be chosen with the numbers already in view, so it cannot "
            "be pre-registered. Read it as where to look, not as a result.",
    }]
    return proposals, observations


def _from_angle_gaps(cov: dict, prior_by_angle: dict) -> list[dict]:
    """Angles in the bank that have never run, or never had enough spend.

    FILEABLE, unlike the other two, and the difference is worth knowing: an
    arm may name an `angle_id` that has no ads yet. You register the question,
    then make the ads. That is the mechanism working exactly as designed --
    which is also why this stays wired while ads.angle is empty and produces
    nothing.
    """
    out = []
    for gap in ("never_run", "under_spent"):
        for a in cov.get(gap) or []:
            slug = a.get("angle_slug")
            out.append(_proposal(
                "angle_gap",
                f"Untested angle: {a.get('angle_name')}",
                f"Does {a.get('angle_name')} produce cheaper conversions than "
                f"what is running now?",
                (a.get("definition")
                 or f"{a.get('angle_name')} is worth a test.")
                + " Stated as a hypothesis, not a finding.",
                f"In the bank, {gap.replace('_', ' ')}"
                + (f", family {a.get('family')}" if a.get("family") else "")
                + ".",
                [{"label": "control",
                  "blocked_on": "the angle_id of whatever you are testing "
                                "against -- `intel angles` lists the bank"},
                 {"label": a.get("angle_name"), "angle_id": a.get("angle_id")}],
                "cpa",
                evidence=[{"pointer": f"ads.angle_coverage.state[{slug}]",
                           "value": gap}],
                prior=prior_by_angle.get(slug),
            ))
    return out


def _mix_effect(why: dict) -> list[dict]:
    """Budget that moved toward a worse performer.

    An observation and never a proposal. "Hold the split or shift it" is a
    budget policy, and 005's arms are groups of ads -- there is no honest way
    to express a policy as an arm, so it is not pretended.
    """
    out = []
    for r in (why.get("rows") or [])[:5]:
        mix = r.get("mix_effect")
        if mix is None or float(mix) <= 0:
            continue
        out.append({
            "source": "mix_effect",
            "says": (f"{r.get('entity_name')} added {mix} to brand CPA "
                     f"through MIX -- money moving toward it -- rather than "
                     f"through its own rate changing "
                     f"({r.get('rate_effect')} from rate)."),
            "evidence": [
                {"pointer": f"ads.cpa_bridge.mix_effect[{r.get('entity_key')}]",
                 "value": mix},
                {"pointer": f"ads.cpa_bridge.rate_effect[{r.get('entity_key')}]",
                 "value": r.get("rate_effect")}],
            "not_an_experiment":
                "A budget split is not a group of ads, and 005's arms are "
                "groups of ads. There is no honest way to register this as a "
                "test, so it is reported as a finding and left there.",
        })
    return out


# --------------------------------------------------------------------- verb

async def propose(slug: str, days: int = 14, until: date | None = None,
                  min_spend: float = 200.0) -> dict:
    """Experiment ideas and observations. Files nothing, concludes nothing."""
    f = await _frame(slug, days, until)

    prior_rows = await fetch_all(
        """
        select e.name, e.question, e.conclusion, e.concluded_at,
               arm.angle_id, arm.ad_keys
          from ads.experiment e
          join ads.experiment_arm arm on arm.experiment_id = e.id
         where e.brand_id = %s
        """,
        (f["brand_id"],),
    )
    prior_by_angle: dict = {}
    prior_by_ad: dict = {}
    for r in prior_rows:
        if r.get("angle_id"):
            prior_by_angle.setdefault(str(r["angle_id"]), []).append(r)
        for k in (r.get("ad_keys") or []):
            prior_by_ad.setdefault(str(k), []).append(r)

    fat = await metrics.fatigue(slug, days, until, min_spend, False)
    why = await metrics.why(slug, days, until, limit=12)
    cov = await angles_mod.coverage(slug, None, max(days, 90), until)

    # The vocabulary is read over a WIDER window than the report. How a style
    # is spelled, and how widely it is reused, is a property of the account
    # rather than of the fortnight being reported -- and a fortnight is not
    # enough names for the reuse test to separate a style from a person's
    # name, which is the one thing that filter exists to do.
    vocab = _vocabulary(await fetch_all(
        "select entity_name, spend from ads.window_metrics(%s, %s, %s, 'ad')",
        (f["brand_id"], f["until"] - timedelta(days=VOCAB_DAYS), f["until"]),
    ))

    fmt_props, fmt_obs = _from_format_gaps(vocab, PER_SOURCE)
    proposals = (_from_fatigue(fat, prior_by_ad)
                 + fmt_props
                 + _from_angle_gaps(cov, prior_by_angle)[:PER_SOURCE])

    return {
        "verb": "propose", **f,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "observations": fmt_obs + _mix_effect(why),
        "note": (
            "Proposals are questions about things that have not run; their "
            "thresholds are blank because deciding what would convince you "
            "after reading the window is not deciding it in advance. "
            "Observations describe what already ran and cannot be "
            "pre-registered -- they are where to look, not results. Nothing "
            "here is filed and nothing here concludes."),
    }
