"""The ask box, answered in sentences, by a Claude Code session.

    answer("which creatives are tiring", "renegade")
    -> {"answer": "Five ads show...", "verbs": ["fatigue"], "ok": True}

WHY A CLAUDE CODE SESSION AND NOT AN API CLIENT

growth-engine holds the rule this has to live beside: `tests/test_no_model_in_app.py`
reads every file in that repo and fails if any provider library is imported at
all. Its reasoning is that the agent writes the language and the app stores the
result -- the app is not a thing that generates.

Shelling out to `claude -p` keeps that true rather than breaking it. There is
no `anthropic`, `openai` or `genai` import here and no API key in `.env`: the
app invokes the agent, which is the same arrangement both repos already
describe, just reached from a browser instead of a terminal. It also means the
prose comes from the session that already has the ads-intel skill, so the rules
about unsettled days and optimization goals are the ones it already follows.

The cost is that an answer takes seconds, not milliseconds, and spawns a
process per question. That is the trade and it is why this is a considered
endpoint rather than something a page polls.

WHAT IT IS ALLOWED TO DO, AND WHY THAT LIST IS EXPLICIT

A question from a browser reaches a shell here, so the allowlist is the whole
security model and it is written as a named list -- 006:189's rule, for
006:189's reason: "a grant that is too wide produces no error, ever."

`Bash(python -m intel <verb>:*)`, one entry per READ verb. Two verbs that
`python -m intel` offers are deliberately absent, the same two ask.py refuses
and for the same reasons:

    record   writes. A text box that can write is a text box that eventually
             writes something nobody signed.
    live     spends the Meta rate limit. A page that can spend it one
             keystroke at a time will, and the failure lands on the scheduled
             sync rather than here.

Edit, Write and the rest are not granted at all, and are denied a second time
explicitly. A prompt that talks its way into wanting them finds nothing to use.

WHAT IT IS TOLD, AND WHY THE NUMBERS ARE SAFE

The session is handed CLAUDE.md's rules in its system prompt, and the one that
matters is the repo's first: every figure comes out of a verb, and a number
that is not in a verb's output does not get said. That is the same contract
intel/readings.py enforces on the brief with a rule registry; here it is an
instruction plus the fact that the only way to learn a number is to run a verb
that prints it.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

from log import get as _get_logger

log = _get_logger("chat")

ROOT = Path(__file__).resolve().parent

#: The venv's Scripts directory, put FIRST on the subprocess PATH so that bare
#: `python` resolves to the interpreter that has psycopg installed.
#:
#: This is the fix for a problem worth recording, because the obvious repair
#: was the wrong one. The first live test failed at import -- bare `python` on
#: PATH has no database driver -- so the natural move was to allowlist the venv
#: path instead. That failed too: the project's own .claude/settings.json
#: grants `Bash(python -m intel *)` and nothing else, so every other spelling
#: of the interpreter came back "requires approval" in a session that has
#: nobody to approve it.
#:
#: Widening settings.json would have worked and is the wrong trade: a
#: permissions file should not grow an entry because a feature found it
#: inconvenient. Prepending the venv to PATH makes the command the model types
#: -- `python -m intel fatigue` -- both the one that is already permitted AND
#: the one that works. No grant is widened and no new spelling is introduced.
_VENV_BIN = ROOT / ".venv" / "Scripts"
PYTHON = "python"

#: Every verb the box may reach. One entry per verb rather than a wildcard over
#: the interpreter, because that would include `record`.
READ_VERBS = ("status", "brief", "overview", "compare", "why", "fatigue",
              "trend", "ad", "angles", "candidates", "queue", "coverage",
              "versus", "experiments", "experiment")

#: Denied a second time. The allowlist above already excludes these, so this is
#: belt and braces -- but the cost of the belt failing is a write from a text
#: box, and `record` is one word away from a verb that is allowed.
DENIED = ("Edit", "Write", "NotebookEdit", "WebFetch", "WebSearch",
          "Bash(python -m intel record:*)",
          "Bash(python -m meta_ads:*)",
          "Bash(python ads_migrate.py:*)")


def _denied() -> list[str]:
    """The deny list, spelled for both interpreters.

    `record` is one word from a verb that is allowed, so it is denied under the
    bare name AND under the resolved venv path -- a deny entry that does not
    match the command the model would actually type is decoration.
    """
    out = list(DENIED)
    if PYTHON != "python":
        out += [f"Bash({PYTHON} -m intel record:*)",
                f"Bash({PYTHON} -m meta_ads:*)",
                f"Bash({PYTHON} ads_migrate.py:*)"]
    return out

#: A browser request cannot wait forever and a person will not either. Long
#: enough for a few verbs and a considered answer; short enough that a hung
#: session fails as a sentence rather than a spinner.
TIMEOUT_SECONDS = 180.0

SYSTEM = """\
You are answering one question about this brand's Meta ads, for the operator,
in a browser. Answer in prose. Be brief -- a few sentences, or a short list.

HOW TO FIND THE ANSWER
Run the read verbs. `python` on your PATH is already this project's virtualenv
interpreter, so use it plainly and do not substitute an absolute path or
`.venv/Scripts/python.exe` -- those spellings are not permitted and will be
refused:

    {python} -m intel <verb> --brand {brand}

Start with `status` if anything looks stale or empty. Every verb prints JSON.

THE ONE RULE THAT MATTERS
Every figure you state must have come out of a verb you actually ran. Do not
calculate. Do not add up a column, do not work out a percentage, do not derive
a rate from two other numbers. If the number you want is not in a verb's
output, say that it is not available and name the verb you looked in. This is
the repo's first rule and the reason is that a number you computed and a number
on the dashboard can disagree, and whoever is reading has no way to tell which
is wrong.

WHAT TO REPEAT EVERY TIME
- `unsettled_days` and the `caveat`, if either is set. Meta restates
  conversions for about three days and a decline at the edge of a window is
  usually not real.
- "daily frequency", never "frequency". Never sum reach.
- A null CPA is undefined, not zero. Never rank on it.
- CPA is not comparable between ads with different `optimization_goal`. Say so
  rather than comparing them.
- Report unconfident fatigue rows as a count, not as findings.

WHAT YOU CANNOT DO
Approve anything, activate an angle, conclude an experiment, file a proposal,
pull from Meta, or apply a migration. If the answer is "somebody has to sign
this", say that and say where: growth-engine's UI.

Facts and opinions are different. A number a verb returned is a fact. Your
reading of it is an opinion. Keep them apart in the wording.

Answer only the question asked. No preamble, no summary of what you are about
to do, no closing offer of further help.
"""


class ChatUnavailable(RuntimeError):
    """No Claude Code on PATH, so this endpoint cannot work at all."""


def _allowed() -> list[str]:
    return [f"Bash({PYTHON} -m intel {verb}:*)" for verb in READ_VERBS]


def available() -> bool:
    """Is there a `claude` to shell out to?

    Checked rather than assumed so `/ask` can render a page that explains
    itself, the way _fail does for a missing database url -- a missing CLI is
    a state of the install, not a fault in the question.
    """
    return shutil.which("claude") is not None


async def answer(question: str, brand: str = "renegade") -> dict:
    """One question, one answer, one short-lived Claude Code session."""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "answer": None, "error": "No question."}
    if not available():
        raise ChatUnavailable(
            "The `claude` command is not on PATH, so the ask box cannot answer "
            "in sentences. `python -m intel <verb>` still works in a terminal, "
            "and /brief is unaffected.")

    cmd = [
        "claude", "-p", question,
        "--model", "claude-opus-5",
        "--output-format", "json",
        "--append-system-prompt", SYSTEM.format(brand=brand, python=PYTHON),
        "--allowedTools", *_allowed(),
        "--disallowedTools", *_denied(),
        # Bounded, but not tightly. The first cut was 8 and that was wrong:
        # "how did spend go last week" needs 9 -- status, then overview, then
        # compare, then the answer -- and the run died one turn short with
        # subtype error_max_turns. A cap that cuts off ordinary questions is
        # not a safety rail, it is a bug that looks like one. 20 still stops a
        # loop; TIMEOUT_SECONDS is the real backstop.
        "--max-turns", "20",
    ]

    # cwd is the repo, because `python -m intel` has to resolve. env is
    # inherited so the session finds the same .env and the same credential the
    # verbs use.
    env = {**os.environ}
    if _VENV_BIN.is_dir():
        env["PATH"] = str(_VENV_BIN) + os.pathsep + env.get("PATH", "")

    # A BLOCKING subprocess in a thread, not asyncio.create_subprocess_exec,
    # and on Windows there is no choice about it.
    #
    # db.py:22 installs WindowsSelectorEventLoopPolicy because psycopg cannot
    # run async on the ProactorEventLoop Windows gives you by default, and
    # main.py builds the server's loop the same way. But asyncio subprocess
    # support on Windows is a PROACTOR feature: under the selector loop,
    # create_subprocess_exec raises NotImplementedError. The two requirements
    # are mutually exclusive in one process.
    #
    # So the loop stays as psycopg needs it and this call goes to a worker
    # thread instead, where plain subprocess.run works regardless of policy.
    # `to_thread` keeps the server responsive while it waits, which matters --
    # a question takes the better part of a minute.
    def _run() -> tuple[int, bytes, bytes]:
        done = subprocess.run(cmd, cwd=str(ROOT), env=env,
                              capture_output=True, timeout=TIMEOUT_SECONDS)
        return done.returncode, done.stdout, done.stderr

    try:
        code, out, err = await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        log.warning("chat: timed out after %ss on %r", TIMEOUT_SECONDS, question)
        return {"ok": False, "answer": None,
                "error": f"That took longer than {int(TIMEOUT_SECONDS)}s and was "
                         f"stopped. Try a narrower question, or run the verb "
                         f"directly."}
    except OSError as exc:
        log.warning("chat: could not start the session -- %s", exc)
        return {"ok": False, "answer": None,
                "error": f"Could not start a Claude Code session: {exc}"}

    text = (out or b"").decode("utf-8", "replace").strip()
    if code != 0:
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        log.warning("chat: exit %s -- %s", code, detail[:1])
        return {"ok": False, "answer": None,
                "error": f"The session exited {code}."
                         + (f" {detail[0][:200]}" if detail else "")}

    # --output-format json wraps the reply. Fall back to the raw text rather
    # than failing: a changed wrapper shape should cost the metadata, not the
    # answer.
    payload, reply = None, text
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            reply = (payload.get("result") or payload.get("text") or text)
    except ValueError:
        pass

    return {
        "ok": True,
        "answer": reply.strip() if isinstance(reply, str) else str(reply),
        "question": question,
        "brand": brand,
        # Surfaced so a reader can see what it cost and how long it took --
        # this endpoint is slow and expensive by construction, and hiding that
        # would make it look free.
        "cost_usd": (payload or {}).get("total_cost_usd"),
        "duration_ms": (payload or {}).get("duration_ms"),
        "turns": (payload or {}).get("num_turns"),
    }
