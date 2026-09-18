"""Export the repo as markdown, for pasting into a model that cannot see disk.

    python scripts/export_for_llm.py            # -> llm-export/

WHY A SCRIPT AND NOT A ONE-OFF COPY. The export is derived, so it is stale the
moment anything changes, and a stale copy of a codebase is worse than no copy:
you get confident answers about code that no longer exists. Re-run it rather
than trusting the one you made last week.

WHAT IS EXCLUDED, AND HOW. The file list comes from `git ls-files`, so
.gitignore does the excluding -- .env and .env.bak.* (live Supabase passwords),
.venv, __pycache__, logs. That is deliberate rather than a second hand-written
list of what is secret: two such lists drift apart, and the one that drifts is
always the one nobody re-reads. A file has to be committed to be exported,
which also means an uncommitted experiment never leaks into a paste.

The output is itself gitignored. It is a second copy of the whole repo, and
committing it would double every future diff.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "llm-export"

NL = chr(10)
BACKTICK = chr(96)

LANG = {
    ".py": "python", ".sql": "sql", ".html": "html", ".js": "javascript",
    ".json": "json", ".md": "markdown", ".txt": "text", ".ini": "ini",
    ".ps1": "powershell", ".bat": "bat", ".example": "bash",
    ".gitignore": "gitignore",
}

#: Bundles, in reading order -- the one CLAUDE.md recommends: the boundary
#: first, then the schema everything is derived from, then the code, then the
#: tests that pin it. Each is (name, blurb, predicate).
BUNDLES = [
    ("01-orientation",
     "Start here: what this repo is, and what it refuses to do.",
     lambda r: r in {"CLAUDE.md", "README.md", ".env.example",
                     "requirements.txt", "pytest.ini", ".gitignore"}),
    ("02-schema",
     "The ads schema. Every rate in the system is defined here and not in "
     "Python; 003 is the single most important file in the repo.",
     lambda r: r.startswith("migrations/")),
    ("03-importer",
     "meta_ads/: Meta's JSON in, public.meta_* rows out. parse.py is pure, "
     "and is where the four disagreeing creative shapes are reconciled.",
     lambda r: r.startswith("meta_ads/") or r in {
         "db_meta.py", "identity.py", "log.py", "tracking.py",
         "scripts/sync.py", "scripts/sync.bat",
         "scripts/register_sync_task.ps1"}),
    ("04-verbs",
     "intel/: the sixteen verbs. Thin on purpose -- they choose a window, "
     "call a Postgres function, and shape the result.",
     lambda r: r.startswith("intel/")),
    ("05-dashboard",
     "The read surface: routes, hand-rolled SVG charts, templates, ask box.",
     lambda r: r.startswith(("templates/", "static/")) or r in {
         "ui.py", "ask.py", "charts.py", "main.py"}),
    ("06-plumbing",
     "Settings, the three connection pools, the migration runner, scripts.",
     lambda r: r in {"config.py", "db.py", "db_owner.py", "ads_migrate.py",
                     "scripts/scan_secrets.py", "scripts/seed_angles.py",
                     "scripts/export_for_llm.py"}),
    ("07-tests",
     "What the repo refuses to let itself do. Read these as the spec.",
     lambda r: r.startswith("tests/")),
    ("08-agent",
     "Skills and permissions: what an agent is allowed to run here.",
     lambda r: r.startswith(".claude/")),
]


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout
    return [p for p in out.replace(chr(13), "").split(NL) if p]


def fence_for(text: str) -> str:
    """A fence longer than any run of backticks inside the file.

    CLAUDE.md and the SKILL files carry fenced blocks of their own. Wrapping
    one in a three-backtick fence ends the block at the file's first fence and
    renders the remainder as prose, which is how an export quietly loses half
    its content without erroring.
    """
    longest = run = 0
    for ch in text:
        run = run + 1 if ch == BACKTICK else 0
        longest = max(longest, run)
    return BACKTICK * max(3, longest + 1)


def as_markdown(rel: str, text: str) -> str:
    fence = fence_for(text)
    lines = text.count(NL) + 1
    return (f"## {BACKTICK}{rel}{BACKTICK}" + NL + NL
            + f"*{lines} lines*" + NL + NL
            + fence + LANG.get(Path(rel).suffix, "") + NL
            + text.rstrip(NL) + NL
            + fence + NL)


def _wipe(directory: Path) -> None:
    if not directory.exists():
        return
    for p in sorted(directory.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()


def main() -> int:
    files = tracked()
    if not files:
        sys.exit("git ls-files returned nothing -- is this a git repo?")

    _wipe(OUT)
    (OUT / "files").mkdir(parents=True, exist_ok=True)
    (OUT / "bundles").mkdir(parents=True, exist_ok=True)

    rendered: dict[str, str] = {}
    skipped: list[str] = []
    for rel in files:
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append(rel)          # a binary got committed; say so
            continue
        rendered[rel] = as_markdown(rel, text)
        dest = OUT / "files" / (rel + ".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(rendered[rel], encoding="utf-8")

    used: set[str] = set()
    manifest: list[tuple[str, int, int, str]] = []
    for name, blurb, pred in BUNDLES:
        members = [r for r in files if r in rendered and pred(r)]
        if not members:
            continue
        used.update(members)
        body = [f"# {name}", "", blurb, "", f"*{len(members)} files.*", "",
                "---", ""]
        body += [rendered[r] + NL + "---" + NL for r in members]
        out = OUT / "bundles" / (name + ".md")
        out.write_text(NL.join(body), encoding="utf-8")
        manifest.append((name, len(members), out.stat().st_size, blurb))

    # Anything no predicate claimed. Present rather than silently dropped: a
    # new top-level file would otherwise be missing from every bundle and
    # nobody would notice until a model said the code did not exist.
    leftover = [r for r in files if r in rendered and r not in used]
    if leftover:
        body = ["# 09-other", "", "Files no bundle claimed. If something "
                "important is here, add it to BUNDLES in "
                "scripts/export_for_llm.py.", "", "---", ""]
        body += [rendered[r] + NL + "---" + NL for r in leftover]
        out = OUT / "bundles" / "09-other.md"
        out.write_text(NL.join(body), encoding="utf-8")
        manifest.append(("09-other", len(leftover), out.stat().st_size,
                         "Files no bundle claimed."))

    total = sum(len(t) for t in rendered.values())
    idx = [
        "# Ads Growth Engine, as markdown", "",
        "Generated by `scripts/export_for_llm.py`. **Derived, and therefore "
        "perishable** -- re-run it rather than trusting an old copy.", "",
        f"{len(rendered)} files, {total // 1024} KB of markdown.", "",
        "## How to feed this to a model", "",
        "Use `bundles/` first: eight files in reading order, which is what you "
        "want when an upload limit counts files rather than bytes. `files/` "
        "mirrors the tree one file at a time, for when you need exactly one "
        "thing.", "",
        "If you can only send one bundle, send **`02-schema`**. Every rate in "
        "this system is defined in SQL, so the Python alone cannot tell a "
        "model where a number comes from -- it will guess, and it will guess "
        "that the arithmetic happens in Python, which is the one thing this "
        "codebase is built to prevent.", "",
        "## Bundles", "",
        "| bundle | files | size | what it is |",
        "| --- | --- | --- | --- |",
    ]
    for name, n, size, blurb in manifest:
        idx.append(f"| `{name}.md` | {n} | {size // 1024} KB | {blurb} |")

    idx += ["", "## Every file", "", "| path | lines |", "| --- | --- |"]
    for rel in files:
        if rel in rendered:
            lines = (ROOT / rel).read_text(encoding="utf-8").count(NL) + 1
            idx.append(f"| `{rel}` | {lines} |")
    if skipped:
        idx += ["", "## Not exported", "", "Committed but not utf-8:", ""]
        idx += [f"- `{r}`" for r in skipped]

    (OUT / "00-INDEX.md").write_text(NL.join(idx) + NL, encoding="utf-8")

    print(f"  {len(rendered)} files -> {OUT}")
    print(f"  {len(manifest)} bundles, {total // 1024} KB of markdown")
    if skipped:
        print(f"  skipped (not utf-8): {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
