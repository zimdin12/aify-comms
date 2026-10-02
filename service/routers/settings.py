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

from service.api_core.definition_guard import DEFINED_SQL
from service.api_core.request_body import json_object_body
from service.api_core.operator_authz import recorded_operator_actor
from service.api_core.routing import domain_router
from service.api_core.serialization import _json_loads_or
from service.api_core.harness_defaults import defaulted_runtimes, defaults_for
from service.api_core.model_effort import with_effort
from service.api_core.settings import _invalidate_settings_cache, _load_settings
from service.api_core.settings_spec import GROUPS, SETTINGS, SettingError, validate_update
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db

logger = logging.getLogger("aify_comms.routers.settings")

router = domain_router()


#: A managed agent of one runtime that no host defines: what the defaults may rewrite. A defined agent's
#: model and effort are its definition's (P0 C5), so it is skipped and counted.
_MANAGED_OF_RUNTIME = "runtime = ? AND (session_mode = 'managed' OR launch_mode = 'managed' OR managed_by != '')"
_UNDEFINED_SPEC = "agent_id NOT IN (SELECT agent_id FROM agent_definitions)"


def _defaulted(runtime_config: Any, effort: str) -> dict:
    """A record's runtime config holding the default effort as its only effort, and no model of its own.

    The launch reads `thinking` after `effort` and `runtimeConfig.model` after the model column
    (model_effort.py), so either one left behind beat an empty default: "the runtime's own" launched at
    the old value."""
    config = with_effort(runtime_config, effort)
    config.pop("model", None)
    return config


async def _apply_managed_runtime_defaults(db, settings: dict[str, Any]) -> int:
    """Rewrite every existing undefined managed agent's model and effort to the current defaults, and
    return how many defined managed agents it left as their definitions set them.

    ONLY ON REQUEST since 2026-09-19. It ran on every save that carried any `managed_` key, and the
    dashboard sent every field on every save, so changing the colour scheme reset the model of every
    managed agent -- including ones spawned with a model of their own -- and the next restart
    relaunched them on the default. Saving a default now changes only what NEW workers get.
    """
    skipped = 0
    for runtime in defaulted_runtimes():
        model, effort = defaults_for(settings, runtime)
        skipped += (await (await db.execute(
            f"SELECT COUNT(*) FROM agents WHERE {_MANAGED_OF_RUNTIME} AND {DEFINED_SQL}", (runtime,))).fetchone())[0]
        await db.execute(
            f"UPDATE agents SET model = ? WHERE {_MANAGED_OF_RUNTIME} AND NOT {DEFINED_SQL}", (model, runtime))
        cursor = await db.execute(
            f"SELECT id, runtime_config FROM agents WHERE {_MANAGED_OF_RUNTIME} AND NOT {DEFINED_SQL}", (runtime,))
        for row in await cursor.fetchall():
            runtime_config = _defaulted(_json_loads_or(row["runtime_config"], {}), effort)
            await db.execute(
                "UPDATE agents SET runtime_config = ? WHERE id = ?",
                (json.dumps(runtime_config), row["id"]),
            )
        await db.execute(f"UPDATE spawn_specs SET model = ? WHERE runtime = ? AND {_UNDEFINED_SPEC}", (model, runtime))
        spec_cursor = await db.execute(
            f"SELECT id, metadata FROM spawn_specs WHERE runtime = ? AND {_UNDEFINED_SPEC}", (runtime,))
        for row in await spec_cursor.fetchall():
            metadata = _json_loads_or(row["metadata"], {})
            metadata = metadata if isinstance(metadata, dict) else {}
            metadata = {**metadata, "runtimeConfig": _defaulted(metadata.get("runtimeConfig"), effort)}
            await db.execute(
                "UPDATE spawn_specs SET metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(metadata), _now(), row["id"]),
            )
    return skipped


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
    """THE OPERATOR'S: with an OPERATOR_KEY set, refused (403) without it, before the body is read. A saved
    default is what every new managed worker starts with, and Steven ruled on 2026-10-02 that changing an
    agent's model and data is operator-protected; the per-agent routes were gated and this bulk one was
    not (whole-range review of 0.8). The dashboard is its one caller and sends the key."""
    recorded_operator_actor(None, request, action="changing the service's settings as the operator")
    body = await json_object_body(request)
    try:
        clean = validate_update(body)
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
    """Give every existing undefined managed agent the current model and effort defaults, and say how
    many defined ones it skipped. The operator asks, and with an OPERATOR_KEY set must prove it (403):
    this rewrites the model and effort of every undefined managed agent at once."""
    recorded_operator_actor(None, request, action="applying managed defaults to existing agents as the operator")
    db = await get_db()
    try:
        skipped = await _apply_managed_runtime_defaults(db, await _load_settings(db))
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("settings_updated")
        return {"ok": True, "skippedDefined": skipped}
    finally:
        await db.close()
