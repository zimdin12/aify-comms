"""Session-row reconcilers: what a session says versus what is actually alive.

v0.5 slice 3a, extracted from `service/routers/api_v2.py`. See `docs/V0.5_SLICE3.md` for the measured
dependency table — taken with the CORRECTED scan that walks every name, not only call sites, after
slice 2 lost 372 tests to three constants a call-only scan could not see. `LIVE_SESSION_STATUSES` is
one of those constants; it moved here with the functions that read it.

MOVE PLUS ONE DECLARED SEAM, not a pure move. Both reconcilers read exactly one setting,
`resident_lease_seconds`, and the caller now passes it as a required scalar from the settings its
sweep pass already loads. Third use of the shape approved in slice 1a: pure derivation in the caller,
required parameter in the callee, no optional default that could quietly mean something narrower than
policy.

`_agent_liveness` did NOT come with them (slice 3b, deferred): it drags `_agent_has_live_terminal`,
`_has_live_channel_sidecar` and `_resident_bridge_is_fresh`, which `api_v2.py`'s own TODO calls "a
separate, risky migration" because of their many callers. `_compute_session_display_status` calls it
through the router, which is safe in that direction — the router is loaded by call time.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from service.clock import now as _now
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state



logger = logging.getLogger(__name__)

# Phase 3 (2026-06-03) — ONE canonical `agent_sessions.status` live set.
# This is the FULL set of agent_sessions.status values that count as a live
# (not-yet-terminal) session row, used by the session reconcilers
# (_reconcile_dead_session_status / _reconcile_duplicate_resident_sessions),
# the new on-read deriver (_compute_session_display_status), and embedded into
# the dashboard bootstrap config so Dashboard Next reads the SAME set instead
# of its own wider hardcode. It is a SUPERSET of _LIVE_SESSION_STATUSES above:
# _LIVE_SESSION_STATUSES is the narrower "live agent-status engine" gate used by
# _compute_live_status_cache (which treats attached/active/idle as worker-detail
# rather than session-live), whereas this set is the session-row liveness set the
# reconcilers historically used as their inline `live_states` tuple. Keep these
# two distinct on purpose — collapsing them would change the agent-status engine.
# Members are EXACTLY the inline `live_states` tuple the two session reconcilers
# historically used, so adopting the constant is behavior-preserving for them.
LIVE_SESSION_STATUSES = {
    "running",
    "attached",
    "active",
    "idle",
    "starting",
    "recovering",
}
# ENDED `agent_sessions.status` values — the complement of LIVE_SESSION_STATUSES that
# `_current_agent_session_row` filters on (R2c, 2026-07-26). A session in one of these is over and
# can never become live again, so it must never answer "what is this agent's CURRENT session".
#
# Deliberately its OWN set even though the members coincide with two neighbours today, because a
# set's name encodes its purpose and these are read by unrelated call sites:
#   * `_SESSION_DELETE_ALLOWED_STATUSES` is a DELETION allowlist (reusing it as a filter once broke
#     comms_restart / comms_compact — see the c2f0e38 round);
#   * `_TERMINAL_DEAD_STATUSES` is about `terminal_sessions.status`, a different table.
# NOT the same thing as the TRANSITIONAL statuses `restarting` / `cli-takeover`, which are neither
# live nor ended and must keep resolving as current so a restart can find its own session.


async def _compute_session_display_status(db, session_row, agent_row=None) -> str:
    """Phase 3 (2026-06-03) — DERIVE the EFFECTIVE session status from LIVE truth.

    GET /sessions historically served `agent_sessions.status` RAW (a denorm that
    drifts and is only corrected lazily by the reconcilers), so the session badge
    and the agent dot disagreed ("Stopped/Stale but running"). This deriver makes
    the stored status a CACHE: the displayed status is computed from the same live
    truth the agent dot derives from.

    Rules (only ever DOWNGRADES a live-looking stored status to 'stopped'; never
    promotes a terminal stored status):
      - stored status not in LIVE_SESSION_STATUSES → return it unchanged
        (genuinely-terminal rows pass through).
      - owning agent.status == 'stopped' → 'stopped'.
      - managed/managed-warm session: live iff a live terminal_sessions row backs
        it (the agent has a live, non-synth terminal) OR a live channel sidecar /
        managed-wrapper-child proves deliverability; otherwise 'stopped'.
      - resident session: live iff the resident bridge is fresh (or a live
        sidecar); otherwise 'stopped'.
    The stored row is NOT mutated here — only the returned display value changes.
    """
    stored = str((session_row["status"] if session_row is not None else "") or "").strip()
    if stored.lower() not in {s.lower() for s in LIVE_SESSION_STATUSES}:
        # INVERSE truthfulness (2026-06-11): a DEAD stored status with a LIVE bound terminal
        # is factually wrong — the dashboard rendered "Console stopped" over a visibly-running
        # terminal (next-manager, twice: a heartbeat lapse let the dead-session reconciler
        # downgrade the row AFTER the bind, so the bind-moment promotion never re-fired).
        # Display 'running' when the bound terminal row is live and the agent is not
        # operator-stopped. Display-only — the stored row is never mutated, and operator
        # disable (agents.status='stopped') still wins.
        keys0 = session_row.keys() if session_row is not None else []
        bound_terminal = str((session_row["terminal_id"] if "terminal_id" in keys0 else "") or "").strip()
        if bound_terminal and not bound_terminal.startswith("vterm_"):
            try:
                if agent_row is None:
                    agent_row = await (await db.execute(
                        "SELECT * FROM agents WHERE id = ?",
                        (str((session_row["agent_id"] if "agent_id" in keys0 else "") or ""),),
                    )).fetchone()
                agent_stopped = bool(agent_row and str(agent_row["status"] or "").strip().lower() == "stopped")
                if not agent_stopped:
                    _t = await (await db.execute(
                        "SELECT status FROM terminal_sessions WHERE id = ?",
                        (bound_terminal,),
                    )).fetchone()
                    if _t and str(_t["status"] or "").strip().lower() in {"starting", "attached", "running", "active", "idle"}:
                        return "running"
            except Exception:
                pass
        return stored  # already terminal — display as-is.
    agent_id = str((session_row["agent_id"] if session_row is not None else "") or "").strip()
    if not agent_id:
        return stored
    if agent_row is None:
        try:
            agent_row = await (
                await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
            ).fetchone()
        except Exception:
            agent_row = None
    # Honor a manually-stopped agent: its sessions are not live regardless of any
    # lingering terminal/bridge row.
    if agent_row is not None and str(agent_row["status"] or "").strip().lower() == "stopped":
        return "stopped"

    keys = session_row.keys()
    raw_owner_mode = str((session_row["owner_mode"] if "owner_mode" in keys else "") or "").strip().lower()
    session_mode = str((session_row["mode"] if "mode" in keys else "") or "").strip().lower()
    is_resident = raw_owner_mode == "resident" or session_mode == "resident"

    # FUNCTION-SCOPE IMPORT, deliberately. `_agent_liveness` moved to api_core/liveness.py in v0.5.4,
    # and that module imports LIVE_SESSION_STATUSES from THIS one — so a module-level import here is a
    # cycle. The underlying inversion is that an api_core leaf reaches up to a reconciler for a
    # constant; moving LIVE_SESSION_STATUSES to a leaf would fix it properly and is not this slice.
    from service.api_core.liveness import _agent_liveness

    liveness = await _agent_liveness(db, agent_id, agent_row=agent_row)
    if is_resident:
        # Resident liveness == a fresh, non-superseded owning bridge within the
        # lease (or a live channel sidecar, which the freshness helper already
        # folds in). This is the SAME signal the agent dot derives a live resident
        # from, so the badge and the dot agree.
        if liveness["resident_bridge_fresh"]:
            return stored
        return "stopped"
    # Managed / managed-warm: liveness is the LIVE CONSOLE/WORKER truth — a live,
    # non-synth terminal_sessions row. This is the live-truth join case (a) of
    # _reconcile_dead_session_status uses, so the on-read badge and the lazy
    # reconciler AGREE: a managed session with all-dead terminals derives 'stopped'
    # (killing "Stopped/Stale but running"). We deliberately do NOT treat a bare
    # channel-sidecar / managed-wrapper-child heartbeat as session-live here: for
    # the sidecar-delivery runtimes a live sidecar WITHOUT a live console is a
    # headless orphan the agent dot itself reports `available` (not online), so
    # honoring it would re-introduce the badge/dot divergence this fix removes.
    if liveness["console_live"] or liveness["worker_live"]:
        return stored
    return "stopped"


async def _reconcile_duplicate_resident_sessions(db, *, lease_seconds: int, limit: int = 500) -> int:
    """Reconcile (2026-06-03): a resident agent should have exactly ONE live
    resident session, but the resident session id is a hash of session_handle, so
    each relaunch with a new native handle minted a NEW resident_* row while older
    ones stayed 'running' — the dashboard showed duplicate/stale resident sessions
    the operator could not tell apart ("no way of knowing what to delete"). The
    register-time dedup only retires siblings on a FRESH register; this collapses
    the EXISTING duplicates: keep the most-recently-seen resident session per
    agent, retire the rest. Returns rows retired.

    LIVE-SESSION GUARD (HAZARD 2 fix, 2026-06-03): a resident agent_sessions row's
    last_seen is FROZEN at register time — the 30s heartbeat updates agents /
    bridge_instances, NOT agent_sessions — so ranking siblings by `last_seen DESC`
    can keep a dead-but-newer row and retire a LIVE one. We therefore NEVER retire a
    non-survivor sibling whose owning bridge (owner_bridge_id) is still FRESH: a
    fresh, non-superseded bridge_instances row (last_seen within
    resident_lease_seconds) proves that session is still live. The freshest session
    per agent is still the survivor, but among the rest we retire ONLY those whose
    owning bridge is stale/gone — a sibling with a fresh bridge is LEFT ALONE (a
    transient duplicate is safer than retiring a live session)."""
    # SEAM NORMALIZATION, v0.5 slice 3a (declared). One key, `resident_lease_seconds`,
    # now supplied by the caller from its pass settings — same key, same default, same
    # use. `_load_settings` lives in the router and importing it back would be the cycle
    # this release removes. Third use of the shape the reviewer approved in slice 1a.
    # Phase 3 item 4 (2026-06-03): use the ONE canonical LIVE_SESSION_STATUSES set
    # (module-level) instead of an inline tuple — lowercased for the IN(...) match.
    live_states = tuple(sorted(s.lower() for s in LIVE_SESSION_STATUSES))
    state_ph = ",".join("?" for _ in live_states)
    rows = await (
        await db.execute(
            f"""
            SELECT id, agent_id, owner_bridge_id
            FROM agent_sessions
            WHERE mode = 'resident' AND status IN ({state_ph})
            ORDER BY agent_id ASC, last_seen DESC, rowid DESC
            """,
            list(live_states),
        )
    ).fetchall()

    async def _owner_bridge_is_fresh(owner_bridge_id: str, agent_id: str) -> bool:
        """True when the session's owning bridge has a fresh, non-superseded
        bridge_instances row (last_seen within the resident lease) — i.e. the
        session is still live and must NOT be retired as a duplicate."""
        bid = str(owner_bridge_id or "").strip()
        if not bid:
            return False
        try:
            cur = await db.execute(
                """
                SELECT 1 FROM bridge_instances
                WHERE id = ? AND agent_id = ?
                  AND COALESCE(superseded_by, '') = ''
                  AND datetime(last_seen) > datetime('now', ?)
                LIMIT 1
                """,
                (bid, agent_id, f"-{int(lease_seconds)} seconds"),
            )
            return (await cur.fetchone()) is not None
        except Exception:
            return False

    # Group each agent's live resident sessions (rows arrive freshest-first by the
    # ORDER BY). Annotate each with whether its owning bridge is still fresh.
    per_agent: dict[str, list[dict]] = {}
    for row in rows:
        agent_id = str(row["agent_id"] or "")
        keys = row.keys()
        owner_bridge_id = str(row["owner_bridge_id"] if "owner_bridge_id" in keys else "")
        per_agent.setdefault(agent_id, []).append(
            {
                "id": str(row["id"]),
                "owner_bridge_id": owner_bridge_id,
                "bridge_fresh": await _owner_bridge_is_fresh(owner_bridge_id, agent_id),
            }
        )

    retire: list[str] = []
    retire_agents: dict[str, str] = {}
    for agent_id, sessions in per_agent.items():
        if len(sessions) < 2:
            continue  # single session → no-op
        # Survivor selection: prefer a session whose owning bridge is still FRESH
        # (a LIVE session must always win over a dead-but-newer sibling — the
        # resident last_seen is frozen at register time and can rank a dead row
        # newest). Among equal liveness the SQL last_seen order already put the
        # freshest first, so the first live session (or the first row overall when
        # none are live) is the survivor.
        survivor = next((s for s in sessions if s["bridge_fresh"]), sessions[0])
        for s in sessions:
            if s["id"] == survivor["id"]:
                continue
            # Retire a non-survivor ONLY when its owning bridge is stale/gone — a
            # still-fresh bridge means it's a LIVE session, left alone (a transient
            # duplicate is safer than retiring a live session).
            if s["bridge_fresh"]:
                continue
            retire.append(s["id"])
            retire_agents[s["id"]] = agent_id
    retire = retire[:limit]
    if not retire:
        return 0
    now = _now()
    id_ph = ",".join("?" for _ in retire)
    await db.execute(
        f"""
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE id IN ({id_ph}) AND status IN ({state_ph})
        """,
        [now, *retire, *live_states],
    )
    # Phase 3 item 5 (2026-06-03): invalidate the live-state cache for every agent
    # whose duplicate session we just retired, so the derived dot refreshes in the
    # SAME reconcile pass. Best-effort.
    for aid in {v for v in retire_agents.values() if v}:
        try:
            await _invalidate_agent_live_state(db, aid)
        except Exception:
            pass
    await db.commit()
    return len(retire)


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
