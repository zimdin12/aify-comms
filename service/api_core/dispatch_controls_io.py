"""Claiming dispatch controls in ONE short-lived immediate transaction.

Moved out of `service/routers/dispatch_messages/dispatch.py` in v0.5.4, byte-identical. A router
should hold routes, and this was its only substantial non-route declaration — the sibling move to
`service/api_core/terminal_controls_io.py` in the same series did the same for the terminal claim, so
the two now sit at the same layer instead of one in a router and one in a leaf.

WHY THE SHORT BUSY TIMEOUT. A claim probe is "is there work for me?" — idempotent and retry-safe — so
under write contention it should fail fast and report "nothing claimed this round" rather than camp on
the write lock for the full 5s. Without that, a long-poll's final attempt near the wait deadline could
block ~5s on the lock and push the whole request past the bridge's ~28s HTTP timeout.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.serialization import _machine_ids_same_host
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.models import DispatchControlClaimRequest

async def _claim_dispatch_controls_once(req: DispatchControlClaimRequest, request: Request):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        machine_id = req.machineId or ""
        if machine_id and agent["machine_id"] and not _machine_ids_same_host(agent["machine_id"], machine_id):
            await db.rollback()
            return {"ok": True, "controls": []}

        # Claim pending controls for this agent. No filter on run status —
        # Claude resident runs complete immediately on delivery, so their
        # controls would never be claimable under the old ('claimed','running')
        # filter. The channel bridge polls for controls independently and
        # delivers them as notifications regardless of run state.
        controls_cursor = await db.execute(
            """
            SELECT dc.*, dr.target_agent, dr.status as run_status
            FROM dispatch_controls dc
            JOIN dispatch_runs dr ON dr.id = dc.run_id
            WHERE dr.target_agent = ? AND dc.status = 'pending'
              AND (? = '' OR dc.run_id = ?)
            ORDER BY dc.requested_at ASC, dc.id ASC
            LIMIT 20
            """,
            (req.agentId, req.runId or "", req.runId or "")
        )
        controls = await controls_cursor.fetchall()
        if not controls:
            await db.commit()
            return {"ok": True, "controls": []}

        claimed_at = _now()
        results = []
        for control in controls:
            await db.execute(
                "UPDATE dispatch_controls SET status = 'claimed', claim_machine_id = ?, claimed_at = ? WHERE id = ?",
                (machine_id, claimed_at, control["id"])
            )
            results.append({
                "id": control["id"],
                "runId": control["run_id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "requestedAt": control["requested_at"],
                "claimedAt": claimed_at,
            })

        await db.commit()
        return {"ok": True, "controls": results}
    finally:
        await db.close()
