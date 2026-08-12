"""The `terminals` route domain: PTY output, input, resize, stop, death reports and controls.

v0.5.2j. Eight handlers, four domain-local helpers, and only TWO borrows — the cleanest domain since
usage, which is worth noting for a subsystem this operationally central. The terminal surface turns
out to be genuinely self-contained once the console/session handlers moved.

`_clear_console_terminal_binding` moved here, and `service/reconcilers/terminal_consistency.py` was
borrowing it from the router — that shim is repointed at this module in the same tag, per the
completion rule. Leaving it would have made the call path reconciler -> api_v2 -> terminals, with the
router as a pointless middleman for code it no longer owns.

BORROW TABLE:

    _terminal_control_to_dict          retires with: the remaining router console helpers
    _terminal_session_to_dict          retires with: agents

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

import asyncio

from service import longpoll
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _iso_from_ms, _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.terminal_runs import _close_active_terminal_runs_for_terminal
from service.terminal_snapshot import (
    TERMINAL_MAX_COLS,
    TERMINAL_MAX_ROWS,
    feed_live_screen as _feed_live_terminal_screen,
    infer_source_width as _infer_terminal_source_width,
    render_live_screen as _render_live_terminal_screen,
    render_snapshot as _render_terminal_snapshot,
    resize_live_screen as _resize_live_terminal_screen,
)
# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not
# fail import, it silently demotes the request body to a query parameter.
from service.models import (
    TerminalControlClaim,
    TerminalControlRequest,
    TerminalControlUpdate,
    TerminalDeadReport,
    TerminalOutputRequest,
)
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

logger = logging.getLogger("aify_comms.routers.terminals")

router = domain_router()



def _terminal_status_transition(*a, **k):
    """BORROWED: still used by router-owned console code."""
    from service.routers.api_v2 import _terminal_status_transition as _impl

    return _impl(*a, **k)



def _trim_terminal_output(text: str, max_chars: int = 65536) -> str:
    value = str(text or "")
    if len(value) <= max_chars:
        return value
    tail = value[-max_chars:]
    # Start the kept tail at a clean LINE boundary (2026-06-07). A raw char-count slice
    # routinely cuts mid-line or — worse — mid-ANSI-escape-sequence, so when the dashboard
    # seeds a FRESH xterm with this buffer the leading bytes are a broken escape that xterm
    # misparses into on-screen garbage (part of the "glitchy console" report). Dropping at
    # most the first partial line makes the seed parse cleanly. If the whole window is one
    # huge line (no newline), fall back to the raw tail rather than return empty.
    newline = tail.find("\n")
    if 0 <= newline < len(tail) - 1:
        return tail[newline + 1:]
    return tail



def _borrowed_terminal_output_writes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import TERMINAL_OUTPUT_WRITES

    return TERMINAL_OUTPUT_WRITES



def _borrowed_virtual_rpc_command_set():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import VIRTUAL_RPC_COMMAND_SET

    return VIRTUAL_RPC_COMMAND_SET



def _borrowed_terminal_end_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.routers.api_v2 import _TERMINAL_END_STATUSES

    return _TERMINAL_END_STATUSES



def _terminal_control_to_dict(*a, **k):
    from service.routers.api_v2 import _terminal_control_to_dict as _impl

    return _impl(*a, **k)


def _terminal_session_to_dict(*a, **k):
    from service.routers.api_v2 import _terminal_session_to_dict as _impl

    return _impl(*a, **k)


def _terminal_event_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "terminalId": row["terminal_id"],
        "eventType": row["event_type"],
        "body": row["body"] or "",
        "createdAt": row["created_at"] or "",
    }


async def _append_terminal_output(db, terminal, output: str, *, status: str = "", seq: Optional[int] = None):
    chunk = str(output or "")
    if not chunk and not status:
        return
    current = terminal["output"] if "output" in terminal.keys() else ""
    next_output = _trim_terminal_output(f"{current or ''}{chunk}")

    # LIVE SCREEN (2026-07-14). Feed this chunk into the terminal's persistent screen, the way a
    # real terminal consumes bytes. The console renders from THAT, not from a replay of the
    # stored log — because the stored log is a 64KB TAIL and claude never clears the screen (zero
    # ESC[2J fleet-wide; it paints one line per frame), so replaying a suffix into a blank screen
    # can only ever rebuild part of it. That is the scrambled / half-empty / "old state stuck on
    # screen" console, and why Refresh did not help: it re-rendered the same broken buffer.
    # Best-effort and non-fatal: on any failure the screen is dropped and the reader falls back to
    # the replay path, i.e. exactly today's behaviour. Never worse.
    if chunk:
        try:
            keys = terminal.keys()
            _feed_live_terminal_screen(
                str(terminal["id"]),
                chunk,
                cols=(terminal["cols"] if "cols" in keys else 0),
                rows=(terminal["rows"] if "rows" in keys else 0),
                seed=str(current or ""),  # only used when the screen does not exist yet
            )
        except Exception:
            logger.debug("live screen feed failed for terminal=%s", terminal["id"], exc_info=True)
    updates = ["output = ?", "updated_at = ?"]
    params: list[Any] = [next_output, _now()]
    if seq is not None:
        updates.append("output_seq = ?")
        params.append(int(seq))
    next_status = _terminal_status_transition(terminal["status"] if "status" in terminal.keys() else "", status)
    if next_status:
        updates.append("status = ?")
        params.append(next_status)
        if next_status in {"stopped", "failed"}:
            updates.append("stopped_at = COALESCE(stopped_at, ?)")
            params.append(_now())
    params.append(terminal["id"])
    await db.execute(
        f"UPDATE terminal_sessions SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    if chunk:
        await _append_terminal_event(db, terminal["id"], "terminal_output", chunk[-2000:])


async def _claim_terminal_controls_once(req: TerminalControlClaim):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        now = _now()
        cursor = await db.execute(
            """
            SELECT *
            FROM terminal_controls
            WHERE environment_id = ?
              AND COALESCE(bridge_id, '') = ?
              AND status = 'pending'
            ORDER BY requested_at ASC, id ASC
            LIMIT 20
            """,
            (req.environmentId, req.bridgeId),
        )
        controls = await cursor.fetchall()
        if controls:
            ids = [row["id"] for row in controls]
            await db.executemany(
                "UPDATE terminal_controls SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'pending'",
                [(now, control_id) for control_id in ids],
            )
            await db.commit()
            refreshed = []
            for control_id in ids:
                row = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
                if row:
                    refreshed.append(row)
            controls = refreshed
        # Attach the target terminal's stored PTY root pid so a claiming bridge
        # can kill-by-pid when its in-memory terminals Map misses (orphaned PTY,
        # owning bridge gone). The claim is already env+bridge scoped, so the pid
        # only ever reaches the bridge for terminals on its own machine.
        out = []
        for row in controls:
            term_row = await (await db.execute(
                "SELECT process_id, agent_id, runtime FROM terminal_sessions WHERE id = ?",
                (row["terminal_id"],),
            )).fetchone()
            pid = str((term_row["process_id"] if term_row else "") or "")
            agent_id = str((term_row["agent_id"] if term_row else "") or "")
            runtime = str((term_row["runtime"] if term_row else "") or "")
            # Surface the target agent's session_mode so a claiming bridge can
            # decide whether a `stop` control needs a MANAGED-HERMES triad teardown
            # (fix/hermes-leak P2). Best-effort; resident/unknown → "".
            session_mode = ""
            if agent_id:
                agent_row = await (await db.execute(
                    "SELECT session_mode FROM agents WHERE id = ?",
                    (agent_id,),
                )).fetchone()
                session_mode = _normalize_session_mode((agent_row["session_mode"] if agent_row else "") or "")
            out.append(_terminal_control_to_dict(
                row, pid=pid, agent_id=agent_id, runtime=runtime, session_mode=session_mode,
            ))
        return {"ok": True, "controls": out}
    finally:
        await db.close()


async def _clear_console_terminal_binding(db, agent_id: str, terminal_id: str, *, now: Optional[str] = None) -> None:
    agent_id = str(agent_id or "").strip()
    terminal_id = str(terminal_id or "").strip()
    if not agent_id or not terminal_id:
        return
    row = await (await db.execute("SELECT runtime_state, status_note FROM agents WHERE id = ?", (agent_id,))).fetchone()
    if not row:
        return
    runtime_state = _json_loads_or(row["runtime_state"], {})
    if not isinstance(runtime_state, dict):
        return
    cleared = False
    console_terminal = runtime_state.get("consoleTerminal")
    if isinstance(console_terminal, dict) and str(console_terminal.get("terminalId") or "").strip() == terminal_id:
        runtime_state.pop("consoleTerminal", None)
        cleared = True
    # The dashboard-start path writes the TOP-LEVEL runtime_state.terminalId (and the synth
    # path virtualTerminalId); leaving either pointing at a dead terminal made the dashboard
    # auto-mount an xterm over a stopped PTY's stale buffer with no Start affordance
    # (graph-tech-lead incident, 2026-07-02).
    for key in ("terminalId", "virtualTerminalId"):
        if str(runtime_state.get(key) or "").strip() == terminal_id:
            runtime_state.pop(key, None)
            cleared = True
    if not cleared:
        return
    status_note = str(row["status_note"] or "").strip()
    if status_note == "Dashboard Console PTY attached.":
        status_note = ""
    await db.execute(
        """
        UPDATE agents
        SET runtime_state = ?,
            status_note = ?,
            last_seen = ?
        WHERE id = ?
        """,
        (json.dumps(runtime_state), status_note, now or _now(), agent_id),
    )
    await _invalidate_agent_live_state(db, agent_id)


@router.get("/terminals/{terminal_id}")
async def get_terminal(terminal_id: str, cols: Optional[int] = None, rows: Optional[int] = None):
    await _borrowed_terminal_output_writes().flush_terminal(terminal_id)
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


@router.post("/terminals/{terminal_id}/output")
async def append_terminal_output(terminal_id: str, req: TerminalOutputRequest, request: Request):
    db = await get_db()
    try:
        # Deliberately omit the (up to 64KB) `output` blob: this is the
        # high-frequency ingest path and never needs the existing buffer. The
        # queue flush re-reads only what it concatenates.
        terminal = await (await db.execute(
            """
            SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                   workspace, command, output_seq, status, requested_by,
                   created_at, updated_at, stopped_at, error
            FROM terminal_sessions WHERE id = ?
            """,
            (terminal_id,),
        )).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        # Bridge-ownership check: for REAL PTY terminals (a node-pty process
        # spawned by one bridge), a mismatched bridge_id MUST 409 — only the
        # owning bridge can write to its PTY. But synthesized virtual rpc
        # terminals (pi/hermes/codex/opencode) are just frame buffers with no
        # underlying owned process; sequential bridges that take over an
        # agent (e.g., aify-comms restarted between dispatches) need to
        # write to the SAME terminal_session row so the operator's Console
        # view stays continuous. Operator-reported 2026-05-22:
        # graph-tester-pi's synth terminal stopped updating at the
        # timestamp of the bridge that originally created it — every later
        # dispatch was rejected with 409.
        new_bridge_id = str(req.bridgeId or "").strip()
        existing_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_command = str(terminal["command"] or "")
        is_virtual_rpc = terminal_command in _borrowed_virtual_rpc_command_set()
        if new_bridge_id and existing_bridge_id and new_bridge_id != existing_bridge_id:
            if is_virtual_rpc:
                # Transfer ownership of the synth terminal to the new bridge.
                # Audit so operators see the takeover in the event log.
                #
                # Revive if previously stopped — the bridge-supersession
                # cleanup (`_stop_virtual_terminals_for_superseded_bridges`)
                # can race against an in-flight dispatch on the new bridge:
                # supersession stops the row, then the new bridge's
                # /output POST arrives. Operator-reported 2026-05-22:
                # codex synth terminal showed "started then stopped" yet
                # the agent still replied — frames were accumulating
                # in terminal_events while the row was stale-stopped,
                # leaving the dashboard rendering "terminal is not
                # running" despite a healthy stream of frames. The
                # arriving POST is hard proof the new bridge is
                # actively writing, so undo the stale stop.
                current_status = str(terminal["status"] or "").strip().lower()
                if current_status == "stopped":
                    await db.execute(
                        """
                        UPDATE terminal_sessions
                        SET bridge_id = ?, status = 'running', stopped_at = NULL, error = ''
                        WHERE id = ?
                        """,
                        (new_bridge_id, terminal_id),
                    )
                else:
                    await db.execute(
                        "UPDATE terminal_sessions SET bridge_id = ? WHERE id = ?",
                        (new_bridge_id, terminal_id),
                    )
                await _append_terminal_event(
                    db,
                    terminal_id,
                    "virtual_rpc_bridge_takeover",
                    json.dumps({
                        "from": existing_bridge_id,
                        "to": new_bridge_id,
                        "revived": current_status == "stopped",
                    }),
                )
                # Commit immediately — the endpoint's only other commit
                # is inside the _borrowed_terminal_end_statuses() branch, which
                # doesn't fire for normal "running" output POSTs. Without
                # this, the bridge_id transfer + revive would silently
                # be lost on the next connection (failing the takeover
                # contract for any subsequent reader).
                await db.commit()
            else:
                raise HTTPException(409, "Terminal is owned by a different bridge")
        status = str(req.status or "").strip()
        next_seq = await _borrowed_terminal_output_writes().enqueue(
            terminal_id,
            req.output or "",
            status=status,
            base_seq=int(terminal["output_seq"] or 0),
            autoschedule=not bool(getattr(request.app.state, "testing", False)),
        )
        if status in _borrowed_terminal_end_statuses():
            now = _now()
            summary = f"Terminal {status} before an explicit reply was recorded."
            await _close_active_terminal_runs_for_terminal(db, terminal, status, now=now, reason=summary)
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?,
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?)
                WHERE id = ?
                """,
                (status, now, now, terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = 'managed',
                    last_seen = ?
                WHERE id = ?
                """,
                (status, now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            await db.commit()
        # Do NOT broadcast per-POST here: concurrent POSTs reorder vs seq and
        # the dashboard's seq-dedupe then drops frames (scrambled console).
        # Hand the ws manager to the write queue, which emits one ordered,
        # coalesced, post-commit broadcast per flush instead.
        ws = await _get_ws(request)
        if ws is not None:
            _borrowed_terminal_output_writes().ws_manager = ws
        # Ingest ack only — the response intentionally carries no output buffer
        # (clients read full output via GET /terminals/{id}). The sole caller
        # is the bridge, which uses outputSeq/status and ignores the rest.
        terminal_payload = _terminal_session_to_dict(terminal)
        terminal_payload["outputSeq"] = next_seq
        if status:
            terminal_payload["status"] = status
        return {"ok": True, "terminal": terminal_payload}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/input")
async def send_terminal_input(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="input",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(db, terminal_id, "terminal_input_requested", json.dumps({"requestedBy": requested_by, "controlId": control_id}))
        await db.commit()
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal_id, "action": "input"})
        return {"ok": True, "control": _terminal_control_to_dict(control)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/resize")
async def resize_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        # Clamp the resize to sane maxima before it is ever recorded or forwarded to the bridge
        # (Hermes parity). An absurd winsize crashes node-pty's TIOCSWINSZ ioctl (their WSL2
        # `columns=131072` incident); clamping at the service means a bad value can never reach any
        # bridge, even one running older code. 0 stays 0 (the bridge substitutes its own default).
        _cols = int(req.cols or 0)
        _rows = int(req.rows or 0)
        # Clamp to what the RENDERER can represent (C1). These used to be 2000x1000 while the live
        # screen clamped to 500x200, so a wider console got a snapshot at the wrong width — the
        # garbling the snapshot exists to prevent. One max, owned by terminal_snapshot.
        _cols = 0 if _cols <= 0 else min(_cols, TERMINAL_MAX_COLS)
        _rows = 0 if _rows <= 0 else min(_rows, TERMINAL_MAX_ROWS)
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="resize",
            requested_by=requested_by,
            cols=_cols,
            rows=_rows,
        )
        await _append_terminal_event(db, terminal_id, "terminal_resize_requested", json.dumps({"requestedBy": requested_by, "cols": _cols, "rows": _rows}))
        await db.commit()
        control = await (await db.execute("SELECT * FROM terminal_controls WHERE id = ?", (control_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal_id, "action": "resize"})
        return {"ok": True, "control": _terminal_control_to_dict(control)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/stop")
async def stop_terminal(terminal_id: str, req: TerminalControlRequest, request: Request):
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (terminal["environment_id"],))).fetchone()
        settings = await _load_settings(db)
        env_status = _environment_effective_status(
            env_row,
            offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
        ) if env_row else "offline"
        current_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
        terminal_bridge_id = str(terminal["bridge_id"] or "").strip()
        terminal_status = str(terminal["status"] or "").strip().lower()
        bridge_can_claim = bool(
            terminal_bridge_id
            and current_bridge_id
            and terminal_bridge_id == current_bridge_id
            and env_status in {"online", "degraded"}
        )
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal_id,
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=req.body or "",
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "console_stop_requested",
            json.dumps({"requestedBy": requested_by, "body": req.body or "", "controlId": control_id}),
        )
        if terminal_status in {"stopped", "failed"} or not bridge_can_claim:
            reason = "Terminal bridge is no longer current; stop reconciled in control plane."
            await db.execute(
                """
                UPDATE terminal_controls
                SET status = 'completed',
                    claimed_at = COALESCE(claimed_at, ?),
                    handled_at = ?
                WHERE id = ?
                """,
                (now, now, control_id),
            )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = 'stopped',
                    updated_at = ?,
                    stopped_at = COALESCE(stopped_at, ?),
                    error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
                WHERE id = ?
                """,
                (now, now, reason if terminal_status not in {"stopped", "failed"} else "", terminal_id),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    terminal_status = 'stopped',
                    last_seen = ?
                WHERE id = ?
                """,
                (now, terminal["session_id"]),
            )
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
            # _clear_console_terminal_binding only invalidates when the agent's
            # consoleTerminal pointer matches (no-ops for virtual/RPC terminals,
            # whose pointer is virtualTerminalId). Invalidate explicitly here —
            # mirroring the sibling bridge-reported completion path — so the
            # reconciled stop drops the agent out of `online`/`working`
            # immediately rather than lying until the 60s sweep.
            await _invalidate_agent_live_state(db, terminal["agent_id"])
            await _append_terminal_event(
                db,
                terminal_id,
                "console_stop_reconciled",
                json.dumps({
                    "requestedBy": requested_by,
                    "reason": reason,
                    "terminalBridge": terminal_bridge_id,
                    "environmentBridge": current_bridge_id,
                    "environmentStatus": env_status,
                }),
            )
            await db.commit()
            updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
            return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopping', updated_at = ?
            WHERE id = ?
            """,
            (now, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET terminal_status = 'stopping',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated)}
    finally:
        await db.close()


@router.post("/terminals/{terminal_id}/report-dead")
async def report_terminal_dead(terminal_id: str, req: TerminalDeadReport, request: Request):
    """Host-reported dead-PTY signal (WS4 Task 4.2).

    The server cannot probe a remote host's PID; only the OWNING environment
    bridge can. When a bridge observes that one of its `attached` console PTY
    rows has a `process_id` that is no longer alive locally, it POSTs here so the
    server can mark the row stopped, close any active runs, clear the console
    binding, and invalidate the agent's live state (a frozen/crashed console
    can otherwise keep manufacturing presence).

    SAFETY: if a `processId` is supplied it MUST match the stored process_id.
    A bridge that has since restarted the console owns a NEW pid; a stale
    report carrying the OLD pid must NOT stop the live row. Already-terminal
    rows are a harmless idempotent no-op.
    """
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        now = _now()
        current_status = str(terminal["status"] or "").strip().lower()
        # Idempotent: already terminal → nothing to do.
        if current_status in _borrowed_terminal_end_statuses():
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False}
        # PID guard: a supplied pid must match the stored process_id so a stale
        # report can't stop a row a restarted bridge now owns with a NEW pid.
        reported_pid = str(req.processId or "").strip()
        stored_pid = str(terminal["process_id"] or "").strip()
        if reported_pid and stored_pid and reported_pid != stored_pid:
            await _append_terminal_event(
                db,
                terminal_id,
                "console_dead_report_ignored",
                json.dumps({"reportedPid": reported_pid, "storedPid": stored_pid, "bridgeId": req.bridgeId or ""}),
            )
            await db.commit()
            return {"ok": True, "terminal": _terminal_session_to_dict(terminal), "changed": False, "ignored": "pid-mismatch"}
        reason = str(req.reason or "").strip() or "Console PTY process is no longer alive (host-reported)."
        # Close any active runs bound to this terminal before stopping the row.
        await _close_active_terminal_runs_for_terminal(
            db,
            terminal,
            "stopped",
            now=now,
            reason=reason,
        )
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET owner_mode = 'managed',
                terminal_status = 'stopped',
                last_seen = ?
            WHERE id = ?
            """,
            (now, terminal["session_id"]),
        )
        await _clear_console_terminal_binding(db, terminal["agent_id"], terminal_id, now=now)
        # Bug D fix (2026-07-02): the dead PTY's in-session wrapper-child bridge died with it,
        # but its heartbeat row would otherwise look "live" for up to
        # ACTIVE_RUN_BRIDGE_STALE_SECONDS and suppress the send-path coldstart. Supersede the
        # rows now so the very next send cold-starts a fresh worker instead of queuing.
        # SCOPED to this terminal (review 2026-07-02): a stale dead-report for an OLD
        # terminal must not kill the NEW live worker's row. Rows with no terminal_id
        # (flag-only wrapper children) are still covered — nothing else supersedes them
        # at death, and a false-positive there only costs one redundant coldstart check.
        await db.execute(
            """
            UPDATE bridge_instances
            SET superseded_by = 'terminal-dead:' || ?,
                superseded_at = ?
            WHERE agent_id = ?
              AND bridge_kind = 'managed-wrapper-child'
              AND COALESCE(superseded_by, '') = ''
              AND (COALESCE(terminal_id, '') = '' OR terminal_id = ?)
            """,
            (terminal_id, now, terminal["agent_id"], terminal_id),
        )
        # Phantom-pending fix (review 2026-07-02): a `running` spawn_request is the
        # terminal SUCCESS state and its timestamps freeze at boot, so for 5 minutes
        # after boot _has_pending_or_booting_spawn_request would treat this now-dead
        # worker as "mid-boot" and suppress the very respawn its death requires (and
        # burn the backstop's one-shot rescue on a phantom). Mark the death terminal.
        # SCOPED to THIS terminal's session (review 2026-07-03): an unscoped agent-wide
        # stamp would also finish a NEW live worker's still-booting spawn (bound to a
        # different session) — a stale dead-report for an OLD terminal would then trigger a
        # DUPLICATE spawn whose registration supersedes and fails the live worker, exactly
        # what the terminal-scoped bridge supersede above exists to prevent. Set status too
        # so the row reaches a terminal state (else it lingers `running`+finished forever,
        # skipped by _fail_orphaned_running_spawn_requests). Empty-session_id arm covers
        # legacy rows written before session binding.
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                finished_at = ?,
                error = COALESCE(NULLIF(error, ''), 'Worker terminal died (report-dead); spawn finalized.'),
                updated_at = ?
            WHERE agent_id = ?
              AND status = 'running'
              AND COALESCE(finished_at, '') = ''
              AND (COALESCE(session_id, '') = '' OR session_id = ?)
            """,
            (now, now, terminal["agent_id"], terminal["session_id"]),
        )
        await _invalidate_agent_live_state(db, terminal["agent_id"])
        await _append_terminal_event(
            db,
            terminal_id,
            "console_dead_reported",
            json.dumps({"reportedPid": reported_pid, "bridgeId": req.bridgeId or "", "reason": reason}),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_stopped", {"terminalId": terminal_id, "sessionId": terminal["session_id"]})
        return {"ok": True, "terminal": _terminal_session_to_dict(updated), "changed": True}
    finally:
        await db.close()


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
        terminal_status = str(req.terminalStatus or "").strip()
        if status == "failed":
            terminal_status = terminal_status or "failed"
        if control["action"] == "stop" and status == "completed":
            terminal_status = terminal_status or "stopped"
        if terminal_status:
            terminal_status_norm = terminal_status.strip().lower()
            if terminal_status_norm in _borrowed_terminal_end_statuses():
                await _close_active_terminal_runs_for_terminal(
                    db,
                    terminal,
                    terminal_status_norm,
                    now=now,
                    reason=f"Terminal {terminal_status_norm} before an explicit reply was recorded.",
                )
            await db.execute(
                """
                UPDATE terminal_sessions
                SET status = ?, updated_at = ?, stopped_at = CASE WHEN ? IN ('stopped','failed') THEN COALESCE(stopped_at, ?) ELSE stopped_at END,
                    error = CASE WHEN ? = 'failed' THEN ? ELSE error END
                WHERE id = ?
                """,
                (terminal_status, now, terminal_status, now, status, req.error or "", terminal["id"]),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET terminal_status = ?,
                    owner_mode = CASE WHEN ? IN ('stopped','failed') THEN 'managed' ELSE owner_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (terminal_status, terminal_status, now, terminal["session_id"]),
            )
        if terminal_status in {"stopped", "failed"}:
            await _clear_console_terminal_binding(db, terminal["agent_id"], terminal["id"], now=now)
        if terminal_status.strip().lower() in _borrowed_terminal_end_statuses():
            await _invalidate_agent_live_state(db, terminal["agent_id"])
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
