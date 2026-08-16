"""Resizing a live console screen — and the two ways it declines to.

`feed_live_screen` and `render_live_screen` have tests; `resize_live_screen` and `live_screen_count`
had none. Resize is the operation an operator triggers by dragging the console pane, and its failures
are visual and silent: a screen resized when it did not need to be loses content pyte cannot recover,
and a screen NOT resized when the PTY was renders every subsequent line at the wrong width.

TWO RULES CARRY THE WEIGHT:

  * It resizes only when the clamped dimensions actually DIFFER. A redundant `pyte` resize is not
    free — it reflows the grid — so a resize control that repeats the current size must be a no-op
    rather than a rebuild.
  * On any failure it DROPS the screen and returns False, matching every other entry point in this
    module: the caller then falls back to the replay path. The comment on the sibling says it
    plainly — never serve a corrupt screen. A screen kept after a failed resize is exactly that.

`_LIVE_SCREENS` is a process-global, so every test here restores it. The module's own docstring is
about state versus format, and a test that leaks screens into the next one is the state half going
wrong in miniature.
"""
from __future__ import annotations

import pytest

from service import terminal_snapshot
from service.terminal_snapshot import (
    TERMINAL_MAX_COLS,
    TERMINAL_MAX_ROWS,
    TERMINAL_MIN_COLS,
    TERMINAL_MIN_ROWS,
    drop_live_screen,
    feed_live_screen,
    live_screen_count,
    render_live_screen,
    resize_live_screen,
)

pytestmark = pytest.mark.skipif(not terminal_snapshot._HAVE_PYTE, reason="pyte is not installed")

TERMINAL = "term-resize-test"
ANSI = "\x1b[32mhello\x1b[0m"  # a screen is only created once ANSI appears — plain logs stay logs


@pytest.fixture(autouse=True)
def restore_the_live_screens():
    saved = dict(terminal_snapshot._LIVE_SCREENS)
    try:
        yield
    finally:
        terminal_snapshot._LIVE_SCREENS.clear()
        terminal_snapshot._LIVE_SCREENS.update(saved)


def make_screen(cols=100, rows=28):
    assert feed_live_screen(TERMINAL, ANSI, cols=cols, rows=rows), "the fixture screen was not created"
    return terminal_snapshot._LIVE_SCREENS[TERMINAL]


# ── it declines when there is nothing to resize ──────────────────────────────────────────────
def test_an_untracked_terminal_is_false_not_an_error():
    """The commonest input: a resize control for a terminal whose screen was never created (a plain
    log, or one dropped after a failure). False routes the caller to the replay path."""
    assert resize_live_screen("never-seen", 120, 40) is False
    assert resize_live_screen("", 120, 40) is False
    assert resize_live_screen(None, 120, 40) is False
    assert live_screen_count() == 0 or TERMINAL not in terminal_snapshot._LIVE_SCREENS


# ── the resize itself ────────────────────────────────────────────────────────────────────────
def test_a_real_resize_is_applied():
    live = make_screen(cols=100, rows=28)
    assert resize_live_screen(TERMINAL, 120, 40) is True
    assert (live.cols, live.rows) == (120, 40)

    rendered = render_live_screen(TERMINAL)
    assert rendered is not None
    assert rendered[1:] == (120, 40), "render reports the NEW geometry, not the creation geometry"


def test_resizing_to_the_current_size_reports_success_and_preserves_the_screen():
    """The `!=` guard around the resize call is an OPTIMISATION, not a correctness rule, and this
    test says so rather than inventing a reason. Measured directly: `pyte.HistoryScreen.resize` to
    the same dimensions leaves display and history byte-identical, so removing the guard is not
    observable from outside — it only spends work. What IS asserted is the contract callers depend
    on: content survives and the answer is True, because the requested state holds either way."""
    live = make_screen(cols=100, rows=28)
    before = live.render()
    assert resize_live_screen(TERMINAL, 100, 28) is True
    assert (live.cols, live.rows) == (100, 28)
    assert live.render() == before, "a no-change resize must not disturb what is on the screen"


def test_the_comparison_happens_after_clamping():
    """A request for 5000 columns on a screen already at the 500 maximum is the SAME size once
    clamped, so it takes the unchanged path and the geometry does not move."""
    live = make_screen(cols=TERMINAL_MAX_COLS, rows=TERMINAL_MAX_ROWS)
    before = live.render()
    assert resize_live_screen(TERMINAL, 5000, 9000) is True
    assert (live.cols, live.rows) == (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS)
    assert live.render() == before


# ── clamping ─────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "cols,rows,expected",
    [
        (5000, 9000, (TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS)),
        (1, 1, (TERMINAL_MIN_COLS, TERMINAL_MIN_ROWS)),
        (-40, -10, (TERMINAL_MIN_COLS, TERMINAL_MIN_ROWS)),
        (0, 0, (100, 28)),
        (None, None, (100, 28)),
    ],
)
def test_the_requested_grid_is_clamped_before_it_is_applied(cols, rows, expected):
    """A PTY can report an absurd or zero geometry during startup. An unclamped resize would either
    allocate an enormous grid or collapse the console to nothing."""
    live = make_screen(cols=80, rows=24)
    assert resize_live_screen(TERMINAL, cols, rows) is True
    assert (live.cols, live.rows) == expected


def test_zero_and_none_mean_default_not_minimum():
    """`int(cols or 100)` — a falsy dimension is "unspecified", so it takes the DEFAULT geometry
    rather than clamping down to the 20x5 floor a literal 0 would otherwise produce."""
    live = make_screen(cols=80, rows=24)
    resize_live_screen(TERMINAL, 0, 0)
    assert (live.cols, live.rows) == (100, 28)
    assert (live.cols, live.rows) != (TERMINAL_MIN_COLS, TERMINAL_MIN_ROWS)


def test_an_unusable_dimension_drops_the_screen_rather_than_keeping_a_bad_one():
    """`int("wide")` raises inside the try, and every failure path in this module drops the screen so
    the caller falls back to the replay path. Never serve a corrupt screen."""
    make_screen()
    assert TERMINAL in terminal_snapshot._LIVE_SCREENS
    assert resize_live_screen(TERMINAL, "wide", 40) is False
    assert TERMINAL not in terminal_snapshot._LIVE_SCREENS, "the screen must not survive a failed resize"
    assert render_live_screen(TERMINAL) is None


def test_a_numeric_string_is_accepted_rather_than_dropped():
    """`int("120")` succeeds — a resize control arriving with stringified numbers is ordinary, not a
    failure, and dropping the screen for it would blank a working console."""
    live = make_screen(cols=80, rows=24)
    assert resize_live_screen(TERMINAL, "120", "40") is True
    assert (live.cols, live.rows) == (120, 40)


# ── live_screen_count ────────────────────────────────────────────────────────────────────────
def test_the_count_tracks_creation_and_removal():
    terminal_snapshot._LIVE_SCREENS.clear()
    assert live_screen_count() == 0

    make_screen()
    assert live_screen_count() == 1
    feed_live_screen("term-second", ANSI, cols=80, rows=24)
    assert live_screen_count() == 2

    drop_live_screen(TERMINAL)
    assert live_screen_count() == 1
    drop_live_screen("term-second")
    assert live_screen_count() == 0


def test_a_failed_resize_is_visible_in_the_count():
    """The drop-on-failure is not just an internal detail — it is the bound on how many screens the
    process holds, and this is the observable that proves it happened."""
    terminal_snapshot._LIVE_SCREENS.clear()
    make_screen()
    assert live_screen_count() == 1
    resize_live_screen(TERMINAL, "wide", 40)
    assert live_screen_count() == 0
