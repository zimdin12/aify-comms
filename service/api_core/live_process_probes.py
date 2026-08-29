"""Is something belonging to this agent actually ALIVE right now — six independent probes.

Extracted from `service/api_core/liveness.py` in v0.5.4. This is a LAYER boundary as much as a
subject one: everything here answers a question by looking at a row and a clock, and nothing here
aggregates. `_agent_liveness` and the other derived facts stay behind and now import from this
module, which is the direction that was always implied — the derivations were the only readers.

THE TWO STALENESS CONSTANTS CAME WITH THEM, and they had to. Leaving them behind would have made
this module import from `liveness.py` while `liveness.py` imports from here — a cycle, which this
repo gates against. They travelled because their only readers travelled: `CHANNEL_SIDECAR_STALE_SECONDS`
is read by the sidecar probe and `ACTIVE_RUN_BRIDGE_STALE_SECONDS` by the wrapper-child probe.
`CONSOLE_WORKING_LEASE_SECONDS` stayed for the same reason in reverse — its reader, the console-lease
check, is not a process probe.

EACH PROBE ANSWERS FOR ONE MECHANISM, and that is deliberate rather than repetitive. A managed agent
can be alive through a wrapper child, a channel sidecar, or a terminal session, and those are
genuinely different pieces of evidence with different staleness rules — collapsing them into one
"is it alive" query is how an agent that is alive by one mechanism reads as dead because another was
checked.

Bodies byte-identical to what stood in `liveness.py`.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL
from service.api_core.serialization import _json_loads_or
from service.api_core.tuning import LIVE_SESSION_STATUSES
from service.clock import iso_to_epoch as _iso_to_epoch


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
            f"""
            SELECT COUNT(*) AS cnt FROM terminal_sessions
            WHERE agent_id = ?
              AND status IN {TERMINAL_LIVE_FILTER_SQL}
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
