"""The read surface cannot mutate, and it is a test that says so.

growth-engine proves the same property about verdicts.py the same way
(tests/test_verdicts.py: `assert "cursor" not in from_db`). A docstring saying
"read only" is a claim; this is the thing that is still true after somebody
adds a verb in a hurry on a Friday.

None of these tests need a database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INTEL = ROOT / "intel"

#: The only module allowed to write. Everything else is a read verb.
WRITER = "record.py"

READ_MODULES = sorted(
    p for p in INTEL.glob("*.py")
    if p.name not in {WRITER, "__init__.py"}
)

MUTATING_SQL = re.compile(
    r"\b(insert\s+into|update\s+\w|delete\s+from|alter\s+|drop\s+|"
    r"create\s+(table|view|function|role|schema)|grant\s+|revoke\s+|truncate)\b",
    re.IGNORECASE,
)


def _imports(path: Path) -> set[str]:
    """Every module name this file imports, however it imports it."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _string_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_there_are_read_modules_to_check():
    # Guards against the whole file silently passing because a rename emptied
    # the glob -- a green suite that checks nothing is worse than a red one.
    assert len(READ_MODULES) >= 5, [p.name for p in READ_MODULES]


@pytest.mark.parametrize("path", READ_MODULES, ids=lambda p: p.name)
def test_read_module_does_not_import_the_owner_pool(path: Path):
    assert "db_owner" not in _imports(path), (
        f"{path.name} imports db_owner. Only {WRITER} may. If this verb needs "
        f"to write, it is not a read verb."
    )


@pytest.mark.parametrize("path", READ_MODULES, ids=lambda p: p.name)
def test_read_module_contains_no_mutating_sql(path: Path):
    for text in _string_literals(path):
        # Skip prose: the docstrings here legitimately discuss inserts, and a
        # test that cannot tell a sentence from a statement gets deleted by the
        # third person it annoys. SQL in this codebase always names a schema.
        if "ads." not in text and "public." not in text:
            continue
        found = MUTATING_SQL.search(text)
        assert not found, (
            f"{path.name} contains what looks like mutating SQL "
            f"({found.group(0)!r}) in:\n{text[:200]}"
        )


def test_writer_exists_and_is_the_only_one():
    assert (INTEL / WRITER).is_file()
    writers = [p.name for p in INTEL.glob("*.py")
               if "db_owner" in _imports(p)]
    assert writers == [WRITER], (
        f"expected exactly one module to import db_owner, found {writers}")


def test_writer_has_no_approval_verbs():
    """The agent may propose and may not decide.

    Mirrors growth-engine's auth.assert_can_approve, which refuses any cli:
    identity. Here the refusal is structural: there is no code path at all, so
    there is nothing to refuse.
    """
    # Only SQL matters. The docstring legitimately explains why each of these
    # is absent, and a test that cannot tell a sentence from a statement is one
    # somebody deletes the third time it cries wolf.
    sql = [t for t in _string_literals(INTEL / WRITER)
           if "insert into" in t.lower() or "update " in t.lower()]
    assert sql, "expected to find SQL in the writer"
    # SQL comments too: the insert carries a `-- approved_by, which needs a
    # person` explaining why the column is absent, and that sentence is the
    # point rather than a violation of it.
    joined = re.sub(r"--[^\n]*", "", chr(10).join(sql)).lower()
    for forbidden in ("approved_by", "approved_at", "conclusion",
                      "concluded_by", "concluded_at", "started_on", "ended_on"):
        assert forbidden not in joined, (
            f"{WRITER} writes {forbidden!r}. That is a decision, and decisions "
            f"carry a person's name -- see intel/shapes.py.")


def test_cli_exposes_no_approval_flags():
    # Parse the argparse calls rather than grepping the file: the module
    # docstring says "there is no --approve", and a substring match cannot tell
    # that apart from an --approve that exists.
    tree = ast.parse((INTEL / "__main__.py").read_text(encoding="utf-8"))
    flags = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert flags, "found no argparse flags to check"
    for flag in ("--approve", "--conclude", "--activate", "--launch",
                 "--sign", "--reject"):
        assert flag not in flags, f"{flag} must not exist"


def test_settings_denies_the_migration_apply_path():
    """The agent prepares DDL and a person applies it.

    This database is shared and several Claude Code sessions run against it, so
    "never migrate unprompted" is a property of the tooling rather than a rule
    somebody remembers.
    """
    import json
    cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    deny = cfg["permissions"]["deny"]
    allow = cfg["permissions"]["allow"]
    assert any("ads_migrate.py --apply" in d for d in deny), deny
    assert not any("--apply" in a for a in allow), allow
    assert any("psql" in d for d in deny), "direct psql must stay denied"


# ---------------------------------------------------------------------------
# The grant the code did not have.
#
# Added after `intel status` died on its very first query with "permission
# denied for table brands". intel/context.py, intel/record.py and
# scripts/seed_angles.py all read public.brands directly on the READ pool, and
# ads_reader holds no grant in public -- deliberately, per 006 and CLAUDE.md.
#
# Nothing caught it for two reasons worth remembering. The tests above check
# that a read module never WRITES; reading the wrong schema is a different
# fault. And every manual run until then had been as postgres or ads_owner,
# both of which can read public -- the privileged path is the one you develop
# against, so the second role's failures only appear when it first connects.
#
# public.brands has no RLS, so this surfaced as an error. Against a table that
# does have RLS -- meta_ads, campaign_angles, ad_reviews -- the same mistake
# returns ZERO ROWS and no error, and reads as an empty database.
# ---------------------------------------------------------------------------

READ_POOL_MODULES = (sorted(INTEL.glob("*.py"))
                     + [ROOT / "scripts" / "seed_angles.py",
                        # ui.py runs its own SQL for the chart series, and
                        # ask.py is the newest surface on the read pool. Both
                        # are as able to reach for public.* as context.py was.
                        ROOT / "ui.py", ROOT / "ask.py"])

#: A string literal is SQL if it starts with a SQL verb once indentation is
#: stripped. Prose mentioning "public.*" -- record.py's docstring does -- must
#: not trip this, which is why it anchors at the start rather than searching.
SQL_START = re.compile(r"^(select|insert|update|delete|with)\b", re.IGNORECASE)


@pytest.mark.parametrize("path", READ_POOL_MODULES, ids=lambda p: p.name)
def test_nothing_on_the_read_pool_touches_public(path: Path):
    """Every public table reaches this schema through a seam view in ads.*."""
    for text in _string_literals(path):
        stripped = text.strip()
        if not SQL_START.match(stripped):
            continue
        assert "public." not in stripped, (
            f"{path.name} queries public.* on the read pool:\n"
            f"  {stripped[:200]}\n"
            f"ads_reader holds no grant in public. Add a seam view in ads.* "
            f"and read that -- see migrations/008_ads_brand_seam.sql."
        )


# ---------------------------------------------------------------------------
# The third pool, added when the importer moved in from growth-engine.
#
# meta_ads/ writes public.meta_* and must: that is its whole job. The risk is
# not that it writes, it is that its pool is now sitting in this repo where a
# read verb could reach it. db.py offers no cursor precisely so that intel/
# cannot write; importing db_meta would hand back everything that guard takes
# away, and it would look like a one-line convenience in a diff.
#
# So the pool is scoped by a list, the same way db_owner is.
# ---------------------------------------------------------------------------

#: Everything allowed to open the import pool. meta_ads/ does the importing;
#: sync.py is the scheduled wrapper around it; conftest opens it for dbtests.
MAY_IMPORT_DB_META = {
    "meta_ads", "scripts/sync.py", "tests/conftest.py",
}


def _may_import_meta(rel: str) -> bool:
    # The importer's own dbtests open the pool to read back what a pull wrote;
    # asserting on the rows is the point of them. Scoped to test_meta_* rather
    # than to tests/ as a whole, so a new test elsewhere cannot quietly become
    # the place the boundary is crossed.
    if rel.startswith("tests/test_meta_"):
        return True
    return rel in MAY_IMPORT_DB_META or rel.split("/")[0] in MAY_IMPORT_DB_META


def _all_sources() -> list[Path]:
    return [p for p in ROOT.rglob("*.py")
            if ".venv" not in p.parts and "__pycache__" not in p.parts]


def test_only_the_importer_opens_the_import_pool():
    offenders = []
    for path in _all_sources():
        rel = path.relative_to(ROOT).as_posix()
        if _may_import_meta(rel):
            continue
        if any(m == "db_meta" or m.startswith("db_meta.") for m in _imports(path)):
            offenders.append(rel)
    assert not offenders, (
        f"{offenders} import db_meta, the pool that can write public.*. "
        f"Only meta_ads/ may. If a read verb needs something from public, it "
        f"wants a seam view in ads.* -- see migrations/008_ads_brand_seam.sql.")


def test_the_importer_does_not_reach_for_the_ads_pools():
    """And the boundary holds in the other direction too.

    meta_ads/ owns public.meta_*; it has no business writing ads.*, which is
    derived from what it imports. If the importer ever wrote a fact row
    directly, ads.fact_ad_day would stop being a view over the truth and start
    being a second copy of it -- which is the staleness this whole design
    avoids.
    """
    for path in sorted((ROOT / "meta_ads").glob("*.py")):
        names = _imports(path)
        assert "db_owner" not in names, path.name
        assert "db" not in names, (
            f"{path.name} imports db (the ads_reader pool). The importer's "
            f"connection is db_meta.")
