"""A current bridge registration for an agent, for tests that send the bridge's own reports.

A `bridge-heartbeat` session-handle report counts only from a bridge that is a current registration
of the agent (service/api_core/bridge_report_gate.py). Tests of what a report DOES seed that row here,
so each one says which bridge is reporting rather than relying on a route that believed any caller.
"""

from __future__ import annotations

import sqlite3


def registered_bridge(db_path, agent_id: str) -> str:
    """Ensure `bridge-<agent_id>` is a current, non-superseded bridge of `agent_id`; return its id."""
    bridge_id = f"bridge-{agent_id}"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO bridge_instances (id, agent_id, registered_at, last_seen)"
            " VALUES (?, ?, '2026-09-28T00:00:00Z', '2026-09-28T00:00:00Z')",
            (bridge_id, agent_id),
        )
        conn.commit()
    finally:
        conn.close()
    return bridge_id
