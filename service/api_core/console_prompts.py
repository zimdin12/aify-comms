"""Console dialogs a managed worker cannot get past on its own — decided HERE.

WHY THIS TIER. These rules are a model of a claude SCREEN, which is a runtime concept, and this
service already owns runtime concepts: `service/runtimes/*.py` declares each runtime's session
variables and builds its launch. The host that runs the process must not learn what claude looks
like — it is about to run processes for aify-dashboard and aify-project-graph too, and a host
carrying one service's screen model would have to carry all of them.

It was briefly implemented in aify-env, to unblock a fleet at 5am on 2026-09-03. That was the wrong
layer and the operator said so; this is the move.

MATCHED AGAINST THE RENDERED SCREEN, never the raw stream, and that distinction is not academic.
Measured the same night: claude does not send spaces, it moves the cursor --

    I<ESC>[1Cam<ESC>[1Cusing<ESC>[1Cthis<ESC>[1Cfor<ESC>[1Clocal<ESC>[1Cdevelopment

-- so a matcher run on raw bytes is looking for a string that is never transmitted. It watched the
dialog it was written for and did nothing, with every one of its tests green, because the tests had
been written from what a screen LOOKS like. This service already keeps a pyte-rendered screen per
terminal (`render_live_screen`), which is a real terminal emulator and the only honest input.

WHAT IS DELIBERATELY NOT HERE. The trust dialog ("do you trust the files in this folder"). It is
persisted per project as `hasTrustDialogAccepted` in the operator's `~/.claude.json`, so it is STATE
to write once rather than a dialog to answer every time. Answering it here would be catching a
symptom that has a cause with an off switch.
"""

from __future__ import annotations

import re
from typing import NamedTuple

#: A menu cursor. Claude marks the selected option with one of these.
CURSOR = re.compile(r"[❯›▶]")

#: The keystrokes a rule can send. Named so a typo is an import error rather than a stray byte in
#: somebody's terminal.
ENTER = "\r"
DOWN = "[B"


class PromptAnswer(NamedTuple):
    """What to type, and which rule decided — the name travels so a log can attribute a keystroke."""

    rule: str
    keys: str
    why: str


#: `render_live_screen` returns an ANSI-DECORATED render: pyte has already resolved the layout --
#: cursor moves become real spaces, which is the hard part and the reason matching happens here at
#: all -- but it re-emits colour and style codes between the words. Measured against the real
#: capture: the rendered line reads `I<SGR> <SGR>am<SGR> <SGR>using`, so a plain `in` test still
#: fails. Caught by this module's own control test, which renders the captured bytes and asserts the
#: dialog is READABLE before asserting anything is answered.
#: Built from `chr(27)` and `re.escape` rather than written as escapes. A pattern carrying a raw
#: ESC byte is invisible in a diff, and one carrying backslashes has been silently mangled by
#: three different tools tonight. This source line is pure ASCII with no backslash in it at all,
#: so nothing between here and the interpreter can change what it means.
_ESC = chr(27)
ANSI = re.compile(re.escape(_ESC + chr(91)) + '[0-9;?]*[ -/]*[@-~]')


def plain_text(screen: str) -> str:
    """The screen as a person reads it: layout from pyte, styling removed."""
    return ANSI.sub("", str(screen or ""))


def _cursor_line(screen: str) -> str:
    """The line the menu cursor is on, or "". Latest wins: a screen accumulates nothing, but a
    partially repainted one can briefly show two."""
    for line in reversed(screen.splitlines()):
        if CURSOR.search(line):
            return line
    return ""


#: A resume menu's highlighted default is "Resume from summary", so a stray Enter there silently
#: SUMMARISES — compacts — a session's entire context. The aify-comms bridge did exactly that on
#: every worker cold-start for a while, because a rule matched the flag name in ordinary boot output.
#: While one is the live thing on screen, no rule here may answer anything.
RESUME_MENU = re.compile(r"Resume (?:from summary|full session)", re.I)


def answer_for_screen(screen: str, *, resume_policy: str = "") -> PromptAnswer | None:
    """The keystrokes this dialog needs, or None for every other screen.

    PURE: a screen in, an answer out. No clock, no database, no terminal. The paths that matter are
    the ones that only happen when something is already wrong, and they have to be testable.

    `resume_policy` is the fact only this service holds, and it is why compaction cannot be decided
    by a host: "keep the context" and "start fresh" want opposite answers to the same dialog.
    """
    text = plain_text(screen)
    if not text.strip():
        return None
    if RESUME_MENU.search(text):
        # Refused wholesale rather than ordered by position: a resume menu is the one screen where
        # a wrong keystroke is unrecoverable, and no rule here is worth that risk.
        return None

    cursor_line = _cursor_line(text)

    # ── the development-channels acknowledgment ────────────────────────────────────────────────
    #
    # `claude-aify` launches with `--dangerously-load-development-channels`, which is how the channel
    # that delivers dispatches is loaded at all, and claude answers with a first-run acknowledgment
    # it then WAITS at. The worker registers `online` and claims nothing: "up but deaf", measured on
    # the operator's fleet on 2026-07-03 and again on 2026-09-03.
    #
    # MATCHED ON THE DIALOG'S OWN QUESTION LINE, never on the flag name -- the flag appears in every
    # worker's boot output, and matching it is how the bridge came to press Enter into a resume menu.
    if "I am using this for local development" in text and "Enter to confirm" in text:
        if "I am using this for local development" in cursor_line:
            return PromptAnswer(
                rule="dev-channels-accept",
                keys=ENTER,
                why="the development-channels acknowledgment parks a worker before it can claim work",
            )
        return None

    return None


#: ANSWERED ONCE PER TERMINAL PER RULE, and that is not an optimisation.
#:
#: `comms_console_input`'s own documentation records the measurement: a repeated Enter into a stuck
#: claude draft was tried five times, every call reported success, and nothing ever submitted. A loop
#: pressing keys at a screen it cannot change is indistinguishable from one that is working, and it
#: types into whatever replaces that screen.
#:
#: In memory, bounded, and deliberately not persisted. A service restart does not kill terminals, so
#: this could in principle re-answer one dialog per terminal after a restart -- which is the same
#: cost as answering it the first time, and far cheaper than a schema change for a fact that stops
#: mattering the moment the dialog is gone.
_ANSWERED: dict[str, set[str]] = {}
_MAX_ANSWERED_TERMINALS = 256


def should_answer(terminal_id: str, answer: "PromptAnswer | None") -> bool:
    """True once per terminal per rule. Records the decision as a side effect, so a caller cannot
    ask and then forget to record it -- which is how a once-only guard becomes a loop."""
    if answer is None:
        return False
    tid = str(terminal_id or "")
    if not tid:
        return False
    seen = _ANSWERED.get(tid)
    if seen is None:
        if len(_ANSWERED) >= _MAX_ANSWERED_TERMINALS:
            # Oldest-ish eviction: dicts keep insertion order, so this drops the least recently
            # ADDED terminal. A fleet larger than the cap re-answers one dialog for an old terminal
            # at worst, which is the same keystroke it would have sent anyway.
            _ANSWERED.pop(next(iter(_ANSWERED)), None)
        seen = set()
        _ANSWERED[tid] = seen
    if answer.rule in seen:
        return False
    seen.add(answer.rule)
    return True


def forget_terminal(terminal_id: str) -> None:
    """Drop a terminal's record when it ends. Terminal ids are not reused, so this is housekeeping
    rather than correctness -- but an unbounded map on a long-running service is its own defect."""
    _ANSWERED.pop(str(terminal_id or ""), None)
