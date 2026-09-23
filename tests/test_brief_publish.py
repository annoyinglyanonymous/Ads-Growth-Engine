"""The weekly brief writes its own prose, and the pipeline runs it.

`scripts/brief.py` published the cited readings and nothing else. The sentences
a person actually reads existed only as the response to POST
/brief/summary.json -- written when somebody pressed something, against numbers
that had moved since, and never kept. These tests pin the two halves of the
fix: the prompt has one home, and the follow-on chain runs the brief.

None of these need a database, a model, or the `claude` binary.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SYNC = (ROOT / "scripts" / "sync.py").read_text(encoding="utf-8")
BRIEF = (ROOT / "scripts" / "brief.py").read_text(encoding="utf-8")
UI = (ROOT / "ui.py").read_text(encoding="utf-8")
INTEL_BRIEF = (ROOT / "intel" / "brief.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# One prompt, one home.
# ---------------------------------------------------------------------------

def test_the_prompt_lives_in_exactly_one_place():
    """It was inline in a request handler, which is why the scheduled
    publisher could not reach it. Two copies drift, and the copy that drifts
    silently is the one nobody opens."""
    assert "BRIEF_SUMMARY_PROMPT = " in INTEL_BRIEF
    assert "BRIEF_SUMMARY_PROMPT = " not in UI, (
        "ui.py kept its own copy of the prompt")


def test_both_callers_go_through_the_same_helper():
    assert "brief_mod.summarise(" in UI
    assert "brief_mod.summarise(" in BRIEF


def test_intel_does_not_import_the_web_framework():
    """summarise() serialises the pack itself. Reaching for fastapi's
    jsonable_encoder would put the web framework behind `python -m intel` and
    behind a scheduled task that has no web process at all."""
    tree = ast.parse(INTEL_BRIEF)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "fastapi" not in names
    assert "starlette" not in names


def test_the_summary_is_asked_for_within_the_prompt_budget():
    """The question reaches the session as one argv element and Windows caps a
    command line at 32,767 characters. The ladder that shortens row lists is
    the same one /brief/summary.json used before the move."""
    body = INTEL_BRIEF.split("async def summarise", 1)[1]
    assert "for keep in (8, 3, 0)" in body
    assert "PROMPT_BUDGET" in body


# ---------------------------------------------------------------------------
# The prose cannot cost the archive.
# ---------------------------------------------------------------------------

def test_the_document_always_carries_a_summary_key():
    """Set before anything can fail, so a reader never has to ask whether the
    key is there -- only whether it is null. The page renders the readings
    alone in that case."""
    body = BRIEF.split("async def publish", 1)[1]
    assert 'doc["summary"] = None' in body
    assert body.index('doc["summary"] = None') < body.index("summarise(doc")


def test_a_failed_summary_still_publishes_the_brief():
    """A brief with readings and no summary is the document this script
    published all along. A brief that did not publish because a model call
    timed out is a week with no record at all."""
    body = BRIEF.split("async def publish", 1)[1].split("\ndef ", 1)[0]
    assert "except chat.ChatUnavailable" in body
    assert "except Exception as exc" in body
    # The write happens after the try, not inside it.
    assert body.index("except Exception as exc") < body.index("BRIEF_DIR.mkdir")


def test_the_summary_can_be_skipped():
    assert '"--no-summary"' in BRIEF


# ---------------------------------------------------------------------------
# The pipeline runs it.
# ---------------------------------------------------------------------------

def test_the_import_chains_tags_then_suggest_then_brief():
    """Tags first: the suggestion and the brief both read hook/offer/audience,
    and reading them before this run's new ads are labelled would describe the
    account as it was one import ago."""
    tag = SYNC.index('_run_after("tag.py"')
    suggest = SYNC.index('_run_after("suggest.py"')
    brief = SYNC.index('_run_after("brief.py"')
    assert tag < suggest < brief


def test_the_brief_runs_after_the_lock_is_released():
    """brief.py takes its own lock and makes a model call. Running it inside
    sync's lock would keep the next scheduled PULL out."""
    unlink = SYNC.find("LOCK.unlink(missing_ok=True)")
    call = SYNC.find('_run_after("brief.py"')
    assert unlink != -1 and call != -1 and unlink < call


def test_the_weekly_hold_is_not_logged_as_a_failure():
    """brief.py exits 3 on the six mornings a week when the week has not
    settled. Logging that as a failure is how a log stops being read."""
    call = SYNC.split('_run_after("brief.py"', 1)[1][:400]
    assert "benign=" in call and "3:" in call
    suggest = SYNC.split('_run_after("suggest.py"', 1)[1][:300]
    assert "benign=" in suggest and "3:" in suggest


def test_no_suggest_skips_every_model_call():
    """It has only ever meant "do not make model calls after the pull", and
    the brief now makes one. A flag that suppressed the suggestion and let the
    brief through would spend on the run that asked not to."""
    body = SYNC.split("if code == 0 and not a.no_suggest:", 1)[1]
    guarded = body.split("elif a.no_suggest:", 1)[0]
    for script in ('"tag.py"', '"suggest.py"', '"brief.py"'):
        assert script in guarded, f"{script} runs even with --no-suggest"


def test_the_brief_alone_can_be_skipped():
    assert '"--no-brief"' in SYNC
    assert "a.no_brief" in SYNC


def test_there_is_one_follow_on_runner():
    """It was three near-identical functions. The third would have been the
    one that quietly forgot the venv, or the timeout, or the exit code."""
    assert "def _suggest(" not in SYNC
    assert SYNC.count("def _run_after(") == 1


# ---------------------------------------------------------------------------
# Figures are written the way a person writes them.
# ---------------------------------------------------------------------------

def test_the_pack_is_formatted_before_the_model_sees_it():
    """The 2026-09-20 brief opened "You spent 15747.79 ... at 41.4416 each".
    Telling the model to round instead would argue with the same prompt's
    "quote figures exactly as they appear", and would disagree with the tiles:
    charts.money drops cents above a thousand, so the card says $15,748 while
    a model told to write dollars-and-cents says $15,747.79."""
    body = INTEL_BRIEF.split("async def summarise", 1)[1]
    assert "prose_mod.for_prose(payload)" in body
    assert body.index("for_prose") < body.index("BRIEF_SUMMARY_PROMPT.format")


def test_the_archive_keeps_full_precision():
    """Only the COPY handed over is formatted. A brief whose numbers were
    rounded for reading is one nobody can check afterwards, and checking them
    afterwards is the only way anyone finds out whether they were any good."""
    body = INTEL_BRIEF.split("async def summarise", 1)[1]
    assert 'doc["facts"] = ' not in body, "summarise() must not rewrite the doc"
    assert "payload = prose_mod.for_prose(payload)" in body


def test_money_and_percent_are_named_not_guessed():
    """cpa_change is dollars and attributable_share is a percentage, and no
    substring match on "change" or "share" gets that right."""
    from intel import prose
    assert "cpa_change" in prose.MONEY
    assert "attributable_share" in prose.PERCENT
    assert "rate_effect" in prose.MONEY and "mix_effect" in prose.MONEY
    assert not prose.MONEY & prose.PERCENT, "a key cannot be both"


def test_formatting_leaves_counts_and_text_alone():
    from intel import prose
    out = prose.for_prose({"spend": "15747.79", "cpa": 41.4416,
                           "link_ctr": 0.3271, "conversions": 380,
                           "ads_run": 26, "name": "Static | Drown",
                           "comparable_on_cost": False})
    assert out["spend"] == "$15,748"
    assert out["cpa"] == "$41.44"
    assert out["link_ctr"] == "0.33%"
    assert out["conversions"] == 380 and out["ads_run"] == 26
    assert out["name"] == "Static | Drown"
    assert out["comparable_on_cost"] is False, "a bool is not a number"


def test_formatting_survives_the_archive_round_trip():
    """A pack that has been through json.dumps(default=str) carries
    "15747.79" rather than Decimal("15747.79")."""
    from decimal import Decimal
    from intel import prose
    assert prose.for_prose({"spend": Decimal("15747.79")})["spend"] == "$15,748"
    assert prose.for_prose({"spend": "15747.79"})["spend"] == "$15,748"


# ---------------------------------------------------------------------------
# Health: a failure a later success answered is not a problem.
# ---------------------------------------------------------------------------

def test_health_ignores_failures_a_later_success_answered():
    """The structure pull failed nine times over 21-22 September and succeeded
    on the 23rd. `intel status` still said healthy: false, and the brief opened
    with "the ad-list pull has now failed ten times"."""
    src = (ROOT / "intel" / "health.py").read_text(encoding="utf-8")
    assert "unresolved" in src
    sql = src.split("unresolved = await fetch_all", 1)[1][:900]
    assert "not exists" in sql
    assert "ok.kind       = p.kind" in sql, (
        "structure and insights fail independently; a successful insights "
        "pull says nothing about whether the ad list imported")
    assert "ok.started_at > p.started_at" in sql


def test_the_history_is_still_reported():
    """recent_failures stays as it was -- the fix narrows what `healthy` is
    computed from, it does not hide the history."""
    src = (ROOT / "intel" / "health.py").read_text(encoding="utf-8")
    assert '"recent_failures": failures,' in src
    assert '"unresolved_failures": unresolved,' in src
    assert "if unresolved:" in src, "the verdict must read the narrowed list"
