"""Choosing a width to draw a terminal at, which is not the width of the pane looking at it.

Extracted from `get_terminal` in `service/routers/terminals.py` in v0.5.4;
`test_get_terminal_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column.

THREE SOURCES, IN PRIORITY ORDER. A LIVE screen from the running emulator is authoritative and ends
the question. Failing that, the PTY's recorded size is used, because a resize control that completed
wrote a real number. Only when neither exists is the width INFERRED from the drawn cells, which is a
heuristic and can mis-size a redraw.

NEVER RENDER NARROWER THAN THE SOURCE, which is the rule the defect came from. A resident wrapper
mirrors the operator's own terminal, often far wider than the dashboard pane, and its native width
is not stored. Rendering at the pane's fit-width re-wrapped every line -- the "gappy / bugged
console" report. The render happens at the MAX of source and viewer width and the client widens its
xterm to `renderedCols`, so a wide mirror scrolls instead of re-wrapping. A managed terminal is drawn
at the size we set it, so source and viewer agree and nothing changes for it.

IT MUTATES `term_dict` IN PLACE rather than returning a new one. That is not a style choice: the
caller serialises the same dict afterwards, and returning a copy would change the call site, which
the round trip cannot follow.

FAILURE IS SILENT ON PURPOSE. A terminal that cannot be rendered still returns its raw output and
its row; a snapshot is an enhancement to a GET, not the GET.
"""
from __future__ import annotations

import asyncio

from service.api_core.terminal_tail_buffer import current_seq
from service.terminal_snapshot import (
    TERMINAL_MAX_COLS,
    TERMINAL_MAX_ROWS,
    TERMINAL_MIN_COLS,
    TERMINAL_MIN_ROWS,
    infer_source_width as _infer_terminal_source_width,
)
from service.terminal_snapshot import render_live_screen as _render_live_terminal_screen
from service.terminal_snapshot import render_snapshot as _render_terminal_snapshot


async def _attach_terminal_snapshot(term_dict, cols, rows) -> None:
        """Fill in `snapshot`, `renderedCols` and `renderedRows` on the serialised terminal.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        live = None
        if term_dict.get("id"):
            try:
                live = _render_live_terminal_screen(str(term_dict["id"]))
            except Exception:
                live = None
        if live:
            snap, live_cols, live_rows = live
            term_dict["snapshot"] = snap
            term_dict["renderedCols"] = live_cols
            term_dict["renderedRows"] = live_rows
            # THE BYTES AND THE SEQUENCE COME FROM ONE GENERATION, and until 2026-09-08 they did
            # not. The whole-diff review constructed the tear: `_terminal_session_to_dict` reads
            # `outputSeq` from the live buffer, the caller then AWAITS the agent's role lookup, and
            # this render happens after it. Output appended during that await is IN the screen and
            # NOT in the number, so the response seeds a browser with a picture already containing
            # bytes it is about to be sent again -- and for a TUI a second write of the same bytes
            # is a cursor movement nobody asked for, which is the corruption class the sequence
            # exists to prevent. The next quiescent GET then returns the higher sequence with the
            # identical snapshot, which is how the tear hides.
            #
            # RE-READ HERE, NOT MOVED EARLIER. Moving the await only narrows the window. This is a
            # synchronous read taken with no await between it and the render above, and
            # `_append_terminal_output` feeds the screen and records the sequence with no await
            # between those either -- so on one event loop the pair cannot be split.
            term_dict["outputSeq"] = current_seq(str(term_dict["id"]), term_dict.get("outputSeq"))
        elif cols and rows and term_dict.get("output"):
            try:
                loop = asyncio.get_event_loop()
                raw = term_dict["output"]
                # Never render NARROWER than the source: a resident wrapper mirrors the
                # operator's real (often much wider) terminal, and its native width is not
                # stored. Rendering at the pane's fit-width wrapped/mangled every line
                # ("gappy / bugged console"). Infer the source width and render at the max
                # of it and the viewer width; the client widens its xterm to renderedCols so
                # the wide mirror scrolls instead of re-wrapping. Managed terminals are drawn
                # at the size we set, so inferred≈viewer and behaviour is unchanged.
                # A3 real-cols (2026-07-02): prefer the PTY's AUTHORITATIVE size (recorded
                # when a resize control completes) over the heuristic — inference guesses
                # from drawn cells and can mis-size a live redraw. Fall back to inference
                # for rows that predate real-cols recording (stored cols 0/NULL).
                stored_cols = int(term_dict.get("cols") or 0)
                if stored_cols > 0:
                    src_w = stored_cols
                else:
                    src_w = await loop.run_in_executor(None, _infer_terminal_source_width, raw)
                # THE BOUNDS COME FROM THE RENDERER, which is the binding constraint -- a pyte
                # screen is allocated cols*rows cells. Typed here as literals they were a fourth copy
                # of numbers the snapshot module already declares.
                eff_cols = max(TERMINAL_MIN_COLS, min(max(int(cols), int(src_w or 0)), TERMINAL_MAX_COLS))
                eff_rows = max(TERMINAL_MIN_ROWS, min(int(rows), TERMINAL_MAX_ROWS))
                term_dict["snapshot"] = await loop.run_in_executor(
                    None, _render_terminal_snapshot, raw, eff_cols, eff_rows
                )
                term_dict["renderedCols"] = eff_cols
                term_dict["renderedRows"] = eff_rows
            except Exception:
                pass
