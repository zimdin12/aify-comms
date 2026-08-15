"""Choosing a width to draw a terminal at, tested by calling the chooser.

`_attach_terminal_snapshot` was inline in `get_terminal` until v0.5.4, so exercising it meant driving
`GET /terminals/{id}` with a live emulator behind it. It is now a leaf and these tests call it
directly, with the three width sources stubbed so the CHOICE can be observed rather than inferred
from rendered text.

THE RULE THESE PROTECT is "never render narrower than the source". A resident wrapper mirrors the
operator's own terminal, often far wider than the dashboard pane, and its native width is not stored.
Rendering at the pane's fit-width re-wrapped every line — the "gappy / bugged console" report. So the
render happens at the MAX of source and viewer width, and the client widens its xterm to
`renderedCols` so a wide mirror scrolls instead of re-wrapping.

The priority order matters as much as the maximum: a LIVE screen ends the question, the PTY's
RECORDED size beats the heuristic, and inference is the last resort because it guesses from drawn
cells and can mis-size a redraw.
"""

from __future__ import annotations

import unittest

import service.api_core.terminal_snapshot_view as view
from service.api_core.terminal_snapshot_view import _attach_terminal_snapshot

RAW = "some \x1b[1mrendered\x1b[0m output"


class TerminalSnapshotViewTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._live = view._render_live_terminal_screen
        self._infer = view._infer_terminal_source_width
        self._render = view._render_terminal_snapshot
        #: Every render call, as (raw, cols, rows). The WIDTH ARGUMENT is what these tests are
        #: about, so it is recorded rather than being inferred from the rendered string.
        self.rendered: list[tuple] = []
        view._render_terminal_snapshot = lambda raw, cols, rows: (
            self.rendered.append((raw, cols, rows)) or f"snapshot@{cols}x{rows}")
        view._render_live_terminal_screen = lambda tid: None
        view._infer_terminal_source_width = lambda raw: 0

    def tearDown(self):
        view._render_live_terminal_screen = self._live
        view._infer_terminal_source_width = self._infer
        view._render_terminal_snapshot = self._render

    async def _attach(self, term_dict, cols=80, rows=24):
        await _attach_terminal_snapshot(term_dict, cols, rows)
        return term_dict

    def _term(self, **over):
        base = {"id": "t1", "output": RAW, "cols": 0, "rows": 0}
        base.update(over)
        return base

    # ---- the live screen wins ----------------------------------------------

    async def test_a_live_screen_is_authoritative_and_ends_the_question(self):
        view._render_live_terminal_screen = lambda tid: ("LIVE", 200, 50)
        term = await self._attach(self._term(cols=100))
        self.assertEqual("LIVE", term["snapshot"])
        self.assertEqual(200, term["renderedCols"])
        self.assertEqual(50, term["renderedRows"])
        self.assertEqual([], self.rendered, "no re-render is needed when the emulator has the screen")

    async def test_a_live_screen_that_RAISES_falls_through_rather_than_failing_the_GET(self):
        """A snapshot is an enhancement to a GET, not the GET."""
        def _boom(tid):
            raise RuntimeError("emulator gone")

        view._render_live_terminal_screen = _boom
        term = await self._attach(self._term(cols=120))
        self.assertEqual(1, len(self.rendered), "it must fall back to rendering the stored output")
        self.assertIn("snapshot@", term["snapshot"])

    async def test_a_terminal_with_no_id_is_not_asked_for_a_live_screen(self):
        def _never(tid):
            raise AssertionError("must not be called without an id")

        view._render_live_terminal_screen = _never
        term = await self._attach(self._term(id="", cols=120))
        self.assertEqual(120, term["renderedCols"])

    # ---- the recorded size beats the heuristic -----------------------------

    async def test_the_PTYs_RECORDED_size_is_preferred_over_inference(self):
        def _never(raw):
            raise AssertionError("inference must not run when a real width was recorded")

        view._infer_terminal_source_width = _never
        term = await self._attach(self._term(cols=140), cols=80)
        self.assertEqual(140, term["renderedCols"])

    async def test_inference_is_used_only_for_rows_predating_real_cols_recording(self):
        view._infer_terminal_source_width = lambda raw: 132
        term = await self._attach(self._term(cols=0), cols=80)
        self.assertEqual(132, term["renderedCols"])

    # ---- never narrower than the source ------------------------------------

    async def test_a_WIDE_source_beats_a_narrow_viewer(self):
        """The defect this exists for: rendering a wide mirror at the pane width mangled every line."""
        term = await self._attach(self._term(cols=200), cols=80)
        self.assertEqual(200, term["renderedCols"])
        self.assertEqual(200, self.rendered[0][1], "the render must happen at the wide width")

    async def test_a_WIDE_viewer_beats_a_narrow_source(self):
        """A managed terminal is drawn at the size we set, so the viewer is the honest width."""
        term = await self._attach(self._term(cols=60), cols=120)
        self.assertEqual(120, term["renderedCols"])

    async def test_the_width_is_clamped_at_both_ends(self):
        for source, viewer, expected in ((1, 1, 20), (5000, 80, 500)):
            with self.subTest(source=source, viewer=viewer):
                self.rendered.clear()
                term = await self._attach(self._term(cols=source), cols=viewer)
                self.assertEqual(expected, term["renderedCols"])

    async def test_the_row_count_is_clamped_and_never_widened_by_the_source(self):
        """Rows are the viewer's business: a taller source would just scroll."""
        for viewer, expected in ((1, 5), (500, 200), (24, 24)):
            with self.subTest(viewer=viewer):
                self.rendered.clear()
                term = await self._attach(self._term(cols=100), rows=viewer)
                self.assertEqual(expected, term["renderedRows"])

    # ---- nothing to render -------------------------------------------------

    async def test_a_terminal_with_no_output_gets_no_snapshot(self):
        term = await self._attach(self._term(output=""))
        self.assertNotIn("snapshot", term)
        self.assertEqual([], self.rendered)

    async def test_a_request_with_no_viewer_size_gets_no_snapshot(self):
        term = await self._attach(self._term(), cols=0, rows=0)
        self.assertNotIn("snapshot", term)

    async def test_a_render_that_RAISES_leaves_the_terminal_usable(self):
        """Silent on purpose: the row and its raw output still come back."""
        def _boom(raw, cols, rows):
            raise RuntimeError("render failed")

        view._render_terminal_snapshot = _boom
        term = await self._attach(self._term(cols=100))
        self.assertNotIn("snapshot", term)
        self.assertEqual(RAW, term["output"], "the raw output must survive a failed render")


if __name__ == "__main__":
    unittest.main()
