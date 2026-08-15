"""Marking a session dead when the thing that was serving it is gone.

RELOCATED from `service/reconcilers/sessions.py` in v0.5.4, byte-identical. That module held five
functions and 552 lines; this one was 210 of them and shares nothing with the rest — it calls no
sibling in that file and reads none of its constants. One module per responsibility is how
`service/reconcilers/` is organised, and this is a responsibility.

WHAT IT GUARDS. A session whose backing is gone but whose row still says `running` is the shape that
strands work: dispatch keeps targeting it, the dashboard keeps showing it live, and nothing ever
closes the loop because the process that would have is the one that died. The freshness test is a
LEASE, not a heartbeat comparison, which is why `_agent_has_fresh_bridge` is nested here rather than
shared — it answers this reconciler's question and no other.

DB ACCESS: `db` is passed in, and the caller commits — `sweep.py` wraps each step in `_commit_step`,
so a reconciler that committed on its own would break that batching.
"""
from __future__ import annotations

from service.api_core.tuning import LIVE_SESSION_STATUSES
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


async def _reconcile_dead_session_status(db, *, lease_seconds: int, limit: int = 500) -> int:
    """Downgrade a live-status `agent_sessions` row to 'stopped' once its BACKING
    is dead (2026-06-03).

    PROBLEM: a session row keeps a live status (running/attached/…) after the thing
    that backs it died, so the dashboard renders a contradictory row like
    "Stopped … running" / "Stale … running". The ghost-console / managed-worker
    hygiene reconcile marks the TERMINAL dead but never updates the SESSION row, and
    a resident session is never downgraded when its OWNING bridge dies. Verified
    live: mp-senior-dev (managed-warm session 'running' but terminal 'failed',
    error='reconciled_managed_ghost_console_dead_worker', agent 'stopped', no live
    worker) and lc-manager (resident session 'running' but the operator closed the
    console and its owning resident bridge is stale/gone).

    Marks a live-status row 'stopped' (+ ended_at) for three dead-backing cases:
      a. MANAGED/managed-warm session whose OWNING/current live terminal truth is
         dead — i.e. the session HAS terminal_sessions rows but NONE is live (all
         stopped/failed/exited/lost). Phase 3 fix (2026-06-03): this now JOINS the
         live terminal_sessions table instead of reading the FROZEN
         agent_sessions.terminal_status denorm. The worker-hygiene reaper only
         updates that denorm for terminals that are STILL active, so a terminal
         that went stopped/failed on its OWN left the denorm frozen at 'attached'
         and case (a) MISSED it (cms-manager/lc-coder/lc-tech-lead live showed
         status='running' + terminal_status='attached'/'' while their actual
         terminal_sessions row was stopped/failed). Joining the live table catches
         them.
      b. ANY session whose owning agent is stopped (agents.status='stopped').
      c. RESIDENT session whose OWNING bridge is gone — owner_bridge_id is empty,
         OR has no non-superseded bridge_instances heartbeat within
         resident_lease_seconds (default 150).

    FALSE-POSITIVE GUARD: never stops a genuinely-live session. A managed session
    with a healthy terminal stays. A managed session with NO terminal rows AT ALL
    is left alone (a just-starting session before its console attaches — case (a)
    requires that the session HAS terminals and ALL are dead). A resident session
    whose owning bridge IS fresh stays — case (c) is keyed ONLY on the bridge
    heartbeat (mirroring _owner_bridge_is_fresh in
    _reconcile_duplicate_resident_sessions), never on the live-state engine's
    derived 'stale' (ci-manager/ci-senior-dev read 'stale' there but HAVE a fresh
    bridge + live session and must NOT be stopped). Returns rows changed."""
    # SEAM NORMALIZATION, v0.5 slice 3a (declared). One key, `resident_lease_seconds`,
    # now supplied by the caller from its pass settings — same key, same default, same
    # use. `_load_settings` lives in the router and importing it back would be the cycle
    # this release removes. Third use of the shape the reviewer approved in slice 1a.
    # Phase 3 item 4 (2026-06-03): ONE canonical LIVE_SESSION_STATUSES set.
    live_states = tuple(sorted(s.lower() for s in LIVE_SESSION_STATUSES))
    state_ph = ",".join("?" for _ in live_states)
    now = _now()
    changed = 0
    mutated_agents: set[str] = set()

    # Case (a): managed session whose LIVE terminal truth is dead. JOIN the
    # terminal_sessions table (the real console/worker state) instead of the
    # frozen agent_sessions.terminal_status denorm. A managed session is dead when
    # it HAS at least one (non-synth) terminal row but NONE is live. The HAVING
    # check on the live-count, gated by an EXISTS on any terminal row, encodes
    # "has terminals AND all dead" without falsely stopping a session that simply
    # hasn't spawned a console yet (no terminal rows → not matched). Computed in
    # Python so we can also invalidate each mutated agent's live-state cache.
    dead_terminal_rows = await (
        await db.execute(
            f"""
            SELECT s.id AS id, s.agent_id AS agent_id
            FROM agent_sessions s
            WHERE s.owner_mode = 'managed'
              AND s.status IN ({state_ph})
              AND EXISTS (
                SELECT 1 FROM terminal_sessions t
                WHERE t.session_id = s.id AND t.id NOT LIKE 'vterm_%'
              )
              AND NOT EXISTS (
                SELECT 1 FROM terminal_sessions t
                WHERE t.session_id = s.id
                  AND t.id NOT LIKE 'vterm_%'
                  AND LOWER(COALESCE(t.status, '')) IN ({state_ph})
              )
            """,
            [*live_states, *[s.lower() for s in live_states]],
        )
    ).fetchall()
    dead_a_ids = [str(r["id"]) for r in (dead_terminal_rows or [])]
    for r in (dead_terminal_rows or []):
        aid = str(r["agent_id"] or "")
        if aid:
            mutated_agents.add(aid)
    if dead_a_ids:
        id_ph = ",".join("?" for _ in dead_a_ids)
        cur_a = await db.execute(
            f"""
            UPDATE agent_sessions
            SET status = 'stopped', ended_at = ?
            WHERE id IN ({id_ph}) AND status IN ({state_ph})
            """,
            [now, *dead_a_ids, *live_states],
        )
        changed += int(cur_a.rowcount or 0)

    # Case (b): ANY live-status session whose owning agent is stopped. Unambiguous
    # DB truth — a direct set-based UPDATE. Collect the affected agents first so we
    # can invalidate their live-state caches too.
    stopped_agent_rows = await (
        await db.execute(
            f"""
            SELECT DISTINCT agent_id FROM agent_sessions
            WHERE status IN ({state_ph})
              AND agent_id IN (SELECT id FROM agents WHERE status = 'stopped')
            """,
            list(live_states),
        )
    ).fetchall()
    for r in (stopped_agent_rows or []):
        aid = str(r["agent_id"] or "")
        if aid:
            mutated_agents.add(aid)
    cur = await db.execute(
        f"""
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE status IN ({state_ph})
          AND agent_id IN (SELECT id FROM agents WHERE status = 'stopped')
        """,
        [now, *live_states],
    )
    changed += int(cur.rowcount or 0)

    # Case (c): resident session whose owning bridge is stale/gone. Done in Python
    # (fetch live resident rows + owner_bridge_id, test heartbeat freshness, batch
    # the UPDATE) to avoid a fragile correlated subquery and to mirror
    # _owner_bridge_is_fresh's exact cutoff construction (non-superseded
    # bridge_instances row with last_seen within the lease).
    resident_rows = await (
        await db.execute(
            f"""
            SELECT id, agent_id, owner_bridge_id
            FROM agent_sessions
            WHERE mode = 'resident' AND status IN ({state_ph})
            """,
            list(live_states),
        )
    ).fetchall()

    async def _agent_has_fresh_bridge(agent_id: str) -> bool:
        # AGENT-LEVEL liveness (NOT the session's recorded owner_bridge_id): a
        # relaunch leaves the session row pointing at the OLD (now superseded)
        # bridge while the agent registers a NEW fresh one — so keying on the
        # specific owner_bridge_id FALSE-POSITIVED live agents (ci-manager/
        # mp-manager have a fresh channel-sidecar / managed-wrapper-child but a
        # stale owner_bridge_id on the session). The session is live iff the AGENT
        # has ANY fresh, non-superseded bridge, regardless of which bridge the row
        # recorded.
        aid = str(agent_id or "").strip()
        if not aid:
            return False
        try:
            c = await db.execute(
                """
                SELECT 1 FROM bridge_instances
                WHERE agent_id = ?
                  AND COALESCE(superseded_by, '') = ''
                  AND datetime(last_seen) > datetime('now', ?)
                LIMIT 1
                """,
                (aid, f"-{int(lease_seconds)} seconds"),
            )
            return (await c.fetchone()) is not None
        except Exception:
            return False

    stop_ids: list[str] = []
    stop_agents: list[str] = []
    for row in (resident_rows or []):
        keys = row.keys()
        sid = str(row["id"])
        agent_id = str(row["agent_id"] or "")
        # Stop ONLY when the AGENT has no fresh bridge at all. A fresh bridge
        # (any kind) proves the agent is live, so its session is left alone — even
        # if the session's recorded owner_bridge_id is a stale post-relaunch id.
        if not await _agent_has_fresh_bridge(agent_id):
            stop_ids.append(sid)
            stop_agents.append(agent_id)
    stop_ids = stop_ids[: max(0, int(limit or 500))]
    stop_agents = stop_agents[: len(stop_ids)]
    if stop_ids:
        id_ph = ",".join("?" for _ in stop_ids)
        cur2 = await db.execute(
            f"""
            UPDATE agent_sessions
            SET status = 'stopped', ended_at = ?
            WHERE id IN ({id_ph}) AND status IN ({state_ph})
            """,
            [now, *stop_ids, *live_states],
        )
        changed += int(cur2.rowcount or 0)
        for aid in stop_agents:
            if aid:
                mutated_agents.add(aid)

    # Phase 3 item 5 (2026-06-03): invalidate the live-state cache for every agent
    # whose session we just mutated, so the derived agent dot refreshes in the SAME
    # reconcile pass (the closing _refresh_expired_agent_live_states only recomputes
    # rows already past refresh_after). Best-effort — never let a cache invalidate
    # fail the reconcile.
    for aid in mutated_agents:
        try:
            await _invalidate_agent_live_state(db, aid)
        except Exception:
            pass

    await db.commit()
    return changed
