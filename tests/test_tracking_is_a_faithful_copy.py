"""tracking.py is growth-engine's. This asserts our copy has not drifted.

WHY THERE IS A COPY AT ALL

`tracking.tracked_url` writes the utm values onto every approved asset, over in
growth-engine where copy is produced and approved. `meta_ads.parse` reads those
values back off an ad, here. Those two halves are one convention, written
months apart, and NOTHING else makes them agree -- if either side changes its
spelling, every ad in the account quietly stops matching, campaign pages start
saying "not written here" about copy this system wrote, and no test fails.

tests/test_meta_match.py is the test that catches that, and it only works if it
round-trips through BOTH real modules. Its own docstring says so: a fixture url
copied into the test file would keep passing after tracking.py changed, which
is the exact failure it exists to catch.

So when the importer moved into this repo, tracking.py had to come with it --
and a duplicated file nobody checks is a slower version of the same silent
drift. This turns it into a failing test instead.

WHICH COPY IS THE ORIGINAL

growth-engine's. Three modules there -- approvals, handoff, newsletter -- call
it to stamp real urls; nothing here calls it outside these tests. If this test
fails, the fix is almost always to copy theirs over ours. Not the reverse, and
never by editing both by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
ORIGIN = ROOT.parent / "growth-engine" / "tracking.py"
COPY = ROOT / "tracking.py"

CRLF = chr(13) + chr(10)
LF = chr(10)


def _normalised(path: Path) -> str:
    # Line endings only. The files are copied by hand between two repos and git
    # converts on checkout, so CRLF differences are noise -- and a drift test
    # that cries wolf about invisible characters is one somebody deletes rather
    # than investigates.
    return path.read_text(encoding="utf-8").replace(CRLF, LF)


def test_our_tracking_matches_growth_engines():
    if not ORIGIN.is_file():
        pytest.skip(
            f"no sibling checkout at {ORIGIN}; this repo stands alone here and "
            f"the copy cannot be checked. Run the suite where both are present.")
    assert _normalised(COPY) == _normalised(ORIGIN), (
        "tracking.py has drifted from growth-engine's copy. That module IS the "
        "utm convention: if the two sides disagree about a separator or a "
        "parameter name, every ad stops matching its asset and nothing else "
        f"fails. Fix: copy {ORIGIN} over {COPY}, then run test_meta_match.py.")


def test_the_copy_is_only_used_by_the_tests_that_need_it():
    """If application code here starts calling tracking, the copy stops being a
    test fixture and becomes a second implementation of stamping -- which is
    the thing the duplicate was allowed in order to avoid."""
    users = []
    for p in ROOT.rglob("*.py"):
        if ".venv" in p.parts or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith("tests/") or rel == "tracking.py":
            continue
        text = p.read_text(encoding="utf-8")
        if "import tracking" in text or "from tracking" in text:
            users.append(rel)
    assert not users, (
        f"{users} import tracking. Stamping urls is growth-engine's job -- it "
        f"happens at approval, where a person signs the copy. This repo reads "
        f"the stamp back; it must never write one.")
