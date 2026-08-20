"""What a Console start does to `terminal_sessions`: the two row inserts, and the two virtual paths.

FOUR FUNCTIONS NOW, not the two this docstring described until v0.5.4 — the inserts came out first,
then the two virtual-pi console branches that surround them. `_start_virtual_pi_console` opens a new
RPC console (and calls `_insert_virtual_console_terminal` below it); `_reuse_virtual_rpc_console_-
terminal` takes over when one is already live. Keeping the pair adjacent is the point: which one runs
is decided by a single status test in the handler, and reading them apart is how you get two
answers to "what happens when a pi console starts".

Extracted from `start_session_console` in `service/routers/sessions.py`;
`test_start_session_console_split_is_inert.py` inlines them all back and AST-compares against the
pre-split fixture. Each body is at its ORIGINAL column so the SQL literals are preserved
byte-for-byte -- which is why they are indented at four different depths. That looks like a mistake
and is the opposite: re-indenting any one of them would rewrite the string contents inside it, and
the extract-method gate would refuse the move.

THEY ARE TWINS AND THEY ARE NOT MERGED. Twenty-five lines each, and exactly two differ: the command,
and the status a terminal is BORN with. A virtual RPC console is born `running` because the session
it fronts already exists; a real PTY is born `starting` because a bridge still has to spawn the
process, and one that claimed `running` before its process existed would make the agent look alive
to every status consumer. Collapsing them means threading that through as a parameter, which is
behaviour-shaped work on a refactor line.

PUTTING THEM IN ONE FILE IS THE POINT. Inline they sat ninety lines apart inside a 292-line handler,
where the duplication was invisible and a fix applied to one of them was silent. Here they are
adjacent, and `test_terminal_session_inserts_agree.py` fails if they drift beyond their two declared
differences -- or if they ever become identical, at which point merging them is a decision worth
making rather than something that quietly happened.
"""
from __future__ import annotations

import json

import time
import uuid

from fastapi import HTTPException

from service.api_core.events import _append_terminal_event
from service.api_core.records import (
    _agent_session_to_dict,
    _environment_record_to_dict,
    _terminal_session_to_dict,
)
from service.api_core.serialization import _json_loads_or
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMANDS_BY_RUNTIME
from service.api_core.workspace import _workspace_for_environment
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _insert_virtual_console_terminal(
    db, terminal_id, session_id, session, bridge_id, workspace, virtual_command, requested_by, now
) -> None:
            """The RPC-backed console row. Born `running`: the session it fronts already exists."""
            await db.execute(
                """
                INSERT INTO terminal_sessions (
                    id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command, argv,
                    output, status, requested_by, created_at, updated_at, stopped_at, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    terminal_id,
                    session_id,
                    session["agent_id"],
                    session["environment_id"],
                    bridge_id,
                    session["runtime"],
                    workspace,
                    virtual_command,
                    "",
                    "",
                    "running",
                    requested_by,
                    now,
                    now,
                    None,
                    "",
                ),
            )


async def _insert_pty_console_terminal(
    db, terminal_id, session_id, session, bridge_id, workspace, command, requested_by, now, argv
) -> None:
        """The real-PTY console row. Born `starting`: a bridge still has to spawn the process."""
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command, argv,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                terminal_id,
                session_id,
                session["agent_id"],
                session["environment_id"],
                bridge_id,
                session["runtime"],
                workspace,
                command,
                json.dumps(argv) if argv else "",
                "",
                "starting",
                requested_by,
                now,
                now,
                None,
                "",
            ),
        )


async def _reuse_virtual_rpc_console_terminal(
    db, req, request, session, session_id,
    virtual_terminal, virtual_terminal_id, virtual_status, virtual_command,
):
                        """Attach a session to an ALREADY-LIVE virtual pi RPC terminal instead of starting one.

                        Extracted from `start_session_console` in v0.5.4. An early exit: it ends in the response the
                        handler returns, a shape `service/tests/extract_method.py` could not judge until the
                        call-site-shape rule landed in this release.

                        THE BODY IS AT ITS ORIGINAL COLUMN ON PURPOSE, and it looks wrong until you know why. The
                        block contains a triple-quoted SQL literal, and dedenting the block re-indents the STRING'S
                        CONTENTS — a different `ast.Constant` value, so the inline-back comparison fails and the
                        extraction cannot be proved. Python only requires consistent indentation, not minimal, so
                        the body and this docstring both stay at the column they had in the handler.
                        """
                        attach_now = _now()
                        # Point the requesting session at the canonical
                        # virtual terminal so the dashboard's session view
                        # follows it.
                        await db.execute(
                            """
                            UPDATE agent_sessions
                            SET terminal_id = ?,
                                terminal_status = ?,
                                terminal_command = ?,
                                last_seen = ?
                            WHERE id = ?
                            """,
                            (virtual_terminal_id, virtual_status, virtual_command, attach_now, session_id),
                        )
                        await _append_terminal_event(
                            db,
                            virtual_terminal_id,
                            "virtual_pi_rpc_console_attached",
                            json.dumps({
                                "requestedBy": str(req.requestedBy or "dashboard").strip() or "dashboard",
                                "sessionId": session_id,
                                "agentId": session["agent_id"],
                            }),
                        )
                        await db.commit()
                        updated_session_for_virtual = await (await db.execute(
                            "SELECT * FROM agent_sessions WHERE id = ?",
                            (session_id,),
                        )).fetchone()
                        ws_for_virtual = await _get_ws(request)
                        if ws_for_virtual:
                            await ws_for_virtual.broadcast(
                                "terminal_started",
                                {
                                    "terminalId": virtual_terminal_id,
                                    "sessionId": session_id,
                                    "agentId": session["agent_id"],
                                    "virtual": True,
                                    "reused": True,
                                },
                            )
                        return {
                            "ok": True,
                            "terminal": _terminal_session_to_dict(virtual_terminal),
                            "session": _agent_session_to_dict(updated_session_for_virtual),
                            "reused": True,
                            "virtual": True,
                        }


async def _start_virtual_pi_console(
    db, req, request, session, session_id,
    settings, env_row, agent_row_for_virtual,
):
            """Start a NEW virtual pi RPC console: gate the environment, insert the row, announce it.

            Extracted from `start_session_console` in v0.5.4 — the counterpart to
            `_reuse_virtual_rpc_console_terminal` above, which takes over when one is already live.
            Another early exit ending in the handler's response, and it ENCLOSES
            `_insert_virtual_console_terminal`, so it needed both this release's call-site-shape rule
            and its dependency-ordered inlining.

            Body at its ORIGINAL COLUMN, same reason as its neighbours: it contains triple-quoted SQL
            and dedenting would rewrite the string contents.

            The two `raise HTTPException` guards travelled with it deliberately. A raise propagates out
            of a helper exactly as it did from the handler, which is why the extract-method gate counts
            `return` as an escape and `raise` as ordinary control flow.
            """
            environment = _environment_record_to_dict(env_row, offline_seconds=settings.get("environment_offline_seconds", 90))
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}')
            if not str(session["session_handle"] or "").strip() and not bool(req.freshContext):
                raise HTTPException(409, 'Pi Console needs a session handle to preserve context. Set a handle or request freshContext=true.')
            workspace, _workspace_root = _workspace_for_environment(environment, req.workspace, session["workspace"] or "")
            terminal_id = f"vterm_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            now = _now()
            bridge_id = str(environment.get("bridgeId") or "").strip()
            virtual_command = VIRTUAL_RPC_COMMANDS_BY_RUNTIME["pi"]
            requested_by = str(req.requestedBy or "dashboard").strip() or "dashboard"
            await _insert_virtual_console_terminal(
                db, terminal_id, session_id, session, bridge_id, workspace, virtual_command,
                requested_by, now,
            )
            await _append_terminal_event(
                db,
                terminal_id,
                "virtual_pi_rpc_console_started",
                json.dumps({"requestedBy": requested_by, "sessionId": session_id, "workspace": workspace}),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'managed',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = 'running',
                    terminal_command = ?,
                    terminal_workspace = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (bridge_id, terminal_id, virtual_command, workspace, now, session_id),
            )
            next_runtime_state = _json_loads_or((agent_row_for_virtual["runtime_state"] if agent_row_for_virtual else "") or "{}", {}) or {}
            next_runtime_state["virtualTerminal"] = True
            next_runtime_state["virtualTerminalId"] = terminal_id
            await db.execute(
                "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                (json.dumps(next_runtime_state), now, session["agent_id"]),
            )
            # The agent now has a live worker (virtualTerminalId + terminal_status
            # running). Invalidate the live-status cache so it recomputes to online
            # immediately instead of lying `available` until the 60s sweep.
            await _invalidate_agent_live_state(db, session["agent_id"])
            await db.commit()
            terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
            updated_session = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
            ws_for_virtual = await _get_ws(request)
            if ws_for_virtual:
                await ws_for_virtual.broadcast(
                    "terminal_started",
                    {"terminalId": terminal_id, "sessionId": session_id, "agentId": session["agent_id"], "virtual": True},
                )
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(terminal),
                "session": _agent_session_to_dict(updated_session),
                "reused": False,
                "virtual": True,
            }


async def _reanchor_existing_virtual_terminal(
    db, existing, session_row, session_id, bridge_id, virtual_command,
):
            """Re-point an existing virtual RPC terminal at the session now asking for it.

            Extracted from `ensure_virtual_terminal` (`service/routers/agents/console.py`) in v0.5.4.
            Another early exit, extractable only since this release's call-site-shape rule.

            IT IS THE INVERSE OF `_reuse_virtual_rpc_console_terminal` ABOVE, and they are deliberately
            adjacent rather than merged. That one points a SESSION at an existing terminal (it writes
            `agent_sessions`); this one points a TERMINAL at a new session (it writes
            `terminal_sessions.session_id`). Same pair of rows, opposite direction, different entry
            points — sessions console start versus the agents console ensure. Collapsing them would
            mean deciding which row is authoritative, which is a behaviour question.

            `existing` and `session_row` are parameters AND rebound here: on the path where the
            terminal is already anchored to this session neither is re-read, so both must arrive from
            the caller. A free-name scan misses that — the names are written somewhere in the block, so
            they look local — and only the gate's live-in check sees it.

            Body at its ORIGINAL COLUMN: it contains triple-quoted SQL, and dedenting rewrites the
            string contents.
            """
            existing_session_id = existing["session_id"]
            if existing_session_id != session_id:
                rebind_now = _now()
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET session_id = ?,
                        bridge_id = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (session_id, bridge_id, rebind_now, existing["id"]),
                )
                # Detach the prior session from the terminal but keep its
                # historical record otherwise intact.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = '',
                        terminal_command = ''
                    WHERE id = ? AND terminal_id = ?
                    """,
                    (existing_session_id, existing["id"]),
                )
                # Point the new active session at the terminal.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_id = ?,
                        terminal_status = 'running',
                        terminal_command = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (existing["id"], virtual_command, rebind_now, session_id),
                )
                await _append_terminal_event(
                    db,
                    existing["id"],
                    "virtual_pi_rpc_reanchored",
                    json.dumps({
                        "fromSessionId": existing_session_id,
                        "toSessionId": session_id,
                        "bridgeId": bridge_id,
                    }),
                )
                await db.commit()
                existing = await (await db.execute(
                    "SELECT * FROM terminal_sessions WHERE id = ?",
                    (existing["id"],),
                )).fetchone()
                session_row = await (await db.execute(
                    "SELECT * FROM agent_sessions WHERE id = ?",
                    (session_id,),
                )).fetchone()
            return {
                "ok": True,
                "terminal": _terminal_session_to_dict(existing),
                "session": _agent_session_to_dict(session_row),
                "reused": True,
            }
