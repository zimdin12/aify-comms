"""Liveness: how long before something counts as dead, and the predicates that decide. Leaf module.

THE OLDEST OUTSTANDING ITEM IN THE SERIES. docs/ROADMAP.md has carried "Post-v0.5 — the consolidation
the borrows are waiting for" since v0.5.0, reviewer-ordered as **liveness family first**. This is it.

TEN predicates and the FOUR staleness thresholds they apply, which until now lived up to 4,900 lines
apart in the carrier — thresholds declared near the top, readers scattered through the middle, so
answering "how long until a bridge is considered stale" meant reading two distant parts of a
6,500-line module.

(That sentence said "Seven predicates and the three staleness thresholds" until slice 10 added three
more of each and made it false. Counts in prose go stale on the next commit; this one is now stated
without line numbers for the same reason, since those moved too.)

Owning the thresholds AND the predicates together is the point. A staleness constant separated from
the predicate that applies it is how the two drift, and drift here means an agent that reads live on
one code path and dead on another — the class behind the status-flap and stranded-claim bugs.

DB ACCESS: every predicate takes `db` explicitly and issues reads only. None opens a connection,
commits, or rolls back, so each joins whatever transaction its caller already has. That is what makes
them movable under the reviewer's rule for DB-touching helpers rather than pure ones.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from service.api_core.serialization import _json_loads_or
from service.clock import iso_to_epoch as _iso_to_epoch
from service.reconcilers.sessions import LIVE_SESSION_STATUSES


CONSOLE_WORKING_LEASE_SECONDS = 20
CHANNEL_SIDECAR_STALE_SECONDS = 180
ACTIVE_RUN_BRIDGE_STALE_SECONDS = 120


async def _has_live_terminal_session(db, agent_id: str) -> bool:
    """Plan 4: True when this agent has a live terminal_session row
    (managed-via-wrapper path).

    Plan 5 follow-up (2026-05-26): synth/virtual terminals (id prefix
    `vterm_`) MUST NOT count as live for this check. Plan 4 deprecated
    synth terminals for wrapper-backed runtimes (see
    `_synth_terminal_should_be_created`), but pre-Plan-4 rows persist in
    operator DBs with `status='running'` and no cleanup path. Observed
    2026-05-26 — sc-coder, sc-architect kept showing `online` after Plan
    5 deploy because their stale 2026-05-24 `vterm_*` rows hid the dead
    worker from the gate.
    """
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT COUNT(*) AS cnt FROM terminal_sessions
            WHERE agent_id = ?
              AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')
              AND id NOT LIKE 'vterm_%'
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        return bool(row and int(row["cnt"] or 0) > 0)
    except Exception:
        return False


async def _has_live_channel_sidecar(db, agent_id: str) -> bool:
    """Task 1.6 (2026-05-30): True when a standalone channel sidecar
    (claude-channel.js for claude; the `hermes-managed-host.js run <agent>`
    gateway delivery loop for hermes) is currently heartbeating for this agent.

    This is the deliverability/liveness signal for runtimes whose managed wake
    is delivered by a standalone channel sidecar that owns NO wrapper PTY
    (hermes via its hidden `hermes dashboard --tui` gateway host). It is the
    runtime-agnostic equivalent of claude's `_has_live_terminal_session` gate:
    claude's sidecar runs INSIDE the claude-aify wrapper PTY (so a live PTY
    terminal_session is its liveness proof), whereas hermes's gateway loop is a
    separate process whose proof is its own `bridge_kind='channel-sidecar'`
    bridge_instances row with a fresh last_seen (kept fresh by the claim poll loop).

    Returns False when no such row exists (no sidecar ever ran), the row is
    superseded, or its heartbeat is older than CHANNEL_SIDECAR_STALE_SECONDS
    (the sidecar process died) — so status falls back to `available` instead of
    a falsely positive `online`.
    """
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT last_seen FROM bridge_instances
            WHERE agent_id = ?
              AND bridge_kind = 'channel-sidecar'
              AND COALESCE(superseded_by, '') = ''
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        last_seen = _iso_to_epoch(str(row["last_seen"] or ""))
        if not last_seen:
            return False
        age = datetime.now(timezone.utc).timestamp() - last_seen
        return age <= CHANNEL_SIDECAR_STALE_SECONDS
    except Exception:
        return False


async def _has_live_managed_wrapper_child(db, agent_id: str) -> bool:
    """FIX SET B2 (2026-06-03): True when a live `managed-wrapper-child` bridge
    (the visible TUI's in-session aify-comms MCP, registered by a wrapper-backed
    managed worker — codex/hermes) is currently heartbeating for this agent.

    This is the deliverability signal a wrapper-backed managed 'channel' run needs:
    the run is rejected `managed_wrapper_child_required` until such a bridge claims
    it, so the send-path autostart must NOT treat a leftover RESIDENT-mode terminal
    as satisfying the managed coldstart. Returns False when no row exists, the row
    is superseded, or its heartbeat is older than ACTIVE_RUN_BRIDGE_STALE_SECONDS
    (mirrors _has_live_channel_sidecar's cutoff construction exactly)."""
    if db is None:
        return False
    try:
        cursor = await db.execute(
            """
            SELECT last_seen FROM bridge_instances
            WHERE agent_id = ?
              AND bridge_kind = 'managed-wrapper-child'
              AND COALESCE(superseded_by, '') = ''
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        last_seen = _iso_to_epoch(str(row["last_seen"] or ""))
        if not last_seen:
            return False
        age = datetime.now(timezone.utc).timestamp() - last_seen
        # NOTE (Bug D, 2026-07-02): a worker that crashed at boot leaves a fresh-but-dead
        # heartbeat row that would satisfy this age check for the full stale window and
        # suppress the send-path coldstart. That is fixed at the DEATH site, not here
        # (FIX B3 requires a fresh wrapper-child to count even when the terminal ROW
        # transiently failed): report_terminal_dead supersedes the dead terminal's
        # wrapper-child rows the moment the PTY is known dead. (The ghost-console
        # reconcile does NOT supersede — by the time it fires the heartbeat is already
        # past this stale window, so there is nothing left to mask.)
        return age <= ACTIVE_RUN_BRIDGE_STALE_SECONDS
    except Exception:
        return False


async def _claimer_lease_row(db, agent_id: str):
    """WS5 Task 5.1: fetch the agent's single claimer-lease row (or None when no
    lease has EVER been recorded). Returns the raw row so callers can distinguish
    'no lease ever' (fall back to the sidecar/bridge check — lazy-claim contract)
    from 'lease present' (the lease is authoritative)."""
    if db is None:
        return None
    try:
        cursor = await db.execute(
            "SELECT agent_id, bridge_id, state, updated_at FROM claimer_leases WHERE agent_id = ?",
            (agent_id,),
        )
        return await cursor.fetchone()
    except Exception:
        return None


async def _agent_has_live_terminal(db, agent_id: str) -> bool:
    """True when the agent owns a LIVE terminal_sessions row (the real console/
    worker truth), keyed on the terminal's OWN status — not the frozen
    agent_sessions.terminal_status denorm.

    A managed session's backing console is alive when SOME terminal_sessions row
    for the agent is in a live state (and is not a deprecated synth `vterm_*`
    row). Mirrors the live-truth join the dashboard agent-dot already trusts; the
    session deriver uses it so the badge stops reading the stale denorm.
    """
    if db is None:
        return False
    live_ph = ",".join("?" for _ in LIVE_SESSION_STATUSES)
    try:
        cur = await db.execute(
            f"""
            SELECT 1 FROM terminal_sessions
            WHERE agent_id = ?
              AND LOWER(COALESCE(status, '')) IN ({live_ph})
              AND id NOT LIKE 'vterm_%'
            LIMIT 1
            """,
            (agent_id, *[s.lower() for s in LIVE_SESSION_STATUSES]),
        )
        return (await cur.fetchone()) is not None
    except Exception:
        return False


async def _bridge_is_superseded(db, bridge_id: str, agent_id: str) -> bool:
    if not bridge_id:
        return False
    cursor = await db.execute(
        "SELECT superseded_by, bridge_kind FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if not row:
        return False
    return bool((row["superseded_by"] or "").strip())


async def _console_working_lease_fresh(db, agent_id: str) -> bool:
    """True when the agent's console-working spinner lease (agent_console_signal.working_at)
    is within CONSOLE_WORKING_LEASE_SECONDS. Both StatusInputs builders OR this (gated on a
    live worker) into in_turn so the spinner-driven `working` is identical on the served
    byproduct path AND the WS-push path (_gather_status_inputs) — without this, /console-working
    pushes `online` while the next poll serves `working` (push/poll flicker)."""
    if db is None:
        return False
    try:
        row = await (await db.execute(
            "SELECT working_at FROM agent_console_signal WHERE agent_id = ?", (agent_id,)
        )).fetchone()
    except Exception:
        return False
    if not row:
        return False
    seen = _iso_to_epoch(str((row["working_at"] if "working_at" in row.keys() else "") or "").strip())
    return bool(seen and datetime.now(timezone.utc).timestamp() - seen <= CONSOLE_WORKING_LEASE_SECONDS)

# ---------------------------------------------------------------------------------------------------
# v0.5.4 slice 10 completes the family. ROADMAP's post-v0.5 item named six predicates; four came in
# slice 6 and these two are the rest that layer 0 can reach. `_agent_liveness` is NOT here: it calls
# other carrier helpers, so it is layer 1+ and moving it is a separate decision, not an omission.
# `CLAIMER_LEASE_STALE_SECONDS` came with its only reader.
# ---------------------------------------------------------------------------------------------------

CLAIMER_LEASE_STALE_SECONDS = 240


async def _has_live_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1 (2026-06-02): True when the agent has a currently-LIVE
    delivery-loop claimer lease.

    A lease is the POSITIVE "a loop is a live claimer RIGHT NOW" signal the
    delivery loop POSTs on becoming ready (`claimer-acquire`) and clears on
    teardown (`claimer-release`). This is the disambiguator that unblocks the
    Task 5.1b deaf-target fail-fast: unlike the channel-sidecar heartbeat (which
    a not-yet-polled healthy loop has not written yet), a lease is set the moment
    the loop is ready and cleared the moment it exits.

    True ONLY when the lease state is 'acquired' AND its last refresh is within
    CLAIMER_LEASE_STALE_SECONDS (backstop for a missed release after a crash).
    A 'released' lease ⇒ False IMMEDIATELY (no staleness wait). No lease row ⇒
    False (caller must treat absence-of-lease as 'fall back to the sidecar check',
    NOT as deaf — see `_has_recorded_claimer_lease`).
    """
    row = await _claimer_lease_row(db, agent_id)
    if not row:
        return False
    if str(row["state"] or "").strip().lower() != "acquired":
        return False
    updated = _iso_to_epoch(str(row["updated_at"] or ""))
    if not updated:
        return False
    age = datetime.now(timezone.utc).timestamp() - updated
    return age <= CLAIMER_LEASE_STALE_SECONDS


async def _has_recorded_claimer_lease(db, agent_id: str) -> bool:
    """WS5 Task 5.1: True when a lease has EVER been recorded for this agent
    (acquired OR released). Used to decide whether the lease is AUTHORITATIVE
    (so a released/stale lease ⇒ deaf) vs whether to fall back to the
    sidecar/bridge-freshness check (no lease ever ⇒ pre-existing/older loop or a
    lazy claimer that has not polled — must NOT be treated as deaf)."""
    return await _claimer_lease_row(db, agent_id) is not None


async def _resident_bridge_is_fresh(db, row, *, lease_seconds: int) -> bool:
    lease = max(15, int(lease_seconds or 150))
    bridge_id = str(_json_loads_or(row["runtime_state"], {}).get("bridgeInstanceId") or "").strip()
    if bridge_id:
        cursor = await db.execute(
            "SELECT last_seen, superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
            (bridge_id, row["id"]),
        )
        bridge = await cursor.fetchone()
        if bridge and not str((bridge["superseded_by"] if "superseded_by" in bridge.keys() else "") or "").strip():
            seen_s = _iso_to_epoch((bridge["last_seen"] or ""))
            if seen_s and time.time() - seen_s <= lease:
                return True
    # Fallback (operator-reported 2026-05-31, sc-manager): an IDLE resident
    # claude's MCP bridge is NOT heartbeated — the turn-busy heartbeat only fires
    # during an active turn, and the session-handle heartbeat only POSTs when the
    # session id CHANGES. So a live-but-idle resident goes stale after the lease
    # and the dashboard shows it dead. Its channel sidecar (claude-channel.js) is
    # a CHILD of the live session and polls /dispatch/claim every ~3s, so a fresh,
    # non-superseded channel-sidecar bridge is proof the resident session is
    # alive. Treat that as fresh too. (If the session dies, the sidecar child dies
    # and its bridge goes stale — so this never masks a genuinely dead resident.)
    # KEPT (Task A' #154, 2026-06-01): still needed even with the 30s liveness
    # beat. Residents are operator-launched and may run a MIXED bridge version
    # that predates liveness-heartbeat.js, so the resident MCP bridge can still
    # go stale while idle; a live channel sidecar is the proof-of-life fallback.
    # Removal probe broke test_idle_resident_with_live_sidecar_is_not_stale.
    if await _has_live_channel_sidecar(db, row["id"]):
        return True
    return False
