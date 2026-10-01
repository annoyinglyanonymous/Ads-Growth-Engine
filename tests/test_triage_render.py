"""templates/_triage.html, rendered against published files of every shape.

The fixtures in tests/fixtures/suggestions/ are synthetic -- no real campaign
or ad copy -- and cover the three shapes a published file can have: plain
`changes` (before 2026-09-24), `problems` with their fixes, and the analyst
report (`issues`, shape 2). An old file must keep rendering after the report
lands: nothing rewrites the files already on disk.
"""
import copy
import json
import re
from pathlib import Path

import pytest

import ui

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "suggestions"


def _load(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


def _render(published: dict) -> str:
    return ui.templates.env.get_template("_triage.html").render(published=published)


@pytest.mark.parametrize("name", ["old_changes", "old_problems", "report"])
def test_every_shape_renders_folded(name):
    html = _render(_load(name))
    assert '<details class="tgroup' in html
    assert "<details open" not in html, "a group or campaign starts open"
    assert not re.search(r"<script|<button", html, re.I)
    for c in _load(name)["triage"]:
        assert c["campaign"] in html
    # Every campaign is its own fold inside its group.
    assert html.count('<details class="tcamp">') == len(_load(name)["triage"])


def test_old_changes_render_as_fixes():
    html = _render(_load("old_changes"))
    assert "Pause the weakest ad in the main ad group." in html
    assert "How to fix it" in html
    assert "Not rated" in html, "an unrated campaign went missing"


def test_old_problems_render_with_their_fixes():
    html = _render(_load("old_problems"))
    assert "How to fix it" in html
    assert "The lead ad&#39;s click-through fell for two weeks." in html
    assert "still open since 2026-09-23" in html
    assert "Since the last review" in html
    assert "1 still open" in html and "2 new" in html
    assert "Resolved since the last review" in html
    assert "not confirmed fixed" in html
    assert "Not found in the data" in html


SECTIONS = ["What changed", "What's causing it", "Where it's concentrated",
            "Issues", "What not to do yet"]


def test_the_report_renders_every_section_in_order():
    html = _render(_load("report"))
    alpha = html[html.index("Campaign Alpha</span>"):html.index("Campaign Beta</span>")]
    at = [alpha.index(f'<h4 class="tsec">{s}') for s in SECTIONS]
    assert at == sorted(at), "the report's sections are out of order"
    assert "How to fix it" not in html, "a report fell through to the legacy card"


def test_the_provisional_row_is_marked_once_per_campaign():
    pub = _load("report")
    html = _render(pub)
    with_ladder = [c for c in pub["triage"] if c.get("ladder")]
    assert html.count('<span class="tag">provisional</span>') == len(with_ladder)
    assert html.count('class="prov"') == len(with_ladder)
    assert "Provisional: Meta can still restate the last three days." in html


def test_the_funnel_says_which_way_each_change_went():
    html = _render(_load("report"))
    assert "+61.5%" in html and "−32.4%" in html
    # CPA up is worse; CVR down is worse; CPM up is worse; frequency has no word.
    cpa_row = html[html.index("<td>CPA</td>"):]
    cpa_row = cpa_row[:cpa_row.index("</tr>")]
    assert "worse" in cpa_row
    freq_row = html[html.index("<td>Daily frequency</td>"):]
    freq_row = freq_row[:freq_row.index("</tr>")]
    assert "better" not in freq_row and "worse" not in freq_row
    assert "Share of the CPA change" in html
    assert re.search(r'chip-d primary">Post-click \(CVR\)\s*85%', html)
    assert "Evidence: <b>moderate</b>" in html
    # Spend over the prior CPA is a count of conversions it would have bought,
    # and is worded as one; "6.5x its baseline CPA" read as an alarm.
    assert re.search(r"would have bought\s+6\.5 conversions", html)
    assert "its own baseline CPA" not in html


def test_daily_frequency_shows_only_when_a_rung_has_it():
    pub = _load("report")
    camp = next(c for c in pub["triage"] if c.get("ladder"))
    for w in camp["ladder"]:
        w.pop("daily_frequency", None)
    pub["triage"] = [camp]
    assert "Daily freq</th>" not in _render(pub)
    camp["ladder"][1]["daily_frequency"] = 1.12
    assert "Daily freq</th>" in _render(pub)


def test_the_issues_carry_their_chips_and_test_plan():
    html = _render(_load("report"))
    for chip in ("ACT", "TEST", "MONITOR"):
        assert f">{chip}</span>" in html, chip
    for label in ("Primary", "Secondary", "Monitor"):
        assert f'<div class="prio">{label}</div>' in html
    assert "Likely cause (hypothesis)" in html
    assert "<b>Control</b>" in html and "<b>Challenger</b>" in html
    assert "audience · budget · creative" in html
    assert re.search(r"The review proposed ACT; the\s+evidence supports TEST\.", html)
    assert re.search(r"gave this medium\s+confidence; the evidence supports low", html)
    assert "test plan was dropped: the evidence does not support a test yet" in html
    assert "Do not raise the budget until CVR recovers." in html
    assert "ACT / TEST / MONITOR is what the evidence supports, not a decision." in html


def test_the_report_counts_issues_since_the_last_review():
    html = _render(_load("report"))
    assert "1 still open" in html
    assert "3 new" in html  # two on Alpha, one on Beta


def test_a_missing_where_falls_back_to_the_spread_sentence():
    html = _render(_load("report"))
    beta = html[html.index("Campaign Beta</span>"):]
    assert "91.0% of the spend" in beta
    assert "Steady, with too little data to call a change." in beta, (
        "an empty headline should fall back to why")


def test_a_report_without_a_funnel_still_renders():
    pub = _load("report")
    alpha = pub["triage"][0]
    for k in ("funnel", "where", "ladder"):
        alpha.pop(k)
    html = _render(pub)
    assert "What's causing it" not in html
    assert "Conversion rate after the click fell" in html


def test_a_report_with_nothing_but_issues_renders():
    pub = copy.deepcopy(_load("report"))
    pub["triage"] = [{"campaign": "Bare", "rating": "red", "issues": [
        {"observation": "o", "recommendation": None, "confidence": None}]}]
    html = _render(pub)
    assert "Bare" in html and "Unranked" in html


# ---------------------------------------------------------------------------
# Round 2 (contracts2 D4): buckets, movement, folded issues, asset vs new,
# outcomes, and the two panels above the groups on /suggestions.
# report_v3.json is synthetic, like the other fixtures.
# ---------------------------------------------------------------------------

def _between(html: str, start: str, end: str | None = None) -> str:
    a = html.index(start)
    return html[a:html.index(end, a)] if end else html[a:]


def test_v3_still_folds_and_renders_every_campaign():
    pub = _load("report_v3")
    html = _render(pub)
    assert "<details open" not in html
    assert not re.search(r"<script|<button", html, re.I)
    assert html.count('<details class="tcamp">') == len(pub["triage"])
    for c in pub["triage"]:
        assert c["campaign"] in html


def test_buckets_group_in_order_act_test_monitor_no_change():
    html = _render(_load("report_v3"))
    order = re.findall(r'<details class="tgroup (\w+)" data-bucket="(\w+)"', html)
    assert order == [("red", "act"), ("yellow", "test"),
                     ("none", "monitor"), ("green", "no_change")]
    # Inside a group, the order given (integration sorts by impact).
    act = _between(html, 'data-bucket="act"', 'data-bucket="test"')
    assert act.index('class="ttl">Campaign Delta</span>') < act.index('class="ttl">Campaign Hotel</span>')
    # No rating group is drawn when the entries carry buckets.
    assert "Critical" not in html and "Needs changes" not in html


def test_an_entry_without_a_bucket_still_lands_somewhere():
    pub = _load("report_v3")
    pub["triage"].append({"campaign": "Unsorted One", "rating": "red", "issues": []})
    html = _render(pub)
    assert 'data-bucket="none"' in html and "Unsorted One" in html


def test_the_folded_line_carries_bucket_summary_and_movement():
    html = _render(_load("report_v3"))
    delta = _between(html, 'class="ttl">Campaign Delta</span>', "</summary>")
    assert "CPA +22%. Lower conversion rate after the click appears" in delta
    m7 = _between(delta, 'class="mv-row m7"', 'class="mv-row m14"')
    for label, value, word in (("CPA", "+22.4%", "worse"), ("Link CTR", "−18.3%", "worse"),
                               ("CPC", "+9.1%", "worse"), ("CVR", "−4.2%", "worse")):
        assert re.search(rf'<span class="mv {word}"><span class="ml">{label}</span>'
                         rf'<b>{re.escape(value)}</b><span class="mw">{word}</span>', m7), label
    m14 = _between(delta, 'class="mv-row m14"')
    # CVR up is better, by sign alone.
    assert re.search(r'mv better"><span class="ml">CVR</span><b>\+1\.2%</b>', m14)
    # Figures are kept, small, after the movement.
    assert delta.index('class="mv-row m7"') < delta.index('class="figs v3"')
    golf = _between(html, 'class="ttl">Campaign Golf</span>', "</summary>")
    assert re.search(r'mv better"><span class="ml">CPA</span><b>−12\.0%</b>', golf)
    assert "mv-row m14" not in golf, "a missing 14-day comparison drew a row"


def test_a_missing_summary_falls_back_to_the_headline():
    html = _render(_load("report_v3"))
    fox = _between(html, 'class="ttl">Campaign Foxtrot</span>', "</summary>")
    assert "Too few conversions to read a trend." in fox
    # A null metric is left out, not printed as "--".
    assert "Link CTR" not in fox


def test_the_v3_sections_are_renamed_and_in_order():
    html = _render(_load("report_v3"))
    delta = _between(html, 'class="ttl">Campaign Delta</span>', 'class="ttl">Campaign Hotel</span>')
    names = ["What changed", "Where the change came from", "Where it's concentrated",
             "Issues", "What not to do yet"]
    at = [delta.index(f'<h4 class="tsec">{s}') for s in names]
    assert at == sorted(at)
    assert "What's causing it" not in html
    assert re.search(r'chip-d primary">Post-click \(CVR\) — appears to be the largest'
                     r' contributor\s+· 79%', delta)
    assert re.search(r'<span class="chip-d">Click-through \(CTR\)\s+12%', delta)


def test_causal_and_unverified_notes_show():
    html = _render(_load("report_v3"))
    assert "Causal wording flagged: due to." in html
    assert "Not found in the data:" in html and "$999.00" in html
    # The rating is internal in round 2: its note would explain a hidden label.
    assert "The review rated this" not in html


def test_each_issue_is_its_own_closed_fold():
    pub = _load("report_v3")
    html = _render(pub)
    n = sum(len(c["issues"]) for c in pub["triage"])
    assert html.count('<details class="issue">') == n
    first = _between(html, '<details class="issue">', "</summary>")
    assert '<span class="plabel">Primary</span>' in first
    assert ">ACT</span>" in first
    assert 'class="dots"' in first and "Medium confidence" in first
    assert "Pause Ad D2 and let the budget move to Ad D1." in first
    body = _between(html, '<details class="issue">', "</details>")
    for label in ("Observation", "Evidence", "Likely cause (hypothesis)",
                  "Expected outcome", "Confidence reason"):
        assert f"<dt>{label}</dt>" in body, label


def test_the_outcome_tag_reads_the_ledger():
    html = _render(_load("report_v3"))
    assert "implemented 2026-10-03 (likely) · measuring, 4 settled days" in html
    assert "implemented 2026-09-22 (strong) · measured: improved" in html
    assert '<span class="otag">not yet seen in the account</span>' in html


def test_existing_asset_and_new_creative_are_labelled_blocks():
    html = _render(_load("report_v3"))
    asset = _between(html, "EXISTING ASSET TO TEST", "</div></div>")
    assert '<div class="cad">Ad D3</div>' in asset
    assert "Its question-first opener" in asset
    new = _between(html, "NEW CREATIVE NEEDED", "</dl>")
    for k, v in (("Format", "15-second video"), ("Angle or hook", "problem"),
                 ("Built from", "the problem hook")):
        assert re.search(rf"<dt>{k}</dt><dd>{re.escape(v)}", new), k
    assert "Still paying for cover you never use?" in new
    assert re.search(r"The review proposed an existing asset, but no ad of that name"
                     r" is in this goal's pool;\s+it is shown as new creative needed\.", html)


def test_an_incomplete_issue_says_what_is_missing():
    html = _render(_load("report_v3"))
    assert re.search(r"Missing: evidence, test plan;\s+shown as monitor\.", html)
    assert '<span class="itag">incomplete</span>' in html


def test_a_no_change_campaign_says_so():
    html = _render(_load("report_v3"))
    assert "No issue raised" in _between(html, 'class="ttl">Campaign Golf</span>')


@pytest.mark.parametrize("name", ["old_changes", "old_problems", "report"])
def test_older_files_never_reach_the_v3_markup(name):
    html = _render(_load(name))
    for v3 in ("data-bucket", 'class="mv-row', '<details class="issue">',
               "Where the change came from", 'class="otag"'):
        assert v3 not in html, v3


# --- the page around it ----------------------------------------------------

from types import SimpleNamespace


def _row(value, spend, conv, cpa, rel, pct, ev):
    return {"optimization_goal": "OFFSITE_CONVERSIONS", "value": value, "ads_run": 2,
            "spend": spend, "conversions": conv, "rates": {"cpa": cpa}, "goal_cpa": 80.0,
            "relative_pct": pct, "relative": rel, "evidence_level": ev,
            "spend_share_pct": 40.0}


def _page(published, *, goal=None, by_goal=True):
    """suggestions.html rendered whole, against a hand-built pack. The route
    builds the pack from the database; the template only reads it."""
    d = {"brand": "sample", "days": 28, "until": "2026-10-04",
         "settled_through": "2026-10-01",
         "goals": [{"optimization_goal": g, "ads_run": 2, "rankable_on_cost": True,
                    "cheapest": [], "dearest": []}
                   for g in ("OFFSITE_CONVERSIONS", "LINK_CLICKS")],
         "dimensions": {"hook": [{"value": "mixed-goal hook", "spend": 10.0,
                                  "conversions": 1}]}}
    if by_goal:
        d["dimensions_by_goal"] = {"OFFSITE_CONVERSIONS": {
            "hook": [_row("problem", 900.0, 12, 75.0, "baseline", -6.25, "directional"),
                     _row("question", 300.0, 2, 150.0, "worse", 87.5, "insufficient")],
            "offer": [_row("free quote", 800.0, 60, 13.33, "better", -83.34, "strong")],
            "audience": []}}
    req = SimpleNamespace(query_params={"goal": goal} if goal else {})
    return ui.templates.env.get_template("suggestions.html").render(
        request=req, d=d, published=published, nav="suggestions", brand="sample",
        settled="2026-10-01", caveat=None, until=None, today="2026-10-05", days=28)


def test_the_page_puts_what_matters_and_the_scorecard_above_the_groups():
    html = _page(_load("report_v3"))
    today = html.index('aria-label="What matters today"')
    followed = html.index('aria-label="Recommendations followed up"')
    groups = html.index('<details class="tgroup')
    assert today < followed < groups
    panel = _between(html, 'aria-label="What matters today"', "</section>")
    assert "Offsite conversions" in panel and "CPA +18.2%" in panel
    assert "2 campaigns account for 68.4% of that rise." in panel
    assert "Campaign Delta" in panel and "41.5% of the change" in panel
    assert "2 have issues with low current impact." in panel
    assert "Most of the rise sits in two conversion campaigns" in panel
    card = _between(html, 'aria-label="Recommendations followed up"', "</section>")
    assert re.search(r"<b>6</b><span>made</span>", card)
    assert re.search(r"<b>3</b><span>implemented</span>", card)
    assert re.search(r"<b>1</b><span>improved</span>", card)
    assert re.search(r"<b>2</b><span>too early to read</span>", card)
    assert "<td>Creative test</td>" in card and "<td>Delivery cut</td>" in card
    assert "Campaign Echo" in card and "improved</b> after 9 settled days" in card
    read = _between(html, '<section class="ai-read"', '<section class="stage"')
    assert "<details open" not in read and not re.search(r"<script|<button", read, re.I)


def test_the_scorecard_empty_state_and_absent_panels():
    pub = _load("report_v3")
    pub["outcomes"] = {"available": True,
                       "scorecard": {"made": 2, "implemented": 0, "by_result": {},
                                     "by_kind": {}, "recent": []}}
    html = _page(pub)
    assert "No recommendation has been implemented yet." in html
    assert "2 act or test recommendation(s) made." in html
    pub.pop("outcomes")
    pub.pop("account_summary")
    html = _page(pub)
    assert 'aria-label="What matters today"' not in html
    assert 'aria-label="Recommendations followed up"' not in html
    assert '<details class="tgroup' in html


def test_an_old_file_renders_the_page_without_the_new_panels():
    html = _page(_load("report"), by_goal=False)
    assert 'aria-label="What matters today"' not in html
    assert "Critical" in html
    # No per-goal tables in the pack: the old mixed-goal panels stay.
    assert "mixed-goal hook" in html and "ads.facet_by_goal" not in html


def test_the_label_tables_sit_inside_the_goal_tab_with_relative():
    html = _page(_load("report_v3"))
    dims = _between(html, '<div class="goal-dims"')
    assert "under offsite conversions only" in dims
    assert "<th>Relative</th>" in dims and '<th class="num">CPA</th>' in dims
    hook = _between(dims, 'aria-label="Hook it opens with"', "</section>")
    assert re.search(r'rel baseline">baseline</span> <span class="small">−6\.2%', hook)
    assert re.search(r'rel worse">worse</span> <span class="small">\+87\.5%', hook)
    assert '<span class="ev">directional</span>' in hook
    assert '<span class="ev">insufficient</span>' in hook
    offer = _between(dims, 'aria-label="Offer it asks for"', "</section>")
    assert "better" in offer and '<span class="ev">' not in offer
    assert "No ad under this goal carries this label yet." in dims
    # The mixed-goal panels give way when the goal has its own, and the
    # tables sit under the goal tabs.
    assert "mixed-goal hook" not in html
    assert html.index('class="tabs"') < html.index('<div class="goal-dims"')


def test_a_goal_tab_without_its_own_tables_falls_back():
    html = _page(_load("report_v3"), goal="LINK_CLICKS")
    assert '<div class="goal-dims"' not in html
    assert "mixed-goal hook" in html
