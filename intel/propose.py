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

#: Which movement each stage's `says` sentence has already described, so
#: `also_broke` does not repeat the headline finding underneath itself.
_LEADS = {
    "attention": "link ctr",
    "landing": "landing page",
    "after the click": "conversion rate",
    "saturation": "cpm",
    "unclear": "\0",          # matches nothing; every movement is news here
}

#: The five ads.fatigue flags paired with the metric each one implicates.
#:
#: NO LONGER USED BY THIS MODULE, and kept because intel/ad_readings.py
#: imports it as the canonical symptom-to-metric list. The plan for this pass
#: said to delete it; it stopped being dead between writing that and doing it.
#:
#: `propose` itself reads the RATES now (see _diagnose) rather than these
#: booleans. The flags say that something moved; the rates say which way and
#: by how much -- and an ad whose link CTR is UP does not want a creative test
#: however many flags fired. Two of the four cards on this page were
#: recommending exactly that before the change.
SYMPTOM_METRIC: tuple[tuple[str, str], ...] = (
    ("link_ctr_decline", "link_ctr"),
    ("cpa_rise", "cpa"),
    ("cpm_rise", "cpm"),
    ("frequency_rise", "cpm"),
    ("ranking_drop", "link_ctr"),
)

#: Which metric a refresh test registers, given which stage broke. Read from
#: the RATES rather than from ads.fatigue's boolean flags: the flags say that
#: something moved, the rates say which direction and by how much, and an ad
#: whose link CTR is UP does not want a creative test however many flags fired.
_STAGE_METRIC = {"attention": "link_ctr", "saturation": "cpm"}

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


def _rate(side: dict | None, name: str):
    return ((side or {}).get("rates") or {}).get(name)


def _move(prior, recent) -> float | None:
    """Percent change, or None when either side is undefined.

    None is not zero. An ad with no conversions last window has an UNDEFINED
    cpa, not a cpa of nothing, and treating the arrival of its first
    conversion as an infinite improvement is how a $12 ad reaches the top of
    a ranked list.
    """
    if prior in (None, 0) or recent is None:
        return None
    return 100.0 * (float(recent) - float(prior)) / float(prior)


#: What changed, in the order the funnel runs. The FIRST stage that broke is
#: the one worth acting on: a collapse in conversion rate downstream of a
#: healthy click rate is not a creative problem, and rewriting the ad would
#: change the one part that is still working.
def _diagnose(prior: dict, recent: dict) -> dict:
    ctr = _move(_rate(prior, "link_ctr"), _rate(recent, "link_ctr"))
    cvr = _move(_rate(prior, "conversion_rate"), _rate(recent, "conversion_rate"))
    lpv = _move(_rate(prior, "lp_view_rate"), _rate(recent, "lp_view_rate"))
    cpm = _move(_rate(prior, "cpm"), _rate(recent, "cpm"))

    # Every stage past its threshold, not just the winning one. An ad can be
    # broken in two places at once -- "Become an Agent | Start Franchise" was
    # link CTR -28% AND landing page views -91% -- and naming only the first
    # in funnel order hides the larger number. The primary still decides the
    # RESPONSE; this decides what the reader gets told.
    also = []
    if ctr is not None and ctr <= -10:
        also.append(f"link CTR {ctr:.0f}%")
    if lpv is not None and lpv <= -15:
        also.append(f"landing page views {lpv:.0f}%")
    if cvr is not None and cvr <= -15:
        also.append(f"conversion rate {cvr:.0f}%")
    if cpm is not None and cpm >= 15:
        also.append(f"CPM +{cpm:.0f}%")

    def _out(d: dict) -> dict:
        # The primary is already spelled out in `says`; the rest is the tail.
        d["also_broke"] = [a for a in also
                           if not a.lower().startswith(_LEADS[d["stage"]])]
        return d

    if ctr is not None and ctr <= -10:
        return _out({
            "stage": "attention",
            "creative_is_the_problem": True,
            "says": f"Link CTR fell {abs(ctr):.0f}%. Fewer people who see it "
                    f"are clicking, which is the creative's own job.",
            "change": "The opening seconds, the thumbnail, the headline -- "
                      "whatever earns the click. Keep the offer fixed so the "
                      "test answers one question.",
        })
    if lpv is not None and lpv <= -15:
        return _out({
            "stage": "landing",
            "creative_is_the_problem": False,
            "says": f"Clicks held up but landing page views fell {abs(lpv):.0f}%. "
                    f"People are clicking and not arriving.",
            "change": "The destination, not the ad. Page speed, a redirect, a "
                      "broken link, or mobile load time.",
        })
    if cvr is not None and cvr <= -15:
        return _out({
            "stage": "after the click",
            "creative_is_the_problem": False,
            "says": f"Link CTR held"
                    + (f" (up {ctr:.0f}%)" if ctr and ctr > 0 else "")
                    + f" while conversion rate fell {abs(cvr):.0f}%. The ad is "
                      f"still earning the click; what follows it is not "
                      f"converting.",
            "change": "The page, the form, or the offer. Rewriting the "
                      "creative would change the one part still working -- "
                      "and a creative test here would answer nothing.",
        })
    if cpm is not None and cpm >= 15:
        return _out({
            "stage": "saturation",
            "creative_is_the_problem": True,
            "says": f"CPM rose {cpm:.0f}% without the click rate falling. The "
                    f"auction is charging more to reach the same people.",
            "change": "Audience or rotation before copy. The same idea in "
                      "front of new people is the cheaper test.",
        })
    return _out({
        "stage": "unclear",
        "creative_is_the_problem": True,
        "says": "Cost rose without one stage clearly breaking.",
        "change": "A straight refresh is the cheapest way to find out which "
                  "half moved.",
    })


def _brief(name: str, dx: dict, moved: list[dict], copy: dict,
           untried: list[str], metric: str) -> dict:
    """A handoff to whoever writes the copy. NOT copy.

    THIS MODULE DOES NOT WRITE AD COPY AND MUST NOT START.

    Copy production sits behind growth-engine's claim gate, an approved
    `concept_id` that 013 makes NOT NULL, the brand voice guides, and a
    person's signature. For an insurance advertiser those gates are the
    product, not paperwork -- "carrier appointments stay active through the
    transfer" is a claim somebody has to stand behind.

    A headline invented here would carry none of that, and could not be filed
    through `engine record` anyway. All it would do is look shippable. So
    every field below is READ from somewhere: the copy out of ads.ad_copy, the
    styles out of the ad names, the numbers out of ads.fatigue.

    The key is `ad_copy` and not `copy` on purpose: Jinja resolves `p.copy`
    to dict.copy, the built-in method, which is truthy -- so an `{% if %}`
    guard passes and the macro renders nothing at all. Silent, and it cost a
    render to find.

    `campaign` stays the literal string "<campaign>". This repo does not know
    growth-engine's campaign names, and a brief naming the wrong one is worse
    than a brief naming none.
    """
    lead = next((m for m in moved if m["metric"].lower().startswith(
        _LEADS.get(dx["stage"], "\0"))), None)
    return {
        "angle": _normalise(_SEGMENT.split(name)[0]),
        "broke": (f"{lead['metric']} {lead['pct']:+.0f}% ({dx['stage']})"
                  if lead else dx["stage"]),
        "why": dx["says"],
        "change": dx["change"],
        "current_headline": copy.get("first_headline"),
        "current_body": copy.get("first_body"),
        "never_run_as": untried,
        "beat": {"metric": metric,
                 "current": (lead or {}).get("recent")},
        "campaign": "<campaign>",
        "written_where": (
            "cd ..\\growth-engine\n"
            'python -m engine context "<campaign>" --stage meta_ads'),
        "note": ("A brief, not copy. Nothing here writes ad text: the claim "
                 "gate, the approved concept and the signature all live in "
                 "growth-engine, and copy that skipped them could not be "
                 "filed even if it read well."),
    }


def _proposal(source: str, title: str, question: str, hypothesis: str,
              why: str, arms: list[dict], metric: str, *,
              evidence: list[dict] | None = None,
              prior: list[dict] | None = None,
              extra: dict | None = None) -> dict:
    blocked = [a["blocked_on"] for a in arms if a.get("blocked_on")]
    return {
        **(extra or {}),
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

def _from_fatigue(fat: dict, prior_by_ad: dict,
                  copy_by_ad: dict | None = None,
                  angle_styles: dict | None = None
                  ) -> tuple[list[dict], list[dict]]:
    """-> (refresh proposals, diagnoses).

    TWO LISTS, BECAUSE THEY ARE TWO DIFFERENT STATEMENTS. A refresh proposal
    is a test you could run. A diagnosis is a finding about an ad whose
    creative is fine -- it has no arms, no metric to register and no way to be
    filed, because there is nothing to test on the ad.

    Splitting them is not tidiness. The first version put both in one list,
    and it recommended rewriting two creatives whose click-through rate had
    gone UP -- one of them by 115%. A reader following that advice would have
    replaced the one part still working.

    A refresh is not fileable either, and that is honest rather than a defect:
    arm B is a creative that does not exist yet, so there is no ad_key to
    name. File it the day the replacement goes live -- registering the
    threshold THEN is still before that arm has any numbers, so the mechanism
    survives.
    """
    copy_by_ad = copy_by_ad or {}
    angle_styles = angle_styles or {}
    out: list[dict] = []
    found: list[dict] = []
    # Ranked by recent spend, capped: the money decides which tiring ad is
    # worth a person's week, not the symptom count. A 3/5 on $60 is arithmetic.
    rows = sorted((r for r in fat.get("rows") or []
                   if r.get("confident")
                   and (r.get("score") or 0) >= FATIGUE_FLOOR),
                  key=lambda r: float(r.get("spend_recent") or 0), reverse=True)
    for r in rows[:PER_SOURCE]:
        name = r.get("entity_name") or r.get("ad_key")
        dx = _diagnose(r.get("prior") or {}, r.get("recent") or {})
        # str() on both sides: ad_key is a UUID object here and the two
        # dicts are keyed by string, so a raw lookup silently misses and
        # every card renders with no copy and no prior art.
        key = str(r.get("ad_key"))
        copy = copy_by_ad.get(key) or {}
        tried, untried = angle_styles.get(_normalise(
            _SEGMENT.split(name)[0]), (set(), set()))

        # The movements, named, in the order the funnel runs. "2/5 symptoms"
        # tells a reader that something is wrong; this tells them WHAT.
        moved = []
        # `field`, not `key`: this loop used to bind `key` and leave it set to
        # "cpa", so the prior-art lookup below silently searched for an ad
        # named after a metric and every proposal came back already_tested
        # False. Shadowing a name across thirty lines is invisible in review.
        for label, field in (("link CTR", "link_ctr"),
                             ("conv rate", "conversion_rate"),
                             ("LP views", "lp_view_rate"), ("CPM", "cpm"),
                             ("CPA", "cpa")):
            pct = _move(_rate(r.get("prior"), field), _rate(r.get("recent"), field))
            if pct is None or abs(pct) < 5:
                continue
            moved.append({"metric": label,
                          "prior": _rate(r.get("prior"), field),
                          "recent": _rate(r.get("recent"), field),
                          "pct": round(pct, 1)})

        if not dx["creative_is_the_problem"]:
            # A finding, not a proposal. It gets no arms and no metric on
            # purpose: there is nothing on the AD to test, and giving it the
            # shape of an experiment would invite somebody to file one.
            found.append({
                "source": "fatigue",
                "title": name,
                "ad_key": r.get("ad_key"),
                "diagnosis": dx,
                "moved": moved,
                "ad_copy": copy,
                "spend_recent": r.get("spend_recent"),
                "window": f"{r.get('days_observed')} day(s) to "
                          f"{r.get('recent_until')}",
                "not_an_experiment": (
                    "The creative is still doing its job. Rewriting it would "
                    "change the one part that is working, and a creative test "
                    "here would answer nothing."),
            })
            continue

        metric = _STAGE_METRIC.get(dx["stage"], "cpa")
        out.append(_proposal(
            "fatigue",
            f"Refresh: {name}",
            f"Does a new execution recover {metric}, or is the idea spent?",
            f"A replacement on the same audience beats the current one on "
            f"{metric}. If it does not, the problem is the angle rather than "
            f"the execution, and the next test is a different angle.",
            dx["says"] + " " + dx["change"]
            + f" {r.get('spend_recent')} of spend over "
              f"{r.get('days_observed')} day(s) to {r.get('recent_until')}.",
            [{"label": "current", "ad_keys": [r.get("ad_key")]},
             {"label": "replacement",
              "blocked_on": "the replacement creative, which does not exist "
                            "yet -- file this once it is live"}],
            metric,
            evidence=[{"pointer": f"ads.fatigue.score[{r.get('ad_key')}]",
                       "value": r.get("score")}],
            prior=prior_by_ad.get(key),
            extra={"diagnosis": dx, "moved": moved, "ad_copy": copy,
                   "styles_tried": sorted(tried),
                   "styles_untried": sorted(untried),
                   "brief": _brief(name, dx, moved, copy,
                                   sorted(untried), metric)},
        ))
    return out, found


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


def _angle_styles(rows: list[dict],
                  vocab: dict[str, dict]) -> dict[str, tuple[set, set]]:
    """-> {angle: (styles it has run as, styles it has not)}.

    This is the half that makes a refresh proposal a brief rather than a
    complaint. "Rewrite it" is not a direction; "this angle has run as video
    and UGC and has never run as static or carousel" is one, and it comes out
    of names the account already wrote.
    """
    known = set(vocab)
    tried: dict[str, set] = {}
    for r in rows:
        name = r.get("entity_name") or ""
        if "|" not in name:
            continue
        parts = [_normalise(x) for x in _SEGMENT.split(name)]
        tried.setdefault(parts[0], set()).update(
            x for x in parts[1:] if x in known)
    return {a: (t, known - t) for a, t in tried.items()}


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
    vocab_rows = await fetch_all(
        "select entity_name, spend from ads.window_metrics(%s, %s, %s, 'ad')",
        (f["brand_id"], f["until"] - timedelta(days=VOCAB_DAYS), f["until"]),
    )
    vocab = _vocabulary(vocab_rows)
    angle_styles = _angle_styles(vocab_rows, vocab)

    # The copy each tiring ad is currently running, so a card can show what
    # would be rewritten rather than only its name. Read through ads.ad_copy,
    # which intel/angles.py::queue already joins for the same reason.
    keys = [r.get("ad_key") for r in (fat.get("rows") or []) if r.get("ad_key")]
    copy_by_ad: dict = {}
    if keys:
        for row in await fetch_all(
            "select ad_key, first_headline, first_body "
            "  from ads.ad_copy where ad_key = any(%s)",
            (keys,),
        ):
            copy_by_ad[str(row["ad_key"])] = row

    fmt_props, fmt_obs = _from_format_gaps(vocab, PER_SOURCE)
    refreshes, diagnoses = _from_fatigue(fat, prior_by_ad, copy_by_ad,
                                         angle_styles)
    proposals = (refreshes
                 + fmt_props
                 + _from_angle_gaps(cov, prior_by_angle)[:PER_SOURCE])

    return {
        "verb": "propose", **f,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "diagnosis_count": len(diagnoses),
        "diagnoses": diagnoses,
        "observations": fmt_obs + _mix_effect(why),
        "note": (
            "Three kinds of statement, kept apart on purpose. PROPOSALS are "
            "questions about things that have not run; their thresholds are "
            "blank because deciding what would convince you after reading the "
            "window is not deciding it in advance. DIAGNOSES are ads whose "
            "creative is still working and whose problem is downstream of the "
            "click -- there is nothing on the ad to test, so they carry no "
            "arms and no metric. OBSERVATIONS describe what already ran and "
            "cannot be pre-registered at all. Nothing here is filed, nothing "
            "here concludes, and nothing here writes ad copy."),
    }
