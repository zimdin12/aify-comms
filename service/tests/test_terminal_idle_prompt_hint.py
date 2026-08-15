"""Did the PTY return to an idle prompt without replying — or is it still working?

`_terminal_idle_prompt_hint` reads raw terminal output and decides whether a claude PTY has gone back
to its input prompt with no structured reply. `_close_idle_pi_terminal_run_without_reply` and its
claude counterpart act on that answer, so both errors are expensive and neither raises:

  * a FALSE POSITIVE closes a run while claude is still generating, and whatever it was about to say
    is discarded as "no reply";
  * a FALSE NEGATIVE leaves the run open forever, which is the strand this detection exists to end.

It is a heuristic over TUI text, which is the most fragile thing in this repo to leave untested — the
prompt markers and the spinner vocabulary are Claude's, not ours, and they change without notice. The
tests below are written so that when they break, the failure names WHICH assumption about the TUI
stopped holding.

Nothing here needs a terminal: the function takes a string.
"""
from __future__ import annotations

from service.reconcilers.terminal_runs import _terminal_idle_prompt_hint

IDLE = "Claude PTY returned to an idle prompt without an explicit reply."

# The bottom-of-screen furniture claude leaves when it is sitting at the prompt.
PROMPT = "❯ "
BYPASS = "  ⏵⏵ bypass permissions on (shift+tab to cycle)"


def screen(*lines):
    return "\n".join(lines)


# ── the idle verdict ─────────────────────────────────────────────────────────────────────────
def test_an_idle_prompt_after_output_is_reported():
    out = _terminal_idle_prompt_hint(screen("ran the tests, all green", "", PROMPT))
    assert out == IDLE


def test_the_bypass_permissions_footer_is_also_an_idle_marker():
    assert _terminal_idle_prompt_hint(screen("done", BYPASS)) == IDLE


def test_the_for_agents_marker_is_recognised_case_insensitively():
    assert _terminal_idle_prompt_hint(screen("done", "FOR AGENTS")) == IDLE


def test_no_marker_at_all_is_no_verdict():
    """Absence of a prompt is not evidence of idleness — it is no evidence, so no hint."""
    assert _terminal_idle_prompt_hint("just some log output with no prompt furniture") == ""


def test_empty_and_blank_output_yield_nothing():
    for value in ("", "   \n\t ", None):
        assert _terminal_idle_prompt_hint(value) == ""


# ── still working beats idle ─────────────────────────────────────────────────────────────────
def test_a_spinner_after_the_prompt_means_still_working():
    """The marker can appear in scrollback ABOVE live activity. Only what follows it decides."""
    for verb in ("Calling", "Cogitating", "Honking", "Thinking", "Running", "Undulating"):
        out = _terminal_idle_prompt_hint(screen("done", PROMPT, f"✻ {verb}… (12s · esc to interrupt)"))
        assert out == "", f"{verb} after the prompt means claude is generating, not idle"


def test_an_interrupt_footer_after_the_prompt_means_still_working():
    for footer in ("press esc to interrupt", "esc to interrupt", "Press  Esc  to interrupt"):
        assert _terminal_idle_prompt_hint(screen("done", PROMPT, footer)) == "", footer


def test_activity_BEFORE_the_last_marker_does_not_suppress_the_verdict():
    """This is why the scan starts at the LAST marker: a spinner from earlier in the session is
    scrollback. Suppressing on it would strand every run that ever showed one."""
    out = _terminal_idle_prompt_hint(screen(
        "✻ Thinking… (3s · esc to interrupt)",
        "finished that",
        PROMPT,
    ))
    assert out == IDLE


def test_the_latest_marker_wins_when_several_are_present():
    out = _terminal_idle_prompt_hint(screen(
        PROMPT,
        "✻ Thinking… (3s · esc to interrupt)",
        "done",
        BYPASS,
    ))
    assert out == IDLE, "the bypass footer is the latest marker and nothing busy follows it"


# ── an interactive prompt is BLOCKED, not idle ───────────────────────────────────────────────
def test_a_question_awaiting_the_operator_is_not_reported_as_idle():
    """`_terminal_awaiting_input_hint` takes precedence. A claude waiting on a human has not
    finished without replying — closing its run would discard the answer it is about to receive."""
    out = _terminal_idle_prompt_hint(screen(
        "done",
        BYPASS,
        "Overwrite the existing file? (y/n)",
    ))
    assert out == "", "an open y/n is blocked, not idle"


def test_the_precedence_only_applies_to_a_LIVE_prompt():
    """`_terminal_awaiting_input_hint` requires the question to be the current bottom of the screen —
    a y/n with substantial output after it is answered scrollback. So an idle prompt below an old
    question is still idle, and the run is correctly closed rather than left open forever."""
    out = _terminal_idle_prompt_hint(screen(
        "Overwrite the existing file? (y/n)",
        "y",
        "wrote 42 files and finished the task, then kept going for a while longer " * 4,
        PROMPT,
    ))
    assert out == IDLE


# ── the text is normalised before any of this ────────────────────────────────────────────────
def test_ansi_escapes_do_not_hide_the_marker():
    coloured = f"\x1b[32mdone\x1b[0m\n\x1b[1;34m{PROMPT}\x1b[0m"
    assert _terminal_idle_prompt_hint(coloured) == IDLE


def test_ansi_escapes_do_not_hide_a_spinner_either():
    """Stripping must happen before the busy check too, or a coloured spinner reads as idle and the
    run is closed mid-generation."""
    coloured = f"{PROMPT}\n\x1b[33m✻ Thinking…\x1b[0m (4s · \x1b[2mesc to interrupt\x1b[0m)"
    assert _terminal_idle_prompt_hint(coloured) == ""


def test_control_characters_are_removed():
    assert _terminal_idle_prompt_hint(f"done\x07\x08\n{PROMPT}\x00") == IDLE


def test_only_the_tail_is_considered():
    """A 3000-character window. A prompt further back than that is old screen, not current state."""
    assert _terminal_idle_prompt_hint(PROMPT + "x" * 4000) == "", "the marker scrolled out of the window"
    assert _terminal_idle_prompt_hint("x" * 4000 + "\n" + PROMPT) == IDLE, "and a recent one is in it"


def test_a_spinner_that_scrolled_out_cannot_suppress_a_current_prompt():
    long_run = "✻ Thinking… (1s · esc to interrupt)\n" + ("output line\n" * 400) + PROMPT
    assert _terminal_idle_prompt_hint(long_run) == IDLE
