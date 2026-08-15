"""The unconditional liveness beat: what a bridge saying "I am alive" writes.

Extracted from `agent_heartbeat` in `service/routers/agents/liveness.py` in v0.5.4;
`test_agent_heartbeat_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

WHY IT UPSERTS RATHER THAN UPDATES. A long-lived bridge posts `{bridgeId, bridgeKind, liveness:true}`
on a fixed interval whether or not a turn is running, so `last_seen` is a true "alive now" signal.
The plain UPDATE elsewhere in the handler no-ops when the bridge has no row yet -- an idle
channel-sidecar that never claimed anything -- so that bridge would beat forever and never appear
live. This path creates the row. It never clears `superseded_by` and never touches turn state.

THE DEMOTION GUARD IS THE SUBTLE PART (FIX SET B3, 2026-06-03). The 30s beat from the host-side
bridge posts `bridgeKind="resident"`, but the SAME agent may have a wrapper-child or channel-sidecar
row carrying the authoritative managed kind. A plain COALESCE let the generic beat DEMOTE
`managed-wrapper-child` back to `resident`, after which the live-managed-child and live-sidecar
predicates stopped matching and the managed agent silently lost its claimer. An incoming `''` or
`'resident'` can now never overwrite either managed kind; any other kind still wins as before.
"""
from __future__ import annotations

from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
from service.api_core.serialization import _normalize_machine_id


async def _upsert_bridge_liveness_beat(db, agent_id, bridge_id, bridge_kind, body, now) -> None:
        """Refresh (or create) the bridge row this beat belongs to.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every argument
        is passed under the caller's own name for the same reason: inline-back does not substitute
        arguments.
        """
        if body.get("liveness") and bridge_id:
            arow = await (await db.execute(
                "SELECT machine_id, runtime, session_mode FROM agents WHERE id = ?", (agent_id,),
            )).fetchone()
            arow_machine = (arow["machine_id"] if arow else "") or ""
            arow_runtime = (arow["runtime"] if arow else "") or "generic"
            if bridge_kind == "channel-sidecar":
                await _record_channel_sidecar_heartbeat(
                    db,
                    bridge_id=bridge_id,
                    agent_id=agent_id,
                    machine_id=arow_machine,
                    runtime=arow_runtime,
                    session_mode=(arow["session_mode"] if arow else "") or "managed",
                    now=now,
                )
            else:
                # FIX SET B3 (2026-06-03): the 30s liveness beat from the host-side
                # bridge (server.js) posts bridgeKind="resident", but the SAME agent
                # may have a wrapper-child / channel-sidecar bridge row that registered
                # the authoritative managed kind. A plain COALESCE(NULLIF(?,''),...)
                # let that generic "resident" beat DEMOTE a 'managed-wrapper-child'
                # (or 'channel-sidecar') back to 'resident' — after which
                # _has_live_managed_wrapper_child / _has_live_channel_sidecar stop
                # matching and the managed agent loses its claimer (the lc-coder /
                # codex-managed strand). Guard: an incoming '' or 'resident' can NEVER
                # overwrite an existing 'managed-wrapper-child' or 'channel-sidecar';
                # any other incoming kind still COALESCE-wins as before.
                updated = await db.execute(
                    "UPDATE bridge_instances SET last_seen = ?, "
                    "bridge_kind = CASE "
                    "WHEN COALESCE(bridge_kind, '') IN ('managed-wrapper-child', 'channel-sidecar') "
                    "AND COALESCE(?, '') IN ('', 'resident') THEN bridge_kind "
                    "ELSE COALESCE(NULLIF(?, ''), bridge_kind) END "
                    "WHERE id = ? AND agent_id = ?",
                    (now, bridge_kind, bridge_kind, bridge_id, agent_id),
                )
                if not getattr(updated, "rowcount", 0):
                    await db.execute(
                        """
                        INSERT OR IGNORE INTO bridge_instances (
                            id, agent_id, machine_id, runtime, session_mode,
                            session_handle, terminal_id, bridge_kind,
                            registered_at, last_seen, superseded_by, superseded_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (bridge_id, agent_id,
                         _normalize_machine_id(arow_machine),
                         arow_runtime,
                         "managed", "", "", bridge_kind or "resident",
                         now, now, "", None),
                    )
                    await db.execute(
                        "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                        (now, bridge_id, agent_id),
                    )
