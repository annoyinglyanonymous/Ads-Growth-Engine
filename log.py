"""One log file saying what the engine executed, and when.

    tail -f logs/engine.log        # or open it in an editor

WHY A FILE AND NOT THE TERMINAL
The server prints to whatever console started it, and half the interesting
work -- generations fired by the auto-chain, model calls made mid-request --
happens inside requests nobody is watching. The database records DECISIONS
(who approved what); this records EXECUTION (what ran, for how long, and how
it ended), which is the half a person debugging "what just happened" needs.

WHAT GETS LOGGED, AND WHERE FROM
Two seams cover almost everything, on purpose -- a log call scattered into
every function is a log nobody keeps consistent:

  (was: the provider layer)  every model call, size in,
                            duration, size out, or the failure
  ui.py                     every action a person takes on a campaign page,
                            with who took it and the outcome message

The CLI paths log too: anything that reaches the model goes through the same
prompting seam, so `python -m newsletter --draft ...` lands in the same file
as a button press.

FILE-ONLY BY DESIGN. The 'engine' logger does not propagate to the root
logger, so nothing here duplicates into uvicorn's console output. Rotation
keeps the file ~2 MB with three predecessors; on Windows a rotation can fail
if two processes hold the file at once, in which case logging carries on in
the oversized file rather than taking a request down.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import settings

_configured = False


def get(name: str) -> logging.Logger:
    """A logger under 'engine.*', configuring the file handler on first use.

    Lazy rather than a setup() call in main.py, so the CLI entrypoints --
    which never import main -- log to the same file without each one
    remembering to initialise anything.
    """
    global _configured
    if not _configured:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2_000_000,
                                      backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)-18s %(message)s"))
        engine = logging.getLogger("engine")
        engine.setLevel(logging.INFO)
        engine.addHandler(handler)
        engine.propagate = False
        _configured = True
    return logging.getLogger(name if name.startswith("engine")
                             else f"engine.{name}")


def safe_console() -> None:
    """Let this process print text the console codepage does not have.

    THE BUG THIS EXISTS FOR
    A Windows console is cp1252 by default. `runner.py` printed an agent's
    report that contained an arrow (U+2192, from a "See flood markets ->"
    style line) and died with UnicodeEncodeError -- AFTER the work was done
    and the row was written. The operator saw a traceback where the result
    should have been, which is the worst possible place to lose a message:
    the work succeeded and the screen said it crashed.

    Latent everywhere, not just there. Every CLI in this project prints text
    a model or an agent wrote, and em dashes happen to live in cp1252 while
    arrows and some quotes do not -- so it only bites on the characters
    nobody thought to test.

    IT HAD ALREADY HAPPENED ONCE, AND THE FIX STAYED LOCAL
    generate.py hit this years-of-the-project earlier: U+202F, a narrow
    no-break space in the Renegade corpus, killed --show-prompt "on precisely
    the pages it exists to inspect". It was fixed in that file, with a comment
    noting that the class of character is bigger than one codepoint. Correct,
    and still only one file -- which is why the runner rediscovered it with an
    arrow. A fix that knows it generalises should be moved when it is
    written.

    errors="replace" rather than a crash: a report with one character
    substituted is readable, and a report that raised is not there at all.

    Called explicitly from each main() rather than run as an import side
    effect, because a module that silently rewires stdout on import is a
    module nobody can debug.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a real stream -- captured by pytest, or a pipe already
            # wrapped. Nothing to do and nothing worth failing over.
            pass
