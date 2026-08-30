"""The `spawn-requests` route domain: create, list, claim and update a spawn request.

v0.5.2g. FOUR HANDLERS MOVE; FOURTEEN HELPERS DO NOT. This tag is explicitly "handlers move, helpers
borrowed" and NOT "the spawn family is retired" — the reviewer required that distinction be stated
rather than implied, because fourteen undocumented borrows is how carry debt becomes invisible
permanent architecture.

Every borrow below was measured, not assumed: each still has users in domains that have not moved.
None is copied; each is reached through a function-scope import so there is exactly one owner and no
module-level cycle.

BORROW TABLE, and the retirement map that stops this being permanent. TEN OF THE ORIGINAL TWELVE
BORROWS RETIRED IN v0.5.4, not by their owning domains moving as predicted, but by the block that
used them leaving this module. What remains:

    _environment_record_to_dict             retires with: agents, sessions
    _normalize_workspace_for_environment    retires with: sessions
    _workspace_root_for                     retires with: sessions

The user counts that stood beside each name are deliberately gone rather than updated: they were
measured once and never re-measured, so every one of them was a claim about the tree at the moment
the table was typed. Count them when you need them.

`_claim_spawn_request_once` had no users outside this domain and moved with the handlers.

`update_spawn_request` WAS 384 lines and moved WHOLE, byte-identical, in v0.5.2g. In v0.5.4 its
299-line `status == "running"` branch left for `service/api_core/running_spawn.py`, proved by
`test_update_spawn_request_split_is_inert.py`. The handler is now the request-status bookkeeping
around that one transition.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.claim_emptiness import spawn_request_is_empty
from service.api_core.running_spawn import _settle_running_spawn
from service.api_core.routing import domain_router
from service.api_core.runtime import (
    _normalize_runtime,
    _runtime_capability_for_environment,
    _runtime_unlaunchable_reason,
)
from service.api_core.records import _environment_record_to_dict
from service.env_status import environment_has_live_bridge as _environment_has_live_bridge
from service.env_status import (
    BRIDGE_STAMP_INVALID, BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS, SPAWN_CLAIMER_FRESH_SECONDS,
    bridge_stamp_state as _bridge_stamp_state,
)
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.models import SpawnRequestClaim, SpawnRequestCreate, SpawnRequestUpdate
from service.api_core.workspace import _normalize_workspace_for_environment, _workspace_root_for
from service.api_core.spawn_requests_io import (
    _claim_spawn_request_once,
    _spawn_request_to_dict,
    _spawn_spec_to_dict,
)

logger = logging.getLogger("aify_comms.routers.spawn_requests")


async def _a_live_bridge_row_exists(db, bridge_id: str) -> bool:
    """Is the bridge that registered this environment still live?

    THE AUTHORITY for "could anything here claim a spawn". `environments.bridge_id` names that
    bridge, which is the same identity `metadata.bridgeLastSeen` tracks -- so this asks the same
    question the stamp answers, of the table that cannot be written by a host advertiser.

    The predicate is the one the turn lease and the dead-bridge sweep already share: the row must
    exist, must NOT be superseded (a replaced bridge keeps heartbeating, because supersession is a
    server-side fact it is never told about), and must have beaten inside
    `SPAWN_CLAIMER_FRESH_SECONDS` -- the SAME window the stamped arm ages against. They used to
    differ (90 against 120), so one bridge at age 100s was live before it gained a stamp and dead
    after, with nothing about the bridge having changed.

    FAILS CLOSED. An empty `bridge_id` or an unreadable table is not evidence that a bridge is
    there, so the caller refuses the spawn with a diagnostic rather than accepting one nothing can
    claim -- which is the strand this whole gate exists to prevent.
    """
    owner = str(bridge_id or "").strip()
    if not owner:
        return False
    try:
        row = await (await db.execute(
            """
            SELECT 1 FROM bridge_instances
            WHERE id = ?
              AND COALESCE(superseded_by, '') = ''
              AND datetime(last_seen) > datetime('now', ?)
              -- ...and not WILDLY ahead of us. `> now - stale` is satisfied for ever by a stamp
              -- hours in the future, so one bad write would make a dead bridge authorize spawns
              -- permanently. The tolerance is not zero: a strict no-future rule is what made
              -- doctor call every environment dead over a 4.1s container clock offset.
              AND datetime(last_seen) <= datetime('now', ?)
            LIMIT 1
            """,
            (
                owner,
                f"-{SPAWN_CLAIMER_FRESH_SECONDS} seconds",
                f"+{BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS} seconds",
            ),
        )).fetchone()
    except Exception:
        return False
    return row is not None



router = domain_router()



#: EVERY STATUS A SPAWN REQUEST CAN HOLD, in the order it moves through them.
#:
#: `queued` is the column default and arrives with the row; the other five are what
#: `PATCH /spawn-requests/{id}` accepts, and it answers 400 for anything else. This set existed as a
#: literal at that validation site with `_SPAWN_TERMINAL_STATUSES` three hundred lines above holding
#: a subset -- two spellings of one vocabulary, neither naming the other, and `queued` in neither.
#:
#: It is named so a test can read it. `service/tests/test_the_spawn_panel_names_real_statuses.py`
#: compares it against the words the dashboard's spawn panel puts in front of an operator, which is
#: how the panel's promise of a "completed" spawn -- a state the service refuses -- was found.
SPAWN_REQUEST_STATUSES = ("queued", "claimed", "starting", "running", "failed", "cancelled")

#: The subset a bridge may PATCH. `queued` is not one: nothing moves a request BACK to unclaimed.
SPAWN_REQUEST_PATCHABLE_STATUSES = frozenset(SPAWN_REQUEST_STATUSES) - {"queued"}

# Domain-local: after the handlers moved, nothing outside this module referenced either.
_SPAWN_TERMINAL_STATUSES = {"running", "failed", "cancelled"}
_SPAWN_MODES = {"managed-warm"}








# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.





# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/longpoll.py in v0.5.4 — the module that
# already owned the other waiter registry — so a plain import works.



@router.get("/spawn-requests")
async def list_spawn_requests(
    request: Request,
    status: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    db = await get_db()
    try:
        # Read-path-write fix (2026-06-29): these two WRITE repairs used to run on EVERY dashboard
        # poll of this GET endpoint (~every 15s), opening write transactions that contended with all
        # concurrent reads — the #1 SLOW-REQ source and a "database is locked" contributor. They now
        # run in the 60s reconcile loop instead; this endpoint is a pure read.
        where = []
        params: list[Any] = []
        if status:
            where.append("sr.status = ?")
            params.append(status)
        if environmentId:
            where.append("sr.environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"""
            SELECT sr.*, ss.id AS spec_row_id
            FROM spawn_requests sr
            LEFT JOIN spawn_specs ss ON ss.id = sr.spawn_spec_id
            {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ?
            """,
            (*params, limit + 1),
        )
        # ONE ROW WIDER THAN THE PAGE, so the response can say whether this is the whole answer. The
        # dashboard asks for 200 and renders exactly what it gets; without this the Environments page
        # cannot tell a complete spawn history from a window onto it. Costs nothing -- the spec batch
        # below already reads only the rows kept.
        rows = await cursor.fetchall()
        truncated = len(rows) > limit
        rows = rows[:limit]
        # ONE query for every spec, not one per row. This loop used to run a separate
        # `SELECT * FROM spawn_specs WHERE id = ?` for each row -- at the dashboard's limit=200 that
        # is 200 extra round trips on EVERY poll, and the dashboard polls about every 15 seconds.
        # Measured 2026-08-25 before the change: 6.1ms at limit=1 against 74.3ms at limit=200, so
        # roughly 0.35ms per row of purely per-row work on a service that is deliberately
        # single-worker and whose recurring failure is write-lock contention.
        #
        # The JOIN above already reaches spawn_specs and keeps only `ss.id AS spec_row_id`. Widening
        # it to `ss.*` would be fewer queries still, but sr and ss share column names (id,
        # created_at), and in a sqlite Row the later duplicate wins silently -- a spec's id landing
        # in the request's id is the kind of corruption that reads as data, not as an error. A
        # second batched read costs one round trip and cannot collide.
        spec_ids = [row["spawn_spec_id"] for row in rows if row["spawn_spec_id"]]
        specs: dict[Any, Any] = {}
        if spec_ids:
            unique = list(dict.fromkeys(spec_ids))  # order-stable, and one bind per distinct id
            placeholders = ",".join("?" * len(unique))
            spec_cursor = await db.execute(
                f"SELECT * FROM spawn_specs WHERE id IN ({placeholders})", unique,
            )
            for spec_row in await spec_cursor.fetchall():
                # WITHOUT THE INSTRUCTIONS BODY. It is 34.2% of this endpoint and 21.2% of the
                # dashboard's whole refresh bundle, and the only spawnSpec field any consumer of
                # this list reads is `metadata`. The claim path still carries it, which is where
                # the bridge gets the prompt from.
                specs[spec_row["id"]] = _spawn_spec_to_dict(spec_row, include_instructions=False)
        result = []
        for row in rows:
            result.append(_spawn_request_to_dict(row, specs.get(row["spawn_spec_id"])))
        return {"ok": True, "spawnRequests": result, "truncated": truncated, "limit": limit}
    finally:
        await db.close()


@router.post("/spawn-requests")
async def create_spawn_request(req: SpawnRequestCreate, request: Request):
    validate_name(req.agentId, "agent ID")
    normalized_runtime = _normalize_runtime(req.runtime)
    mode = str(req.mode or "managed-warm").strip()
    if mode not in _SPAWN_MODES:
        raise HTTPException(400, f'Unsupported spawn mode "{mode}"')

    db = await get_db()
    try:
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{req.environmentId}" is {environment.get("status") or "unknown"}; restart its bridge before spawning.')
        # ONLINE IS NOT THE SAME AS CLAIMABLE since aify-env began heartbeating this row. It describes
        # the host, which keeps `last_seen` fresh and the status `online`, while the thing that CLAIMS
        # a spawn request is the environment bridge. With no bridge the request was accepted and sat
        # `queued` for ever, with no error anywhere -- measured on the deployed system, and it went
        # `running` the instant a bridge started.
        # The same window the status check one line up used: `_environment_record_to_dict` was called
        # with its default, and reading settings here would add a query to a path that already knows
        # the answer it needs.
        #
        # AN ABSENT STAMP IS RESOLVED AGAINST THE AUTHORITY, not assumed. Every row registered
        # before `bridgeLastSeen` existed has none, and the first version of this gate read that as
        # "unknown, and unknown means yes" -- so a legacy row was treated as having a live bridge for
        # ever, which is the queued-for-ever strand this gate exists to prevent, reintroduced through
        # the gate itself. `bridge_instances` is the authority (the same table the turn lease
        # consults), so the question is answered with evidence rather than with a grace period that
        # would need an expiry and a doctor row of its own.
        bridge_rows_say_live = await _a_live_bridge_row_exists(db, environment.get("bridgeId"))
        if not _environment_has_live_bridge(environment, bridge_rows_say_live=bridge_rows_say_live):
            # WHY, not just NO. The three refusals send an operator to three different places, and a
            # single message would send them to the wrong two thirds of the time: a stale stamp means
            # start the bridge, an absent one with no bridge row means the same, and an UNPARSEABLE
            # stamp means the row is corrupt and starting a bridge will not fix it.
            state = _bridge_stamp_state(environment)
            if state == BRIDGE_STAMP_INVALID:
                raise HTTPException(
                    409,
                    f'Environment "{req.environmentId}" has an unreadable bridge timestamp, so this '
                    f'service cannot tell whether a bridge is live. That is corrupt row data rather '
                    f'than a missing bridge -- starting one will not clear it. Re-register the '
                    f'environment, or check `metadata.bridgeLastSeen` on that row.',
                )
            raise HTTPException(
                409,
                f'Environment "{req.environmentId}" is described by aify-env but has no live '
                f'environment bridge, and only a bridge claims a spawn. Run `aify-comms` on that host, '
                f'then retry.',
            )
        runtime_capability = _runtime_capability_for_environment(environment, normalized_runtime)
        if not runtime_capability:
            raise HTTPException(400, f'Environment "{req.environmentId}" does not advertise runtime "{normalized_runtime}"')
        # SAYING NO EARLY, WITH THE HOST'S OWN REASON. Accepting this spawn meant the launcher was not
        # found minutes later, by the tier that runs it, and reported as "the agent did not start".
        unlaunchable = _runtime_unlaunchable_reason(environment, normalized_runtime)
        if unlaunchable:
            raise HTTPException(
                409,
                f'Environment "{req.environmentId}" cannot launch runtime "{normalized_runtime}". {unlaunchable}',
            )
        workspace = _normalize_workspace_for_environment(environment, req.workspace or "")
        workspace_root = _workspace_root_for(environment, workspace)
        if not workspace and workspace_root:
            workspace = workspace_root
        settings = await _load_settings(db)
        model = str(req.model or "").strip()
        if not model:
            if normalized_runtime == "codex":
                model = str(settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"])).strip()
            elif normalized_runtime == "claude-code":
                model = str(settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"])).strip()
            elif normalized_runtime == "pi":
                model = str(settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"])).strip()
        runtime_config = req.runtimeConfig or {}
        if normalized_runtime == "codex" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]).strip()}
        elif normalized_runtime == "claude-code" and not str(runtime_config.get("effort") or "").strip():
            runtime_config = {**runtime_config, "effort": str(settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]).strip()}
        elif normalized_runtime == "pi" and not str(runtime_config.get("effort") or runtime_config.get("thinking") or "").strip():
            pi_effort = str(settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]).strip()
            if pi_effort:
                runtime_config = {**runtime_config, "effort": pi_effort}
        metadata = req.metadata or {}
        if runtime_config:
            metadata = {**metadata, "runtimeConfig": runtime_config}

        now = _now()
        spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO spawn_specs (
                id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                context_policy, restart_policy, metadata, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                spec_id,
                req.agentId,
                req.environmentId,
                normalized_runtime,
                workspace,
                model,
                req.profile or "",
                mode,
                req.systemPrompt or "",
                req.instructions or "",
                json.dumps(req.envVars or {}),
                json.dumps(req.channelIds or []),
                json.dumps(req.budgetPolicy or {}),
                json.dumps(req.contextPolicy or {}),
                json.dumps(req.restartPolicy or {}),
                json.dumps(metadata),
                now,
                now,
            ),
        )
        await db.execute(
            """
            INSERT INTO spawn_requests (
                id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                workspace, workspace_root, initial_message, priority, subject, mode,
                resume_policy, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                spec_id,
                req.createdBy or "dashboard",
                req.environmentId,
                req.agentId,
                req.role or "coder",
                req.name or req.agentId,
                normalized_runtime,
                workspace,
                workspace_root,
                req.initialMessage or "",
                req.priority or "normal",
                req.subject or "",
                mode,
                req.resumePolicy or "native_first",
                "queued",
                now,
                now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
        spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_created", {"spawnRequestId": request_id, "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(row, _spawn_spec_to_dict(spec))}
    finally:
        await db.close()


@router.post("/spawn-requests/claim")
async def claim_spawn_request(req: SpawnRequestClaim, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only when there
    # is nothing to spawn; a claimed request OR a blockedBy directive returns immediately.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_spawn_request_once(req, request),
        spawn_request_is_empty,
        scope="spawn",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "spawnRequest": None},
    )




@router.patch("/spawn-requests/{spawn_request_id}")
async def update_spawn_request(spawn_request_id: str, req: SpawnRequestUpdate, request: Request):
    status_value = str(req.status or "").strip().lower()
    if status_value not in SPAWN_REQUEST_PATCHABLE_STATUSES:
        raise HTTPException(400, f'Unsupported spawn request status "{req.status}"')
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f'Spawn request "{spawn_request_id}" not found')
        current_status = str(row["status"] or "").strip().lower()
        if current_status in {"failed", "cancelled"} and status_value != current_status:
            raise HTTPException(
                409,
                f'Spawn request "{spawn_request_id}" is already {current_status}; late bridge update "{status_value}" was ignored.',
            )
        if req.bridgeId and row["claimed_by_bridge_id"] and row["claimed_by_bridge_id"] != req.bridgeId:
            raise HTTPException(409, f'Spawn request "{spawn_request_id}" is claimed by another bridge')

        now = _now()
        session_id = row["session_id"] or ""
        finished_at = row["finished_at"]
        started_at = row["started_at"]
        if status_value == "starting" and not started_at:
            started_at = now
        if status_value in _SPAWN_TERMINAL_STATUSES:
            finished_at = now if status_value in {"failed", "cancelled"} else finished_at

        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))).fetchone()
        if not spec_row:
            raise HTTPException(500, f'Spawn spec "{row["spawn_spec_id"]}" missing')

        runtime_state = req.runtimeState or {}
        if req.bridgeId:
            runtime_state = {**runtime_state, "bridgeInstanceId": req.bridgeId}

        session_id = await _settle_running_spawn(
            db, req, row, spec_row, now, started_at, status_value, session_id, runtime_state
        )

        # TOCTOU guard (bughunt 2026-07-03): the status check above read `current_status`
        # ONCE; between that read and this write a concurrent operator Stop/CLI-takeover
        # can commit status='cancelled'. Without a WHERE guard this write would clobber it
        # back to 'running' AFTER the PTY was already spawned — silently losing the Stop and
        # leaving a live zombie worker. Make the write CONDITIONAL on the row not already
        # being terminal; a 0-rowcount means a concurrent finalize won, so we return that
        # real state instead of the phantom success (and skip the running/registered casts).
        upd = await db.execute(
            """
            UPDATE spawn_requests
            SET status = ?, process_id = ?, session_handle = ?, session_id = ?, error = ?,
                updated_at = ?, started_at = ?, finished_at = ?
            WHERE id = ? AND status NOT IN ('cancelled', 'failed')
            """,
            (
                status_value,
                req.processId or row["process_id"] or "",
                req.sessionHandle or row["session_handle"] or "",
                session_id,
                req.error or "",
                now,
                started_at,
                finished_at,
                spawn_request_id,
            ),
        )
        await db.commit()
        if (upd.rowcount or 0) == 0 and status_value not in {"cancelled", "failed"}:
            # A concurrent Stop/fail finalized the row first — honor it, don't resurrect.
            concurrent = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
            concurrent_status = str((concurrent["status"] if concurrent else "") or "").strip().lower()
            if concurrent_status in {"cancelled", "failed"}:
                raise HTTPException(
                    409,
                    f'Spawn request "{spawn_request_id}" was concurrently {concurrent_status}; the "{status_value}" update was dropped to avoid resurrecting a stopped worker.',
                )
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
        updated_spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_updated", {"spawnRequestId": spawn_request_id, "status": status_value})
            if status_value == "running":
                await ws.broadcast("agent_registered", {"agentId": row["agent_id"], "runtime": row["runtime"], "sessionMode": "managed"})
                if row["status"] != "running" and str(row["initial_message"] or "").strip():
                    await ws.broadcast("dispatch_queued", {"targetAgentId": row["agent_id"]})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(updated_spec) if updated_spec else None)}
    finally:
        await db.close()
