"""The outcomes ledger: was a recommendation carried out, and what followed.

READ ONLY against the database. The ledger is a FILE, outcomes/<brand>.json
(gitignored, like suggestions/), rebuilt and extended on each suggest.py run.
Nothing here writes ads.* and nothing here concludes: a measured result is a
reading of SQL figures, never `ads.experiment.conclusion`.

THREE STEPS, called in order by scripts/suggest.py after publishing:

  update_ledger(ledger, triage, changes, today, overrides)   pure
      Registers every published `act` / `test` issue (keyed by its id, which
      persists across runs), refreshes `last_mentioned_on`, applies the
      person's overrides, matches the account's own changes (creative
      .changes_since, the "you" items) to open entries, and expires open
      entries nobody has mentioned for EXPIRE_DAYS.

  measure(ledger, slug, settled_through)                       async, SQL
      For implemented / measuring entries: metrics.arm_compare (a creative
      test: control against challenger from the implementation day) or
      metrics.entity_outcome (14 settled days before against the days since).
      Classified by comparison only; re-measured each run until
      FROZEN_AFTER settled days.

  scorecard(ledger, today, days)                               pure
      Counts, by len.

IMPLEMENTATION IS AUTO-DETECTED, AND A PERSON CORRECTS IT

A match records what it rests on (`matched_by`) and how sure it is (`match`:
strong when the change names exactly what was recommended, likely when it only
fits). outcomes/overrides.json is the one manual surface:
{issue_id: {"implemented_on": "YYYY-MM-DD"} | {"not_implemented": true}}.
An override always wins and is labelled "person".

Landing page, form, offer and tracking changes are not visible in Meta's
structure data, so those entries are `not_detectable` until their tag
(utm_content=EXP-<issue id>, the strongest match of all, for any test) shows
up on an added ad, or a person says otherwise.

A launched test becomes an experiment PROPOSAL: experiment_payload builds it
from the entry, and scripts/suggest.py files it through `intel record` in a
separate process. Nothing here sets started_on or a conclusion.

NOTHING HERE COMPUTES

Every figure is a column an 018 function returned. Dates are compared, never
subtracted -- except inside _days_before / _days_after, the two named helpers
tests/test_outcomes.py's AST check exempts, which step a date by a whole
number of days to name a window edge.
"""

from __future__ import annotations

import copy
import json
from datetime import date, timedelta
from pathlib import Path

from db import fetch_all

from . import context, metrics

ROOT = Path(__file__).resolve().parent.parent
OUTCOME_DIR = ROOT / "outcomes"

#: An open entry not mentioned for this many days is expired.
EXPIRE_DAYS = 30
#: Fewer settled days after the change than this is too early to read.
MIN_SETTLED_DAYS = 7
#: The before window, in settled days ending the day before implementation.
BEFORE_DAYS = 14
#: After this many settled days a measured reading is frozen.
FROZEN_AFTER = 28
#: CPA effect (percent, from SQL) at or beyond which a result is improved /
#: worse. Lower CPA is better.
IMPROVED_AT = -10
WORSE_AT = 10

STATUSES = ("open", "implemented", "measuring", "measured", "expired",
            "not_detectable")
KINDS = ("creative_test", "creative_refresh", "delivery_cut", "budget",
         "post_click", "other")
RESULTS = ("improved", "neutral", "worse", "too_early")

#: Variables whose change Meta's structure data cannot show.
POST_CLICK_VARIABLES = ("landing_page", "form", "offer", "tracking")
NOT_DETECTABLE_WHY = ("a form or page change is not visible in Meta's "
                      "structure data; a person can set it in "
                      "outcomes/overrides.json")

#: Words in an `act` recommendation that mean "spend less on this ad".
_CUT_WORDS = ("pause", "turn off", "switch off", "stop", "delivery",
              "reduce spend", "cut spend", "shift spend", "move spend")
_BUDGET_WORDS = ("budget",)
_REFRESH_WORDS = ("wording", "headline", "copy", "rewrite", "refresh",
                  "opening line", "primary text")
_POST_CLICK_WORDS = ("landing page", "form", "tracking", "pixel", "offer")

ACTION_CHARS = 200


# ------------------------------------------------------------ dates --
# The only place a date moves. Exempt by name from the AST rule.

def _days_before(d: date, n: int) -> date:
    return d - timedelta(days=n)


def _days_after(d: date, n: int) -> date:
    return d + timedelta(days=n)


def _date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _iso(value) -> str | None:
    d = _date(value)
    return d.isoformat() if d else None


# --------------------------------------------------------- the file --

def empty(slug: str) -> dict:
    return {"brand": slug, "updated_at": None, "entries": {}}


def path_for(slug: str) -> Path:
    return OUTCOME_DIR / f"{slug}.json"


def load(slug: str) -> dict:
    """The brand's ledger, or an empty one."""
    p = path_for(slug)
    if not p.exists():
        return empty(slug)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc.setdefault("brand", slug)
    doc.setdefault("entries", {})
    return doc


def save(slug: str, ledger: dict) -> Path:
    p = path_for(slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ledger, indent=2, ensure_ascii=False, default=str),
                 encoding="utf-8")
    return p


def load_overrides() -> dict:
    """outcomes/overrides.json, or {} when there is none.

    A file that is not valid JSON raises ValueError naming it: a person's
    correction silently ignored would read as the correction having taken.
    """
    p = OUTCOME_DIR / "overrides.json"
    if not p.exists():
        return {}
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p} is not valid JSON: {exc}") from exc
    return {str(k): v for k, v in doc.items() if isinstance(v, dict)} \
        if isinstance(doc, dict) else {}


# ------------------------------------------------------ registration --

def _norm(s) -> str:
    return "_".join(str(s or "").lower().replace("-", " ").split())


def _has(text, words) -> bool:
    t = str(text or "").lower()
    return any(w in t for w in words)


def _where_ads(entry: dict) -> list:
    return [a for a in ((entry.get("where") or {}).get("ads") or [])
            if isinstance(a, dict)]


def _where_groups(entry: dict) -> list:
    return [g for g in ((entry.get("where") or {}).get("ad_groups") or [])
            if isinstance(g, dict)]


def _key_of(d: dict):
    for k in ("ad_key", "entity_key", "key", "_key", "ad_group_key"):
        if d.get(k):
            return str(d[k])
    return None


def _ad_named(entry: dict, name) -> dict:
    for a in _where_ads(entry):
        if name and (a.get("name") or a.get("ad")) == name:
            return a
    return {}


def _group_named(entry: dict, name) -> dict:
    for g in _where_groups(entry):
        if name and (g.get("name") or g.get("ad_group")) == name:
            return g
    for g in entry.get("ad_groups") or []:
        if isinstance(g, dict) and name and \
                (g.get("ad_group") or g.get("name")) == name:
            return g
    return {}


def _ad_group_of(entry: dict, ad_name) -> str | None:
    """The ad group an ad sits in, from what the triage entry carries: the
    diagnosis's control, then the where-ads list. None when not known."""
    control = (entry.get("diagnosis") or {}).get("control") or {}
    if ad_name and ad_name in (control.get("ad"), control.get("incumbent")) \
            and control.get("ad_group"):
        return control["ad_group"]
    a = _ad_named(entry, ad_name)
    if a.get("ad_group"):
        return a["ad_group"]
    groups = _where_groups(entry) or [g for g in entry.get("ad_groups") or []
                                      if isinstance(g, dict)]
    if len(groups) == 1:
        return groups[0].get("name") or groups[0].get("ad_group")
    return None


def _decision_spend(issue: dict, entry: dict, ad_group) -> object:
    plan = issue.get("test_plan") or {}
    if plan.get("decision_spend_per_arm") is not None:
        return plan["decision_spend_per_arm"]
    floor = (_group_named(entry, ad_group).get("test_floor") or {})
    if floor.get("decision_spend_per_arm") is not None:
        return floor["decision_spend_per_arm"]
    groups = [g for g in entry.get("ad_groups") or [] if isinstance(g, dict)]
    if len(groups) == 1:
        return (groups[0].get("test_floor") or {}).get("decision_spend_per_arm")
    return None


def _kind(rtype: str, variable, level, pattern, action, challenger) -> str:
    v = _norm(variable)
    if v and any(p in v for p in POST_CLICK_VARIABLES):
        return "post_click"
    if "budget" in v:
        return "budget"
    if "creative" in v:
        if rtype == "test" and challenger:
            return "creative_test"
        if level == "ad":
            return "creative_refresh"
        return "other"
    if rtype == "act":
        if level == "ad" and (pattern == "no_conversions_spend"
                              or _has(action, _CUT_WORDS)):
            return "delivery_cut"
        if pattern in ("diminishing_returns", "scaling_opportunity") \
                or _has(action, _BUDGET_WORDS):
            return "budget"
        if _has(action, _POST_CLICK_WORDS):
            return "post_click"
        if level == "ad" and _has(action, _REFRESH_WORDS):
            return "creative_refresh"
    return "other"


def _measure_target(kind: str, level, entity, ad_group, campaign) -> tuple:
    """(level, name) whose before/after entity_outcome measures the entry.
    Cutting an ad's delivery is read on what it sat in, since the ad itself
    stops spending; everything else on the entity the issue named."""
    if kind == "delivery_cut":
        return ("ad_group", ad_group) if ad_group else ("campaign", campaign)
    if level and entity:
        return level, entity
    return "campaign", campaign


def _register(entry: dict, issue: dict, today: date) -> dict:
    rec = issue.get("recommendation") or {}
    rtype = str(rec.get("type") or "").lower()
    plan = issue.get("test_plan") or {}
    findings = {f.get("id"): f for f in
                (entry.get("diagnosis") or {}).get("findings") or []
                if isinstance(f, dict)}
    f = findings.get(issue.get("basis")) or {}
    campaign = entry.get("campaign")
    goals = entry.get("optimization_goals") or []
    level = issue.get("entity_level") or f.get("level")
    entity = issue.get("entity") or f.get("entity")
    if level == "campaign" and not entity:
        entity = campaign
    pattern = issue.get("pattern") or f.get("pattern")
    action = rec.get("action")
    control = plan.get("control")
    challenger = plan.get("challenger")
    kind = _kind(rtype, plan.get("variable"), level, pattern, action,
                 challenger)

    if level == "ad_group":
        ad_group = entity
    else:
        ad_group = _ad_group_of(entry, control if control else entity)

    if level == "ad":
        entity_key = issue.get("entity_key") or _key_of(_ad_named(entry, entity))
    elif level == "ad_group":
        entity_key = issue.get("entity_key") or _key_of(_group_named(entry, entity))
    elif level == "campaign":
        entity_key = issue.get("entity_key") or entry.get("campaign_key")
    else:
        entity_key = issue.get("entity_key")
    m_level, m_entity = _measure_target(kind, level, entity, ad_group, campaign)
    if (m_level, m_entity) == (level, entity):
        m_key = entity_key
    elif m_level == "campaign":
        m_key = entry.get("campaign_key")
    else:
        m_key = _key_of(_group_named(entry, m_entity))

    out = {
        "id": issue.get("id"),
        "campaign": campaign,
        "goal": goals[0] if len(goals) == 1 else None,
        "entity": entity,
        "entity_level": level,
        "entity_key": str(entity_key) if entity_key else None,
        "type": rtype,
        "variable": plan.get("variable"),
        "challenger_kind": plan.get("challenger_kind"),
        "challenger_ad": plan.get("challenger_ad"),
        "challenger": challenger,
        "control": control,
        "control_ad_key": (_key_of(_ad_named(entry, control))
                           or plan.get("control_ad_key")) if control else None,
        "ad_group": ad_group,
        # Every test carries its ad group's decision spend: it is the
        # experiment's minimum_spend_per_arm when the launch is filed, for a
        # form or offer test as much as for a creative one.
        "decision_spend_per_arm": (_decision_spend(issue, entry, ad_group)
                                   if rtype == "test" else None),
        # The utm_content the person puts on the new ad ("EXP-<issue id>",
        # creative.experiment_tag). Seeing it on an added ad is the strongest
        # match there is, for ANY kind of test.
        "tag": plan.get("tag"),
        "setup": plan.get("setup"),
        "pre_registered": _pre_registration(issue, today),
        "experiment": None,
        "measure_level": m_level,
        "measure_entity": m_entity,
        "measure_key": str(m_key) if m_key else None,
        "recommended_on": _iso(issue.get("open_since")) or today.isoformat(),
        "last_mentioned_on": today.isoformat(),
        "action": str(action or "")[:ACTION_CHARS] or None,
        "status": "open",
        "implemented_on": None,
        "matched_by": None,
        "match": None,
        "challenger_ad_key": None,
        "kind": kind,
        "measurement": None,
        "reason": None,
    }
    if kind == "post_click":
        out["status"] = "not_detectable"
        out["reason"] = NOT_DETECTABLE_WHY
    elif kind == "other":
        out["reason"] = "no rule detects this kind of change automatically"
    return out


def _pre_registration(issue: dict, today: date) -> dict | None:
    """The test as the report published it, before anything launched: the
    text the experiment's question and hypothesis are built from when the
    launch is detected. None for an issue with no test plan."""
    plan = issue.get("test_plan")
    if not isinstance(plan, dict):
        return None
    rec = issue.get("recommendation") or {}
    return {"published_on": today.isoformat(),
            "observation": issue.get("observation"),
            "diagnosis": issue.get("diagnosis"),
            "expected_outcome": rec.get("expected_outcome"),
            "decide_when": plan.get("decide_when"),
            "hold_constant": plan.get("hold_constant")}


#: Statuses in which an entry has not launched, so its plan may still be
#: refreshed from the latest report and its tag may still turn up.
UNLAUNCHED = ("open", "not_detectable", "expired")


def _issues(triage: list):
    """(entry, issue) for every act/test issue in shape-2 entries."""
    for entry in triage or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("issues"),
                                                           list):
            continue
        for issue in entry["issues"]:
            if not isinstance(issue, dict) or not issue.get("id"):
                continue
            rtype = str((issue.get("recommendation") or {}).get("type")
                        or "").lower()
            if rtype in ("act", "test"):
                yield entry, issue


# ----------------------------------------------------------- matching --

def _you(changes: dict) -> list:
    """(campaign, change) for every structured "you" change."""
    out = []
    for campaign, items in (changes or {}).items():
        for ch in items or []:
            if isinstance(ch, dict) and ch.get("by") == "you" and ch.get("kind"):
                out.append((campaign, ch))
    return out


def _quoted_in(name, text) -> bool:
    if not name or not text:
        return False
    return any(q in str(text) for q in (f'"{name}"', f"“{name}”",
                                        f"'{name}'", f"‘{name}’"))


def _match_one(e: dict, campaign: str, ch: dict, today: date):
    """(strength, implemented_on) when change `ch` implements entry `e`,
    else None. Strength is "strong" or "likely"."""
    if campaign != e.get("campaign"):
        return None
    kind, ck = e.get("kind"), ch.get("kind")
    rec_on = _date(e.get("recommended_on"))
    if kind == "creative_test":
        if ck != "ad_added":
            return None
        if e.get("ad_group") and ch.get("ad_group") \
                and e["ad_group"] != ch["ad_group"]:
            return None
        seen = _date(ch.get("first_spend_day")) or today
        if rec_on and seen < rec_on:
            return None
        name = ch.get("ad")
        strong = bool(name) and (name == e.get("challenger_ad")
                                 or _quoted_in(name, e.get("challenger")))
        return ("strong" if strong else "likely"), seen
    if kind == "delivery_cut":
        stopped = (ck == "ad_gone" or (ck == "status_changed"
                                       and ch.get("status_to") != "ACTIVE"))
        return ("strong", today) if stopped and _same_ad(e, ch) else None
    if kind == "creative_refresh":
        if ck == "wording_changed" and _same_ad(e, ch):
            return "strong", today
        return None
    if kind == "budget":
        if ck != "budget_changed":
            return None
        level = e.get("entity_level")
        if ch.get("level") == "ad_group":
            if level == "ad_group":
                same = (ch.get("ad_group") == e.get("entity")) or (
                    e.get("entity_key") and ch.get("ad_group_key")
                    == e.get("entity_key"))
                return ("strong", today) if same else None
            if level == "campaign":
                return "likely", today
            if e.get("ad_group") and ch.get("ad_group") == e.get("ad_group"):
                return "likely", today
            return None
        # A campaign budget (CBO) moves every ad group in it.
        return ("strong" if level == "campaign" else "likely"), today
    return None


def _same_ad(e: dict, ch: dict) -> bool:
    if e.get("entity_level") != "ad":
        return False
    if e.get("entity_key") and ch.get("ad_key"):
        return str(e["entity_key"]) == str(ch["ad_key"])
    return bool(ch.get("ad")) and ch.get("ad") == e.get("entity")


def _apply_match(e: dict, campaign: str, ch: dict, strength: str,
                 on: date, today: date) -> None:
    e["status"] = "implemented"
    e["implemented_on"] = on.isoformat()
    e["match"] = strength
    e["matched_by"] = f'{ch.get("what")} (seen in the review of {today.isoformat()})'
    e["reason"] = None
    if e.get("kind") == "creative_test":
        e["challenger_ad_key"] = ch.get("ad_key")
        e["challenger_ad_seen"] = ch.get("ad")


def _apply_override(e: dict, o: dict) -> None:
    if o.get("not_implemented"):
        e.update({"status": "open", "implemented_on": None, "match": "person",
                  "matched_by": "set by a person: not implemented",
                  "challenger_ad_key": None, "measurement": None,
                  "override": "not_implemented",
                  "reason": "a person marked this not implemented"})
        return
    on = _iso(o.get("implemented_on"))
    if not on:
        return
    if e.get("override") == "implemented" and e.get("implemented_on") == on:
        return              # already applied; keep its measurement
    e.update({"status": "implemented", "implemented_on": on,
              "match": "person", "matched_by": "set by a person",
              "measurement": None, "override": "implemented", "reason": None})
    if o.get("challenger_ad_key"):
        e["challenger_ad_key"] = str(o["challenger_ad_key"])


def update_ledger(ledger: dict, triage: list, changes: dict, today: date,
                  overrides: dict | None = None) -> dict:
    """The ledger after this run's review. Pure: returns a new dict.

    `triage` is the published entries (shape 2; entries without `issues` are
    skipped), `changes` is creative.changes_since's output, `today` the
    review date, `overrides` outcomes/overrides.json.
    """
    out = copy.deepcopy(ledger) if ledger else empty(None)
    out.setdefault("entries", {})
    entries = out["entries"]
    overrides = overrides or {}

    # 1. Register and refresh.
    for entry, issue in _issues(triage):
        iid = str(issue["id"])
        e = entries.get(iid)
        if e is None:
            entries[iid] = _register(entry, issue, today)
            continue
        e["last_mentioned_on"] = today.isoformat()
        if e.get("status") in UNLAUNCHED and not e.get("experiment"):
            # Before launch the latest published plan is the pre-registration;
            # an entry from before tags existed picks its tag up here.
            plan = issue.get("test_plan") or {}
            for k in ("tag", "setup"):
                if plan.get(k):
                    e[k] = plan[k]
            if e.get("decision_spend_per_arm") is None and plan:
                e["decision_spend_per_arm"] = _decision_spend(
                    issue, entry, e.get("ad_group"))
            e["pre_registered"] = (_pre_registration(issue, today)
                                   or e.get("pre_registered"))
        if e.get("status") == "expired":
            # Recommended again: it is open again.
            e["status"] = ("not_detectable" if e.get("kind") == "post_click"
                           else "open")
            e["reason"] = (NOT_DETECTABLE_WHY if e["status"] == "not_detectable"
                           else None)

    # 2. The person's corrections win.
    for iid, o in overrides.items():
        if iid in entries and isinstance(o, dict):
            _apply_override(entries[iid], o)

    # 3. Match the account's changes to open entries. Strong matches are
    #    placed first, so a new ad named as one test's challenger is not
    #    taken by another test it merely fits; a change implements one entry.
    taken = {str(e["challenger_ad_key"]) for e in entries.values()
             if e.get("challenger_ad_key")}
    used: set = set()
    you = _you(changes)

    # 3a. The tag first. An added ad carrying an entry's utm_content tag is
    #     that entry's launch, whatever kind of test it is -- which is how a
    #     form, offer or landing test (not_detectable on structure alone)
    #     becomes detectable. A tagged ad is never handed to another entry.
    tags = {str(e["tag"]) for e in entries.values() if e.get("tag")}
    for e in sorted(entries.values(), key=lambda e: str(e.get("id"))):
        if not e.get("tag") or e.get("override") \
                or e.get("status") not in UNLAUNCHED:
            continue
        for n, (campaign, ch) in enumerate(you):
            if (campaign, ch.get("id"), n) in used:
                continue
            if ch.get("kind") != "ad_added" or ch.get("utm_content") != e["tag"]:
                continue
            if ch.get("ad_key") and str(ch["ad_key"]) in taken:
                continue
            on = _date(ch.get("first_spend_day")) or today
            _apply_match(e, campaign, ch, "strong", on, today)
            e["matched_by"] = f"tagged utm_content={e['tag']}"
            e["challenger_ad_key"] = ch.get("ad_key")
            e["challenger_ad_seen"] = ch.get("ad")
            used.add((campaign, ch.get("id"), n))
            if ch.get("ad_key"):
                taken.add(str(ch["ad_key"]))
            break
    candidates = sorted(
        [e for e in entries.values()
         if e.get("status") == "open" and not e.get("override")],
        key=lambda e: (str(e.get("recommended_on")), str(e.get("id"))))
    for wanted in ("strong", "likely"):
        for e in candidates:
            if e.get("status") != "open":
                continue
            for n, (campaign, ch) in enumerate(you):
                if (campaign, ch.get("id"), n) in used:
                    continue
                if ch.get("kind") == "ad_added" and ch.get("ad_key") \
                        and str(ch["ad_key"]) in taken:
                    continue
                if ch.get("kind") == "ad_added" \
                        and ch.get("utm_content") in tags:
                    continue        # another entry's tagged launch
                m = _match_one(e, campaign, ch, today)
                if not m or m[0] != wanted:
                    continue
                _apply_match(e, campaign, ch, m[0], m[1], today)
                used.add((campaign, ch.get("id"), n))
                if ch.get("kind") == "ad_added" and ch.get("ad_key"):
                    taken.add(str(ch["ad_key"]))
                break

    # 4. Expire what nobody has mentioned for EXPIRE_DAYS.
    cutoff = _days_before(today, EXPIRE_DAYS)
    for e in entries.values():
        if e.get("status") == "open" and not e.get("override"):
            seen = _date(e.get("last_mentioned_on"))
            if seen and seen < cutoff:
                e["status"] = "expired"
                e["reason"] = (f"not implemented within {EXPIRE_DAYS} days of "
                               f"its last mention ({e.get('last_mentioned_on')})")

    out["updated_at"] = today.isoformat()
    return out


# -------------------------------------------------------- measurement --

UNAVAILABLE = "migration 018 is not applied"


async def _entity_key(slug: str, level: str, campaign: str, name) -> str | None:
    """An entity's key by name within its campaign; None when absent or
    ambiguous (two entities share the name)."""
    b = await context.brand(slug)
    if level == "campaign":
        rows = await fetch_all(
            "select campaign_key as key from ads.campaign "
            " where brand_id = %s and name = %s", (b["id"], name or campaign))
    elif level == "ad_group":
        rows = await fetch_all(
            """
            select g.ad_group_key as key
              from ads.ad_group g
              join ads.campaign c on c.campaign_key = g.campaign_key
             where c.brand_id = %s and c.name = %s and g.name = %s
            """, (b["id"], campaign, name))
    elif level == "ad":
        rows = await fetch_all(
            """
            select a.ad_key as key
              from ads.ad a
              join ads.campaign c on c.campaign_key = a.campaign_key
             where c.brand_id = %s and c.name = %s and a.name = %s
            """, (b["id"], campaign, name))
    else:
        return None
    return str(rows[0]["key"]) if len(rows) == 1 else None


def _classify(cpa, evidence, settled, spend_sufficient=None) -> tuple:
    """(result, basis) by comparison only."""
    if settled is None or settled < MIN_SETTLED_DAYS:
        return "too_early", (f"{settled if settled is not None else 0} settled "
                             f"day(s) after the change; {MIN_SETTLED_DAYS} "
                             f"are needed")
    if evidence in (None, "insufficient"):
        return "too_early", f"evidence is {evidence or 'not available'}"
    if spend_sufficient is False:
        return "too_early", "an arm is under its decision spend"
    if cpa is None:
        return "too_early", "no CPA on one side of the comparison"
    basis = (f"CPA effect {cpa}% on {evidence} evidence over {settled} "
             f"settled day(s)")
    if cpa <= IMPROVED_AT:
        return "improved", basis
    if cpa >= WORSE_AT:
        return "worse", basis
    return "neutral", basis


def _due(e: dict) -> bool:
    if e.get("status") in ("implemented", "measuring"):
        return True
    if e.get("status") == "measured":
        days = (e.get("measurement") or {}).get("settled_days")
        return days is None or days < FROZEN_AFTER
    return False


def _wrapper(name: str):
    """The 018 wrapper in intel/metrics.py, or None before it exists."""
    return getattr(metrics, name, None)


async def measure(ledger: dict, slug: str, settled_through) -> dict:
    """The ledger with every implemented / measuring entry (and every
    measured one not yet frozen) read against SQL. Returns a new dict."""
    out = copy.deepcopy(ledger)
    edge = _date(settled_through)
    for e in (out.get("entries") or {}).values():
        if not _due(e) or edge is None:
            continue
        impl = _date(e.get("implemented_on"))
        if impl is None:
            continue
        if impl > edge:
            e["status"] = "measuring"
            e["measurement"] = {
                "window": {"since": impl.isoformat(), "until": edge.isoformat()},
                "figures": {}, "settled_days": 0, "result": "too_early",
                "basis": "the change is after the settled edge"}
            continue

        if e.get("kind") == "creative_test":
            control = e.get("control_ad_key")
            if not control and e.get("control"):
                control = await _entity_key(slug, "ad", e.get("campaign"),
                                            e.get("control"))
                e["control_ad_key"] = control
            challenger = e.get("challenger_ad_key")
            if not (control and challenger):
                arm = "control" if not control else "challenger"
                e["reason"] = (f"no key for the {arm} ad, so the arms "
                               f"cannot be compared")
                continue
            fn = _wrapper("arm_compare")
            res = (await fn(slug, control, challenger, impl, edge,
                            decision_spend=e.get("decision_spend_per_arm"))
                   if fn else {"available": False, "why": UNAVAILABLE})
            if not res.get("available"):
                e["reason"] = res.get("why") or UNAVAILABLE
                continue
            row = res.get("row") or {}
            settled = row.get("settled_days")
            cpa = (row.get("effect_pct") or {}).get("cpa")
            result, basis = _classify(cpa, row.get("evidence_level"), settled,
                                      row.get("spend_sufficient"))
            window = {"since": impl.isoformat(), "until": edge.isoformat()}
            figures = {k: row.get(k) for k in (
                "control_m", "challenger_m", "effect_pct", "evidence_level",
                "spend_sufficient", "settled_days")}
        else:
            level = e.get("measure_level") or e.get("entity_level") or "campaign"
            key = e.get("measure_key")
            if not key:
                key = await _entity_key(slug, level, e.get("campaign"),
                                        e.get("measure_entity")
                                        or e.get("entity"))
                e["measure_key"] = key
            if not key:
                e["reason"] = "no key"
                continue
            before_since = _days_before(impl, BEFORE_DAYS)
            before_until = _days_before(impl, 1)
            fn = _wrapper("entity_outcome")
            res = (await fn(slug, level, key, before_since, before_until,
                            impl, edge)
                   if fn else {"available": False, "why": UNAVAILABLE})
            if not res.get("available"):
                e["reason"] = res.get("why") or UNAVAILABLE
                continue
            row = res.get("row") or {}
            settled = row.get("after_settled_days")
            cpa = (row.get("pct") or {}).get("cpa")
            result, basis = _classify(cpa, row.get("evidence_level"), settled)
            window = {"before": {"since": before_since.isoformat(),
                                 "until": before_until.isoformat()},
                      "after": {"since": impl.isoformat(),
                                "until": edge.isoformat()}}
            figures = {k: row.get(k) for k in (
                "cur_m", "pri_m", "pct", "evidence_level", "cpa_direction",
                "primary_driver", "after_settled_days")}

        e["measurement"] = {"window": window, "figures": figures,
                            "settled_days": settled, "result": result,
                            "basis": basis}
        e["reason"] = None
        frozen = settled is not None and settled >= FROZEN_AFTER
        e["status"] = ("measured" if result != "too_early" or frozen
                       else "measuring")
    return out


# ---------------------------------------------------------- reading it --

def _recent(e: dict, since: date) -> bool:
    d = _date(e.get("recommended_on"))
    return d is not None and d >= since


def _counts(entries: list) -> dict:
    measured = [e for e in entries if e.get("measurement")
                and e.get("status") in ("measuring", "measured")]
    return {
        "made": len(entries),
        "implemented": len([e for e in entries if e.get("implemented_on")]),
        **{r: len([e for e in measured
                   if (e.get("measurement") or {}).get("result") == r])
           for r in RESULTS},
    }


def scorecard(ledger: dict, today: date, days: int = 90) -> dict:
    """Counts over the recommendations made in the last `days` days."""
    since = _days_before(today, days)
    entries = [e for e in (ledger.get("entries") or {}).values()
               if _recent(e, since)]
    c = _counts(entries)
    by_kind = {}
    for kind in KINDS:
        mine = [e for e in entries if e.get("kind") == kind]
        if mine:
            by_kind[kind] = _counts(mine)
    measured = sorted(
        [e for e in entries if e.get("status") == "measured"],
        key=lambda e: (str(e.get("implemented_on")), str(e.get("id"))),
        reverse=True)
    return {"days": days, "made": c["made"], "implemented": c["implemented"],
            "by_result": {r: c[r] for r in RESULTS},
            "by_kind": by_kind,
            "recent": measured[:5]}


def for_campaign(ledger: dict, campaign: str) -> list:
    """The campaign's entries still in play: open, implemented, measuring."""
    return [e for e in (ledger.get("entries") or {}).values()
            if e.get("campaign") == campaign
            and e.get("status") in ("open", "implemented", "measuring")]


# ------------------------------------------------- filing the experiment --
# When a test's launch is detected, the experiment is filed as a PROPOSAL
# (intel record --kind experiment, run by scripts/suggest.py as a separate
# process: this module stays on the read pool). Everything in the payload
# comes from the ledger entry, i.e. from the test plan the report published
# before the launch -- the thresholds included, so nothing is chosen with the
# result in view. started_on and the conclusion are never in it
# (intel/shapes.py): a person records the launch and a person concludes.

#: The bar the experiment registers, in percent: the same 10% the ledger's
#: reading classifies by (IMPROVED_AT / WORSE_AT). One default for every
#: filed test, so the experiment and the ledger cannot disagree about what
#: counts. A different bar is a re-file under a new name; nothing edits a
#: filed proposal.
MINIMUM_EFFECT_PCT = 10

#: Ledger kinds that are tests, and so become experiments when launched.
TEST_KINDS = ("creative_test", "post_click", "other")

#: Variables read on the conversion rate rather than the CPA: the change is
#: after the click, so the click-through and the auction are held constant.
CONVERSION_RATE_VARIABLES = ("form", "landing")

NAME_CHALLENGER_CHARS = 60


def _short(text, n: int) -> str:
    t = " ".join(str(text or "").split())
    if len(t) <= n:
        return t
    return f"{t[:n].rstrip()}…"


def is_test(e: dict) -> bool:
    return e.get("type") == "test" and e.get("kind") in TEST_KINDS


def needs_experiment(e: dict) -> bool:
    """Implemented (by any match, a person's included), a test, and not yet
    filed. A failed filing leaves `experiment` empty, so it is tried again."""
    return (is_test(e) and not e.get("experiment")
            and e.get("status") in ("implemented", "measuring", "measured")
            and bool(e.get("implemented_on")))


def primary_metric(e: dict) -> str:
    v = _norm(e.get("variable"))
    if any(w in v for w in CONVERSION_RATE_VARIABLES):
        return "conversion_rate"
    return "cpa"


def experiment_name(e: dict) -> str:
    return (f"{e.get('id')} | {e.get('campaign')} | {e.get('variable')}: "
            f"{_short(e.get('challenger'), NAME_CHALLENGER_CHARS)}")


def _sentence(text) -> str | None:
    t = " ".join(str(text or "").split())
    if not t:
        return None
    return t if t[-1] in ".?!" else f"{t}."


def experiment_payload(e: dict, slug: str) -> tuple:
    """(payload, None), or (None, why it cannot be filed yet).

    Deterministic and figure-free beyond what the report already published:
    the question and the hypothesis are the issue's own observation,
    diagnosis and expected outcome, joined; the bar is MINIMUM_EFFECT_PCT;
    the spend per arm is the ad group's decision spend (its test_floor).
    Arm A is the control by ad key; arm B is the challenger by the tag when
    the launch was seen by its tag (or no ad key is known), else by the
    matched ad's key -- an untagged new ad is not in a utm_content arm.
    """
    spend = e.get("decision_spend_per_arm")
    if spend is None:
        return None, ("no decision spend per arm (the ad group's test_floor) "
                      "on the entry, so there is no minimum_spend_per_arm to "
                      "register")
    control = e.get("control_ad_key")
    if not control:
        return None, "no ad key for the control, so arm A has no members"
    tag = e.get("tag")
    tagged = bool(tag) and str(e.get("matched_by") or "").startswith("tagged")
    if tagged or (tag and not e.get("challenger_ad_key")):
        arm_b = {"label": "B challenger", "utm_content": tag}
    elif e.get("challenger_ad_key"):
        arm_b = {"label": "B challenger",
                 "ad_keys": [str(e["challenger_ad_key"])]}
    else:
        return None, ("no tag and no challenger ad key, so arm B has no "
                      "members")
    metric = primary_metric(e)
    pre = e.get("pre_registered") or {}
    challenger = _short(e.get("challenger"), 200)
    control_name = _short(e.get("control"), 120)
    ask = (f"Does the challenger ({challenger}) beat the control "
           f"({control_name}) on {metric.replace('_', ' ')} in "
           f"{e.get('campaign')}?")
    question = " ".join(p for p in (_sentence(pre.get("observation")), ask) if p)
    expected = _sentence(pre.get("expected_outcome"))
    hypothesis = " ".join(p for p in (
        _sentence(pre.get("diagnosis")),
        f"Expected: {expected}" if expected else None) if p) or (
        f"Changing the {e.get('variable')} to the challenger moves "
        f"{metric.replace('_', ' ')} against the control.")
    payload = {
        "brand": slug,
        "name": experiment_name(e),
        "question": question,
        "hypothesis": hypothesis,
        "primary_metric": metric,
        "minimum_effect_pct": MINIMUM_EFFECT_PCT,
        "minimum_spend_per_arm": spend,
        "arms": [{"label": "A control", "ad_keys": [str(control)]}, arm_b],
        "knowledge_snapshot": {
            "source": ("outcomes ledger: filed automatically when the "
                       "launch was detected"),
            "issue_id": e.get("id"),
            "campaign": e.get("campaign"),
            "ad_group": e.get("ad_group"),
            "goal": e.get("goal"),
            "kind": e.get("kind"),
            "variable": e.get("variable"),
            "control": e.get("control"),
            "challenger": e.get("challenger"),
            "tag": tag,
            "setup": e.get("setup"),
            "recommended_on": e.get("recommended_on"),
            "launch_detected": {"implemented_on": e.get("implemented_on"),
                                "match": e.get("match"),
                                "matched_by": e.get("matched_by"),
                                "challenger_ad_key": e.get("challenger_ad_key")},
            "pre_registered": pre or None,
            "minimum_effect_pct_is": ("the system default, the same bar the "
                                      "ledger reads results against"),
            "minimum_spend_per_arm_is": ("the ad group's test_floor "
                                         "decision_spend_per_arm"),
        },
    }
    return payload, None


def mark_filed(e: dict, result: dict, name: str, today: date) -> dict:
    """Record on the entry what `intel record` answered. An experiment of
    that name already existing is filed too: a run that filed it and then
    failed to save the ledger is the usual way that happens."""
    e["experiment"] = {"name": result.get("name") or name,
                       "id": str(result["id"]) if result.get("id") else None,
                       "filed_on": today.isoformat(),
                       "written": bool(result.get("written")),
                       "note": result.get("why")}
    e.pop("experiment_error", None)
    e.pop("experiment_skipped", None)
    return e


def mark_not_filed(e: dict, why: str, today: date, skipped: bool = False) -> dict:
    """Why this run did not file it; the next run tries again."""
    key = "experiment_skipped" if skipped else "experiment_error"
    e[key] = {"on": today.isoformat(), "why": str(why)[:300]}
    return e
