"""Whether a registration may proceed: is the environment reachable, is a worker live, is another
bridge already holding this mode, and is the cwd one this machine can actually use.

Several gates, plus a conflict predicate, a cwd validator and a two-line helper. They are one module
because they are asked in sequence about the same request and each can refuse it — a reader working out
why a registration was rejected needs those answers in one place, not one module per question.

This docstring said "four gates ... 192 lines" until v0.5.4 and both numbers were wrong by then. It then
said FIVE and was wrong again within the same afternoon, because another gate came out of the route. So
neither a count nor a line total is stated here now: nothing in the suite reads prose, and a number in a
docstring only records the day someone wrote it. The gates are listed by NAME, which rots visibly.

    _enforce_env_reachable_gate           is the owning environment actually online
    _enforce_live_worker_gate             is there really a live worker behind a cached `online`
    _enforce_same_mode_bridge_gate        is another bridge already holding this mode (v0.5.4)
    _enforce_driving_mode_switch_gate     would this switch a mode out from under a live turn (v0.5.4)
    _enforce_tombstone_registration_gate  is this a tombstoned id not asking to be restored (v0.5.4)
    _enforce_tombstone_resurrection_gate  would this resurrect a deliberately-removed agent (v0.5.4)

The last four arrived by extract-method out of the 684-line `register_agent`, which is why they read as
route body rather than as helpers designed here: their bodies are verbatim, and the inline-back proof in
`service/tests/test_register_agent_split_is_inert.py` requires them to stay that way. The two tombstone
gates are deliberately separate and run in order — the first refuses when nothing asked for a restore,
the second decides whether a restore that DID ask is fresh enough to allow.

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
from service.api_core.live_process_probes import _has_live_terminal_session
from service.api_core.managed_env import _managed_owning_environment_row
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _timestamp_sort_key
from service.api_core.resume_command import _resume_command_for
from service.clock import now as _now
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import _live_state_get


#: Both separators. It matched only `C:/` until 2026-08-16, so the gate below refused `C:/repo` on a
#: linux host and ADMITTED `C:\repo` — the canonical Windows drive-letter path, and exactly what its
#: own refusal message names. A backslash cwd then reached the codex app-server as a directory that
#: does not exist on that host, which is the failure this gate exists to prevent.
_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:[\\/]")
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




async def _enforce_driving_mode_switch_gate(req, row, normalized_runtime, normalized_session_mode) -> None:
    """Refuse a re-register that would change an agent's session mode out from under a live turn.

    v0.5.4, extracted verbatim out of `register_agent`. A gate: it raises or returns None, which is why it
    belongs beside the other four rather than in the route.

    THE CARVE-OUT IS THE INTERESTING PART. `driver_state == "driving"` means a bridge is actively driving a
    turn, and switching mode under it strands that turn — so the change is refused with a 409 that tells
    the operator how to resume. managed -> resident is EXEMPT because that is the graceful takeover the
    product offers deliberately; refusing it would break a documented flow. So the guard fires on a mode
    change that is not the graceful one.

    Returns None when the registration may proceed; raises HTTPException(409) otherwise.
    """
    if row and not bool(req.restoreDeleted):
        existing_mode = _normalize_session_mode(row["session_mode"] or "resident")
        driver_state = str((row["driver_state"] if "driver_state" in row.keys() else "") or "idle").strip().lower()
        graceful_resident_candidate = (
            normalized_session_mode == "resident" and existing_mode == "managed"
        )
        if (
            driver_state == "driving"
            and existing_mode != normalized_session_mode
            and not graceful_resident_candidate
        ):
            resume_command = _resume_command_for(
                row["runtime"] or normalized_runtime,
                row["session_handle"] or "",
                req.agentId,
            )
            detail = (
                f"agent '{req.agentId}' is currently {existing_mode} — "
                f"switch it to {normalized_session_mode} in the dashboard first, then run: "
                f"{resume_command}"
                if resume_command
                else (
                    f"agent '{req.agentId}' is currently {existing_mode} — "
                    f"switch it to {normalized_session_mode} in the dashboard first."
                )
            )
            raise HTTPException(409, detail)


async def _enforce_tombstone_resurrection_gate(db, req, tombstone) -> None:
    """Refuse a passive re-register that would resurrect a deliberately-removed agent.

    v0.5.4, extracted verbatim out of `register_agent`. The comments inside are the incident record and are
    preserved exactly. The short version: the bridge sets `restoreDeleted=true` on EVERY auto-register, so
    with no freshness check a lingering bridge that predates a deletion would clear the tombstone and bring
    the agent back into /agents and the dashboard DM rail.

    The test is relaunch freshness — only a bridge whose `bridgeStartedAt` is NEWER than the tombstone's
    `removed_at` may restore. An explicit operator restore (`restoreDeleted=true` with
    `autoRegister=false`) is still honoured: that is a person asking, not a stale beat.

    WRITES: deletes the tombstone row on the restore path, left UNCOMMITTED for the caller's transaction.
    Raises HTTPException(410) to keep the agent deleted.
    """
    if tombstone and req.restoreDeleted:
        # Tombstone-resurrection guard (2026-06-03). The bridge sets
        # restoreDeleted=true UNCONDITIONALLY on every auto/comms_register, so
        # a still-running bridge that predates the deletion would otherwise
        # clear the tombstone and resurrect a deliberately-removed agent
        # (it reappears in /api/v1/agents and the dashboard DM rail). Mirror
        # the environment forget-tombstone freshness check: only a GENUINE
        # fresh relaunch — a bridge whose bridgeStartedAt is NEWER than the
        # tombstone's removed_at — may restore. A passive auto re-register
        # from a bridge that launched BEFORE the deletion (or with no/older
        # bridgeStartedAt) keeps the agent deleted (410, tombstone untouched).
        #
        # An explicit, operator-initiated restore (restoreDeleted=true with
        # autoRegister=false — not a passive bridge beat) is preserved: a
        # deliberate operator bring-back still clears the tombstone.
        removed_at = _timestamp_sort_key(tombstone["removed_at"] if "removed_at" in tombstone.keys() else "")
        incoming_started = _timestamp_sort_key(req.bridgeStartedAt)
        relaunched = bool(incoming_started) and (not removed_at or incoming_started > removed_at)
        if req.autoRegister and not relaunched:
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed at "
                    f"{tombstone['removed_at']}; a lingering bridge cannot "
                    "resurrect it. Relaunch the agent to restore."
                ),
            )
        # FIX 4 (2026-06-03): COLLATE NOCASE so the explicit-restore clear path
        # matches the same row the case-insensitive lookup above found.
        await db.execute(
            "DELETE FROM agent_tombstones WHERE agent_id = ? COLLATE NOCASE",
            (req.agentId,),
        )


async def _enforce_tombstone_registration_gate(req, tombstone) -> None:
    """Refuse any registration of a tombstoned agent id that is not asking to restore it.

    v0.5.4, extracted verbatim out of `register_agent`. The FIRST of the two tombstone gates and the
    blunter one: no `restoreDeleted` flag at all means the caller is not asking to bring the agent back,
    so the id stays refused whether the beat is automatic or manual. `_enforce_tombstone_resurrection_gate`
    below handles the harder case — `restoreDeleted=true`, where freshness decides.

    TWO DISTINCT 410 MESSAGES, preserved exactly. An auto-register is told re-registration is blocked; a
    manual one is told which flag to pass. Collapsing them into one message would take the actionable
    instruction away from the human and hand it to a bridge that cannot act on it.

    Returns None only when there is no tombstone; otherwise raises HTTPException(410).
    """
    if tombstone and not req.restoreDeleted:
        if req.autoRegister:
            raise HTTPException(
                410,
                (
                    f"Agent '{req.agentId}' was intentionally removed at "
                    f"{tombstone['removed_at']}; auto re-registration is blocked."
                ),
            )
        raise HTTPException(
            410,
            (
                f"Agent '{req.agentId}' was intentionally removed. "
                "Pass restoreDeleted=true to register this ID again."
            ),
        )


# _enforce_same_mode_bridge_gate and _fresh_same_mode_bridge_conflict moved to
# service/api_core/same_mode_bridge_gate.py in v0.5.4 — they call only each other, and the
# gate is correct only in company with the freshness test that bounds it.
