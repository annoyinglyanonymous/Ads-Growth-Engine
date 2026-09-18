"""What a Meta ad is made of, worked out from its JSON and nothing else.

PURE ON PURPOSE, THE SAME DISCIPLINE AS tracking.py
No database, no network, no settings, no clock. Every function here takes a
piece of Graph API response and returns a value. That is what makes the hard
part testable: creative shapes are where this import is most likely to be
wrong, and each shape is one committed fixture rather than an account somebody
has to own before the test can run.

THERE ARE FOUR CREATIVE SHAPES AND THEY DISAGREE ABOUT EVERYTHING
A single link ad puts its copy in `object_story_spec.link_data`. A carousel
puts one headline per card in `child_attachments` and keeps a single body above
them. A flexible (dynamic) creative puts several bodies, titles, descriptions
and call to action types in `asset_feed_spec` and lets Meta assemble them per
impression, so it has no single headline at all. An ad boosting an existing
Page post has none of those: its words live on the post, and an ads_read token
cannot read them.

The last one is the reason `creative_texts` returns flags and not just rows. An
empty list of texts means "this ad has no copy" for three of those shapes and
"this ad's copy is not visible to this token" for the fourth, and a reviewer
handed an empty ad needs to know which.

THE utm JOIN READS THE STAMPED URL, NEVER A RECOMPUTED ONE
025 stamps campaign_assets.tracked_url at approval precisely so a campaign
renamed after launch does not silently change the link that shipped.
`match_asset` therefore parses that stored string. Rebuilding the slug from
today's campaign name would undo 025's guarantee and, worse, would undo it
quietly: every ad approved before the rename would simply stop matching, and
the page would say "not written here" about copy this engine wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

#: The four text fields, and the whole vocabulary meta_ad_texts.field allows.
#: 'cta' holds Meta's call to action TYPE ('LEARN_MORE'), not a label anyone
#: wrote: the button text is Meta's, chosen from a fixed list, and storing it
#: beside the copy is what lets the reviewer see that a lead form ad is asking
#: people to Learn More.
FIELDS = ("headline", "body", "description", "cta")

#: Action types that count as a lead.
#:
#: Three and not one because the same conversion arrives under a different name
#: depending on where it happened: `lead` is the account-level roll-up,
#: `onsite_conversion.lead_grouped` is an instant form filled without leaving
#: Facebook, and `offsite_conversion.fb_pixel_lead` is the pixel firing on a
#: thank-you page. An account running both a form and a landing page reports
#: both, and a report that counted only `lead` would under-report one of them
#: without ever looking wrong.
#:
#: DERIVED IN PYTHON, NOT IN SQL. Which events count is a judgement that will
#: change the first time a new form type appears, and every insights row keeps
#: its whole `actions` array, so changing this list is an edit plus a replay
#: rather than a migration.
LEAD_ACTION_TYPES: tuple[str, ...] = (
    "lead",
    "onsite_conversion.lead_grouped",
    "offsite_conversion.fb_pixel_lead",
)

#: EMPTY, AND DELIBERATELY SO. Nobody has decided what a booked call is in Meta
#: terms: it could be a pixel Schedule event, a custom conversion with an id
#: only this account knows, or something that only ever reaches the CRM. Until
#: she decides, `booked` is 0 everywhere and the UI shows leads only. Every
#: action type is kept on the row, so the answer applies to history rather than
#: starting from the day it is given.
BOOKED_ACTION_TYPES: tuple[str, ...] = ()

#: Not a lead, and worth its own column: it is the first thing to look at when
#: link clicks are healthy and leads are not, because it separates "the ad is
#: not working" from "the page is not loading".
LANDING_PAGE_ACTION_TYPES: tuple[str, ...] = ("landing_page_view",)

#: Meta grades an ad against others competing for the same audience, and
#: returns this when it has not had enough impressions to grade it. It is not a
#: grade below the lowest one, so it is stored as NULL rather than as a word
#: somebody will sort.
UNGRADED = "unknown"

#: The macros Meta substitutes into url_tags and link fields when the ad
#: serves. Anything else is left alone; see `substitute_macros`.
_MACRO = re.compile(r"\{\{([A-Za-z0-9_.]+)\}\}")


# --------------------------------------------------------------------------
# The copy
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CreativeTexts:
    """The texts, plus the two facts that say how to read an empty list.

    A plain list of rows would be the obvious return and it would be wrong in
    one specific case: an ad with no texts. That happens because the ad runs an
    existing Page post whose words this token may not fetch, and it happens
    because a creative genuinely carries nothing, and those call for different
    handling everywhere downstream. Both flags are discovered while walking the
    same tree, and neither is recoverable from the rows afterwards.
    """

    texts: tuple[tuple[str, int, str], ...]
    is_dynamic: bool
    needs_page_scope: bool


class _Rows:
    """Ordinals assigned in emission order, per field.

    ordinal is the position of a text within its own field, and it is the only
    thing that pairs a body with a title: variant i of a flexible creative is
    ordinal i of each field. So the walk order below is part of the contract,
    not an implementation detail, and it has to stay stable -- changing it
    would re-pair copy that has already been reviewed.
    """

    def __init__(self) -> None:
        self.rows: list[tuple[str, int, str]] = []
        self._next: dict[str, int] = {}

    def add(self, field: str, value) -> None:
        text = str(value or "").strip()
        if not text:
            # A blank is not a variant. Meta returns empty strings for fields
            # an advertiser left alone, and storing one would put an empty
            # headline in front of the reviewer as though somebody wrote it.
            return
        ordinal = self._next.get(field, 0)
        self.rows.append((field, ordinal, text))
        self._next[field] = ordinal + 1


def _entries(value) -> list[str]:
    """asset_feed_spec lists, which are sometimes objects and sometimes not.

    `bodies` and `titles` arrive as [{"text": "..."}]; `call_to_action_types`
    arrives as ["LEARN_MORE"]. One helper rather than two branches at four call
    sites.
    """
    out = []
    for entry in value or []:
        if isinstance(entry, dict):
            out.append(entry.get("text") or entry.get("value") or "")
        else:
            out.append(entry)
    return out


def creative_texts(creative: dict | None) -> CreativeTexts:
    """Every text variant an ad carries, as (field, ordinal, text).

    The four shapes are tried in the order they override each other. A
    flexible creative's asset_feed_spec wins outright where it exists, because
    Meta serves from it and the object_story_spec beside it is the fallback
    single-asset version that never actually runs.
    """
    creative = creative or {}
    spec = creative.get("asset_feed_spec") or {}
    story = creative.get("object_story_spec") or {}
    rows = _Rows()

    is_dynamic = any(spec.get(key) for key in
                     ("bodies", "titles", "descriptions",
                      "call_to_action_types"))

    if is_dynamic:
        for title in _entries(spec.get("titles")):
            rows.add("headline", title)
        for body in _entries(spec.get("bodies")):
            rows.add("body", body)
        for description in _entries(spec.get("descriptions")):
            rows.add("description", description)
        for cta in _entries(spec.get("call_to_action_types")):
            rows.add("cta", cta)
    else:
        link = story.get("link_data") or {}
        video = story.get("video_data") or {}

        # The single-asset shapes. link_data.name is the headline and
        # link_data.message is the body, which is the one naming in this API
        # worth reading twice.
        rows.add("headline", link.get("name") or video.get("title"))
        rows.add("body", link.get("message") or video.get("message"))
        rows.add("description",
                 link.get("description") or video.get("link_description"))
        rows.add("cta", (link.get("call_to_action")
                         or video.get("call_to_action")
                         or {}).get("type"))

        # A carousel. One card per entry, each with its own headline,
        # description and button, under the single body added above.
        for card in link.get("child_attachments") or []:
            if not isinstance(card, dict):
                continue
            rows.add("headline", card.get("name"))
            rows.add("description", card.get("description"))
            rows.add("cta", (card.get("call_to_action") or {}).get("type"))

        # Legacy fields, still present on ads created years ago through the
        # old creative endpoints. Added only where the modern shape produced
        # nothing for that field, so an ad carrying both does not get its
        # headline twice.
        if not any(f == "headline" for f, _, _ in rows.rows):
            rows.add("headline", creative.get("title"))
        if not any(f == "body" for f, _, _ in rows.rows):
            rows.add("body", creative.get("body"))

    # A boosted Page post. Its copy is on the post, reachable only with
    # pages_read_engagement on that Page, which an ads_read System User token
    # does not carry -- so this is a fact about what may be read, not a parse
    # failure, and it is flagged rather than logged.
    page_post = bool(creative.get("object_story_id")
                     or creative.get("effective_object_story_id"))
    return CreativeTexts(tuple(rows.rows), is_dynamic,
                         page_post and not rows.rows)


# --------------------------------------------------------------------------
# The links
# --------------------------------------------------------------------------

def substitute_macros(value: str, macros: dict[str, str] | None) -> str:
    """Meta's {{ad.name}} placeholders, resolved the way the ad server would.

    Values are percent-encoded as they go in. An ad called "Own your book | A
    & B" substituted raw would put an ampersand in the middle of a query
    string, which splits one parameter into two and truncates the utm value at
    the ampersand -- so the ad would match nothing and the reason would be
    invisible in the stored URL.

    A macro with no value is LEFT AS IT IS rather than blanked. An unresolved
    {{ad.name}} sitting in utm_content is a visible, greppable "this was never
    filled in"; an empty string is the same failure with the evidence removed.
    """
    if not value or not macros:
        return value or ""

    def one(match: re.Match) -> str:
        replacement = macros.get(match.group(1))
        return (quote(str(replacement), safe="") if replacement is not None
                else match.group(0))

    return _MACRO.sub(one, value)


def _with_tags(url: str, tags: str, macros: dict[str, str] | None) -> str:
    """One destination with the creative's url_tags appended.

    url_tags is where most advertisers put their utm parameters: it is one
    field on the creative rather than one edit per link, so a carousel with six
    cards is tagged once. Nothing in the link itself says the tags are coming,
    which is why an importer that read only `link` finds ad after ad with no
    utm values and concludes the account is untagged.

    Existing parameters of the same name are REPLACED, not duplicated, for
    tracking.py's reason: two utm_content values in one URL is undefined
    behaviour in every analytics tool. The tags win because that is the order
    they are appended in when the ad serves.
    """
    url = substitute_macros(url, macros)
    tags = substitute_macros((tags or "").lstrip("?&"), macros)
    if not tags:
        return url

    parts = urlsplit(url)
    extra = parse_qsl(tags, keep_blank_values=True)
    taken = {key for key, _ in extra}
    query = [(k, v) for k, v
             in parse_qsl(parts.query, keep_blank_values=True)
             if k not in taken]
    return urlunsplit(parts._replace(query=urlencode(query + extra)))


def _cta_link(node) -> str | None:
    """The link behind a call_to_action button, through two optional levels.

    Written out rather than chained because `call_to_action` and `value` are
    each independently absent in the wild, and `x.get("value", {})` returns
    None rather than the default when the key is present and null -- which is
    an AttributeError one field deeper, on real data, during an import.
    """
    cta = (node or {}).get("call_to_action") or {}
    return (cta.get("value") or {}).get("link")


def link_urls(creative: dict | None,
              macros: dict[str, str] | None = None) -> list[str]:
    """Every destination this ad can send somebody to, fully resolved.

    Several, not one, and each has to be checked: a carousel sends each card
    somewhere different, a link ad's button can point somewhere other than the
    image, and a flexible creative carries its destinations in a list of its
    own. An importer that read `link_data.link` alone would miss the utm values
    on every ad whose tagging lives on the button.

    Returned in a stable order with duplicates removed, because the common case
    is the same URL reached three ways and three identical rows would suggest
    three destinations.
    """
    creative = creative or {}
    story = creative.get("object_story_spec") or {}
    link = story.get("link_data") or {}
    video = story.get("video_data") or {}
    spec = creative.get("asset_feed_spec") or {}

    raw: list[str] = [
        link.get("link"),
        _cta_link(link),
        _cta_link(video),
        creative.get("object_url"),
    ]
    for card in link.get("child_attachments") or []:
        if not isinstance(card, dict):
            continue
        raw.append(card.get("link"))
        raw.append(_cta_link(card))
    for entry in spec.get("link_urls") or []:
        if isinstance(entry, dict):
            raw.append(entry.get("website_url"))

    tags = creative.get("url_tags") or ""
    out: list[str] = []
    for url in raw:
        if not url:
            continue
        tagged = _with_tags(str(url), tags, macros)
        if tagged not in out:
            out.append(tagged)
    return out


def parse_utm(url: str | None) -> tuple[str | None, str | None]:
    """(utm_campaign, utm_content) out of one URL, or (None, None).

    The last occurrence of a repeated parameter wins, matching what appending
    url_tags does to a link that was already tagged. `_with_tags` has already
    removed the duplicates on anything this module produced, so this only
    matters for a URL that arrived from somewhere else.

    An empty value reads as absent. 'utm_content=' is a tag somebody meant to
    fill in, and treating it as a value to match on would join an ad to
    whichever asset also failed to fill it in.
    """
    query = dict(parse_qsl(urlsplit(url or "").query, keep_blank_values=True))
    return (query.get("utm_campaign") or None,
            query.get("utm_content") or None)


# --------------------------------------------------------------------------
# The join back to our own copy
# --------------------------------------------------------------------------

def _pairs_agree(seen: tuple[str | None, str | None],
                 stamped: tuple[str | None, str | None]) -> bool:
    """Does an ad's utm pair name the asset this stamped URL belongs to?

    utm_content carries the slot and the version ('ad-a-v5') and is what does
    the identifying, so it must be present on both sides and must match
    exactly. utm_campaign is checked only where both sides have one: it
    disambiguates two campaigns that each reached an 'ad-a-v5', and an ad whose
    tags carry the content but not the campaign is still a match rather than a
    mystery.
    """
    if not seen[1] or not stamped[1] or seen[1] != stamped[1]:
        return False
    if seen[0] and stamped[0] and seen[0] != stamped[0]:
        return False
    return True


def _rank(candidate: dict) -> tuple:
    """Approved first, then the newest version, then the latest decision.

    A slot can hold several rows -- v1 superseded, v2 approved, v3 drafted
    since -- and 013's partial unique index guarantees at most one of them is
    approved. The approved row is the copy that actually ran, so it wins; the
    rest of the key only decides between rows nobody signed off.

    Timestamps are compared as strings rather than as datetimes on purpose:
    the rows come from three nullable columns and a mix of aware and naive
    values would raise inside a sort, which is a ridiculous way to lose an
    import.
    """
    when = (candidate.get("approved_at") or candidate.get("updated_at")
            or candidate.get("created_at"))
    return (candidate.get("status") == "approved",
            candidate.get("version_number") or 0,
            (1, str(when)) if when is not None else (0, ""))


def match_asset(pairs, candidates) -> dict | None:
    """The campaign_asset one ad's links point at, or None.

    `pairs` is what parse_utm returned for each of the ad's destinations;
    `candidates` are campaign_assets rows carrying a stamped tracked_url.

    THE STAMPED URL IS PARSED, NEVER REBUILT. Recomputing the slug from the
    campaign's current name would be the obvious implementation and it breaks
    silently the first time a campaign is renamed: every ad approved under the
    old name stops matching, no error is raised, and the page reports that copy
    this engine wrote was written somewhere else. 025 stamped the URL at
    approval for exactly this reason.

    None is the ordinary answer, not a failure. Most ads in an account predate
    this system or were written elsewhere, and those are the ads the review
    stage exists for.
    """
    seen = [p for p in pairs if p and p[1]]
    if not seen:
        return None

    matched = [row for row in candidates or []
               if row.get("tracked_url")
               and any(_pairs_agree(pair, parse_utm(row["tracked_url"]))
                       for pair in seen)]
    return max(matched, key=_rank) if matched else None


# --------------------------------------------------------------------------
# The numbers
# --------------------------------------------------------------------------

def _int(value) -> int | None:
    """None, not 0, when Meta did not report the field.

    The two are different facts and the difference matters at the only place
    anyone looks: an ad with no impressions did not run, and an ad whose
    impressions are missing from the response is a pull that asked for the
    wrong fields. Defaulting both to 0 makes the second invisible.
    """
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _dec(value) -> Decimal | None:
    """Decimal via str, never float. Money is summed here and float is not."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _action_total(actions, wanted: tuple[str, ...]) -> int:
    total = 0
    for entry in actions or []:
        if isinstance(entry, dict) and entry.get("action_type") in wanted:
            total += _int(entry.get("value")) or 0
    return total


def _ranking(value) -> str | None:
    text = (value or "").strip().lower()
    return None if not text or text == UNGRADED else text


def _day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def insight_row(raw: dict, currency: str | None = None) -> dict:
    """One day of one ad, as the columns meta_ad_insights holds.

    `currency` comes from the account and is copied onto the row rather than
    joined to at read time, because an account's currency can be changed and a
    spend column whose unit is only knowable from today's account row silently
    re-denominates its own history.

    cost_per_lead is computed here rather than in SQL for the same reason the
    lead count is: both depend on LEAD_ACTION_TYPES, which is a judgement, and
    a generated column would make revising that judgement a migration instead
    of an edit and a replay from `raw`.
    """
    actions = raw.get("actions") or []
    leads = _action_total(actions, LEAD_ACTION_TYPES)
    spend = _dec(raw.get("spend"))

    # NULL and never 0 when there are no leads. A zero averages into a brand
    # median as a free lead and sorts an ad that produced nothing to the top of
    # a cheapest-CPL column. An ad with no leads has an undefined cost per
    # lead, and this is how that is spelled.
    cost_per_lead = None
    if leads > 0 and spend is not None:
        cost_per_lead = (spend / Decimal(leads)).quantize(
            Decimal("0.0001"), rounding=ROUND_HALF_UP)

    return {
        "ad_id": str(raw.get("ad_id") or "") or None,
        "date": _day(raw.get("date_start")),

        "impressions": _int(raw.get("impressions")),
        "reach": _int(raw.get("reach")),
        "frequency": _dec(raw.get("frequency")),
        "clicks": _int(raw.get("clicks")),
        "inline_link_clicks": _int(raw.get("inline_link_clicks")),

        "ctr": _dec(raw.get("ctr")),
        "inline_link_click_ctr": _dec(raw.get("inline_link_click_ctr")),
        "cpc": _dec(raw.get("cpc")),
        "cpm": _dec(raw.get("cpm")),
        "spend": spend,
        "currency": currency,

        "leads": leads,
        "landing_page_views": _action_total(actions,
                                            LANDING_PAGE_ACTION_TYPES) or None,
        # 0 until somebody decides what a booked call is in Meta terms. The
        # whole actions array is kept below so that decision reaches history.
        "booked": _action_total(actions, BOOKED_ACTION_TYPES),
        "cost_per_lead": cost_per_lead,

        "actions": actions or None,
        "cost_per_action": raw.get("cost_per_action_type") or None,

        "quality_ranking": _ranking(raw.get("quality_ranking")),
        "engagement_rate_ranking": _ranking(
            raw.get("engagement_rate_ranking")),
        "conversion_rate_ranking": _ranking(
            raw.get("conversion_rate_ranking")),

        "raw": raw,
    }
