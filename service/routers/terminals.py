"""The `terminals` route domain: PTY output, input, resize, stop, death reports and controls.

v0.5.2j. Eight handlers, four domain-local helpers, and only TWO borrows — the cleanest domain since
usage, which is worth noting for a subsystem this operationally central. The terminal surface turns
out to be genuinely self-contained once the console/session handlers moved.

`_clear_console_terminal_binding` moved here, and `service/reconcilers/terminal_consistency.py` was
borrowing it from the router — that shim is repointed at this module in the same tag, per the
completion rule. Leaving it would have made the call path reconciler -> api_v2 -> terminals, with the
router as a pointless middleman for code it no longer owns.

BORROW TABLE:

    _terminal_session_to_dict          retires with: agents

`_terminal_control_to_dict` left this table in v0.5.3: it is owned here now, because every caller
was already in this module.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

from fastapi import HTTPException, Request


from service.api_core.tuning import TERMINAL_EVENTS_KEPT_PER_TERMINAL
from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.terminal_snapshot_view import _attach_terminal_snapshot
from service.api_core.routing import domain_router
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.launch_env import managed_launch_env
from service.api_core.records import _terminal_session_to_dict
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.api_core.ws import _get_ws
from service.db import get_db
from service.terminal_snapshot import TERMINAL_MAX_COLS, TERMINAL_MAX_ROWS
# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not
# fail import, it silently demotes the request body to a query parameter.
from service.models import TerminalControlRequest, TerminalOutputRequest
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES, _TERMINAL_END_STATUSES
from service.api_core.terminal_output_settlement import (
    _close_out_terminal_on_end_status,
    _settle_bridge_takeover_for_output,
)
from service.api_core.terminal_controls_io import _terminal_control_to_dict

logger = logging.getLogger("aify_comms.routers.terminals")

router = domain_router()

# THE TERMINAL DOMAIN IS THREE FILES, COMPOSED HERE rather than in `api_v2.py`. Controls and the two
# ways a terminal ends left in v0.5.4; this module keeps the read and the output/input surface and
# includes the other two, so `api_v2.py` still sees ONE terminal router.
#
# Not converted to a package, deliberately. Thirteen provenance comments across `api_core/`,
# `control_plane.py` and the split fixtures say a helper "moved out of service/routers/terminals.py"
# — statements about what HAPPENED. Turning this module into a package of that name would make every
# one of them false, and rewriting history in comments to satisfy a path gate is the wrong trade.
from service.routers.terminal_controls import router as _terminal_controls_router
from service.routers.terminal_lifecycle import router as _terminal_lifecycle_router

router.include_router(_terminal_controls_router)
router.include_router(_terminal_lifecycle_router)


# _TERMINAL_MONOTONIC_STATUSES moved to service/api_core/terminal_status.py in v0.5.4, together
# with `_terminal_status_transition` and `_TERMINAL_ACTIVE_STATUSES`. This module owned it for one
# release on the grounds that its only reader lived here; the reader left, and a constant does not
# stay behind its reader.


# _terminal_status_transition moved to service/api_core/terminal_status.py in v0.5.4.



# _trim_terminal_output moved to service/api_core/terminal_output.py in v0.5.4.



















def _terminal_event_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "terminalId": row["terminal_id"],
        "eventType": row["event_type"],
        "body": row["body"] or "",
        "createdAt": row["created_at"] or "",
    }


# _append_terminal_output moved to service/api_core/terminal_output.py in v0.5.4.






#: The statuses that mean a terminal is supposed to have a process behind it right now.
#:
#: IMPORTED, NOT DECLARED. The first version spelled the five out under a new name, and two gates
#: caught it in the same run: `test_status_set_literal_twins_are_frozen` and
#: `test_no_unruled_constant_coincidences`, which reported it as one concept with two owners. It is
#: -- `_TERMINAL_ACTIVE_STATUSES` in `api_core/terminal_status.py` already had exactly this set and
#: four consumers, this module among them.
#:
#: Sorted for a stable placeholder order; the set itself is unordered.
LIVE_TERMINAL_STATUSES = tuple(sorted(_TERMINAL_ACTIVE_STATUSES))

#: Hard ceiling on one listing, whatever the caller asks. A reply nobody bounded is a reply that
#: works until the day it does not.
MAX_TERMINAL_LIST = 500


@router.get("/terminals")
async def list_terminals(
    status: str = "live",
    agentId: str = "",
    environmentId: str = "",
    limit: int = 200,
):
    """Which terminals exist, and which are supposed to be running.

    THIS DID NOT EXIST, and its absence is why an orphaned PTY is invisible. Measured 2026-08-28:
    the API could fetch ONE terminal by id and claim controls for them, and could not enumerate them
    at all -- so nothing, not the dashboard and not `aify-comms doctor`, could ask "which terminals
    are live?" and compare that against what is actually running on a host.

    The operator hit the consequence the same day: aify-env owned a live PTY for `ef-manager`
    (pid 155844) while all 80 most recent sessions read `stopped` and the dashboard showed nothing.
    They asked for exactly this -- "aify-env side running process visibility, to catch orphans like
    that" -- and the join needs both sides listable. `process_id` on these rows is the OS pid,
    measured: 99 of 103 rows hold a numeric pid, which is the key aify-env's own listing shares.

    OUTPUT IS EXCLUDED, deliberately. `terminal_sessions.output` is a replay buffer; including it
    would make a 200-row listing tens of megabytes and turn a cheap reconciliation read into the
    most expensive call in the API.
    """
    normalized = str(status or "live").strip().lower()
    # RANGE ONLY. FastAPI's own typing refuses a non-integer before this runs (422), which is the
    # right layer for it -- so the try/except that used to sit here was unreachable, and a branch that
    # cannot execute is a branch nobody can test.
    capped = min(MAX_TERMINAL_LIST, max(1, int(limit)))

    where = []
    params: list[Any] = []
    if normalized == "live":
        where.append(f"status IN ({', '.join('?' for _ in LIVE_TERMINAL_STATUSES)})")
        params.extend(LIVE_TERMINAL_STATUSES)
    elif normalized not in ("", "all", "any"):
        # An unrecognised status filters to nothing rather than being ignored. Ignoring it would
        # answer a question the caller did not ask, with a list that looks complete.
        where.append("status = ?")
        params.append(normalized)
    if str(agentId or "").strip():
        where.append("agent_id = ?")
        params.append(str(agentId).strip())
    if str(environmentId or "").strip():
        where.append("environment_id = ?")
        params.append(str(environmentId).strip())

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    db = await get_db()
    try:
        # ORDER BY is not decoration on a LIMIT: without it SQLite may return any N rows, so a
        # truncated listing would be an arbitrary sample presented as the most recent.
        rows = await (await db.execute(
            f"SELECT * FROM terminal_sessions{clause} ORDER BY updated_at DESC, rowid DESC LIMIT ?",
            (*params, capped + 1),
        )).fetchall()
    finally:
        await db.close()

    truncated = len(rows) > capped
    terminals = []
    for row in rows[:capped]:
        terminal = _terminal_session_to_dict(row)
        # The replay buffer is the whole reason this is not just the row.
        terminal.pop("output", None)
        terminals.append(terminal)
    # SAYS WHEN IT IS CLIPPED. A truncated list that does not admit it reads as "that is everything",
    # which is the failure mode a reconciliation check cannot survive: it would report the missing
    # rows as orphans.
    return {"ok": True, "terminals": terminals, "count": len(terminals), "truncated": truncated}


@router.get("/terminals/{terminal_id}/launch")
async def get_terminal_launch(terminal_id: str):
    """EVERYTHING A PROCESS HOST NEEDS TO RUN THIS TERMINAL, AND NOTHING ELSE.

    THE SEAM aify-env BECOMES THE PROCESS HOST THROUGH. Until now the only tier that could start a
    managed worker was the aify-comms environment bridge, because starting one meant composing the
    launch -- and that composition lived on the host. The bridge is being removed; the operator's
    reasoning is exact: aify-comms is a container service, so it cannot hold the agents or they
    would be in the container's environment rather than the host's.

    So the split is stated here. The service says WHAT to run: the program and its arguments (which
    it already composed -- `command` and `argv` have been on the terminal row since Phase 8) and the
    aify-owned variables the worker must be launched with. The host adds only what the service
    cannot know: its own base environment, and CODEX_HOME, which names a directory that has to be
    CREATED on the machine that will run the process.

    NO BASE ENVIRONMENT TRAVELS. A process environment on the wire carries whatever the sender
    happened to hold, including its secrets. `env` here is an OVERLAY, small by construction, and a
    test asserts it stays that way -- a large one means a base environment leaked in.

    404 rather than an empty launch for an unknown terminal: a host given a blank command would
    start nothing and report success, which is the silence this whole tier keeps being bitten by.
    """
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,),
        )).fetchone()
        if not row:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        terminal = _terminal_session_to_dict(row)

        agent: dict[str, Any] = {}
        agent_id = str(terminal.get("agentId") or "").strip()
        if agent_id:
            agent_row = await (await db.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,),
            )).fetchone()
            if agent_row:
                # THE FIVE FIELDS THE LAUNCH NEEDS, projected explicitly rather than through
                # `_agent_record_to_dict`. That serialiser needs a computed status, an unread count
                # and a dispatch state -- none of which a launch depends on, and asking for them
                # here would make starting a process depend on the status engine agreeing.
                keys = set(agent_row.keys())
                agent = {
                    "id": agent_row["id"],
                    "role": (agent_row["role"] if "role" in keys else "") or "",
                    "runtime": (agent_row["runtime"] if "runtime" in keys else "") or "",
                    "model": (agent_row["model"] if "model" in keys else "") or "",
                    "sessionHandle": (agent_row["session_handle"] if "session_handle" in keys else "") or "",
                    "runtimeConfig": _json_loads_or(
                        agent_row["runtime_config"] if "runtime_config" in keys else "", {},
                    ),
                    "runtimeState": _json_loads_or(
                        agent_row["runtime_state"] if "runtime_state" in keys else "", {},
                    ),
                }

        settings = await _load_settings(db)
        runtime = str(terminal.get("runtime") or agent.get("runtime") or "")
        return {
            "ok": True,
            "launch": {
                "terminalId": terminal_id,
                "agentId": agent_id,
                "runtime": runtime,
                "command": terminal.get("command") or "",
                # THE STRUCTURAL FORM, which is what a host can actually execute. An operator-supplied
                # command has none, and splitting a human's shell string is the quoting bug this
                # design exists to avoid -- so an empty argv is a real answer meaning "not ours to run".
                "argv": terminal.get("argv") or [],
                "cwd": terminal.get("workspace") or "",
                "cols": terminal.get("cols") or 0,
                "rows": terminal.get("rows") or 0,
                "sessionHandle": str(terminal.get("sessionHandle") or agent.get("sessionHandle") or ""),
                "env": managed_launch_env(
                    terminal=terminal,
                    agent=agent,
                    workspace=terminal.get("workspace") or "",
                    terminal_id=terminal_id,
                    managed_via_wrapper=_managed_via_wrapper_for_runtime(settings, runtime),
                ),
            },
        }
    finally:
        await db.close()


@router.get("/terminals/{terminal_id}")
async def get_terminal(terminal_id: str, cols: Optional[int] = None, rows: Optional[int] = None):
    await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal_id)
    db = await get_db()
    try:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if not terminal:
            raise HTTPException(404, f'Terminal "{terminal_id}" not found')
        # THE LAST 200, not the first. `ORDER BY id ASC LIMIT 200` returned a terminal's OLDEST
        # events, so for any console busier than 200 rows everything recent -- including whatever
        # it was doing when it died -- was unreachable through the one endpoint that exists to
        # explain a terminal. Measured on a live console: the cap was hit exactly, which is what
        # being truncated looks like from outside.
        #
        # Selected DESC and reversed so the response stays in chronological order: the shape does
        # not change, only which 200 rows it carries.
        # ONE ROW WIDER THAN THE PAGE, so the response can say whether this is the whole history --
        # the same shape as /sessions, /dispatch/runs, /contracts and /messages/recent. The number
        # comes from `TERMINAL_EVENTS_KEPT_PER_TERMINAL` rather than being written here a second
        # time: the pruner keeps exactly that many, and two hardcoded 200s in different modules
        # agreed by coincidence.
        event_rows = await (await db.execute(
            "SELECT * FROM terminal_events WHERE terminal_id = ? ORDER BY id DESC LIMIT ?",
            (terminal_id, TERMINAL_EVENTS_KEPT_PER_TERMINAL + 1),
        )).fetchall()
        events_truncated = len(event_rows) > TERMINAL_EVENTS_KEPT_PER_TERMINAL
        events = list(reversed(event_rows[:TERMINAL_EVENTS_KEPT_PER_TERMINAL]))
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
        await _attach_terminal_snapshot(term_dict, cols, rows)
        return {
            "ok": True,
            "terminal": term_dict,
            "events": [_terminal_event_to_dict(row) for row in events],
            # WHAT THE CALLER IS LOOKING AT. Measured 2026-08-29: 21 of 26 terminals held 200 or
            # more events, so for most of them this list was already a page and said nothing.
            "eventsShowing": len(events),
            "eventsTruncated": events_truncated,
        }
    finally:
        await db.close()


async def _record_terminal_exit(db, terminal_id: str, exit_code, exit_signal) -> None:
    """Persist how a terminal's process ended.

    NOTHING RECORDED THIS UNTIL 2026-08-26. node-pty gives the bridge `{exitCode, signal}`,
    `terminal-runtime.js` spreads both into the exit detail, and `terminal-manager.mjs` then posted
    only an output marker and a status -- so both numbers were dropped at the last hop. When two
    managed workers died mid-turn the operator asked why, and every row said `status='stopped'`, an
    empty `error`, and nothing else. A terminal that dies takes its reason with it.

    Written with COALESCE so a later streaming chunk cannot blank a recorded exit, and so a second
    exit report (a retry, a duplicate flush) cannot turn a known code back into unknown.
    """
    updates = []
    params: list = []
    if exit_code is not None:
        updates.append("exit_code = COALESCE(exit_code, ?)")
        params.append(int(exit_code))
    signal = str(exit_signal or "").strip()
    if signal:
        updates.append("exit_signal = COALESCE(NULLIF(exit_signal, ''), ?)")
        params.append(signal)
    if not updates:
        return
    params.append(terminal_id)
    await db.execute(f"UPDATE terminal_sessions SET {', '.join(updates)} WHERE id = ?", tuple(params))
    await db.commit()


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
        is_virtual_rpc = terminal_command in VIRTUAL_RPC_COMMAND_SET
        await _settle_bridge_takeover_for_output(
            db, terminal, terminal_id, new_bridge_id, existing_bridge_id, is_virtual_rpc,
        )
        status = str(req.status or "").strip()
        # HOW IT ENDED, written straight to the row rather than through the output queue.
        #
        # The queue exists to COALESCE a high-frequency stream: many chunks collapse into one write.
        # An exit is reported once and its two values have nothing to do with that batching, so
        # threading them through the pending state would complicate the hot path to carry a field it
        # would forward unchanged. Writing here also means the exit survives a later output chunk --
        # bytes can still arrive after the exit POST on a busy terminal, and the queue's UPDATE names
        # only output, seq and status, so it cannot clobber these columns.
        #
        # `is not None` rather than truthiness: 0 is a clean exit and the most common value, and
        # `if req.exitCode:` would drop exactly the case this exists to record.
        if req.exitCode is not None or str(req.exitSignal or "").strip():
            await _record_terminal_exit(db, terminal_id, req.exitCode, req.exitSignal)
        next_seq = await TERMINAL_OUTPUT_WRITES.enqueue(
            terminal_id,
            req.output or "",
            status=status,
            base_seq=int(terminal["output_seq"] or 0),
            autoschedule=not bool(getattr(request.app.state, "testing", False)),
        )
        await _close_out_terminal_on_end_status(
            db, terminal, terminal_id, status, _TERMINAL_END_STATUSES,
        )
        # Do NOT broadcast per-POST here: concurrent POSTs reorder vs seq and
        # the dashboard's seq-dedupe then drops frames (scrambled console).
        # Hand the ws manager to the write queue, which emits one ordered,
        # coalesced, post-commit broadcast per flush instead.
        ws = await _get_ws(request)
        if ws is not None:
            TERMINAL_OUTPUT_WRITES.ws_manager = ws
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
