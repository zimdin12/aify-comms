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


def test_a_RETRIED_chunk_is_not_painted_twice():
    """Review's pre-existing P2, closed by the number this file is about.

    `_append_terminal_output` feeds the screen and folds the chunk into the held tail BEFORE the
    UPDATE, and the write queue requeues the same chunk when that UPDATE throws. `restore()` puts
    the tail back and nothing puts the SCREEN back, so the retry gave a stored tail of AB beside a
    screen of ABB. For a TUI that is not a duplicated line, it is a cursor movement nobody asked for.
    """
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=1)
    feed_live_screen(TERMINAL, "B", cols=80, rows=24, seq=2)
    first = render_live_screen(TERMINAL)[0]

    feed_live_screen(TERMINAL, "B", cols=80, rows=24, seq=2)     # the requeued chunk
    assert render_live_screen(TERMINAL)[0] == first, (
        "the retried chunk was applied a second time; the screen and the stored tail now disagree")
    assert live_screen_seq(TERMINAL) == 2


def test_a_NEW_chunk_after_a_retry_still_paints():
    """NEGATIVE CONTROL for the guard above, and the failure it would hide is silence: a screen that
    refused everything would pass the test above perfectly."""
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=1)
    feed_live_screen(TERMINAL, "B", cols=80, rows=24, seq=2)
    feed_live_screen(TERMINAL, "B", cols=80, rows=24, seq=2)
    before = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "C", cols=80, rows=24, seq=3)
    assert render_live_screen(TERMINAL)[0] != before, "the guard swallowed a genuinely new chunk"
    assert live_screen_seq(TERMINAL) == 3


def test_the_SAME_number_with_DIFFERENT_bytes_is_still_fed():
    """The guard that closed the retry started as `seq <= live.seq`, and that was too wide.

    Any sequence that failed to advance became silently dropped output, which is a worse failure
    than the double paint it fixes -- and it fired at once: a terminal whose live screen outlived its
    database row saw the numbering restart and lost the frame. A guard that can swallow bytes has to
    be certain, and only an identical chunk at an identical number is.
    """
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=5)
    before = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "DIFFERENT", cols=80, rows=24, seq=5)
    assert render_live_screen(TERMINAL)[0] != before, (
        "different bytes at the same number were refused as a repeat, which is silent output loss")


def test_IDENTICAL_bytes_at_a_NEW_number_are_fed_again():
    """The other half of the guard, and the common case rather than the exotic one.

    An agent that repaints a spinner sends identical bytes over and over, each with its own
    sequence. Matching on the bytes alone would swallow every repeat after the first -- a console
    frozen on one frame while the agent works, which is the operator's complaint wearing the mask of
    a fix. A requeue is the same bytes at the SAME number; anything else is new output.
    """
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=1)
    feed_live_screen(TERMINAL, "X", cols=80, rows=24, seq=2)
    once = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "X", cols=80, rows=24, seq=3)
    assert render_live_screen(TERMINAL)[0] != once, (
        "identical bytes at a new sequence were swallowed; a repainting spinner would freeze")
    assert live_screen_seq(TERMINAL) == 3


def test_an_UNNUMBERED_chunk_is_always_fed_even_after_a_numbered_one():
    """There is no basis for calling an unnumbered chunk a repeat, and dropping output on a guess is
    worse than painting it twice. `append_outside_the_queue` numbers nothing."""
    feed_live_screen(TERMINAL, PAINT + "A", cols=80, rows=24, seq=5)
    before = render_live_screen(TERMINAL)[0]
    feed_live_screen(TERMINAL, "B", cols=80, rows=24)
    assert render_live_screen(TERMINAL)[0] != before, "an unnumbered chunk was refused as a repeat"


def test_a_chunk_with_no_ESC_creates_no_screen_and_so_no_number():
    """POSITIVE CONTROL for the assertions above: they would all read the same if nothing here ever
    created a screen. A plain log deliberately gets none -- `feed_live_screen` says so -- and the
    sequence follows the screen rather than existing without one."""
    assert feed_live_screen(TERMINAL, "a plain log line", cols=80, rows=24, seq=7) is False
    assert live_screen_seq(TERMINAL) is None
    assert feed_live_screen(TERMINAL, PAINT + "painted", cols=80, rows=24, seq=7) is True
    assert live_screen_seq(TERMINAL) == 7
