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
    assert "6.5&times; its own baseline CPA" in html


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
