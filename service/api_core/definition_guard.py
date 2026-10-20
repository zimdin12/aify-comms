"""What a host's definition protects from the paths that wrote it before (P0 C5).

A defined agent's descriptive columns are written by its definition, through the push, and by nothing
else. Registration and running settlement still write an undefined agent's, as they always have.

THE GUARD IS PART OF THE STATEMENT THAT WRITES. `kept_when_defined` reads `agent_definitions` inside
the UPDATE itself, and one SQLite statement is atomic, so a push racing a registration cannot land
between the check and the write.
"""
from __future__ import annotations

import json

#: SQL, true inside an UPDATE of `agents` (or its ON CONFLICT branch) when a host defines that agent.
DEFINED_SQL = "EXISTS (SELECT 1 FROM agent_definitions WHERE agent_definitions.agent_id = agents.id)"


def kept_when_defined(column: str, value_sql: str) -> str:
    """The SET clause for a descriptive column: as the definition set it for a defined agent, else
    `value_sql`. `column` is a literal from the caller's own statement, never input."""
    return f"{column} = CASE WHEN {DEFINED_SQL} THEN agents.{column} ELSE {value_sql} END"


async def defined_on(db, agent_id: str) -> str:
    """The machine whose definition this agent is, or "" for an agent no host defines."""
    row = await (await db.execute(
        "SELECT machine_id FROM agent_definitions WHERE agent_id = ?", (agent_id,))).fetchone()
    return str(row["machine_id"]) if row else ""


async def defined_mode(db, agent_id: str) -> str:
    """The mode this agent's definition asks for, or "" for an agent no host defines."""
    row = await (await db.execute("SELECT body FROM agent_definitions WHERE agent_id = ?", (agent_id,))).fetchone()
    return str(json.loads(row["body"] or "{}").get("mode") or "") if row else ""


async def defined_among_sessions_on(db, environment_id: str) -> list[str]:
    """The defined agents an environment stop would write: every agent with ANY session there, current or ended,
    whichever machine defines it (B2). Not forget's question, which asks only what this environment's store governs."""
    rows = await (await db.execute(
        "SELECT DISTINCT s.agent_id FROM agent_sessions s JOIN agent_definitions d ON d.agent_id = s.agent_id "
        "WHERE s.environment_id = ? ORDER BY s.agent_id", (environment_id,))).fetchall()
    return [row["agent_id"] for row in rows]
