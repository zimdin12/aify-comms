"""_terminal_awaiting_input_hint: a WORKING claude (live spinner footer) is never
"awaiting input", even when decision-flavored PROSE sits in the scrollback.

Regression for the subagent->blocked incident (2026-06-07): Claude Code's Task/subagent
reports are verbose and decision-oriented ("which option", "your call", "choose one"),
which matched the awaiting-input heuristic and flipped a busy claude to `blocked` while it
was actually generating. The spinner footer ("esc to interrupt" / "<glyph> <verb> for <N>s")
means claude is working — a real prompt pauses that footer — so it must suppress the hint.
"""

from service.routers.api_v2 import _terminal_awaiting_input_hint

# A subagent report full of decision words, with the live working footer at the bottom.
_SUBAGENT_WORKING_TAIL = (
    "Subagent finished. It recommends approach A over B; you should choose one based on\n"
    "latency. Tell me which option you prefer — your call on whether to continue.\n"
    "\n"
    "✻ Synthesizing for 42s (esc to interrupt)\n"
)

# The SAME decision prose, but claude is at an idle prompt (no spinner) — a genuine ask.
_DECISION_AT_IDLE_TAIL = (
    "I can refactor it two ways. Which option do you want — your call?\n"
    "\n"
    "> \n"
)


def test_working_spinner_footer_suppresses_decision_prose():
    # esc-to-interrupt footer present => claude is generating, NOT awaiting input.
    assert _terminal_awaiting_input_hint(_SUBAGENT_WORKING_TAIL) == ""


def test_spinner_verb_for_seconds_footer_also_suppresses():
    tail = "...choose one of the options above, your call.\n\n✻ Crunched for 3m 12s (esc to interrupt)\n"
    assert _terminal_awaiting_input_hint(tail) == ""


def test_decision_prompt_at_idle_still_detected():
    # No working footer => a real awaiting-input prompt must still be flagged.
    assert _terminal_awaiting_input_hint(_DECISION_AT_IDLE_TAIL) != ""


def test_yes_no_prompt_without_footer_still_detected():
    assert _terminal_awaiting_input_hint("Overwrite existing file? (y/n) ") != ""


def test_bare_prose_esc_to_interrupt_without_glyph_does_not_suppress():
    # claude writing the phrase "esc to interrupt" in prose has no spinner glyph, so it must
    # NOT manufacture the working-suppression (mirrors the tightened bridge classifier).
    tail = "As I noted, press esc to interrupt is the shortcut. Which option do you want? your call:\n"
    assert _terminal_awaiting_input_hint(tail) != ""


def test_real_prompt_after_a_stale_footer_is_still_detected():
    # Position-aware (2026-06-07): a genuine prompt that renders AFTER an old spinner footer is
    # the current bottom-of-screen state → must be flagged, NOT suppressed by the stale footer.
    tail = "✻ Crunched for 1m 0s (esc to interrupt)\nFinished. Overwrite existing file? (y/n) "
    assert _terminal_awaiting_input_hint(tail) == "Awaiting console confirmation."


def test_prose_before_footer_is_suppressed_but_prompt_after_is_not():
    # subagent decision prose BEFORE the live footer → stale scrollback (suppressed); but if a
    # real y/n renders AFTER the footer it is detected. Here the footer is last → suppressed.
    tail = "subagent says: which option, your call?\n✻ Synthesizing for 9s (esc to interrupt)\n"
    assert _terminal_awaiting_input_hint(tail) == ""


# A claude/hermes session-RESUME menu the agent already answered, lingering in the buffer as
# scrollback while the agent works on. There's no live spinner footer (idle/standing by), so the
# old code scanned the whole tail and matched the menu's "Enter to confirm" → false "Awaiting
# console confirmation" (operator-reported on idle lca/mp, 2026-07-06/07).
_STALE_RESUME_MENU_THEN_WORK = (
    "1. Resume most recent\n"
    "2. Resume full session as-is\n"
    "3. Don't ask me again\n"
    "Enter to confirm\n"
    "Confirming: Called aify-comms. Liveness confirmed — replied online and reachable, no work\n"
    "per role right now. I checked the channel for any pending dispatch and there is nothing\n"
    "outstanding; the prior prep task is complete and there are no open questions for me.\n"
    "Standing by for the next dispatch; nothing else to do at the moment. Will respond as soon\n"
    "as a new message arrives on the channel.\n"
)

# The resume picker rendered right at the bottom (pyte \r-overwrite artifact: the menu options
# collapse onto in-place lines, so almost nothing trails "Enter to confirm"). The near-bottom
# guard alone would flag this — the picker SIGNATURE is what saves it.
_RESUME_MENU_AT_BOTTOM_COLLAPSED = (
    "Found a previous session. Resume?\r"
    "1. Resume most recent\r"
    "2. Resume full session as-is\r"
    "3. Don't ask me again\r"
    "Enter to confirm\r> \r"
)


def test_stale_resume_menu_followed_by_work_is_suppressed():
    # Substantial output follows the answered menu → it's scrollback, not the live prompt.
    assert _terminal_awaiting_input_hint(_STALE_RESUME_MENU_THEN_WORK) == ""


def test_live_resume_picker_is_reported_when_auto_answer_did_not_clear_it():
    # Auto-answer is the first line of defence, not a reason to hide a dialog that is still the
    # current bottom-of-screen state. The same picker signature is also used by compaction; a
    # signature-only suppression made genuinely stuck agents look working.
    assert _terminal_awaiting_input_hint(_RESUME_MENU_AT_BOTTOM_COLLAPSED) == (
        "Awaiting console confirmation."
    )


def test_real_confirm_prompt_at_bottom_without_picker_signature_still_flagged():
    # A genuine y/n at the bottom (NO resume-picker signature) must still be detected — the
    # signature suppression is scoped to the picker, not to prompts in general.
    assert _terminal_awaiting_input_hint("Delete 12 files? This cannot be undone. (y/n) ") != ""
