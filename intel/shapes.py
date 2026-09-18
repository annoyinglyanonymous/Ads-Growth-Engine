"""The shapes `intel record` will accept, and nothing else.

A registry rather than per-verb validation, for growth-engine's reason
(engine/record.py): the set of things an agent may file is a governance
question, and a governance question should be answerable by reading one list.

WHAT IS NOT IN ANY SHAPE, AND WHY
--------------------------------
These fields exist in the schema and are deliberately unreachable from here:

    ads.angle.status = 'active'      an angle joins the vocabulary when a
    ads.angle.approved_by            person signs it. The writer hard-codes
    ads.angle.approved_at            'proposed' as a literal, and the
                                     angle_active_is_signed constraint makes
                                     that structural rather than polite.

    ads.experiment.started_on        a launch is a thing that happened in Ads
    ads.experiment.ended_on          Manager. The agent did not do it and
                                     cannot know it.

    ads.experiment.conclusion        naming a result is the decision. 044 has
    ads.experiment.concluded_by      no verdict column for the same reason and
    ads.experiment.concluded_at      says so: nothing here is read as
                                     permission, and nothing ever should be.

An agent may tighten and may not loosen -- growth-engine's auth.py:252 makes
the same asymmetry explicit. Filing a proposal is transcription. Approving one
is a judgement with a name attached.
"""

from __future__ import annotations

from typing import Any

#: kind -> (required fields, optional fields, one-line description)
SHAPES: dict[str, tuple[tuple[str, ...], tuple[str, ...], str]] = {
    "ad_facet": (
        ("ad_key", "copy_hash", "brand", "confidence", "rationale"),
        ("angle_slug", "hook", "offer", "audience", "context_id"),
        "What an ad's copy is doing: angle, hook, offer, audience.",
    ),
    "angle_proposal": (
        ("brand", "family", "slug", "name", "definition"),
        ("product_slug", "audience", "from_campaign_angle_id", "context_id"),
        "A candidate angle for the bank. Filed as 'proposed'; a person "
        "activates it.",
    ),
    "experiment": (
        ("brand", "name", "question", "hypothesis", "primary_metric",
         "minimum_effect_pct", "minimum_spend_per_arm", "arms"),
        ("context_id", "knowledge_snapshot"),
        "A test worth running, with the thresholds that would settle it "
        "registered before any number is looked at.",
    ),
}

METRICS = ("cpa", "cpc", "cost_per_link_click", "ctr", "link_ctr", "cpm",
           "conversion_rate")

CONFIDENCE = ("stated", "inferred")


class ShapeError(ValueError):
    """Raised with a sentence a person can act on, never a schema dump."""


def validate(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind not in SHAPES:
        raise ShapeError(
            f"unknown kind {kind!r}. Accepted: {', '.join(sorted(SHAPES))}.")
    required, optional, _ = SHAPES[kind]

    missing = [f for f in required if payload.get(f) in (None, "", [])]
    if missing:
        raise ShapeError(
            f"{kind} is missing required field(s): {', '.join(missing)}.")

    unknown = set(payload) - set(required) - set(optional)
    if unknown:
        # Named individually, because the common case is one of the fields the
        # module docstring lists as deliberately unreachable, and the person
        # filing it deserves to know that is why rather than guessing at a typo.
        raise ShapeError(
            f"{kind} does not accept: {', '.join(sorted(unknown))}. "
            f"If one of those is a launch date or a conclusion, that is "
            f"deliberate -- see intel/shapes.py.")

    if kind == "ad_facet" and payload["confidence"] not in CONFIDENCE:
        raise ShapeError(
            f"confidence must be one of {', '.join(CONFIDENCE)}, got "
            f"{payload['confidence']!r}. Use 'stated' only when the copy says "
            f"it outright.")

    if kind == "experiment":
        if payload["primary_metric"] not in METRICS:
            raise ShapeError(
                f"primary_metric must be one of {', '.join(METRICS)}, got "
                f"{payload['primary_metric']!r}.")
        arms = payload["arms"]
        if not isinstance(arms, list) or len(arms) < 2:
            raise ShapeError(
                "an experiment needs at least two arms; one arm is not a test.")
        for arm in arms:
            if not isinstance(arm, dict) or not arm.get("label"):
                raise ShapeError("every arm needs a label.")
            rules = [k for k in ("angle_slug", "utm_content", "ad_keys")
                     if arm.get(k)]
            if len(rules) != 1:
                raise ShapeError(
                    f"arm {arm.get('label')!r} must have exactly one "
                    f"membership rule (angle_slug, utm_content or ad_keys), "
                    f"got {len(rules)}. Two rules means an ad can be in the "
                    f"arm by one and out by the other.")
        for f in ("minimum_effect_pct", "minimum_spend_per_arm"):
            try:
                if float(payload[f]) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ShapeError(f"{f} must be a positive number.") from None

    return payload
