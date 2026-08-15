"""The environment-control claim funnel: one bridge, one control, one claim.

RELOCATED, not rewritten, in v0.5.4 -- byte-identical from `service/routers/environments.py`.

SERVICE LEVEL, NOT api_core, for exactly the reason `service/dispatch_claim.py` gives. Every api_core
leaf takes `db` and owns no transaction: no `get_db(`, no `.commit(`, no `.rollback(`. This function
opens its own connection and commits twice, because SERIALISING THE CLAIM IS ITS JOB -- two bridges
must not claim the same control. So it sits beside `dispatch_claim.py` and `terminal_write_queue.py`,
the other service-level transaction owners, rather than diluting the api_core rule to fit it.

IT IS THE SAME SHAPE AS THE DISPATCH CLAIM AND IS NOT MERGED WITH IT. Both hand exactly one unit of
work to exactly one bridge under a write transaction, but they claim different things with different
gates -- a dispatch claim reasons about agents, turns and stale console owners, while this one
reasons about which environment a bridge is serving. Unifying them would be a behaviour change
dressed as deduplication, and v0.5.x is the refactor line.

THE LONG-POLL WRAPPER STAYS IN THE ROUTER. `claim_environment_control` holds the request open and
calls this repeatedly, which is identical to a bridge re-polling over HTTP -- so the claim semantics
live here and the waiting lives there.
"""
from __future__ import annotations

from service.api_core.records import _environment_record_to_dict
from service.api_core.serialization import _json_loads_or
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db

# Imported for the ANNOTATION. Under postponed evaluation a missing model does not fail import --
# it silently demotes a request body to a query parameter and the endpoint 422s at request time.
from service.models import EnvironmentControlClaim


async def _claim_environment_control_once(req: EnvironmentControlClaim):
    db = await get_db(busy_timeout_ms=SQLITE_CLAIM_BUSY_TIMEOUT_MS)
    try:
        row = None
        while True:
            cursor = await db.execute(
                """
                SELECT *
                FROM environment_controls
                WHERE environment_id = ?
                  AND status = 'pending'
                  AND (bridge_id = '' OR bridge_id = ?)
                ORDER BY requested_at ASC
                LIMIT 1
                """,
                (req.environmentId, req.bridgeId),
            )
            candidate = await cursor.fetchone()
            if not candidate:
                return {"ok": True, "control": None}
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
            env = await env_cursor.fetchone()
            env_bridge_id = str((env["bridge_id"] if env else "") or "").strip()
            metadata = _json_loads_or(env["metadata"], {}) if env else {}
            bridge_started_at = metadata.get("bridgeStartedAt") or ""
            claimer_is_current_owner = bool(env_bridge_id) and env_bridge_id == req.bridgeId
            is_supersede_stop = (
                candidate["action"] == "stop"
                and str(candidate["requested_by"] or "") == "server:superseded-bridge"
            )
            requested_before_bridge_start = (
                _iso_to_epoch(candidate["requested_at"]) > 0
                and _iso_to_epoch(bridge_started_at) > 0
                and _iso_to_epoch(candidate["requested_at"]) < _iso_to_epoch(bridge_started_at)
            )
            # Void a stop the CURRENT env owner must never honor:
            #   1. A `server:superseded-bridge` stop that targets the current owner is
            #      self-contradictory — a bridge cannot be both the live owner AND a
            #      superseded predecessor. This is the race that self-terminated
            #      freshly-registered env bridges (2026-07-03): the supersede-stop was
            #      created at/after the bridge became current, so the timestamp guard
            #      (case 2) missed it and the current owner claimed its own stop and
            #      exited. 99 such controls had accumulated for a single env.
            #   2. Any stop requested BEFORE this bridge started — it predates this
            #      incarnation (the original stale guard).
            # An OPERATOR env-stop for the current owner (requested_by != superseded,
            # fresh timestamp) matches neither and correctly stops the bridge.
            if candidate["action"] == "stop" and claimer_is_current_owner and (
                is_supersede_stop or requested_before_bridge_start
            ):
                now = _now()
                reason = (
                    "superseded-bridge stop targeted the current live owner"
                    if is_supersede_stop
                    else f'requested before bridge "{req.bridgeId}" started'
                )
                await db.execute(
                    "UPDATE environment_controls SET status = 'failed', handled_at = ?, error = ? WHERE id = ? AND status = 'pending'",
                    (
                        now,
                        f"Stale stop control ignored: {reason}.",
                        candidate["id"],
                    ),
                )
                await db.commit()
                continue
            row = candidate
            break
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = 'claimed', machine_id = ?, claimed_at = ? WHERE id = ? AND status = 'pending'",
            (req.machineId or "", now, row["id"]),
        )
        await db.commit()
        return {
            "ok": True,
            "control": {
                "id": row["id"],
                "environmentId": row["environment_id"],
                "bridgeId": row["bridge_id"] or "",
                "action": row["action"],
                "requestedBy": row["requested_by"] or "",
                "requestedAt": row["requested_at"] or "",
                "currentEnvironment": _environment_record_to_dict(env) if env else None,
            },
        }
    finally:
        await db.close()
