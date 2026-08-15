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
