"""The ask box. No database: routing is a pure function and is tested as one.

The box is a widened surface -- it takes a sentence a person typed and turns
it into a call. What keeps that safe is not care at the call site, it is that
there is no call site to be careless at: a fixed dict of verbs, and no string
from the question ever reaches a cursor. These tests pin that, because the
tempting next commit is always "just let it pass through a filter".
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

import ask

ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------- the allowlist

def test_the_two_dangerous_verbs_are_absent():
    """record writes. live spends a rate limit. Neither belongs behind a box
    that invites typing."""
    assert "record" not in ask.VERBS
    assert "live" not in ask.VERBS
    for _, verb in ask.ROUTES:
        assert verb not in ("record", "live"), verb


def test_every_listed_verb_is_actually_wired():
    """A verb in the menu with no branch is a promise the box cannot keep --
    it would advertise the answer and then raise on it."""
    src = (ROOT / "ask.py").read_text(encoding="utf-8")
    body = src.split("async def answer", 1)[1]
    handled = set(re.findall(r'verb == "(\w+)"', body))
    assert not (set(ask.VERBS) - handled), sorted(set(ask.VERBS) - handled)


def test_every_route_target_is_on_the_menu():
    for _, verb in ask.ROUTES:
        assert verb in ask.VERBS, verb


def test_ask_holds_no_sql_and_no_write_pool():
    """The routing module must stay a router. The moment it grows a query of
    its own, the allowlist stops being the whole story."""
    tree = ast.parse((ROOT / "ask.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "db_owner" not in imported
    assert "db" not in imported, "ask.py should call verbs, not the pool"

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower().strip()
            assert not low.startswith(("select ", "insert ", "update ", "delete ")), node.value


# ---------------------------------------------------------------- routing

@pytest.mark.parametrize("question,expected", [
    ("why did CPA move", "why"),
    ("what moved cpa last week", "why"),
    ("what creatives are fatiguing", "fatigue"),
    ("anything tired?", "fatigue"),
    ("which angles have we never tested", "coverage"),
    ("what should we test next", "coverage"),
    ("which ads have no tag", "queue"),
    ("unmapped handles", "candidates"),
    ("what have we tested before", "experiments"),
    ("how is each angle doing", "angles"),
    ("is this data stale", "status"),
    ("cpa trend over time", "trend"),
    ("how are we doing on spend", "overview"),
])
def test_routes(question, expected):
    verb, _ = ask.route(question)
    assert verb == expected


def test_why_outranks_overview():
    """'why did CPA move' contains 'cpa', which the overview rule also
    matches. Order in ROUTES is the tie-break and this is what pins it."""
    assert ask.route("why did cpa move")[0] == "why"
    assert ask.route("what is our cpa")[0] == "overview"


def test_an_unmatched_question_is_refused_with_the_menu():
    with pytest.raises(ask.Unroutable) as exc:
        ask.route("book me a flight to lisbon")
    # The refusal has to say what it *could* have been asked, or the box is a
    # guessing game played against a hidden list.
    for verb in ("fatigue", "coverage", "status"):
        assert verb in str(exc.value)


def test_an_empty_question_is_refused_without_a_stack_trace():
    with pytest.raises(ask.Unroutable):
        ask.route("   ")


# ------------------------------------------------------------------ windows

@pytest.mark.parametrize("question,days", [
    ("fatigue last 14 days", 14),
    ("why 7d", 7),
    ("angles over 90 days", 90),
])
def test_a_window_in_the_question_is_used(question, days):
    assert ask.route(question)[1]["days"] == days


def test_an_absurd_window_is_clamped_not_refused():
    """The question was clear; only the number was silly. Clamping answers it;
    refusing makes the reader guess which part offended."""
    assert ask.route("fatigue last 999 days")[1]["days"] == 365
    assert ask.route("fatigue last 0 days")[1]["days"] == 1


def test_no_window_means_the_verb_picks_its_own_default():
    assert "days" not in ask.route("what is fatiguing")[1]


def test_trend_reads_the_metric_out_of_the_question():
    assert ask.route("trend over time")[1]["metric"] == "cpa"
    assert ask.route("cpm trend over time")[1]["metric"] == "cpm"


def test_link_ctr_is_not_swallowed_by_ctr():
    """Asking for link CTR and getting all-clicks CTR is the one substitution
    this repo warns about everywhere else; the router must not make it."""
    assert ask.route("link ctr trend")[1]["metric"] == "link_ctr"
    assert ask.route("ctr trend")[1]["metric"] == "ctr"
