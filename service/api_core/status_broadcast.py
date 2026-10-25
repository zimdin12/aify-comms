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
anything derived, which is what the `_MANUAL_STATUSES` check guards in both.

BOTH ARE BEST-EFFORT AND SWALLOW EVERYTHING. A failed push must never fail the request that
triggered it -- the state change already happened, and the next poll will show it.
"""
from __future__ import annotations

from service.api_core import switched_view

from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.records import _row_status_note
from service.api_core.settings import _load_settings
from service.api_core.status_inputs import _compute_live_status_cache, engine_status
from service.status_engine import derive


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
        # PUSH/POLL PARITY after the 0.9.8 switch: the push serves the operational view's word, exactly as the poll
        # does (status_refresh), note and last-known included, over the same legacy word the poll derives first (its
        # note blanked where the derivation disagrees). An unreadable view pushes `unknown`.
        legacy = cache.get("status") or ""
        if legacy not in _MANUAL_STATUSES:
            try:
                derived = derive(cache["status_inputs"])
                if derived != legacy:
                    cache["reason"] = ""
                cache["status"] = derived
            except Exception:
                pass
        try:
            cache = await switched_view.word_into(db, row, cache)
        except Exception:
            cache.update(status="unknown", reason="comms could not read aify-env's record of this agent", last_known=None)
        status, note = cache.get("status") or "", cache.get("reason") or ""
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status,
            "statusNote": note,
            "lastKnown": cache.get("last_known"),
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
        if manual not in _MANUAL_STATUSES:
            await engine_status(db, row, settings=settings)  # the legacy derivation, for the rollback until S3
        # THE PUSHED WORD AFTER THE 0.9.8 SWITCH is the operational view's, the same one the poll serves: aify-env's
        # record, unknown first, the D8 rows (an undefined agent's operator stop among them) and row 5. An unreadable
        # view pushes `unknown`, never the legacy word.
        try:
            view = await switched_view.view_for(db, row)
            status, note, last_known = view.status, switched_view.note_of(view), switched_view.last_known_api(view)
            # D8 row 1: the operator's stop, with the operator's own note, as the poll serves it. Only a real operator
            # stop is that authority: a registration's raw word that happens to equal the view's word is not.
            if status == manual and manual in _MANUAL_STATUSES:
                note = _row_status_note(row)
            elif status == "misconfigured":  # comms' own reason beside aify-env's word, as the poll serves it
                note = await switched_view.misconfigured_note(db, row)
        except Exception:
            status, note, last_known = "unknown", "comms could not read aify-env's record of this agent", None
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status or "",
            "statusNote": note or "",
            "lastKnown": last_known,
        })
    except Exception:
        pass
