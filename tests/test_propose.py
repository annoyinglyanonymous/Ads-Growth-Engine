"""propose -- the generators, as pure functions. No database.

The risk in this module is not that it crashes. It is that it quietly turns
a description of what already happened into a pre-registered experiment, and
that failure has no symptom: the row looks exactly like a real one. So most of
these tests are about what a proposal may NOT contain.
"""

from __future__ import annotations

import pytest

from intel import propose


# ------------------------------------------------------- the vocabulary

def _ad(name, spend):
    return {"entity_name": name, "spend": spend}


def test_normalise_collapses_the_ways_a_style_gets_typed():
    for variant in ("Blue Background", "blue  background",
                    "  BLUE BACKGROUND  ", "Blue Background - Copy"):
        assert propose._normalise(variant) == "blue background"


def test_a_style_is_a_segment_reused_across_different_messages():
    """The filter that separates 'carousel' from 'doug'.

    Spend cannot do this and the real data proves it: doug had more budget
    than carousel. Reuse across messages is what makes something a style.
    """
    rows = [_ad(f"Angle {i} | Static", 100) for i in range(propose.STYLE_REUSE)]
    rows += [_ad("Angle 0 | Doug", 9999), _ad("Angle 0 | Doug", 9999)]
    vocab = propose._vocabulary(rows)
    assert "static" in vocab
    assert "doug" not in vocab, "a name beside one message is not a style"


def test_the_reuse_filter_counts_distinct_messages_not_ads():
    rows = [_ad("One Angle | Carousel", 50) for _ in range(20)]
    assert "carousel" not in propose._vocabulary(rows)


def test_an_unnamed_or_unpiped_ad_is_ignored_rather_than_guessed_at():
    assert propose._vocabulary([_ad("no pipes here", 500), _ad(None, 10)]) == {}


# ------------------------------------------ what a proposal may not claim

def _fatigue_pack(n=6):
    return {"rows": [{
        "ad_key": f"k{i}", "entity_name": f"Ad {i} | UGC", "confident": True,
        "score": 2, "spend_recent": 100 * (i + 1), "days_observed": 13,
        "recent_until": "2026-09-18", "cpa_rise": True,
    } for i in range(n)]}


def test_no_proposal_ever_carries_a_threshold():
    """The core rule. 005 makes you register what would convince you BEFORE
    any number exists; a threshold this module suggested after reading the
    window is a description of what it just saw."""
    out = propose._from_fatigue(_fatigue_pack(), {})
    assert out
    for p in out:
        assert p["would_register"]["minimum_effect_pct"] is None
        assert p["would_register"]["minimum_spend_per_arm"] is None


def test_nothing_proposed_is_marked_filed():
    for p in propose._from_fatigue(_fatigue_pack(), {}):
        assert p["filed"] is False
        assert "intel record" in p["how_to_file"]


def test_no_proposal_names_a_winner_or_concludes():
    """Mirrors 005 having no winner column and 044 having no verdict."""
    banned = ("winner", "won", "beat it", "conclude", "concluded", "proven",
              "clearly better", "we should stop")
    for p in propose._from_fatigue(_fatigue_pack(), {}):
        blob = " ".join(str(p[k]) for k in
                        ("title", "question", "hypothesis", "why")).lower()
        for word in banned:
            assert word not in blob, f"{p['title']} says {word!r}"


def test_fatigue_proposals_are_ranked_by_money_and_capped():
    out = propose._from_fatigue(_fatigue_pack(12), {})
    assert len(out) == propose.PER_SOURCE
    # Highest recent spend first: the money decides whose week this is worth.
    assert "Ad 11" in out[0]["title"]


def test_an_unconfident_or_single_symptom_row_is_not_proposed():
    pack = {"rows": [
        {"ad_key": "a", "entity_name": "A | UGC", "confident": False,
         "score": 5, "spend_recent": 9999, "cpa_rise": True},
        {"ad_key": "b", "entity_name": "B | UGC", "confident": True,
         "score": 1, "spend_recent": 9999, "cpa_rise": True},
    ]}
    assert propose._from_fatigue(pack, {}) == []


@pytest.mark.parametrize("symptom,metric", [
    ("link_ctr_decline", "link_ctr"),
    ("cpa_rise", "cpa"),
    ("cpm_rise", "cpm"),
])
def test_the_registered_metric_is_the_one_that_is_failing(symptom, metric):
    """Registering cpa for an ad whose complaint is link CTR tests something
    nobody asked about, and it would pass or fail for unrelated reasons."""
    pack = {"rows": [{"ad_key": "a", "entity_name": "A | UGC",
                      "confident": True, "score": 2, "spend_recent": 500,
                      symptom: True, "frequency_rise": True}]}
    out = propose._from_fatigue(pack, {})
    assert out[0]["would_register"]["primary_metric"] == metric


def test_a_refresh_is_honest_that_it_cannot_be_filed_yet():
    """Arm B is a creative nobody has made. Hiding the idea for failing a
    validation it was never going to pass would lose the idea."""
    p = propose._from_fatigue(_fatigue_pack(1), {})[0]
    assert p["fileable"] is False
    assert "does not exist yet" in p["blocked_on"]


def test_prior_art_is_carried_so_nothing_is_proposed_twice():
    prior = {"k0": [{"name": "we tried this", "conclusion": "no effect"}]}
    out = propose._from_fatigue(_fatigue_pack(1), prior)
    assert out[0]["already_tested"] is True
    assert out[0]["prior_experiments"][0]["name"] == "we tried this"


# ------------------------------------------------- observations are not tests

def test_an_observation_says_so_and_offers_no_way_to_file_it():
    vocab = {"video": {"heads": set("abcd"), "ads": 50, "spend": 20000.0},
             "carousel": {"heads": set("abcd"), "ads": 6, "spend": 1000.0}}
    _, obs = propose._from_format_gaps(vocab, 4)
    assert obs
    for o in obs:
        assert o["not_an_experiment"]
        assert "how_to_file" not in o, (
            "an observation with a filing command is an experiment wearing a "
            "label that says it is not one")


def test_the_mix_effect_is_only_ever_an_observation():
    """A budget split is not a group of ads, and 005's arms are groups of ads.
    There is no honest arm for 'hold the split', so it is never a proposal."""
    why = {"rows": [{"entity_name": "A", "entity_key": "k",
                     "mix_effect": 1.38, "rate_effect": 0.11}]}
    for o in propose._mix_effect(why):
        assert o["not_an_experiment"]
        assert "arms" not in o


def test_a_negative_mix_effect_is_not_reported_as_a_problem():
    why = {"rows": [{"entity_name": "A", "entity_key": "k",
                     "mix_effect": -2.0, "rate_effect": 0.0}]}
    assert propose._mix_effect(why) == []


# --------------------------------------------------------- format gaps

def test_only_under_funded_styles_are_proposed_and_the_leader_is_not():
    vocab = {"video": {"heads": set("abcd"), "ads": 50, "spend": 20000.0},
             "ugc": {"heads": set("abcd"), "ads": 40, "spend": 18000.0},
             "carousel": {"heads": set("abcd"), "ads": 6, "spend": 1000.0}}
    props, _ = propose._from_format_gaps(vocab, 4)
    titles = " ".join(p["title"] for p in props)
    assert "carousel" in titles
    assert "ugc" not in titles, "18k against 20k is not an under-funded style"
    assert not any(p["title"].startswith("Under-funded: video") for p in props)


def test_format_gaps_are_capped():
    vocab = {"lead": {"heads": set("abcd"), "ads": 99, "spend": 99999.0}}
    vocab.update({f"s{i}": {"heads": set("abcd"), "ads": 3, "spend": 10.0 * i}
                  for i in range(12)})
    props, _ = propose._from_format_gaps(vocab, propose.PER_SOURCE)
    assert len(props) == propose.PER_SOURCE


def test_an_empty_account_proposes_nothing_rather_than_raising():
    props, obs = propose._from_format_gaps({}, 4)
    assert props == [] and obs == []
