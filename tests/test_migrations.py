"""Properties of the SQL that would otherwise only be discovered in production.

None of these need a database. They are cheap, and each one pins a decision
that is easy to undo by accident.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = sorted((ROOT / "migrations").glob("*.sql"))

RATE_NAMES = ("ctr", "cpc", "cpm", "cpa", "cost_per_lead", "conversion_rate",
              "link_ctr", "cost_per_link_click", "lp_view_rate")


def _strip_comments(sql: str) -> str:
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


def test_there_are_migrations():
    assert len(MIGRATIONS) >= 6, [p.name for p in MIGRATIONS]


def test_numbering_is_unique_and_ordered():
    numbers = [p.name.split("_", 1)[0] for p in MIGRATIONS]
    assert len(numbers) == len(set(numbers)), f"duplicate prefix in {numbers}"
    assert numbers == sorted(numbers)


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_each_file_governs_its_own_transaction(path: Path):
    """ads_migrate.py runs with autocommit on and lets the file decide.

    That is what makes the same file apply identically through the runner,
    through `supabase db push`, or pasted into the dashboard SQL editor --
    none of those three paths is privileged.
    """
    code = _strip_comments(path.read_text(encoding="utf-8")).lower()
    assert len(re.findall(r"\bbegin\s*;", code)) == 1, path.name
    assert len(re.findall(r"\bcommit\s*;", code)) == 1, path.name


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_no_rate_is_ever_an_average_of_daily_ratios(path: Path):
    """The trap store.overview_rows:432 already documents.

    An average of daily ratios weights a day with one lead as heavily as a day
    with fifty. Every rate in this schema is sum-over-sum, computed in
    ads.rate() and nowhere else -- so an avg() next to a rate name is either a
    bug or a second definition, and both are worth failing over.
    """
    code = _strip_comments(path.read_text(encoding="utf-8")).lower()
    for m in re.finditer(r"avg\s*\(([^)]*)\)", code):
        inner = m.group(1)
        offenders = [r for r in RATE_NAMES if r in inner]
        assert not offenders, (
            f"{path.name}: avg({inner.strip()}) averages a ratio "
            f"({offenders}). Rates are sum-over-sum; see ads.rate().")


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_daily_reach_is_never_summed(path: Path):
    """Daily reach is deduplicated within the day.

    Summing it over a window counts the same person once per day. 002 names the
    column reach_this_day precisely so this is hard to do by accident, and
    window_metrics returns max(), not sum().
    """
    code = _strip_comments(path.read_text(encoding="utf-8")).lower()
    assert "sum(s.reach" not in code and "sum(f.reach" not in code and \
           "sum(reach" not in code, (
        f"{path.name} sums a reach column. Daily reach is not additive.")


def test_functions_touching_public_are_security_definer():
    """Otherwise they return zero rows and nothing says why.

    Every public.meta_* table has RLS on with no policy, and ads_reader is
    NOBYPASSRLS -- a SECURITY INVOKER function returns an empty result rather
    than a permission error, which is the most confusing failure available.
    """
    for path in MIGRATIONS:
        code = path.read_text(encoding="utf-8")
        for m in re.finditer(
                r"create (?:or replace )?function\s+(ads\.\w+)(.*?)(?=\$fn\$|\Z)",
                code, re.IGNORECASE | re.DOTALL):
            name, head = m.group(1), m.group(2).lower()
            if "returns trigger" in head:
                continue
            # ads.rate is pure arithmetic over its arguments and touches no
            # table, so it needs no elevation and gets none.
            if name == "ads.rate":
                assert "security definer" not in head, (
                    f"{name} touches no table and should not be definer")
                continue
            assert "security definer" in head, f"{name} is not SECURITY DEFINER"
            assert "set search_path" in head, f"{name} has no pinned search_path"


def test_definer_functions_contain_no_dynamic_sql():
    """A definer function that builds SQL from an argument is an escalation.

    This is why ads.window_metrics resolves p_level with a static CASE over the
    grouping key rather than interpolating a column name.
    """
    for path in MIGRATIONS:
        code = path.read_text(encoding="utf-8")
        # DO blocks run as the migration's own role at apply time, not as a
        # stored definer function, so format()/execute there is ordinary DDL.
        bodies = re.findall(r"\$fn\$(.*?)\$fn\$", code, re.DOTALL)
        for body in bodies:
            assert not re.search(r"\bexecute\s+(format|'|\"|\w+\s*\|\|)", body,
                                 re.IGNORECASE), (
                f"{path.name}: dynamic SQL inside a function body")


def test_the_ads_schema_does_not_say_adset():
    """Meta's adset is translated to ad_group exactly once, in ads.ad.

    Neutral level vocabulary is what makes Google a union branch later rather
    than a migration. Comments may mention Meta's own word; identifiers may not.
    """
    for path in MIGRATIONS:
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"\bads\.\w*adset\w*", code, re.IGNORECASE):
            pytest.fail(f"{path.name}: {m.group(0)} -- use ad_group")


def test_governance_constraints_are_present():
    """The two constraints that stop an agent deciding anything."""
    taxonomy = (ROOT / "migrations" / "004_ads_taxonomy.sql").read_text(encoding="utf-8")
    assert "angle_active_is_signed" in taxonomy
    assert "status <> 'active' or approved_by is not null" in taxonomy

    experiments = (ROOT / "migrations" / "005_ads_experiments.sql").read_text(encoding="utf-8")
    assert "experiment_conclusion_attributed" in experiments
    # No winner COLUMN, for 044's reason: nothing here is read as permission.
    # Checked against the create-table body only -- the comment on the function
    # legitimately says "does not name a winner", and that sentence is the
    # point rather than a violation.
    body = re.search(r"create table ads\.experiment\s*\((.*?)^\);",
                     _strip_comments(experiments),
                     re.DOTALL | re.IGNORECASE | re.MULTILINE)
    assert body, "could not find the experiment table body"
    columns = body.group(1).lower()
    for forbidden in ("winner", "verdict", "passed", "succeeded"):
        assert forbidden not in columns, (
            f"ads.experiment has a {forbidden!r} column; naming a result is a "
            f"decision, not a stored field")


def test_rls_policies_exist_for_every_public_table_read():
    """Without these, every query succeeds and returns nothing."""
    # Comments stripped first. 006's prose legitimately quotes its own SQL --
    # "the `grant select on ads.* to ads_reader` statements near the end fail"
    # -- and a regex that cannot tell a sentence from a statement matched there
    # instead, then ran forward to the real `to ads_owner;` and swallowed the
    # whole block between. Same fix as test_governance_constraints_are_present.
    roles = _strip_comments(
        (ROOT / "migrations" / "006_ads_roles.sql").read_text(encoding="utf-8"))
    granted = re.search(r"grant select on(.*?)to ads_owner;", roles, re.DOTALL)
    assert granted, "no select grant block for ads_owner"
    tables = [t.strip().removeprefix("public.")
              for t in granted.group(1).split(",") if t.strip()]
    # Policies come from every migration, not just 006 -- 009 adds three more.
    covered: set[str] = set()
    for path in MIGRATIONS:
        sql = _strip_comments(path.read_text(encoding="utf-8"))
        for loop in re.findall(r"foreach t in array array\[(.*?)\]", sql, re.DOTALL):
            covered |= set(re.findall(r"'(\w+)'", loop))
        covered |= set(re.findall(
            r"create policy \w+ on public\.(\w+)", sql, re.IGNORECASE))

    # There is NO exemption list here, and there used to be. It named brands,
    # campaigns and products as tables with no RLS -- inferred from grepping
    # growth-engine's migrations for `enable row level security` and not
    # finding them. The catalogue said otherwise: all three had RLS on with
    # zero policies, so `intel status` reported "Known brands: none" against a
    # table holding two rows. See 009.
    #
    # The lesson the exemption list taught is that an exemption asserted from
    # migration text cannot be trusted, because RLS is also turned on from the
    # Supabase dashboard, which leaves no trace in any file this test can read.
    # So: every granted table needs a policy, and a table that genuinely has no
    # RLS loses nothing by having one.
    missing = set(tables) - covered
    assert not missing, (
        f"granted select but no RLS policy: {sorted(missing)} -- if RLS is on "
        f"for any of these they return zero rows SILENTLY, which reads as an "
        f"empty database rather than a permission problem")
