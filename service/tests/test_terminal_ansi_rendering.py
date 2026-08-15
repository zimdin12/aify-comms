"""Tests that CALL the pyte-screen-to-ANSI serialiser — what the dashboard console actually paints.

Visible TUI in the web console is a hard requirement, and this module is the last step before the
bytes reach xterm. Its three main functions had no test naming them, which is a poor place for a gap:
every failure here is VISUAL and silent. Nothing raises when a row is painted one column to the right
or an attribute run leaks past its cell — the console simply looks wrong, and only a human notices.

Two rules encoded here were paid for in production and are the reason this file exists:

  * A WIDE CHAR (CJK/emoji) occupies two cells in pyte — the glyph, then an EMPTY-STRING continuation
    cell. Emitting a space for that continuation shifted every following column one to the right per
    wide char (bughunt 2026-07-03), mis-aligning exactly the TUIs the snapshot exists to repaint.
  * The snapshot paints SCROLLBACK above the screen (2026-07-14), because attach/refresh calls
    `term.reset()` — needed to un-scramble a reused pane — which wipes whatever scrollback xterm had
    accumulated live. History in the snapshot is what makes a refresh non-destructive.

The fakes are deliberately minimal: these functions only ever touch `.get(x)`, `.data`, `.buffer`,
`.cursor` and the boolean attribute flags, so a real pyte screen is not needed to exercise them.
"""
from __future__ import annotations

from service.terminal_ansi import _cell_sgr, _color_sgr, _line_to_ansi, _screen_to_ansi

PLAIN = [0, 39, 49]  # reset + default fg + default bg — what a blank, unstyled cell renders as


class Char:
    def __init__(self, data, **flags):
        self.data = data
        self.fg = flags.pop("fg", "default")
        self.bg = flags.pop("bg", "default")
        for name in ("bold", "italics", "underscore", "blink", "reverse", "strikethrough"):
            setattr(self, name, flags.pop(name, False))
        assert not flags, f"unknown flag(s): {flags}"


class Line(dict):
    """A screen row. `get` is the only access the serialiser makes."""


def line(*chars):
    return Line(enumerate(chars))


class Cursor:
    def __init__(self, x=0, y=0):
        self.x, self.y = x, y


class Screen:
    def __init__(self, buffer, x=0, y=0):
        self.buffer = buffer
        self.cursor = Cursor(x, y)


# ── _color_sgr ───────────────────────────────────────────────────────────────────────────────
def test_default_and_unset_colors():
    for value in ("", "default", None):
        assert _color_sgr(value, background=False) == [39]
        assert _color_sgr(value, background=True) == [49]


def test_named_colors_and_the_background_offset():
    assert _color_sgr("red", background=False) == [31]
    assert _color_sgr("red", background=True) == [41], "background is the foreground code + 10"
    assert _color_sgr("brightcyan", background=False) == [96]
    assert _color_sgr("RED", background=False) == [31], "names are matched case-insensitively"


def test_brown_and_yellow_are_the_same_code():
    """pyte reports the 33 slot under both names; a table missing one would silently fall to default."""
    assert _color_sgr("brown", background=False) == _color_sgr("yellow", background=False) == [33]


def test_hex_truecolor():
    assert _color_sgr("ff8000", background=False) == [38, 2, 255, 128, 0]
    assert _color_sgr("ff8000", background=True) == [48, 2, 255, 128, 0]
    assert _color_sgr("000000", background=False) == [38, 2, 0, 0, 0]


def test_an_unusable_color_falls_back_to_default_rather_than_raising():
    """A malformed colour must not take the whole snapshot down — one bad cell would blank a console."""
    assert _color_sgr("zzzzzz", background=False) == [39], "six characters, not hex"
    assert _color_sgr("ff80", background=False) == [39], "wrong length"
    assert _color_sgr("notacolor", background=True) == [49]


# ── _cell_sgr ────────────────────────────────────────────────────────────────────────────────
def test_a_plain_cell_is_a_self_contained_reset():
    assert _cell_sgr(Char("a")) == PLAIN, "each run starts with 0 so it cannot inherit the previous run"


def test_attributes_appear_in_their_fixed_order_before_the_colors():
    params = _cell_sgr(Char("a", bold=True, italics=True, underscore=True,
                            blink=True, reverse=True, strikethrough=True))
    assert params == [0, 1, 3, 4, 5, 7, 9, 39, 49]


def test_colors_come_after_attributes_and_foreground_before_background():
    assert _cell_sgr(Char("a", bold=True, fg="red", bg="blue")) == [0, 1, 31, 44]


def test_a_char_missing_attributes_entirely_is_treated_as_plain():
    """Every flag is read with getattr(..., False), so a stand-in object cannot raise."""
    class Bare:
        data = "x"
    assert _cell_sgr(Bare()) == PLAIN


# ── _line_to_ansi ────────────────────────────────────────────────────────────────────────────
def test_an_empty_row_renders_as_nothing():
    assert _line_to_ansi(Line(), 10) == ""
    assert _line_to_ansi(line(Char(" "), Char(" ")), 2) == "", "unstyled blanks are not content"


def test_trailing_blanks_are_trimmed_but_interior_ones_are_kept():
    out = _line_to_ansi(line(Char("a"), Char(" "), Char("b"), Char(" "), Char(" ")), 5)
    assert out.endswith("\x1b[0m")
    assert "a b" in out
    assert not out.replace("\x1b[0m", "").endswith(" "), "trailing blanks must not be painted"


def test_a_styled_blank_counts_as_content():
    """A space with a background colour is a painted cell — trimming it would erase highlight bars."""
    out = _line_to_ansi(line(Char("a"), Char(" ", bg="blue")), 2)
    assert "\x1b[0;39;44m" in out
    assert out.count(" ") == 1


def test_an_sgr_run_is_emitted_once_and_reused_until_it_changes():
    out = _line_to_ansi(line(Char("a", fg="red"), Char("b", fg="red"), Char("c")), 3)
    assert out.count("\x1b[0;31;49m") == 1, "the run is not re-emitted per identical cell"
    assert out.index("\x1b[0;31;49m") < out.index("a")
    assert "\x1b[0;39;49m" in out, "and the change back to default IS emitted"


def test_every_row_terminates_its_own_attributes():
    out = _line_to_ansi(line(Char("a", bold=True, fg="red")), 1)
    assert out.endswith("\x1b[0m"), "an unterminated run would bleed into the next row"


def test_a_gap_in_the_row_is_painted_as_a_plain_space():
    """`line.get(x)` returning None is a hole, not the end of the row — it must hold its column."""
    out = _line_to_ansi(Line({0: Char("a"), 2: Char("b")}), 3)
    assert "a b" in out


def test_a_wide_char_continuation_cell_does_not_shift_the_row():
    """THE 2026-07-03 BUG. pyte stores a wide glyph as the character plus an EMPTY-STRING cell; the
    old code emitted a space for that continuation and pushed every later column one to the right."""
    out = _line_to_ansi(line(Char("A"), Char("漢"), Char(""), Char("B")), 4)
    painted = out.replace("\x1b[0;39;49m", "").replace("\x1b[0m", "")
    assert painted == "A漢B", (
        f"the continuation cell must contribute nothing; a space here is the one-column shift "
        f"this test exists to catch. got {painted!r}"
    )


def test_several_wide_chars_do_not_accumulate_a_shift():
    out = _line_to_ansi(line(Char("漢"), Char(""), Char("字"), Char(""), Char("X")), 5)
    painted = out.replace("\x1b[0;39;49m", "").replace("\x1b[0m", "")
    assert painted == "漢字X", f"got {painted!r}"


# ── _screen_to_ansi ──────────────────────────────────────────────────────────────────────────
def test_the_snapshot_opens_by_resetting_and_clearing():
    out = _screen_to_ansi(Screen({0: line(Char("a"))}), cols=1, rows=1)
    assert out.startswith("\x1b[0m\x1b[2J\x1b[H"), (
        "a snapshot repaints a pane that may hold anything — it must not inherit it"
    )


def test_rows_are_separated_by_crlf_with_none_after_the_last():
    out = _screen_to_ansi(Screen({0: line(Char("a")), 1: line(Char("b"))}), cols=1, rows=2)
    body = out[len("\x1b[0m\x1b[2J\x1b[H"):]
    assert body.count("\r\n") == 1, "two rows, one separator"


def test_a_missing_row_is_still_a_row():
    """`buffer.get(y, {})` — a sparse buffer must not shorten the screen, or the cursor lands wrong."""
    out = _screen_to_ansi(Screen({0: line(Char("a"))}), cols=1, rows=3)
    assert out[len("\x1b[0m\x1b[2J\x1b[H"):].count("\r\n") == 2


def test_history_is_painted_above_the_screen_each_line_followed_by_crlf():
    """This is what survives a `term.reset()` — without it a refresh costs the operator the scrollback."""
    out = _screen_to_ansi(
        Screen({0: line(Char("n"))}), cols=1, rows=1,
        history=[line(Char("o")), line(Char("p"))],
    )
    assert out.index("o") < out.index("p") < out.index("n"), "oldest first, screen last"
    assert out[len("\x1b[0m\x1b[2J\x1b[H"):].count("\r\n") == 2, "one after each history line"


def test_the_cursor_is_restored_as_1_based_cup():
    out = _screen_to_ansi(Screen({0: line(Char("a"))}, x=3, y=2), cols=10, rows=5)
    assert out.endswith("\x1b[3;4H"), "pyte is 0-based, CUP is 1-based, row before column"


def test_the_cursor_is_clamped_into_the_viewport():
    assert _screen_to_ansi(Screen({}, x=99, y=99), cols=10, rows=5).endswith("\x1b[5;10H")
    assert _screen_to_ansi(Screen({}, x=-4, y=-9), cols=10, rows=5).endswith("\x1b[1;1H")


def test_an_unreadable_cursor_costs_only_the_cursor():
    """The screen content is worth more than the cursor position, so a bad cursor is swallowed."""
    class NoCursor:
        buffer = {0: line(Char("a"))}
        cursor = None
    out = _screen_to_ansi(NoCursor(), cols=1, rows=1)
    assert "a" in out
    assert "H" not in out[len("\x1b[0m\x1b[2J\x1b[H"):], "no CUP was appended"
