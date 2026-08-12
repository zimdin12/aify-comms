"""Cleaning up after a bridge that has been superseded — its runs, and its virtual terminals.

Two functions, 139 lines, both called by `_record_bridge_registration` and by nothing else. When a new
bridge instance registers for an environment the previous one is superseded, and the predecessor leaves
two kinds of debris behind: dispatch runs it had claimed and will now never deliver, and virtual
terminals it was serving. Neither cleans itself up, because the superseded process is typically already
gone — it does not get a chance to tidy.

They are together and separate from the registration path on purpose. Registration is a decision;
this is the consequence of that decision, and the consequence outlives the request. Keeping them here
means the next person changing supersede semantics finds both halves in one file rather than one half
and a call.

STATE-BASED, NOT EVENT-BASED, for the reason that rule exists: cleanup that must hold for ALL paths
keys on the recorded state rather than on some earlier notification having fired. A spawn once sat
`running` for 97 minutes waiting for a call that one of ~26 writers never made.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback — the caller owns the
transaction, so the supersede and the cleanup land together or not at all.
"""

from __future__ import annotations

import json
from typing import Optional

from service.api_core.events import (
    _append_dispatch_event,
    _append_terminal_event,
)
from service.api_core.serialization import _json_loads_or
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _fail_active_runs_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    machine_id: str,
    superseding_bridge_id: str,
    finished_at: str,
    superseded_bridge_ids: Optional[list[str]] = None,
) -> list[str]:
    # Scope-narrowed: only fail runs whose claim_bridge_id is in the explicit
    # superseded-bridge list. Callers without an explicit list fall back to
    # the legacy "any bridge_id different from the new one" behavior.
    if superseded_bridge_ids is not None:
        if not superseded_bridge_ids:
            return []
        placeholders = ",".join("?" for _ in superseded_bridge_ids)
        cursor = await db.execute(
            f"""
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND claim_bridge_id IN ({placeholders})
            """,
            (agent_id, machine_id, *superseded_bridge_ids),
        )
    else:
        cursor = await db.execute(
            """
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND COALESCE(claim_bridge_id, '') != ?
            """,
            (agent_id, machine_id, superseding_bridge_id),
        )
    rows = await cursor.fetchall()
    if not rows:
        return []

    affected_run_ids: list[str] = []
    for row in rows:
        affected_run_ids.append(row["id"])
        previous_bridge_id = (row["claim_bridge_id"] or "").strip()
        owner_label = previous_bridge_id or "legacy-unowned"
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', error_text = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                f'Run was owned by superseded bridge instance "{owner_label}" and was replaced by "{superseding_bridge_id}" during re-registration',
                finished_at,
                row["id"],
            ),
        )
        await _append_dispatch_event(
            db,
            row["id"],
            "failed",
            f"Register supersession: {owner_label} -> {superseding_bridge_id}",
        )
    return affected_run_ids


async def _stop_virtual_terminals_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    superseded_bridge_ids: list[str],
    now: str,
) -> None:
    """Mark synthesized virtual rpc terminal_sessions stopped when the
    bridge that owned them is superseded.

    Operator-reported symptom (2026-05-22): after restarting aify-comms,
    multiple managed pi/hermes agents flipped to `online` immediately
    even though no message had been sent and the bridge had freshly
    started — its in-memory PiSession pool was empty so there was no
    actual omp process behind the terminal_session row. Stale rows
    survive bridge restarts; the worker-detection rule then trusts the
    DB and reports `online`. Cleaning them up at supersession time is
    the right correctness fix.
    """
    if not superseded_bridge_ids:
        return
    placeholders = ",".join("?" for _ in superseded_bridge_ids)
    # Defense-in-depth (code review I6, 2026-05-22): scope by agent_id
    # too. Each bridge process today has exactly one AIFY_AGENT_ID so
    # bridge_id is unique per agent, but if multi-agent bridges land
    # later this prevents cross-agent terminal slaughter.
    cursor = await db.execute(
        f"""
        SELECT id, agent_id FROM terminal_sessions
        WHERE bridge_id IN ({placeholders})
          AND agent_id = ?
          AND command IN ({",".join("?" for _ in VIRTUAL_RPC_COMMAND_SET)})
          AND status NOT IN ('stopped', 'failed')
        """,
        (*superseded_bridge_ids, agent_id, *VIRTUAL_RPC_COMMAND_SET),
    )
    rows = await cursor.fetchall()
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = COALESCE(stopped_at, ?),
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, "Superseded by bridge re-registration; in-memory worker pool empty after restart.", terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "virtual_rpc_stopped_on_bridge_supersession",
            json.dumps({"agentId": owner_agent, "supersededBridgeIds": superseded_bridge_ids}),
        )
        if owner_agent:
            # Clear the agent's virtualTerminal* pointers so dashboard
            # status correctly reports `available` until the next dispatch
            # spawns a fresh worker.
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)
