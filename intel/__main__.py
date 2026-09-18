"""python -m intel <verb> [flags] -- JSON to stdout, one sentence to stderr.

Every verb prints a JSON object and exits 0, or prints one sentence to stderr
and exits 2. That is growth-engine's shape (review/__main__.py, meta_ads/
__main__.py) and it is what lets a SKILL.md call these without a parser.

Fifteen read verbs and one write verb. There is no --approve, no --conclude and
no --activate, here or anywhere: see intel/shapes.py.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import db
from config import NotConfigured

from . import angles, experiments, health, live as live_mod, metrics
from .context import UnknownBrand
from .graph import GraphError, NotConfigured as TokenNotConfigured
from .shapes import SHAPES, ShapeError

CLI_PREFIX = "cli:"


def identity() -> str:
    """cli:<os user>.

    Never falls back to a configured operator name. growth-engine's
    auth.cli_operator makes the same refusal and explains it: attributing a
    CLI action to whoever happens to be listed first is fabrication dressed as
    a default. A row filed by a script should say so.
    """
    try:
        return f"{CLI_PREFIX}{getpass.getuser()}"
    except Exception:
        return f"{CLI_PREFIX}unknown"


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        # str, not float. A CPA is money and a float is an approximation of
        # money; the consumer here is a language model reading a number, and
        # 46.230000000000004 is a distraction it does not need.
        return str(o)
    if isinstance(o, UUID):
        return str(o)
    if isinstance(o, memoryview):
        return o.tobytes().decode("utf-8", "replace")
    raise TypeError(f"cannot serialise {type(o).__name__}")


def _day(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"not a date: {value!r}. Use YYYY-MM-DD.") from None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="intel", description=__doc__)
    sub = p.add_subparsers(dest="verb", required=True)

    def brandish(name, help_):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--brand", default="renegade")
        return s

    def windowed(name, help_, days=28):
        s = brandish(name, help_)
        s.add_argument("--days", type=int, default=days)
        s.add_argument("--until", help="YYYY-MM-DD; defaults to the settled edge")
        return s

    s = windowed("overview", "what ran, what it cost, what a reviewer thought")
    s.add_argument("--level", choices=("ad", "ad_group", "campaign"), default="ad")
    s.add_argument("--limit", type=int, default=200)

    s = windowed("compare", "this window against the equal one before it", 14)
    s.add_argument("--level", choices=("ad", "ad_group", "campaign"), default="ad")
    s.add_argument("--limit", type=int, default=100)

    s = windowed("why", "what moved CPA: rate effect vs mix effect", 7)
    s.add_argument("--limit", type=int, default=25)

    s = brandish("fatigue", "five named symptoms per ad, and their count")
    s.add_argument("--window", type=int, default=7, dest="window_days")
    s.add_argument("--until", help="YYYY-MM-DD; defaults to the settled edge")
    s.add_argument("--min-spend", type=float, default=100.0)
    s.add_argument("--include-unconfident", action="store_true",
                   help="include ads below the spend and impression floors")

    s = sub.add_parser("ad", help="one ad in full")
    s.add_argument("--key", required=True, dest="ad_key")
    s.add_argument("--days", type=int, default=90)

    s = windowed("trend", "one metric over consecutive buckets", 90)
    s.add_argument("--metric", default="cpa")
    s.add_argument("--level", choices=("ad", "ad_group", "campaign"),
                   default="campaign")
    s.add_argument("--bucket", type=int, default=7)

    s = windowed("angles", "the bank, with what each angle did", 90)
    s.add_argument("--product")
    s.add_argument("--min-spend", type=float, default=250.0)

    brandish("candidates", "reviewer angle handles the bank does not map yet")

    s = windowed("queue", "ads that spent money and carry no tag", 90)
    s.add_argument("--limit", type=int, default=50)

    s = windowed("coverage", "what we have never run", 365)
    s.add_argument("--product")
    s.add_argument("--min-spend", type=float, default=250.0)

    s = windowed("versus", "two angles over identical windows", 30)
    s.add_argument("--a", required=True)
    s.add_argument("--b", required=True)

    s = brandish("experiments", "what we decided to test, and where each stands")
    s.add_argument("--open", action="store_true", dest="open_only")
    s.add_argument("--angle")

    s = brandish("experiment", "one experiment, with its computed result")
    s.add_argument("--name", required=True)

    s = brandish("live", "what is running RIGHT NOW, and where the warehouse "
                         "has drifted from it")
    s.add_argument("--all", action="store_true", dest="all_statuses",
                   help="include paused and inactive, not just what is running")

    s = brandish("status", "is anything here worth reading? run this first")
    s.add_argument("--stale-after", type=int, default=2, dest="stale_after_days")

    s = sub.add_parser(
        "record",
        help="file a proposal or a tag. The only verb that writes.")
    s.add_argument("--kind", required=True, choices=sorted(SHAPES))
    s.add_argument("--json", required=True, dest="path",
                   metavar="PATH",
                   help="path to a JSON file. Not stdin: a payload that "
                        "arrived down a pipe leaves nothing to inspect when "
                        "the row turns out to be wrong.")
    return p


async def run(a: argparse.Namespace) -> dict:
    v = a.verb
    if v == "overview":
        return await metrics.overview(a.brand, a.days, _day(a.until), a.level, a.limit)
    if v == "compare":
        return await metrics.compare(a.brand, a.days, _day(a.until), a.level, a.limit)
    if v == "why":
        return await metrics.why(a.brand, a.days, _day(a.until), a.limit)
    if v == "fatigue":
        return await metrics.fatigue(a.brand, a.window_days, _day(a.until),
                                     a.min_spend, a.include_unconfident)
    if v == "ad":
        return await metrics.ad(a.ad_key, a.days)
    if v == "trend":
        return await metrics.trend(a.brand, a.metric, a.days, _day(a.until),
                                   a.level, a.bucket)
    if v == "angles":
        return await angles.angles(a.brand, a.days, _day(a.until), a.product,
                                   a.min_spend)
    if v == "candidates":
        return await angles.candidates(a.brand)
    if v == "queue":
        return await angles.queue(a.brand, a.days, _day(a.until), a.limit)
    if v == "coverage":
        return await angles.coverage(a.brand, a.product, a.days, _day(a.until),
                                     a.min_spend)
    if v == "versus":
        return await angles.versus(a.brand, a.a, a.b, a.days, _day(a.until))
    if v == "experiments":
        return await experiments.experiments(a.brand, a.open_only, a.angle)
    if v == "experiment":
        return await experiments.experiment(a.brand, a.name)
    if v == "live":
        return await live_mod.live(a.brand, active_only=not a.all_statuses)
    if v == "status":
        return await health.status(a.brand, a.stale_after_days)
    if v == "record":
        # Imported here and not at module scope, so that every read verb runs
        # in a process that has never imported db_owner.
        from . import record as record_mod
        return await record_mod.record(a.kind, a.path, identity())
    raise SystemExit(f"unimplemented verb {v!r}")


async def _main(a: argparse.Namespace) -> int:
    try:
        out = await run(a)
    except (UnknownBrand, ShapeError, ValueError, NotConfigured,
            TokenNotConfigured, GraphError) as exc:
        # One sentence, never a traceback. growth-engine's ui._oneline takes the
        # same line: a stack trace in a tool result is context the reader has to
        # dig through to find the sentence that was always the whole message.
        print(str(exc).strip().splitlines()[0] if str(exc) else repr(exc),
              file=sys.stderr)
        return 2
    finally:
        await db.close_read()
        if "intel.record" in sys.modules:
            await sys.modules["intel.record"].close()
    print(json.dumps(out, indent=2, ensure_ascii=False, default=_json_default))
    return 0


def main() -> int:
    a = build_parser().parse_args()
    return asyncio.run(_main(a))


if __name__ == "__main__":
    raise SystemExit(main())
