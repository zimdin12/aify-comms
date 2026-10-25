"""Managed environments: which one owns an agent, whether it is reachable, whether a spawn is
already in flight. Leaf module.

Layer-0 slice of the v0.5.4 decomposition. Six reads that answer "can this agent be started here, and
is something already starting it".

WHY THE TWO SPAWN WINDOWS BELONG HERE. `SPAWN_INFLIGHT_WINDOW_SECONDS` suppresses a duplicate start;
`SPAWN_STARTING_WINDOW_SECONDS` decides how long the dashboard keeps SHOWING `starting`. They are
deliberately the same number, and `test_status_starting` asserts that equality — because if the
display expires before the suppressor, there is a window where the dashboard says nothing is starting
while the dispatcher still refuses to start one. Two numbers that happen to agree today will drift
apart if they live apart, so the constants and both readers are in one file.

DB ACCESS: `db` passed to every function, reads only, no connection opened and no transaction taken.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from service.api_core.spawn_request_state import lifecycle_start_in_flight
from service.api_core.dead_terminal_spawn_query import spawn_worker_ended_sql
from service.api_core.records import _environment_record_to_dict
from service.api_core.runtime import (
    _normalize_runtime,
    _normalize_session_mode,
    _runtime_capability_for_environment,
    _runtime_unlaunchable_reason,
)
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.clock import ISO_SECONDS
from service.env_status import environment_effective_status as _environment_effective_status


SPAWN_INFLIGHT_WINDOW_SECONDS = 300


async def _managed_environment_status(db, row) -> tuple[str, str, str]:
    if not row or _normalize_session_mode(row["session_mode"] or "resident") != "managed":
        return "", "", ""
    runtime_state = _json_loads_or(row["runtime_state"], {})
    environment_id = str(runtime_state.get("environmentId") or "").strip()
    if not environment_id:
        session_cursor = await db.execute(
            """
            SELECT environment_id
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (row["id"],),
        )
        session = await session_cursor.fetchone()
        environment_id = str((session["environment_id"] if session else "") or "").strip()
    if not environment_id:
        return "", "", ""

    settings = await _load_settings(db)
    env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
    env = await env_cursor.fetchone()
    env_status = _environment_effective_status(
        env,
        offline_seconds=settings.get("environment_offline_seconds", 90),
    ) if env else "offline"
    env_bridge = str((env["bridge_id"] if env else "") or "").strip()
    return environment_id, env_status, env_bridge


async def _has_pending_or_booting_spawn_request(db, agent_id: str) -> bool:
    """Like _has_claimable_spawn_request, but ALSO counts a RECENT `running` request
    (worker mid-boot, before it registers a session). Bug D fix (2026-07-02): a second
    cold-start created while one worker was still booting produced a duplicate whose
    kill-prior could murder the booting worker. Time-bound (5 min) so a stuck `running`
    orphan never blocks future autostarts (the orphan reaper frees those anyway)."""
    # Shared with the `starting` display window — see SPAWN_INFLIGHT_WINDOW_SECONDS. These were two
    # independent numbers (300 here, 180 there) and the gap between them was a window where the
    # status said "idle, send something" while this function was still refusing to start a second
    # worker.
    running_cutoff = time.strftime(
        ISO_SECONDS, time.gmtime(time.time() - SPAWN_INFLIGHT_WINDOW_SECONDS)
    )
    # `starting` is the bridge's pre-`running` PATCH — count it with the time-bounded
    # arm so a concurrent coldstart in that sub-second window can't duplicate.
    # `running` rows with finished_at set are KNOWN-DEAD workers (report_terminal_dead
    # stamps them) — a dead worker must not suppress the respawn it just made necessary.
    # A spawn whose own worker already came and went is not booting (2026-09-30), whatever its row
    # says until the finalizer settles it; counting it refused the start that death made necessary.
    ended, ended_params = spawn_worker_ended_sql("s")
    row = await (await db.execute(
        f"""
        SELECT s.id
        FROM spawn_requests s
        WHERE s.agent_id = ?
          AND (
            s.status IN ('queued', 'claimed')
            OR (
              s.status IN ('starting', 'running')
              AND COALESCE(s.finished_at, '') = ''
              AND COALESCE(NULLIF(s.updated_at, ''), s.created_at) >= ?
              AND NOT {ended}
            )
          )
        LIMIT 1
        """,
        (agent_id, running_cutoff, *ended_params),
    )).fetchone()
    return bool(row) or await lifecycle_start_in_flight(db, agent_id)


async def _select_online_environment_for_runtime(
    db, runtime: str, *, offline_seconds: int = 90, machine_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Pick the freshest ONLINE environment that advertises `runtime`, on `machine_id` when one is named.

    Used by Phase 2 auto-bind: when a managed agent has no usable session
    environment, bind it to a live env so it can be cold-started on first
    message. Deterministic order: most-recently-seen environment first, so a
    freshly-heartbeating bridge is preferred. Returns the environment dict, or
    None when no online environment advertises the runtime.
    """
    normalized_runtime = _normalize_runtime(runtime or "")
    if not normalized_runtime:
        return None
    cursor = await db.execute("SELECT * FROM environments ORDER BY last_seen DESC")
    for env_row in await cursor.fetchall():
        environment = _environment_record_to_dict(env_row, offline_seconds=offline_seconds)
        if str(environment.get("status") or "").lower() != "online":
            continue
        if machine_id is not None and environment.get("machineId") != machine_id:
            continue
        if not _runtime_capability_for_environment(environment, normalized_runtime):
            continue
        # An environment that has already said it cannot start this runtime is not a candidate for it.
        # Skipping here lets a LATER environment that can be chosen instead, which is the whole point
        # of scanning more than one; refusing at the spawn gate alone would stop at the first match.
        if _runtime_unlaunchable_reason(environment, normalized_runtime):
            continue
        return environment
    return None


# v0.5.4: moved out of the control plane, which had no claim on it beyond history. Its ONLY dependency
# is `_managed_environment_status` directly above, and both of its readers reached it through borrow
# shims — the all-consumers-through-a-shim shape that says the carrier was a hiding place, not an owner.
async def _managed_environment_unavailable_reason(db, row) -> Optional[str]:
    environment_id, env_status, _env_bridge = await _managed_environment_status(db, row)
    if not environment_id:
        return None
    if env_status not in {"online", "degraded"}:
        return f'managed environment "{environment_id}" is {env_status}'
    return None
