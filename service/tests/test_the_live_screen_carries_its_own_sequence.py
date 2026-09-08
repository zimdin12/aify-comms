"""The number that describes a live screen lives ON the live screen, and what happens when it cannot.

WHY IT MOVED THERE, 2026-09-08. The terminal GET renders the live screen and serves a sequence beside
it, and until this it took that sequence from the TAIL BUFFER. The two have different lifetimes: an
appending write on a terminal that is STOPPING feeds the screen and then `forget()`s the buffer, so
the reader fell through to a stale serialised number while the screen carried newer bytes. Review
reproduced it against real HTTP with `status=stopped`: served sequence 1 with the new generation
already drawn, then a quiescent GET returning sequence 2 with the identical snapshot.

A picture and a number that describe each other have to share a lifetime. This one does.

NONE IS UNKNOWN, NOT ZERO, and that is the case with the sharpest consequence. `feed_live_screen` is
also reached by `append_outside_the_queue`, which numbers nothing -- so a screen can move past any
number anybody gave it. Keeping the old number there would be the same tear through a third door;
answering 0 would tell a browser it holds nothing and start a repaint of everything. The screen says
it does not know, and the reader keeps whatever it was serialised with, which is what it did before
any of this existed.

`_LIVE_SCREENS` is a process-global, so this file restores it around every test.
"""
from __future__ import annotations

import pytest

from service import terminal_snapshot
from service.terminal_snapshot import feed_live_screen, live_screen_seq, render_live_screen

TERMINAL = "term-live-seq"
ESC = chr(27)
PAINT = ESC + "[2J" + ESC + "[H"


@pytest.fixture(autouse=True)
def restore_the_live_screens():
    saved = dict(terminal_snapshot._LIVE_SCREENS)
    terminal_snapshot._LIVE_SCREENS.clear()
    try:
        yield
    finally:
        terminal_snapshot._LIVE_SCREENS.clear()
        terminal_snapshot._LIVE_SCREENS.update(saved)


def test_an_untracked_terminal_answers_UNKNOWN_rather_than_zero():
    assert live_screen_seq(TERMINAL) is None


def test_a_numbered_chunk_is_the_number_the_screen_reports():
    assert feed_live_screen(TERMINAL, PAINT + "hello", cols=80, rows=24, seq=7) is True
    assert live_screen_seq(TERMINAL) == 7


def test_the_number_advances_with_the_screen():
    feed_live_screen(TERMINAL, PAINT + "one", cols=80, rows=24, seq=7)
    feed_live_screen(TERMINAL, ESC + "[2;1Htwo", cols=80, rows=24, seq=8)
    assert live_screen_seq(TERMINAL) == 8


def test_an_UNNUMBERED_chunk_clears_the_number_rather_than_leaving_a_stale_one():
    """The survivor that made this file exist.

    `append_outside_the_queue` feeds the screen with no sequence. Leaving 7 standing would serve a
    number for a screen that has moved past it -- the same pair defect the move to this module was
    supposed to close, arriving through the one caller that does not number its writes.
    """
    feed_live_screen(TERMINAL, PAINT + "numbered", cols=80, rows=24, seq=7)
    assert live_screen_seq(TERMINAL) == 7
    feed_live_screen(TERMINAL, ESC + "[3;1Hunnumbered", cols=80, rows=24)
    assert live_screen_seq(TERMINAL) is None, (
        "an unnumbered chunk left the previous number describing a screen it no longer covers")


def test_an_UNNUMBERED_chunk_is_always_fed_even_after_a_numbered_one():
    """There is no basis for calling an unnumbered chunk a repeat, and dropping output on a guess is
    worse than painting it twice. `append_outside_the_queue` numbers nothing."""
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=5)
    before = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "B", cols=80, rows=24)
    assert render_live_screen(TERMINAL)[0] != before, "an unnumbered chunk was refused as a repeat"


def test_THE_SCREEN_APPLIES_EVERY_CHUNK_IT_IS_FED():
    """A terminal handed bytes applies them. Recognising a retry is the WRITER'S job.

    This is here because a guard once lived in `feed_live_screen` that refused a chunk carrying a
    sequence that did not advance -- and it turned every non-advancing number into SILENTLY DROPPED
    output, which is a worse failure than the double paint it was closing. It also could not see the
    case review actually found: `_requeue_front` prepends the failed chunk to a newer batch, so a
    retry of B arrives as BC and no comparison against the last chunk matches it.

    The rollback lives in `_append_terminal_output` now, where the speculative state is created.
    Without this test, putting the guard back breaks nothing and the loss returns silently.
    """
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=5)
    before = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "DIFFERENT", cols=80, rows=24, seq=5)
    assert render_live_screen(TERMINAL)[0] != before, (
        "a chunk was refused because its sequence did not advance, which is silent output loss")

    once = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "X", cols=80, rows=24, seq=6)
    twice_first = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "X", cols=80, rows=24, seq=7)
    assert twice_first != once and render_live_screen(TERMINAL)[0] != twice_first, (
        "identical bytes at a new sequence were swallowed; a repainting spinner would freeze")


def test_a_chunk_with_no_ESC_creates_no_screen_and_so_no_number():
    """POSITIVE CONTROL for the assertions above: they would all read the same if nothing here ever
    created a screen. A plain log deliberately gets none -- `feed_live_screen` says so -- and the
    sequence follows the screen rather than existing without one."""
    assert feed_live_screen(TERMINAL, "a plain log line", cols=80, rows=24, seq=7) is False
    assert live_screen_seq(TERMINAL) is None
    assert feed_live_screen(TERMINAL, PAINT + "painted", cols=80, rows=24, seq=7) is True
    assert live_screen_seq(TERMINAL) == 7


# THE RETRY TESTS THAT STOOD HERE ARE GONE, and where they went matters more than that they went.
#
# `feed_live_screen` briefly refused a chunk carrying the same sequence and the same bytes as the
# last one, to undo a double paint left by a failed write. Review broke it: `_requeue_front` PREPENDS
# the failed chunk to a newer pending batch, so a failed B comes back as BC and no comparison against
# the last chunk can recognise it. The rollback moved to `_append_terminal_output`, which is where
# the speculative state is created, and its tests live in
# `test_a_failed_tail_write_does_not_duplicate_the_chunk.py`.
#
# What this file still owns is the NUMBER: which output a screen has consumed, and what it says when
# nobody numbered it.
