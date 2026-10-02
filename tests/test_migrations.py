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


#: A sum of reach_this_day is a count of PERSON-DAYS: a real quantity, and the
#: right denominator for impressions per person-day (017's daily_frequency).
#: It is not a reach, and the one way it becomes a lie is by being printed as
#: one. So it may be named (`as c_pd`) and divided by (`/ nullif(x.c_pd, 0)`)
#: and nothing else -- never selected, compared, returned or rounded on its own.
PERSON_DAYS = re.compile(r"\b(person_days|\w+_pd)\b", re.IGNORECASE)


def test_person_days_is_only_ever_a_denominator():
    seen = 0
    for path in MIGRATIONS:
        code = _strip_comments(path.read_text(encoding="utf-8")).lower()
        for m in PERSON_DAYS.finditer(code):
            seen += 1
            before = code[:m.start()]
            if re.search(r"\bas\s+$", before):
                continue  # the alias that names the sum
            assert re.search(r"/\s*nullif\(\s*(\w+\.)?$", before), (
                f"{path.name}: {m.group(0)} used outside `/ nullif(`:\n"
                f"  ...{code[max(0, m.start() - 80):m.end() + 20]}...\n"
                f"A person-day count is a denominator, never a figure.")
        # And never a column of any function's result.
        for body in re.findall(r"returns\s+table\s*\((.*?)\)\s*language",
                               code, re.DOTALL):
            assert not PERSON_DAYS.search(body), (
                f"{path.name} returns a person-day count")
    # Guard against a green test that checked nothing: 017 sums person-days.
    assert seen, "expected 017 to define a person-day denominator"


# ---------------------------------------------------------------------------
# 016 onwards: every function a migration creates is handed to ads_owner and
# granted to ads_reader, under the signature it was created with.
#
# The grant test in test_read_only.py checks that a grant NAMES the function.
# It cannot see a grant written against the wrong argument list -- and that
# one fails at apply time, inside a DO block, after the function exists,
# which is the most awkward moment there is. 018's signatures carry defaulted
# threshold arguments, so they are the easiest ones to get wrong.
# ---------------------------------------------------------------------------

def _joined_literals(sql: str) -> str:
    """Adjacent SQL string literals as one: 'a ' newline 'b' -> 'a b'."""
    return re.sub(r"'\s*\n\s*'", "", sql)


def _arg_types(params: str) -> str:
    types = []
    for p in params.split(","):
        words = p.split()
        if not words:
            continue
        if "default" in words:
            words = words[:words.index("default")]
        types.append(" ".join(words[1:]).lower())
    return ", ".join(types)


OWNED_FROM = 16


@pytest.mark.parametrize(
    "path", [p for p in MIGRATIONS if int(p.name.split("_", 1)[0]) >= OWNED_FROM],
    ids=lambda p: p.name)
def test_each_function_is_owned_and_granted_under_its_own_signature(path: Path):
    code = _strip_comments(path.read_text(encoding="utf-8"))
    created = re.findall(r"create\s+(?:or\s+replace\s+)?function\s+(ads\.\w+)\s*\((.*?)\)\s*returns",
                         code, re.IGNORECASE | re.DOTALL)
    assert created, f"{path.name} creates no function"
    joined = re.sub(r"\s+", " ", _joined_literals(code)).lower()
    for name, params in created:
        sig = f"{name.lower()}({_arg_types(params)})"
        assert f"alter function {sig} owner to ads_owner" in joined, (
            f"{path.name}: no `alter function {sig} owner to ads_owner`")
        assert f"grant execute on function {sig} to ads_reader" in joined, (
            f"{path.name}: no `grant execute on function {sig} to ads_reader`")


# ---------------------------------------------------------------------------
# A cost per result is never ordered across optimization goals.
#
# 013 made the cross-goal CPA rank something the database cannot produce;
# 018 ranks campaigns by their contribution to a goal's CPA change, which is
# in CPA units too. Prose loses to a column that sorts, so: a window that
# orders by anything CPA-shaped must partition by the goal, and in 018 --
# whose every window is about a goal -- every window partitions by it.
# Dollars may cross goals (016: a dollar is a dollar); 018 never needs to
# rank them inside a window, so the stricter rule costs nothing there.
# ---------------------------------------------------------------------------

WINDOW_CLAUSE = re.compile(r"\bover\s*\(((?:[^()]|\([^()]*\))*)\)", re.IGNORECASE)
GOAL_KEY = re.compile(r"\b(\w+\.)?(gl|goal|only_goal|optimization_goal)\b")


def _windows(path: Path) -> list[str]:
    code = _strip_comments(path.read_text(encoding="utf-8")).lower()
    return [m.group(1) for m in WINDOW_CLAUSE.finditer(code)]


@pytest.mark.parametrize("path", MIGRATIONS, ids=lambda p: p.name)
def test_no_window_orders_a_cpa_across_goals(path: Path):
    for clause in _windows(path):
        if "order by" not in clause:
            continue
        ordering = clause.split("order by", 1)[1]
        if "cpa" not in ordering and "contrib" not in ordering:
            continue
        partition = clause.split("order by", 1)[0]
        assert "partition by" in partition and GOAL_KEY.search(partition), (
            f"{path.name}: `over ({clause.strip()})` orders a cost per result "
            f"without partitioning by the optimization goal")


def test_018_every_window_is_within_a_goal():
    path = ROOT / "migrations" / "018_ads_impact_and_outcomes.sql"
    clauses = _windows(path)
    assert clauses, "018 ranks and accumulates inside windows; found none"
    for clause in clauses:
        partition = clause.split("order by", 1)[0]
        assert "partition by" in partition and GOAL_KEY.search(partition), (
            f"018: `over ({clause.strip()})` is not partitioned by the goal")


def test_018_bodies_qualify_against_their_out_columns():
    """Every RETURNS TABLE column is a plpgsql variable (017's pragma)."""
    code = (ROOT / "migrations" / "018_ads_impact_and_outcomes.sql").read_text(
        encoding="utf-8")
    bodies = re.findall(r"\$fn\$(.*?)\$fn\$", code, re.DOTALL)
    assert len(bodies) == 4, len(bodies)
    for body in bodies:
        assert body.lstrip().startswith("#variable_conflict use_column"), (
            body.lstrip()[:80])


def test_018_contribution_and_excess_follow_the_header():
    """The two identities the triage ranks on, pinned to their text."""
    code = _strip_comments((ROOT / "migrations" /
                            "018_ads_impact_and_outcomes.sql").read_text(
                                encoding="utf-8")).lower()
    flat = re.sub(r"\s+", " ", code)
    # excess = current spend - current conversions at the PRINTED prior CPA.
    assert "round(mm.c_spend - mm.c_conv * mm.p_cpa, 2) as excess" in flat
    # contribution = cpa_bridge's total_effect with the goal as population.
    assert ("then p.c_spend / nullif(p.g_c_conv, 0) "
            "- p.p_spend / nullif(p.g_p_conv, 0) end as contrib_u") in flat
    # The running share is over an explicit ROWS frame, so ties do not merge.
    assert "rows between unbounded preceding and current row" in flat


# ---------------------------------------------------------------------------
# 019: typical ranges by campaign family.
# ---------------------------------------------------------------------------

M019 = ROOT / "migrations" / "019_ads_family_baselines.sql"


def test_019_bodies_qualify_against_their_out_columns():
    """Every RETURNS TABLE column is a plpgsql variable (017's pragma)."""
    bodies = re.findall(r"\$fn\$(.*?)\$fn\$", M019.read_text(encoding="utf-8"),
                        re.DOTALL)
    assert len(bodies) == 4, len(bodies)
    for body in bodies:
        assert body.lstrip().startswith("#variable_conflict use_column"), (
            body.lstrip()[:80])


def test_019_has_no_window_at_all():
    """Bands are ordered-set aggregates per (family, goal); nothing is ranked."""
    assert _windows(M019) == []


def test_019_the_current_window_is_never_in_its_band():
    flat = re.sub(r"\s+", " ", _strip_comments(
        M019.read_text(encoding="utf-8")).lower())
    assert "v_before := p_cur_since - 1;" in flat
    # Both reads of the sample are anchored on that day, not on the window.
    assert flat.count("(p_brand_id, v_before, p_weeks,") == 2
    # Bands are percentile_cont, rounded back to ads.rate's four places.
    for q in ("0.25", "0.50", "0.75"):
        assert (f"percentile_cont({q}) within group "
                f"(order by w.val::double precision)") in flat
    assert flat.count("round(a.q") == 3 and "::numeric, 4)" in flat


def _seeded_rules() -> list[tuple[str, str, str, int]]:
    code = _strip_comments(M019.read_text(encoding="utf-8"))
    block = re.search(r"insert into ads\.campaign_family_rule.*?\) as v\(",
                      code, re.DOTALL | re.IGNORECASE)
    assert block, "019 seeds no rules"
    return [(d, p, v, int(n)) for d, p, v, n in re.findall(
        r"\('(\w+)',\s*'([^']+)',\s*'(\w+)',\s*(\d+)\)", block.group(0))]


def _classify(name: str, rules, dimension: str):
    """The function's rule, in Python: highest-priority `~*` match wins.

    The seeded patterns are plain literals, which read the same in POSIX
    and Python regular expressions.
    """
    hits = [(n, v) for d, p, v, n in rules
            if d == dimension and re.search(p, name, re.IGNORECASE)]
    return max(hits)[1] if hits else None


def test_019_rules_are_case_insensitive_and_highest_priority_wins():
    flat = re.sub(r"\s+", " ", _strip_comments(
        M019.read_text(encoding="utf-8")).lower())
    assert flat.count("c.name ~* r.pattern") == 2
    assert flat.count("order by r.priority desc limit 1") == 2
    assert "unique (brand_id, dimension, priority)" in flat
    assert "where b.slug = 'renegade'" in flat


def test_019_seeded_priorities_follow_the_contract():
    rules = _seeded_rules()
    assert len(rules) == 10, rules
    top = {(d, v): max(n for dd, _, vv, n in rules if (dd, vv) == (d, v))
           for d, _, v, _n in rules}
    lowest = {(d, v): min(n for dd, _, vv, n in rules if (dd, vv) == (d, v))
              for d, _, v, _n in rules}
    order = ["hiring", "b_roll", "m_and_a", "franchise"]
    for hi, lo in zip(order, order[1:]):
        assert lowest[("family", hi)] > top[("family", lo)], (hi, lo)
    assert lowest[("audience", "retargeting")] > top[("audience", "prospecting")]


@pytest.mark.parametrize("name, family, audience", [
    ("Renegade | Hiring Ads | Open Audience", "hiring", "prospecting"),
    ("Renegade | B-Roll | Retargeting", "b_roll", "retargeting"),
    ("Renegade | M&A | Core States", "m_and_a", "prospecting"),
    ("Renegade | book roll | Custom List", "m_and_a", "retargeting"),
    ("Renegade | FRANCHISE | Custom Audience", "franchise", "retargeting"),
    ("Renegade | Franchise | Hiring Ads", "hiring", None),
    ("Renegade | M&A | B-Roll", "b_roll", None),
    ("Renegade | Franchise | Retarget | Open Audience", "franchise", "retargeting"),
    ("Renegade | Brand Awareness", None, None),
])
def test_019_seed_reads_live_names_as_the_contract_says(name, family, audience):
    rules = _seeded_rules()
    assert _classify(name, rules, "family") == family  # None reads 'other'
    assert _classify(name, rules, "audience") == audience
    assert "coalesce(fr.v, 'other')" in _strip_comments(
        M019.read_text(encoding="utf-8"))


def test_019_the_rule_table_is_owned_and_readable():
    joined = re.sub(r"\s+", " ", _joined_literals(_strip_comments(
        M019.read_text(encoding="utf-8")))).lower()
    assert "alter table ads.campaign_family_rule owner to ads_owner" in joined
    assert "grant select on ads.campaign_family_rule to ads_reader" in joined
    assert "references public.brands (id) on delete cascade" in joined
    # The reader is granted nothing that writes.
    assert not re.search(r"grant (insert|update|delete|all)[^;]*campaign_family_rule",
                         joined)


#: Postgres keywords that cannot be a bare column name in RETURNS TABLE.
#: 019 shipped `position text` and its apply failed on 2026-10-02 ("syntax
#: error at or near position"); quoting it ("position") is the fix.
_RESERVED_COLUMNS = {"position", "user", "order", "group", "select", "from",
                     "where", "table", "column", "check", "default", "end",
                     "limit", "offset", "window", "both", "leading",
                     "trailing", "references", "primary", "unique",
                     "analyse", "analyze", "array", "case", "cast", "do",
                     "else", "for", "into", "only", "some", "then", "to",
                     "union", "using", "when", "with"}


def test_no_migration_returns_a_bare_reserved_word():
    import re as _re  # noqa: PLC0415
    from pathlib import Path as _P  # noqa: PLC0415
    bad = []
    for f in sorted((_P(__file__).resolve().parent.parent / "migrations")
                    .glob("0*.sql")):
        sql = f.read_text(encoding="utf-8")
        for block in _re.findall(r"returns table\s*\((.*?)\)\s*\n\s*language",
                                 sql, flags=_re.S | _re.I):
            for line in block.splitlines():
                m = _re.match(r"\s*([a-z_]+)\s+[a-z]", line.split("--")[0],
                              flags=_re.I)
                if m and m.group(1).lower() in _RESERVED_COLUMNS:
                    bad.append(f"{f.name}: {m.group(1)}")
    assert not bad, bad
