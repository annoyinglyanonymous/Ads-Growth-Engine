"""The join back to our own copy, round-tripped against the real tracking.py.

THE PROPERTY THIS FILE EXISTS FOR
tracking.tracked_url writes the utm values onto every approved asset and
meta_ads.parse reads them back off an ad. Those are two halves of one
convention written months apart, and nothing else makes them agree. If either
side changes its spelling -- a different separator in slot_content, a different
parameter name, a channel whose source stops being 'facebook' -- every ad in
the account quietly stops matching, campaign pages start saying "not written
here" about copy this engine wrote, and no test fails.

So the assertion is a round trip through both real modules and not a pair of
hardcoded strings. A fixture URL copied into this file would keep passing
after tracking.py changed, which is the exact failure it is supposed to catch.

THE SECOND PROPERTY IS THE RENAME
025 stamps tracked_url at approval so a campaign renamed afterwards does not
change the link that shipped. match_asset therefore parses the STAMPED string.
The obvious alternative -- rebuild the slug from today's campaign name -- looks
identical on a campaign nobody has renamed and breaks silently on one that has
been, so it gets its own test rather than a comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tracking
from meta_ads import parse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meta"

DESTINATION = "https://renegadeinsurance.com/franchise"
CAMPAIGN = "Outreach101"


def _ad(ad_id: str) -> dict:
    for page in ("ads_page_1.json", "ads_page_2.json"):
        for ad in json.loads(
                (FIXTURES / page).read_text(encoding="utf-8"))["data"]:
            if ad["id"] == ad_id:
                return ad
    raise AssertionError(f"no fixture ad {ad_id}")


def _stamp(*, campaign_name: str = CAMPAIGN, variant: str = "A",
           position: int | None = None, version: int = 5,
           asset_type: str = "meta_ad") -> str:
    return tracking.tracked_url(
        DESTINATION, campaign_name=campaign_name, channel="meta_ads",
        asset_type=asset_type, variant=variant, position=position,
        version=version)


def _asset(tracked_url: str, **overrides) -> dict:
    row = {"id": "11111111-1111-1111-1111-111111111111",
           "tracked_url": tracked_url,
           "status": "approved",
           "version_number": 5,
           "approved_at": "2026-09-01T10:00:00+00:00"}
    row.update(overrides)
    return row


# --------------------------------------------------------------------------
# The round trip
# --------------------------------------------------------------------------

@pytest.mark.parametrize("asset_type, variant, position, version", [
    ("meta_ad", "A", None, 5),
    ("meta_ad", "B", None, 2),
    ("video_script", "C", None, 1),
    # A sequence position is part of the slot and 0 is a real one.
    ("email", "A", 0, 3),
    ("email", "B", 2, 11),
])
def test_what_tracking_stamps_is_what_parse_reads_back(asset_type, variant,
                                                       position, version):
    """parse_utm(tracked_url(...)) == (campaign_slug(name), slot_content(...)).

    Both sides computed, so the test fails the moment either convention moves
    rather than the moment somebody notices ads have stopped matching.
    """
    channel = "email" if asset_type == "email" else "meta_ads"
    url = tracking.tracked_url(
        DESTINATION, campaign_name=CAMPAIGN, channel=channel,
        asset_type=asset_type, variant=variant, position=position,
        version=version)

    assert parse.parse_utm(url) == (
        tracking.campaign_slug(CAMPAIGN),
        tracking.slot_content(asset_type, variant, position, version))


def test_the_round_trip_survives_a_name_that_needs_folding():
    """Accents, punctuation and case all fold on the way in, and the value
    that comes back out is the folded one, not the original."""
    name = "Sao Paulo Push -- Q4/2026 (Agents!)"
    url = tracking.tracked_url(
        DESTINATION, campaign_name=name, channel="meta_ads",
        asset_type="meta_ad", variant="A", position=None, version=1)

    campaign, content = parse.parse_utm(url)
    assert campaign == tracking.campaign_slug(name)
    assert "/" not in campaign and " " not in campaign
    assert content == "ad-a-v1"


def test_a_fixture_ad_carries_exactly_the_url_tracking_would_have_stamped():
    """The fixture is not an independent guess at the convention.

    If this fails, the committed ads no longer look like ads this engine's
    assets would have produced, and every other test in this file is testing
    a shape that does not occur.
    """
    urls = parse.link_urls(_ad("120210000000000001")["creative"])

    assert urls == [_stamp(variant="A", version=5)]


# --------------------------------------------------------------------------
# match_asset
# --------------------------------------------------------------------------

def test_an_ad_matches_the_asset_whose_stamped_url_it_carries():
    ad = _ad("120210000000000001")
    pairs = [parse.parse_utm(u) for u in parse.link_urls(ad["creative"])]
    asset = _asset(_stamp(variant="A", version=5))

    assert parse.match_asset(pairs, [asset, _asset(
        _stamp(variant="B", version=2), id="other")]) is asset


def test_a_renamed_campaign_still_matches_because_the_stamp_is_parsed():
    """The failure 025 exists to prevent, as a test rather than a comment.

    The ad shipped under the old name and its link still says so. Recomputing
    the slug from today's campaign name would produce 'outreach101-renamed',
    match nothing, and report that copy this engine wrote was written
    somewhere else -- with no error anywhere.
    """
    ad = _ad("120210000000000001")
    pairs = [parse.parse_utm(u) for u in parse.link_urls(ad["creative"])]

    stamped = _stamp(campaign_name="Outreach101", variant="A", version=5)
    renamed_today = tracking.campaign_slug("Outreach101 (renamed)")

    assert parse.match_asset(pairs, [_asset(stamped)]) is not None
    assert renamed_today != tracking.campaign_slug("Outreach101")


def test_the_approved_row_wins_over_a_draft_in_the_same_slot():
    """A slot holds v1 superseded, v2 approved, v3 drafted since. The approved
    row is the copy that actually ran, so it is the one the ad belongs to."""
    url = _stamp(variant="A", version=5)
    draft = _asset(url, id="draft", status="draft", version_number=6,
                   approved_at=None)
    approved = _asset(url, id="approved", status="approved",
                      version_number=5)

    assert parse.match_asset([("outreach101", "ad-a-v5")],
                             [draft, approved]) is approved


def test_with_nothing_approved_the_latest_version_wins():
    url = _stamp(variant="A", version=5)
    older = _asset(url, id="older", status="draft", version_number=4,
                   approved_at=None)
    newer = _asset(url, id="newer", status="draft", version_number=6,
                   approved_at=None)

    assert parse.match_asset([("outreach101", "ad-a-v5")],
                             [older, newer]) is newer


def test_utm_content_must_agree_exactly_because_it_is_what_identifies():
    """'ad-a-v5' and 'ad-a-v6' are two versions of one slot and not the same
    copy. Matching on the slot alone would attribute v6's performance to the
    wording of v5."""
    assert parse.match_asset([("outreach101", "ad-a-v6")],
                             [_asset(_stamp(variant="A", version=5))]) is None


def test_a_campaign_that_disagrees_is_not_a_match():
    """Two campaigns can each reach an 'ad-a-v5'. utm_campaign is what tells
    them apart when both sides have one."""
    assert parse.match_asset([("some-other-campaign", "ad-a-v5")],
                             [_asset(_stamp(variant="A", version=5))]) is None


def test_an_ad_tagged_with_content_but_no_campaign_still_matches():
    """A match rather than a mystery: utm_content does the identifying, and
    utm_campaign only ever disambiguates."""
    assert parse.match_asset([(None, "ad-a-v5")],
                             [_asset(_stamp(variant="A", version=5))]) \
        is not None


def test_an_untagged_ad_matches_nothing_rather_than_the_first_row():
    """None is the ordinary answer. Most ads in an account were written
    somewhere else, and those are the ads the review stage is for."""
    page_post = _ad("120210000000000003")
    pairs = [parse.parse_utm(u)
             for u in parse.link_urls(page_post["creative"])]

    assert pairs == []
    assert parse.match_asset(pairs, [_asset(_stamp())]) is None


def test_two_blank_utm_content_values_do_not_join_to_each_other():
    """'utm_content=' on both sides is two things nobody filled in, not a
    pair."""
    blank = "https://renegadeinsurance.com/franchise?utm_content="

    assert parse.match_asset([parse.parse_utm(blank)],
                             [_asset(blank)]) is None


def test_a_flexible_ad_tagged_by_macro_does_not_match_a_stamped_asset():
    """Its utm_content is the ad's name, not a slot label.

    Worth pinning: it is the common way an ad this engine did not write ends
    up carrying a utm_content at all, and a looser matcher would attach it to
    whichever asset sorted first.
    """
    flexible = _ad("120210000000000002")
    pairs = [parse.parse_utm(u) for u in parse.link_urls(
        flexible["creative"], {"ad.name": flexible["name"],
                               "campaign.name": "Outreach101 Q4"})]

    assert pairs == [("Outreach101 Q4", "Outreach101 | Flexible B")]
    assert parse.match_asset(pairs, [_asset(_stamp())]) is None


def test_a_candidate_with_no_stamped_url_is_skipped_not_crashed_on():
    """An asset approved before 025, or never approved, has no tracked_url."""
    assert parse.match_asset([("outreach101", "ad-a-v5")],
                             [_asset(None), _asset("")]) is None
