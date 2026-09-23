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
