"""The pre-split `get_terminal`, frozen.

Not imported by anything. It is the ONE true original that
`test_get_terminal_split_is_inert.py` inlines every extraction back against.

Captured from `git show HEAD:service/routers/terminals.py` at the commit before the extraction,
decoded as utf-8 rather than through the locale codec.
"""


async def get_terminal(terminal_id: str, cols: Optional[int] = None, rows: Optional[int] = None):
    await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal_id)
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        events = await (await db.execute(
            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id ASC LIMIT 200",
            (terminal_id,),
        )).fetchall()
        term_dict = _terminal_session_to_dict(terminal)
        # The agent's ROLE travels with the terminal, so a launch never depends on a second call.
        #
        # Found reviewing my own AIFY_AGENT_ROLE fix: the bridge reads the role from
        # `GET /agents/{id}` and falls back to `{}` on ANY failure (server.js, the terminal-start
        # control). A transient 503 or lock there silently reinstates the exact bug the fix closed —
        # the child defaults to "coder" and its self-register overwrites the spawn's role. The
        # fallback I wrote in terminal-env.js (`terminal.role`) was DEAD CODE, because this payload
        # never carried one.
        #
        # One indexed lookup on a control path that already does several, and it makes the fallback
        # real: the role now arrives with the terminal the bridge is already fetching.
        try:
            if terminal["agent_id"]:
                agent_row = await (await db.execute(
                    "SELECT role FROM agents WHERE id = ?", (terminal["agent_id"],)
                )).fetchone()
                term_dict["role"] = str((agent_row["role"] if agent_row else "") or "")
            else:
                term_dict["role"] = ""
        except Exception:
            # Never fail a terminal fetch over an advisory field.
            term_dict["role"] = ""
        # Clean replay (2026-06-30): when the viewer passes its grid size, render the raw
        # byte log through a headless VT emulator sized to that grid and return a clean
        # current-screen snapshot. Replaying THIS (instead of the raw log) into a fresh
        # xterm fixes the full-screen-TUI scramble in BOTH dashboards. One-shot per attach,
        # offloaded to a thread so the parse never blocks the event loop; falls back to the
        # raw output on any error / when pyte is absent. See service/terminal_snapshot.py.
        # LIVE SCREEN FIRST (2026-07-14). If we have been feeding this terminal's screen, IT is
        # the truth — render it directly. Replaying the stored log (below) cannot reconstruct a
        # differential painter's screen from a 64KB tail, which is the scrambled/half-missing
        # console. The live screen is rendered at the PTY's OWN geometry; the client already
        # widens its xterm to `renderedCols` (applyRenderedWidth), so a wide mirror still fits.
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
        return {
            "ok": True,
            "terminal": term_dict,
            "events": [_terminal_event_to_dict(row) for row in events],
        }
    finally:
        await db.close()
