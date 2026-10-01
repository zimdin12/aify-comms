"""Preparing the spawn a session RESTART or RESET needs.

Extracted from `control_session` in v0.5.4. Its own module rather than an existing one because of the
import graph, not taste: the block calls `_coldstart_spawn_request_for_dispatch` from
`api_core/dispatch_start.py`, and `dispatch_start.py` already imports `api_core/spawn_request_state.py`
— so putting this beside `_has_claimable_spawn_request`, which is where it would naturally read, would
have closed a cycle. Nothing imports this module except the sessions router.

Restart and Reset differ in ONE thing and it is the whole point of the resume policy: a restart reuses
the saved backing, a reset discards it and starts a fresh context. Both go through the same spawn
request, so the policy is what carries the difference to the bridge.
"""
from __future__ import annotations

import time
import uuid

from fastapi import HTTPException

from service.api_core.definition_start import (
    CHANGED_WHILE_STARTING,
    StartRefused,
    insert_spawn_request,
    spec_columns,
    start_binding,
)
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch, twin_refusal
from service.api_core.dispatch_text import _coldstart_refusal_message
from service.api_core.managed_env import _select_online_environment_for_runtime
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import _runtime_capability_for_environment
from service.api_core.settings import _load_settings
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.start_intent import REPLACE
from service.api_core.workspace import (
    _normalize_workspace_for_environment,
    _workspace_for_environment,
    _workspace_root_for,
)


async def _prepare_restart_spawn(db, req, session, session_id: str, agent_id: str, action: str, now: str,
                                 coldstart_warnings, spawn_request_row, spawn_spec_row):
        """Resolve (or create) the spawn request a restart/reset will be served by.

        `test_control_session_split_is_inert.py` (retired in v0.6.13) inlined this back and AST-compared against the
        pre-split fixture, so the round trip is re-proved on every run. Body left at its original
        8-space column so the multi-line SQL literals inside are preserved byte-for-byte.

        TAKES BOTH ROWS IN AND RETURNS BOTH, rather than returning only what it computes. The caller
        initialises them to `None` before this runs, and the block is conditional — a helper that
        returned only its own results would overwrite the caller's `None`s with `None`s on the matching
        path and, worse, would need a second shape for the non-matching one. Passing them through keeps
        one shape and makes the no-op case genuinely a no-op.

        `coldstart_warnings` is a list that is APPENDED TO, so it does not need returning; the caller
        reports it either way.
        """
        if action in {"restart", "recreate"}:
            # A DEFINED agent restarts from its definition (P0 C5, C7), not from the session's old spec;
            # a withdrawn one does not restart (C6).
            try:
                binding = await start_binding(db, agent_id)
            except StartRefused as refused:
                raise HTTPException(409, str(refused))
            spec_id = str(session["spawn_spec_id"] or "").strip()
            if binding is not None:
                spawn_request_row, spawn_spec_row = await _bound_restart_spawn(
                    db, req, session, agent_id, action, now, binding,
                    preferred_environment_id=str(session["environment_id"] or ""))
            elif not spec_id:
                # FIX 5 (2026-06-03): a resident-origin session has a NULL spawn_spec,
                # yet the SEND path already auto-starts it via the cold-start helper.
                # Mirror that here instead of hard-erroring: cold-start a managed worker
                # (creates a queued spawn_request a bridge can claim), then continue to
                # the status-update tail with that queued/claimed spawn_request row. Only
                # raise when nothing can host it (no cold-start AND no claimable request).
                settings = await _load_settings(db)
                coldstarted = await _coldstart_spawn_request_for_dispatch(
                    db,
                    agent_id,
                    runtime=str(session["runtime"] or ""),
                    settings=settings,
                    requested_by=req.from_agent or "dashboard",
                    warnings=coldstart_warnings,
                    start_intent=REPLACE,
                )
                if not coldstarted and not await _has_claimable_spawn_request(db, agent_id):
                    # The refusal REASON is already in `coldstart_warnings` — the helper records
                    # which of its five causes fired. This raise used to discard it and assert one
                    # instead ("no online environment can host managed X"), which is the N8 shape:
                    # true only for the environment-resolution causes, false for a runtime that
                    # cannot be cold-started or a corrupt environment row. The success path reports
                    # these warnings (session_control.py), so the reason was being dropped on the
                    # ONE path where the operator needs it.
                    raise HTTPException(
                        409,
                        (
                            f'Session "{session_id}" has no stored spawn spec. '
                            + _coldstart_refusal_message(
                                coldstart_warnings, str(session["runtime"] or ""))
                        ),
                    )
                spawn_request_row = await (await db.execute(
                    """
                    SELECT *
                    FROM spawn_requests
                    WHERE agent_id = ?
                      AND status IN ('queued', 'claimed')
                    ORDER BY created_at DESC
                    LIMIT 1
                    """,
                    (agent_id,),
                )).fetchone()
                # Fall through to the shared status-update tail below.
                spawn_spec_row = None
            else:
                spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))
                spawn_spec_row = await spec_cursor.fetchone()
                if not spawn_spec_row:
                    raise HTTPException(409, f'Session "{session_id}" references missing spawn spec "{spec_id}"')
                env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (spawn_spec_row["environment_id"],))
                env_row = await env_cursor.fetchone()
                if not env_row:
                    raise HTTPException(409, f'Environment "{spawn_spec_row["environment_id"]}" is not available')

                agent_cursor = await db.execute("SELECT role, name FROM agents WHERE id = ?", (agent_id,))
                agent_row = await agent_cursor.fetchone()
                environment = _environment_record_to_dict(env_row)
                if str(environment.get("status") or "").lower() != "online":
                    raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}; assign a live environment before {action}.')
                workspace = _normalize_workspace_for_environment(environment, spawn_spec_row["workspace"] or session["workspace"] or "")
                workspace_root = _workspace_root_for(environment, workspace)
                request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
                # GUARDED like every start: the binding read above said "never defined", and a push or
                # withdrawal landing since must not be answered with a start from this old spec (review
                # of 8de83233, N6). The route commits once, at the end, so a refusal here undoes its
                # interrupt as well.
                inserted = await insert_spawn_request(db, {
                    "id": request_id,
                    "spawn_spec_id": spec_id,
                    "created_by": req.from_agent or "dashboard",
                    "environment_id": spawn_spec_row["environment_id"],
                    "agent_id": agent_id,
                    "role": (agent_row["role"] if agent_row else "") or "coder",
                    "name": (agent_row["name"] if agent_row else "") or agent_id,
                    "runtime": spawn_spec_row["runtime"],
                    "workspace": workspace,
                    "workspace_root": workspace_root,
                    "initial_message": req.body or "",
                    "priority": req.priority or "normal",
                    "subject": req.subject or f"{action.title()} {agent_id}",
                    "mode": spawn_spec_row["mode"] or session["mode"] or "managed-warm",
                    "resume_policy": "fresh_context" if action == "recreate" else "native_first",
                    "status": "queued",
                    "session_handle": "" if action == "recreate" else (session["session_handle"] or ""),
                    "created_at": now,
                    "updated_at": now,
                    "start_intent": REPLACE,
                }, None)
                if not inserted:
                    raise HTTPException(409, f'Agent "{agent_id}": {CHANGED_WHILE_STARTING}')
                spawn_request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
                if action == "recreate":
                    await _forget_native_session(db, agent_id, now)
        return spawn_request_row, spawn_spec_row


async def _forget_native_session(db, agent_id: str, now: str) -> None:
    """A recreate starts a fresh context, so the agent no longer names the native session it had."""
    await db.execute("UPDATE agents SET session_handle = '', runtime_state = '{}', last_seen = ? WHERE id = ?",
                     (now, agent_id))


async def _bound_restart_spawn(db, req, session, agent_id: str, action: str, now: str, binding,
                               *, preferred_environment_id: str):
    """The spawn a defined agent's restart or recreate is served by: built from `binding`, on an online
    environment of the machine that defines it (the session's own when it is one), recording the
    revision it was built from. Raises 409 when none can be made."""
    agent_row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
    twin = await twin_refusal(db, agent_row)
    if twin:
        raise HTTPException(409, f'Agent "{agent_id}": {twin}')
    settings = await _load_settings(db)
    offline_seconds = settings.get("environment_offline_seconds", 90)
    environment = None
    preferred = await (await db.execute(
        "SELECT * FROM environments WHERE id = ?", (preferred_environment_id,))).fetchone()
    if preferred is not None:
        candidate = _environment_record_to_dict(preferred, offline_seconds=offline_seconds)
        if (str(candidate.get("status") or "").lower() == "online"
                and candidate.get("machineId") == binding.machine_id
                and _runtime_capability_for_environment(candidate, binding.runtime)):
            environment = candidate
    if environment is None:
        environment = await _select_online_environment_for_runtime(
            db, binding.runtime, offline_seconds=offline_seconds, machine_id=binding.machine_id)
    if environment is None:
        raise HTTPException(409, f'Agent "{agent_id}" is defined on {binding.machine_id}, and no online environment '
                                 f'there can start "{binding.runtime}"; start aify-env on that host')
    workspace, workspace_root = _workspace_for_environment(environment, binding.workspace)
    spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    spec = {"id": spec_id, "agent_id": agent_id, "environment_id": environment["id"], "workspace": workspace,
            "created_at": now, "updated_at": now, **spec_columns(binding)}
    await db.execute(f"INSERT INTO spawn_specs ({', '.join(spec)}) VALUES ({', '.join('?' * len(spec))})",
                     tuple(spec.values()))
    request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    inserted = await insert_spawn_request(db, {
        "id": request_id, "spawn_spec_id": spec_id, "created_by": req.from_agent or "dashboard",
        "environment_id": environment["id"], "agent_id": agent_id, "role": binding.role, "name": binding.name,
        "runtime": binding.runtime, "workspace": workspace, "workspace_root": workspace_root,
        "initial_message": req.body or "", "priority": req.priority or "normal",
        "subject": req.subject or f"{action.title()} {agent_id}", "mode": "managed-warm",
        "resume_policy": "fresh_context" if action == "recreate" else "native_first", "status": "queued",
        "session_handle": "" if action == "recreate" else (session["session_handle"] or ""),
        "created_at": now, "updated_at": now, "start_intent": REPLACE,
    }, binding)
    if not inserted:
        raise HTTPException(409, f'Agent "{agent_id}": {CHANGED_WHILE_STARTING}')
    if action == "recreate":
        await _forget_native_session(db, agent_id, now)
    request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
    return request_row, await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))).fetchone()
