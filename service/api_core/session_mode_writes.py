"""The single UPDATE that moves an agent between resident and managed.

Extracted from `switch_agent_session_mode` (`service/routers/agents/session_mode.py`) in v0.5.4 —
forty-two lines of one SQL statement writing sixteen columns, sitting in the middle of a 251-line
handler. Not a decision: every gate has already run by the time it executes. It counted because a
large opaque write is what makes the decisions around it hard to read.

THE THREE OTHER PIECES OF THIS HANDLER ALREADY HAVE HOMES — `session_mode_gates.py`,
`session_mode_env_binding.py`, `session_mode_audit.py`. This is the WRITE the gates guard and the
audit records, so it gets its own module beside them rather than joining any of them: a gate module
that also performs the mutation it gates is the arrangement those splits existed to undo.

Body at its ORIGINAL COLUMN: the statement contains triple-quoted SQL, and dedenting would rewrite the
string contents and make the round trip unprovable.

DB ACCESS: `db` is passed in, nothing opens a connection or commits — this joins its caller's
transaction.
"""
from __future__ import annotations

import json


async def _apply_session_mode_switch_to_agent(
    db, agent_id, new_mode, current_mode,
    runtime, effective_runtime, runtime_config, runtime_state,
    capabilities, switch_session_handle, next_cwd, next_launch_mode,
    next_machine_id, resident_candidate, requested_by, now,
):
        """Write the new mode and every column that moves with it."""
        await db.execute(
            """
            UPDATE agents
            SET session_mode = ?,
                runtime = ?,
                launch_mode = ?,
                session_handle = ?,
                machine_id = ?,
                cwd = ?,
                capabilities = ?,
                runtime_config = ?,
                runtime_state = ?,
                driver_state = ?,
                status = CASE WHEN status = 'stopped' THEN 'idle' ELSE status END,
                status_note = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (
                new_mode,
                effective_runtime,
                next_launch_mode,
                switch_session_handle,
                next_machine_id,
                next_cwd,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                json.dumps(runtime_state),
                # Switching TO resident while adopting a LIVE resident bridge keeps that
                # session as the active driver. The previous unconditional 'idle' clobbered
                # the 'driving' the just-registered resident session had set, so its OWN
                # channel sidecar was told to RELEASE on its next claim/heartbeat and
                # resident delivery silently died — sends said "sent", runs queued forever
                # (sc-manager, 2026-06-12: launch terminal first, click switch second).
                ("driving" if (new_mode == "resident" and str(resident_candidate.get("bridgeId") or "").strip()) else "idle"),
                f"Manually switched from {current_mode} to {new_mode} by {requested_by}"
                + (f" (runtime {runtime}->{effective_runtime})" if effective_runtime != runtime else "")
                + ".",
                now,
                agent_id,
            ),
        )
