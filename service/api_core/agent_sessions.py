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
import uuid
from typing import Any, Optional

from service.api_core.active_run_lookup import _get_blocking_active_run
from service.api_core.runtime import _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.events import _append_terminal_control, _append_terminal_event
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


async def _supersede_stale_resident_terminals(db, req, terminal_id: str, now: str, bridge_id: str) -> None:
            """A resident registration takes the agent over from whatever was running it.

            Extracted from `register_agent` in v0.5.4, and `test_register_agent_split_is_inert.py` inlines it
            back and AST-compares against the pre-split fixture to prove nothing changed.

            THE BODY IS INDENTED 12 SPACES, WHICH LOOKS WRONG AND IS DELIBERATE. It contains three multi-line
            SQL literals, and re-indenting the block would have re-indented their CONTENTS — changing the
            string values. SQLite would not have cared, but the gate compares ASTs and refused it, correctly:
            "the whitespace inside a query does not matter" is exactly the kind of reasoning a proof exists to
            make unnecessary. Python only requires a function body to be consistently indented, not minimally,
            so keeping the original column preserves every literal byte-for-byte.

            THREE THINGS HAPPEN PER STALE TERMINAL and all three are needed. The row is marked stopped, so the
            dashboard stops showing it; an event is appended, so the reason is recoverable afterwards; and a
            `stop` control is queued, so the owning bridge tears the wrapper subprocess down. Queuing is
            best-effort BY DESIGN — if that bridge is already dead the control is never claimed, which does not
            matter, because the row is marked stopped either way.

            The agent_sessions unbinding at the end is the part that is easy to leave out: a session row still
            pointing at a just-stopped terminal renders as a live Console the operator can click into and type
            at — a ghost of a process that no longer exists.
            """
            stale_terminals = await (
                await db.execute(
                    """
                    SELECT id, environment_id, bridge_id
                    FROM terminal_sessions
                    WHERE agent_id = ?
                      AND status IN ('starting','attached','running','active','idle','recovering')
                      AND (? = '' OR id != ?)
                    """,
                    (req.agentId, terminal_id, terminal_id),
                )
            ).fetchall()
            for term in stale_terminals:
                await db.execute(
                    """
                    UPDATE terminal_sessions
                    SET status = 'stopped',
                        stopped_at = ?,
                        updated_at = ?,
                        error = COALESCE(NULLIF(error, ''), 'superseded_by_resident_takeover')
                    WHERE id = ?
                    """,
                    (now, now, term["id"]),
                )
                await _append_terminal_event(
                    db,
                    term["id"],
                    "superseded_by_resident_takeover",
                    json.dumps({
                        "agentId": req.agentId,
                        "residentBridge": bridge_id,
                        "newSessionMode": "resident",
                    }),
                )
                # Best-effort kill: enqueue 'stop' so the owning bridge
                # tears down the wrapper subprocess if still alive. If
                # the bridge is dead, the row is already marked stopped
                # so it doesn't matter that the control is never claimed.
                await _append_terminal_control(
                    db,
                    terminal_id=term["id"],
                    environment_id=term["environment_id"] or "",
                    bridge_id=term["bridge_id"] or "",
                    action="stop",
                    requested_by="resident-takeover",
                    body="",
                )
            if stale_terminals:
                # Clear agent_sessions.terminal_id binding for sessions
                # that pointed at any of the just-stopped terminals so
                # the dashboard stops rendering a ghost Console.
                stopped_ids = [t["id"] for t in stale_terminals]
                placeholders = ",".join(["?"] * len(stopped_ids))
                await db.execute(
                    f"""
                    UPDATE agent_sessions
                    SET terminal_id = '',
                        terminal_status = ''
                    WHERE agent_id = ?
                      AND terminal_id IN ({placeholders})
                    """,
                    (req.agentId, *stopped_ids),
                )


async def _stage_manual_resident_takeover(db, req, row, bridge_id: str, normalized_runtime: str,
                                          session_handle: str, runtime_config, capabilities,
                                          resolved_cwd: str, now: str):
            """A MANAGED agent tried to register as resident. Record the candidate; do not switch it.

            Extracted from `register_agent` in v0.5.4 and re-proved on every run by
            `test_register_agent_split_is_inert.py`, which inlines it back and AST-compares against the
            pre-split fixture.

            Body left at its original 12-space column so the multi-line SQL literal inside it is preserved
            byte-for-byte — see `_supersede_stale_resident_terminals` for why that matters and why the gate
            refused the tidier version.

            THE ONE-DRIVER INVARIANT is what this is protecting. Two things cannot drive one agent, so a
            managed agent does not silently become resident because a CLI session registered: the candidate
            is stashed in `manualResidentCandidate` and the operator flips the mode deliberately. The stale
            `pendingResidentTakeover` is popped first, or a candidate from an earlier attempt would still be
            sitting there when this one is read.

            Returns the blocking active run, which the caller reports back to the registering session so it
            can say WHY the switch did not happen rather than just that it did not.
            """
            active_run = await _get_blocking_active_run(db, req.agentId)
            existing_state_dict = _json_loads_or(row["runtime_state"], {})
            existing_state_dict.pop("pendingResidentTakeover", None)
            existing_state_dict["manualResidentCandidate"] = {
                "bridgeId": bridge_id,
                "machineId": req.machineId or "",
                "runtime": normalized_runtime,
                "sessionHandle": session_handle,
                "runtimeConfig": runtime_config,
                "capabilities": capabilities or [],
                "cwd": resolved_cwd,
                "launchMode": req.launchMode or "detached",
                "registeredAt": now,
            }
            await db.execute(
                """
                UPDATE agents
                SET runtime_state = ?,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    json.dumps(existing_state_dict),
                    (
                        f"Resident CLI registered, but agent remains managed. Use Switch to resident when ready."
                        + (f" Active run {active_run.get('runId') or ''} is still running." if active_run else "")
                    ),
                    now,
                    req.agentId,
                ),
            )
            if session_handle:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET session_handle = ?,
                        telemetry = CASE
                            WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                            ELSE telemetry
                        END,
                        last_seen = ?
                    WHERE id = (
                        SELECT id
                        FROM agent_sessions
                        WHERE agent_id = ?
                          AND runtime = ?
                          AND status = 'cli-takeover'
                        ORDER BY last_seen DESC
                        LIMIT 1
                    )
                    """,
                    (
                        session_handle,
                        json.dumps({"registeredHandle": _runtime_state_with_handle(normalized_runtime, {}, session_handle)}),
                        now,
                        req.agentId,
                        normalized_runtime,
                    ),
                )
            return active_run


async def _settle_agent_for_session_control(db, session_id: str, agent_id: str, action: str, now: str,
                                            cancelled_spawns: int) -> int:
        """Bring the AGENT row into line with a session control, and cancel spawns it invalidates.

        Extracted from `control_session` in v0.5.4 and re-proved on every run by
        `test_control_session_split_is_inert.py`, which inlines it back and AST-compares against the
        pre-split fixture. Body left at its original 8-space column so the multi-line SQL literals
        inside are preserved byte-for-byte — the extract-method gate compares ASTs and refuses a
        re-indent that rewrites a query string.

        THE PENDING-SPAWN CANCELLATION is the part that is easy to miss and expensive to get wrong. A
        stop or a CLI takeover invalidates any spawn already queued/claimed/starting for this agent:
        without cancelling them, the spawn completes moments later and hands the operator back the
        worker they just stopped. The cancellation carries a reason naming which of the two happened,
        because "cancelled" with no cause is indistinguishable from a failure.

        THE THREE-WAY AGENT OUTCOME is deliberate, not an accident of nesting:
          * cli_takeover  -> `stopped`, launch_mode cleared, and a note telling the operator how to get
            control back, since nothing else will tell them;
          * stop of a RESIDENT session -> `stopped`, because the live bridge is expected to terminate
            the CLI host;
          * stop of anything else -> `offline`, and only if it was not ALREADY `stopped` — a stop must
            never promote a stopped agent back to offline.

        Returns the running cancellation count so the caller can report it.
        """
        if action in {"stop", "cli_takeover"}:
            pending_spawn_cursor = await db.execute(
                """
                SELECT id
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                """,
                (agent_id,),
            )
            for pending_spawn in await pending_spawn_cursor.fetchall():
                await db.execute(
                    """
                    UPDATE spawn_requests
                    SET status = 'cancelled',
                        error = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('queued', 'claimed', 'starting')
                    """,
                    (
                        f'Session "{session_id}" was {"paused for CLI takeover" if action == "cli_takeover" else "stopped from the dashboard"} before spawn completed.',
                        now,
                        now,
                        pending_spawn["id"],
                    ),
                )
                cancelled_spawns += 1
            if action == "cli_takeover":
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        "Paused for direct CLI takeover. Close the CLI session and use Sessions -> Restart to return control to the dashboard.",
                        now,
                        agent_id,
                    ),
                )
            else:
                agent_current = await (await db.execute("SELECT session_mode FROM agents WHERE id = ?", (agent_id,))).fetchone()
                if agent_current and _normalize_session_mode(agent_current["session_mode"] or "resident") == "resident":
                    await db.execute(
                        """
                        UPDATE agents
                        SET status = 'stopped',
                            status_note = ?,
                            launch_mode = 'none',
                            last_seen = ?
                        WHERE id = ?
                        """,
                        ("Resident session stop requested from dashboard; live bridge should terminate the CLI host.", now, agent_id),
                    )
                else:
                    await db.execute(
                        "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END, last_seen = ? WHERE id = ?",
                        (now, agent_id),
                    )
        else:
            await db.execute(
                "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'idle' END, last_seen = ? WHERE id = ?",
                (now, agent_id),
            )
        return cancelled_spawns


# --- the resident session row --------------------------------------------------------------------
#
# Moved here from `service/routers/agents/shared.py` in v0.5.4, byte-identical. This is the module
# that owns agent_sessions rows, and having the upsert in a router was a live constraint rather than
# a tidiness question: an earlier v0.5.4 slice had to STOP an extraction short because a block ending
# in this call could not move to api_core without importing a router upward. That constraint is gone.

async def _upsert_resident_agent_session(
    db,
    *,
    agent_id: str,
    runtime: str,
    workspace: str,
    machine_id: str,
    session_handle: str,
    runtime_config: dict[str, Any] | None,
    bridge_id: str,
    capabilities: list[str] | None,
    now: str,
) -> str:
    """Create the dashboard-visible session row for an operator-open CLI."""

    config = runtime_config if isinstance(runtime_config, dict) else {}
    machine = str(machine_id or "").strip()
    env_row = None
    if machine:
        env_row = await (await db.execute(
            """
            SELECT id
            FROM environments
            WHERE lower(machine_id) = lower(?)
              AND status != 'forgotten'
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (machine,),
        )).fetchone()
    if not env_row:
        return ""

    # FIX 1 (2026-06-03): the resident session id must be STABLE across relaunches
    # so a relaunch UPSERTs the SAME row instead of minting a new resident_* every
    # launch. session_handle / gatewayUrl / bridge_id all ROTATE per launch, so the
    # ON CONFLICT(id) DO UPDATE could never match and duplicate rows accumulated.
    # Key on (machine or env id or agent) — stable per (agent_id, runtime, machine).
    key_material = machine or str(env_row["id"]) or agent_id
    session_id = f"resident_{uuid.uuid5(uuid.NAMESPACE_URL, f'aify-comms:{agent_id}:{runtime}:{key_material}').hex[:16]}"
    app_server_url = str(config.get("appServerUrl") or "").strip()
    telemetry = {
        "resident": True,
        "nativeResume": bool(session_handle),
        "bridgeResume": bool(bridge_id),
        "cliAttach": True,
        "gateway": bool(str(config.get("gatewayUrl") or "").strip()),
    }
    await db.execute(
        """
        INSERT INTO agent_sessions (
            id, agent_id, environment_id, runtime, workspace, mode,
            owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
            process_id, session_handle, app_server_url, spawn_spec_id, spawn_request_id,
            capabilities, telemetry, status, started_at, last_seen, ended_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            runtime = excluded.runtime,
            workspace = excluded.workspace,
            mode = excluded.mode,
            owner_mode = excluded.owner_mode,
            owner_bridge_id = excluded.owner_bridge_id,
            session_handle = excluded.session_handle,
            app_server_url = excluded.app_server_url,
            capabilities = excluded.capabilities,
            telemetry = excluded.telemetry,
            status = 'running',
            last_seen = excluded.last_seen,
            ended_at = NULL
        """,
        (
            session_id,
            agent_id,
            env_row["id"],
            runtime,
            workspace or "",
            "resident",
            "resident",
            bridge_id or "",
            "",
            "",
            "",
            "",
            "",
            session_handle or "",
            app_server_url,
            None,
            None,
            json.dumps({"resident": True, "cliAttach": True, "capabilities": capabilities or []}),
            json.dumps(telemetry),
            "running",
            now,
            now,
            None,
        ),
    )
    # RC3 (2026-06-03): collapse duplicate resident sessions. The resident session
    # id is a hash of the session_handle (line ~12879), so a relaunch with a new
    # native handle mints a NEW resident_* row while the prior one stays 'running'
    # — the dashboard then shows two live resident sessions for one agent. Retire
    # every OTHER resident session for this agent so exactly one stays live.
    await db.execute(
        """
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE agent_id = ?
          AND mode = 'resident'
          AND id != ?
          AND status NOT IN ('stopped', 'failed', 'exited')
        """,
        (now, agent_id, session_id),
    )
    return session_id


async def _adopt_console_terminal_on_register(db, req, console_terminal, terminal_id: str,
                                              normalized_runtime: str, session_handle: str,
                                              resolved_cwd: str, next_state, existing_capabilities,
                                              existing_runtime_config, now: str) -> None:
            """Write a live console PTY into the agent and its session rows, as one act.

            Extracted from `register_agent` in v0.5.4;
            `test_register_agent_split_is_inert.py` inlines it back and AST-compares against the
            pre-split fixture. Body left at its original 12-space column so the two multi-line SQL
            literals inside are preserved byte-for-byte — the gate compares ASTs and refuses a
            re-indent that rewrites a query string.

            TWO UPDATES, ONE MEANING, which is why they are extracted together rather than separately:
            a console PTY attaching IS the authoritative "backing (re)started" event, so the agent row
            learns about the terminal and the session row is promoted out of a dead denorm state in the
            same breath. Splitting them would invite a later change to one without the other, and a
            session left in a dead state with a live PTY reads to the dashboard as a console that
            cannot be typed into.

            Everything it needs is passed in; it calls nothing.
            """
            await db.execute(
                """
                UPDATE agents
                SET role = ?,
                    name = ?,
                    cwd = ?,
                    runtime = ?,
                    machine_id = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    capabilities = ?,
                    runtime_config = ?,
                    runtime_state = ?,
                    status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END,
                    status_note = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    req.role,
                    req.name or req.agentId,
                    resolved_cwd,
                    normalized_runtime,
                    req.machineId or "",
                    session_handle,
                    session_handle,
                    existing_capabilities,
                    existing_runtime_config,
                    json.dumps(next_state),
                    "Dashboard Console PTY attached.",
                    now,
                    req.agentId,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET owner_mode = 'console',
                    owner_bridge_id = ?,
                    terminal_id = ?,
                    terminal_status = ?,
                    session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                    -- A live console PTY attaching IS the authoritative "backing (re)started"
                    -- event: promote a dead-state denorm back to running, else the session row
                    -- stays 'stopped' from the PREVIOUS backing's death and the Console label
                    -- reads "Console stopped" for a live attached terminal forever (cms-manager,
                    -- 2026-06-10 — the display deriver deliberately never promotes, so the bind
                    -- moment must). Operator disable is enforced on agents.status, not here.
                    status = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                  THEN 'running' ELSE status END,
                    ended_at = CASE WHEN status IN ('cli-takeover','stopped','ended','failed','lost','cancelled','completed')
                                    THEN NULL ELSE ended_at END,
                    last_seen = ?
                WHERE id = ?
                """,
                (
                    console_terminal["bridge_id"] or "",
                    terminal_id,
                    console_terminal["status"] or "attached",
                    session_handle,
                    session_handle,
                    now,
                    console_terminal["session_id"],
                ),
            )
