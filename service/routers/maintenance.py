"""DESTRUCTIVE maintenance routes: bulk clear, and scheduled rotation.

v0.5.2k. Two handlers, and they are grouped together because of what they DO rather than where they
sat: both delete data in bulk. `/clear` purges messages, files and agents; `/rotate` expires and
trims on a schedule. Naming that plainly is the point of giving them their own module — they were
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
from service.db import get_db
# Imported for the ANNOTATION. Under postponed evaluation a missing model does not fail import --
# FastAPI demotes the body to a query param and the endpoint 422s. On a DESTRUCTIVE route that
# would mean the scope of what gets deleted is read from the wrong place.
from service.models import ClearRequest
from service.routers.settings import get_settings
from service.api_core.agent_removal import _remove_agent_record

logger = logging.getLogger("aify_comms.routers.maintenance")

router = domain_router()


from service.api_core.message_store import _delete_messages_where  # noqa: E402




@router.post("/clear")
async def clear_data(req: ClearRequest, request: Request):
    db = await get_db()
    try:
        cutoff = None
        if req.olderThanHours:
            cutoff = int((time.time() - req.olderThanHours * 3600) * 1000)

        deleted_messages = 0
        deleted_files = 0
        deleted_agents = 0

        if req.target in ("inbox", "all"):
            if req.agentId:
                if cutoff:
                    deleted_messages += await _delete_messages_where(
                        db,
                        "to_agent = ? AND timestamp < ?",
                        (req.agentId, cutoff),
                    )
                else:
                    deleted_messages += await _delete_messages_where(db, "to_agent = ?", (req.agentId,))
            else:
                if cutoff:
                    deleted_messages += await _delete_messages_where(
                        db,
                        "to_agent IS NOT NULL AND timestamp < ?",
                        (cutoff,),
                    )
                else:
                    deleted_messages += await _delete_messages_where(db, "to_agent IS NOT NULL")

        if req.target in ("shared", "all"):
            # Delete binary files from disk
            cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE is_binary = 1")
            for row in await cursor.fetchall():
                if row["file_path"]:
                    p = Path(row["file_path"])
                    if p.exists(): p.unlink()
            count_cursor = await db.execute("SELECT COUNT(*) FROM shared_artifacts")
            deleted_files = (await count_cursor.fetchone())[0]
            await db.execute("DELETE FROM shared_artifacts")

        if req.target in ("agents", "all"):
            if req.agentId and req.target == "agents":
                agent_rows = await (await db.execute("SELECT id FROM agents WHERE id = ?", (req.agentId,))).fetchall()
            else:
                agent_rows = await (await db.execute("SELECT id FROM agents")).fetchall()
            agent_ids = [row["id"] for row in agent_rows]
            for agent_id in agent_ids:
                deleted_agents += await _remove_agent_record(
                    db,
                    agent_id,
                    removed_by="clear",
                    reason=f'clear(target="{req.target}")',
                )

        if req.target in ("channels", "all"):
            await db.execute("DELETE FROM channel_members")
            deleted_messages += await _delete_messages_where(db, "channel IS NOT NULL")
            await db.execute("DELETE FROM channels")

        if req.target == "all":
            await db.execute("DELETE FROM read_receipts")
            await db.execute("DELETE FROM agent_sessions")
            await db.execute("DELETE FROM spawn_requests")
            await db.execute("DELETE FROM spawn_specs")
            await db.execute("DELETE FROM environments")

        await db.commit()
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
    settings = await get_settings(request)
    if not settings.get("rotation_enabled", True):
        return {"ok": False, "reason": "Rotation disabled"}

    db = await get_db()
    try:
        stats = {"expired_messages": 0, "trimmed_messages": 0, "expired_files": 0, "stale_agents": 0}

        # Expire old messages
        retention_ms = int(settings["retention_days"] * 86400 * 1000)
        cutoff = int(time.time() * 1000) - retention_ms
        stats["expired_messages"] = await _delete_messages_where(db, "timestamp < ?", (cutoff,))

        # Trim per-agent inboxes
        max_msgs = settings["max_messages_per_agent"]
        agents_c = await db.execute("SELECT id FROM agents")
        for agent in await agents_c.fetchall():
            aid = agent["id"]
            c = await db.execute("SELECT COUNT(*) FROM messages WHERE to_agent = ?", (aid,))
            count = (await c.fetchone())[0]
            if count > max_msgs:
                trim = count - max_msgs
                stats["trimmed_messages"] += await _delete_messages_where(
                    db,
                    """
                    id IN (
                        SELECT id FROM messages
                        WHERE to_agent = ?
                        ORDER BY timestamp ASC
                        LIMIT ?
                    )
                    """,
                    (aid, trim),
                )

        # NOTE (2026-06-18): the old "Mark stale agents" UPDATE (stamped agents.status='stale'
        # for agents not seen in stale_agent_hours) was REMOVED. Under the proof-based status
        # model, offline/staleness is DERIVED from liveness at read time (status_engine.derive),
        # never stamped — and 'stale' is no longer a valid status word. Stamping it was a pure
        # write (one per cleanup cycle) of a dead vocabulary value that the live-state cache
        # already overrode. Any legacy 'stale' raw row now canonicalizes to 'offline' via
        # _LEGACY_RAW_STATUS_TO_CANONICAL. The vestigial `stale_agent_hours` setting itself was
        # also removed (2026-07-01) — it had no remaining consumer after this UPDATE was deleted.

        # Clean orphaned read receipts
        await db.execute("DELETE FROM read_receipts WHERE message_id NOT IN (SELECT id FROM messages)")

        await db.commit()
        return {"ok": True, "stats": stats}
    finally:
        await db.close()
