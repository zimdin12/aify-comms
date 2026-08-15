"""The agent console surface and virtual-terminal ensure.

v0.5.2m, one surface of the agents package. Built with `domain_router()`;
declares NO tags — the parent applies `tags=["api"]` once when api_v2 includes the package.
"""

from __future__ import annotations

from service.api_core.managed_pty_for_dispatch import _ensure_managed_pty_for_dispatch
from service.api_core.terminal_text import _ANSI_RE
import asyncio
import json
import logging
import time

from fastapi import HTTPException, Request

from service.api_core.status_events import _apply_status_event
from service.api_core.routing import domain_router

logger = logging.getLogger("aify_comms.routers.agents.console")

# Imported for the ANNOTATIONS. Under postponed evaluation these are strings, so a
# missing one does not fail import -- FastAPI demotes the body to a query parameter and
# the endpoint 422s at request time. The route annotation gate caught 17 of these here.
from service.models import AgentConsoleInputRequest

from service.api_core.events import _append_terminal_control, _append_terminal_event
from service.api_core.console_terminal_rows import _reanchor_existing_virtual_terminal
from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_inputs import _compute_live_status_cache
from service.api_core.ws import _get_ws
from service.db import get_db
import re
from service.clock import now as _now
from service.terminal_diagnostics import (
    failure_tail as _terminal_failure_tail,
    meaningful_failure_line as _terminal_failure_line,
)
from service.terminal_snapshot import (
    render_live_screen as _render_live_terminal_screen,
    render_snapshot as _render_terminal_snapshot,
)
import sqlite3
from service.routers.agents.shared import (
    _borrowed_console_tail_max_bytes,
    _borrowed_console_tail_max_lines,
    logger,
)
from service.terminal_write_queue import TERMINAL_OUTPUT_WRITES
from service.api_core.agent_terminal_ops import (
    _resolve_live_console_terminal,
)

router = domain_router()

@router.get("/agents/{agent_id}/console")
async def get_agent_console(agent_id: str, lines: int = 40):
    """Read the tail of an agent's console output, live or LAST RECORDED (read-only).

    Side-effect-free: never starts a worker. Resolves the agent's live console
    terminal via runtime_state; if none, falls back to the most recent terminal the
    agent had and returns its RECORDED tail with live=false, historical=true.

    Why the fallback exists (v0.2 WS-1). Until v0.2 this returned only "no live
    console", which made a DEAD worker's output — the case that matters most —
    unreachable by the agent that needed it. On 2026-08-07 the cause of a failed
    managed hermes launch sat in `terminal_sessions.output` for 2.5 hours while the
    requesting agent was told "No online environment can host managed hermes", and
    the operator had to relay the real error to a human. The bytes were never
    missing; nothing would serve them.
    """
    db = await get_db()
    try:
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
            if not agent_row:
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            # Most recent terminal this agent had, whatever its state. Ordered by
            # created_at (not updated_at) so a late touch on an old row cannot
            # outrank the genuinely newest attempt.
            past = await (await db.execute(
                """
                SELECT id, status, output, error, stopped_at, updated_at, command
                FROM terminal_sessions
                WHERE agent_id = ?
                ORDER BY datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01')) DESC, rowid DESC
                LIMIT 1
                """,
                (agent_id,),
            )).fetchone()
            recorded = str((past["output"] if past is not None else "") or "")
            if past is not None and (recorded.strip() or str(past["error"] or "").strip()):
                tail_lines = max(1, min(int(lines or 40), _borrowed_console_tail_max_lines()))
                output = _terminal_failure_tail(recorded, max_lines=tail_lines)
                cause = _terminal_failure_line(recorded) or str(past["error"] or "").strip()
                died_at = str(past["stopped_at"] or past["updated_at"] or "")
                status = str(past["status"] or "").strip().lower() or "unknown"
                return {
                    "ok": True,
                    "live": False,
                    "historical": True,
                    "terminalId": str(past["id"] or ""),
                    "status": status,
                    "stoppedAt": died_at,
                    "command": str(past["command"] or ""),
                    "failureLine": cause,
                    "lines": len(output.splitlines()) if output else 0,
                    "output": output,
                    "message": (
                        f"{agent_id} has NO live console. This is the last recorded output of "
                        f"terminal {past['id']} ({status}"
                        + (f" at {died_at}" if died_at else "")
                        + "), not a running session."
                    ),
                }
            return {
                "ok": True,
                "live": False,
                "historical": False,
                "message": f"{agent_id} has no live console (it lazy-starts on a message).",
            }
        # Drain any buffered output for this terminal so the tail is current,
        # then re-read the row to pick up the flushed bytes.
        await TERMINAL_OUTPUT_WRITES.flush_terminal(terminal["id"])
        terminal = await (
            await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal["id"],))
        ).fetchone()
        tail_lines = max(1, min(int(lines or 40), _borrowed_console_tail_max_lines()))
        keys = terminal.keys()
        full_output = (terminal["output"] if "output" in keys else "") or ""
        screen_output = full_output
        terminal_id = str(terminal["id"] or "")
        if terminal_id and not terminal_id.startswith("vterm_"):
            try:
                live = _render_live_terminal_screen(terminal_id)
                if live:
                    screen_output = live[0]
                elif "\x1b" in full_output:
                    screen_output = await asyncio.to_thread(
                        _render_terminal_snapshot,
                        full_output,
                        int(terminal["cols"] or 0) if "cols" in keys else 100,
                        int(terminal["rows"] or 0) if "rows" in keys else 40,
                    )
            except Exception:
                screen_output = full_output
        clean = _ANSI_RE.sub("", screen_output)
        clean = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", clean)
        screen_lines = clean.splitlines()
        while screen_lines and not screen_lines[-1].strip():
            screen_lines.pop()
        selected = screen_lines[-tail_lines:]
        output = "\n".join(selected)
        if len(output.encode("utf-8", "ignore")) > _borrowed_console_tail_max_bytes():
            output = output.encode("utf-8", "ignore")[-_borrowed_console_tail_max_bytes():].decode("utf-8", "ignore")
        return {
            "ok": True,
            "live": True,
            "historical": False,
            "terminalId": terminal["id"],
            "status": terminal["status"] or "",
            "lines": len(selected),
            "output": output,
        }
    finally:
        await db.close()


@router.post("/agents/{agent_id}/console/input")
async def post_agent_console_input(agent_id: str, req: AgentConsoleInputRequest, request: Request):
    """Send input (keystrokes/text) into an agent's live console. Audited.

    SAFETY: the caller (`from`) must be a registered agent; the input is
    recorded against that caller in both the terminal control's requested_by
    and an `agent_console_input` audit event. Callers can only target the
    agent's own resolved console terminal — never an arbitrary terminal id.
    Managed agents only (v1). This explicit recovery/control path is intentionally
    independent of `insert_messages_via_console`, which gates only legacy automatic
    message delivery through a PTY. Disabling that legacy path must never disable
    deliberate console control.
    """
    db = await get_db()
    try:
        agent_row = await (await db.execute("SELECT id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        caller = str(req.from_ or "").strip()
        if not caller:
            raise HTTPException(400, "console input requires a `from` caller (the requesting agent id)")
        caller_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (caller,))).fetchone()
        if not caller_row:
            raise HTTPException(403, f"caller '{caller}' is not a registered agent")

        settings = await _load_settings(db)
        terminal = await _resolve_live_console_terminal(db, agent_id)
        if not terminal:
            # Best-effort lazy-autostart the SAME way dispatch does so the
            # visible-TUI requirement is preserved. If nothing can be started
            # (no live session / offline env), return the clear message.
            started = await _ensure_managed_pty_for_dispatch(
                db,
                agent_id,
                runtime=str(agent_row["runtime"] or ""),
                settings=settings,
                requested_by=caller,
            )
            await db.commit()
            if not started:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }
            # Re-resolve via runtime_state (autostart publishes the pointer).
            terminal = await _resolve_live_console_terminal(db, agent_id)
            if not terminal:
                # The freshly-started terminal row exists but its runtime_state
                # pointer may not be the consoleTerminal shape yet (e.g. the
                # `starting` row from _ensure_managed_pty_for_dispatch). Use it
                # directly — it is agent-scoped (the helper only returns this
                # agent's own session terminal).
                started_id = started["terminal_id"] if "terminal_id" in started.keys() else started["id"]
                terminal = await (
                    await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (started_id,))
                ).fetchone()
            if not terminal:
                return {
                    "ok": False,
                    "live": False,
                    "message": f"{agent_id} has no live console; send a message to start it first.",
                }

        text = str(req.text or "")
        body = text + ("\r" if (req.enter is None or req.enter) else "")
        control_id = await _append_terminal_control(
            db,
            terminal_id=terminal["id"],
            environment_id=terminal["environment_id"],
            bridge_id=terminal["bridge_id"] or "",
            action="input",
            requested_by=caller,
            body=body,
        )
        await _append_terminal_event(
            db,
            terminal["id"],
            "agent_console_input",
            json.dumps({"from": caller, "controlId": control_id, "bytes": len(body)}),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("terminal_control_requested", {"terminalId": terminal["id"], "action": "input"})
        # HONESTY (C8, 2026-07-26). `ok` here means the control was QUEUED — nothing more. It was
        # being read as "the input landed and the runtime acted on it", and that is not a claim this
        # endpoint can make: an operator's sc-manager issued two text writes and three bare-Enter
        # retries against a stuck managed-claude draft, ALL of which reached status='completed' with
        # handled_at set, while the draft never submitted. A completed control proves only that the
        # bridge wrote the bytes to the PTY; whether the TUI consumed them as a keypress is
        # unobservable from here. The tool lied to an AGENT, which then burned ~15 minutes of
        # critical path retrying a lever that could not work.
        #
        # Deliberately NOT verified here. Two reasons, and the second is why there is no byte-diff
        # affordance either:
        #   * confirming would mean waiting for the bridge's poll cycle inside this handler, and the
        #     service is single-worker by hard constraint (_LIVE_STATE_CACHE) — a blocking wait would
        #     stall every other request;
        #   * a tail diff CANNOT prove submission (review finding on `35cc646`). A managed claude
        #     repaints its spinner and footer continuously, so the console output changes constantly
        #     whether or not the keystroke was consumed. An earlier cut of this returned
        #     `consoleBytesBefore` for the caller to diff; that was a misleading affordance dressed
        #     as evidence, so it is gone rather than documented.
        # What is left is the honest shape: say it is queued, say submission is unknown, and point at
        # the only thing that actually settles it — a human or agent READING the console and judging
        # whether the draft is still sitting at the prompt.
        return {
            "ok": True,
            "live": True,
            "terminalId": terminal["id"],
            "controlId": control_id,
            "queued": True,
            # Tri-state on purpose: not False (no failure was observed) and not True (no submit was
            # observed). Unknown is the only defensible value, and it is not knowable from here.
            "submitted": None,
            "note": (
                "QUEUED, not confirmed. A completed control proves only that the bytes were "
                "written to the PTY — not that the runtime acted on them. Nothing this endpoint "
                "returns can confirm a submit; read the console with comms_console_tail and judge "
                "whether the draft is still at the prompt."
            ),
        }
    finally:
        await db.close()
