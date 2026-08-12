"""Liveness: how long before something counts as dead, and the predicates that decide. Leaf module.

THE OLDEST OUTSTANDING ITEM IN THE SERIES. docs/ROADMAP.md has carried "Post-v0.5 — the consolidation
the borrows are waiting for" since v0.5.0, reviewer-ordered as **liveness family first**. This is it.

Seven predicates and the three staleness thresholds they apply, which until now lived 500 to 4,900
lines apart in the carrier: the thresholds were declared at lines 332, 524 and 831 while their readers
sat at 628, 2339, 2897, 3257 and 5224. Answering "how long until a bridge is considered stale" meant
reading two distant parts of a 6,500-line module.

Owning the thresholds AND the predicates together is the point. A staleness constant separated from
the predicate that applies it is how the two drift, and drift here means an agent that reads live on
one code path and dead on another — the class behind the status-flap and stranded-claim bugs.

DB ACCESS: every predicate takes `db` explicitly and issues reads only. None opens a connection,
commits, or rolls back, so each joins whatever transaction its caller already has. That is what makes
them movable under the reviewer's rule for DB-touching helpers rather than pure ones.
"""

from __future__ import annotations

from datetime import datetime, timezone

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
