"""The tagging pass files nothing it cannot justify, and nothing it invented.

`scripts/tag.py` labels what each ad ARGUES, and those labels become every
creative number on the site. A label the model imagined is not a harmless
wrong row -- `hook` and `offer` are foreign keys, so an invented value is a row
the database refuses, and a plausible-but-wrong one is a number somebody
compares against.

So the pass checks every value against the seeded vocabulary before it goes
near intel/record.py, and these are the tests for that check.

None of these need a database, a model, or the `claude` binary.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# scripts/ is not a package, so load tag.py by path.
_spec = importlib.util.spec_from_file_location("tag_script", ROOT / "scripts" / "tag.py")
tag = importlib.util.module_from_spec(_spec)
sys.modules["tag_script"] = tag
_spec.loader.exec_module(tag)


HOOKS = {"callout", "contrast", "demo", "pattern_interrupt", "question",
         "stat", "story", "testimonial"}
OFFERS = {"call", "demo", "free_consult", "guide", "none", "quote", "valuation"}

AD = {"ad_key": "11111111-1111-1111-1111-111111111111",
      "copy_hash": "abc123", "brand": "renegade",
      "first_headline": "Own the book you've been building",
      "first_body": "A good year in captive means a good paycheck."}
BY_KEY = {AD["ad_key"]: AD}


def _clean(**over):
    row = {"ad_key": AD["ad_key"], "hook": "callout", "offer": "call",
           "audience": "captive agents", "confidence": "inferred",
           "rationale": 'Opens "Own the book you have been building".'}
    row.update(over)
    return tag.clean(row, HOOKS, OFFERS, BY_KEY)


# ---------------------------------------------------------------------------
# Nothing outside the vocabulary reaches the database.
# ---------------------------------------------------------------------------

def test_a_good_row_becomes_a_filable_payload():
    payload, why = _clean()
    assert why is None
    assert payload["hook"] == "callout"
    assert payload["copy_hash"] == "abc123"
    assert payload["brand"] == "renegade"


def test_an_invented_hook_is_dropped_with_a_reason():
    """`hook` is a foreign key to ads.hook. A value the model imagined is a row
    the database refuses -- and a pass that files nothing because one label was
    invented is worse than one that drops the row and says which."""
    payload, why = _clean(hook="urgency")
    assert payload is None
    assert "urgency" in why and "ads.hook" in why


def test_an_invented_offer_is_dropped_with_a_reason():
    payload, why = _clean(offer="webinar")
    assert payload is None
    assert "webinar" in why and "ads.offer" in why


def test_an_unknown_ad_key_is_dropped():
    """The model returning a key that was not in the batch means it has lost
    track of which ad it is labelling, and the label belongs to nothing."""
    payload, why = _clean(ad_key="99999999-9999-9999-9999-999999999999")
    assert payload is None
    assert "not in the batch" in why


@pytest.mark.parametrize("bad", ["certain", "high", "", None, "STATED"])
def test_confidence_must_be_one_of_the_two(bad):
    payload, why = _clean(confidence=bad)
    assert payload is None
    assert "confidence" in why


def test_a_tag_without_a_rationale_is_dropped():
    """The rationale is what a person reads when checking the work. A label
    with no reason behind it cannot be checked, only believed."""
    for empty in ("", "   ", None):
        payload, why = _clean(rationale=empty)
        assert payload is None
        assert "rationale" in why or "cannot be checked" in why


def test_a_row_that_labels_nothing_is_dropped():
    """A facet with no hook, no offer and no audience describes nothing. Filing
    it would only stop the ad appearing in this pass's own selection next
    time -- it would look tagged and say nothing."""
    payload, why = _clean(hook=None, offer=None, audience=None)
    assert payload is None
    assert "nothing was labelled" in why


def test_a_partial_label_is_still_worth_filing():
    """A hook with no offer is a real finding. Demanding all three would throw
    away the labels the copy does support."""
    payload, why = _clean(offer=None, audience=None)
    assert why is None
    assert payload["hook"] == "callout"
    assert "offer" not in payload and "audience" not in payload


def test_the_copy_hash_comes_from_the_ad_not_the_model():
    """The hash of the copy AS READ. If the model echoed one back, an ad
    rewritten between the read and the write would have its new wording
    labelled with the old wording's tag."""
    payload, why = _clean(copy_hash="something-the-model-made-up")
    assert why is None
    assert payload["copy_hash"] == "abc123"


def test_a_long_rationale_is_cut_not_rejected():
    payload, why = _clean(rationale="x" * 900)
    assert why is None
    assert len(payload["rationale"]) <= 500


# ---------------------------------------------------------------------------
# The pass cannot decide anything.
# ---------------------------------------------------------------------------

def test_the_pass_cannot_set_its_own_source():
    """`source` is a SQL literal in intel/record.py -- 'tagged', never a
    parameter. There is no value this pass can supply that makes a row look
    like a person filed it, and record.py's upsert refuses to overwrite one
    that was."""
    src = (ROOT / "scripts" / "tag.py").read_text(encoding="utf-8")
    assert '"source"' not in src and "'source'" not in src
    record = (ROOT / "intel" / "record.py").read_text(encoding="utf-8")
    assert "'tagged'" in record
    assert "where ads.ad_facet.source <> 'operator'" in record


def test_the_pass_does_not_open_the_writing_pool():
    """It goes through intel/record.py like everything else. Importing
    db_owner here would put the writing credential in a script that also talks
    to a model."""
    tree = ast.parse((ROOT / "scripts" / "tag.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    assert "db_owner" not in names
    assert "db_meta" not in names


def test_classification_is_asked_with_no_tools_at_all():
    """A pass is handed the copy and asked to label it. Every tool is a grant
    with no purpose, and a grant with no purpose is the one that gets used for
    something nobody intended."""
    import chat
    src = (ROOT / "chat.py").read_text(encoding="utf-8")
    body = src.split("async def classify", 1)[1].split("\nclass ", 1)[0]
    # Quoted, so the comment above the command ("No --allowedTools at all")
    # does not satisfy the assertion the way a bare substring did.
    assert '"--allowedTools"' not in body, "the classifier was granted tools"
    assert "--disallowedTools" in body, "the deny list is not applied"
    assert "--max-turns" in body
    assert chat.CLASSIFY_SYSTEM is not chat.SYSTEM


def test_the_selection_is_keyed_on_the_copy_hash():
    """What makes the pass cheap after its first run, and correct when copy
    changes: rewrite an ad and its old tag stops matching, so it comes back
    round to be relabelled."""
    src = (ROOT / "scripts" / "tag.py").read_text(encoding="utf-8")
    sel = src.split("async def needs_tagging", 1)[1].split("\ndef ", 1)[0]
    assert "fa.copy_hash = c.copy_hash" in sel
    assert "not exists" in sel


def test_batches_stay_inside_the_prompt_budget():
    """The question reaches the session as one argv element and Windows caps a
    command line at 32,767 characters."""
    from intel import creative
    hooks = [{"slug": h, "definition": "d" * 60} for h in sorted(HOOKS)]
    offers = [{"slug": o, "definition": "d" * 60} for o in sorted(OFFERS)]
    ads = [{"ad_key": f"{i:08d}-1111-1111-1111-111111111111",
            "name": "Become an Agent | Start Franchise | Multi Color | Video",
            "first_headline": "Own Your Own P&C Insurance Franchise",
            "first_body": "You already know how to sell. " * 14,
            "cta": "LEARN_MORE"} for i in range(tag.BATCH)]
    # 420-character bodies: forty of these do NOT fit, which is the point.
    # The planner has to split them rather than emit one oversized prompt.
    for batch in tag.plan_batches("renegade", hooks, offers, ads):
        prompt = tag.build_prompt("renegade", hooks, offers, batch)
        assert len(prompt) <= creative.PROMPT_BUDGET, (
            f"a batch of {len(batch)} came to {len(prompt)} chars")
    assert len(tag.plan_batches("renegade", hooks, offers, ads)) > 1, (
        "forty long-bodied ads should have been split")


def test_the_pass_names_its_exit_codes():
    src = (ROOT / "scripts" / "tag.py").read_text(encoding="utf-8")
    for code in ("0  filed", "1  something failed", "2  a run was already"):
        assert code in src, f"exit code {code!r} is undocumented"
