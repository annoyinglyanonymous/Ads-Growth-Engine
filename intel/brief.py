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

import json

from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

import chat
from db import fetch_all, fetch_one

from . import angles as angles_mod
from . import creative as creative_mod
from . import prose as prose_mod
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

    # -- Which ads carry creative labels, which is a different question ---
    #
    # ads.untagged_spend counts an ad as tagged when facet_effective resolves
    # an ANGLE for it. ads.angle is empty on this brand and will stay empty --
    # it is filled by a sibling repo that is not running -- so that function
    # correctly reports 100% untagged forever.
    #
    # Handed over alone, it reads as "no creative labels exist", and the model
    # said exactly that in the 2026-09-20 brief: "Nothing in this account is
    # tagged ... filing tags is what would let you ask which message is
    # working". scripts/tag.py had labelled 243 ads by then. intel/creative.py
    # hit the same wall and split the two the same way; this is that fix, in
    # the other pack.
    labels = await _section(degraded, "creative_labels", lambda: fetch_one(
        "select count(*)::int                                     as ads, "
        "       count(*) filter (where fe.hook is not null)::int   as with_a_hook, "
        "       count(*) filter (where fe.offer is not null)::int  as with_an_offer "
        "  from (select distinct f.ad_key "
        "          from ads.fact_ad_day f "
        "         where f.brand_id = %s::uuid "
        "           and f.day between %s and %s) spent "
        "  left join ads.facet_effective fe on fe.ad_key = spent.ad_key",
        (bid, since, end)))

    facts = {
        "trust": trust, "spend": spend, "movement": movement,
        "creative": creative, "copy": copy, "angles": angles,
        "untagged": {
            "from": "ads.untagged_spend", **(untagged or {}),
            "means": "spend whose ad resolves to an ANGLE. ads.angle is empty "
                     "on this brand, so this is 0 by construction and says "
                     "nothing about hook, offer or audience.",
        },
        "creative_labels": {
            "from": "ads.facet_effective", **(labels or {}),
            "means": "ads that spent and carry a hook or offer label. This is "
                     "the one that says whether the copy has been read.",
        },
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


# ---------------------------------------------------------------------------
# The brief, in sentences.
#
# This lived inline in ui.py, in the POST /brief/summary.json handler, which
# made it reachable only by pressing a button. scripts/brief.py publishes the
# same document on a schedule and could not reach the prompt at all, so the
# archive carried the cited readings and no prose.
#
# It is here rather than in ui.py because both callers are outside the web
# process' concerns and one of them has no web process at all. Same move
# intel/creative.py made for the suggestion, for the same reason.
# ---------------------------------------------------------------------------

BRIEF_SUMMARY_PROMPT = """\
You are explaining this brand's Meta ads for the window below to the person who
owns the business. They will not read the tables. They want to know what
happened, why, and what is worth doing about it -- in their words, not the
dashboard's.

Brand: {brand}
Window: {since} to {until} ({days} days). Settled through {settled}; the last
{unsettled} day(s) can still move as Meta restates conversions.

THE FACTS, already read from the verbs (row lists shortened -- run a verb only
if you need something that is not here):

{facts}

WHAT THE RULES ALREADY FOUND -- build on these, do not repeat them back:

{readings}

WRITE IT LIKE THIS

Eight to ten plain sentences in one or two paragraphs. No headings, no bullet
points, no markdown, no opening line about what you are about to do and no
closing offer.

Lead with the result: what was spent, what it produced, what each result cost,
and whether each of those is up or down against the window before -- if the
facts carry the prior window, say the direction in words ("up from", "down
from") and quote both figures; if they do not, say this is one window with
nothing to compare it to.

Then say why the cost per result moved, naming the one or two ads that drove
most of it by their names as given. Put rate effect and mix effect into plain
words every time: the rate effect is the ads themselves getting cheaper or
dearer, the mix effect is money shifting toward cheaper or dearer ads. Do not
use either term without its plain phrase beside it.

Then what is tiring: the one or two ads worth refreshing first, and what the
symptom is in ordinary language -- "costs more per thousand views than a
fortnight ago", "fewer of the people who see it click" -- never the field
name. Say whether the spend behind that reading is enough to trust.

If parts of the brief are empty because nothing has been tagged or filed, say
so once, in one sentence, and name what filing them would unlock. Then move on.

`untagged` and `creative_labels` are TWO different questions and the names do
not make that obvious. `untagged` is about ANGLES, which this brand has none of
and never will, so it reads 100% untagged always -- it is NOT evidence that the
copy is unlabelled. `creative_labels` is the one that says whether hook and
offer have been filed. Read that one before writing anything about tagging, and
never tell the reader to go and tag ads it says are already labelled.

Close with what is worth looking at next -- two or three concrete things,
taken from the readings and from waiting_on_you. Offer them as things to look
at, not as decisions: nothing here approves, pauses or concludes anything.

VOCABULARY

Every figure in the facts is ALREADY WRITTEN THE WAY IT SHOULD APPEAR -- money
as $15,748 or $41.44, rates as 0.33%. Copy them across exactly as they are.
Do not strip the $ or the %, do not re-round, and do not turn "$41.44" back
into a bare number. A figure with no unit on it is a count.

Say "cost per lead" (or "cost per conversion" if the goal is not leads) and put
"CPA" in brackets the first time only. Say "cost per thousand views" for CPM,
"the share of people who clicked" for link CTR, "how often the same person saw
it in a day" for daily frequency. Refer to ads by their names. Quote figures
exactly as they appear in the facts; do not round, total, average, or work out
a percentage that is not already there. If conversions in the last few days
are part of a decline, say in one clause that those days are not final.
"""


async def summarise(doc: dict, slug: str | None = None) -> dict:
    """Ask a session to explain an already-built brief. Returns chat.answer's dict.

    Takes the DOC rather than a brand and a window, so the prose is written
    against the same numbers the archive keeps. Handing it the brand instead
    would mean building the brief twice and explaining the second one.

    `default=str` rather than fastapi's jsonable_encoder: intel/ does not import
    the web framework, and scripts/suggest.py already serialises its pack this
    way. A Decimal becomes "34.8621" rather than 34.862100000000004, which is
    what the prompt means by "quote figures exactly as they appear".
    """
    # gaps is the standing roadmap, not this window; waiting_on_you is small
    # and is the answer to "so what do I do", so it rides along with facts.
    payload = {"facts": doc.get("facts"),
               "waiting_on_you": doc.get("waiting_on_you"),
               "degraded": doc.get("degraded")}

    # Formatted before the model sees it, by the same charts.money the tiles
    # call, so the sentence and the card cannot drift apart. intel/prose.py
    # carries the argument. The COPY is formatted; doc["facts"] keeps full
    # precision, because a brief whose numbers were rounded for reading is one
    # nobody can check afterwards.
    payload = prose_mod.for_prose(payload)
    readings = [r["says"] for r in doc.get("readings") or []]

    question = None
    for keep in (8, 3, 0):
        question = BRIEF_SUMMARY_PROMPT.format(
            brand=doc.get("brand") or slug, since=doc.get("since"),
            until=doc.get("until"), days=doc.get("days"),
            settled=doc.get("settled_through"),
            unsettled=doc.get("unsettled_days"),
            facts=json.dumps(creative_mod.compact(payload, keep), indent=1,
                             ensure_ascii=False, default=str),
            readings=json.dumps(readings, indent=1, ensure_ascii=False,
                                default=str))
        if len(question) <= creative_mod.PROMPT_BUDGET:
            break
    return await chat.answer(question, doc.get("brand") or slug)
