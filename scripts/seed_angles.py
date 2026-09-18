"""Propose a starting angle bank from the campaign angles she already approved.

    python scripts/seed_angles.py --brand renegade
    python scripts/seed_angles.py --brand renegade --out .intel/angles.json

IT PRINTS. IT DOES NOT FILE.

Promoting a per-campaign angle to a standing brand vocabulary is policy, not
transcription: the same words that were right for one campaign may be too
narrow, too broad, or a duplicate of something already in the bank. So this
prints proposals with a suggested family and stops, the way growth-engine's
`verdicts --draft-example` prints an entry and stops. A person reads them and
files the ones that should exist.

WHY PROMOTE RATHER THAN INVENT

public.campaign_angles is ALREADY an approved, human-signed, 2-4 word angle
vocabulary -- the `angles` skill writes them, she approves them, and they show
up in reviewer_verdicts. Inventing a second, parallel set of names would mean
ads.inherited_facet matches nothing, because that view joins on
lower(ads.angle.name) = lower(campaign_angles.name). The free tagging of every
future campaign depends on the two vocabularies being the same words.

Read-only: connects as ads_reader and runs one select.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
from config import NotConfigured  # noqa: E402

#: Keyword -> family. A SUGGESTION and nothing more; the whole reason this file
#: prints instead of filing is that a keyword match is not a judgement about
#: what an angle is doing. The person reading the output overrides it freely.
HINTS: list[tuple[str, str]] = [
    (r"cash|upfront|valuation|fee|price|pay|money|worth|multiple", "financial"),
    (r"retire|exit|succession|legacy|sell|step away|wind down", "exit"),
    (r"buyer|broker|listing|earnout|direct|deal|close", "buyer"),
    (r"now|timing|market|window|before|deadline|plan ahead", "timing"),
    (r"burnout|tired|stress|worry|uncertain|staff|client|protect|family",
     "emotional"),
    (r"carrier|market access|appointment|product|tool|platform|reach",
     "capability"),
]


def suggest_family(name: str, hypothesis: str | None) -> str:
    text = f"{name} {hypothesis or ''}".lower()
    for pattern, family in HINTS:
        if re.search(pattern, text):
            return family
    return "financial"


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s) or "angle"


async def collect(brand: str) -> dict:
    # ads.brand, not public.brands -- this runs on the read pool, which holds
    # no grant in public. See migrations/008_ads_brand_seam.sql.
    brand_row = await db.fetch_one(
        "select id, slug from ads.brand where slug = %s", (brand,))
    if not brand_row:
        raise SystemExit(f"no brand {brand!r}")

    existing = {
        r["slug"] for r in await db.fetch_all(
            "select slug from ads.angle where brand_id = %s", (brand_row["id"],))
    }

    # Through the ads seam, not public.* -- ads_reader deliberately holds no
    # grant on growth-engine's tables, so a read that reached them directly
    # would work for postgres in testing and return nothing in production.
    rows = await db.fetch_all(
        """
        select campaign_angle_id, name, hypothesis, rationale, product_slug
          from ads.campaign_angle_approved
         where brand_id = %s
         order by name
        """,
        (brand_row["id"],),
    )

    proposals, skipped = [], []
    for r in rows:
        slug = slugify(r["name"])
        item = {
            "brand": brand,
            "family": suggest_family(r["name"], r["hypothesis"]),
            "slug": slug,
            "name": r["name"],
            # The hypothesis is the closest thing to a definition that already
            # exists. It is usually not one, which is why the output says so.
            "definition": (r["hypothesis"] or r["rationale"]
                           or "TODO: what makes an ad THIS angle and not the "
                              "one next to it?"),
            "from_campaign_angle_id": str(r["campaign_angle_id"]),
            "product_slug": r["product_slug"],
        }
        (skipped if slug in existing else proposals).append(item)
    return {"brand": brand, "proposals": proposals, "already_in_bank": skipped}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brand", default="renegade")
    p.add_argument("--out", help="also write the proposals to this path, ready "
                                 "for `intel record --kind angle_proposal`")
    a = p.parse_args()

    try:
        data = asyncio.run(_run(a.brand))
    except NotConfigured as exc:
        print(str(exc).splitlines()[0], file=sys.stderr)
        return 2

    props = data["proposals"]
    print(f"\n{len(props)} angle(s) to consider for {a.brand}, "
          f"{len(data['already_in_bank'])} already in the bank.\n")
    for item in props:
        print(f"  {item['family']:<11} {item['slug']:<26} {item['name']}")
        if item["definition"].startswith("TODO"):
            print(f"  {'':<11} {'':<26} ^ needs a definition before filing")
    if props:
        print("\nThe family is a keyword guess. Read it before you trust it.")
        print("Nothing has been filed: promoting a campaign angle to a standing "
              "brand vocabulary is a decision.")
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(props, indent=2), encoding="utf-8")
        print(f"\nwritten to {a.out}")
    return 0


async def _run(brand: str) -> dict:
    try:
        return await collect(brand)
    finally:
        await db.close_read()


if __name__ == "__main__":
    raise SystemExit(main())
