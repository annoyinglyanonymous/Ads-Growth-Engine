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

def _rates(link_ctr, cvr=40.0, lpv=20.0, cpm=50.0, cpa=30.0):
    return {"rates": {"link_ctr": link_ctr, "conversion_rate": cvr,
                      "lp_view_rate": lpv, "cpm": cpm, "cpa": cpa}}


def _fatigue_pack(n=6, ctr_then=1.0, ctr_now=0.5, **now):
    """Ads whose link CTR has HALVED by default -- an attention problem, so
    the diagnosis routes them to a refresh. The rates matter now: the
    generator reads them rather than the boolean flags, because an ad whose
    click rate is rising does not want a creative test however many flags
    fired."""
    return {"rows": [{
        "ad_key": f"k{i}", "entity_name": f"Ad {i} | UGC", "confident": True,
        "score": 2, "spend_recent": 100 * (i + 1), "days_observed": 13,
        "recent_until": "2026-09-18", "cpa_rise": True,
        "prior": _rates(ctr_then),
        "recent": _rates(ctr_now, **now),
    } for i in range(n)]}


def test_no_proposal_ever_carries_a_threshold():
    """The core rule. 005 makes you register what would convince you BEFORE
    any number exists; a threshold this module suggested after reading the
    window is a description of what it just saw."""
    out, _ = propose._from_fatigue(_fatigue_pack(), {})
    assert out
    for p in out:
        assert p["would_register"]["minimum_effect_pct"] is None
        assert p["would_register"]["minimum_spend_per_arm"] is None


def test_nothing_proposed_is_marked_filed():
    for p in propose._from_fatigue(_fatigue_pack(), {})[0]:
        assert p["filed"] is False
        assert "intel record" in p["how_to_file"]


def test_no_proposal_names_a_winner_or_concludes():
    """Mirrors 005 having no winner column and 044 having no verdict."""
    banned = ("winner", "won", "beat it", "conclude", "concluded", "proven",
              "clearly better", "we should stop")
    for p in propose._from_fatigue(_fatigue_pack(), {})[0]:
        blob = " ".join(str(p[k]) for k in
                        ("title", "question", "hypothesis", "why")).lower()
        for word in banned:
            assert word not in blob, f"{p['title']} says {word!r}"


def test_fatigue_proposals_are_ranked_by_money_and_capped():
    out, _ = propose._from_fatigue(_fatigue_pack(12), {})
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
    assert propose._from_fatigue(pack, {}) == ([], [])


@pytest.mark.parametrize("ctr_then,ctr_now,extra,metric", [
    (1.0, 0.5, {}, "link_ctr"),                      # clicks collapsed
    (1.0, 1.0, {"cpm": 80.0}, "cpm"),                # same clicks, dearer
])
def test_the_registered_metric_follows_the_diagnosis(ctr_then, ctr_now,
                                                     extra, metric):
    """Registering cpa for an ad whose complaint is link CTR tests something
    nobody asked about, and it would pass or fail for unrelated reasons.

    This used to be parametrised on ads.fatigue's BOOLEAN flags. It is
    parametrised on rates now because the flags cannot tell you which way a
    number moved, and two of the four cards on the live page were proposing a
    creative rewrite for ads whose click rate had gone up.
    """
    out, _ = propose._from_fatigue(
        _fatigue_pack(1, ctr_then=ctr_then, ctr_now=ctr_now, **extra), {})
    assert out[0]["would_register"]["primary_metric"] == metric


def test_a_refresh_is_honest_that_it_cannot_be_filed_yet():
    """Arm B is a creative nobody has made. Hiding the idea for failing a
    validation it was never going to pass would lose the idea."""
    p = propose._from_fatigue(_fatigue_pack(1), {})[0][0]
    assert p["fileable"] is False
    assert "does not exist yet" in p["blocked_on"]


def test_prior_art_is_carried_so_nothing_is_proposed_twice():
    prior = {"k0": [{"name": "we tried this", "conclusion": "no effect"}]}
    out, _ = propose._from_fatigue(_fatigue_pack(1), prior)
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


# ------------------------------------------------- reading the rate movements

@pytest.mark.parametrize("prior,recent", [(None, 5), (0, 5), (5, None)])
def test_an_undefined_movement_is_none_and_never_a_number(prior, recent):
    """The undefined-rate trap the rest of this repo guards everywhere.

    An ad with no conversions last window has an UNDEFINED cpa, not a cpa of
    zero. Treating the arrival of its first conversion as an infinite
    improvement is how a $12 ad reaches the top of a ranked list.
    """
    assert propose._move(prior, recent) is None


def test_a_falling_click_rate_is_the_creatives_problem():
    dx = propose._diagnose(_rates(1.0), _rates(0.5))
    assert dx["stage"] == "attention"
    assert dx["creative_is_the_problem"] is True


def test_a_rising_click_rate_with_a_falling_conversion_rate_is_not():
    """THE BUG THIS WHOLE PASS EXISTS FOR.

    Two live cards recommended rewriting a creative whose link CTR had risen
    -- one of them by 115% -- because the generator read boolean symptom flags
    instead of the rates behind them. Following that advice would have
    replaced the one part still working.
    """
    dx = propose._diagnose(_rates(1.0, cvr=40.0, lpv=20.0),
                           _rates(1.2, cvr=15.0, lpv=19.0))
    assert dx["creative_is_the_problem"] is False
    assert dx["stage"] == "after the click"


def test_every_broken_stage_is_named_not_only_the_first():
    """'Become an Agent | Start Franchise' was link CTR -28% AND landing page
    views -91%. Funnel order picks the response; it must not hide the rest."""
    dx = propose._diagnose(_rates(1.0, cvr=40.0, lpv=20.0),
                           _rates(0.7, cvr=18.0, lpv=2.0))
    assert dx["stage"] == "attention"
    joined = " ".join(dx["also_broke"]).lower()
    assert "landing page" in joined and "conversion rate" in joined
    assert "link ctr" not in joined, "the primary is already in `says`"


def test_a_diagnosis_is_not_shaped_like_an_experiment():
    """No arms, no metric, no filing command. Giving a finding the shape of a
    test invites somebody to register one, and the result looks like an
    answer to a question nobody asked."""
    _, found = propose._from_fatigue(
        _fatigue_pack(1, ctr_then=1.0, ctr_now=1.2, cvr=10.0), {})
    assert found, "a rising CTR with a collapsed conversion rate is a finding"
    for d in found:
        assert "arms" not in d
        assert "would_register" not in d
        assert "how_to_file" not in d
        assert d["not_an_experiment"]


# ------------------------------------------------------ the brief is not copy

def test_the_brief_invents_no_ad_text():
    """This repo does not write ad copy and must not start.

    Copy production sits behind growth-engine's claim gate, an approved
    concept_id and a person's signature. For an insurance advertiser those
    gates are the product. Every field of a brief has to be READ from
    somewhere, so this asserts the headline and body come out of ad_copy
    unchanged rather than being generated.
    """
    copy = {"first_headline": "HEADLINE FROM THE DATABASE",
            "first_body": "BODY FROM THE DATABASE"}
    dx = propose._diagnose(_rates(1.0), _rates(0.5))
    b = propose._brief("Angle | Static", dx, [], copy, ["ugc"], "link_ctr")
    assert b["current_headline"] == copy["first_headline"]
    assert b["current_body"] == copy["first_body"]

    empty = propose._brief("Angle | Static", dx, [], {}, [], "link_ctr")
    assert empty["current_headline"] is None, (
        "no copy on the ad must stay no copy -- never a suggested one")


def test_the_brief_never_names_a_campaign_it_cannot_know():
    """This repo has no access to growth-engine's campaign names, and a brief
    naming the wrong one is worse than one naming none."""
    dx = propose._diagnose(_rates(1.0), _rates(0.5))
    b = propose._brief("Angle | Static", dx, [], {}, [], "cpa")
    assert b["campaign"] == "<campaign>"
    assert "<campaign>" in b["written_where"]


def test_an_untried_style_is_one_from_the_vocabulary_not_a_persons_name():
    rows = [_ad(f"Angle {i} | Static", 10) for i in range(propose.STYLE_REUSE)]
    rows += [_ad("Angle 0 | Doug", 10)]
    vocab = propose._vocabulary(rows)
    styles = propose._angle_styles(rows, vocab)
    every = set()
    for tried, untried in styles.values():
        every |= tried | untried
    assert "doug" not in every, "a person is not a creative direction"


# ------------------------------------------- the public diagnose() contract

def test_diagnose_never_registers_a_metric_that_improved():
    """THE PROPERTY THE WHOLE EXPORT EXISTS FOR.

    intel/ad_readings.py found four live ads registering a metric that had
    moved the GOOD way -- one with CPM down 41%. The cause was not the
    ads.fatigue flags, which 003 makes one-sided and honest. It was
    SYMPTOM_METRIC's two PROXY entries: frequency_rise -> cpm and
    ranking_drop -> link_ctr, neither measured on the metric it points at.

    This sweeps the corners of the rate space and asserts the property
    directly, so it holds for inputs nobody thought to enumerate.
    """
    better = {"link_ctr": lambda a, b: b > a,      # up is good
              "conversion_rate": lambda a, b: b > a,
              "cpm": lambda a, b: b < a,           # down is good
              "cpa": lambda a, b: b < a}
    for ctr in (0.5, 1.0, 2.0):
        for cvr in (10.0, 40.0, 80.0):
            for lpv in (2.0, 20.0, 40.0):
                for cpm in (20.0, 50.0, 90.0):
                    for cpa in (10.0, 30.0, 60.0):
                        row = {"prior": _rates(1.0),
                               "recent": _rates(ctr, cvr, lpv, cpm, cpa)}
                        dx = propose.diagnose(row)
                        m = dx["metric"]
                        if m is None:
                            continue
                        prior = propose._rate(row["prior"], m)
                        recent = propose._rate(row["recent"], m)
                        assert not better[m](prior, recent), (
                            f"{dx['stage']} registered {m} which improved "
                            f"{prior} -> {recent}")


def test_a_steady_ad_gets_no_metric_and_claims_nothing():
    """ads.fatigue can flag an ad on a proxy symptom while every rate it
    tracks held. Saying 'cost rose' there would be false, and registering a
    metric would be worse."""
    dx = propose.diagnose({"prior": _rates(1.0), "recent": _rates(1.0)})
    assert dx["stage"] == "steady"
    assert dx["metric"] is None
    assert dx["creative_is_the_problem"] is False


def test_a_downstream_problem_gets_no_metric_either():
    """`metric` means what a REFRESH would register, and there is no refresh
    to run for a landing page. This is what stops /ad/<key> naming a metric
    for an ad /experiments files nothing against."""
    dx = propose.diagnose({"prior": _rates(1.0, cvr=40.0, lpv=20.0),
                           "recent": _rates(1.2, cvr=15.0, lpv=19.0)})
    assert dx["creative_is_the_problem"] is False
    assert dx["metric"] is None


def test_diagnose_takes_a_row_and_tolerates_a_bare_one():
    """ad_readings passes ads.fatigue rows straight through; a row with no
    prior/recent must not raise."""
    dx = propose.diagnose({})
    assert dx["metric"] is None and dx["stage"] == "unreadable"


def test_we_did_not_look_is_not_the_same_as_nothing_moved():
    """The distinction this repo exists to keep.

    A flagged ad whose rates cannot be read must not report "no rate moved
    adversely" -- that asserts a fact nobody established, and it is the same
    shape as a stale import reading as a quiet week. Both return no metric;
    only one of them claims to have looked.

    Raised by the session on intel/ad_readings.py, whose guard named cpa here
    on the reasoning that a missing rate is not evidence of improvement. It
    is not evidence of anything, which is why the sentence had to change
    rather than the metric.
    """
    blind = propose.diagnose({"cpa_rise": True,
                              "prior": {"rates": {}}, "recent": {"rates": {}}})
    assert blind["stage"] == "unreadable"
    assert blind["metric"] is None
    assert "either way" in blind["says"]

    looked = propose.diagnose({"prior": _rates(1.0), "recent": _rates(1.0)})
    assert looked["stage"] == "steady"
    assert looked["metric"] is None
    assert "moved adversely" in looked["says"]

    assert blind["says"] != looked["says"], (
        "two different findings must not render as the same sentence")

