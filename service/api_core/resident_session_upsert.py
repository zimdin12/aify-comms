"""Writing the agent_sessions row for a RESIDENT agent at registration.

RELOCATED from `service/api_core/agent_sessions.py` in v0.5.4, byte-identical. It was 113 of that
module's 452 lines and is the cleanest relocation in this series: it calls no sibling there, reads
none of its constants, and its only free names are `json`, `uuid` and `Any`. One importer,
`routers/agents/identity.py`, which is the registration route.

RESIDENT IS THE DIFFERENT CASE, which is why it is its own module rather than sharing one with the
managed path. A managed session is minted by a spawn becoming a worker — see
`api_core/running_spawn.py`, whose upsert this deliberately does NOT share. A resident session is
minted by the agent REGISTERING itself: the process already exists, nothing is being started, and the
row has to describe something that is already running. Collapsing the two would mean a single writer
that has to be told which of those two stories it is in.

DB ACCESS: `db` is passed in and nothing here opens a connection or commits — this joins its caller's
transaction, which for registration is the one `register_agent` commits at the end.
"""
from __future__ import annotations

import json
import uuid
from typing import Any


async def _upsert_resident_agent_session(
    db,
    *,
    agent_id: str,
    runtime: str,
    workspace: str,
    machine_id: str,
    session_handle: str,
    runtime_config: dict[str, Any] | None,
    bridge_id: str,
    capabilities: list[str] | None,
    now: str,
) -> str:
    """Create the dashboard-visible session row for an operator-open CLI."""

    config = runtime_config if isinstance(runtime_config, dict) else {}
    machine = str(machine_id or "").strip()
    env_row = None
    if machine:
        env_row = await (await db.execute(
            """
            SELECT id
            FROM environments
            WHERE lower(machine_id) = lower(?)
              AND status != 'forgotten'
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (machine,),
        )).fetchone()
    if not env_row:
        return ""

    # FIX 1 (2026-06-03): the resident session id must be STABLE across relaunches
    # so a relaunch UPSERTs the SAME row instead of minting a new resident_* every
    # launch. session_handle / gatewayUrl / bridge_id all ROTATE per launch, so the
    # ON CONFLICT(id) DO UPDATE could never match and duplicate rows accumulated.
    # Key on (machine or env id or agent) — stable per (agent_id, runtime, machine).
    key_material = machine or str(env_row["id"]) or agent_id
    session_id = f"resident_{uuid.uuid5(uuid.NAMESPACE_URL, f'aify-comms:{agent_id}:{runtime}:{key_material}').hex[:16]}"
    app_server_url = str(config.get("appServerUrl") or "").strip()
    telemetry = {
        "resident": True,
        "nativeResume": bool(session_handle),
        "bridgeResume": bool(bridge_id),
        "cliAttach": True,
        "gateway": bool(str(config.get("gatewayUrl") or "").strip()),
    }
    await db.execute(
        """
        INSERT INTO agent_sessions (
            id, agent_id, environment_id, runtime, workspace, mode,
            owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
            process_id, session_handle, app_server_url, spawn_spec_id, spawn_request_id,
            capabilities, telemetry, status, started_at, last_seen, ended_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            runtime = excluded.runtime,
            workspace = excluded.workspace,
            mode = excluded.mode,
            owner_mode = excluded.owner_mode,
            owner_bridge_id = excluded.owner_bridge_id,
            session_handle = excluded.session_handle,
            app_server_url = excluded.app_server_url,
            capabilities = excluded.capabilities,
            telemetry = excluded.telemetry,
            status = 'running',
            last_seen = excluded.last_seen,
            ended_at = NULL
        """,
        (
            session_id,
            agent_id,
            env_row["id"],
            runtime,
            workspace or "",
            "resident",
            "resident",
            bridge_id or "",
            "",
            "",
            "",
            "",
            "",
            session_handle or "",
            app_server_url,
            None,
            None,
            json.dumps({"resident": True, "cliAttach": True, "capabilities": capabilities or []}),
            json.dumps(telemetry),
            "running",
            now,
            now,
            None,
        ),
    )
    # RC3 (2026-06-03): collapse duplicate resident sessions. The resident session
    # id is a hash of the session_handle (line ~12879), so a relaunch with a new
    # native handle mints a NEW resident_* row while the prior one stays 'running'
    # — the dashboard then shows two live resident sessions for one agent. Retire
    # every OTHER resident session for this agent so exactly one stays live.
    await db.execute(
        """
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE agent_id = ?
          AND mode = 'resident'
          AND id != ?
          AND status NOT IN ('stopped', 'failed', 'exited')
        """,
        (now, agent_id, session_id),
    )
    return session_id
