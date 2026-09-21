"""The recurring read: one dated fact pack, assembled from the other verbs.

WHAT THIS MODULE IS ALLOWED TO DO

Select, order, label, and hand the result to intel/readings.py. That is all.

It performs no arithmetic of any kind -- no division, no summing a column, no
share, no average. Every figure it carries came out of a SQL function, and each
fact section records the function it came from in a `from` key so a reader can
trace any number in the brief back to the thing that produced it. A figure with
no `from` behind it is unsourced and there is nowhere in this shape to put one.

tests/test_brief.py enforces the rule by walking this module's AST rather than
by trusting the docstring: an arithmetic operator applied to a fetched row, or
a sum() over one, fails the suite. CLAUDE.md's first rule is "You never compute
a rate, a delta or a share yourself", and a brief is the loudest available place
to break it -- sixty numbers, dated, archived, and read as a summary of the week.

WHY COMPOSITION RATHER THAN ONE BIG QUERY

Every section here calls the same function the agent calls and the dashboard
renders. ui.py's docstring states the reason for the pages and it holds harder
for a brief: if this module had its own queries, the brief and `intel overview`
could disagree about the same window, and the disagreement would be DATED --
archived, quoted in prose, and impossible to adjudicate weeks later.

DEGRADING RATHER THAN FAILING

A brief that renders nine sections and names the tenth as unavailable is worth
far more than a traceback where the brief was. `_section` catches per section,
records what failed in `degraded`, and carries on. Two things make that safe
rather than sloppy:

  - a suppressed section is never rendered as zero, empty or unremarkable. It
    is named, with the error, in `degraded`, and the skill is told to read that
    list before concluding the data was silent about anything.
  - readings.py's rules tolerate a missing section and simply do not fire, so a
    degraded brief loses the sentence rather than gaining a wrong one.

This is also what lets the brief exist before every function it wants does. A
verb naming its own missing dependency is how migrations/003's "that is a
missing verb -- say so, and say which one" becomes something the software does
rather than something a person remembers to do.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from db import fetch_all, fetch_one

from . import angles as angles_mod
from . import experiments as exp_mod
from . import gaps as gaps_mod
from . import health, metrics, readings
from .metrics import _frame

#: Sections whose absence changes what the rest of the brief means, rather than
#: merely shortening it. Named here so `degraded` can say which kind a failure
#: was instead of listing eight equal-looking rows.
LOAD_BEARING = frozenset({"trust", "spend"})


async def _section(degraded: list[dict], name: str,
                   coro: Callable[[], Awaitable[Any]]) -> Any:
    """Run one section, or record why it is missing and return None."""
    try:
        return await coro()
    except Exception as exc:
        degraded.append({
            "section": name,
            "guard": f"{type(exc).__name__}: {str(exc).strip().splitlines()[0]}",
            "load_bearing": name in LOAD_BEARING,
        })
        return None


async def brief(slug: str, days: int = 28, until: date | None = None,
                product: str | None = None) -> dict:
    """Everything worth reading about one brand's ads in one window."""
    f = await _frame(slug, days, until)
    bid, since, end = f["brand_id"], f["since"], f["until"]
    degraded: list[dict] = []

    # -- Trust ------------------------------------------------------------
    # First, and never collapsed. A stale import looks exactly like a quiet
    # week, so everything below is read against this or not read at all.
    status = await _section(degraded, "trust", lambda: health.status(slug))
    match = await _section(degraded, "trust.match", lambda: fetch_one(
        "select approved_assets, with_tracked_url, matched, match_rate, "
        "       unmatched "
        "  from ads.match_health(%s::uuid, %s, %s)", (bid, since, end)))

    trust = {
        "from": "intel status + ads.match_health",
        **{k: (status or {}).get(k) for k in
           ("healthy", "problems", "settled_through", "credential", "coverage",
            "conversion_definitions", "untagged_ads", "unmapped_angle_handles",
            "accounts")},
        "match": match or {},
    }

    # -- The window in totals --------------------------------------------
    spend_row = await _section(degraded, "spend", lambda: fetch_one(
        "select spend, conversions, impressions, clicks, link_clicks, rates, "
        "       currencies "
        "  from ads.window_metrics(%s::uuid, %s, %s, 'brand')",
        (bid, since, end)))
    spend_cmp = await _section(degraded, "spend.compare", lambda: fetch_one(
        "select current_m, prior_m, delta, pct, appeared, disappeared "
        "  from ads.compare(%s::uuid, %s, %s, 'brand')", (bid, since, end)))

    spend = {"from": "ads.window_metrics(level=brand) + ads.compare", **(spend_row or {})}
    if spend_row is not None:
        # A comparison, not a computation: the function returned the count.
        spend["mixed_currency"] = (spend_row.get("currencies") or 1) > 1
    if spend_cmp:
        spend["delta"] = spend_cmp.get("delta")
        spend["pct"] = spend_cmp.get("pct")
        spend["appeared"] = spend_cmp.get("appeared")
        spend["disappeared"] = spend_cmp.get("disappeared")

    # -- What moved it ----------------------------------------------------
    # Structure changes render above fatigue, deliberately: most of the time a
    # CPA move is a budget edit rather than worn-out creative (007:16-20).
    why = await _section(degraded, "movement",
                         lambda: metrics.why(slug, min(days, 14), until))
    totals = await _section(degraded, "movement.totals", lambda: fetch_one(
        "select cpa_change, rate_effect, mix_effect, attributable_effect, "
        "       unattributable_effect, attributable_share, attributable_ads, "
        "       unattributable_ads "
        "  from ads.cpa_bridge_totals(%s::uuid, %s, %s)", (bid, since, end)))

    movement = {
        "from": "ads.cpa_bridge_totals + intel why",
        **(totals or {}),
        "rows": (why or {}).get("rows") or [],
        "structure_changes": (why or {}).get("structure_changes") or {},
    }

    # -- What is tiring ---------------------------------------------------
    # include_unconfident is False on purpose, so an unreadable row never
    # reaches the rules at all and can only ever appear as a count.
    fatigue = await _section(degraded, "creative",
                             lambda: metrics.fatigue(slug, 7, until))
    creative = {
        "from": "ads.fatigue",
        "rows": (fatigue or {}).get("rows") or [],
        "suppressed_unconfident": (fatigue or {}).get("suppressed_unconfident"),
        "frequency_note": (fatigue or {}).get("frequency_note"),
    }

    # -- What the copy is doing -------------------------------------------
    # format first, and that ordering is deliberate: it needs no tag and no
    # signature, so this section says something real even when the bank is
    # inert and every angle number below is empty.
    copy: dict[str, Any] = {"from": "ads.facet_performance"}
    for dim in ("format", "family", "hook", "offer", "audience"):
        copy[dim] = await _section(degraded, f"copy.{dim}", lambda d=dim: fetch_all(
            "select dimension, value, ads_run, spend, conversions, rates, "
            "       optimization_goals, optimization_goal_count, "
            "       comparable_on_cost, spend_sufficient, rank_within_goal "
            "  from ads.facet_performance(%s::uuid, %s, %s, %s::text)",
            (bid, since, end, d))) or []

    # -- Which angles earned their place ----------------------------------
    perf = await _section(degraded, "angles", lambda: fetch_all(
        "select angle_id, family, angle_slug, angle_name, definition, ads_run, "
        "       tagged_ads, inherited_ads, spend, conversions, rates, "
        "       spend_share_pct, conversion_share_pct, optimization_goals, "
        "       optimization_goal_count, comparable_on_cost, spend_sufficient, "
        "       rank_within_goal, first_run, last_run, state "
        "  from ads.angle_performance(%s::uuid, %s, %s, %s::text)",
        (bid, since, end, product)))
    cov = await _section(degraded, "angles.coverage",
                         lambda: angles_mod.coverage(slug, product, 365, until))
    bank = await _section(degraded, "angles.bank",
                          lambda: angles_mod.angles(slug, days, until, product))

    angles = {
        "from": "ads.angle_performance + intel coverage",
        "rows": perf or [],
        "never_run": (cov or {}).get("never_run") or [],
        "under_spent": (cov or {}).get("under_spent") or [],
        "prior_experiments": (cov or {}).get("prior_experiments") or [],
    }

    # -- How much of the account the shares above are a share of ----------
    untagged = await _section(degraded, "untagged", lambda: fetch_one(
        "select total_spend, tagged_spend, untagged_ads, untagged_spend, "
        "       inheritable_ads, inheritable_spend, stale_tag_ads, "
        "       stale_tag_spend "
        "  from ads.untagged_spend(%s::uuid, %s, %s)", (bid, since, end)))

    # -- Tests in flight --------------------------------------------------
    exps = await _section(degraded, "experiments",
                          lambda: exp_mod.experiments(slug))
    cands = await _section(degraded, "candidates",
                           lambda: angles_mod.candidates(slug))

    experiments = {
        "from": "intel experiments",
        "rows": (exps or {}).get("rows") or [],
        "awaiting_launch": (exps or {}).get("awaiting_launch"),
    }

    facts = {
        "trust": trust, "spend": spend, "movement": movement,
        "creative": creative, "copy": copy, "angles": angles,
        "untagged": {"from": "ads.untagged_spend", **(untagged or {})},
        "experiments": experiments,
    }

    # Everything only she can do. This section is what makes the brief
    # recurring rather than a report: it ends with the three or four signatures
    # that make next week's brief better than this one.
    waiting = {
        "angles_unsigned": (bank or {}).get("awaiting_approval") or [],
        "experiments_unlaunched": [r for r in experiments["rows"]
                                   if r.get("state") == "proposed"],
        "experiments_unconcluded": [r for r in experiments["rows"]
                                    if r.get("state") == "awaiting_conclusion"],
        "unmapped_handles": (cands or {}).get("rows") or [],
        "untagged_spend": (untagged or {}).get("untagged_spend"),
        "where": "http://127.0.0.1:8000/angles",
    }

    doc = {
        "verb": "brief", **f, "product": product,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # A decline measured across a partial day is the most common false
        # alarm in this system, so the whole document carries the flag rather
        # than each section arguing about it.
        "provisional": bool(f["unsettled_days"]) or (status or {}).get("healthy") is False,
        "facts": facts,
        "waiting_on_you": waiting,
        "proposals": _proposals(angles),
        "gaps": gaps_mod.register(),
        "degraded": degraded,
    }
    doc["readings"] = readings.read(doc)
    return doc


def _proposals(angles: dict) -> list[dict]:
    """Angles worth testing, stated as proposals and filed by nobody.

    Two rules, both from 005's header rather than from taste:

    `minimum_effect_pct` is left null. The thresholds exist so that somebody
    decides what would convince them BEFORE seeing a number, and a proposal
    written immediately after reading this week's numbers has inverted the
    mechanism. Suggesting it here would launder a post-hoc figure into a
    pre-registration.

    `already_tested` gates the whole thing. Proposing the same question for the
    seventh time is the specific failure the experiment table exists to
    prevent, so an angle with prior art leads with what happened instead.
    """
    prior: dict[str, list] = {}
    for e in angles.get("prior_experiments") or []:
        prior.setdefault(e.get("angle_slug"), []).append(e)

    out = []
    for gap in ("never_run", "under_spent"):
        for a in angles.get(gap) or []:
            slug = a.get("angle_slug")
            seen = prior.get(slug) or []
            out.append({
                "angle_slug": slug,
                "angle_name": a.get("angle_name"),
                "family": a.get("family"),
                "definition": a.get("definition"),
                "gap": gap,
                "already_tested": bool(seen),
                "prior_experiments": seen,
                "would_register": {
                    "primary_metric": "cpa",
                    "minimum_effect_pct": None,
                    "note": "Both thresholds are hers, and the effect one is "
                            "deliberately absent here: registering it after "
                            "reading the window is not registering it.",
                },
                "filed": False,
                "how_to_file": "python -m intel record --kind experiment --json <path>",
            })
    return out
