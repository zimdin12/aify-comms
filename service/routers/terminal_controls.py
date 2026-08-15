"""Terminal CONTROLS: claim the pending ones, report what happened to one.

Extracted from `service/routers/terminals.py` in v0.5.4, with a closure measured before the move —
`api_core` and `service` leaves only, nothing local to that router. The same subject boundary as the
dispatch controls split earlier in this series, for the same reason: a control is a request made
ABOUT a terminal while it keeps running, with its own row and its own lifecycle.

`update_terminal_control` IS NOT A STATUS WRITE WITH EXTRA STEPS. Completing a control can change
the terminal's own status, resize the live screen buffer, and append output describing what
happened — three effects on the terminal, driven by the outcome of a request about it. That is why
this module imports the screen and output helpers a controls file would not obviously need.

Bodies and route decorators are byte-identical to what stood in `terminals.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

from service import longpoll
from service.api_core.events import _append_terminal_event
from service.api_core.records import _terminal_session_to_dict
from service.api_core.routing import domain_router
from service.api_core.terminal_control_status import _apply_terminal_status_from_control
from service.api_core.terminal_controls_io import (
    _claim_terminal_controls_once,
    _terminal_control_to_dict,
)
from service.api_core.terminal_output import _append_terminal_output
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
# ALIASED, and copying it without the alias is an import error rather than a silent one — the sweep
# caught exactly that here. The leaf calls it `resize_live_screen`; every caller in the router layer
# has always spelled it `_resize_live_terminal_screen`, and keeping that spelling is what makes the
# moved body byte-identical.
from service.terminal_snapshot import resize_live_screen as _resize_live_terminal_screen

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import TerminalControlClaim, TerminalControlUpdate

router = domain_router()



@router.post("/terminals/controls/claim")
async def claim_terminal_controls(req: TerminalControlClaim):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Fallback 1s matches
    # the legacy 800ms console-control poll so interactivity latency never regresses.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_terminal_controls_once(req),
        lambda r: r.get("controls") == [],
        scope="terminal-control",
        fallback_s=1.0,
        lock_result={"ok": True, "controls": []},
    )



@router.patch("/terminals/controls/{control_id}")
async def update_terminal_control(control_id: str, req: TerminalControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, f'Unsupported terminal control status "{req.status}"')
    db = await get_db()
    try:
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        if not control:
            raise HTTPException(404, f'Terminal control "{control_id}" not found')
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (control["terminal_id"],))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{control["terminal_id"]}" not found')
        now = _now()
        await db.execute(
            """
            UPDATE terminal_controls
            SET status = ?, handled_at = ?, error = ?
            WHERE id = ?
            """,
            (status, now, req.error or "", control_id),
        )
        # Persist the PTY root pid reported by the owning bridge (start-control
        # attach). Stored so Dashboard Stop/Restart can kill-by-pid even if the
        # owning bridge later dies and the PTY is orphaned. Only set on a real
        # positive value — never blank out an existing pid.
        report_pid = str(req.processId or "").strip()
        if report_pid:
            await db.execute(
                "UPDATE terminal_sessions SET process_id = ? WHERE id = ?",
                (report_pid, terminal["id"]),
            )
        terminal_status = await _apply_terminal_status_from_control(db, req, control, terminal, status, now)
        # A3 real-cols (2026-07-02): a COMPLETED resize control means the bridge actually
        # applied these dims to the PTY — record them as the terminal's authoritative size.
        # GET /terminals prefers this over the infer_source_width heuristic, so the console
        # snapshot renders at the PTY's true width (kills the live-redraw garble caused by
        # inferred≠actual width).
        if (
            status == "completed"
            and str(control["action"] or "").strip().lower() == "resize"
            and int(control["cols"] or 0) > 0
            and int(control["rows"] or 0) > 0
        ):
            await db.execute(
                "UPDATE terminal_sessions SET cols = ?, rows = ? WHERE id = ?",
                (int(control["cols"]), int(control["rows"]), terminal["id"]),
            )
            _resize_live_terminal_screen(terminal["id"], control["cols"], control["rows"])
        if req.output:
            latest_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
            await _append_terminal_output(db, latest_terminal or terminal, req.output, status=terminal_status)
        await _append_terminal_event(
            db,
            terminal["id"],
            f"terminal_control_{status}",
            json.dumps({"controlId": control_id, "action": control["action"], "error": req.error or ""}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        updated_terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_updated", {"terminalId": terminal["id"], "controlId": control_id, "status": status})
        return {"ok": True, "control": _terminal_control_to_dict(updated), "terminal": _terminal_session_to_dict(updated_terminal)}
    finally:
        await db.close()
