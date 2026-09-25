"""DESTRUCTIVE maintenance routes: bulk clear, and scheduled rotation.

v0.5.2k. Two handlers, and they are grouped together because of what they DO rather than where they
sat: both delete data in bulk. `/clear` purges messages, files and agents; `/rotate` expires and
trims, on request and hourly from the sweep. Naming that plainly is the point of giving them their own module — they were
previously two unremarkable handlers among a hundred, and the next person reading a route list
should not have to infer which ones destroy data.

`rotate` calls the settings HANDLER (`get_settings`) rather than the loader, which is a smell — but
rewriting it would change the call subject and its request/DB lifecycle, so it follows the handler to
its owner instead. Structural moves do not get to fix smells in passing.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import Request

from service.api_core.routing import domain_router
from service.api_core.ws import _get_ws
from service.clock import ISO_SECONDS
from service.db import get_db
# Imported for the ANNOTATION. Under postponed evaluation a missing model does not fail import --
# FastAPI demotes the body to a query param and the endpoint 422s. On a DESTRUCTIVE route that
# would mean the scope of what gets deleted is read from the wrong place.
from service.models import ClearRequest
from service.routers.settings import get_settings
from service.api_core.agent_removal import _remove_agent_record
from service.reconcilers.message_rotation import RotationPolicy, rotate_messages

logger = logging.getLogger("aify_comms.routers.maintenance")

router = domain_router()


from service.api_core.message_store import _delete_messages_where  # noqa: E402




@router.post("/clear")
async def clear_data(req: ClearRequest, request: Request):
    """Bulk delete. With `olderThanHours`, EVERY target keeps what is newer (v0.7, A1): until 0.7.0 the
    cutoff reached inbox messages only, so `clear(all, olderThanHours=1)` wiped new files and agents.
    The tables only `all` touches (sessions, spawn records, environments, read receipts) have no single
    age to honour, so a cutoff leaves them alone rather than guessing."""
    db = await get_db()
    try:
        cutoff_ms = cutoff_iso = None
        if req.olderThanHours:
            cutoff_epoch = time.time() - req.olderThanHours * 3600
            cutoff_ms = int(cutoff_epoch * 1000)
            cutoff_iso = time.strftime(ISO_SECONDS, time.gmtime(cutoff_epoch))

        deleted_messages = 0
        deleted_files = 0
        deleted_agents = 0
        files_to_unlink: list[Path] = []

        if req.target in ("inbox", "all"):
            where, params = ("to_agent IS NOT NULL", ())
            if req.agentId:
                where, params = ("to_agent = ?", (req.agentId,))
            if cutoff_ms:
                where, params = (f"{where} AND timestamp < ?", (*params, cutoff_ms))
            deleted_messages += await _delete_messages_where(db, where, params)

        if req.target in ("shared", "all"):
            where, params = ("1 = 1", ()) if not cutoff_iso else ("shared_at < ?", (cutoff_iso,))
            rows = await (await db.execute(f"SELECT file_path, is_binary FROM shared_artifacts WHERE {where}", params)).fetchall()
            files_to_unlink = [Path(row["file_path"]) for row in rows if row["is_binary"] and row["file_path"]]
            deleted_files = len(rows)
            await db.execute(f"DELETE FROM shared_artifacts WHERE {where}", params)

        if req.target in ("agents", "all"):
            where, params = ("1 = 1", ())
            if req.agentId and req.target == "agents":
                where, params = ("id = ?", (req.agentId,))
            if cutoff_iso:
                where, params = (f"{where} AND COALESCE(last_seen, '') < ?", (*params, cutoff_iso))
            agent_rows = await (await db.execute(f"SELECT id FROM agents WHERE {where}", params)).fetchall()
            for row in agent_rows:
                deleted_agents += await _remove_agent_record(
                    db,
                    row["id"],
                    removed_by="clear",
                    reason=f'clear(target="{req.target}")',
                )

        if req.target in ("channels", "all"):
            if cutoff_ms:
                # Old channel messages go; the channels and their members stay.
                deleted_messages += await _delete_messages_where(db, "channel IS NOT NULL AND timestamp < ?", (cutoff_ms,))
            else:
                await db.execute("DELETE FROM channel_members")
                deleted_messages += await _delete_messages_where(db, "channel IS NOT NULL")
                await db.execute("DELETE FROM channels")

        if req.target == "all" and not cutoff_ms:
            await db.execute("DELETE FROM read_receipts")
            await db.execute("DELETE FROM agent_sessions")
            await db.execute("DELETE FROM spawn_requests")
            await db.execute("DELETE FROM spawn_specs")
            await db.execute("DELETE FROM environments")

        await db.commit()
        # Off the disk only once the rows are gone: unlinking first left rows naming missing files
        # whenever the commit then failed.
        for path in files_to_unlink:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("clear: could not remove %s", path)
        ws = await _get_ws(request)
        if ws: await ws.broadcast("data_cleared", {"target": req.target})
        return {
            "ok": True,
            "deletedMessages": deleted_messages,
            "cleared": {
                "messages": deleted_messages,
                "files": deleted_files,
                "agents": deleted_agents,
            },
        }
    finally:
        await db.close()


@router.post("/rotate")
async def rotate(request: Request):
    """Run rotation now. The sweep also runs it hourly; see service/reconcilers/message_rotation.py."""
    policy = RotationPolicy.from_settings(await get_settings(request))
    if not policy.active:
        return {"ok": False, "reason": "Rotation is off: both message limits are 0"}
    db = await get_db()
    try:
        stats = await rotate_messages(db, policy, now_ms=int(time.time() * 1000))
        await db.commit()
        return {"ok": True, "stats": stats}
    finally:
        await db.close()
