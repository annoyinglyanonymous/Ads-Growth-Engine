"""A failed session says one sentence. It never prints its own envelope.

THE BUG THIS FILE EXISTS FOR

Asked "What ads creatives can be improved?", the ask box rendered a 900-
character wall of JSON -- session id, token counts, cache hits, a permission
denial, `"is_error":true` -- and ended with "$0.39". A reader got the receipt
instead of the answer.

The CLI had exited ZERO. `answer()` only inspected the payload when the exit
code was non-zero, so a clean exit carrying `is_error: true` and no `result`
key fell through to `payload.get("result") or payload.get("text") or text`,
and `text` is the whole envelope.

None of these need a database, a model, or the `claude` binary: `_run` is
stubbed with the envelope the real failure produced.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

import chat

ROOT = Path(__file__).resolve().parent.parent


class _Done:
    """What subprocess.run hands back. answer()'s inner _run reads exactly
    these three attributes."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _answer(monkeypatch, payload: dict, code: int = 0, stderr: bytes = b""):
    """Run answer() against a canned CLI result.

    subprocess.run is what gets stubbed, not asyncio.to_thread: `_run` is a
    closure inside answer() and there is no seam above it. The thread hop is
    left real so the code under test takes the path it takes in production.
    """
    def fake_subprocess_run(*a, **k):
        return _Done(code, json.dumps(payload).encode("utf-8"), stderr)

    monkeypatch.setattr(chat, "available", lambda: True)
    monkeypatch.setattr(chat.subprocess, "run", fake_subprocess_run)
    return asyncio.run(chat.answer("what creatives can be improved", "renegade"))


#: The envelope the live failure produced, trimmed to the keys that matter.
FAILED = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "num_turns": 8,
    "duration_ms": 48087,
    "session_id": "ecc8fedb-ad17-452a-bf12-ec6edab0acee",
    "total_cost_usd": 0.385183,
    "stop_reason": "tool_use",
    "permission_denials": [
        {"tool_name": "Bash", "tool_use_id": "toolu_013D",
         "tool_input": {"command": "python - <<'EOF'\\nimport json\\n"}}],
    "errors": ["[ede_diagnostic] result_type=user stop_reason=tool_use"],
    # NOTE: no "result" key at all. That absence is the bug.
}


def test_a_failed_run_that_exited_zero_is_not_reported_as_an_answer(monkeypatch):
    out = _answer(monkeypatch, FAILED)
    assert out["ok"] is False
    assert out["answer"] is None


def test_the_envelope_never_reaches_the_reader(monkeypatch):
    """The specific regression: no JSON, no session id, no token counts."""
    out = _answer(monkeypatch, FAILED)
    blob = json.dumps(out)
    for leak in ("session_id", "total_cost_usd", "cache_read_input_tokens",
                 "ecc8fedb", "is_error", "permission_denials", "modelUsage"):
        assert leak not in blob, f"{leak!r} leaked into what the page renders"


def test_a_denied_tool_is_named(monkeypatch):
    """"Something went wrong" sends somebody to the logs. Naming the tool says
    the session wanted a command it is not allowed, which is a decision rather
    than a fault."""
    out = _answer(monkeypatch, FAILED)
    assert "Bash" in out["error"]
    assert "8 steps" in out["error"]


def test_a_partial_answer_survives_a_failed_run(monkeypatch):
    """It failed on the way out but had already said something useful. Throwing
    that away to report the failure would be a worse trade."""
    payload = dict(FAILED)
    payload["result"] = "Three ads are tiring; the cheapest all open on a task."
    out = _answer(monkeypatch, payload)
    assert out["ok"] is True
    assert out["answer"].startswith("Three ads are tiring")


def test_an_empty_reply_is_a_failure_not_a_blank_box(monkeypatch):
    """Returning "" renders as the model having nothing to say, rather than as
    something having broken."""
    for empty in ("", "   ", None):
        out = _answer(monkeypatch, {"type": "result", "result": empty})
        assert out["ok"] is False, f"{empty!r} was reported as an answer"
        assert out["answer"] is None


def test_a_clean_run_still_works(monkeypatch):
    out = _answer(monkeypatch, {
        "type": "result", "is_error": False, "num_turns": 3,
        "result": "Spend was $60,163 across 28 days.",
        "total_cost_usd": 0.12, "duration_ms": 9000})
    assert out["ok"] is True
    assert out["answer"] == "Spend was $60,163 across 28 days."


# ---------------------------------------------------------------------------
# The allowlist.
# ---------------------------------------------------------------------------

def test_propose_is_askable():
    """"What creatives can be improved" IS propose's job. Leaving it off the
    list did not make the box safer -- it made the session reach for Bash to
    rebuild propose out of raw fatigue rows, which is the tool that actually
    is dangerous."""
    assert "propose" in chat.READ_VERBS
    assert any("intel propose" in entry for entry in chat._allowed())


def test_the_two_verbs_that_stay_off_are_still_off():
    """record writes. live spends the Meta rate limit one keystroke at a
    time. Neither reason has changed."""
    assert "record" not in chat.READ_VERBS
    assert "live" not in chat.READ_VERBS
    denied = " ".join(chat._denied())
    assert "record" in denied


def test_every_allowed_verb_actually_exists():
    """A verb on the list that argparse does not accept is a session that
    learns its own allowlist is wrong, one wasted turn at a time."""
    import ast
    tree = ast.parse((ROOT / "intel" / "__main__.py").read_text(encoding="utf-8"))
    parsers = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_parser"
        and node.args and isinstance(node.args[0], ast.Constant)
    }
    # `brandish`/`windowed` wrap add_parser, so also pick up their call sites.
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in {"brandish", "windowed"}
                and node.args and isinstance(node.args[0], ast.Constant)):
            parsers.add(node.args[0].value)
    missing = sorted(set(chat.READ_VERBS) - parsers)
    assert not missing, f"allowed but not a verb: {missing}"
