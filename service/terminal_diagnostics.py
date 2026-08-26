"""Pure helpers for turning a dead terminal's recorded output into ONE readable cause.

Why this exists (v0.2 WS-1). On 2026-08-07 a managed hermes worker died 65s after
spawn. The cause was recorded verbatim in `terminal_sessions.output` and was still
there 2.5 hours later:

    [hermes-managed-host] fatal: hermes dashboard at http://127.0.0.1:9147/
                          did not become ready within 60000ms: fetch failed

The requesting agent was shown "No online environment can host managed hermes for
this agent" — which was false. Nobody could see the real diagnosis, so the operator
had to relay it to a human, who found the actual cause (a hermes install with no
built web UI) in minutes once the terminal output was read. v0.1.3's N8 fixed the
CATEGORY of the refusal; it did not surface the evidence.

Kept PURE and in its own module on purpose: `api_v2.py` is ~23k lines and only
reachable through the app, so logic that lives there can only fail in production.
Everything here takes a string and returns a string — see
`service/tests/test_terminal_diagnostics.py`, whose fixture is the REAL 459-char
frame captured from that incident, not a hand-written one. (The compaction bug
shipped because its fixtures were written from documentation instead of capture.)
"""

from __future__ import annotations

import re

# Same shape as api_v2's console cleaner: CSI/OSC escapes, then stray control bytes.
# WIDENED 2026-08-18 to match `service/api_core/terminal_text.py` exactly.
#
# The old pattern handled CSI, OSC, charset and keypad — and left DCS, APC, PM and SOS payloads
# completely intact. This module produces the one-line explanation of why a terminal died, text an
# operator and other agents read, so an unstripped APC payload is raw escape bytes in a diagnostic.
#
# `terminal_text.py` claimed in prose that THIS pattern was "broader", and a reviewer ruling not to
# unify them was recorded on that sentence. Running both over real sequences measured the opposite.
# The ruling's premise was that unifying would cost something; it does not.
#
# Kept as a COPY rather than an import: a service-level leaf may not import api_core. The two are
# pinned equal by `service/tests/test_ansi_strippers_agree.py`, which is this repo's answer to two
# copies that must agree — an agreement test, not a refactor.
_ANSI_RE = re.compile(
    r"\x1b\][\s\S]*?(?:\x07|\x1b\\)|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b[PX^_][\s\S]*?\x1b\\|"
    r"\x1b[()][A-Za-z0-9]|"
    r"\x1b[=>]"
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

# Substring markers, matched case-insensitively against each cleaned line. Ordered
# by how specific they are only for readability — matching is a plain any().
_FATAL_MARKERS = (
    "fatal",
    "panic:",
    "traceback (most recent call last)",
    "unhandled exception",
    "error:",
    "err!",
    "cannot ",
    "could not ",
    "refused",
    "did not become ready",
    "did not come up",
    "timed out",
    "timeout",
    "no such file",
    "not recognized as",
    "command not found",
    "permission denied",
    "address already in use",
    "exited with code",
)

# Harness scaffolding the bridge writes around the real program output. These are
# never the cause of anything, and "[terminal exited]" is ALWAYS the last line of a
# dead terminal — so without this list the fallback would return it every time and
# the feature would report "the terminal exited" as the diagnosis.
_NOISE_PREFIXES = (
    "[terminal attached",
    "[terminal exited",
    "[terminal detached",
    "[console attached",
    "[console exited",
)

DEFAULT_MAX_CHARS = 240


def clean_terminal_text(raw: str) -> str:
    """Strip ANSI escapes and stray control bytes; normalize CRLF/CR to LF."""
    text = _ANSI_RE.sub("", str(raw or ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return _CTRL_RE.sub("", text)


def meaningful_lines(raw: str) -> list[str]:
    """Cleaned, stripped, non-empty lines with harness scaffolding removed."""
    out: list[str] = []
    for line in clean_terminal_text(raw).split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        low = stripped.lower()
        if any(low.startswith(prefix) for prefix in _NOISE_PREFIXES):
            continue
        out.append(stripped)
    return out


#: Codepoint ranges that only ever appear as terminal DECORATION: box drawing, block elements,
#: geometric shapes and dingbats. A runtime reporting why it died writes words; a full-screen TUI
#: paints these. Arrows are deliberately NOT here -- "->" appears in real messages.
_DECORATIVE_RANGES = (
    (0x2500, 0x257F),  # box drawing
    (0x2580, 0x259F),  # block elements
    (0x25A0, 0x25FF),  # geometric shapes
    (0x2700, 0x27BF),  # dingbats
)


def is_terminal_decoration(line: str) -> bool:
    """True when a line carries characters that only a drawing terminal emits.

    Used to decide whether the LAST line of a dead terminal may stand as its cause. The
    fallback below assumes a runtime that dies without a fatal marker still printed something
    explaining itself -- true of a piped process, false of a TUI, where the last line is simply
    whatever happened to be on screen.

    Captured 2026-08-25 from a real spawn failure: three cold-starts for one agent each reported
    "Worker terminal ... is stopped: <the agent's own conversation text, spinner glyphs and a
    compaction percentage>". The operator was told the worker died and handed a progress meter as
    the reason, three times over. Nothing in that line is wrong about the words -- a
    letters-based heuristic accepts it happily -- so the signal has to be the glyphs.
    """
    return any(
        any(low <= ord(char) <= high for low, high in _DECORATIVE_RANGES)
        for char in str(line or "")
    )


def meaningful_failure_line(raw: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """The ONE line that best explains why a terminal died. "" when nothing usable.

    Prefers the FIRST line carrying a fatal marker, not the last. In the captured
    incident three consecutive lines were fatal, and the later two are consequences
    of the first — the third literally says "see the error above". First-fatal is
    therefore the root cause; last-fatal would report the symptom.

    Falls back to the last meaningful line (a runtime that dies without a marker
    still usually prints something), and to "" when the recording holds nothing but
    scaffolding — an empty string means "no diagnosis recorded", which callers must
    render differently from a diagnosis, never as one.

    Bounded by `max_chars`: a truncated fatal line is useful, a 4KB ANSI dump in
    every failure message is the over-messaging problem this project already has.
    """
    lines = meaningful_lines(raw)
    if not lines:
        return ""
    chosen = ""
    for line in lines:
        low = line.lower()
        if not any(marker in low for marker in _FATAL_MARKERS):
            continue
        # A DECORATED LINE IS THE SCREEN, NOT AN EPITAPH -- and until now that reasoning was applied
        # to the FALLBACK path only, so a marker match on a TUI frame walked straight past it.
        #
        # Measured 2026-08-26 over 83 real dead consoles from the operator's database: one produced
        # 870 characters of a tool-call tree as its cause, matched on `cannot ` occurring inside the
        # agent's own English -- "source movement occurred between snapshot but CANNOT BE claimed as
        # the cause of the numerical...". Box-drawing, a caret and a bullet, reported as why the
        # terminal died. That is the same failure this module already records in
        # `is_terminal_decoration` -- "the operator was told the worker died and handed a progress
        # meter as the reason, three times over" -- reached by the other route.
        #
        # PER LINE, not per recording, and the difference matters. The fallback rejects the whole
        # recording when ANY line is decoration, because it is guessing. This path is not guessing:
        # it has a marker. So a plain-text fatal line still stands even when a spinner was painted
        # elsewhere in the same capture -- which is the founding incident's shape, and its acceptance
        # test would fail on the stricter rule.
        #
        # CONTINUE rather than break: a decorated match is not evidence that no undecorated one
        # follows, and a runtime often paints a frame before writing its own fatal line.
        if is_terminal_decoration(line):
            continue
        chosen = line
        break
    if not chosen and not any(is_terminal_decoration(line) for line in lines):
        # THE FALLBACK IS FOR PIPED RUNTIMES ONLY. A process writing plain text to a pipe usually does
        # say something about its own death, so the last line is worth reporting. A full-screen TUI
        # says nothing of the sort: its recording is the screen, and every line of it is conversation,
        # menu chrome or a progress meter.
        #
        # Narrowed on real data. The first version rejected only the decorated LINE and fell back to
        # the one above it, which for the captured incident was the agent's own prose -- an improvement
        # on reporting a spinner, and still a confident answer about nothing. If ANY line in the
        # recording is decoration then the terminal was drawing, and no line in it is an epitaph.
        chosen = lines[-1]
    limit = max(16, int(max_chars or DEFAULT_MAX_CHARS))
    if len(chosen) > limit:
        return chosen[: limit - 1].rstrip() + "…"
    return chosen


def failure_tail(raw: str, *, max_lines: int = 12, max_chars: int = 1200) -> str:
    """The last `max_lines` meaningful lines, for a caller that wants context.

    Used by the historical-console read, where the agent asked to SEE the console
    and a single line would be less than it asked for. `meaningful_failure_line` is
    the one-line form for embedding in a refusal or a run error.
    """
    lines = meaningful_lines(raw)
    if not lines:
        return ""
    text = "\n".join(lines[-max(1, int(max_lines or 12)):])
    limit = max(64, int(max_chars or 1200))
    if len(text) > limit:
        text = "…" + text[-(limit - 1):]
    return text


#: What a dying terminal writes about ITSELF. These say THAT it ended, never why -- so a recording
#: containing nothing else explains nothing, however non-empty it looks to `.strip()`.
#:
#: `[terminal failed] <error>` is deliberately NOT here: `terminal-manager.mjs` writes the reason
#: after that marker, so the line carries real content and must survive.
_SELF_REPORT_ONLY = ("[terminal exited]", "[terminal closed]", "[terminal attached")


def is_self_report_only(line: str) -> bool:
    """True when a line is a terminal reporting its own state and nothing more."""
    stripped = str(line or "").strip().lower()
    if not stripped:
        return True
    return any(stripped.startswith(marker) and stripped[len(marker):].strip(" ]") == ""
               for marker in _SELF_REPORT_ONLY)


def says_what_it_was_doing(raw: str) -> bool:
    """True when a recording holds at least one line that is not the terminal talking about itself.

    THE DISTINCTION THIS DRAWS, and why `.strip()` could not draw it. sc-architect died on
    2026-08-26 with `terminal_sessions.output` holding exactly `'[terminal exited]\n'` -- eighteen
    characters, non-empty, truthy. The console tail took its historical branch, rendered that marker,
    and the operator read "(nothing was recorded)" while 5,308 characters of the rendered screen sat
    in the `snapshot` column of the same row, ending on the search the agent was running when it
    died. Non-empty is not the same as informative.
    """
    return any(not is_self_report_only(line) for line in meaningful_lines(raw))




def richest_recording(streamed: str, replayed: str) -> tuple[str, str]:
    """The recording that explains a dead terminal, and the name of the store it came from.

    TWO PERSISTED STORES HOLD ONE TERMINAL'S OUTPUT, and they do not always agree about how much.
    `streamed` is `terminal_sessions.output`, the accumulated column. `replayed` is the same bytes
    rebuilt from the `terminal_events` rows. For most terminals the column is the fuller of the two;
    for some it holds almost nothing while the events hold everything.

    MEASURED on two real deaths, 2026-08-26, characters in each store:

        agent          terminal_sessions.output   terminal_events rebuilt
        sc-claude                        63,423                    10,913
        sc-architect                         18                    14,773

    sc-architect's eighteen characters are its own exit marker. Non-empty, so a `.strip()` gate
    passed it and the console tail rendered a line that says only THAT it ended -- while 14,773
    characters describing what it was doing sat in the events of the same terminal. The operator
    asked why the agent died and was told nothing was recorded.

    `streamed` is preferred whenever it says anything beyond the terminal's own markers: it is the
    column every path writes to, and reading it costs a query already being made. The events are the
    fallback, and they are named in the result because the caller should be able to say which store
    answered.

    Returns ("", "") when neither says anything, which is a real answer -- the terminal ended and
    left no account of itself -- and a different one from "nothing was recorded".
    """
    if says_what_it_was_doing(streamed):
        return str(streamed or ""), "output"
    if says_what_it_was_doing(replayed):
        return str(replayed or ""), "events"
    return "", ""


def terminal_end_summary(status: str, exit_code=None, exit_signal: str = "") -> str:
    """The sentence a requesting agent reads when a terminal ended under its run.

    THE WORD "STOPPED" IS A CLAIM, and it was being made about deaths nobody chose. Until this
    existed the sentence was `f"Terminal {status} before an explicit reply was recorded."`, and the
    bridge reports `stopped` for every ending that is not a spawn failure -- a clean exit, a non-zero
    exit, and a kill alike. So a worker something SIGKILLed mid-turn told its requester that its
    terminal had stopped, which is the word for a deliberate shutdown.

    On 2026-08-26 the operator asked why their agents kept dropping. This sentence, in the run list,
    is the first place anyone looks, and every instance of it said `stopped`. The exit code and signal
    that say what actually happened were written to the same row moments earlier, in the same request.

    THE THREE ANSWERS, kept apart because they are different facts:

      * a SIGNAL means something killed it. Named, because "killed by SIGKILL" is the answer.
      * a NON-ZERO code means it fell over on its own. The number is the evidence.
      * code 0, or nothing recorded, keeps the original wording. A clean exit under an open run is
        still a failure to reply, and inventing a cause for it would be worse than saying only what
        happened.

    Zero is passed through rather than defaulted, and the check is `is not None` rather than
    truthiness, for the reason the column exists: 0 is a clean exit and the most common value there
    is, while NULL means nobody reported one, and `if exit_code:` collapses the two.
    """
    label = str(status or "").strip().lower() or "ended"
    signal = str(exit_signal or "").strip()
    if signal:
        return f"Terminal was KILLED by {signal} before an explicit reply was recorded."
    if exit_code is not None and int(exit_code) != 0:
        return f"Terminal exited with code {int(exit_code)} before an explicit reply was recorded."
    if exit_code is not None:
        return f"Terminal exited cleanly (code 0) before an explicit reply was recorded."
    return f"Terminal {label} before an explicit reply was recorded."
