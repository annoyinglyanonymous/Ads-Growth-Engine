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
              "versus", "experiments", "experiment", "propose")

#: `propose` is on the list because leaving it off cost a real question.
#:
#: Asked "What ads creatives can be improved?", the session did the only thing
#: left to it: ran `fatigue`, got a page of rows, and reached for Bash to pick
#: them apart -- which the allowlist refuses, correctly. It then spent eight
#: turns going nowhere and returned an error envelope.
#:
#: That question IS propose's job. It reads fatigue, the CPA bridge and angle
#: coverage and returns the ideas already ranked, which is one call instead of
#: a reconstruction the session is not permitted to perform. A read verb kept
#: off this list does not make the box safer, it makes it reach for the tool
#: that is genuinely dangerous.
#:
#: `record` and `live` stay off, and for unchanged reasons: one writes, the
#: other spends the Meta rate limit one keystroke at a time.

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
You are answering one question about this brand's Meta ads, in a browser.

STYLE
Plain sentences. No markdown at all: no asterisks, no bold, no headings, no
tables, no bullet points unless the answer genuinely is a list of things, in
which case use plain lines.

Short. Two or three sentences answers most questions. Answer what was asked
and then stop -- no preamble, no restating the question, no description of
what you are about to do, no summary of what you did, no offer to help
further, no closing paragraph.

HOW TO FIND THE ANSWER
Run the read verbs. `python` on your PATH is already this project's virtualenv
interpreter, so use it plainly and do not substitute an absolute path or
`.venv/Scripts/python.exe` -- those spellings are not permitted and will be
refused:

    {python} -m intel <verb> --brand {brand}

Every verb prints JSON.

THE ONE RULE
Every figure you state must have come out of a verb you actually ran. Do not
calculate: no adding up a column, no percentages, no deriving a rate from two
other numbers. If the number is not in a verb's output, say so in one sentence
and name the verb you looked in.

CAVEATS, SPARINGLY
Mention a caveat only when it changes how the answer should be read -- a
decline or a conversion count inside the unsettled window, a CPA that is null,
a comparison across different optimization goals. One short clause, in your
own words. Do not quote the caveat text verbatim, do not print field names
like unsettled_days, and do not add a trust paragraph to an answer that does
not need one.

Never rank on a null CPA: it is undefined, not zero. Never compare CPA between
ads with different optimization_goal -- say they are not comparable. Say
"daily frequency", never "frequency". Never sum reach. Report unconfident
fatigue rows as a count.

Do not label your sentences as facts or opinions. If you are reading something
into the numbers rather than quoting them, a short "looks like" or "probably"
carries it.

WHAT YOU CANNOT DO
Approve, activate an angle, conclude an experiment, file anything, pull, or
apply a migration. If the answer is that somebody has to sign something, say
so in one line.
"""


#: What a classification pass is told. Deliberately not SYSTEM: that one is
#: written for a person reading sentences in a browser and forbids the only
#: output shape this needs.
CLASSIFY_SYSTEM = """\
You are labelling advertising copy against a fixed vocabulary. You return JSON
and nothing else.

No preamble, no explanation, no markdown fence, no trailing commentary. The
first character of your reply is [ and the last is ]. A reply that is not
parseable JSON is discarded and the work is wasted.

Use only the values you are given. Inventing a value outside the vocabulary
makes the row unfilable, and the row is dropped.

Where the copy does not support a label, say null rather than guessing. A null
is a usable answer; a wrong label is worse than none, because it becomes a
number somebody compares against.
"""


async def classify(prompt: str, *, timeout: float | None = None,
                   system: str | None = None) -> list:
    """Ask for JSON, with NO tools at all. Returns the parsed list.

    WHY THIS IS NOT answer().

    `answer` grants Bash for the read verbs, because a question about the
    account needs to go and look. A classification pass needs nothing: it is
    handed the copy and asked to label it, so every tool is a grant with no
    purpose, and a grant with no purpose is the one that gets used for
    something nobody intended. `--allowedTools` is empty here and the deny list
    is still applied on top.

    It also cannot use SYSTEM. That prompt is written for somebody reading
    sentences in a browser -- "plain sentences, no markdown, two or three
    sentences answers most questions" -- which is the opposite of a JSON array
    of forty rows.

    `system` replaces CLASSIFY_SYSTEM for a caller whose JSON is not a
    vocabulary label -- the campaign triage in scripts/suggest.py is the one.

    Raises ChatUnavailable when there is no `claude` on PATH, and ValueError
    when the reply will not parse. The caller decides whether a batch that
    would not parse is fatal; here it is only a fact.
    """
    if not available():
        raise ChatUnavailable(
            "The `claude` command is not on PATH, so nothing can be "
            "classified. The pack still builds: try --dry-run.")

    # THE PROMPT GOES ON STDIN, not argv. Windows caps a command line at
    # 32,767 characters, and the campaign triage in scripts/suggest.py sends
    # one row per campaign -- ~34k on this account before anything else. `-p`
    # with no positional prompt reads it from stdin, which has no such cap.
    cmd = [
        "claude", "-p",
        "--model", "claude-opus-5",
        "--output-format", "json",
        "--append-system-prompt", system or CLASSIFY_SYSTEM,
        # No --allowedTools at all. The deny list stays, belt and braces, for
        # the same reason _denied() exists: the cost of the belt failing is a
        # write from something that was only ever meant to read copy.
        "--disallowedTools", *_denied(),
        "--max-turns", "1",
    ]

    env = {**os.environ}
    if _VENV_BIN.is_dir():
        env["PATH"] = str(_VENV_BIN) + os.pathsep + env.get("PATH", "")

    def _run() -> tuple[int, bytes, bytes]:
        done = subprocess.run(
            cmd, cwd=str(ROOT), env=env, capture_output=True,
            input=prompt.encode("utf-8"),
            timeout=TIMEOUT_SECONDS if timeout is None else timeout)
        return done.returncode, done.stdout, done.stderr

    code, out, err = await asyncio.to_thread(_run)
    text = (out or b"").decode("utf-8", "replace").strip()

    payload = None
    try:
        payload = json.loads(text)
    except ValueError:
        pass
    if not isinstance(payload, dict):
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        raise ValueError(f"the session returned no JSON envelope (exit {code})"
                         + (f": {detail[0][:160]}" if detail else ""))
    if payload.get("is_error") or "result" not in payload:
        raise ValueError(f"the session failed: {payload.get('subtype')} after "
                         f"{payload.get('num_turns')} turn(s)")

    reply = (payload.get("result") or "").strip()
    # A fence survives an instruction not to use one often enough to be worth
    # stripping rather than failing on.
    if reply.startswith("```"):
        reply = reply.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        rows = json.loads(reply)
    except ValueError as exc:
        raise ValueError(f"the reply was not JSON: {exc}. First 160 chars: "
                         f"{reply[:160]!r}") from None
    if not isinstance(rows, list):
        raise ValueError(f"expected a JSON array, got {type(rows).__name__}")
    return rows


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
        # The CLI prints its result JSON even when it exits non-zero, and that
        # payload carries the reason. The first version of this read stderr --
        # which is EMPTY for a max-turns stop -- and discarded stdout, so it
        # reported a bare "the session exited 1" while holding the explanation
        # the whole time. That cost most of an hour ruling out the event loop,
        # the worker thread and the interpreter before anyone looked in the
        # stream it had thrown away. Read the payload first, stderr second.
        subtype, turns = None, None
        try:
            failed_payload = json.loads(text)
            if isinstance(failed_payload, dict):
                subtype = failed_payload.get("subtype")
                turns = failed_payload.get("num_turns")
        except ValueError:
            pass
        detail = (err or b"").decode("utf-8", "replace").strip().splitlines()
        log.warning("chat: exit %s subtype=%s turns=%s -- %s",
                    code, subtype, turns, detail[:1])

        if subtype == "error_max_turns":
            msg = (f"That question needed more than {turns} steps and was "
                   f"stopped. Try asking for one thing at a time.")
        elif detail:
            msg = f"The session exited {code}. {detail[0][:200]}"
        else:
            msg = f"The session exited {code} without saying why."
        return {"ok": False, "answer": None, "error": msg}

    # --output-format json wraps the reply. Fall back to the raw text rather
    # than failing: a changed wrapper shape should cost the metadata, not the
    # answer.
    payload, reply = None, text
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            # PRESENT-BUT-EMPTY IS NOT ABSENT, and `or` cannot tell them apart.
            # `payload.get("result") or ... or text` falls through on an empty
            # string exactly as it does on a missing key, and `text` is the
            # whole envelope -- so a session that finished with nothing to say
            # printed its own receipt, the same leak as a failed run. Keyed on
            # membership so an empty answer stays empty and is caught below.
            if "result" in payload:
                reply = payload["result"]
            elif "text" in payload:
                reply = payload["text"]
            # else: an unrecognised wrapper. Keep `text`, on the original
            # reasoning that a changed shape should cost the metadata rather
            # than the answer.
    except ValueError:
        pass

    # A ZERO EXIT IS NOT A SUCCESS, and this is the branch that was missing.
    #
    # The CLI can finish cleanly and still report a failed run: `is_error` is
    # true, `subtype` says which kind, and there is NO `result` key at all. The
    # fallback above then handed back `text` -- the entire envelope -- and the
    # page printed a wall of JSON, session ids and token counts where a
    # sentence belonged. Observed on "What ads creatives can be improved?":
    # subtype error_during_execution, eight turns, a denied Bash call, and a
    # 900-character blob on screen ending in "$0.39".
    #
    # Checked AFTER the parse rather than before, so a run that failed but
    # still wrote a partial answer keeps the answer.
    if isinstance(payload, dict) and payload.get("is_error"):
        denied = [d.get("tool_name") for d in (payload.get("permission_denials") or [])
                  if isinstance(d, dict)]
        subtype = payload.get("subtype")
        turns = payload.get("num_turns")
        log.warning("chat: is_error subtype=%s turns=%s denied=%s on %r",
                    subtype, turns, denied, question)
        if reply is payload.get("result") and isinstance(reply, str) and reply.strip():
            # It failed on the way out but had already said something useful.
            pass
        else:
            if denied:
                # Naming the tool is the whole value here. "Something went
                # wrong" sends somebody to the logs; this says the session
                # wanted a command it is not allowed, which is a decision
                # rather than a fault.
                msg = (f"The session tried to use {denied[0]}, which it is not "
                       f"allowed, and stopped after {turns} steps. It can only "
                       f"run the read verbs. Try asking for one thing at a "
                       f"time, or run the verb yourself.")
            elif subtype == "error_max_turns":
                msg = (f"That question needed more than {turns} steps and was "
                       f"stopped. Try asking for one thing at a time.")
            else:
                msg = (f"The session stopped after {turns} step(s) without "
                       f"finishing" + (f" ({subtype})" if subtype else "") +
                       ". Try a narrower question.")
            return {"ok": False, "answer": None, "error": msg}

    # An empty or non-string reply is a failure too, whatever the envelope
    # claimed. Returning "" would render as a blank answer box, which reads as
    # the model having nothing to say rather than as something having broken.
    if not isinstance(reply, str) or not reply.strip():
        log.warning("chat: empty reply on %r (subtype=%s)", question,
                    (payload or {}).get("subtype"))
        return {"ok": False, "answer": None,
                "error": "The session finished without writing an answer. "
                         "Try asking again, or narrow the question."}

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
