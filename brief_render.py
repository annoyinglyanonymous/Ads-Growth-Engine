"""The brief as markdown. A view of the fact pack, and nothing more.

Beside charts.py rather than inside intel/, for charts.py's reason: formatting
is not a verb. This module receives a finished document and decides how it
reads; it never asks the database anything and never derives a figure. If a
number is not in the pack, there is no way to put it on the page.

WHAT THE LAYOUT IS DOING

Facts and readings are different kinds of statement and the page has to make
that unmistakable, because the whole value of the brief is that a reader can
tell "CPA rose 22%" from "this looks like fatigue" at a glance.

  * A fact is a number a SQL function returned. Facts live in tables, and every
    table names the function under it -- `ads.fatigue.score`, not "score".
  * A reading is one of the named rules in intel/readings.py. Readings are
    block quotes, labelled, with the rule name and the pointers it fired on.
    A reading never appears inside a table cell.
  * A proposal is something nobody has agreed to, labelled as such, with the
    command that would file it.

The three are never interleaved. A reader skimming only the tables gets every
number and no opinion, which is the right default.

Section order is governance, not taste. Trust is first because a stale import
looks exactly like a quiet week. Structure changes come before fatigue because
most CPA moves are a budget edit rather than worn-out creative. "Waiting on
you" is last-but-one because it is the part that makes the next brief better.
"""

from __future__ import annotations

from typing import Any

from charts import money, num, pct


def _rates(row: dict | None, key: str) -> str:
    """One rate, out of the jsonb ads.rate() returned.

    Never a flat key on the row: a rate that is reachable as row["cpa"] is a
    rate somebody computed in Python on the way here.
    """
    r = (row or {}).get("rates") or {}
    v = r.get(key)
    return money(v) if key in ("cpa", "cpm", "cpc", "cost_per_link_click") else pct(v)


def _readings_for(doc: dict, section: str) -> str:
    out = []
    for r in doc.get("readings") or []:
        if r.get("section") != section:
            continue
        subj = (r.get("subject") or {}).get("name")
        head = f"**{subj}** " if subj else ""
        # The rule name, the provisional flag and the pointers a rule fired on
        # are no longer printed -- see templates/brief.html's reading() macro
        # for the reasoning. They are still on every reading and still in
        # briefs/<date>-<brand>.json, and readings.py still refuses to let a
        # rule state a figure it cannot cite. Only the display changed.
        out.append(f"> {head}{r['says']}")
    return "\n\n".join(out)


def _table(headers: list[str], rows: list[list[Any]], source: str) -> str:
    """`source` is still required, and is now deliberately unused.

    Every caller names the SQL function its rows came from. That argument is
    kept after the attribution stopped printing because it is the only place a
    reader of THIS file can see which function feeds which table -- deleting
    the parameter would remove that from the source as well as from the page,
    and the next person would have to read three modules to find out where a
    column comes from. Restore the <sub> line and it prints again.
    """
    if not rows:
        return "_No rows._"
    head = "| " + " | ".join(headers) + " |"
    rule = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join("" if c is None else str(c) for c in r) + " |"
                     for r in rows)
    return f"{head}\n{rule}\n{body}"


def markdown(doc: dict) -> str:
    f = doc.get("facts") or {}
    trust = f.get("trust") or {}
    spend = f.get("spend") or {}
    move = f.get("movement") or {}
    creative = f.get("creative") or {}
    copy = f.get("copy") or {}
    angles = f.get("angles") or {}
    untag = f.get("untagged") or {}
    exps = f.get("experiments") or {}
    wait = doc.get("waiting_on_you") or {}

    p: list[str] = []
    a = p.append

    a(f"# Ads brief — {doc.get('brand')}")
    a(f"**{doc.get('since')} to {doc.get('until')}** · generated "
      f"{doc.get('generated_at')}")
    if doc.get("caveat"):
        a(f"_{doc['caveat']}_")

    # -- 1. Trust ---------------------------------------------------------
    a("\n## Can you trust this?")
    a(_table(["Check", "Value"], [
        ["Healthy", trust.get("healthy")],
        ["Settled through", trust.get("settled_through")],
        ["Unsettled days", doc.get("unsettled_days")],
        ["Reading as", (trust.get("credential") or {}).get("confirmed_as")],
        ["Ad-days imported", (trust.get("coverage") or {}).get("ad_days")],
        ["Conversion definitions", trust.get("conversion_definitions")],
    ], "intel status"))
    for prob in trust.get("problems") or []:
        a(f"- ⚠ {prob}")

    match = trust.get("match") or {}
    if match:
        a("\n### Did our ads match what we wrote?")
        a(_table(["Approved", "With tracked_url", "Matched", "Rate"],
                 [[match.get("approved_assets"), match.get("with_tracked_url"),
                   match.get("matched"), match.get("match_rate")]],
                 "ads.match_health"))
        unmatched = match.get("unmatched") or []
        if unmatched:
            a("\n**Approved but never matched** — the expected value is what "
              "should be in the link in Ads Manager:")
            a(_table(["Campaign", "Variant", "Expected utm_content"],
                     [[u.get("campaign"), u.get("variant"),
                       f"`{u.get('expected_utm_content')}`"] for u in unmatched],
                     "ads.match_health.unmatched"))
    a(_readings_for(doc, "trust"))

    # -- 2. The window ----------------------------------------------------
    a("\n## The window")
    if spend.get("mixed_currency"):
        a("> **No money total is printed below.** This brand's accounts do not "
          "all report in one currency, so a total would be adding different "
          "units together. Filter to one account.")
    else:
        d = spend.get("delta") or {}
        a(_table(["Metric", "Value", "vs prior window"], [
            ["Spend", money(spend.get("spend")), money(d.get("spend"))],
            ["Conversions", num(spend.get("conversions")), num(d.get("conversions"))],
            ["CPA", _rates(spend, "cpa"), money(d.get("cpa"))],
            ["CPM", _rates(spend, "cpm"), None],
            ["Link CTR", _rates(spend, "link_ctr"), pct(d.get("link_ctr"))],
        ], "ads.window_metrics(level=brand) + ads.compare"))
    a(_readings_for(doc, "spend"))

    # -- 3. What moved it -------------------------------------------------
    a("\n## What moved it")
    sc = move.get("structure_changes") or {}
    if sc.get("available") is False:
        a(f"> **The change log is unavailable**, so whether anything changed in "
          f"the account is unknown rather than known to be nothing. "
          f"{sc.get('why') or ''}")
    elif sc.get("rows"):
        a("**What changed in the account** — read this before blaming creative:")
        a(_table(["When", "What", "Field", "From", "To"],
                 [[r.get("changed_at"), r.get("entity_name"), r.get("field"),
                   r.get("old_value"), r.get("new_value")] for r in sc["rows"][:15]],
                 "ads.structure_change"))
    # money(), not num(): every one of these is a CPA denominated in currency,
    # and a bare float beside a dollar figure reads as a different kind of
    # number. charts.fmt("cpa") makes the same choice.
    a(_table(["CPA change", "Rate effect", "Mix effect", "Split covers"],
             [[money(move.get("cpa_change")), money(move.get("rate_effect")),
               money(move.get("mix_effect")), move.get("attributable_share")]],
             "ads.cpa_bridge_totals"))
    a(_readings_for(doc, "movement"))

    # -- 4. What is tiring ------------------------------------------------
    a("\n## What is tiring")
    a(_table(["Ad", "Score", "Spend", "CPA now", "CPA before"],
             [[r.get("entity_name"), f"{r.get('score')}/5",
               money(r.get("spend_recent")),
               money(((r.get("recent") or {}).get("rates") or {}).get("cpa")),
               money(((r.get("prior") or {}).get("rates") or {}).get("cpa"))]
              for r in (creative.get("rows") or [])[:15]],
             "ads.fatigue"))
    if creative.get("frequency_note"):
        a(f"_{creative['frequency_note']}_")
    a(_readings_for(doc, "creative"))

    # -- 5. What the copy is doing ----------------------------------------
    a("\n## What the copy is doing")
    for dim in ("format", "family", "hook", "offer", "audience"):
        rows = copy.get(dim) or []
        if not rows:
            continue
        a(f"\n### By {dim}")
        ok = [r for r in rows if r.get("comparable_on_cost")]
        no = [r for r in rows if not r.get("comparable_on_cost")]
        a(_table(["Value", "Ads", "Spend", "CPA", "Rank in goal"],
                 [[r.get("value"), r.get("ads_run"), money(r.get("spend")),
                   _rates(r, "cpa"), r.get("rank_within_goal")] for r in ok],
                 f"ads.facet_performance(dimension={dim})"))
        if no:
            a("\n_Ran under more than one optimisation goal, so not rankable "
              "on cost:_")
            a(_table(["Value", "Ads", "Spend", "Goals"],
                     [[r.get("value"), r.get("ads_run"), money(r.get("spend")),
                       r.get("optimization_goal_count")] for r in no],
                     f"ads.facet_performance(dimension={dim})"))

    # -- 6. Angles --------------------------------------------------------
    a("\n## Which angles earned their place")
    a(_table(["Angle", "Spend", "Spend share", "Conv share", "CPA",
              "Rank in goal", "Inherited"],
             [[r.get("angle_name"), money(r.get("spend")),
               f"{r.get('spend_share_pct')}%", f"{r.get('conversion_share_pct')}%",
               _rates(r, "cpa"), r.get("rank_within_goal"), r.get("inherited_ads")]
              for r in (angles.get("rows") or [])[:20]],
             "ads.angle_performance"))
    a("_Spend share against conversion share is the comparison that matters. "
      "Inherited means the angle was read off the campaign_asset chain rather "
      "than judged by anyone._")
    if untag:
        a(_table(["Total spend", "Tagged", "Untagged", "Inheritable", "Stale tags"],
                 [[money(untag.get("total_spend")), money(untag.get("tagged_spend")),
                   money(untag.get("untagged_spend")),
                   money(untag.get("inheritable_spend")),
                   money(untag.get("stale_tag_spend"))]],
                 "ads.untagged_spend"))
    a(_readings_for(doc, "angles"))

    # -- 7. Never said ----------------------------------------------------
    a("\n## What we have never said")
    for gap in ("never_run", "under_spent"):
        rows = angles.get(gap) or []
        if rows:
            a(f"\n**{gap.replace('_', ' ').title()}** — "
              + ", ".join(str(r.get("angle_name")) for r in rows))

    props = doc.get("proposals") or []
    if props:
        a("\n### Proposals — not filed")
        for pr in props:
            tested = (" _Tested before._" if pr.get("already_tested") else "")
            a(f"- **{pr.get('angle_name')}** ({pr.get('family')}, "
              f"{pr.get('gap')}).{tested}")
            for e in pr.get("prior_experiments") or []:
                a(f"    - prior: _{e.get('name')}_ — "
                  f"{e.get('conclusion') or 'no conclusion was ever filed'}")
        a(f"\nTo file one: `{props[0].get('how_to_file')}`  \n"
          "_The minimum effect is deliberately absent: registering a threshold "
          "after reading the window is not registering it._")

    # -- 8. Experiments ---------------------------------------------------
    a("\n## Tests in flight")
    a(_table(["Name", "State", "Metric", "Conclusion"],
             [[r.get("name"), r.get("state"), r.get("primary_metric"),
               r.get("conclusion") or "—"] for r in exps.get("rows") or []],
             "intel experiments"))
    a(_readings_for(doc, "experiments"))

    # -- 9. Waiting on you ------------------------------------------------
    a("\n## Waiting on you")
    a("Nothing below can be done by an agent. Each one makes next week's brief "
      "better than this one.")
    a(_table(["What", "How many"], [
        ["Angles proposed, unsigned", len(wait.get("angles_unsigned") or [])],
        ["Experiments never launched", len(wait.get("experiments_unlaunched") or [])],
        ["Experiments ended, no conclusion", len(wait.get("experiments_unconcluded") or [])],
        ["Reviewer handles unmapped", len(wait.get("unmapped_handles") or [])],
        ["Spend carrying no tag", money(wait.get("untagged_spend"))],
    ], "intel angles + intel experiments + intel candidates"))
    a(f"\nSigning happens in growth-engine: {wait.get('where')}")

    # -- 10. What it could not say -----------------------------------------
    # Removed on request, in both renderers. `doc["degraded"]` and
    # `doc["gaps"]` are still built by intel/brief.py and still written to the
    # json, so nothing stopped being COMPUTED -- a section that failed to load
    # is still recorded as absent rather than as empty. It is simply no longer
    # printed. Re-add nine lines here and in templates/brief.html to bring it
    # back; the data has been waiting the whole time.

    return "\n\n".join(x for x in p if x and x.strip())
