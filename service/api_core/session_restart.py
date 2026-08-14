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

from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch
from service.api_core.records import _environment_record_to_dict
from service.api_core.settings import _load_settings
from service.api_core.spawn_request_state import _has_claimable_spawn_request
from service.api_core.workspace import _normalize_workspace_for_environment, _workspace_root_for


async def _prepare_restart_spawn(db, req, session, session_id: str, agent_id: str, action: str, now: str,
                                 coldstart_warnings, spawn_request_row, spawn_spec_row):
        """Resolve (or create) the spawn request a restart/reset will be served by.

        `test_control_session_split_is_inert.py` inlines this back and AST-compares against the
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
            spec_id = str(session["spawn_spec_id"] or "").strip()
            if not spec_id:
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
                )
                if not coldstarted and not await _has_claimable_spawn_request(db, agent_id):
                    raise HTTPException(
                        409,
                        (
                            f'Session "{session_id}" has no stored spawn spec and no online '
                            f'environment can host managed {session["runtime"] or "runtime"}.'
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
                resume_policy = "fresh_context" if action == "recreate" else "native_first"
                request_session_handle = "" if action == "recreate" else (session["session_handle"] or "")
                await db.execute(
                    """
                    INSERT INTO spawn_requests (
                        id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                        workspace, workspace_root, initial_message, priority, subject, mode,
                        resume_policy, status, session_handle, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request_id,
                        spec_id,
                        req.from_agent or "dashboard",
                        spawn_spec_row["environment_id"],
                        agent_id,
                        (agent_row["role"] if agent_row else "") or "coder",
                        (agent_row["name"] if agent_row else "") or agent_id,
                        spawn_spec_row["runtime"],
                        workspace,
                        workspace_root,
                        req.body or "",
                        req.priority or "normal",
                        req.subject or f"{action.title()} {agent_id}",
                        spawn_spec_row["mode"] or session["mode"] or "managed-warm",
                        resume_policy,
                        "queued",
                        request_session_handle,
                        now,
                        now,
                    ),
                )
                spawn_request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
                if action == "recreate":
                    await db.execute(
                        """
                        UPDATE agents
                        SET session_handle = '',
                            runtime_state = '{}',
                            last_seen = ?
                        WHERE id = ?
                        """,
                        (now, agent_id),
                    )
        return spawn_request_row, spawn_spec_row
