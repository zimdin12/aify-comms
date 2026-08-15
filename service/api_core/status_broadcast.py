"""Pushing an agent's status to dashboards without waiting for the next poll.

RELOCATED, not rewritten, in v0.5.4 -- all three functions are byte-identical from
`service/routers/agents/shared.py`. That module declares no routes; it is a re-export surface with
a handful of real helpers in it, and these were the largest.

WHY PUSH AT ALL. An operator-driven transition -- a stop, a mode switch, a confirmed session -- is
invisible until something recomputes status, and the reconcile sweep runs every 60 seconds. Without
a push the dashboard shows the old state for up to a minute after the operator clicked, which reads
as the click not having worked.

TWO FUNCTIONS BECAUSE THERE ARE TWO STATUS PATHS. `_broadcast_agent_status` serves the polled
compute; `_broadcast_engine_status` serves the proof-based engine. They are not merged: the point of
each is to push exactly what the corresponding READ would return, so a push that served the other
path's answer would be a push that disagrees with the next poll.

A MANUAL STATUS IS NEVER OVERWRITTEN BY EITHER. An operator who set a status explicitly outranks
anything derived, which is what `_borrowed_manual_statuses` guards in both. It travelled with them
because they were its only two callers.

BOTH ARE BEST-EFFORT AND SWALLOW EVERYTHING. A failed push must never fail the request that
triggered it -- the state change already happened, and the next poll will show it.
"""
from __future__ import annotations

from service.api_core.records import _row_status_note
from service.api_core.settings import _load_settings
from service.api_core.status_inputs import _compute_live_status_cache, engine_status
from service.status_engine import derive


def _borrowed_manual_statuses():
    """One owner, never a copy (finding N7) — and the owner is now a LEAF, not the control plane.

    This borrowed through `service.control_plane` while `_MANUAL_STATUSES` lived there. v0.5.4 moved it to
    `api_core/manual_status.py`, a stdlib-only leaf, so this reads the owner directly and
    the control plane is no longer in the path.
    """
    from service.api_core.manual_status import _MANUAL_STATUSES

    return _MANUAL_STATUSES


async def _broadcast_agent_status(ws, db, agent_id: str) -> None:
    """Recompute one agent's live status and push it to dashboards so an
    operator-driven state transition is reflected without waiting for the 60s
    reconcile sweep or a full client refetch. Best-effort: never raise into the
    caller. Mirrors the single-agent GET status compute (_compute_live_status_cache).
    """
    if ws is None:
        return
    try:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            return
        settings = await _load_settings(db)
        cache = await _compute_live_status_cache(db, row, settings=settings)
        status = cache.get("status") or ""
        # PUSH/POLL PARITY: the WS push serves the SAME proof-engine value the polled read does
        # (derive of the assembled inputs), so a push never overwrites a correct polled status.
        note = cache.get("reason") or ""
        if status not in _borrowed_manual_statuses():
            try:
                _derived = derive(cache["status_inputs"])
                # PUSH/POLL PARITY of the NOTE too (2026-07-10 review): the polled
                # read blanks the legacy-cascade reason when derive() disagrees
                # (the reason describes the superseded status). Mirror it here so the
                # WS-pushed statusNote never contradicts the pushed status.
                if _derived != status:
                    note = ""
                status = _derived
            except Exception:
                pass
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status,
            "statusNote": note,
        })
    except Exception:
        pass


async def _broadcast_engine_status(ws, db, agent_id: str, *, settings=None) -> None:
    """status v2 (Phase D1): push the EVENT-ENGINE status for one agent over WS
    so the dashboard reflects a turn start/end the instant the event lands — not
    on its next poll. Best-effort: never raise into the caller. Only meaningful
    under `status_engine=new`; callers gate on the flag so the legacy `old` path
    stays push-identical to before (it uses `_broadcast_agent_status`).
    """
    if ws is None:
        return
    try:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            return
        settings = settings or await _load_settings(db)
        # Manual statuses (stop/disable) are operator overrides both paths honor
        # identically — surface the persisted status, not an engine derivation.
        manual = str(row["status"] or "").strip().lower()
        if manual in _borrowed_manual_statuses():
            status = manual
            note = _row_status_note(row)
        else:
            status = await engine_status(db, row, settings=settings)
            note = ""
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status or "",
            "statusNote": note or "",
        })
    except Exception:
        pass
