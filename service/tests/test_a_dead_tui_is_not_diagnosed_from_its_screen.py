"""A drawing terminal's last line is the screen, not the cause of death.

`meaningful_failure_line` prefers the first line carrying a fatal marker and falls back to the last
meaningful line. The fallback is right for a piped process -- a runtime that dies without a marker
usually still prints something about itself. It is wrong for a full-screen TUI, where the last line is
whatever happened to be painted when the process ended.

CAPTURED FROM A LIVE INCIDENT, 2026-08-25. Two managed claude agents lost their workers and every
cold-start died in turn; `spawn_terminal_settlement` settled each spawn with the terminal's recorded
cause, and what the operator read, three times per agent, was:

    Worker terminal term_1787683898449_0938b55a is stopped: <the agent's own conversation text,
    spinner glyphs, and a compaction percentage>

They were told the worker died and handed a progress meter as the reason. The words in that line are
not wrong about anything -- a heuristic counting letters accepts it happily, because it IS prose --
so the only reliable signal is the glyphs a TUI paints and a runtime never writes.

`meaningful_failure_line`'s own docstring already promised the right behaviour: "" means "no diagnosis
recorded, which callers must render differently from a diagnosis, never as one", and the caller does
render it differently ("no output was recorded"). That branch was simply unreachable for any TUI
runtime, because there is always SOME last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.terminal_diagnostics import is_terminal_decoration, meaningful_failure_line

#: The real line, reconstructed from the spawn_requests row of the incident above.
LIVE_CHROME = (
    "← aify-comms-channel: aify-comms message received [NORMAL] sc-manager→sc-claude "
    "The non-circular part is why that matters. This team is very good at producing evidence, "
    "and every evidence activity ▰9 ✢·✢ ✶10% ✻✽✻"
)


def test_the_captured_line_is_recognised_as_decoration() -> None:
    assert is_terminal_decoration(LIVE_CHROME)


def test_a_real_error_line_is_not() -> None:
    """The direction that would hurt more. Rejecting a genuine cause leaves an operator with nothing,
    which is worse than the noise this replaces -- so the predicate must be narrow."""
    for line in (
        "Error: ENOENT: no such file or directory, open '/tmp/x'",
        "fatal: could not read Username for 'https://github.com'",
        "Traceback (most recent call last):",
        "node:internal/modules/cjs/loader:1215",
        "bash: line 1: hermes: command not found",
        "API Error: Rate limited (429) -> retrying in 30s",
    ):
        assert not is_terminal_decoration(line), line


def test_a_tui_screen_yields_no_diagnosis_rather_than_a_confident_one() -> None:
    assert meaningful_failure_line(LIVE_CHROME) == "", (
        "the screen was returned as the cause of death"
    )


def test_a_fatal_marker_still_wins_even_surrounded_by_chrome() -> None:
    """The change must not cost the case the function exists for. A TUI that DOES print an error
    still has to report it, chrome above and below."""
    raw = "\n".join([LIVE_CHROME, "Error: ENOENT: no such file or directory", LIVE_CHROME])
    assert meaningful_failure_line(raw) == "Error: ENOENT: no such file or directory"


NL = chr(10)


def test_prose_on_a_drawing_screen_is_still_not_a_cause() -> None:
    """NARROWED ON REAL DATA, and the case the first version of this fix got wrong.

    Rejecting only the decorated LINE made the picker fall back to the line above it, which in the
    captured incident was the agent's own conversation text -- better than reporting a spinner, and
    still a confident answer about nothing. If ANY line is decoration the terminal was drawing, and
    no line of a drawn screen is an epitaph. Verified against the real stored output of
    term_1787683898449_0938b55a: it yields the empty string now and yielded prose before.
    """
    raw = NL.join([
        "takes an existing question and makes its answer more reliable.",
        "I also answered their two open items. The Co-Authored-By question has a decisive fact:",
        LIVE_CHROME,
    ])
    assert meaningful_failure_line(raw) == "", "conversation text was returned as the cause of death"


def test_a_piped_runtime_keeps_its_fallback() -> None:
    """The behaviour being narrowed, not removed. A process writing plain text to a pipe usually does
    say something about its own death, and with no decoration anywhere the last line is still taken.
    """
    raw = NL.join(["starting up", "connecting to gateway", "connection closed by peer"])
    assert meaningful_failure_line(raw) == "connection closed by peer"
