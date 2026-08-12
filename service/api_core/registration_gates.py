"""Whether a registration may proceed: is the environment reachable, is a worker live, is another
bridge already holding this mode, and is the cwd one this machine can actually use.

Four gates and one two-line helper, 192 lines. They are one module because they are asked in sequence
about the same request and each can refuse it — a reader working out why a registration was rejected
needs all four answers in one place, not four modules deep.

CWD VALIDATION IS NOT COSMETIC, and the two regexes are here rather than in the carrier for a measured
reason: both had ZERO code readers there. Every consumer reached them through a borrow accessor, which
is the pattern the reviewer named — all-consumers-through-accessors is hiding-place evidence, not
ownership evidence. A Windows drive path and a WSL drive path are the two shapes that look valid and
then fail much later, when a Codex thread created with a backslash cwd cannot be resumed. The failure
lands nowhere near the cause, which is exactly why it is checked at registration.

`_enforce_live_worker_gate` and `_enforce_env_reachable_gate` raise HTTPException. That is deliberate
and already the pattern for validation leaves here (api_core/routing.py, api_core/validation.py);
converting them to a custom exception re-raised at the route would change the client's response, and
this series does not change behaviour.

DB ACCESS: `db` is passed in. No connection opened, no commit, no rollback.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from fastapi import HTTPException

from service.api_core.capabilities import (
    _has_codex_live_app_server,
    _managed_via_wrapper_for_runtime,
)
from service.api_core.liveness import _has_live_terminal_session
from service.api_core.managed_env import _managed_owning_environment_row
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _normalize_machine_id
from service.clock import iso_to_epoch as _iso_to_epoch
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import _live_state_get


_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:/")
_WSL_DRIVE_CWD_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")


def _machine_family(machine_id: Any) -> str:
    return str(machine_id or "").strip().split(":", 1)[0].lower()


async def _enforce_env_reachable_gate(
    payload: dict[str, Any],
    db,
    settings: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Read-boundary correction #2 (2026-06-12 status audit): a cached LIVE/available
    status must not outlive its owning ENVIRONMENT. `agent_live_state.refresh_after` is
    keyed on heartbeat freshness, and nothing invalidates dependent agents when an env
    bridge dies (env death is computed-on-read from last_seen age — there is no
    transition event) — so a managed agent could keep serving cached `online`/`available`
    for the full refresh window after its machine went dark. (Masked until today: the
    read-path cache upserts were rolled back on close, hiding the staleness behind
    constant recomputes.) Sibling of `_enforce_live_worker_gate`: when the cached status
    claims the env is usable but the env row no longer reads online/degraded, recompute
    fresh — the full derivation applies the offline policy."""
    status = str(payload.get("status") or "").lower()
    if status not in {"online", "ready", "idle", "working", "available"}:
        return payload
    if str(payload.get("sessionMode") or "").lower() != "managed":
        return payload
    env_id = str((payload.get("runtimeState") or {}).get("environmentId") or "").strip()
    if not env_id:
        # The binding may live on the session row instead of runtime_state — the cached
        # live-state entry carries whichever environment the derivation actually used.
        _ls = _live_state_get(agent_id)
        env_id = str((_ls or {}).get("environment_id") or "").strip()
    env_row = None
    if env_id:
        env_row = await (await db.execute(
            "SELECT * FROM environments WHERE id = ?", (env_id,)
        )).fetchone()
    else:
        # No quick binding anywhere — resolve the owning env the same way the offline
        # derivation does (machine_id + runtime), so an agent with no session row and no
        # runtime_state binding still gets gated against its real environment.
        agent_row = await (await db.execute(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        )).fetchone()
        if agent_row is None:
            return payload
        env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="")
        if env_row is None:
            return payload
    offline_seconds = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
    if env_row and _environment_effective_status(env_row, offline_seconds=offline_seconds) in {"online", "degraded"}:
        return payload
    # Env is gone but the cached status predates its death → correct it IN-MEMORY for this
    # response. READ-ONLY (2026-06-18): the previous invalidate + _refresh_agent_live_state +
    # commit ran on the hot read path per agent — a per-poll write storm that starved SQLite's
    # single writer (`database is locked`, fleet-wide). A managed agent whose owning environment
    # is not online/degraded is `offline` (the same conclusion _compute_live_status_cache's
    # managed_env_bridge_offline branch reaches); set it here without persisting. The 60s
    # reconcile sweep persists the correction (env death has no transition event, so the sweep
    # is the durable re-derivation path).
    env_label = env_id or "owning environment"
    payload["status"] = "offline"
    payload["statusRaw"] = "offline"
    payload["statusNote"] = f'Environment "{env_label}" is offline; only its bridge can host this managed worker.'
    return payload


async def _enforce_live_worker_gate(
    payload: dict[str, Any],
    db,
    settings: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Plan 5 Section C (2026-05-25): downgrade cached `online` to `available`
    for managed wrapper-backed agents that have no non-terminated
    `terminal_sessions` row.

    Why this lives at the read boundary (not in the cache):
    `_compute_live_status_cache` already consults `terminal_sessions` when it
    runs, but `agent_live_state.refresh_after` is keyed on heartbeat
    freshness via `_status_refresh_after` — NOT worker presence. When the
    wrapper PTY exits but a parallel heartbeat keeps the agent alive (e.g.
    another bridge polling the same agent), `refresh_after` stays in the
    future and `_refresh_expired_agent_live_states` never re-validates.
    Cached `status='online'` then persists indefinitely.

    Observed 2026-05-25: graph-senior-dev (codex managed) —
    `agent_live_state.status='online'` `terminal_id=''`
    `updated_at=19:29:10Z` `refresh_after=19:30:28Z` (long past), but the
    API still returned `online` because the cache row never fell behind a
    fresh-enough heartbeat to trigger a recompute.

    This gate is a final-step correction at the API boundary. Cache stays
    for performance; the writeback below keeps subsequent reads honest
    without re-running the terminal_sessions check.
    """
    if payload.get("status") not in {"online", "ready"}:
        return payload
    session_mode = str(payload.get("sessionMode") or "").lower()
    if session_mode != "managed":
        return payload
    runtime = str(payload.get("runtime") or "").lower()
    if not _managed_via_wrapper_for_runtime(settings, runtime):
        return payload
    if await _has_live_terminal_session(db, agent_id):
        return payload
    payload["status"] = "available"
    payload["statusRaw"] = "available"
    payload["statusNote"] = "no-live-worker (Plan 5 read-path gate)"
    # READ-ONLY (2026-06-18): the cache writeback was REMOVED. This gate runs on the hot
    # read path (GET /agents | /agents/{id}) per agent; persisting the downgrade here meant
    # up to N writes per roster poll, which starved SQLite's single writer and 503'd the
    # fleet's claim/heartbeat writes (`database is locked`). The downgrade is computed
    # in-memory for THIS response; the 60s reconcile sweep persists the same correction
    # (it re-derives via _compute_live_status_cache, which applies the identical
    # terminal_sessions check). A read re-running the gate is just a couple of cheap reads.
    return payload


async def _fresh_same_mode_bridge_conflict(
    db,
    *,
    agent_id: str,
    machine_id: str,
    new_bridge_id: str,
    session_mode: str,
    lease_seconds: int,
):
    """Return a LIVE same-mode bridge that a new registration would race.

    Phase 4 race guard (2026-05-31, operator-chosen hard-error model). A fresh,
    non-superseded bridge for the SAME (agent, machine) and the SAME resident
    session_mode, owned by a DIFFERENT bridge_id, means a second live wrapper is
    about to claim an identity already being driven — silently superseding it
    would kill the first wrapper's work. We surface that as a 409 (unless the
    caller passes force=true to take over deliberately).

    Scope is RESIDENT-only: managed bridges intentionally use latest-launch-wins
    to reap zombie wrappers, and the visible-TUI managed model runs a legitimate
    sidecar + wrapper-child pair concurrently — neither should trip this guard.
    Returns the conflicting bridge row, or None when there is no live conflict.
    """
    if _normalize_session_mode(session_mode or "") != "resident":
        return None
    normalized_machine = _normalize_machine_id(machine_id)
    cutoff = max(15, int(lease_seconds or 150))
    cursor = await db.execute(
        """
        SELECT id, last_seen, bridge_kind, session_handle
        FROM bridge_instances
        WHERE agent_id = ?
          AND machine_id = ?
          AND id != ?
          AND session_mode = 'resident'
          AND COALESCE(bridge_kind, '') != 'channel-sidecar'
          AND COALESCE(superseded_by, '') = ''
        ORDER BY last_seen DESC
        """,
        (agent_id, normalized_machine, str(new_bridge_id or "").strip()),
    )
    for bridge in await cursor.fetchall():
        seen_s = _iso_to_epoch((bridge["last_seen"] or ""))
        if seen_s and (time.time() - seen_s) <= cutoff:
            return bridge
    return None


def _validate_registration_cwd(
    *,
    agent_id: str,
    runtime: str,
    session_mode: str,
    machine_id: str,
    cwd: str,
    runtime_config: Optional[dict[str, Any]] = None,
) -> None:
    normalized_runtime = _normalize_runtime(runtime)
    normalized_session_mode = _normalize_session_mode(session_mode)
    resolved_cwd = str(cwd or "").strip()
    family = _machine_family(machine_id)
    if not resolved_cwd or normalized_runtime != "codex" or normalized_session_mode != "resident":
        return
    if not _has_codex_live_app_server(runtime_config):
        return
    if family in {"linux", "darwin", "wsl"} and _WINDOWS_DRIVE_CWD_RE.match(resolved_cwd):
        hint = '/mnt/<drive>/...' if family in {"linux", "wsl"} else "/Users/..."
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on {family}. '
                f'Use a native host path such as "{hint}", not a Windows drive-letter path.'
            ),
        )
    if family == "win32" and _WSL_DRIVE_CWD_RE.match(resolved_cwd):
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on Windows. '
                'Use forward-slash drive-letter form like "C:/repo", not a "/mnt/..." WSL path.'
            ),
        )
