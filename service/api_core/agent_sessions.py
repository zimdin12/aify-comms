"""Agent sessions: which row is CURRENT, who owns a handle, and tombstoning. Leaf module.

Layer-0 slice of the v0.5.4 decomposition, and the second DB-touching one.

A word on what "session" means here, because the name has misled before: `agent_sessions` rows are
PROCESSES, not conversations. One hermes conversation resumed by 75 boots produced 79 rows, and the
real defect that time was a query returning all history so a dead row could shadow the live one. That
is why `_current_agent_session_row` exists and why its ENDED-status filter is the interesting part of
this module rather than an implementation detail.

The three ENDED-status constants came WITH it: `ENDED_AGENT_SESSION_STATUSES` is read only to derive
`_ENDED_AGENT_SESSION_STATUS_PARAMS`, which is read only to derive the placeholder string, which is
read only by `_current_agent_session_row`. The whole chain had exactly one consumer, measured with
scripts/constant_readership.py, so it is a sole-reader move end to end.

DB ACCESS: `db` is passed to every function, reads and writes are issued on it, and none opens a
connection, commits, or rolls back — each joins its caller's transaction. That is the reviewer's
condition for moving a DB-touching helper.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from service.api_core.runtime_state import _runtime_state_with_handle
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now


ENDED_AGENT_SESSION_STATUSES = {
    "ended",
    "completed",
    "cancelled",
    "stopped",
    "failed",
    "lost",
}
_ENDED_AGENT_SESSION_STATUS_PARAMS = tuple(sorted(ENDED_AGENT_SESSION_STATUSES))
_ENDED_AGENT_SESSION_STATUS_PLACEHOLDERS = ", ".join("?" * len(_ENDED_AGENT_SESSION_STATUS_PARAMS))


async def _touch_agent(db, agent_id: str):
    await db.execute(
        "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
        (_now(), agent_id)
    )


async def _agent_tombstone(db, agent_id: str):
    # FIX 4 (2026-06-03): match case-insensitively so deleting `hermes-test` then
    # re-registering `Hermes-test` still hits the tombstone (the old case-sensitive
    # lookup let a different-casing re-register bypass the deletion).
    cursor = await db.execute(
        "SELECT * FROM agent_tombstones WHERE agent_id = ? COLLATE NOCASE", (agent_id,)
    )
    return await cursor.fetchone()


async def _tombstone_agent(
    db,
    agent_id: str,
    *,
    removed_by: str = "",
    bridge_id: str = "",
    reason: str = "",
    removed_at: Optional[str] = None,
):
    await db.execute(
        """
        INSERT OR REPLACE INTO agent_tombstones (
            agent_id, removed_at, removed_by, bridge_id, reason
        ) VALUES (?,?,?,?,?)
        """,
        (agent_id, removed_at or _now(), removed_by, bridge_id, reason),
    )


async def _session_handle_live_owner(db, handle: str, *, exclude_agent_id: str, lease_seconds: int):
    """Return a DIFFERENT, currently-LIVE agent that already owns `handle`.

    Cross-agent session-id collision guard (root cause of the 2026-05-31
    incident): a runtime session id must be owned by at most ONE live agent.
    When graph-tech-lead (a managed launch) adopted comms-tech-lead's live
    resident session id 651b895f, the kill-prior reaper then turned that
    collision fatal. This detects the collision at the source — before a handle
    is adopted — so it can be parked instead of bound.

    "Live" = another agent with the same session_handle whose heartbeat is fresh
    within the resident lease (a dead/stale owner means the id is effectively
    free to reassign, so it is NOT a collision). Returns {agentId, sessionMode}
    of the live owner, or None.
    """
    h = str(handle or "").strip()
    if not h:
        return None
    cutoff = max(60, int(lease_seconds or 150))
    cursor = await db.execute(
        "SELECT id, last_seen, session_mode FROM agents WHERE session_handle = ? AND id != ?",
        (h, str(exclude_agent_id or "").strip()),
    )
    for r in await cursor.fetchall():
        seen = _iso_to_epoch(r["last_seen"] or "")
        if seen and (time.time() - seen) <= cutoff:
            return {"agentId": r["id"], "sessionMode": str(r["session_mode"] or "")}
    return None


async def _current_agent_session_row(db, agent_id: str):
    # Phase 3 item 4 (2026-06-03): the canonical live-session membership set is
    # the module-level LIVE_SESSION_STATUSES (running/attached/active/idle/
    # starting/recovering). This picker filters out the TERMINAL statuses
    # (the complement) and uses the CASE only as a PRIORITY tiebreak (prefer a
    # fresh actively-running row over a merely-attached one), not as a membership
    # test — so its narrower CASE list is intentionally a priority hint, kept
    # stable to avoid reordering which session a relaunch picks.
    #
    # R2c (2026-07-26): the WHERE used to exclude only ('ended','completed','cancelled') while the
    # docstring above already claimed the full complement — so `stopped`/`failed`/`lost` passed.
    # That was not a comment nit. The CASE promotes only FOUR statuses, so the live statuses
    # `attached`/`active`/`idle`/`starting` share tier 1 with the dead ones and LOSE the
    # `last_seen DESC` tiebreak to a fresher corpse: this picker answered "the agent's CURRENT
    # session" with a dead row. Downstream that made `_has_live_worker_for` report no live worker
    # for an agent with a live console, pointed both idle-reply closers at the wrong terminal_id,
    # and broke the terminal-close requeue compare. Same shadowing class as c2f0e38.
    # The WHERE is now the documented complement; the ORDER BY is untouched on purpose.
    cursor = await db.execute(
        f"""
        SELECT *
        FROM agent_sessions
        WHERE agent_id = ?
          AND status NOT IN ({_ENDED_AGENT_SESSION_STATUS_PLACEHOLDERS})
        ORDER BY
          CASE WHEN status IN ('running', 'recovering', 'restarting', 'cli-takeover') THEN 0 ELSE 1 END,
          last_seen DESC,
          started_at DESC
        LIMIT 1
        """,
        (agent_id, *_ENDED_AGENT_SESSION_STATUS_PARAMS),
    )
    return await cursor.fetchone()


async def _touch_current_agent_session(db, agent_id: str, runtime_state: dict[str, Any] | None, now: str) -> None:
    """Keep the dashboard backing record fresh when a managed runtime is used."""
    state = runtime_state or {}
    spawn_request_id = str(state.get("spawnRequestId") or "").strip()
    environment_id = str(state.get("environmentId") or "").strip()
    runtime_handle = str(state.get("sessionId") or state.get("threadId") or state.get("sessionFile") or "").strip()
    if spawn_request_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE agent_id = ?
              AND spawn_request_id = ?
              AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
            """,
            (now, runtime_handle, runtime_handle, agent_id, spawn_request_id),
        )
        return
    if environment_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE id = (
                SELECT id
                FROM agent_sessions
                WHERE agent_id = ?
                  AND environment_id = ?
                  AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
                ORDER BY last_seen DESC
                LIMIT 1
            )
            """,
            (now, runtime_handle, runtime_handle, agent_id, environment_id),
        )


# v0.5.4: `_adopt_live_resident_driver` arrived from the control plane. Adopting the live resident driver
# for an agent is a SESSION ownership change, which is this module's subject — it was in the carrier only
# because the claim path called it, and "the caller lives elsewhere" was never an ownership argument.

async def _adopt_live_resident_driver(db, agent_id: str) -> bool:
    """SELF-HEAL for the launch-terminal-first / switch-second ordering (2026-06-12,
    sc-manager strand): a channel sidecar claiming/beating for a RESIDENT-mode agent with
    driver_state != 'driving' is only a DISPLACED MANAGED driver when no live resident
    session exists. When a FRESH resident bridge row is beating, this sidecar IS that live
    resident session's own delivery path — the operator launched the resident terminal
    FIRST (registration set driver_state='driving') and clicked "switch to resident"
    SECOND, and the switch clobbered driver_state back to 'idle'. Releasing the sidecar
    then silently killed resident delivery: sends reported "sent", runs queued forever,
    nothing claimed. Adopt the driving state instead of releasing. Returns True when
    adopted (caller skips the release)."""
    # bridge_kind is '' on a registration-created row (only heartbeats stamp the kind) —
    # accept that shape only when the bridge row itself was registered as a RESIDENT
    # session; a managed registration's kindless bridge must never count as a live
    # resident driver (it would re-adopt a genuinely displaced agent).
    row = await (await db.execute(
        "SELECT id FROM bridge_instances WHERE agent_id = ? "
        "AND (bridge_kind = 'resident' OR (COALESCE(bridge_kind, '') = '' AND session_mode = 'resident')) "
        "AND COALESCE(superseded_by, '') = '' AND datetime(last_seen) > datetime('now', '-150 seconds') "
        "LIMIT 1",
        (agent_id,),
    )).fetchone()
    if not row:
        return False
    await db.execute("UPDATE agents SET driver_state = 'driving' WHERE id = ?", (agent_id,))
    return True


async def _record_registered_session_handle(db, req, normalized_runtime, runtime_config, session_handle, now) -> None:
    """Pin the freshly-registered session handle onto the agent's CURRENT session row.

    v0.5.4, extracted verbatim out of the 622-line `register_agent`. It belongs here because "which
    agent_sessions row is current, and who owns a handle" is this module's stated subject; the route was
    only its caller.

    WHY THE UPDATE TARGETS A SUBQUERY: an agent can have several session rows for one runtime (a
    process-per-boot history), so this pins the handle to the MOST RECENTLY SEEN row rather than to all of
    them. Writing every row would make a stale boot look resumable.

    THE `CASE WHEN ... = '{}'` GUARDS ARE DELIBERATE: capabilities and telemetry are only seeded when the
    row has none. A registration must not clobber richer values a live session already reported.

    THE SQL LITERAL IS INDENTED ONE LEVEL DEEPER THAN ITS SURROUNDING CODE and must not be tidied. The
    interior lines of a triple-quoted string are DATA: dedenting them on the way out of the route would
    change the constant's value while leaving the code correct. `tokenize` identified them and only the
    code lines moved. This exact mistake failed the inline-back proof once already.

    WRITES: left UNCOMMITTED for the caller's transaction, per the DB-leaf rule.
    """
    if session_handle:
        app_server_url = ""
        if isinstance(runtime_config, dict):
            app_server_url = str(runtime_config.get("appServerUrl") or "").strip()
        session_runtime_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
        await db.execute(
            """
                UPDATE agent_sessions
                SET session_handle = ?,
                    app_server_url = CASE WHEN ? != '' THEN ? ELSE app_server_url END,
                    last_seen = ?,
                    capabilities = CASE
                        WHEN COALESCE(NULLIF(capabilities, ''), '{}') = '{}' THEN ?
                        ELSE capabilities
                    END,
                    telemetry = CASE
                        WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                        ELSE telemetry
                    END
                WHERE id = (
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                )
                """,
            (
                session_handle,
                app_server_url,
                app_server_url,
                now,
                json.dumps({"persistent": True, "nativeResume": True, "bridgeResume": True, "cliAttach": True}),
                json.dumps({"registeredHandle": session_runtime_state}),
                req.agentId,
                normalized_runtime,
            ),
        )
