"""charts.py and frontend/src/lib/format.ts print the same figure the same way.

Python formats every number a model or a rule writes into a sentence; the React
pages format the tiles beside those sentences. One set of answers pins both:
frontend/src/lib/format.cases.json. This test holds charts.py to it and
frontend/src/lib/format.test.ts holds format.ts to it, so a change to either
formatter that the other does not share fails one of the two.

    python tests/test_format_parity.py --write   # regenerate after a deliberate change
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import charts  # noqa: E402

CASES = ROOT / "frontend" / "src" / "lib" / "format.cases.json"

VALUES = [None, 0, -0.0, 0.004, -0.004, 0.005, -0.005, 0.125, 0.375, 2.675,
          1.005, 12.5, -12.5, 41.44, -0.15, -0.3688, 999.994, 999.995, 1000,
          1234.5, -1234.5, 15748.49, 2500.5, 3500.5, 1e6 + 0.5, 3.14159,
          -8.06, 0.05, -0.05, 0.15, 99.5, 100.5, "12.5", ""]
PLACES = [0, 1, 2]
HOURS = [None, 0, 0.001, 0.0083, 0.5, 0.99, 1, 1.5, 2.5, 23.5, 47.9, 48, 72.5, 500]
WORDS = [None, "", "  ", "QUALITY_LEAD", "LEAD_GENERATION", "thruplay", "OFFSITE_CONVERSIONS"]
KINDS = ["cpa", "spend", "link_ctr", "conversion_rate", "conversions", "impressions"]


def build() -> dict:
    out: dict = {"money": [], "num": [], "pct": [], "signed_pct": [], "fmt": [],
                 "ago": [], "words": []}
    for v in VALUES:
        out["money"].append([v, charts.money(v)])
        for p in PLACES:
            out["num"].append([v, p, charts.num(v, p)])
            out["pct"].append([v, p, charts.pct(v, p)])
            out["signed_pct"].append([v, p, charts.signed_pct(v, p)])
        for k in KINDS:
            out["fmt"].append([v, k, charts.fmt(v, k)])
    for h in HOURS:
        out["ago"].append([h, charts.ago(h)])
    for w in WORDS:
        out["words"].append([w, charts.words(w)])
    return out


def test_charts_py_matches_the_shared_cases():
    want = json.loads(CASES.read_text(encoding="utf-8"))
    assert build() == want, (
        "charts.py no longer prints what format.cases.json says. If the change "
        "is deliberate, regenerate with `python tests/test_format_parity.py "
        "--write` and make format.ts agree.")


if __name__ == "__main__":
    if "--write" in sys.argv:
        CASES.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n",
                         encoding="utf-8")
        print(f"wrote {CASES}")
