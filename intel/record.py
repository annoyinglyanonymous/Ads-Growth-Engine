"""The one write verb.

THIS IS THE ONLY MODULE IN intel/ THAT IMPORTS db_owner. Everything else reads
through the ads_reader pool and has no write primitive available to it at all.
tests/test_read_only.py asserts exactly that, which is how the property stays
true after the next person adds a verb in a hurry.

What it writes: proposals and observations, all of them into ads.*, none of
them into public.*. What it cannot write is listed in shapes.py -- an angle's
approval, an experiment's launch, an experiment's conclusion. Those are
decisions, and every one of them has a person's name on it.

It takes a PATH, never stdin. growth-engine's engine/record.py makes the same
choice: a payload that arrived down a pipe leaves nothing on disk to look at
when the row turns out to be wrong, and "what exactly did it file?" is the
first question anybody asks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from db import fetch_one
from db_owner import close_owner, cursor

from .shapes import ShapeError, validate


async def _brand_id(slug: str) -> str:
    # ads.brand, not public.brands. The lookups here run on the READ pool --
    # only the write itself takes the owner cursor -- and ads_reader holds no
    # grant in public. See migrations/008_ads_brand_seam.sql.
    row = await fetch_one("select id from ads.brand where slug = %s", (slug,))
    if not row:
        raise ShapeError(f"no brand {slug!r}.")
    return str(row["id"])


async def _angle_id(brand_id: str, slug: str) -> str:
    row = await fetch_one(
        "select id, status from ads.angle where brand_id = %s and slug = %s",
        (brand_id, slug))
    if not row:
        raise ShapeError(
            f"no angle {slug!r} in the bank for this brand. File it first with "
            f"--kind angle_proposal, or pick one from `intel angles`.")
    if row["status"] == "retired":
        raise ShapeError(f"angle {slug!r} is retired; it should not be applied "
                         f"to new ads.")
    return str(row["id"])


async def record(kind: str, path: str, identity: str) -> dict:
    """Validate a payload and file it. Returns what landed, for the transcript."""
    p = Path(path)
    if not p.is_file():
        raise ShapeError(f"no file at {path}")
    try:
        payload: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ShapeError(f"{path} is not valid JSON: {exc}") from None
    if not isinstance(payload, dict):
        raise ShapeError(f"{path} must contain a JSON object, not a "
                         f"{type(payload).__name__}.")

    return await record_payload(kind, payload, identity)


async def record_payload(kind: str, payload: dict, identity: str) -> dict:
    """The same write, from a dict already in hand. Validated identically.

    `record` above still takes a PATH and still should: a person filing one
    proposal should leave the thing they filed on disk, because "a payload that
    arrived down a pipe leaves nothing to inspect when the row turns out to be
    wrong".

    A PASS is the case that reasoning does not cover. scripts/tag.py proposes
    two hundred facets in one run; writing two hundred single-row files to read
    them straight back would be ceremony, not evidence. It writes ONE file with
    every proposal and every dropped row, then files from memory -- so there is
    more on disk to look at afterwards, not less.

    Same `validate`, same shapes, same three kinds. Nothing here is a shortcut
    past the registry; it is the registry with the file step lifted out.
    """
    validate(kind, payload)
    brand_id = await _brand_id(payload["brand"])

    if kind == "ad_facet":
        return await _ad_facet(brand_id, payload, identity)
    if kind == "angle_proposal":
        return await _angle_proposal(brand_id, payload, identity)
    return await _experiment(brand_id, payload, identity)


async def _ad_facet(brand_id: str, d: dict, identity: str) -> dict:
    angle_id = (await _angle_id(brand_id, d["angle_slug"])
                if d.get("angle_slug") else None)
    async with cursor() as cur:
        await cur.execute(
            """
            insert into ads.ad_facet
                (ad_key, copy_hash, platform, brand_id, angle_id, hook, offer,
                 audience, source, confidence, rationale, tagged_by, context_id)
            values (%s, %s, 'meta', %s, %s, %s, %s, %s, 'tagged', %s, %s, %s, %s)
            on conflict (ad_key, copy_hash) do update set
                angle_id   = excluded.angle_id,
                hook       = excluded.hook,
                offer      = excluded.offer,
                audience   = excluded.audience,
                confidence = excluded.confidence,
                rationale  = excluded.rationale,
                tagged_by  = excluded.tagged_by,
                created_at = now()
            -- A person's tag is never overwritten by a pass. They looked at the
            -- ad and decided; this did not.
            where ads.ad_facet.source <> 'operator'
            returning ad_key, copy_hash, angle_id, source
            """,
            (d["ad_key"], d["copy_hash"], brand_id, angle_id, d.get("hook"),
             d.get("offer"), d.get("audience"), d["confidence"],
             d["rationale"], identity, d.get("context_id")),
        )
        row = await cur.fetchone()
    if row is None:
        return {"kind": "ad_facet", "written": False,
                "why": "an operator tag already exists for this ad and this "
                       "exact copy, and a tagging pass does not overwrite one."}
    return {"kind": "ad_facet", "written": True, **row}


async def _angle_proposal(brand_id: str, d: dict, identity: str) -> dict:
    async with cursor() as cur:
        await cur.execute(
            """
            insert into ads.angle
                (brand_id, family, slug, name, definition, product_slug,
                 audience, from_campaign_angle_id, status, added_by)
            -- 'proposed' is a SQL literal and not a parameter. There is no
            -- value a caller can pass that makes this row active; that needs
            -- approved_by, which needs a person (angle_active_is_signed).
            values (%s, %s, %s, %s, %s, %s, %s, %s, 'proposed', %s)
            on conflict (brand_id, slug) do nothing
            returning id, slug, status
            """,
            (brand_id, d["family"], d["slug"], d["name"], d["definition"],
             d.get("product_slug"), d.get("audience"),
             d.get("from_campaign_angle_id"), identity),
        )
        row = await cur.fetchone()
    if row is None:
        return {"kind": "angle_proposal", "written": False,
                "why": f"an angle with slug {d['slug']!r} already exists for "
                       f"this brand. Proposing it again would not change it."}
    return {"kind": "angle_proposal", "written": True, **row,
            "next": "It is 'proposed'. A person activates it in the UI; until "
                    "then it is excluded from coverage and from the bank."}


async def _experiment(brand_id: str, d: dict, identity: str) -> dict:
    async with cursor() as cur:
        await cur.execute(
            """
            insert into ads.experiment
                (brand_id, name, question, hypothesis, primary_metric,
                 minimum_effect_pct, minimum_spend_per_arm, proposed_by,
                 context_id, knowledge_snapshot)
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (brand_id, name) do nothing
            returning id, name
            """,
            (brand_id, d["name"], d["question"], d["hypothesis"],
             d["primary_metric"], d["minimum_effect_pct"],
             d["minimum_spend_per_arm"], identity, d.get("context_id"),
             json.dumps(d["knowledge_snapshot"])
             if d.get("knowledge_snapshot") else None),
        )
        row = await cur.fetchone()
        if row is None:
            return {"kind": "experiment", "written": False,
                    "why": f"an experiment named {d['name']!r} already exists "
                           f"for this brand."}

        arms = []
        for arm in d["arms"]:
            angle_id = (await _angle_id(brand_id, arm["angle_slug"])
                        if arm.get("angle_slug") else None)
            await cur.execute(
                """
                insert into ads.experiment_arm
                    (experiment_id, label, angle_id, utm_content, ad_keys)
                values (%s, %s, %s, %s, %s)
                returning label
                """,
                (row["id"], arm["label"], angle_id, arm.get("utm_content"),
                 arm.get("ad_keys")),
            )
            arms.append((await cur.fetchone())["label"])

    return {"kind": "experiment", "written": True, **row, "arms": arms,
            "next": "It is proposed, not running. started_on is set in the UI "
                    "when the test actually goes live, and the conclusion is "
                    "never written from here."}


async def close() -> None:
    """Shut the owner pool.

    Lives here rather than in __main__ so that db_owner is imported by exactly
    one module. tests/test_read_only.py asserts that, and a lifecycle import in
    the CLI would have quietly made the assertion false while the property it
    protects stayed true -- which is the kind of exception that becomes a habit.
    """
    await close_owner()
