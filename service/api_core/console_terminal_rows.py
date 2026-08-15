"""The two terminal_sessions inserts a Console start makes, side by side.

Extracted from `start_session_console` in `service/routers/sessions.py` in v0.5.4;
`test_start_session_console_split_is_inert.py` inlines both back and AST-compares against the
pre-split fixture. Each body is at its ORIGINAL column so the SQL literals are preserved
byte-for-byte -- which is why the first is indented four spaces deeper than the second. That looks
like a mistake and is the opposite: re-indenting either one would rewrite the string contents inside
it, and the extract-method gate would refuse the move.

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

from service.api_core.events import _append_terminal_event
from service.api_core.records import _agent_session_to_dict, _terminal_session_to_dict
from service.api_core.ws import _get_ws
from service.clock import now as _now


async def _insert_virtual_console_terminal(
    db, terminal_id, session_id, session, bridge_id, workspace, virtual_command, requested_by, now
) -> None:
            """The RPC-backed console row. Born `running`: the session it fronts already exists."""
            await db.execute(
                """
                INSERT INTO terminal_sessions (
                    id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                    output, status, requested_by, created_at, updated_at, stopped_at, error
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                    "running",
                    requested_by,
                    now,
                    now,
                    None,
                    "",
                ),
            )


async def _insert_pty_console_terminal(
    db, terminal_id, session_id, session, bridge_id, workspace, command, requested_by, now
) -> None:
        """The real-PTY console row. Born `starting`: a bridge still has to spawn the process."""
        await db.execute(
            """
            INSERT INTO terminal_sessions (
                id, session_id, agent_id, environment_id, bridge_id, runtime, workspace, command,
                output, status, requested_by, created_at, updated_at, stopped_at, error
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
