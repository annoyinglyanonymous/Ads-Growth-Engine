"""meta_ads.parse against committed Graph responses.

WHY FIXTURES AND NOT A LIVE ACCOUNT
Creative shapes are the part of this import most likely to be parsed wrong,
and they are also the part that is hardest to get hold of: a carousel, a
flexible creative and a boosted Page post have to exist in somebody's account
before a live test can see one. Committed JSON turns "we have never seen that
shape" into a file somebody adds, and lets the whole thing run offline in
milliseconds.

The three ads in ads_page_1.json are the three shapes that disagree with each
other: a single link ad with utm values in the link, a flexible creative with
several bodies and titles and its tagging in url_tags macros, and a Page-post
ad whose words this token cannot read at all. Each is here because getting it
wrong is silent -- wrong ordinals re-pair a body with the wrong title, a
missed url_tags reads as an untagged account, and an empty text list from a
Page post is indistinguishable from an ad with no copy.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from meta_ads import parse

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "meta"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _ads(page: str = "ads_page_1.json") -> dict[str, dict]:
    return {ad["id"]: ad for ad in _load(page)["data"]}


LINK_AD = "120210000000000001"
FLEXIBLE_AD = "120210000000000002"
PAGE_POST_AD = "120210000000000003"

#: What the ad server would substitute for this account's macros. adset.name
#: is deliberately absent; see the unresolved-macro test.
MACROS = {"ad.name": "Outreach101 | Flexible B",
          "campaign.name": "Outreach101 Q4",
          "ad.id": FLEXIBLE_AD}


# --------------------------------------------------------------------------
# creative_texts
# --------------------------------------------------------------------------

def test_a_single_link_ad_yields_one_of_each_field():
    texts = parse.creative_texts(_ads()[LINK_AD]["creative"])

    assert texts.texts == (
        ("headline", 0, "Own your book, not just your desk"),
        ("body", 0, "Independent agents keep the commission split they "
                    "negotiated. See how the model works before you sign "
                    "anything."),
        ("description", 0, "See the model"),
        ("cta", 0, "LEARN_MORE"),
    )
    assert not texts.is_dynamic
    assert not texts.needs_page_scope


def test_link_data_name_is_the_headline_and_message_is_the_body():
    """The one naming in this API worth reading twice.

    `name` is the headline and `message` is the body, which is backwards from
    every other system in this repo. Swapping them would put a 40-character
    headline through the body's limits and the 125-character body through the
    headline's, so QA would pass copy no reviewer would recognise.
    """
    link = _ads()[LINK_AD]["creative"]["object_story_spec"]["link_data"]
    texts = dict(((field, text)
                  for field, _, text in
                  parse.creative_texts(_ads()[LINK_AD]["creative"]).texts))

    assert texts["headline"] == link["name"]
    assert texts["body"] == link["message"]


def test_a_flexible_creative_keeps_every_variant_and_pairs_them_by_ordinal():
    """3 bodies, 2 titles, 2 CTAs, and the ordinal is what pairs them.

    validation/review.py builds variant i out of ordinal i of each field, so
    the ordinals are the contract and not bookkeeping: renumbering them would
    re-pair copy that has already been reviewed, and nothing downstream could
    tell.
    """
    texts = parse.creative_texts(_ads()[FLEXIBLE_AD]["creative"])

    assert texts.is_dynamic
    assert not texts.needs_page_scope

    by_field: dict[str, list[tuple[int, str]]] = {}
    for field, ordinal, text in texts.texts:
        by_field.setdefault(field, []).append((ordinal, text))

    assert [o for o, _ in by_field["body"]] == [0, 1, 2]
    assert [o for o, _ in by_field["headline"]] == [0, 1]
    assert [o for o, _ in by_field["cta"]] == [0, 1]
    assert [t for _, t in by_field["cta"]] == ["LEARN_MORE", "SIGN_UP"]
    assert by_field["body"][0][1] == "Keep the split you negotiated."
    assert by_field["headline"][1][1] == "Keep your renewals"


def test_a_page_post_ad_is_flagged_rather_than_reported_as_empty():
    """Two ads with no texts, and only this flag tells them apart.

    An ads_read token cannot read the words on a Page post, so an empty list
    here means "not readable with this token" and not "nobody wrote any copy".
    A reviewer handed the second without the flag would score a blank ad.
    """
    texts = parse.creative_texts(_ads()[PAGE_POST_AD]["creative"])

    assert texts.texts == ()
    assert texts.needs_page_scope
    assert not texts.is_dynamic


def test_an_ordinary_empty_creative_does_not_claim_a_scope_problem():
    """The other half of the flag, which is what makes it information."""
    texts = parse.creative_texts({"id": "x"})

    assert texts.texts == ()
    assert not texts.needs_page_scope


def test_blank_strings_are_not_variants():
    """Meta returns "" for fields an advertiser left alone.

    Storing one would put an empty headline in front of the reviewer as though
    somebody had written it, and would take ordinal 0 away from the text that
    was actually served.
    """
    texts = parse.creative_texts(
        {"object_story_spec": {"link_data": {"name": "", "message": "  ",
                                             "description": "Real"}}})

    assert texts.texts == (("description", 0, "Real"),)


# --------------------------------------------------------------------------
# link_urls and the macros
# --------------------------------------------------------------------------

def test_a_link_ad_reports_one_destination_not_three_copies_of_it():
    """link, the button's link and the card links are usually the same URL.

    Three identical rows would read as three destinations on the ad page, so
    duplicates are removed while the order is kept.
    """
    urls = parse.link_urls(_ads()[LINK_AD]["creative"])

    assert urls == ["https://renegadeinsurance.com/franchise"
                    "?utm_source=facebook&utm_medium=paid_social"
                    "&utm_campaign=outreach101&utm_content=ad-a-v5"]


def test_url_tags_are_appended_because_that_is_where_the_utm_values_live():
    """The flexible ad's link carries no utm at all until url_tags are added.

    This is the failure that makes a tagged account look untagged: an importer
    reading only `link_urls[].website_url` finds a bare URL on every ad and
    concludes nobody tags anything.
    """
    creative = _ads()[FLEXIBLE_AD]["creative"]
    bare = creative["asset_feed_spec"]["link_urls"][0]["website_url"]

    assert parse.parse_utm(bare) == (None, None)

    tagged = parse.link_urls(creative, MACROS)
    assert len(tagged) == 1
    assert parse.parse_utm(tagged[0]) == ("Outreach101 Q4",
                                          "Outreach101 | Flexible B")


def test_a_macro_value_is_percent_encoded_as_it_goes_in():
    """'Outreach101 | Flexible B' substituted raw would break the query.

    A pipe is survivable; the ampersand in a name like "A & B" is not. Raw
    substitution splits one parameter into two and truncates the utm value at
    the ampersand, so the ad matches nothing and the stored URL shows no sign
    of why.
    """
    assert parse.substitute_macros("utm_content={{ad.name}}",
                                   {"ad.name": "Own your book | A & B"}) == (
        "utm_content=Own%20your%20book%20%7C%20A%20%26%20B")


def test_an_unresolved_macro_is_left_where_it_is():
    """adset.name has no value here, and the placeholder survives.

    Blanking it would produce utm_term= and hide the fact that it was never
    filled in. Left alone it is greppable in the stored URL and on the page.
    """
    url = parse.link_urls(_ads()[FLEXIBLE_AD]["creative"], MACROS)[0]
    query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))

    assert query["utm_term"] == "{{adset.name}}"


def test_existing_parameters_of_the_same_name_are_replaced_not_duplicated():
    """Two utm_content values in one URL is undefined in every tool.

    url_tags win, because that is the order they are appended in when the ad
    serves.
    """
    creative = {
        "object_story_spec": {
            "link_data": {"link": "https://example.com/x"
                                  "?utm_content=old&ref=partner"}},
        "url_tags": "utm_content=new",
    }
    url = parse.link_urls(creative)[0]

    assert parse.parse_utm(url) == (None, "new")
    assert url.count("utm_content") == 1
    assert "ref=partner" in url


# --------------------------------------------------------------------------
# parse_utm
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url, expected", [
    ("https://example.com/x?utm_campaign=c&utm_content=ad-a-v5",
     ("c", "ad-a-v5")),
    # An empty value is a tag somebody meant to fill in. Treating it as a
    # value to match on would join an ad to whichever asset also left it
    # blank.
    ("https://example.com/x?utm_content=", (None, None)),
    ("https://example.com/x", (None, None)),
    (None, (None, None)),
    # The last occurrence wins, matching what appending url_tags does to a
    # link that was already tagged.
    ("https://example.com/x?utm_content=first&utm_content=second",
     (None, "second")),
])
def test_parse_utm(url, expected):
    assert parse.parse_utm(url) == expected


# --------------------------------------------------------------------------
# insight_row
# --------------------------------------------------------------------------

def test_leads_add_up_across_every_name_the_same_conversion_arrives_under():
    """7 `lead` plus 5 `onsite_conversion.lead_grouped` is 12, not 7.

    An account running both an instant form and a landing page reports the
    same conversion under different names. Counting only `lead` would
    under-report one of them and would never look wrong.
    """
    row = parse.insight_row(_load("insights_page.json")["data"][0], "USD")

    assert row["leads"] == 12
    assert row["landing_page_views"] == 88
    # link_click is not a lead, however much it looks like progress.
    assert "link_click" in {a["action_type"] for a in row["actions"]}


def test_cost_per_lead_is_spend_over_leads_and_is_money_not_float():
    row = parse.insight_row(_load("insights_page.json")["data"][0], "USD")

    assert row["spend"] == Decimal("189.00")
    assert row["cost_per_lead"] == Decimal("15.7500")
    assert isinstance(row["cost_per_lead"], Decimal)
    assert row["currency"] == "USD"
    assert row["date"] == date(2026, 9, 10)


def test_no_leads_means_no_cost_per_lead_and_not_a_free_one():
    """NULL and never 0. A zero averages into a brand median as a free lead
    and sorts an ad that produced nothing to the top of a cheapest-CPL
    column."""
    row = parse.insight_row(_load("insights_page.json")["data"][1], "USD")

    assert row["leads"] == 0
    assert row["cost_per_lead"] is None
    assert row["spend"] == Decimal("42.00")


def test_booked_is_zero_and_the_actions_are_kept_so_it_can_be_answered_later():
    """Nobody has decided what a booked call is in Meta terms.

    The whole actions array stays on the row, so the decision applies to
    history instead of starting from the day it is made.
    """
    assert parse.BOOKED_ACTION_TYPES == ()

    row = parse.insight_row(_load("insights_page.json")["data"][0], "USD")
    assert row["booked"] == 0
    assert len(row["actions"]) == 5


def test_an_ungraded_ranking_is_null_rather_than_a_grade_below_the_lowest():
    """UNKNOWN means "not enough impressions to grade", which is not a rank.

    Stored as a word it would sort into a ranking column and read as a verdict
    Meta never gave.
    """
    graded, ungraded = _load("insights_page.json")["data"]

    first = parse.insight_row(graded)
    assert first["quality_ranking"] == "above_average"
    assert first["engagement_rate_ranking"] == "average"
    assert first["conversion_rate_ranking"] is None

    second = parse.insight_row(ungraded)
    assert second["quality_ranking"] is None


def test_a_missing_number_is_none_and_not_zero():
    """An ad with no impressions did not run; an ad whose impressions are
    missing from the response is a pull that asked for the wrong fields.
    Defaulting both to 0 makes the second invisible."""
    row = parse.insight_row({"ad_id": "1", "date_start": "2026-09-10"})

    assert row["impressions"] is None
    assert row["spend"] is None
    assert row["leads"] == 0            # counted from an empty actions list


def test_the_whole_response_is_kept_on_the_row():
    """042's raw column, and the reason a parse fix is a replay rather than a
    re-pull against a rate limit for days Meta no longer serves."""
    raw = _load("insights_page.json")["data"][0]

    assert parse.insight_row(raw)["raw"] == raw
