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

from service.api_core.agent_sessions import _touch_current_agent_session
from service.api_core.liveness import _agent_liveness
from service.api_core.serialization import _json_loads_or
from service.api_core.tuning import LIVE_SESSION_STATUSES
from service.clock import now as _now
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state



logger = logging.getLogger(__name__)

# LIVE_SESSION_STATUSES moved to service/api_core/tuning.py in v0.5.4. It is imported above and
# still read here; it left because `api_core/liveness.py` needed it, and an api_core leaf importing
# a reconciler is the inversion that forced `_agent_liveness` to be imported inside a function body.
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



# --- read-path consistency repairs ------------------------------------------------------------
#
# RELOCATED from `service/routers/sessions.py` in v0.5.4, byte-identical. They are SESSION
# reconciliation and this module owns that; a router declaring them was the odd one out, since
# their sibling `_repair_terminal_session_consistency` already lives in
# `service/reconcilers/terminal_consistency.py` and is called from the same read path.
#
# THE MODULE MOVED; THE CALL SITE DID NOT. `GET /sessions` still awaits both inline, and the note
# there explaining why -- they correct the console/terminal binding shown in THAT response, so a
# 60s reconcile lag would surface a dead terminal as still-attached -- is unchanged and still
# true. "Not moved to reconcile" is a statement about the LOOP, not about which file declares
# them, and this move does not touch it.

# _reconcile_dead_session_status moved to service/reconcilers/dead_session_status.py in v0.5.4 —
# it is its own responsibility, calls nothing in this module and reads none of its constants.


async def _repair_current_session_freshness(db) -> int:
    cursor = await db.execute(
        """
        SELECT id, last_seen, runtime_state
        FROM agents
        WHERE session_mode = 'managed'
          AND runtime_state IS NOT NULL
          AND runtime_state != ''
          AND runtime_state != '{}'
        """
    )
    repaired = 0
    for row in await cursor.fetchall():
        runtime_state = _json_loads_or(row["runtime_state"], {})
        if not (runtime_state.get("spawnRequestId") or runtime_state.get("environmentId")):
            continue
        before = db.total_changes
        await _touch_current_agent_session(db, row["id"], runtime_state, row["last_seen"] or _now())
        if db.total_changes > before:
            repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _repair_superseded_recovering_sessions(db) -> int:
    now = _now()
    cursor = await db.execute(
        """
        SELECT old.id
        FROM agent_sessions old
        WHERE old.status IN ('starting', 'recovering', 'restarting')
          AND EXISTS (
            SELECT 1
            FROM agent_sessions current
            WHERE current.agent_id = old.agent_id
              AND current.id != old.id
              AND current.status = 'running'
              AND COALESCE(NULLIF(current.last_seen, ''), NULLIF(current.started_at, ''), '') >=
                  COALESCE(NULLIF(old.last_seen, ''), NULLIF(old.started_at, ''), '')
          )
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    for row in rows:
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?),
                last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
            WHERE id = ?
              AND status IN ('starting', 'recovering', 'restarting')
            """,
            (now, now, row["id"]),
        )
    await db.commit()
    return len(rows)
