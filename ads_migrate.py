"""Apply numbered SQL migrations to the ads schema, once each, in order.

    python ads_migrate.py --status     # what is applied, what is pending
    python ads_migrate.py --dry-run    # list the files, connect to nothing
    python ads_migrate.py --print 003  # print one file, so it can be reviewed
                                       #   or pasted into the Supabase editor
    python ads_migrate.py --apply      # apply pending migrations

THE APPLY PATH IS NOT AVAILABLE TO THE AGENT. .claude/settings.json allows
--status, --dry-run and --print, and denies --apply. That is deliberate, and it
is why --apply is an explicit flag rather than the bare default that
growth-engine/migrate.py uses: this database is shared, several Claude Code
sessions run against it, and "never migrate unprompted" should be a property of
the tooling rather than a rule somebody remembers. The agent prepares the DDL
and prints it; a person applies it.

TRACKING TABLE. Applied files are recorded in ads.schema_migrations, NOT in
public.schema_migrations. growth-engine's runner keys that table on filename,
and its own migrations are numbered from 001 -- so sharing the table would let
this repo's 001_ads_schema.sql collide with growth-engine's 001_kb_init.sql,
and the loser would be silently considered already applied. Two ledgers, two
globs, no overlap: `python migrate.py --status` over there never sees these
files and never reports drift.

CREDENTIALS. Reads ADS_MIGRATE_URL if set, else ADS_DATABASE_URL. The first
application has to run as a role that may CREATE SCHEMA and CREATE ROLE -- in
Supabase that is postgres, so set ADS_MIGRATE_URL to the postgres url for that
run, or paste the files into the SQL editor, which is equivalent: each file
carries its own BEGIN/COMMIT so none of those paths is privileged. After 006
has created the roles, ADS_DATABASE_URL is the ads_owner url and this script
has nothing further to do.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"

# The schema has to exist before its own ledger can live in it. Both are
# idempotent, and neither is the schema's real definition -- 001 is, and 001 is
# tracked like every other file.
TRACKING_DDL = """
create schema if not exists ads;
create table if not exists ads.schema_migrations (
    filename   text primary key,
    checksum   text        not null,
    applied_at timestamptz not null default now()
)
"""


def load_database_url() -> str:
    for key in ("ADS_MIGRATE_URL", "ADS_DATABASE_URL"):
        url = os.environ.get(key)
        if url:
            return url

    env_path = PROJECT_ROOT / ".env"
    if env_path.is_file():
        try:
            from dotenv import dotenv_values
        except ImportError:
            sys.exit("python-dotenv not available and ADS_DATABASE_URL is not set")
        values = dotenv_values(env_path) or {}
        url = values.get("ADS_MIGRATE_URL") or values.get("ADS_DATABASE_URL")
        if url:
            if "sslmode=" not in url:
                print("  warning: url has no sslmode=require", file=sys.stderr)
            return url

    sys.exit("ADS_DATABASE_URL not found in the environment or .env")


def discover() -> list[tuple[str, str, str]]:
    """-> [(filename, sql, checksum)] sorted by filename."""
    if not MIGRATIONS_DIR.is_dir():
        sys.exit(f"no migrations directory at {MIGRATIONS_DIR}")
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        out.append((path.name, sql, hashlib.sha256(sql.encode("utf-8")).hexdigest()))
    return out


def connect(url: str):
    try:
        import psycopg
    except ImportError:
        sys.exit("psycopg is not installed. pip install psycopg[binary]")
    # autocommit: each .sql file manages its own transaction
    return psycopg.connect(url, autocommit=True)


def applied_state(conn) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute(TRACKING_DDL)
        cur.execute("select filename, checksum from ads.schema_migrations")
        return dict(cur.fetchall())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true",
                        help="show applied vs pending, apply nothing")
    parser.add_argument("--dry-run", action="store_true",
                        help="list migrations without connecting")
    parser.add_argument("--print", dest="show", metavar="PREFIX",
                        help="print the migration whose filename starts with "
                             "PREFIX and exit")
    parser.add_argument("--apply", action="store_true",
                        help="apply pending migrations -- denied to the agent "
                             "by .claude/settings.json; a person runs this")
    args = parser.parse_args()

    migrations = discover()
    if not migrations:
        print(f"no .sql files in {MIGRATIONS_DIR}")
        return 0

    if args.show:
        for name, sql, _ in migrations:
            if name.startswith(args.show):
                print(sql)
                return 0
        print(f"no migration starting with {args.show!r}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"{len(migrations)} migration(s) in {MIGRATIONS_DIR}:")
        for name, sql, checksum in migrations:
            # update/delete/do/grant belong here too: a migration that only
            # changes DATA otherwise reports "~0 statements", which reads as
            # "this file does nothing" to the person running --dry-run to find
            # out what it does.
            statements = sum(1 for line in sql.splitlines()
                             if line.strip().lower().startswith(
                                 ("create", "alter", "insert", "drop", "comment",
                                  "update", "delete", "do ", "grant", "revoke",
                                  "truncate")))
            print(f"  {name:<34} {len(sql):>6} bytes  ~{statements} statements  "
                  f"{checksum[:12]}")
        print("\n(dry run -- nothing connected, nothing applied)")
        return 0

    if not (args.status or args.apply):
        parser.error("choose one of --status, --dry-run, --print or --apply. "
                     "There is no bare default that writes; see the module "
                     "docstring.")

    conn = connect(load_database_url())
    try:
        already = applied_state(conn)

        pending, changed = [], []
        for name, sql, checksum in migrations:
            if name not in already:
                pending.append((name, sql, checksum))
            elif already[name] != checksum:
                changed.append(name)

        for name in changed:
            print(f"  CHANGED SINCE APPLIED: {name} -- write a new migration "
                  f"instead of editing an applied one", file=sys.stderr)

        if args.status:
            print(f"applied: {len(already)}   pending: {len(pending)}")
            for name in sorted(already):
                print(f"  [x] {name}")
            for name, _, _ in pending:
                print(f"  [ ] {name}")
            return 1 if changed else 0

        if not pending:
            print("nothing to apply -- the ads schema is up to date")
            return 1 if changed else 0

        for name, sql, checksum in pending:
            print(f"applying {name} ...", end=" ", flush=True)
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    "insert into ads.schema_migrations (filename, checksum) "
                    "values (%s, %s)", (name, checksum))
            print("ok")

        print(f"\napplied {len(pending)} migration(s)")
        return 1 if changed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
