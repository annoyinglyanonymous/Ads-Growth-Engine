"""One ad's panel says nothing it cannot cite, and decides nothing.

The same two guarantees tests/test_brief_and_readings.py makes about the
brief, made again at ad scope, because intel/ad_readings.py is a second
registry and a guarantee that covers only the first one is not a guarantee.

None of these need a database: every rule is a pure function of a fact pack.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from intel import ad_readings

MODULE = Path(ad_readings.__file__)

#: Signed, so a negative cited value and the literal in the sentence are the
#: same string. Used for BOTH sides of the comparison -- scanning a sentence
#: and scanning a cited value -- because two different patterns would disagree
#: about what a number is.
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def _facts(**over) -> dict:
    """A fact pack shaped exactly as ad_facts builds one.

    The angle name carries digits on purpose -- "20 Years Experience" is a real
    renegade angle -- because a rule that interpolates a name is the easiest
    place to smuggle an uncited figure into a sentence.
    """
    f = {
        "verb": "ad_readings",
        "brand": "renegade",
        "ad_key": "11111111-1111-1111-1111-111111111111",
        "days": 14,
        "settled_through": "2026-09-18",
        "facts": {
            "ad": {
                "name": "20 Years Experience | UGC | Video",
                "effective_status": "ACTIVE",
                "optimization_goal": "OFFSITE_CONVERSIONS",
                "objective": "OUTCOME_LEADS",
                "ad_group_name": "Prospecting",
                "campaign_name": "Agents",
                "is_tagged": True,
                "tag_is_stale": False,
                "angle_slug": "twenty-years",
                "angle_name": "20 Years Experience",
                "tag_source": "reviewer",
                "day_count": 28,
            },
            "fatigue": {
                "score": 2,
                "confident": True,
                "spend_recent": 1436.05,
                "days_observed": 14,
                "symptoms": ["cpa_rise", "cpm_rise"],
                "would_register": "cpa",
                "meets_floor": True,
            },
            "bridge": {
                "total_effect": 3.41,
                "rate_effect": 2.10,
                # NEGATIVE on purpose. A real bridge row for "Become an Agent |
                # Static | White Background" reads rate 0.7975, mix -0.3688: an
                # ad whose own CPA worsened while it took a smaller share of
                # the spend. With an unsigned pattern the minus is dropped,
                # "0.3688" is compared against a cited "-0.3688", and the test
                # fails a sentence that is perfectly sourced.
                "mix_effect": -1.31,
                "reason": "attributable",
                "spend_current": 1436.05,
                "spend_prior": 1201.90,
            },
        },
    }
    for section, patch in over.items():
        if patch is None:
            f["facts"][section] = None
        else:
            f["facts"][section].update(patch)
    return f


# ---------------------------------------------------------------------------
# Sourcing.
# ---------------------------------------------------------------------------

#: Numbers that describe the code rather than the data. Same list as the
#: brief's, and kept as small for the same reason: every entry is a hole.
STRUCTURAL = {
    "5",    # ads.fatigue returns exactly five named symptoms
}


def _allowed(r: dict) -> set[str]:
    allowed = set(STRUCTURAL)
    allowed.add(str(len(r["cites"])))
    for c in r["cites"]:
        v = c["value"]
        allowed.add(str(v))
        if isinstance(v, (list, dict)):
            allowed.add(str(len(v)))
        if isinstance(v, float):
            allowed.add(str(v).rstrip("0").rstrip("."))
            allowed.add(str(int(v)) if v == int(v) else str(v))
        # A figure INSIDE a cited string is sourced: it came out of the
        # database verbatim, in the value the citation points at. The brief's
        # version of this test has no such case only because its fixture has no
        # entity name with a digit in it -- "20 Years Experience" is a real
        # angle here, and a rule naming it is not stating a number.
        if isinstance(v, str):
            allowed.update(NUMBER.findall(v))
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    allowed.update(NUMBER.findall(item))
    return allowed


def test_there_are_rules_to_check():
    """Guards against the file passing because a rename emptied the registry."""
    assert len(ad_readings.AD_RULES) >= 8


def test_every_number_in_a_reading_is_sourced():
    for r in ad_readings.read_ad(_facts()):
        allowed = _allowed(r)
        for literal in NUMBER.findall(r["says"]):
            assert literal in allowed, (
                f"{r['rule']} states {literal!r} which is not a cited value, "
                f"the length of one, a figure inside a cited value, or "
                f"structural. says={r['says']!r} cited={sorted(allowed)}")


def test_a_numeric_claim_always_carries_a_citation():
    for r in ad_readings.read_ad(_facts()):
        if re.search(r"\d", r["says"]) and not r["cites"]:
            pytest.fail(f"{r['rule']} states a figure and cites nothing: "
                        f"{r['says']!r}")


def test_every_citation_resolves():
    for r in ad_readings.read_ad(_facts()):
        for c in r["cites"]:
            assert c["value"] is not None, (
                f"{r['rule']} cites {c['pointer']}, which resolves to nothing")


def test_no_rule_renders_a_null():
    """A missing section must silence a rule, not produce a sentence about
    None. ads.fatigue returns no row at all for an ad that ran in only one of
    the two windows, and the bridge drops an ad that never converted."""
    for pack in (_facts(fatigue=None), _facts(bridge=None),
                 _facts(fatigue=None, bridge=None)):
        for r in ad_readings.read_ad(pack):
            assert "None" not in r["says"], (
                f"{r['rule']} rendered a null: {r['says']!r}")


def test_a_rule_that_raises_does_not_take_the_panel_down():
    broken = {"facts": "not a dict at all"}
    out = ad_readings.read_ad(broken)
    assert all(isinstance(r["says"], str) and r["says"] for r in out)


# ---------------------------------------------------------------------------
# It proposes; it does not conclude.
# ---------------------------------------------------------------------------

#: Words that turn evidence into a verdict. CLAUDE.md: "Performance is evidence
#: about what happened, not a verdict on what to do."
VERDICTS = ("should be paused", "should pause", "turn this off", "kill this",
            "this ad is bad", "underperform", "winner", "loser", "beat the",
            "we recommend", "you should", "best performing", "worst")


def test_no_reading_delivers_a_verdict():
    packs = [_facts(),
             _facts(fatigue={"confident": False, "meets_floor": False,
                             "score": 1, "symptoms": ["cpa_rise"]}),
             _facts(ad={"effective_status": "PAUSED", "is_tagged": False,
                        "angle_name": None}),
             _facts(ad={"tag_is_stale": True})]
    for pack in packs:
        for r in ad_readings.read_ad(pack):
            low = r["says"].lower()
            for word in VERDICTS:
                assert word not in low, (
                    f"{r['rule']} delivers a verdict ({word!r}): {r['says']!r}")


def test_the_module_computes_nothing():
    """No division and no multiplication anywhere in the file.

    CLAUDE.md's first rule is "You never compute a rate, a delta or a share
    yourself". A panel that divided two cited values would pass the sourcing
    test above -- both halves are cited -- and be exactly the thing the rule
    forbids. Checked on the AST rather than the docstring.
    """
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mult,
                                            ast.Sub)), (
                f"arithmetic at line {node.lineno} of {MODULE.name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "sum", (
                f"sum() at line {node.lineno} of {MODULE.name}")


def test_it_borrows_the_brief_s_citation_machinery():
    """Not a copy. Two implementations of _cite drift, and the drift would be
    invisible: both would still produce a dict with a pointer in it."""
    from intel import readings

    assert ad_readings._cite is readings._cite
    assert ad_readings._at is readings._at
    assert ad_readings._reading is readings._reading


def test_the_refresh_metric_comes_from_propose_not_from_here():
    """`would_register` must agree with what intel propose would file, so the
    panel and the Ideas list cannot name different metrics for the same ad."""
    from intel.propose import SYMPTOM_METRIC

    assert ad_readings.SYMPTOMS == tuple(s for s, _ in SYMPTOM_METRIC)


def test_a_negative_effect_is_still_sourced():
    """The bridge's mix_effect is negative whenever an ad took a smaller share
    of the spend than it had before, which is ordinary rather than exceptional.
    Guards the signed-literal handling above from being "simplified" back."""
    pack = _facts(bridge={"rate_effect": 0.7975, "mix_effect": -0.3688,
                          "total_effect": 0.4287})
    fired = [r for r in ad_readings.read_ad(pack)
             if r["rule"] == "moved_the_brand_cpa"]
    assert fired, "the bridge rule did not fire on a negative mix effect"
    for r in fired:
        allowed = _allowed(r)
        assert "-0.3688" in r["says"], "the sign was dropped from the sentence"
        for literal in NUMBER.findall(r["says"]):
            assert literal in allowed, (
                f"{literal!r} not sourced. cited={sorted(allowed)}")
