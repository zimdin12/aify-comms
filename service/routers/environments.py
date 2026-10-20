"""The `environments` route domain: registration heartbeat, roots, and environment controls.

v0.5.2f. Six handlers and three domain-local helpers. The constant that came with them,
SUPERSEDE_STOP_STALE_SECONDS, left again in v0.5.4 with the only block that read it.

This is the first domain whose handlers WRITE state that the fleet depends on — the heartbeat is how
an environment bridge stays ONLINE, and `aify-comms doctor`'s `env-bridge` check keys on exactly that
status. So the domain bars matter more here than they did for stats: every handler is byte-identical,
and the mutating routes stay on `JsonApiRoute` and keep their SQLite write-lock retry.

THE HEARTBEAT'S BEHAVIOUR IS NOT HERE (audit item S2). What a beat says is read in
`service/api_core/environment_beat.py` and its transaction runs in
`service/api_core/environment_heartbeat.py`; the handler below validates the id, opens the connection,
turns a host-proof refusal into a 403 and broadcasts an accepted beat.

BORROWED, measured: `_environment_record_to_dict` has nine users outside this domain, so it is
reached through a function-scope import rather than copied. Everything else the handlers touch is
already leaf-owned.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException, Request

from service import longpoll
from service.api_core.claim_emptiness import environment_control_is_empty
from service.environment_claim import _claim_environment_control_once
from service.api_core.environment_beat import EnvironmentBeat, _derived_environment_id, _normalize_roots
from service.api_core.environment_heartbeat import record_environment_heartbeat
from service.api_core.host_proof import presented_proof
from service.api_core.operator_authz import recorded_operator_actor
from service.api_core.routing import domain_router
from service.api_core.records import _environment_record_to_dict
from service.api_core.definition_freshness import definition_freshness
from service.api_core.definition_guard import defined_among_sessions_on
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.models import (
    EnvironmentControlClaim,
    EnvironmentControlRequest,
    EnvironmentControlUpdate,
    EnvironmentHeartbeat,
    EnvironmentRootsUpdate,
)
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

router = domain_router()


@router.get("/environments")
async def list_environments(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM environments WHERE status != 'forgotten'")
        # WHETHER EACH HOST STILL PUSHES ITS DEFINITIONS (P0 C11): null for an environment no store pushes from.
        stores = {row["environment_id"]: row for row in await (await db.execute(
            "SELECT machine_id, store_id, revision, environment_id, updated_at, pushed_at FROM definition_stores")).fetchall()}
        now = _now()
        environments = [
            _environment_record_to_dict(
                row, offline_seconds=settings.get("environment_offline_seconds", 90),
                definitions=definition_freshness(stores[row["id"]], now) if row["id"] in stores else None)
            for row in await cursor.fetchall()
        ]
        status_rank = {"online": 0, "degraded": 1, "unknown": 2, "offline": 3, "disabled": 4}
        environments.sort(key=lambda env: (status_rank.get(env.get("status") or "", 5), str(env.get("label") or "").lower(), str(env.get("id") or "").lower()))
        return {"ok": True, "environments": environments}
    finally:
        await db.close()


@router.post("/environments/heartbeat")
async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):
    env_id = str(req.id or "").strip() or _derived_environment_id(req.kind, req.hostname)
    if not env_id:
        raise HTTPException(400, "Environment id is required")
    beat = EnvironmentBeat.from_request(req, env_id)
    db = await get_db()
    try:
        outcome = await record_environment_heartbeat(db, beat, presented_proof(request))
        if outcome.refusal:
            raise HTTPException(403, f"environment heartbeat refused: {outcome.refusal}")
        if outcome.written:
            ws = await _get_ws(request)
            if ws:
                await ws.broadcast("environment_heartbeat", {"environmentId": env_id, "bridgeId": req.bridgeId or ""})
        return outcome.body
    finally:
        await db.close()


@router.patch("/environments/{environment_id:path}/roots")
async def update_environment_roots(environment_id: str, req: EnvironmentRootsUpdate, request: Request):
    actor = recorded_operator_actor(req.requestedBy, request, action="changing an environment's roots as the operator")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        metadata = _json_loads_or(env["metadata"], {})
        if req.resetToBridgeAdvertised:
            roots = _normalize_roots(metadata.get("advertisedCwdRoots") or _json_loads_or(env["cwd_roots"], []))
            next_metadata = {k: v for k, v in metadata.items() if k not in {"manualRoots", "manualRootsUpdatedAt", "manualRootsUpdatedBy"}}
            next_metadata["manualRoots"] = False
            next_metadata["manualRootsResetAt"] = now
            next_metadata["manualRootsResetBy"] = actor
        else:
            roots = _normalize_roots(req.roots or [])
            if not roots:
                raise HTTPException(400, "At least one root is required. Use resetToBridgeAdvertised to return to bridge-advertised roots.")
            next_metadata = {
                **metadata,
                "manualRoots": True,
                "manualRootsUpdatedAt": now,
                "manualRootsUpdatedBy": actor,
                "previousCwdRoots": _json_loads_or(env["cwd_roots"], []),
            }
        await db.execute(
            """
            UPDATE environments
            SET cwd_roots = ?,
                metadata = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(roots), json.dumps(next_metadata), now, environment_id),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_roots_updated", {"environmentId": environment_id})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


@router.post("/environments/{environment_id:path}/control")
async def control_environment(environment_id: str, req: EnvironmentControlRequest, request: Request):
    actor = recorded_operator_actor(req.requestedBy, request, action="stopping or forgetting an environment as the operator")
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "forget"}:
        raise HTTPException(400, "Environment control action must be stop or forget")
    db = await get_db()
    try:
        # The write lock first, as a definition push takes it, so no push commits between a check below and the
        # writes it guards: forget's governing check (0.8 whole-range review, R2) and stop's defined agents (B2).
        if action in {"stop", "forget"}:
            await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            await db.rollback()
            raise HTTPException(404, "Environment not found")
        now = _now()
        if action == "forget":
            # NOT WHILE ITS DEFINITIONS GOVERN (P0 C11). A forgotten environment leaves the list, and with it
            # the only place that says whether its host still refreshes the definitions it pushed, while
            # those definitions keep governing (0.8 whole-range review, R2). Release them first.
            governing = await (await db.execute(
                "SELECT s.machine_id, COUNT(d.agent_id) AS n FROM definition_stores s "
                "JOIN agent_definitions d ON d.machine_id = s.machine_id WHERE s.environment_id = ? "
                "GROUP BY s.machine_id", (environment_id,))).fetchall()
            if governing:
                held = ", ".join(f"{row['n']} from {row['machine_id']}" for row in governing)
                await db.rollback()
                raise HTTPException(409, f"environment {environment_id} still governs agent definitions ({held}): "
                                         "forgetting it would hide whether they are still refreshed. Remove them in "
                                         "aify-env on that machine, or release each one (POST "
                                         "/agent-definitions/<id>/release with its machineId), then forget it.")
            await db.execute("DELETE FROM environment_controls WHERE environment_id = ?", (environment_id,))
            await db.execute(
                """
                UPDATE environments
                SET status = 'forgotten',
                    bridge_id = '',
                    bridge_version = '',
                    runtimes = '[]',
                    metadata = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (json.dumps({**_json_loads_or(env["metadata"], {}), "forgottenAt": now, "forgottenBy": actor}), now, environment_id),
            )
            await db.commit()
            ws = await _get_ws(request)
            if ws: await ws.broadcast("environment_forgotten", {"environmentId": environment_id})
            return {"ok": True, "action": action, "environmentId": environment_id}

        # NOT WHILE IT WOULD DISABLE A DEFINED AGENT (B2). The writes below reach every agent with any session here,
        # and nothing on the host consumes the control, so a defined agent would read disabled while aify-env runs it.
        defined = await defined_among_sessions_on(db, environment_id)
        if defined:
            await db.rollback()
            raise HTTPException(409, f"environment {environment_id} has sessions of agents a host defines "
                                     f"({', '.join(defined)}): stopping it would disable them here while their host "
                                     "keeps running them. Their lifecycle is their host's: stop them in aify-env on "
                                     "that machine. This stop stays refused while any of them is defined.")
        control_id = f"envctl-{uuid.uuid4().hex}"
        await db.execute(
            """
            INSERT INTO environment_controls (
                id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                control_id,
                environment_id,
                env["bridge_id"] or "",
                env["machine_id"] or "",
                action,
                "pending",
                actor,
                now,
            ),
        )
        await db.execute("UPDATE environments SET status = ? WHERE id = ?", ("disabled", environment_id))
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'lost',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE environment_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting')
            """,
            (now, now, environment_id),
        )
        await db.execute(
            """
            UPDATE agents
            SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                launch_mode = 'none',
                runtime_state = '{}',
                last_seen = ?
            WHERE id IN (SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?)
            """,
            (now, environment_id),
        )
        # `offline` is not a manual-status short-circuit, so the live-status cache
        # would otherwise keep serving the old status for these bound agents until
        # the 60s sweep. Invalidate each so the disable reflects immediately.
        bound_rows = await (await db.execute(
            "SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?",
            (environment_id,),
        )).fetchall()
        for bound in bound_rows:
            bound_agent = str(bound["agent_id"] or "").strip()
            if bound_agent:
                await _invalidate_agent_live_state(db, bound_agent)
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_requested", {"environmentId": environment_id, "action": action})
        return {"ok": True, "controlId": control_id, "action": action, "environmentId": environment_id}
    finally:
        await db.close()


@router.post("/environments/controls/claim")
async def claim_environment_control(req: EnvironmentControlClaim, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only on the
    # exact "nothing pending" shape; a claimed control (has controlId) returns at once.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_environment_control_once(req),
        environment_control_is_empty,
        scope="env-control",
        fallback_s=3.0,
        request=request,
        lock_result={"ok": True, "control": None},
    )


@router.patch("/environments/controls/{control_id}")
async def update_environment_control(control_id: str, req: EnvironmentControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, "Environment control status must be completed or failed")
    db = await get_db()
    try:
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = ?, handled_at = ?, error = ? WHERE id = ?",
            (status, now, req.error or "", control_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_updated", {"controlId": control_id, "status": status})
        return {"ok": True, "controlId": control_id, "status": status}
    finally:
        await db.close()
