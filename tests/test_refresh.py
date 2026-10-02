"""The refresh button starts a pull without becoming able to write one.

`/refresh` is the first endpoint in this app that causes a write, and the whole
reason it is allowed to exist is that it does not perform one: it spawns
scripts/sync.py as a separate process. Every assertion here is about that
distinction holding, because it is the kind that decays into "just import it,
it's one line" in a diff that otherwise looks fine.

tests/test_read_only.py already proves the general property (only meta_ads,
scripts/sync.py and conftest may open the import pool). This file proves the
specific one: that ui.py starts the pull the way it says it does.

None of these need a database.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui.py"
SYNC = ROOT / "scripts" / "sync.py"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# Spawned, not imported.
# ---------------------------------------------------------------------------

def test_the_page_does_not_import_the_importer():
    """The one that matters.

    db_meta is covered by test_read_only.py, but `meta_ads`, `scripts.sync` and
    `db_owner` all reach it transitively -- sync.py opens the import pool at
    module scope -- and importing any of them would put the writing credential
    in the web process without tripping that test's name check.
    """
    names = _imports(UI)
    for forbidden in ("db_meta", "db_owner", "meta_ads", "scripts.sync",
                      "scripts"):
        assert not any(n == forbidden or n.startswith(forbidden + ".")
                       for n in names), (
            f"ui.py imports {forbidden}. The refresh endpoint spawns "
            f"scripts/sync.py as a subprocess precisely so that this process "
            f"never holds a credential that can write public.*.")


def test_the_spawn_is_detached():
    """A pull must outlive the tab that started it.

    Without this, a closed browser cancels the request, the coroutine dies
    after start_pull wrote a `running` row and before finish_pull could close
    it, and ads.pull grows a row that reads as a pull still in progress.
    """
    src = UI.read_text(encoding="utf-8")
    assert "DETACHED_PROCESS" in src and "CREATE_NEW_PROCESS_GROUP" in src, (
        "the Windows spawn must be detached; see _spawn_detached")
    assert "start_new_session" in src, "the POSIX spawn must be detached too"


def test_the_spawn_reads_no_pipes():
    """stdout to a pipe nobody drains blocks the child mid-pull.

    sync.py writes logs/sync.log itself, so there is nothing here worth
    reading and a full pipe buffer is the only thing a pipe could contribute.
    """
    src = UI.read_text(encoding="utf-8")
    spawn = src.split("def _spawn_detached", 1)[1].split("\n@", 1)[0]
    assert "subprocess.PIPE" not in spawn
    assert spawn.count("subprocess.DEVNULL") >= 3


# ---------------------------------------------------------------------------
# The duplicated lock.
# ---------------------------------------------------------------------------

def _assigned_number(src: str, pattern: str) -> str:
    m = re.search(pattern, src)
    assert m, f"could not find {pattern!r}"
    return m.group(1)


def test_the_lock_copies_agree():
    """ui.py cannot import sync.py's constants without importing sync.py.

    So they are copied, and copies drift. Same device as
    tests/test_tracking_is_a_faithful_copy.py: the duplication is deliberate
    and the test is what keeps it honest.
    """
    ui = UI.read_text(encoding="utf-8")
    sync = SYNC.read_text(encoding="utf-8")

    assert '".sync.lock"' in ui and '".sync.lock"' in sync, (
        "both sides must name the same lock file")

    ui_hours = _assigned_number(
        ui, r"SYNC_LOCK_STALE_AFTER\s*=\s*timedelta\(hours=(\d+)\)")
    sync_hours = _assigned_number(
        sync, r"STALE_LOCK_AFTER\s*=\s*timedelta\(hours=(\d+)\)")
    assert ui_hours == sync_hours, (
        f"ui.py waits {ui_hours}h for a stale lock and scripts/sync.py waits "
        f"{sync_hours}h. The endpoint would report a run in flight that sync.py "
        f"has already decided is dead, or the reverse.")


# ---------------------------------------------------------------------------
# Scope.
# ---------------------------------------------------------------------------

def test_the_button_is_scoped_to_insights():
    """Structure has never completed on act_153704749222533.

    Its /ads edge dies five to nine minutes into pagination. A button wired to
    both phases spins for that long and then fails, which reads as a broken
    button rather than a broken import.
    """
    src = UI.read_text(encoding="utf-8")
    assert '"--phase", "insights"' in src, (
        "the refresh endpoint must pass --phase insights")


def test_sync_accepts_the_phase_flag():
    """And the flag the endpoint passes has to exist on the other side."""
    tree = ast.parse(SYNC.read_text(encoding="utf-8"))
    flags = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_argument"
        and node.args and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert "--phase" in flags, (
        "scripts/sync.py must accept --phase; ui.py passes it")
    assert "--since" not in flags, (
        "backfills stay off the timer and off the button -- see sync.py's "
        "own docstring on why --since is absent")


# ---------------------------------------------------------------------------
# The status read stays on the read pool.
# ---------------------------------------------------------------------------

SQL_START = re.compile(r"^(select|insert|update|delete|with)\b", re.IGNORECASE)


def test_the_status_endpoint_reads_only_ads():
    """ads_reader holds no grant in public, and a missing grant on an RLS
    table returns ZERO ROWS rather than an error -- which would render as a
    dashboard that has simply never imported anything."""
    tree = ast.parse(UI.read_text(encoding="utf-8"))
    sql = [n.value for n in ast.walk(tree)
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and SQL_START.match(n.value.strip())]
    pull_reads = [q for q in sql if "ads.pull" in q]
    assert pull_reads, "expected the status endpoint to read ads.pull"
    for q in sql:
        assert "public." not in q, f"ui.py queries public.* on the read pool:\n{q[:200]}"


@pytest.mark.parametrize("route", ["/refresh", "/refresh/status"])
def test_both_routes_are_registered(route: str):
    src = UI.read_text(encoding="utf-8")
    assert f'"{route}"' in src, f"{route} is not routed"


def test_refresh_is_a_post_and_status_is_a_get():
    """A GET that starts a pull is one prefetch away from a rate limit."""
    src = UI.read_text(encoding="utf-8")
    assert '@router.post("/refresh")' in src
    assert '@router.get("/refresh/status")' in src
