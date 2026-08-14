"""Spawn-request shaping and the claim probe, as a leaf.

Moved out of `service/routers/spawn_requests.py` in v0.5.4, byte-identical.

WHY, AND IT IS NOT THE LINE COUNT. `service/routers/sessions.py` imported `_spawn_request_to_dict`
and `_spawn_spec_to_dict` FROM the spawn-requests ROUTER — one router reaching into another. That
placement was deliberate and correct at the time: v0.5.2i measured the two as belonging to the
spawn-requests DOMAIN, not to sessions, and moving them there retired two borrow shims. The domain
judgement still holds; what changes here is only the layer. Both routers now import from a leaf, and
neither imports the other.

`_claim_spawn_request_once` comes along because it calls both, and leaving it behind would only have
replaced a router-to-router import with a router-to-leaf one in the opposite direction.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Request

from service.api_core.serialization import _json_loads_or
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.models import SpawnRequestClaim

# v0.5.2i: RETIRED BORROWS. Both were borrowed from the router until the sessions
# domain moved; their real owner is here, and this module is now that owner.
def _spawn_request_to_dict(row, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "spawnSpecId": row["spawn_spec_id"],
        "createdBy": row["created_by"] or "",
        "environmentId": row["environment_id"],
        "agentId": row["agent_id"],
        "role": row["role"] or "coder",
        "name": row["name"] or "",
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "workspaceRoot": row["workspace_root"] or "",
        "initialMessage": row["initial_message"] or "",
        "priority": row["priority"] or "normal",
        "subject": row["subject"] or "",
        "mode": row["mode"] or "managed-warm",
        "resumePolicy": row["resume_policy"] or "native_first",
        "status": row["status"] or "queued",
        "claimedByBridgeId": row["claimed_by_bridge_id"] or "",
        "claimMachineId": row["claim_machine_id"] or "",
        "processId": row["process_id"] or "",
        "sessionHandle": row["session_handle"] or "",
        "sessionId": row["session_id"] or "",
        "error": row["error"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "startedAt": row["started_at"] or "",
        "finishedAt": row["finished_at"] or "",
    }
    if spec is not None:
        payload["spawnSpec"] = spec
    return payload


def _spawn_spec_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "model": row["model"] or "",
        "profile": row["profile"] or "",
        "mode": row["mode"] or "managed-warm",
        "systemPrompt": row["system_prompt"] or "",
        "instructions": row["standing_instructions"] or "",
        "envVars": _json_loads_or(row["env_vars"], {}),
        "channelIds": _json_loads_or(row["channel_ids"], []),
        "budgetPolicy": _json_loads_or(row["budget_policy"], {}),
        "contextPolicy": _json_loads_or(row["context_policy"], {}),
        "restartPolicy": _json_loads_or(row["restart_policy"], {}),
        "metadata": _json_loads_or(row["metadata"], {}),
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
    }

async def _claim_spawn_request_once(req: SpawnRequestClaim, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            await db.rollback()
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        env_bridge_id = str(env_row["bridge_id"] or "").strip()
        if env_bridge_id and env_bridge_id != str(req.bridgeId or "").strip():
            await db.commit()
            return {
                "ok": True,
                "spawnRequest": None,
                "blockedBy": {
                    "reason": "bridge_not_current",
                    "environmentId": req.environmentId,
                    "bridgeId": req.bridgeId,
                    "currentBridgeId": env_bridge_id,
                },
            }

        row_cursor = await db.execute(
            """
            SELECT *
            FROM spawn_requests
            WHERE environment_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (req.environmentId,),
        )
        row = await row_cursor.fetchone()
        if not row:
            await db.commit()
            return {"ok": True, "spawnRequest": None}

        claimed_at = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'claimed', claimed_by_bridge_id = ?, claim_machine_id = ?,
                claimed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (req.bridgeId, req.machineId or "", claimed_at, claimed_at, row["id"]),
        )
        await db.execute(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (claimed_at, req.environmentId),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (row["id"],))).fetchone()
        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_claimed", {"spawnRequestId": row["id"], "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(spec_row) if spec_row else None)}
    finally:
        await db.close()
