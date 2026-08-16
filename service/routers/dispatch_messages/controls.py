"""Dispatch CONTROLS: request an interrupt or steer, claim pending ones, report the outcome.

Extracted from `service/routers/dispatch_messages/dispatch.py` in v0.5.4. A whole subject with a
CLEAN closure — measured, not assumed: the three handlers reach only `api_core` and `service` leaves
and touch nothing local to `dispatch.py` and nothing in `shared.py`. That is why they can be their
own route surface instead of an extraction that leaves shims behind.

A CONTROL IS NOT A RUN. It is a request made ABOUT a run while it is active — the run keeps going,
and the control is a separate row with its own lifecycle (pending, then completed or failed). They
shared a file because they share a URL prefix, which is the weakest reason two subjects ever live
together.

`update_dispatch_control` DOES ONE THING THAT IS NOT ABOUT CONTROLS AT ALL, and it is deliberate:
completing a control marks the message that requested it as READ, because the requester has now had
its answer and re-delivering it would be a duplicate. That write reaches `read_receipts` and is the
reason this module knows about messages.

Bodies and route decorators are byte-identical to what stood in `dispatch.py`. The router is built
through `domain_router()` like every other domain — passing `route_class` is rejected there, so a
new surface cannot quietly opt out of the SQLite write-lock retry.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from service import longpoll
from service.api_core.dispatch_controls_io import _claim_dispatch_controls_once
from service.api_core.dispatch_run_state import _append_dispatch_control
from service.api_core.events import _append_dispatch_event
from service.api_core.routing import domain_router
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

# Imported for ANNOTATIONS as well as calls: under postponed evaluation a missing model does not fail
# import, it silently demotes the request body to a query parameter and the endpoint 422s.
from service.models import (
    DispatchControlClaimRequest,
    DispatchControlRequest,
    DispatchControlUpdate,
)

router = domain_router()



@router.post("/dispatch/controls/claim")
async def claim_dispatch_controls(req: DispatchControlClaimRequest, request: Request):
    # Long-poll wrapper — see claim_dispatch / service/longpoll.py. Wait only while the
    # controls list is exactly empty; any pending control returns immediately.
    return await longpoll.longpoll(
        getattr(req, "waitMs", 0),
        lambda: _claim_dispatch_controls_once(req, request),
        lambda r: r.get("controls") == [],
        scope="control",
        fallback_s=3.0,
        is_disconnected=request.is_disconnected,
        lock_result={"ok": True, "controls": []},
    )



@router.post("/dispatch/runs/{run_id}/control")
async def request_dispatch_control(run_id: str, req: DispatchControlRequest, request: Request):
    action = (req.action or "").strip().lower()
    if action not in {"interrupt", "steer"}:
        raise HTTPException(400, "Unsupported control action")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(404, f"Run '{run_id}' not found")
        if run["status"] not in {"claimed", "running"}:
            raise HTTPException(409, f"Run '{run_id}' is not active")

        control_id = await _append_dispatch_control(
            db,
            run_id,
            from_agent=req.from_agent or "",
            action=action,
            body=req.body or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_requested", {"runId": run_id, "controlId": control_id, "action": action})
        return {"ok": True, "controlId": control_id, "runId": run_id, "action": action, "status": "pending"}
    finally:
        await db.close()



@router.patch("/dispatch/controls/{control_id}")
async def update_dispatch_control(control_id: str, req: DispatchControlUpdate, request: Request):
    # NORMALISED, like `action` twelve lines up and like the sibling that does this exact job for
    # environment controls (`update_environment_control` does `str(req.status or "").strip().lower()`
    # before the same {completed, failed} allowlist). This one compared `req.status` RAW, so two
    # endpoints with the same field name, the same values and the same purpose disagreed about
    # whether "Completed" is one of them.
    #
    # Latent rather than live: the bridge writes lowercase literals at both of its call sites
    # (`run-controls.mjs`, `claude-channel.js`). It matters because the writer is the BRIDGE, which
    # is routinely a different build from the service — and a refused control update leaves the
    # control `pending` forever, which strands the run it was meant to close.
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, "Unsupported control status")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        control = await cursor.fetchone()
        if not control:
            raise HTTPException(404, f"Control '{control_id}' not found")

        handled_at = _now()
        await db.execute(
            "UPDATE dispatch_controls SET status = ?, response_text = ?, handled_at = ? WHERE id = ?",
            (status, req.response or "", handled_at, control_id)
        )
        if status == "completed" and (control["source_message_id"] or "").strip():
            run_cursor = await db.execute(
                "SELECT target_agent FROM dispatch_runs WHERE id = ?",
                (control["run_id"],),
            )
            run = await run_cursor.fetchone()
            if run and (run["target_agent"] or "").strip():
                msg_cursor = await db.execute(
                    "SELECT 1 FROM messages WHERE id = ?",
                    ((control["source_message_id"] or "").strip(),),
                )
                if await msg_cursor.fetchone():
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        ((control["source_message_id"] or "").strip(), run["target_agent"], handled_at),
                    )
        await _append_dispatch_event(
            db,
            control["run_id"],
            f"control:{control['action']}:{status}",
            req.response or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_updated", {"controlId": control_id, "status": status})
        return {"ok": True, "controlId": control_id, "status": status}
    finally:
        await db.close()
