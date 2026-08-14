"""Recording that a bridge instance has registered for an environment.

Moved out of `service/routers/agents/shared.py` in v0.5.4, byte-identical. At 186 lines it was the
largest single thing in that module.

IT DOES NOT LIVE IN `bridge_supersede.py`, though that module's two functions are called from here and
from nowhere else. Its docstring says why, and the separation is deliberate: *"Registration is a
decision; this is the consequence of that decision, and the consequence outlives the request."*
Merging them would collapse a boundary someone drew on purpose, so this is its own module and imports
the consequence rather than absorbing it.
"""
from __future__ import annotations

from service.api_core.bridge_supersede import (
    _fail_active_runs_for_superseded_bridges,
    _stop_virtual_terminals_for_superseded_bridges,
)
from service.api_core.serialization import _normalize_machine_id

async def _record_bridge_registration(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    machine_id: str,
    runtime: str,
    session_mode: str,
    session_handle: str,
    terminal_id: str = "",
    managed_wrapper_child: bool = False,
    now: str,
) -> None:
    """Single source of truth for register-time bridge_instances writes.

    Inserts/updates the bridge_instance row carrying the new bridge_id and
    its logical identity, then supersedes older rows according to the
    runtime ownership model. Generic managed bridges use latest-wins;
    resident bridges and managed wrapper-child bridges protect fresh
    same-logical-owner rows so duplicate registration does not kill work.
    """
    normalized_machine = _normalize_machine_id(machine_id)
    normalized_runtime_value = str(runtime or "")
    normalized_session_mode_value = str(session_mode or "")
    normalized_session_handle_value = str(session_handle or "").strip()
    normalized_terminal_id_value = str(terminal_id or "").strip()
    bridge_kind = "managed-wrapper-child" if managed_wrapper_child else ""
    await db.execute(
        """
        INSERT OR REPLACE INTO bridge_instances (
            id, agent_id, machine_id, runtime, session_mode, session_handle,
            terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            bridge_id,
            agent_id,
            normalized_machine,
            normalized_runtime_value,
            normalized_session_mode_value,
            normalized_session_handle_value,
            normalized_terminal_id_value,
            bridge_kind,
            now,
            now,
            "",
            None,
        ),
    )
    # Supersession carve-out applies to resident session_mode and to
    # managed wrapper children. Resident covers operator-side multi-window
    # CLI scenarios where two human-launched shells legitimately coexist
    # for the same identity. Managed wrapper children are bridge-spawned
    # PTYs whose in-process bridge claims channel/resident work; a fresh
    # same-logical-owner re-register is the same wrapper-owner class and
    # must not kill its active turn. Generic MANAGED bridges still use
    # latest-registration-wins to prevent leaked zombies.
    #
    # IMPORTANT: `_fail_active_runs_for_superseded_bridges` will fail
    # in-flight runs owned by the superseded bridges. For generic managed
    # mode that's correct — only one bridge should be driving an active
    # run, and if a new bridge registers, the old in-flight one is
    # presumed orphaned. Resident and wrapper-child carve-outs protect
    # parent/wrapper in-flight runs from duplicate same-owner registrations.
    # Heartbeat-aware carve-out (operator-reported 2026-05-23: 10+ leaked
    # bridge_instances for comms-tech-lead from May 21–22 claude-aify
    # restarts, never superseded). The resident-mode carve-out only
    # protects bridges whose heartbeat is FRESH — a same-identity bridge
    # whose last_seen is past the 5-min stale window is a dead process
    # whose row should be superseded so the table doesn't accumulate
    # zombie entries. Live multi-window resident scenarios still keep the
    # protection because their last_seen heartbeats stay fresh.
    # Latest-launch-wins for resident bridges (2026-05-29). The previous
    # blanket `session_mode == 'resident'` carve-out protected EVERY fresh
    # same-identity resident bridge from supersession, so each new wrapper
    # launch coexisted with the prior one instead of replacing it. In real use
    # that splits one logical agent into multiple live sessions (#1/#2…) and
    # lets stale rows accumulate, and the dashboard/delivery can land on the
    # wrong one. Operators need the tool to self-heal in a messy state, not to
    # require sterile single-launch discipline. So a new resident registration
    # now supersedes prior same-agent/same-machine bridges (the newest live
    # bridge is authoritative). The managed-wrapper-child protection is kept
    # intact: bridge-spawned PTY siblings sharing a terminal must not kill each
    # other. Same-process periodic re-register keeps the same bridge_id and is
    # excluded by `id != ?`, so only genuinely older launches are superseded.
    # Managed visible-TUI coexistence carve-out (2026-05-31). In the visible-TUI
    # managed model a single managed agent has TWO complementary live bridges:
    #   - a standalone `channel-sidecar` (the hermes-managed-host.js delivery
    #     loop) that CLAIMS channel runs and delivers via WS prompt.submit, and
    #   - a `managed-wrapper-child` (the visible TUI's in-session aify-comms MCP)
    #     that exists so the agent can self-reply via comms_send.
    # They play DIFFERENT roles for the same agent and must not supersede each
    # other. Before this carve-out the wrapper-child registration superseded the
    # sidecar (and vice versa on the sidecar's own bridge-registration path),
    # which blocked the superseded one from claiming → delivery silently stalled
    # (observed on gov-tui 2026-05-30: a queued run never claimed). Protect the
    # existing row whenever the registering bridge and the existing row form a
    # sidecar↔wrapper-child pair for the SAME managed agent+machine.
    new_kind = bridge_kind or "managed-resident"  # "" means resident/env bridge
    # KEPT (Task A' #154, 2026-06-01): the liveness beat does not prevent
    # register-time supersession (it only refreshes last_seen and cannot save a
    # row from a competing registration), so this is the only thing protecting a
    # sidecar↔wrapper-child complementary pair from killing each other. Removal
    # probe broke test_wrapper_child_registration_does_not_supersede_channel_sidecar
    # and test_wrapper_child_does_not_supersede_a_STALE_channel_sidecar.
    complementary_pair = (
        (new_kind == "managed-wrapper-child" and normalized_session_mode_value == "managed")
        or new_kind == "channel-sidecar"
    )
    # Complementary visible-TUI pair protection is ABSOLUTE (operator-reported
    # 2026-05-31, sc-claude). A channel-sidecar and a managed-wrapper-child for
    # the SAME managed agent play different roles and must NEVER supersede each
    # other — NOT EVEN when the sidecar's heartbeat is briefly stale during
    # managed-PTY churn. Previously this protection was an OR-branch inside the
    # `stale OR NOT(protected)` predicate, so the 5-min-stale clause overrode it:
    # a stale sidecar got superseded by the wrapper-child registration, and the
    # still-live sidecar's claims were then permanently blocked → delivery
    # silently stalled. Pulling it out as a leading `AND NOT (...)` makes it
    # absolute. The remaining stale/unprotected cleanup applies only to
    # NON-complementary rows (genuine zombies still age out; the live sidecar
    # reuses its stable id and self-refreshes).
    superseded_cursor = await db.execute(
        """
        SELECT id FROM bridge_instances
        WHERE agent_id = ? AND machine_id = ? AND id != ? AND superseded_by = ''
          AND NOT (
            ? = 'resident'
            AND COALESCE(bridge_kind, '') = 'channel-sidecar'
          )
          AND NOT (
            ? = 1
            AND session_mode = 'managed'
            AND COALESCE(bridge_kind, '') IN ('channel-sidecar', 'managed-wrapper-child')
            AND COALESCE(bridge_kind, '') != ?
          )
          AND (
            datetime(COALESCE(last_seen, '1970-01-01')) < datetime('now', '-5 minutes')
            OR NOT (
              runtime = ? AND session_mode = ?
              AND COALESCE(session_handle, '') = ?
              AND ? = 'managed-wrapper-child'
              AND COALESCE(bridge_kind, '') = 'managed-wrapper-child'
              AND COALESCE(terminal_id, '') = ?
            )
          )
        """,
        (
            agent_id,
            normalized_machine,
            bridge_id,
            normalized_session_mode_value,
            1 if complementary_pair else 0,
            new_kind,
            normalized_runtime_value,
            normalized_session_mode_value,
            normalized_session_handle_value,
            bridge_kind,
            normalized_terminal_id_value,
        ),
    )
    superseded_ids = [row["id"] for row in await superseded_cursor.fetchall()]
    if not superseded_ids:
        return
    placeholders = ",".join("?" for _ in superseded_ids)
    await db.execute(
        f"""
        UPDATE bridge_instances
        SET superseded_by = ?, superseded_at = ?
        WHERE id IN ({placeholders})
        """,
        (bridge_id, now, *superseded_ids),
    )
    await _fail_active_runs_for_superseded_bridges(
        db,
        agent_id=agent_id,
        machine_id=normalized_machine,
        superseding_bridge_id=bridge_id,
        finished_at=now,
        superseded_bridge_ids=superseded_ids,
    )
    await _stop_virtual_terminals_for_superseded_bridges(
        db,
        agent_id=agent_id,
        superseded_bridge_ids=superseded_ids,
        now=now,
    )
