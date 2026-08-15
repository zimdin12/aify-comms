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

from service.terminal_snapshot import infer_source_width as _infer_terminal_source_width
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
                eff_cols = max(20, min(max(int(cols), int(src_w or 0)), 500))
                eff_rows = max(5, min(int(rows), 200))
                term_dict["snapshot"] = await loop.run_in_executor(
                    None, _render_terminal_snapshot, raw, eff_cols, eff_rows
                )
                term_dict["renderedCols"] = eff_cols
                term_dict["renderedRows"] = eff_rows
            except Exception:
                pass
