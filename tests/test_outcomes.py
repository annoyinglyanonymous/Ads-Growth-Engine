"""The outcomes ledger: registered, matched, measured, counted -- never judged.

No database: metrics.entity_outcome / metrics.arm_compare (migration 018's
wrappers) are stubbed, and the matching tests feed creative.changes_since's
structured "you" items directly.
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from intel import creative, outcomes

ROOT = Path(__file__).resolve().parent.parent
MODULE = ROOT / "intel" / "outcomes.py"

TODAY = date(2026, 10, 10)


# ------------------------------------------------------------ fixtures --

def _issue(iid, rtype="test", basis="d1", variable="creative",
           control="Control Ad", challenger='a new UGC video, "Fresh Hook"',
           challenger_ad=None, action="Run a challenger", open_since="2026-10-01",
           **extra):
    plan = None
    if rtype == "test":
        plan = {"variable": variable, "control": control,
                "challenger": challenger, "challenger_ad": challenger_ad,
                "challenger_kind": "existing_asset" if challenger_ad
                else "new_creative"}
    return {"id": iid, "status": "new", "open_since": open_since,
            "priority": "primary", "basis": basis,
            "recommendation": {"type": rtype, "action": action,
                               "expected_outcome": "x"},
            "test_plan": plan, **extra}


def _entry(issues, findings=None, campaign="Camp A", control_group="Group 1",
           ad_groups=None):
    return {"campaign": campaign, "optimization_goals": ["LEAD_GENERATION"],
            "issues": issues, "shape": 2,
            "diagnosis": {
                "findings": findings if findings is not None else [
                    {"id": "d1", "level": "ad", "entity": "Control Ad",
                     "pattern": "possible_fatigue"}],
                "control": {"ad_group": control_group, "ad": "Control Ad",
                            "incumbent": "Control Ad"}},
            "where": {"ads": [{"name": "Control Ad", "_key": "k-control",
                               "ad_group": control_group}],
                      "ad_groups": [{"name": control_group, "_key": "g-1"}]},
            "ad_groups": ad_groups if ad_groups is not None else [
                {"ad_group": control_group,
                 "test_floor": {"decision_spend_per_arm": 250.0}}]}


def _added(cid, ad, key, group="Group 1", first="2026-10-03"):
    return {"id": cid, "by": "you", "what": f'ad added: "{ad}"',
            "kind": "ad_added", "ad": ad, "ad_key": key, "ad_group": group,
            "ad_group_key": "g-1", "first_spend_day": first,
            "status_from": None, "status_to": "ACTIVE"}


def _ledger(triage, changes=None, today=TODAY, overrides=None, ledger=None):
    return outcomes.update_ledger(ledger or outcomes.empty("renegade"),
                                  triage, changes or {}, today, overrides or {})


# --------------------------------------------------------- registration --

def test_act_and_test_issues_are_registered_monitor_is_not():
    t = [_entry([_issue("i1"),
                 _issue("i2", rtype="act", basis="d2",
                        action="Pause this ad"),
                 _issue("i3", rtype="monitor")],
                findings=[{"id": "d1", "level": "ad", "entity": "Control Ad",
                           "pattern": "possible_fatigue"},
                          {"id": "d2", "level": "ad", "entity": "Loser Ad",
                           "pattern": "no_conversions_spend"}]),
         {"campaign": "Old shape", "problems": [{"id": "p1"}]}]
    led = _ledger(t)
    e = led["entries"]
    assert set(e) == {"i1", "i2"}
    assert e["i1"]["kind"] == "creative_test"
    assert e["i1"]["status"] == "open"
    assert e["i1"]["recommended_on"] == "2026-10-01"
    assert e["i1"]["ad_group"] == "Group 1"
    assert e["i1"]["control_ad_key"] == "k-control"
    assert e["i1"]["decision_spend_per_arm"] == 250.0
    assert e["i1"]["goal"] == "LEAD_GENERATION"
    assert e["i2"]["kind"] == "delivery_cut"
    assert e["i2"]["entity"] == "Loser Ad"
    assert led["updated_at"] == TODAY.isoformat()


def test_kinds_are_derived():
    k = outcomes._kind
    assert k("test", "creative", "ad", None, "", "x") == "creative_test"
    assert k("act", "creative", "ad", None, "", None) == "creative_refresh"
    assert k("act", None, "ad", "no_conversions_spend", "", None) == "delivery_cut"
    assert k("act", None, "ad", "possible_fatigue", "Reduce delivery", None) \
        == "delivery_cut"
    assert k("test", "budget", "ad_group", None, "", "x") == "budget"
    assert k("act", None, "campaign", "scaling_opportunity", "", None) == "budget"
    for v in ("landing_page", "form", "offer", "tracking"):
        assert k("test", v, "campaign", None, "", "x") == "post_click"
    assert k("act", None, "campaign", "auction_cost", "Wait", None) == "other"


def test_a_re_mention_refreshes_last_mentioned_on_only():
    led = _ledger([_entry([_issue("i1")])], today=date(2026, 10, 2))
    again = _ledger([_entry([_issue("i1", action="changed words")])],
                    today=TODAY, ledger=led)
    e = again["entries"]["i1"]
    assert e["last_mentioned_on"] == TODAY.isoformat()
    assert e["recommended_on"] == "2026-10-01"
    assert e["action"] == "Run a challenger"


def test_an_open_entry_expires_after_30_days_unmentioned():
    led = _ledger([_entry([_issue("i1")])], today=date(2026, 10, 1))
    later = _ledger([], today=date(2026, 10, 31), ledger=led)
    assert later["entries"]["i1"]["status"] == "open"
    later = _ledger([], today=date(2026, 11, 1), ledger=led)
    assert later["entries"]["i1"]["status"] == "expired"
    assert "30 days" in later["entries"]["i1"]["reason"]


def test_post_click_is_not_detectable_and_stays_so():
    led = _ledger([_entry([_issue("i1", variable="landing_page",
                                  basis="d1")],
                          findings=[{"id": "d1", "level": "campaign",
                                     "entity": "Camp A",
                                     "pattern": "post_click"}])],
                  changes={"Camp A": [_added("c1", "New Ad", "k-new")]})
    e = led["entries"]["i1"]
    assert e["kind"] == "post_click"
    assert e["status"] == "not_detectable"
    assert "not visible" in e["reason"]
    assert e["implemented_on"] is None


# ------------------------------------------------------------ matching --

def test_a_new_ad_named_as_the_challenger_is_a_strong_match():
    led = _ledger([_entry([_issue("i1", challenger_ad="Winner Ad")])],
                  changes={"Camp A": [_added("c1", "Winner Ad", "k-new")]})
    e = led["entries"]["i1"]
    assert e["status"] == "implemented"
    assert e["match"] == "strong"
    assert e["implemented_on"] == "2026-10-03", "the first spend day"
    assert e["challenger_ad_key"] == "k-new"
    assert 'ad added: "Winner Ad"' in e["matched_by"]


def test_a_challenger_quoted_in_the_plan_is_strong_too():
    led = _ledger([_entry([_issue("i1")])],
                  changes={"Camp A": [_added("c1", "Fresh Hook", "k-new")]})
    assert led["entries"]["i1"]["match"] == "strong"


def test_any_other_new_ad_in_the_group_is_likely():
    led = _ledger([_entry([_issue("i1")])],
                  changes={"Camp A": [_added("c1", "Something Else", "k-new",
                                             first=None)]})
    e = led["entries"]["i1"]
    assert e["match"] == "likely"
    assert e["implemented_on"] == TODAY.isoformat(), "no spend yet: review date"


def test_a_new_ad_in_another_group_or_before_the_recommendation_is_not_a_match():
    t = [_entry([_issue("i1")])]
    other = _ledger(t, changes={"Camp A": [_added("c1", "X", "k", group="Group 2")]})
    assert other["entries"]["i1"]["status"] == "open"
    early = _ledger(t, changes={"Camp A": [_added("c1", "X", "k",
                                                  first="2026-09-20")]})
    assert early["entries"]["i1"]["status"] == "open"
    elsewhere = _ledger(t, changes={"Camp B": [_added("c1", "X", "k")]})
    assert elsewhere["entries"]["i1"]["status"] == "open"


def test_one_change_cannot_implement_two_tests():
    t = [_entry([_issue("i1", open_since="2026-09-30"),
                 _issue("i2", challenger_ad="Winner Ad")])]
    led = _ledger(t, changes={"Camp A": [_added("c1", "Winner Ad", "k-new")]})
    e = led["entries"]
    assert e["i2"]["status"] == "implemented" and e["i2"]["match"] == "strong"
    assert e["i1"]["status"] == "open", "the ad was i2's named challenger"
    # And a later run cannot hand the same ad to i1 either.
    again = _ledger(t, changes={"Camp A": [_added("c1", "Winner Ad", "k-new")]},
                    ledger=led)
    assert again["entries"]["i1"]["status"] == "open"


def _act(iid, entity, pattern, action, level="ad"):
    return (_issue(iid, rtype="act", basis="d9", action=action),
            {"id": "d9", "level": level, "entity": entity, "pattern": pattern})


def test_a_pause_or_a_gone_ad_implements_a_delivery_cut():
    issue, f = _act("i1", "Loser Ad", "no_conversions_spend", "Cut it")
    t = [_entry([issue], findings=[f])]
    paused = {"id": "c1", "by": "you", "what": '"Loser Ad" ACTIVE -> PAUSED',
              "kind": "status_changed", "ad": "Loser Ad", "ad_key": "k-l",
              "status_from": "ACTIVE", "status_to": "PAUSED"}
    e = _ledger(t, changes={"Camp A": [paused]})["entries"]["i1"]
    assert e["status"] == "implemented" and e["implemented_on"] == TODAY.isoformat()
    gone = {"id": "c1", "by": "you", "what": 'ad gone from the campaign: "Loser Ad"',
            "kind": "ad_gone", "ad": "Loser Ad", "ad_key": "k-l"}
    assert _ledger(t, changes={"Camp A": [gone]})["entries"]["i1"]["status"] \
        == "implemented"
    other = {**paused, "ad": "Other Ad", "ad_key": "k-o"}
    assert _ledger(t, changes={"Camp A": [other]})["entries"]["i1"]["status"] \
        == "open"
    resumed = {**paused, "status_from": "PAUSED", "status_to": "ACTIVE"}
    assert _ledger(t, changes={"Camp A": [resumed]})["entries"]["i1"]["status"] \
        == "open"


def test_a_wording_change_on_the_named_ad_implements_a_refresh():
    issue = _issue("i1", rtype="act", basis="d9", action="Rewrite the headline")
    f = {"id": "d9", "level": "ad", "entity": "Tired Ad",
         "pattern": "possible_fatigue"}
    t = [_entry([issue], findings=[f])]
    assert _ledger(t)["entries"]["i1"]["kind"] == "creative_refresh"
    ch = {"id": "c1", "by": "you", "what": 'wording changed on "Tired Ad"',
          "kind": "wording_changed", "ad": "Tired Ad", "ad_key": None}
    e = _ledger(t, changes={"Camp A": [ch]})["entries"]["i1"]
    assert e["status"] == "implemented" and e["match"] == "strong"


def test_a_budget_change_implements_a_budget_recommendation():
    issue = _issue("i1", rtype="act", basis="d9", action="Raise the budget")
    f = {"id": "d9", "level": "ad_group", "entity": "Group 1",
         "pattern": "scaling_opportunity"}
    t = [_entry([issue], findings=[f])]
    group = {"id": "c1", "by": "you", "kind": "budget_changed",
             "what": 'daily budget 5000 -> 8000 on ad group "Group 1"',
             "level": "ad_group", "ad_group": "Group 1", "ad_group_key": "g-1"}
    e = _ledger(t, changes={"Camp A": [group]})["entries"]["i1"]
    assert e["kind"] == "budget" and e["match"] == "strong"
    camp = {"id": "c1", "by": "you", "kind": "budget_changed",
            "what": "daily budget 5000 -> 8000 on the campaign",
            "level": "campaign"}
    e = _ledger(t, changes={"Camp A": [camp]})["entries"]["i1"]
    assert e["status"] == "implemented" and e["match"] == "likely"
    other = {**group, "ad_group": "Group 2", "ad_group_key": "g-2"}
    assert _ledger(t, changes={"Camp A": [other]})["entries"]["i1"]["status"] \
        == "open"


def test_numbers_changes_never_match():
    ch = {**_added("c1", "Winner Ad", "k"), "by": "numbers"}
    led = _ledger([_entry([_issue("i1", challenger_ad="Winner Ad")])],
                  changes={"Camp A": [ch]})
    assert led["entries"]["i1"]["status"] == "open"


def test_an_override_wins():
    t = [_entry([_issue("i1", challenger_ad="Winner Ad")])]
    ch = {"Camp A": [_added("c1", "Winner Ad", "k-new")]}
    no = _ledger(t, changes=ch, overrides={"i1": {"not_implemented": True}})
    e = no["entries"]["i1"]
    assert e["status"] == "open" and e["implemented_on"] is None
    assert e["match"] == "person"
    yes = _ledger(t, overrides={"i1": {"implemented_on": "2026-10-05"}})
    e = yes["entries"]["i1"]
    assert e["status"] == "implemented" and e["implemented_on"] == "2026-10-05"
    assert e["match"] == "person" and e["matched_by"] == "set by a person"
    nd = _ledger([_entry([_issue("i2", variable="form")])],
                 overrides={"i2": {"implemented_on": "2026-10-04"}})
    assert nd["entries"]["i2"]["status"] == "implemented"
    # An overridden entry never expires on its own.
    old = _ledger([], today=date(2026, 12, 1), ledger=no)
    assert old["entries"]["i1"]["status"] == "open"


def test_update_ledger_does_not_mutate_its_input():
    led = _ledger([_entry([_issue("i1")])])
    before = json.dumps(led, sort_keys=True)
    _ledger([], changes={"Camp A": [_added("c1", "X", "k")]}, ledger=led)
    assert json.dumps(led, sort_keys=True) == before


def test_two_files_a_new_ad_in_the_controls_group_is_implemented_on_first_spend():
    """End to end through creative.changes_since: yesterday's file has a v2
    snapshot, today's snapshot has one more ad in the control's ad group, and
    the open test from yesterday's triage is implemented on that ad's first
    spend day."""
    camp = {"campaign": "Camp A", "status": "ACTIVE"}
    v2_old = {"Camp A": {
        "_campaign": {"status": "ACTIVE", "daily_budget": None},
        "_ad_groups": {"g-1": {"name": "Group 1", "daily_budget": "5000",
                               "optimization_goal": "LEAD_GENERATION"}},
        "k-control": {"ad": "Control Ad", "status": "ACTIVE",
                      "copy_hash": "h1", "ad_group": "Group 1",
                      "ad_group_key": "g-1", "first_spend_day": "2026-08-01"}}}
    v2_new = json.loads(json.dumps(v2_old))
    v2_new["Camp A"]["k-new"] = {"ad": "Brand New Ad", "status": "ACTIVE",
                                 "copy_hash": "h2", "ad_group": "Group 1",
                                 "ad_group_key": "g-1",
                                 "first_spend_day": "2026-10-04"}
    prev = {"pack": {"campaigns": [camp]}, "snapshot": v2_old}
    changes = creative.changes_since(prev, [camp], v2_new)

    yesterday = _ledger([_entry([_issue("i1")])], today=date(2026, 10, 2))
    led = _ledger([_entry([_issue("i1")])], changes=changes, today=TODAY,
                  ledger=yesterday)
    e = led["entries"]["i1"]
    assert e["status"] == "implemented"
    assert e["implemented_on"] == "2026-10-04"
    assert e["match"] == "likely"
    assert e["challenger_ad_key"] == "k-new"


# --------------------------------------------------------- measurement --

def _stub(monkeypatch, *, entity=None, arm=None, calls=None):
    calls = calls if calls is not None else []

    async def entity_outcome(slug, level, key, bs, bu, as_, au):
        calls.append(("entity_outcome", level, key, bs, bu, as_, au))
        return entity

    async def arm_compare(slug, control, challenger, since, until,
                          decision_spend=None):
        calls.append(("arm_compare", control, challenger, since, until,
                      decision_spend))
        return arm

    monkeypatch.setattr(outcomes.metrics, "entity_outcome", entity_outcome,
                        raising=False)
    monkeypatch.setattr(outcomes.metrics, "arm_compare", arm_compare,
                        raising=False)
    return calls


def _implemented(kind="delivery_cut", **extra):
    e = {"id": "i1", "campaign": "Camp A", "kind": kind, "status": "implemented",
         "implemented_on": "2026-10-01", "entity_level": "ad",
         "entity": "Loser Ad", "measure_level": "ad_group",
         "measure_entity": "Group 1", "measure_key": "g-1",
         "control_ad_key": "k-control", "challenger_ad_key": "k-new",
         "decision_spend_per_arm": 250.0, "measurement": None, "reason": None,
         **extra}
    return {"brand": "renegade", "entries": {"i1": e}}


def _measure(led, settled=date(2026, 10, 20)):
    return asyncio.run(outcomes.measure(led, "renegade", settled))["entries"]["i1"]


def _row(cpa, evidence="moderate", days=10):
    return {"available": True,
            "row": {"pct": {"cpa": cpa}, "evidence_level": evidence,
                    "after_settled_days": days, "cur_m": {}, "pri_m": {}}}


@pytest.mark.parametrize("cpa,evidence,days,result,status", [
    (-10, "moderate", 10, "improved", "measured"),
    (-9.99, "moderate", 10, "neutral", "measured"),
    (9.99, "directional", 10, "neutral", "measured"),
    (10, "strong", 10, "worse", "measured"),
    (-40, "moderate", 6, "too_early", "measuring"),
    (-40, "insufficient", 10, "too_early", "measuring"),
    (-40, None, 10, "too_early", "measuring"),
    (None, "moderate", 10, "too_early", "measuring"),
    (-40, "insufficient", 28, "too_early", "measured"),
])
def test_entity_outcome_is_classified_by_comparison(monkeypatch, cpa, evidence,
                                                    days, result, status):
    calls = _stub(monkeypatch, entity=_row(cpa, evidence, days))
    e = _measure(_implemented())
    assert e["measurement"]["result"] == result
    assert e["status"] == status
    assert e["measurement"]["settled_days"] == days
    assert e["measurement"]["figures"]["pct"] == {"cpa": cpa}
    _, level, key, bs, bu, as_, au = calls[0]
    assert (level, key) == ("ad_group", "g-1"), "a cut is read on its ad group"
    assert (bs, bu) == (date(2026, 9, 17), date(2026, 9, 30))
    assert (as_, au) == (date(2026, 10, 1), date(2026, 10, 20))


def _arm(cpa, evidence="moderate", days=10, sufficient=True):
    return {"available": True,
            "row": {"effect_pct": {"cpa": cpa}, "evidence_level": evidence,
                    "settled_days": days, "spend_sufficient": sufficient,
                    "control_m": {}, "challenger_m": {}}}


def test_a_creative_test_compares_the_arms(monkeypatch):
    calls = _stub(monkeypatch, arm=_arm(-25))
    e = _measure(_implemented("creative_test"))
    assert e["measurement"]["result"] == "improved"
    assert calls[0] == ("arm_compare", "k-control", "k-new", date(2026, 10, 1),
                        date(2026, 10, 20), 250.0)


def test_an_arm_under_its_decision_spend_is_too_early(monkeypatch):
    _stub(monkeypatch, arm=_arm(-25, sufficient=False))
    e = _measure(_implemented("creative_test"))
    assert e["measurement"]["result"] == "too_early"
    assert e["status"] == "measuring"
    _stub(monkeypatch, arm=_arm(-25, sufficient=None))
    assert _measure(_implemented("creative_test"))["measurement"]["result"] \
        == "improved", "no decision spend given: not held back on it"


def test_018_unavailable_leaves_the_status_and_says_why(monkeypatch):
    _stub(monkeypatch, entity={"available": False,
                               "why": "migration 018 is not applied"})
    e = _measure(_implemented())
    assert e["status"] == "implemented"
    assert e["measurement"] is None
    assert "018" in e["reason"]
    monkeypatch.delattr(outcomes.metrics, "entity_outcome", raising=False)
    e = _measure(_implemented())
    assert e["status"] == "implemented" and "018" in e["reason"]


def test_a_frozen_measurement_is_not_re_read(monkeypatch):
    calls = _stub(monkeypatch, entity=_row(-30))
    led = _implemented(status="measured",
                       measurement={"settled_days": 28, "result": "improved"})
    e = _measure(led)
    assert calls == [] and e["measurement"]["result"] == "improved"
    led = _implemented(status="measured",
                       measurement={"settled_days": 12, "result": "neutral"})
    assert _measure(led)["measurement"]["result"] == "improved"


def test_a_change_after_the_settled_edge_is_too_early_without_sql(monkeypatch):
    calls = _stub(monkeypatch, entity=_row(-30))
    e = _measure(_implemented(implemented_on="2026-10-22"))
    assert calls == []
    assert e["status"] == "measuring"
    assert e["measurement"]["result"] == "too_early"


def test_a_missing_key_is_looked_up_by_name_or_reported(monkeypatch):
    _stub(monkeypatch, entity=_row(-30))

    async def nobody(slug, level, campaign, name):
        return None

    monkeypatch.setattr(outcomes, "_entity_key", nobody)
    e = _measure(_implemented(measure_key=None))
    assert e["reason"] == "no key" and e["status"] == "implemented"

    async def found(slug, level, campaign, name):
        return f"{level}:{name}"

    monkeypatch.setattr(outcomes, "_entity_key", found)
    e = _measure(_implemented(measure_key=None))
    assert e["measure_key"] == "ad_group:Group 1"
    assert e["status"] == "measured"


# ----------------------------------------------------------- scorecard --

def test_scorecard_counts():
    def ent(iid, kind, status, result=None, rec="2026-09-20", impl=None):
        return {"id": iid, "kind": kind, "status": status,
                "recommended_on": rec, "implemented_on": impl,
                "measurement": {"result": result} if result else None}
    led = {"entries": {
        "a": ent("a", "creative_test", "measured", "improved", impl="2026-09-25"),
        "b": ent("b", "creative_test", "measuring", "too_early", impl="2026-10-01"),
        "c": ent("c", "delivery_cut", "measured", "worse", impl="2026-09-28"),
        "d": ent("d", "budget", "open"),
        "e": ent("e", "post_click", "not_detectable"),
        "old": ent("old", "budget", "measured", "neutral", rec="2026-05-01",
                   impl="2026-05-03"),
    }}
    s = outcomes.scorecard(led, TODAY)
    assert s["made"] == 5 and s["implemented"] == 3
    assert s["by_result"] == {"improved": 1, "neutral": 0, "worse": 1,
                              "too_early": 1}
    assert s["by_kind"]["creative_test"]["made"] == 2
    assert s["by_kind"]["creative_test"]["improved"] == 1
    assert s["by_kind"]["budget"]["made"] == 1
    assert [e["id"] for e in s["recent"]] == ["c", "a"], "newest first"
    assert outcomes.scorecard(led, TODAY, days=400)["made"] == 6


def test_for_campaign_lists_what_is_still_in_play():
    led = {"entries": {
        "a": {"campaign": "A", "status": "open"},
        "b": {"campaign": "A", "status": "measuring"},
        "c": {"campaign": "A", "status": "measured"},
        "d": {"campaign": "B", "status": "open"}}}
    assert [e["status"] for e in outcomes.for_campaign(led, "A")] \
        == ["open", "measuring"]


def test_load_save_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(outcomes, "OUTCOME_DIR", tmp_path / "outcomes")
    assert outcomes.load("renegade") == outcomes.empty("renegade")
    assert outcomes.load_overrides() == {}
    led = _ledger([_entry([_issue("i1")])])
    p = outcomes.save("renegade", led)
    assert p == tmp_path / "outcomes" / "renegade.json"
    assert outcomes.load("renegade") == json.loads(json.dumps(led))
    (tmp_path / "outcomes" / "overrides.json").write_text(
        '{"i1": {"implemented_on": "2026-10-05"}}', encoding="utf-8")
    assert outcomes.load_overrides() == {"i1": {"implemented_on": "2026-10-05"}}
    (tmp_path / "outcomes" / "overrides.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="overrides.json"):
        outcomes.load_overrides()


def test_outcomes_is_gitignored():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "outcomes/" in lines


# ---------------------------------------------------------- the module --

#: The two helpers that step a date by whole days to name a window edge.
DATE_HELPERS = ("_days_before", "_days_after")


def test_the_module_computes_nothing():
    """test_diagnose's rule: no + - * / // % ** at all, no augmented
    assignment, no sum(), a unary minus only on a literal -- outside the two
    named date helpers. A path join (`DIR / "name"`) is not a quotient."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    exempt = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            if node.name in DATE_HELPERS:
                exempt |= {id(n) for n in ast.walk(node)}
    assert set(DATE_HELPERS) <= names, "the exemption must name real helpers"
    for node in ast.walk(tree):
        if id(node) in exempt:
            continue
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) \
                and isinstance(node.right, (ast.Constant, ast.JoinedStr)) \
                and isinstance(getattr(node.right, "value", ""), str):
            continue          # OUTCOME_DIR / "overrides.json"
        if isinstance(node, ast.BinOp):
            assert not isinstance(node.op, (
                ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
                ast.Pow, ast.MatMult)), (
                f"arithmetic at line {node.lineno} of {MODULE.name}")
        assert not isinstance(node, ast.AugAssign), (
            f"augmented assignment at line {node.lineno} of {MODULE.name}")
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            assert isinstance(node.operand, ast.Constant), (
                f"negation of a value at line {node.lineno} of {MODULE.name}")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id != "sum", (
                f"sum() at line {node.lineno} of {MODULE.name}")
