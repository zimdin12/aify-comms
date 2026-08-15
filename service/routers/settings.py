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
import math
import time
from typing import Any

from fastapi import HTTPException, Request

from service.api_core.routing import domain_router
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import DEFAULT_SETTINGS, _invalidate_settings_cache, _load_settings
from service.api_core.ws import _get_ws
from service.clock import now as _now
from service.db import get_db
from service.models import validate_model_shape

logger = logging.getLogger("aify_comms.routers.settings")

router = domain_router()


async def _apply_managed_runtime_defaults(db, settings: dict[str, Any]) -> None:
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


# Per-key server-side floors for settings consumed server-side that would break behavior at
# zero/negative (audit 2026-06-28 — PUT /settings previously accepted ANY value for a known
# key; the min/max in the dashboards were advisory only, so a raw API/MCP caller could set e.g.
# max_shared_size_mb=0 and zero out all uploads). Keys not listed are merely floored at 0.
_SETTINGS_MIN = {
    "max_shared_size_mb": 1,
    "dashboard_refresh_seconds": 5,
    "agent_liveness_seconds": 10,
    "environment_offline_seconds": 10,
    "resident_lease_seconds": 10,
    "max_messages_per_agent": 1,
    "retention_days": 1,
    # 0 is meaningful (= every reminder full); listed for documentation only —
    # unlisted numeric keys are floored at 0 anyway.
    "reply_reminder_full_every": 0,
}


@router.get("/settings")
async def get_settings(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM settings")
        saved = {}
        for row in await cursor.fetchall():
            try:
                saved[row["key"]] = json.loads(row["value"])
            except Exception:
                saved[row["key"]] = row["value"]
        return {**DEFAULT_SETTINGS, **saved}
    finally:
        await db.close()


@router.put("/settings")
async def update_settings(request: Request):
    body = await request.json()
    db = await get_db()
    try:
        for key, value in body.items():
            if key not in DEFAULT_SETTINGS:
                continue
            default = DEFAULT_SETTINGS[key]
            # Validate/clamp numeric settings (bool first — bool is a subclass of int).
            if isinstance(default, bool):
                # Bughunt 2026-07-03: a raw API/MCP caller sending the STRING "false"
                # got bool("false")==True (any non-empty string is truthy) — silently
                # flipping a boolean setting on. Only accept an actual JSON bool; also
                # accept the case-insensitive "true"/"false" strings a form might send.
                if isinstance(value, bool):
                    pass
                elif isinstance(value, str) and value.strip().lower() in ("true", "false"):
                    value = value.strip().lower() == "true"
                else:
                    continue  # reject anything ambiguous for a bool setting
            elif isinstance(default, (int, float)) and not isinstance(value, bool):
                try:
                    num = float(value)
                except (TypeError, ValueError):
                    continue  # reject non-numeric for a numeric setting
                # Reject NaN AND ±inf (bughunt 2026-07-03: a JSON `1e999` literal parses
                # to float('inf'); the old `num != num` NaN guard missed it, and int(inf)
                # then raised OverflowError → HTTP 500).
                if not math.isfinite(num):
                    continue
                num = max(num, float(_SETTINGS_MIN.get(key, 0)))
                value = int(num) if isinstance(default, int) else num
            elif key.endswith("_model"):
                # THE THIRD MODEL INGRESS, and the one with the longest fuse. `managed_*_model`
                # settings are substituted into a spawn AFTER Pydantic has validated the request
                # (see create_spawn_request), and `_apply_managed_runtime_defaults` writes them onto
                # agents and spawn_specs — so a malformed value set here reaches a runtime CLI for
                # every future managed spawn of that runtime, with no request to trace it back to.
                #
                # Same shape rule as the request models, so there is one definition of "that is not
                # a model name" rather than three that can drift.
                #
                # Raises rather than the `continue` the numeric branches use: a silently ignored
                # numeric clamps to something sane, but silently ignoring this would leave the
                # operator believing they had changed the fleet's model. Reporting success for work
                # not done is the failure this repo keeps paying for.
                try:
                    value = validate_model_shape(value) or ""
                except ValueError as exc:
                    raise HTTPException(400, f'Setting "{key}": {exc}') from exc
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                (key, json.dumps(value))
            )
        _invalidate_settings_cache()
        settings = await _load_settings(db)
        if any(str(key).startswith("managed_") for key in body.keys()):
            await _apply_managed_runtime_defaults(db, settings)
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("settings_updated")
        return await get_settings(request)
    finally:
        await db.close()
