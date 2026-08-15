"""The two ends of a turn: an agent says it started working, and that it stopped.

Extracted from `service/routers/agents/liveness.py` in v0.5.4. Closure measured before the move —
`api_core` and `service` leaves only, nothing local, nothing borrowed from `agents/shared.py`. The
lease and status-event handlers could NOT come: they reach `_record_claimer_lease` from `shared.py`,
so moving them would produce a shim rather than a route surface.

THESE ARE THE EVIDENCE THE STATUS ENGINE RUNS ON. This repo's status model is proof-based and has no
time decay: an agent is `working` because something reported a turn starting, and stops being
`working` because something reported it ending — not because a timer expired. That is why both
halves live here and why neither is a heartbeat. A heartbeat says a process is alive; a turn
boundary says what it is DOING, and the two answer different questions about the same agent.

WHICH MAKES A MISSED turn-end THE EXPENSIVE FAILURE. There is no timeout that eventually declares the
turn over, so an agent whose end never arrives reads as busy indefinitely, and the delivery gates
that consult status stop routing work to it. Every bug of that shape in this repo's history — the
resident stuck WORKING while idle, the managed turn-start flap — is a disagreement about one of
these two events, not about the derivation that reads them.

Bodies and route decorators are byte-identical to what stood in `liveness.py`. The router is built
through `domain_router()`, which rejects a hand-passed `route_class`, so a new surface cannot opt out
of the bounded SQLite write-lock retry.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service.api_core.agent_sessions import _agent_tombstone
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime
from service.api_core.settings import _load_settings
from service.api_core.status_broadcast import _broadcast_engine_status
from service.api_core.status_events import _apply_status_event
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state

router = domain_router()



@router.post("/agents/{agent_id}/turn-start")
async def agent_turn_start(agent_id: str, request: Request):
    """Harness-level turn-START signal — symmetric counterpart to /turn-end.

    Called by per-runtime UserPromptSubmit hooks (claude-aify's
    UserPromptSubmit hook installed via install.sh) when the operator
    types a prompt directly into the resident CLI without going through
    aify-comms's dispatch path. Without this, channel-route dispatches
    correctly flip the agent to "working" but direct CLI typing leaves
    the status at "online" while the assistant is actually mid-turn —
    operator-asked 2026-05-22 to make the two surfaces symmetric.

    Idempotent: refreshes turn_updated_at on every call so the 120s
    server-side staleness window keeps resetting while the assistant
    works.
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute(
            "SELECT id, runtime FROM agents WHERE id = ?", (agent_id,)
        )).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        now = _now()
        runtime = _normalize_runtime(agent_row["runtime"] or "claude-code")
        # If a managed dispatch is already in flight (turn_run_id set,
        # fresh, set by a real bridge), DON'T clobber the dispatch
        # context with our user-prompt-submit attribution. Just refresh
        # turn_updated_at so the existing run linkage keeps the
        # dashboard's "working on subject X" display intact.
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 1, '', 'user-prompt-submit', ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 1,
                turn_bridge_id = CASE
                    WHEN turn_busy = 1 AND COALESCE(turn_run_id, '') != ''
                         AND COALESCE(turn_bridge_id, '') NOT IN ('', 'user-prompt-submit')
                    THEN turn_bridge_id
                    ELSE 'user-prompt-submit'
                END,
                turn_runtime = excluded.turn_runtime,
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, runtime, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        # status v2: feed the event-driven engine from the SAME turn signal so the
        # `new` engine reflects working without a separate post. Flag-agnostic — only
        # the `new` read path reads agent_status_state, so this is a no-op for `old`.
        await _apply_status_event(db, agent_id, {"kind": "turn_start", "runId": ""})
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # Push the transition immediately so the dashboard reflects to-working within a
        # second instead of waiting out its ~60s poll.
        settings = await _load_settings(db)
        await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()



@router.post("/agents/{agent_id}/turn-end")
async def agent_turn_end(agent_id: str, request: Request):
    """Harness-level turn-end signal.

    Called by per-runtime Stop hooks (claude-aify's Stop hook, hermes's
    post_tool_call hook variant, etc.) when the agent has finished its
    current turn at the HARNESS level — i.e., the assistant turn is
    actually over, not just "the agent sent a message." Authoritative
    clear of turn_busy regardless of which bridge originally set it,
    because the harness itself is the source of truth about when its
    own turns end. This is the architectural complement to the
    per-runtime native turn-end signals (codex turn/completed, pi
    agent_end, hermes process exit) that already exist for managed
    runs but were missing for resident claude under claude-channel.js.

    Idempotent: calling when turn_busy is already 0 is a no-op (still
    refreshes turn_updated_at for liveness tracking).
    """
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        agent_row = await (await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not agent_row:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        # WS-4a (2026-06-17): a turn-end carrying a bridgeId comes from a bridge-side turn
        # DETECTOR (the harness Stop hook posts no body, so it stays authoritative). If that
        # bridge has been SUPERSEDED by a newer one for this agent, ignore the clear — a stale
        # detector from a replaced bridge must not false-clear the live successor's turn (the
        # F5 working→idle flap on bridge restart mid-turn). The heartbeat turnBusy=false path
        # already has this guard; this brings the dedicated endpoint in line for detector posts.
        try:
            _body = await request.json()
        except Exception:
            _body = {}
        _posting_bridge = str((_body or {}).get("bridgeId") or "").strip()
        if _posting_bridge:
            _sup = await (await db.execute(
                "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
                (_posting_bridge, agent_id),
            )).fetchone()
            if _sup and str((_sup["superseded_by"] if "superseded_by" in _sup.keys() else "") or "").strip():
                return {"ok": True, "agentId": agent_id, "ignored": "superseded_bridge"}
        # No-op fast path (2026-07-19): a KEEP-CLEARED detector re-assert fires every ~45s for the
        # WHOLE idle life of every agent. When there is genuinely nothing to clear — turn_busy already 0
        # AND the engine's in_turn already 0 — the full write+commit+broadcast is pure waste (the
        # periodic-write anti-pattern the _LIVE_STATE_CACHE redesign removed). Skip it. A real stray
        # (either bit set) still takes the full clear below, preserving KEEP-CLEARED's healing purpose.
        # last_seen refresh is safe to skip here: the unconditional liveness beat owns liveness.
        _tb = await (await db.execute(
            "SELECT turn_busy FROM agent_turn_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _st = await (await db.execute(
            "SELECT in_turn FROM agent_status_state WHERE agent_id = ?", (agent_id,)
        )).fetchone()
        _turn_busy = int((_tb["turn_busy"] if _tb and "turn_busy" in _tb.keys() else 0) or 0)
        _in_turn = int((_st["in_turn"] if _st and "in_turn" in _st.keys() else 0) or 0)
        if _turn_busy == 0 and _in_turn == 0:
            return {"ok": True, "agentId": agent_id, "noop": "already-cleared"}
        now = _now()
        await db.execute(
            """
            INSERT INTO agent_turn_state (agent_id, turn_busy, turn_run_id, turn_bridge_id, turn_runtime, turn_updated_at)
            VALUES (?, 0, '', '', '', ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                turn_busy = 0,
                turn_run_id = '',
                turn_bridge_id = '',
                turn_runtime = '',
                turn_updated_at = excluded.turn_updated_at
            """,
            (agent_id, now),
        )
        await db.execute(
            "UPDATE agents SET last_seen = ? WHERE id = ?",
            (now, agent_id),
        )
        # status v2: feed the event-driven engine (clears in_turn). Flag-agnostic —
        # only the `new` read path reads agent_status_state, so it's a no-op for `old`.
        await _apply_status_event(db, agent_id, {"kind": "turn_end", "runId": ""})
        await _invalidate_agent_live_state(db, agent_id)
        await db.commit()
        # Push the to-ready transition immediately — this is the hop the operator most needs
        # ("send queued work after the agent goes ready"); waiting the ~60s poll looked stuck.
        settings = await _load_settings(db)
        await _broadcast_engine_status(await _get_ws(request), db, agent_id, settings=settings)
        return {"ok": True, "agentId": agent_id}
    finally:
        await db.close()
