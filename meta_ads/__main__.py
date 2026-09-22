"""The Meta importer's command line.

    python -m meta_ads --add-account act_123 --brand renegade [--label "..."]
    python -m meta_ads --list-accounts [--brand renegade]
    python -m meta_ads --pull --brand renegade [--since YYYY-MM-DD] [--until YYYY-MM-DD]

READ ONLY, per `meta_ads/client.py`: the token behind every one of these
verbs carries `ads_read`, and nothing here writes to an ad account. `--pull`
is what `review/context.py`'s `for_ad`/`for_all` are waiting on -- until it
has run at least once, `public.meta_ads` and `public.meta_ad_texts` are
empty and those raise `NotBuiltYet`.

WHY THIS DOES NOT REPORT "0 ADS" WHEN THE TOKEN IS MISSING
`GraphClient.from_settings()` raises `NotConfigured` at construction, before
any account is touched, and that propagates here as a plain one-sentence
exit rather than a per-account result -- an empty token is a state of the
install, not a fact about the account. See `meta_ads/client.py`'s docstring
on `__init__`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date

import identity
# Before any event loop exists: db.py's import installs the Windows
# selector-loop policy. Same requirement as review/__main__.py and main.py.
from db_meta import pool
from log import safe_console as _safe_console
from meta_ads import pull as meta_pull
from meta_ads import store
from meta_ads.client import MetaError

_ACCOUNT_ID = re.compile(r"^act_[0-9]+$")


def _date(value: str) -> date:
    return date.fromisoformat(value)


async def _brand(slug: str) -> dict:
    row = await store.brand_by_slug(slug)
    if not row:
        raise ValueError(f"no brand {slug!r}")
    return row


async def amain() -> int:
    ap = argparse.ArgumentParser(
        prog="python -m meta_ads",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brand", default="renegade",
                    help="brand slug (default: renegade)")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--add-account", metavar="ACT_ID",
                       help="register act_<digits> under --brand")
    group.add_argument("--list-accounts", action="store_true",
                       help="every account, active and inactive")
    group.add_argument("--deactivate-account", metavar="ACT_ID",
                       help="stop pulling act_<digits>. Reversible with "
                            "--add-account, and it keeps the account's "
                            "imported rows and its pull history")
    group.add_argument("--pull", action="store_true",
                       help="pull structure and insights for every active "
                            "account under --brand")
    ap.add_argument("--phase", choices=("structure", "insights"),
                    default=None,
                    help="run only this half of the pull. structure is the ad "
                         "list and its copy; insights is the daily numbers. "
                         "They use different Graph edges and different tables, "
                         "so one being broken is not a reason to skip the "
                         "other -- which is exactly why this flag exists.")
    ap.add_argument("--label", default=None,
                    help="what to call the account on screen, for "
                         "--add-account")
    ap.add_argument("--since", type=_date, default=None, metavar="YYYY-MM-DD",
                    help="insights window start, for --pull (default: "
                         "since the last successful pull, or 30 days back "
                         "on a first pull)")
    ap.add_argument("--until", type=_date, default=None, metavar="YYYY-MM-DD",
                    help="insights window end, for --pull (default: today)")
    args = ap.parse_args()

    await pool.open()
    try:
        if args.add_account:
            if not _ACCOUNT_ID.match(args.add_account):
                raise ValueError(
                    f"not a Meta ad account id: {args.add_account!r} "
                    f"(expected 'act_<digits>')")
            brand = await _brand(args.brand)
            out = await store.add_account(
                brand_id=brand["id"], act_id=args.add_account,
                label=args.label, added_by=identity.cli_operator())
        elif args.deactivate_account:
            if not _ACCOUNT_ID.match(args.deactivate_account):
                raise ValueError(
                    f"not a Meta ad account id: "
                    f"{args.deactivate_account!r} (expected 'act_<digits>')")
            out = await store.deactivate_account(
                act_id=args.deactivate_account)
            if out is None:
                # Not silently fine. "Nothing to deactivate" and "deactivated"
                # print the same way if this returns None, and the caller acts
                # on the first as though it were the second.
                raise ValueError(
                    f"{args.deactivate_account} is not registered, so there "
                    f"was nothing to deactivate. List them: "
                    f"python -m meta_ads --list-accounts")
        elif args.list_accounts:
            brand = await _brand(args.brand)
            out = await store.accounts_for(brand["id"], active_only=False)
        else:
            out = await meta_pull.run_pull(
                brand_slug=args.brand, started_by=identity.cli_operator(),
                since=args.since, until=args.until,
                phases=(args.phase,) if args.phase
                       else ("structure", "insights"))
            if not out:
                # Not a crash and not a success: the token was fine (or
                # run_pull would have raised) and there is simply nothing
                # registered to pull from. Printing `[]` and exiting 0 reads
                # as "imported nothing, all good", which is the one outcome
                # nobody investigates.
                raise ValueError(
                    f"no active ad account is registered for "
                    f"{args.brand!r}, so there is nothing to pull. Register "
                    f"one: python -m meta_ads --add-account act_<id> "
                    f"--brand {args.brand}")
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
        return 0
    except (ValueError, MetaError) as exc:
        # One sentence on stderr, not a traceback: a wrong brand, a
        # malformed account id, no token configured, or every account in
        # the brand having failed for the same Meta-side reason.
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        await pool.close()


def _utf8_stdout() -> None:
    """Make stdout carry what the ads actually say.

    Every verb prints `json.dumps(..., ensure_ascii=False)`, which is right --
    escaping an emoji to \ud83d\udc4e makes the copy unreadable for the person
    checking whether the import is correct. But Windows hands a console
    `cp1252` by default, and cp1252 cannot encode most of what ends up in ad
    copy.

    The failure is the worst shape available: the verb runs, the database is
    fine, every number is computed, and the process dies with
    UnicodeEncodeError at the final print. Nothing indicates the problem is a
    thumbs-down emoji in one headline rather than the query above it.

    Found on the first real import: renegade's copy contains U+1F44E and
    `intel overview` crashed on it after the data had already landed.

    `errors="replace"` rather than a raise: a glyph that a redirected file
    cannot hold should cost that character, never the whole answer.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a reconfigurable text stream -- a pipe some callers replace
            # with StringIO, or a Python without it. Printing ASCII-safe output
            # is better than refusing to start.
            pass


def main() -> int:
    _utf8_stdout()
    _safe_console()
    return asyncio.run(amain())


if __name__ == "__main__":
    sys.exit(main())
