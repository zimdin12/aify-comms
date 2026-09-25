"""`GET /bridges`: the bridge processes that are live right now, and which build each one loaded.

`aify-comms doctor`'s `bridge-current` row reads this. It used to read `environments[].metadata`
for a `bridgeBuild` only the retired environment bridge ever sent, so from v0.6.2 it saw no bridge
at all and reported green while every `claude-aify`, `codex-aify` and `hermes-aify` session ran
`server.js` unchecked (v0.7 scan B2). Resident and managed bridges now send `bridgeBuild` when they
register, and it is stored on their `bridge_instances` row.

LIVE MEANS BEATING: not superseded, and seen within `ACTIVE_RUN_BRIDGE_STALE_SECONDS`, the window the
service already uses to decide a bridge still owns its run. Channel sidecars are left out: they are
registered by their heartbeat alone and never report a build, so listing them would make every
Claude host read "did not report".
"""

from __future__ import annotations

import time

from service.api_core.live_process_probes import ACTIVE_RUN_BRIDGE_STALE_SECONDS
from service.api_core.routing import domain_router
from service.clock import ISO_SECONDS
from service.db import get_db

router = domain_router()


@router.get("/bridges")
async def list_live_bridges():
    cutoff = time.strftime(ISO_SECONDS, time.gmtime(time.time() - ACTIVE_RUN_BRIDGE_STALE_SECONDS))
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT id, agent_id, machine_id, runtime, session_mode, bridge_kind, bridge_build, last_seen
            FROM bridge_instances
            WHERE COALESCE(superseded_by, '') = ''
              AND COALESCE(bridge_kind, '') != 'channel-sidecar'
              AND last_seen >= ?
            ORDER BY agent_id, last_seen DESC
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()
    finally:
        await db.close()
    return {
        "liveWithinSeconds": ACTIVE_RUN_BRIDGE_STALE_SECONDS,
        "bridges": [
            {
                "id": row["id"],
                "agentId": row["agent_id"],
                "machineId": row["machine_id"] or "",
                "runtime": row["runtime"] or "",
                "sessionMode": row["session_mode"] or "",
                "bridgeKind": row["bridge_kind"] or "",
                "build": row["bridge_build"] or "",
                "lastSeen": row["last_seen"],
            }
            for row in rows
        ],
    }
