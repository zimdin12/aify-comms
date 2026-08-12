"""Reading a terminal's screen: strip the escapes, then decide what the tail MEANS. Leaf module.

Layer-0 slice of the v0.5.4 decomposition, and PURE — no database, no process state.

`_terminal_awaiting_input_hint` is the one that matters: it decides whether a claude sitting at a
prompt is WAITING FOR THE OPERATOR or still working, which is the difference between the dashboard
reporting an agent blocked and reporting it busy. The `_CLAUDE_WORKING_FOOTER_RE` guard exists because
a live spinner footer must never be read as a prompt — that misread is what made a working agent look
stalled.

`_ANSI_RE` came with them, and had to: both carrier readers are here, and a leaf may not import the
carrier. NOTE `service/terminal_diagnostics.py` keeps its OWN `_ANSI_RE` with a broader pattern — the
reviewer ruled that a separate module with its own contract, so unifying them would be a behaviour
change rather than a move.

`_terminal_prompt_hint_from_raw` was in this slice and was PULLED OUT. It calls
`_terminal_awaiting_input_hint`, so the call graph made it look like a closed group — but it also
reads `_PROMPT_HINT_CACHE`, a MUTABLE PROCESS GLOBAL. Moving that is a process-identity change, not a
relocation, and it needs its own slice with the identity receipt. My first closure check only examined
calls to carrier FUNCTIONS and missed it; the undefined-name sweep caught it.
"""

from __future__ import annotations

import re


_ANSI_RE = re.compile(
    r"\x1b\][\s\S]*?(?:\x07|\x1b\\)|"
    r"\x1b\[[0-?]*[ -/]*[@-~]|"
    r"\x1b[PX^_][\s\S]*?\x1b\\|"
    r"\x1b[()][A-Za-z0-9]|"
    r"\x1b[=>]"
)

_CLAUDE_WORKING_FOOTER_RE = re.compile(
    r"[✱✶✽✺✹✷✵✳✢✻][^\n]*esc to interrupt"
    r"|[✱✶✽✺✹✷✵✳✢✻]\s+\S+\s+for\s+\d+\s*[hms]\b",
    re.I,
)


def _terminal_text_compact(text: str) -> str:
    cleaned = _ANSI_RE.sub(" ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip().lower()


def _terminal_awaiting_input_hint(output: str) -> str:
    clean = _ANSI_RE.sub("", str(output or ""))
    clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
    tail = clean[-2000:].strip()
    if not tail:
        return ""
    # WORKING beats AWAITING-INPUT: a live claude spinner footer ("✻ <verb> for <N>s" /
    # a spinner glyph on the "esc to interrupt" line) means claude is GENERATING — incl.
    # while running subagents/Task tools — not waiting on the operator. Without this, the
    # decision-flavored PROSE patterns below ("which option", "your call", "choose one")
    # matched a subagent's verbose report and a busy claude read `blocked` mid-work (the
    # subagent→blocked incident, 2026-06-07). A REAL interactive prompt pauses the spinner
    # (claude is waiting), so this never suppresses a genuine y/n / decision prompt.
    # Requires a real spinner glyph (mirrors claude-console-spinner.js), so claude writing
    # "esc to interrupt" in prose can't itself manufacture the suppression.
    # A real interactive prompt is the CURRENT bottom-of-screen state, so it appears AFTER the
    # last live spinner footer; only scan the region after it (text before is stale scrollback
    # while claude works — subagent/Task prose, or a y/n echoed in tool output mid-generation).
    # If the footer is the very last thing → generating, not awaiting. No footer → scan all.
    footer_end = -1
    for _m in _CLAUDE_WORKING_FOOTER_RE.finditer(tail):
        footer_end = _m.end()
    region = tail[footer_end:] if footer_end >= 0 else tail
    if not region.strip():
        return ""
    # A genuine interactive prompt is the CURRENT bottom-of-screen state. An auto-answered
    # startup artifact \u2014 the claude/hermes "Resume session? \u2026 2. Resume full session as-is \u2026
    # Enter to confirm" menu, or an old y/n echoed in tool output \u2014 lingers in the captured
    # buffer as SCROLLBACK: the agent already answered it and emitted more output since. When
    # substantial non-whitespace content follows a match, the prompt is NOT the live bottom of
    # the screen, so suppress it. This killed the false "Awaiting console confirmation" on idle
    # lca/mp that had long since answered the resume menu and were actively working
    # (2026-07-06/07). A real prompt has only a cursor / short hint after it, so the small
    # trailing budget never suppresses a genuine y/n or selection.
    def _live_prompt(pattern: str, max_trailing: int = 120) -> bool:
        last = None
        for _pm in re.finditer(pattern, region, re.I):
            last = _pm
        if last is None:
            return False
        trailing = re.sub(r"\s+", "", region[last.end():])
        return len(trailing) <= max_trailing

    # NOTE (2026-07-14): the old `resume_picker` SUPPRESSION lived here — if the text contained
    # "resume full session" / "don't ask me again" etc., the "Enter to confirm" branch below was
    # skipped, on the theory that the picker is an auto-answered startup screen lingering as
    # scrollback. It is DELETED, because it silenced the real thing: the claude COMPACTION dialog
    # offers the very same options ("Resume from summary / Resume full session as-is / Don't ask
    # me again"), so an agent genuinely stuck on it matched the picker signature and was
    # suppressed — it rendered as `working` while doing nothing, with no way for an operator to
    # see why (live: lc-manager, awaiting_input=0, sitting at the dialog).
    #
    # The suppression existed only to compensate for reading the raw byte LOG, where answered
    # scrollback is indistinguishable from a live dialog. Status callers now pass the
    # RECONSTRUCTED SCREEN (`_terminal_prompt_hint_from_raw`), where a dismissed dialog is simply
    # not present — so the heuristic is both unnecessary and harmful. Deleting it is what makes
    # `blocked` reachable for the case that actually blocks agents in practice.
    #
    # Hard confirmation prompts — always honored (near-bottom).
    if _live_prompt(r"(\(y/n\)|\[y/n\]|\by/n\b|\[y/N\]|\[Y/n\]|yes/no|are you sure|overwrite\?|\bpassword\s*:\s*$|passphrase\s*:\s*$)"):
        return "Awaiting console confirmation."
    if _live_prompt(r"(press\s+(enter|any key)|enter\s+to\s+confirm)"):
        return "Awaiting console confirmation."
    if _live_prompt(r"(use arrows|press enter to (select|confirm)|\(use arrow keys\))"):
        return "Awaiting console selection."
    # Claude Code can stop at an interactive prompt without emitting a formal
    # dashboard reply. This keeps the run active but no useful work is moving.
    # Do not match the normal Claude footer ("bypass permissions on",
    # "shift+tab", "for agents") by itself; that footer is present at idle
    # prompts after successful work too. Same near-bottom staleness guard applies \u2014
    # a decision prompt the agent already moved past is stale scrollback.
    if _live_prompt(r"(tell me which|need (?:a )?decision|which (option|one)|choose (one|an option)|say the word)"):
        return "Awaiting console input."
    if _live_prompt(r"your call\s*(?:[:\u2014-]|\n|$)") and re.search(
        r"(decision|option|choose|execute|continue|switch|revert|debug|drive|say the word)",
        region,
        re.I,
    ):
        return "Awaiting console input."
    return ""
