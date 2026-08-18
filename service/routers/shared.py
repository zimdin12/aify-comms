"""The `shared` route domain: artifact upload, listing, read and delete.

v0.5.2d. Extracted on its own rather than batched with the other small read-mostly domains, on the
reviewer's ruling: this one writes FILES to disk under operator-supplied names, so its blast radius
is not the same as stats or contracts even though its route count is similar.

Names arriving here pass `validate_name` before they ever reach the filesystem, which is why that
gate got its own leaf and its own hostile-name suite in v0.5.1f — path traversal, shell
metacharacters, control characters and non-ASCII homoglyphs are all rejected there. This module
should keep calling it and must never grow a second, laxer path.

Built with `domain_router()`, and declares NO tags: the parent applies `tags=["api"]` on include and
FastAPI combines them.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from service.api_core.routing import domain_router
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

logger = logging.getLogger("aify_comms.routers.shared")

router = domain_router()


def _shared_dir(request: Request) -> Path:
    try:
        d = Path(request.app.state.config.data_dir) / "shared_files"
    except Exception:
        d = Path("/data/shared_files")
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.get("/shared")
async def list_shared(request: Request):
    db = await get_db()
    try:
        # Project ONLY metadata (bughunt 2026-07-03): `SELECT *` pulled every row's inline
        # `content` blob (text shares are stored inline, capped only at max_shared_size_mb =
        # 500MB default) though the response uses none of it — a Files-tab / comms_files poll
        # transiently allocated the sum of all blobs → OOM on the single-worker hub.
        cursor = await db.execute(
            "SELECT name, from_agent, description, size, shared_at "
            "FROM shared_artifacts ORDER BY shared_at DESC LIMIT 2000"
        )
        files = []
        for row in await cursor.fetchall():
            files.append({
                "name": row["name"], "from": row["from_agent"],
                "description": row["description"], "size": row["size"],
                "sharedAt": row["shared_at"],
            })
        return {"files": files}
    finally:
        await db.close()


@router.post("/shared")
async def share_artifact(
    request: Request,
    from_agent: str = Form(...), name: str = Form(...),
    description: str = Form(""), content: str = Form(None),
    file: UploadFile = File(None),
):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        now = _now()
        size = 0
        is_binary = False
        # Enforce the configured upload cap (Settings → max_shared_size_mb) on both paths.
        settings = await _load_settings(db)
        max_mb = settings.get("max_shared_size_mb", DEFAULT_SETTINGS.get("max_shared_size_mb", 500))
        max_bytes = int(max_mb) * 1024 * 1024 if max_mb else 0
        if file:
            shared_dir = _shared_dir(request)
            file_path = shared_dir / name
            # Stream-read with an early cap (bughunt 2026-07-03): `await file.read()`
            # materialized the ENTIRE multipart body in RAM before the 413 check, so a
            # 5GB POST against a 500MB cap OOM-killed the single-worker hub for every
            # agent. Read in chunks and abort the moment we exceed the limit.
            chunks = []
            size = 0
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if max_bytes and size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"File exceeds the {max_mb} MB shared-file limit.")
                chunks.append(chunk)
            data = b"".join(chunks)
            is_binary = True
            file_path.write_bytes(data)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, file_path, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, str(file_path), size, 1, now)
            )
        else:
            text = content or ""
            size = len(text.encode("utf-8"))
            if max_bytes and size > max_bytes:
                raise HTTPException(status_code=413, detail=f"Content exceeds the {max_mb} MB shared-file limit.")
            # If a BINARY artifact with this name already exists, its on-disk file would be
            # orphaned by this text overwrite (bughunt 2026-07-03): the row flips to
            # is_binary=0, file_path=NULL, and both reclaim paths gate on is_binary=1, so
            # the file leaks forever. Unlink the prior file first.
            prior = await (await db.execute(
                "SELECT file_path FROM shared_artifacts WHERE name = ? AND is_binary = 1", (name,)
            )).fetchone()
            if prior and prior["file_path"]:
                try: Path(prior["file_path"]).unlink(missing_ok=True)
                except Exception: pass
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, text, size, 0, now)
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("file_shared", {"name": name, "from": from_agent})
        return {"ok": True, "name": name, "size": size, "isBinary": is_binary}
    finally:
        await db.close()


@router.get("/shared/{name}")
async def read_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts WHERE name = ?", (name,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Artifact '{name}' not found")
        meta = {"from": row["from_agent"], "description": row["description"], "size": row["size"], "sharedAt": row["shared_at"]}
        if row["is_binary"] and row["file_path"]:
            from fastapi.responses import FileResponse
            return FileResponse(row["file_path"], filename=name)
        return {"content": row["content"], "meta": meta}
    finally:
        await db.close()


#: Identities allowed to delete an artifact they did not share. Operator surfaces only.
_SHARED_OPERATOR_ACTORS = frozenset({"dashboard", "operator"})


@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request, requestedBy: str = ""):
    """Delete a shared artifact, on behalf of the agent that SHARED it or the operator.

    Same defect and same fix as H4's unsend (2026-08-18): this took a name and deleted, with no
    acting agent and no ownership check, so any agent could remove another's artifact. Found while
    sweeping the tool surface — there was no MCP tool for it at all, so the hole had never been
    reachable from an agent; adding the tool without the check would have opened it.

    Actor is MANDATORY and absence fails closed, for the reason the H4 ruling gives: an optional
    actor is theatre, since an attacker simply omits it. Self-asserted, like every actor in this API.
    """
    validate_name(name, "artifact name")
    actor = str(requestedBy or "").strip()
    if not actor:
        raise HTTPException(
            400,
            "deleting a shared artifact requires `requestedBy` (the agent that shared it, or an "
            "operator surface). Refused rather than defaulted.",
        )
    db = await get_db()
    try:
        owner_cursor = await db.execute("SELECT from_agent FROM shared_artifacts WHERE name = ?", (name,))
        owner_row = await owner_cursor.fetchone()
        if owner_row is None:
            # IDEMPOTENT, as it has always been: deleting something already gone is a success, not an
            # error. A test pins that, and it is the right contract — a caller retrying a delete
            # should not have to distinguish "I removed it" from "it was already removed". The
            # ownership check below applies to rows that EXIST; there is no owner to check here.
            return {"ok": True}
        owner = str(owner_row["from_agent"] or "").strip()
        if actor not in _SHARED_OPERATOR_ACTORS and actor != owner:
            raise HTTPException(
                403,
                f"'{actor}' cannot delete an artifact shared by '{owner or '(unknown)'}'. "
                f"Only the sharer or an operator surface may remove it.",
            )
        # Delete file if binary
        cursor = await db.execute("SELECT file_path FROM shared_artifacts WHERE name = ? AND is_binary = 1", (name,))
        row = await cursor.fetchone()
        if row and row["file_path"]:
            p = Path(row["file_path"])
            if p.exists(): p.unlink()
        await db.execute("DELETE FROM shared_artifacts WHERE name = ?", (name,))
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()
