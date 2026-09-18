"""Service settings routes: read the effective settings, and update them.

v0.5.2c. A route domain extracted from `service/routers/api_v2.py`, built with `domain_router()` so
it cannot be missing the `JsonApiRoute` lock-retry.

NO TAGS ON THIS ROUTER. The parent applies `tags=["api"]` when it includes this one and FastAPI
COMBINES them — declaring the tag here too produced `tags=["api","api"]` on the first domain, which
is visible in the OpenAPI spec and invisible to everything except the route metadata gate.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _invalidate_settings_cache, _load_settings
from service.api_core.settings_spec import GROUPS, SETTINGS, SettingError, validate_update
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

logger = logging.getLogger("aify_comms.routers.settings")

router = domain_router()


async def _apply_managed_runtime_defaults(db, settings: dict[str, Any]) -> None:
    """Rewrite every existing managed agent's model and effort to the current defaults.

    ONLY ON REQUEST since 2026-09-19. It ran on every save that carried any `managed_` key, and the
    dashboard sent every field on every save, so changing the colour scheme reset the model of every
    managed agent -- including ones spawned with a model of their own -- and the next restart
    relaunched them on the default. Saving a default now changes only what NEW workers get.
    """
    defaults = [
        ("claude-code", settings.get("managed_claude_model", DEFAULT_SETTINGS["managed_claude_model"]), settings.get("managed_claude_effort") or DEFAULT_SETTINGS["managed_claude_effort"]),
        ("codex", settings.get("managed_codex_model", DEFAULT_SETTINGS["managed_codex_model"]), settings.get("managed_codex_effort") or DEFAULT_SETTINGS["managed_codex_effort"]),
        ("pi", settings.get("managed_pi_model", DEFAULT_SETTINGS["managed_pi_model"]), settings.get("managed_pi_effort") or DEFAULT_SETTINGS["managed_pi_effort"]),
    ]
    for runtime, model, effort in defaults:
        model = str(model or "").strip()
        effort = str(effort or "").strip()
        await db.execute(
            """
            UPDATE agents
            SET model = ?
            WHERE runtime = ?
              AND (session_mode = 'managed' OR launch_mode = 'managed' OR managed_by != '')
            """,
            (model, runtime),
        )
        cursor = await db.execute(
            """
            SELECT id, runtime_config
            FROM agents
            WHERE runtime = ?
              AND (session_mode = 'managed' OR launch_mode = 'managed' OR managed_by != '')
            """,
            (runtime,),
        )
        for row in await cursor.fetchall():
            runtime_config = _json_loads_or(row["runtime_config"], {})
            runtime_config["effort"] = effort
            await db.execute(
                "UPDATE agents SET runtime_config = ? WHERE id = ?",
                (json.dumps(runtime_config), row["id"]),
            )
        await db.execute("UPDATE spawn_specs SET model = ? WHERE runtime = ?", (model, runtime))
        spec_cursor = await db.execute("SELECT id, metadata FROM spawn_specs WHERE runtime = ?", (runtime,))
        for row in await spec_cursor.fetchall():
            metadata = _json_loads_or(row["metadata"], {})
            runtime_config = metadata.get("runtimeConfig") if isinstance(metadata.get("runtimeConfig"), dict) else {}
            runtime_config = {**runtime_config, "effort": effort}
            metadata = {**metadata, "runtimeConfig": runtime_config}
            await db.execute(
                "UPDATE spawn_specs SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata), _now(), row["id"]),
            )


# Every value is checked against its declaration in settings_spec.py, and a PUT carrying one the
# setting cannot hold is refused with the reason (400). Until 2026-09-19 an invalid value was dropped
# behind a 200, which the dashboard reported as "Saved".
@router.get("/settings")
async def get_settings(request: Request):
    db = await get_db()
    try:
        return await _load_settings(db)
    finally:
        await db.close()


@router.get("/settings/schema")
async def get_settings_schema(request: Request):
    """The declarations the dashboard draws its settings panel from, in panel order."""
    return {"groups": list(GROUPS), "settings": [s.describe() for s in SETTINGS if s.shown]}


@router.put("/settings")
async def update_settings(request: Request):
    body = await request.json()
    try:
        clean = validate_update(body if isinstance(body, dict) else {})
    except SettingError as exc:
        raise HTTPException(400, str(exc)) from exc
    db = await get_db()
    try:
        for key, value in clean.items():
            await db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)", (key, json.dumps(value)))
        _invalidate_settings_cache()
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("settings_updated")
        return await _load_settings(db)
    finally:
        await db.close()


@router.post("/settings/apply-managed-defaults")
async def apply_managed_defaults(request: Request):
    """Give every existing managed agent the current model and effort defaults. The operator asks."""
    db = await get_db()
    try:
        await _apply_managed_runtime_defaults(db, await _load_settings(db))
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("settings_updated")
        return {"ok": True}
    finally:
        await db.close()
