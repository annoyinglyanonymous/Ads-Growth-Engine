"""Every sentence the brief is able to say, as one ordered list.

THE RULE THIS FILE EXISTS TO ENFORCE

A reading is an opinion. It may only be built out of numbers that are already
in the fact pack, and it carries a pointer to each one it used. So the brief's
prose cannot contain a figure the database did not produce -- not because
whoever writes it is careful, but because there is no way to put one there.

`_cite` is the whole mechanism: it takes a JSON pointer into `facts` and
returns the value it found. A rule never types a number. It points at one, gets
it back, and interpolates what it got. A rule that wants to say something the
facts do not support has nothing to interpolate.

tests/test_readings.py walks every emitted reading and asserts each numeric
literal in `says` resolves verbatim at one of its pointers. That is the
strongest mechanical form available of CLAUDE.md's first rule -- "You never
compute a rate, a delta or a share yourself" -- and it survives somebody adding
a rule in a hurry on a Friday.

WHY A REGISTRY AND NOT A PROMPT

shapes.SHAPES holds the set of things an agent may file, for a stated reason:
"a governance question should be answerable by reading one list." The set of
things the system may ASSERT is the same kind of question. Reading this file
tells you everything the brief can claim, which no amount of reading a
generative prompt would.

There is no free-text path in v1. A narrated layer on top is possible later, in
a separately labelled panel, and it may not introduce a number.

PHRASING DISCIPLINE

A reading states what fired, never what it means.

    "shows 4 of 5 fatigue symptoms"        not  "is fatigued"
    "took 41% of spend and returned 12%"   not  "is underperforming"
    "has the lowest CPA within OFFSITE_CONVERSIONS"  not  "is the winner"

The difference is not politeness. The first is a fact about a threshold the
code applied; the second is a verdict, and CLAUDE.md is explicit that
performance is evidence about what happened rather than a verdict on what to
do. Naming a winner is hers.
"""

from __future__ import annotations

from typing import Any, Callable

#: A rule that fires produces one of these. `cites` is what makes it checkable.
Reading = dict[str, Any]


def _at(facts: dict, pointer: str) -> Any:
    """Resolve a JSON pointer into the fact pack, or None if it is not there.

    Tolerant by design. A rule guarded on a section that a degraded brief
    suppressed should quietly not fire, not raise -- the suppression is already
    reported in `degraded`, and a traceback on top of it helps nobody.
    """
    node: Any = facts
    for part in pointer.strip("/").split("/"):
        if node is None:
            return None
        if isinstance(node, list):
            try:
                node = node[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        else:
            return None
    return node


def _usd(v: Any) -> str:
    """A cited dollar figure, written the way every tile on the page writes it.

    The rules used to interpolate the raw value -- "Brand CPA moved -6.3285",
    "spent 60802.76 of 60802.76" -- which is a database talking, and it sat
    one line above a tile reading $36.30. charts.money is the one formatter
    the page uses, so the sentence and the tile cannot disagree about a cent.
    Formatting is not computing: the figure is still the cited one, and
    tests/test_brief_and_readings.py checks exactly that.
    """
    from charts import money
    return money(v)


def _moved(v: Any) -> str:
    """"rose $5.08" / "fell $6.33". The sign picks the verb; nothing is derived."""
    return ("rose " if v > 0 else "fell ") + _usd(abs(v))


def _cite(facts: dict, pointer: str, verb: str, field: str) -> dict:
    """One citation: where the number came from, and what was there."""
    return {"pointer": pointer, "verb": verb, "field": field,
            "value": _at(facts, pointer)}


def _reading(rule: str, says: str, cites: list[dict], *,
             subject: dict | None = None, provisional: bool = False,
             section: str = "") -> Reading:
    return {"rule": rule, "says": says, "cites": cites, "subject": subject,
            "provisional": provisional, "section": section,
            "author": f"rule:{rule}"}


# ---------------------------------------------------------------------------
# Trust. These fire first and they can invalidate everything below them.
# ---------------------------------------------------------------------------

def _stale_import(f: dict) -> list[Reading]:
    healthy = _at(f, "/facts/trust/healthy")
    problems = _at(f, "/facts/trust/problems") or []
    if healthy is not False:
        return []
    return [_reading(
        "stale_import",
        f"intel status reports this brand is not healthy, with "
        f"{len(problems)} problem(s) named. Every number below is measured "
        f"against whatever did import, and a stale import looks exactly like a "
        f"quiet week.",
        [_cite(f, "/facts/trust/healthy", "status", "healthy"),
         _cite(f, "/facts/trust/problems", "status", "problems")],
        section="trust")]


def _no_conversion_definition(f: dict) -> list[Reading]:
    n = _at(f, "/facts/trust/conversion_definitions")
    if n is None or n > 0:
        return []
    return [_reading(
        "no_conversion_definition",
        "No conversion is defined for this brand, so every conversion count "
        "and every CPA in this brief is zero or null. That is a configuration "
        "state, not a performance finding.",
        [_cite(f, "/facts/trust/conversion_definitions", "status",
               "conversion_definitions")],
        section="trust")]


def _match_rate_low(f: dict) -> list[Reading]:
    rate = _at(f, "/facts/trust/match/match_rate")
    unmatched = _at(f, "/facts/trust/match/unmatched") or []
    with_url = _at(f, "/facts/trust/match/with_tracked_url")
    if rate is None or not with_url or rate >= 0.9:
        return []
    return [_reading(
        "match_rate_low",
        f"{len(unmatched)} approved asset(s) carry a tracked_url that no "
        f"imported ad matches, a match rate of {rate}. A link that was retyped "
        f"or shortened on the way into Ads Manager is indistinguishable from an "
        f"ad written outside this system, so these are worth reading before "
        f"anything that depends on an angle.",
        [_cite(f, "/facts/trust/match/match_rate", "match_health", "match_rate"),
         _cite(f, "/facts/trust/match/unmatched", "match_health", "unmatched")],
        section="trust")]


def _mixed_currency(f: dict) -> list[Reading]:
    if not _at(f, "/facts/spend/mixed_currency"):
        return []
    return [_reading(
        "mixed_currency",
        "This brand's accounts do not all report in one currency, so no money "
        "total in this brief is a number. Filter to one account before reading "
        "any of them.",
        [_cite(f, "/facts/spend/currencies", "window_metrics", "currencies")],
        section="spend")]


# ---------------------------------------------------------------------------
# The bank. This is the one that matters most today.
# ---------------------------------------------------------------------------

def _bank_is_inert(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/angles/rows") or []
    unsigned = _at(f, "/waiting_on_you/angles_unsigned") or []
    if rows or not unsigned:
        return []
    return [_reading(
        "bank_is_inert",
        f"The angle bank holds {len(unsigned)} proposed angle(s) and no signed "
        f"ones. ads.angle_coverage filters on status='active', so every angle "
        f"number in this brief is structurally empty until a person signs one "
        f"-- this is not a finding about the ads.",
        [_cite(f, "/waiting_on_you/angles_unsigned", "angles",
               "awaiting_approval")],
        section="angles")]


def _untagged_spend(f: dict) -> list[Reading]:
    spend = _at(f, "/facts/untagged/untagged_spend")
    total = _at(f, "/facts/untagged/total_spend")
    ads_n = _at(f, "/facts/untagged/untagged_ads")
    inheritable = _at(f, "/facts/untagged/inheritable_ads")
    if not spend or not total:
        return []
    return [_reading(
        "untagged_spend",
        f"{ads_n} ad(s) carrying no angle spent {_usd(spend)} of {_usd(total)} in this "
        f"window, and {inheritable} of them can be tagged with no judgement at "
        f"all by following the campaign_asset they were approved from. Every "
        f"angle share below is a share of what is left.",
        [_cite(f, "/facts/untagged/untagged_spend", "untagged_spend",
               "untagged_spend"),
         _cite(f, "/facts/untagged/total_spend", "untagged_spend", "total_spend"),
         _cite(f, "/facts/untagged/untagged_ads", "untagged_spend", "untagged_ads"),
         _cite(f, "/facts/untagged/inheritable_ads", "untagged_spend",
               "inheritable_ads")],
        section="angles")]


def _stale_tags(f: dict) -> list[Reading]:
    n = _at(f, "/facts/untagged/stale_tag_ads")
    spend = _at(f, "/facts/untagged/stale_tag_spend")
    if not n:
        return []
    return [_reading(
        "stale_tags",
        f"{n} ad(s) carry only a tag describing wording they no longer run, so "
        f"{_usd(spend)} is absent from the angle numbers entirely. ads.ad_copy holds "
        f"one wording per ad -- the current one -- so what the copy said at the "
        f"time that spend happened is not recoverable.",
        [_cite(f, "/facts/untagged/stale_tag_ads", "untagged_spend",
               "stale_tag_ads"),
         _cite(f, "/facts/untagged/stale_tag_spend", "untagged_spend",
               "stale_tag_spend")],
        section="angles")]


# ---------------------------------------------------------------------------
# Movement.
# ---------------------------------------------------------------------------

def _structure_changed(f: dict) -> list[Reading]:
    available = _at(f, "/facts/movement/structure_changes/available")
    rows = _at(f, "/facts/movement/structure_changes/rows") or []
    if available is False:
        return [_reading(
            "structure_change_unknown",
            "Whether anything changed in the account is unknown for this "
            "window, not known to be nothing -- the change log is not "
            "available. Read any movement below with that missing.",
            [_cite(f, "/facts/movement/structure_changes/available", "why",
                   "structure_changes.available")],
            section="movement")]
    if not rows:
        return []
    return [_reading(
        "structure_change_first",
        f"{len(rows)} budget, status or optimisation-goal edit(s) landed in "
        f"this window. Most of the time the answer to a CPA move is an edit "
        f"rather than worn-out creative, so these come before the fatigue "
        f"section.",
        [_cite(f, "/facts/movement/structure_changes/rows", "why",
               "structure_changes.rows")],
        section="movement")]


def _cpa_moved(f: dict) -> list[Reading]:
    change = _at(f, "/facts/movement/cpa_change")
    rate = _at(f, "/facts/movement/rate_effect")
    mix = _at(f, "/facts/movement/mix_effect")
    if change is None or not change:
        return []
    out = [_reading(
        "cpa_moved",
        f"Brand CPA {_moved(change)} across this window.",
        [_cite(f, "/facts/movement/cpa_change", "cpa_bridge_totals",
               "cpa_change")],
        provisional=bool(_at(f, "/provisional")),
        section="movement")]
    if rate is not None and mix is not None:
        dominant = "rate_dominates" if abs(rate) >= abs(mix) else "mix_dominates"
        which = ("creative getting more or less expensive"
                 if dominant == "rate_dominates"
                 else "budget moving between ads")
        out.append(_reading(
            dominant,
            f"Of that, {_usd(rate)} is rate effect and {_usd(mix)} is mix effect, so the "
            f"larger share is {which}.",
            [_cite(f, "/facts/movement/rate_effect", "cpa_bridge_totals",
                   "rate_effect"),
             _cite(f, "/facts/movement/mix_effect", "cpa_bridge_totals",
                   "mix_effect")],
            section="movement"))
    return out


def _split_does_not_cover(f: dict) -> list[Reading]:
    share = _at(f, "/facts/movement/attributable_share")
    unattr = _at(f, "/facts/movement/unattributable_ads")
    if share is None or share >= 0.8:
        return []
    return [_reading(
        "split_does_not_cover",
        f"The rate/mix split accounts for {share} of the move, with {unattr} "
        f"ad(s) unattributable because they converted in only one of the two "
        f"windows. The decomposition is partial and should be read as such.",
        [_cite(f, "/facts/movement/attributable_share", "cpa_bridge_totals",
               "attributable_share"),
         _cite(f, "/facts/movement/unattributable_ads", "cpa_bridge_totals",
               "unattributable_ads")],
        section="movement")]


# ---------------------------------------------------------------------------
# Creative.
# ---------------------------------------------------------------------------

_SYMPTOMS = ("link_ctr_decline", "cpm_rise", "cpa_rise", "frequency_rise",
             "ranking_drop")


def _fatigue_confirmed(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/creative/rows") or []
    out: list[Reading] = []
    for i, r in enumerate(rows):
        score = r.get("score")
        if not score or score < 3:
            continue
        named = [s.replace("_", " ") for s in _SYMPTOMS if r.get(s)]
        out.append(_reading(
            "fatigue_confirmed",
            f"shows {score} of 5 fatigue symptoms: {', '.join(named)}.",
            [_cite(f, f"/facts/creative/rows/{i}/score", "fatigue", "score"),
             _cite(f, f"/facts/creative/rows/{i}/spend_recent", "fatigue",
                   "spend_recent")],
            subject={"kind": "ad", "key": r.get("ad_key"),
                     "name": r.get("entity_name")},
            provisional=bool(_at(f, "/provisional")),
            section="creative"))
    return out


def _fatigue_suppressed(f: dict) -> list[Reading]:
    n = _at(f, "/facts/creative/suppressed_unconfident")
    if not n:
        return []
    return [_reading(
        "fatigue_suppressed",
        f"{n} further ad(s) show symptoms on too little spend or too few "
        f"impressions to read, and are counted here rather than listed. A "
        f"symptom count on a handful of dollars is arithmetic, not a finding.",
        [_cite(f, "/facts/creative/suppressed_unconfident", "fatigue",
               "suppressed_unconfident")],
        section="creative")]


# ---------------------------------------------------------------------------
# Angles. Never ranked across optimization goals.
# ---------------------------------------------------------------------------

def _angle_leads_within_goal(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/angles/rows") or []
    out: list[Reading] = []
    for i, r in enumerate(rows):
        if r.get("rank_within_goal") != 1 or not r.get("spend_sufficient"):
            continue
        goals = r.get("optimization_goals") or []
        goal = (goals[0].get("goal") if goals and isinstance(goals[0], dict)
                else "its optimisation goal")
        out.append(_reading(
            "angle_leads_within_goal",
            f"has the lowest CPA among angles that ran under {goal}, on "
            f"{_usd(r.get('spend'))} of spend.",
            [_cite(f, f"/facts/angles/rows/{i}/rank_within_goal",
                   "angle_performance", "rank_within_goal"),
             _cite(f, f"/facts/angles/rows/{i}/spend", "angle_performance",
                   "spend")],
            subject={"kind": "angle", "key": r.get("angle_slug"),
                     "name": r.get("angle_name")},
            section="angles"))
    return out


def _angle_not_comparable(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/angles/rows") or []
    # One citation per angle counted, rather than one pointing at the whole
    # list. The count in the sentence is then the number of citations under it,
    # which a reader can check by looking -- where a single cite at the list
    # would have been evidence for a different number than the one stated.
    cites = [_cite(f, f"/facts/angles/rows/{i}/comparable_on_cost",
                   "angle_performance", "comparable_on_cost")
             for i, r in enumerate(rows)
             if r.get("comparable_on_cost") is False and r.get("spend")]
    if not cites:
        return []
    return [_reading(
        "angle_not_comparable",
        f"{len(cites)} angle(s) ran under more than one optimisation goal, so "
        f"their CPA cannot be ranked against anything. rank_within_goal is null "
        f"for them deliberately; the comparison is unavailable rather than "
        f"merely discouraged.",
        cites,
        section="angles")]


def _angle_overweight(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/angles/rows") or []
    out: list[Reading] = []
    for i, r in enumerate(rows):
        ss, cs = r.get("spend_share_pct"), r.get("conversion_share_pct")
        if ss is None or cs is None or not r.get("spend_sufficient"):
            continue
        if ss - cs < 15:
            continue
        out.append(_reading(
            "angle_overweight",
            f"took {ss}% of tagged spend and returned {cs}% of tagged "
            f"conversions.",
            [_cite(f, f"/facts/angles/rows/{i}/spend_share_pct",
                   "angle_performance", "spend_share_pct"),
             _cite(f, f"/facts/angles/rows/{i}/conversion_share_pct",
                   "angle_performance", "conversion_share_pct")],
            subject={"kind": "angle", "key": r.get("angle_slug"),
                     "name": r.get("angle_name")},
            section="angles"))
    return out


def _angle_never_run(f: dict) -> list[Reading]:
    never = _at(f, "/facts/angles/never_run") or []
    under = _at(f, "/facts/angles/under_spent") or []
    if not never and not under:
        return []
    return [_reading(
        "angle_never_run",
        f"{len(never)} signed angle(s) have never run and {len(under)} ran on "
        f"too little spend to have been tested. An angle under the spend floor "
        f"was glanced at, not tested.",
        [_cite(f, "/facts/angles/never_run", "coverage", "never_run"),
         _cite(f, "/facts/angles/under_spent", "coverage", "under_spent")],
        section="angles")]


# ---------------------------------------------------------------------------
# Experiments. Nothing here names a winner and nothing here is able to.
# ---------------------------------------------------------------------------

def _experiment_not_readable(f: dict) -> list[Reading]:
    rows = _at(f, "/facts/experiments/rows") or []
    out: list[Reading] = []
    for i, r in enumerate(rows):
        if r.get("state") != "running" or r.get("readable") is not False:
            continue
        out.append(_reading(
            "experiment_not_readable",
            "has at least one arm below the spend floor it registered, so any "
            "effect on it is arithmetic rather than a result. Say that before "
            "quoting a number from it.",
            [_cite(f, f"/facts/experiments/rows/{i}/readable", "experiment",
                   "readable")],
            subject={"kind": "experiment", "key": r.get("name"),
                     "name": r.get("name")},
            section="experiments"))
    return out


def _awaiting_conclusion(f: dict) -> list[Reading]:
    rows = _at(f, "/waiting_on_you/experiments_unconcluded") or []
    if not rows:
        return []
    return [_reading(
        "awaiting_conclusion",
        f"{len(rows)} experiment(s) have ended and carry no conclusion. A "
        f"conclusion is hers and has no agent path; until one is written the "
        f"test is not prior art and coverage will propose the question again.",
        [_cite(f, "/waiting_on_you/experiments_unconcluded", "experiments",
               "state")],
        section="experiments")]


# ---------------------------------------------------------------------------
# The registry. Ordered: trust first, because it can invalidate the rest.
# ---------------------------------------------------------------------------

RULES: tuple[tuple[str, Callable[[dict], list[Reading]]], ...] = (
    ("stale_import", _stale_import),
    ("no_conversion_definition", _no_conversion_definition),
    ("match_rate_low", _match_rate_low),
    ("mixed_currency", _mixed_currency),
    ("bank_is_inert", _bank_is_inert),
    ("structure_change", _structure_changed),
    ("cpa_moved", _cpa_moved),
    ("split_does_not_cover", _split_does_not_cover),
    ("fatigue_confirmed", _fatigue_confirmed),
    ("fatigue_suppressed", _fatigue_suppressed),
    ("angle_leads_within_goal", _angle_leads_within_goal),
    ("angle_not_comparable", _angle_not_comparable),
    ("angle_overweight", _angle_overweight),
    ("angle_never_run", _angle_never_run),
    ("untagged_spend", _untagged_spend),
    ("stale_tags", _stale_tags),
    ("experiment_not_readable", _experiment_not_readable),
    ("awaiting_conclusion", _awaiting_conclusion),
)


def read(facts: dict) -> list[Reading]:
    """Fire every rule against the fact pack, in registry order.

    A rule that raises is a bug in that rule, and it must not take the brief
    down with it -- a brief that renders without one reading is far better than
    a traceback where the brief was. The failure is recorded as a reading of its
    own so it is visible rather than silently absent.
    """
    out: list[Reading] = []
    for name, fn in RULES:
        try:
            out.extend(fn(facts) or [])
        except Exception as exc:  # pragma: no cover - defensive
            out.append(_reading(
                "rule_failed",
                f"The rule {name!r} raised {type(exc).__name__} and produced "
                f"nothing. That is a defect in the rule, not a finding about "
                f"the ads.",
                [], section="trust"))
    return out
