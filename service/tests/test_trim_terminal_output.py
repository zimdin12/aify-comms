"""_trim_terminal_output keeps the tail starting at a clean LINE boundary so the dashboard
never seeds a fresh xterm with a half-line / truncated ANSI escape (glitchy console, 2026-06-07)."""
from service.api_core.terminal_output import _trim_terminal_output



def test_short_output_unchanged():
    assert _trim_terminal_output("hello\nworld", max_chars=65536) == "hello\nworld"


def test_trim_starts_after_a_newline_no_partial_first_line():
    # 'AAAA' is the partial first line that the raw slice would start with; the clean trim
    # must drop it and begin right after the newline.
    text = "AAAA\n" + "B" * 50
    out = _trim_terminal_output(text, max_chars=52)  # raw tail would be 'AA\n' + 50 B's
    assert "\n" not in out or out.startswith("B"), out
    assert out == "B" * 50


def test_no_newline_in_window_falls_back_to_raw_tail():
    text = "X" * 100  # one huge line, no newline
    out = _trim_terminal_output(text, max_chars=40)
    assert out == "X" * 40  # never empty


def test_trim_never_returns_empty_when_input_nonempty():
    text = "A" * 30 + "\n"  # newline is the very last char
    out = _trim_terminal_output(text, max_chars=10)
    assert out  # last char is newline => no clean boundary inside => raw tail, not empty
