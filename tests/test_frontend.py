"""The React dashboard's own checks, run from pytest.

The pages render in frontend/ and their tests are vitest files beside them
(frontend/src/**/*.test.tsx); the Python suite would otherwise pass with a
dashboard that does not compile. This runs the typecheck and the vitest suite
once each, as the frontend's own `npm run typecheck` and `npm test` would.

Skipped, not failed, when node or node_modules is missing: a checkout that has
never run `npm install` is a state of the install, and the Python half can be
tested on its own. The skip says so.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
NPX = shutil.which("npx.cmd") or shutil.which("npx")

pytestmark = pytest.mark.skipif(
    NPX is None or not (FRONTEND / "node_modules").is_dir(),
    reason="frontend/node_modules is missing: run `npm install` in frontend/")


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([NPX, *args], cwd=str(FRONTEND), capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          timeout=600, env={**os.environ, "CI": "1"})


def test_the_frontend_typechecks():
    r = _run("tsc", "--noEmit", "-p", ".")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_frontend_tests_pass():
    r = _run("vitest", "run")
    assert r.returncode == 0, (r.stdout + r.stderr)[-6000:]


def test_every_page_url_has_a_route_and_a_file():
    """ui.PAGES, App.tsx and the pages/ folder name the same screens.

    A URL the server serves the shell for and the router does not know
    renders a blank column; a route with no handler fetches a 404 and reports
    a stale server that is not stale.
    """
    import ui

    app = (FRONTEND / "src" / "App.tsx").read_text(encoding="utf-8")
    for path in ui.PAGES:
        route = path.replace("{ad_key}", ":adKey")
        assert f'path="{route}"' in app, f"App.tsx has no route for {path}"
