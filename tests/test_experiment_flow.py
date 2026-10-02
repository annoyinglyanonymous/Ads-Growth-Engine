"""Recommended test -> tagged launch -> experiment filed -> person confirms.

The report and the experiment log are one flow. The outcomes ledger sees a
test's tagged ad launch (tests/test_outcomes.py covers the match); this file
covers what follows: scripts/suggest.py files the experiment as a proposal
through `intel record` in a separate process (stubbed here -- no database),
once, and again next run if it failed; /experiments shows the detected launch
with the statement a person runs; and the one-off statement that closes the
2026-09-28 proposals is guarded. No database, no subprocess actually run.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from intel import experiments as exp_mod
from intel import outcomes, shapes

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 10)


def _suggest():
    spec = importlib.util.spec_from_file_location(
        "suggest_script", ROOT / "scripts" / "suggest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["suggest_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _entry(iid="i1", **extra):
    e = {"id": iid, "campaign": "Camp A", "ad_group": "Group 1", "goal": None,
         "type": "test", "kind": "creative_test", "variable": "creative",
         "control": "Control Ad", "challenger": 'a new video, "Fresh Hook"',
         "control_ad_key": "k-control", "challenger_ad_key": "k-new",
         "decision_spend_per_arm": 250.0, "tag": f"EXP-{iid}",
         "setup": "Duplicate the ad.", "recommended_on": "2026-10-01",
         "status": "implemented", "implemented_on": "2026-10-03",
         "match": "strong", "matched_by": f"tagged utm_content=EXP-{iid}",
         "pre_registered": {"observation": "CPA rose.",
                            "diagnosis": "The hook appears tired.",
                            "expected_outcome": "Link CTR recovers."},
         "experiment": None, "measurement": None, "reason": None}
    e.update(extra)
    return e


class _Proc:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


@pytest.fixture
def suggest(tmp_path, monkeypatch):
    mod = _suggest()
    monkeypatch.setattr(mod, "FILED_DIR", tmp_path / "filed")
    monkeypatch.setattr(mod, "log", lambda m: None)
    return mod


def _stub_run(monkeypatch, suggest, replies):
    calls = []

    def run(argv, **kw):
        calls.append((argv, kw))
        payload = json.loads(Path(argv[argv.index("--json") + 1])
                             .read_text(encoding="utf-8"))
        shapes.validate("experiment", payload)
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        if r == "ok":
            return _Proc(0, json.dumps({"kind": "experiment", "written": True,
                                        "id": "uuid-1",
                                        "name": payload["name"],
                                        "arms": ["A control", "B challenger"]}))
        return _Proc(2, "", r)

    monkeypatch.setattr(suggest.subprocess, "run", run)
    return calls


def test_a_launched_test_is_filed_once_through_intel_record(suggest, monkeypatch):
    calls = _stub_run(monkeypatch, suggest, ["ok"])
    led = {"entries": {"i1": _entry()}}
    n = suggest.file_experiments("renegade", led, TODAY)
    assert n == {"filed": 1, "skipped": 0, "failed": 0}
    argv, kw = calls[0]
    assert argv[:6] == [sys.executable, "-m", "intel", "record", "--kind",
                        "experiment"]
    assert argv[6] == "--json" and Path(argv[7]).is_file()
    assert kw["cwd"] == str(ROOT) and kw["timeout"]
    e = led["entries"]["i1"]
    assert e["experiment"]["id"] == "uuid-1"
    assert e["experiment"]["name"].startswith("i1 | Camp A | creative:")
    assert e["experiment"]["filed_on"] == TODAY.isoformat()
    # The next run files nothing: it is on the entry.
    assert suggest.file_experiments("renegade", led, TODAY)["filed"] == 0
    assert len(calls) == 1


def test_a_failed_filing_is_logged_on_the_entry_and_retried(suggest, monkeypatch):
    calls = _stub_run(monkeypatch, suggest, [
        "no brand 'renegade'.", subprocess.TimeoutExpired("x", 1), "ok"])
    led = {"entries": {"i1": _entry()}}
    assert suggest.file_experiments("renegade", led, TODAY)["failed"] == 1
    e = led["entries"]["i1"]
    assert e["experiment"] is None
    assert "no brand" in e["experiment_error"]["why"]
    assert suggest.file_experiments("renegade", led, TODAY)["failed"] == 1
    assert "TimeoutExpired" in e["experiment_error"]["why"]
    assert suggest.file_experiments("renegade", led, TODAY)["filed"] == 1
    assert e["experiment"]["id"] == "uuid-1"
    assert "experiment_error" not in e
    assert len(calls) == 3


def test_an_existing_name_counts_as_filed(suggest, monkeypatch):
    monkeypatch.setattr(suggest.subprocess, "run", lambda argv, **kw: _Proc(
        0, json.dumps({"kind": "experiment", "written": False,
                       "why": "an experiment named 'x' already exists"})))
    led = {"entries": {"i1": _entry()}}
    assert suggest.file_experiments("renegade", led, TODAY)["filed"] == 1
    e = led["entries"]["i1"]["experiment"]
    assert e["written"] is False and e["name"].startswith("i1 | ")
    assert "already exists" in e["note"]


def test_no_decision_spend_is_skipped_without_a_process(suggest, monkeypatch):
    calls = _stub_run(monkeypatch, suggest, [])
    led = {"entries": {"i1": _entry(decision_spend_per_arm=None),
                       "i2": _entry("i2", status="open",
                                    implemented_on=None),
                       "i3": _entry("i3", type="act", kind="delivery_cut")}}
    n = suggest.file_experiments("renegade", led, TODAY)
    assert n == {"filed": 0, "skipped": 1, "failed": 0}
    assert calls == []
    assert "test_floor" in led["entries"]["i1"]["experiment_skipped"]["why"]
    assert led["entries"]["i1"]["experiment"] is None, "tried again next run"


def test_suggest_files_after_the_review_is_on_disk_and_never_imports_the_writer():
    import ast
    src = (ROOT / "scripts" / "suggest.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    assert not [n for n in names
                if "db_owner" in n or n.endswith("record")], sorted(names)
    body = src[src.index("async def publish"):src.index("def model_facts")]
    assert body.index("out.write_text") < body.index("file_experiments(") \
        < body.index("outcomes_mod.save")


# ---------------------------------------------------------- the page --

def _ledger_for_page():
    filed = _entry(experiment={"name": "i1 | Camp A | creative: it's new",
                               "id": "uuid-1"})
    rec = _entry("i2", kind="post_click", variable="form", status="not_detectable",
                 challenger="one fewer question", implemented_on=None,
                 match=None, matched_by=None, recommended_on="2026-10-05")
    old = _entry("i3", status="open", implemented_on=None, match=None,
                 recommended_on="2026-09-20", tag=None)
    budget = _entry("i4", kind="budget", status="open", implemented_on=None)
    unfiled = _entry("i5", decision_spend_per_arm=None,
                     experiment_skipped={"why": "no decision spend"})
    return {"entries": {e["id"]: e for e in (filed, rec, old, budget, unfiled)}}


def _rows():
    return [{"name": "i1 | Camp A | creative: it's new", "state": "proposed",
             "question": "q", "hypothesis": "h", "primary_metric": "cpa",
             "minimum_effect_pct": 10, "minimum_spend_per_arm": 250,
             "arms": [{"label": "A control"}, {"label": "B challenger"}],
             "conclusion": None, "concluded_by": None},
            {"name": "older", "state": "running", "question": "q",
             "hypothesis": "h", "primary_metric": "cpa",
             "minimum_effect_pct": 20, "minimum_spend_per_arm": 500,
             "arms": [], "conclusion": None, "concluded_by": None}]


def test_launches_and_recommended_tests_come_off_the_ledger():
    led = _ledger_for_page()
    rows = exp_mod.with_launches(_rows(), led)
    assert rows[0]["launch"] == {"issue_id": "i1", "implemented_on": "2026-10-03",
                                 "match": "strong",
                                 "matched_by": "tagged utm_content=EXP-i1",
                                 "tag": "EXP-i1"}
    assert rows[1]["launch"] is None
    assert [r["id"] for r in exp_mod.recommended_tests(led)] == ["i2", "i3"]
    assert [u["id"] for u in exp_mod.launched_unfiled(led)] == ["i5"]
    assert exp_mod.recommended_tests(None) == []


def _render(**ctx):
    import ui
    base = {"nav": "experiments", "brand": "renegade", "settled": "2026-10-07",
            "caveat": None, "until": None, "today": "2026-10-10", "days": 14,
            "ideas": None, "has_ledger": True}
    base.update(ctx)
    return ui.templates.env.get_template("experiments.html").render(**base)


def test_the_page_shows_the_detected_launch_and_the_exact_confirm_sql():
    led = _ledger_for_page()
    rows = exp_mod.with_launches(_rows(), led)
    html = _render(d={"rows": rows, "awaiting_launch": 1}, rows=rows,
                   recommended=exp_mod.recommended_tests(led),
                   unfiled=exp_mod.launched_unfiled(led),
                   launches=[r for r in rows if r["launch"]])
    assert "Launch detected 2026-10-03 (strong, tagged utm_content=EXP-i1)" in html
    sql = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S).group(1)
    sql = sql.replace("&#39;", "'").replace("&#34;", '"')
    assert sql == (
        "update ads.experiment\n"
        "   set started_on = date '2026-10-03'\n"
        " where brand_id = (select id from ads.brand where slug = 'renegade')\n"
        "   and name     = 'i1 | Camp A | creative: it''s new'\n"
        "   and started_on is null;")
    # Tests the report recommends: the ledger's, not propose's.
    assert "Tests the report recommends" in html
    assert "utm_content=EXP-i2" in html and "one fewer question" in html
    assert "2026-10-05" in html and 'href="/suggestions?brand=renegade"' in html
    assert "Source: <code>intel propose</code>" not in html
    assert "Launched, not filed yet" in html and "no decision spend" in html


def test_the_page_falls_back_to_propose_when_the_ledger_is_empty():
    ideas = {"proposals": [], "proposal_count": 0, "diagnoses": [],
             "observations": [], "settled_through": None}
    html = _render(d={"rows": [], "awaiting_launch": 0}, ideas=ideas,
                   has_ledger=False, recommended=[], unfiled=[], launches=[])
    assert "Source: <code>intel propose</code>" in html
    assert "Tests the report recommends" not in html


def test_the_template_has_no_script_and_no_button():
    # base.html carries the site's one script and the Refresh button; the
    # page's own template adds neither.
    tpl = (ROOT / "templates" / "experiments.html").read_text(encoding="utf-8")
    assert not re.search(r"<script|<button", tpl, re.I)


def test_the_confirm_sql_lives_in_the_template_not_in_a_read_module():
    tpl = (ROOT / "templates" / "experiments.html").read_text(encoding="utf-8")
    assert "and started_on is null;" in tpl
    src = (ROOT / "intel" / "experiments.py").read_text(encoding="utf-8")
    assert "update ads." not in src


# ------------------------------------------------ the one-off statement --

def test_the_supersede_statement_is_guarded():
    sql = (ROOT / "scripts" / "supersede_0928_experiments.sql").read_text(
        encoding="utf-8")
    live = "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())
    assert live.strip().startswith("update ads.experiment")
    unquoted = re.sub(r"'(?:[^']|'')*'", "''", live)
    assert unquoted.count(";") == 1, "one statement"
    for guard in ("and started_on is null", "and conclusion is null",
                  "proposed_by = 'cli:Renegade'",
                  "(name like 'p0923-%' or name like 'p0924-%')",
                  "created_at < timestamptz '2026-09-29"):
        assert guard in live, guard
    # experiment_conclusion_attributed: all three set together.
    for col in ("conclusion   =", "concluded_by =", "concluded_at = now()"):
        assert col in live
    assert ("'superseded: filed from the 2026-09-28 triage, whose analysis "
            "was replaced on 2026-10-01; never launched'") in live
    assert "concluded_by = '<your name>'" in live, "a person fills it in"
    assert "started_on =" not in live and "ended_on" not in live
    names = re.findall(r"'(p092[34]-[^|']*\|(?:[^']|'')*)'", live)
    assert len(names) == 5
