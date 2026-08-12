"""Helpers owned by the agents surfaces, plus every borrow they still need.

v0.5.2m. Defined once so the six surface modules share one shim rather than each
declaring its own. Borrows are established by FOLLOWING SHIMS, not raw caller count:
anything another module already borrows from the router stays borrowed here too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service.api_core.routing import domain_router

from service.api_core.events import _append_dispatch_event
from service.api_core.events import _append_terminal_control
from service.api_core.events import _append_terminal_event
from service.api_core.runtime import _normalize_runtime
from service.api_core.runtime import _normalize_session_mode
from service.api_core.runtime import _runtime_capability_for_environment
from service.api_core.serialization import _json_loads_or
from service.api_core.serialization import _normalize_machine_id
from service.api_core.serialization import _timestamp_sort_key
from service.api_core.settings import DEFAULT_SETTINGS
from service.api_core.settings import _load_settings
from service.api_core.validation import validate_name
from service.api_core.vocabulary import SESSION_MODES as _SESSION_MODES
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import get_db
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.managed_workers import _repair_unusable_active_runs
from service.reconcilers.sessions import LIVE_SESSION_STATUSES
from service.reconcilers.status_cache import _live_state_get
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event
from service.status_engine import derive
from service.terminal_diagnostics import failure_tail as _terminal_failure_tail
from service.terminal_diagnostics import meaningful_failure_line as _terminal_failure_line
from service.terminal_snapshot import render_live_screen as _render_live_terminal_screen
from service.terminal_snapshot import render_snapshot as _render_terminal_snapshot
import re
import sqlite3

logger = logging.getLogger("aify_comms.routers.agents.shared")

async def _active_terminal_for_agent(*a, **k):
    from service.control_plane import _active_terminal_for_agent as _impl

    return await _impl(*a, **k)


async def _adopt_live_resident_driver(*a, **k):
    from service.control_plane import _adopt_live_resident_driver as _impl

    return await _impl(*a, **k)


async def _agent_liveness(*a, **k):
    from service.control_plane import _agent_liveness as _impl

    return await _impl(*a, **k)


def _agent_record_to_dict(*a, **k):
    from service.control_plane import _agent_record_to_dict as _impl

    return _impl(*a, **k)


def _agent_session_to_dict(*a, **k):
    from service.control_plane import _agent_session_to_dict as _impl

    return _impl(*a, **k)


async def _agent_tombstone(*a, **k):
    from service.control_plane import _agent_tombstone as _impl

    return await _impl(*a, **k)


async def _append_dispatch_control(*a, **k):
    from service.control_plane import _append_dispatch_control as _impl

    return await _impl(*a, **k)


async def _auto_return_resident_to_managed_if_possible(*a, **k):
    from service.control_plane import _auto_return_resident_to_managed_if_possible as _impl

    return await _impl(*a, **k)


async def _clear_status_state_in_turn(*a, **k):
    from service.control_plane import _clear_status_state_in_turn as _impl

    return await _impl(*a, **k)


def _coldstart_refusal_message(*a, **k):
    from service.control_plane import _coldstart_refusal_message as _impl

    return _impl(*a, **k)


async def _coldstart_spawn_request_for_dispatch(*a, **k):
    from service.control_plane import _coldstart_spawn_request_for_dispatch as _impl

    return await _impl(*a, **k)


async def _compute_agent_status(*a, **k):
    from service.control_plane import _compute_agent_status as _impl

    return await _impl(*a, **k)


async def _compute_live_status_cache(*a, **k):
    from service.control_plane import _compute_live_status_cache as _impl

    return await _impl(*a, **k)


def _default_capabilities_for(*a, **k):
    from service.control_plane import _default_capabilities_for as _impl

    return _impl(*a, **k)


async def _ensure_managed_pty_for_dispatch(*a, **k):
    from service.control_plane import _ensure_managed_pty_for_dispatch as _impl

    return await _impl(*a, **k)


def _environment_record_to_dict(*a, **k):
    from service.control_plane import _environment_record_to_dict as _impl

    return _impl(*a, **k)


async def _fail_active_runs_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    machine_id: str,
    superseding_bridge_id: str,
    finished_at: str,
    superseded_bridge_ids: Optional[list[str]] = None,
) -> list[str]:
    # Scope-narrowed: only fail runs whose claim_bridge_id is in the explicit
    # superseded-bridge list. Callers without an explicit list fall back to
    # the legacy "any bridge_id different from the new one" behavior.
    if superseded_bridge_ids is not None:
        if not superseded_bridge_ids:
            return []
        placeholders = ",".join("?" for _ in superseded_bridge_ids)
        cursor = await db.execute(
            f"""
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND claim_bridge_id IN ({placeholders})
            """,
            (agent_id, machine_id, *superseded_bridge_ids),
        )
    else:
        cursor = await db.execute(
            """
            SELECT id, claim_bridge_id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND status IN ('claimed', 'running')
              AND claim_machine_id = ?
              AND COALESCE(claim_bridge_id, '') != ?
            """,
            (agent_id, machine_id, superseding_bridge_id),
        )
    rows = await cursor.fetchall()
    if not rows:
        return []

    affected_run_ids: list[str] = []
    for row in rows:
        affected_run_ids.append(row["id"])
        previous_bridge_id = (row["claim_bridge_id"] or "").strip()
        owner_label = previous_bridge_id or "legacy-unowned"
        await db.execute(
            """
            UPDATE dispatch_runs
            SET status = 'failed', error_text = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                f'Run was owned by superseded bridge instance "{owner_label}" and was replaced by "{superseding_bridge_id}" during re-registration',
                finished_at,
                row["id"],
            ),
        )
        await _append_dispatch_event(
            db,
            row["id"],
            "failed",
            f"Register supersession: {owner_label} -> {superseding_bridge_id}",
        )
    return affected_run_ids


async def _get_blocking_active_run(*a, **k):
    from service.control_plane import _get_blocking_active_run as _impl

    return await _impl(*a, **k)


async def _get_dispatch_state_for_agent(*a, **k):
    from service.control_plane import _get_dispatch_state_for_agent as _impl

    return await _impl(*a, **k)


async def _get_dispatch_state_map(*a, **k):
    from service.control_plane import _get_dispatch_state_map as _impl

    return await _impl(*a, **k)


async def _get_unread_count_map(*a, **k):
    from service.control_plane import _get_unread_count_map as _impl

    return await _impl(*a, **k)


def _has_codex_live_app_server(*a, **k):
    from service.control_plane import _has_codex_live_app_server as _impl

    return _impl(*a, **k)


async def _has_live_terminal_session(*a, **k):
    from service.control_plane import _has_live_terminal_session as _impl

    return await _impl(*a, **k)


async def _has_pending_or_booting_spawn_request(*a, **k):
    from service.control_plane import _has_pending_or_booting_spawn_request as _impl

    return await _impl(*a, **k)


def _is_lock_error(*a, **k):
    from service.control_plane import _is_lock_error as _impl

    return _impl(*a, **k)


def _machine_family(machine_id: Any) -> str:
    return str(machine_id or "").strip().split(":", 1)[0].lower()


async def _managed_owning_environment_row(*a, **k):
    from service.control_plane import _managed_owning_environment_row as _impl

    return await _impl(*a, **k)


def _managed_via_wrapper_for_runtime(*a, **k):
    from service.control_plane import _managed_via_wrapper_for_runtime as _impl

    return _impl(*a, **k)


async def _record_channel_sidecar_heartbeat(*a, **k):
    from service.control_plane import _record_channel_sidecar_heartbeat as _impl

    return await _impl(*a, **k)


async def _refresh_expired_agent_live_states(*a, **k):
    from service.control_plane import _refresh_expired_agent_live_states as _impl

    return await _impl(*a, **k)


async def _remove_agent_record(*a, **k):
    from service.control_plane import _remove_agent_record as _impl

    return await _impl(*a, **k)


def _row_status_note(*a, **k):
    from service.control_plane import _row_status_note as _impl

    return _impl(*a, **k)


def _runtime_state_with_handle(*a, **k):
    from service.control_plane import _runtime_state_with_handle as _impl

    return _impl(*a, **k)


async def _session_handle_live_owner(*a, **k):
    from service.control_plane import _session_handle_live_owner as _impl

    return await _impl(*a, **k)


async def _stop_virtual_terminals_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    superseded_bridge_ids: list[str],
    now: str,
) -> None:
    """Mark synthesized virtual rpc terminal_sessions stopped when the
    bridge that owned them is superseded.

    Operator-reported symptom (2026-05-22): after restarting aify-comms,
    multiple managed pi/hermes agents flipped to `online` immediately
    even though no message had been sent and the bridge had freshly
    started — its in-memory PiSession pool was empty so there was no
    actual omp process behind the terminal_session row. Stale rows
    survive bridge restarts; the worker-detection rule then trusts the
    DB and reports `online`. Cleaning them up at supersession time is
    the right correctness fix.
    """
    if not superseded_bridge_ids:
        return
    placeholders = ",".join("?" for _ in superseded_bridge_ids)
    # Defense-in-depth (code review I6, 2026-05-22): scope by agent_id
    # too. Each bridge process today has exactly one AIFY_AGENT_ID so
    # bridge_id is unique per agent, but if multi-agent bridges land
    # later this prevents cross-agent terminal slaughter.
    cursor = await db.execute(
        f"""
        SELECT id, agent_id FROM terminal_sessions
        WHERE bridge_id IN ({placeholders})
          AND agent_id = ?
          AND command IN ({",".join("?" for _ in _borrowed_virtual_rpc_command_set())})
          AND status NOT IN ('stopped', 'failed')
        """,
        (*superseded_bridge_ids, agent_id, *_borrowed_virtual_rpc_command_set()),
    )
    rows = await cursor.fetchall()
    for row in rows:
        terminal_id = str(row["id"] or "").strip()
        owner_agent = str(row["agent_id"] or "").strip()
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'stopped',
                stopped_at = COALESCE(stopped_at, ?),
                updated_at = ?,
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, "Superseded by bridge re-registration; in-memory worker pool empty after restart.", terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "virtual_rpc_stopped_on_bridge_supersession",
            json.dumps({"agentId": owner_agent, "supersededBridgeIds": superseded_bridge_ids}),
        )
        if owner_agent:
            # Clear the agent's virtualTerminal* pointers so dashboard
            # status correctly reports `available` until the next dispatch
            # spawns a fresh worker.
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (owner_agent,))).fetchone()
            if agent_row:
                rs = _json_loads_or(agent_row["runtime_state"], {}) or {}
                if str(rs.get("virtualTerminalId") or "").strip() == terminal_id:
                    rs.pop("virtualTerminal", None)
                    rs.pop("virtualTerminalId", None)
                    await db.execute(
                        "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
                        (json.dumps(rs), now, owner_agent),
                    )
            await _invalidate_agent_live_state(db, owner_agent)


def _terminal_session_to_dict(*a, **k):
    from service.control_plane import _terminal_session_to_dict as _impl

    return _impl(*a, **k)


async def _touch_current_agent_session(*a, **k):
    from service.control_plane import _touch_current_agent_session as _impl

    return await _impl(*a, **k)


def _workspace_for_environment(*a, **k):
    from service.control_plane import _workspace_for_environment as _impl

    return _impl(*a, **k)


async def engine_status(*a, **k):
    from service.control_plane import engine_status as _impl

    return await _impl(*a, **k)


def _borrowed_list_agents_refresh_limit():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import LIST_AGENTS_REFRESH_LIMIT

    return LIST_AGENTS_REFRESH_LIMIT


def _borrowed_terminal_output_writes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import TERMINAL_OUTPUT_WRITES

    return TERMINAL_OUTPUT_WRITES


def _borrowed_virtual_pi_rpc_command():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import VIRTUAL_PI_RPC_COMMAND

    return VIRTUAL_PI_RPC_COMMAND


def _borrowed_virtual_rpc_commands_by_runtime():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import VIRTUAL_RPC_COMMANDS_BY_RUNTIME

    return VIRTUAL_RPC_COMMANDS_BY_RUNTIME


def _borrowed_virtual_rpc_command_set():
    """BORROWED constant: one owner, never a copy (finding N7).

    Derived from the map above, and read by `_worker_liveness_for` in the router plus four other
    modules through accessors of their own — so it stays router-owned even though its heaviest
    reader moved here in v0.5.3.
    """
    from service.control_plane import VIRTUAL_RPC_COMMAND_SET

    return VIRTUAL_RPC_COMMAND_SET


def _borrowed_ansi_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _ANSI_RE

    return _ANSI_RE


def _borrowed_channel_claim_runtimes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _CHANNEL_CLAIM_RUNTIMES

    return _CHANNEL_CLAIM_RUNTIMES


def _borrowed_console_tail_max_bytes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _CONSOLE_TAIL_MAX_BYTES

    return _CONSOLE_TAIL_MAX_BYTES


def _borrowed_console_tail_max_lines():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _CONSOLE_TAIL_MAX_LINES

    return _CONSOLE_TAIL_MAX_LINES


def _borrowed_live_session_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _LIVE_SESSION_STATUSES

    return _LIVE_SESSION_STATUSES


def _borrowed_manual_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _MANUAL_STATUSES

    return _MANUAL_STATUSES


def _borrowed_reap_triad_body_sentinel():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _REAP_TRIAD_BODY_SENTINEL

    return _REAP_TRIAD_BODY_SENTINEL


def _borrowed_runtime_config_live_keys():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _RUNTIME_CONFIG_LIVE_KEYS

    return _RUNTIME_CONFIG_LIVE_KEYS


def _borrowed_shell_placeholder_handle_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _SHELL_PLACEHOLDER_HANDLE_RE

    return _SHELL_PLACEHOLDER_HANDLE_RE


def _borrowed_terminal_end_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _TERMINAL_END_STATUSES

    return _TERMINAL_END_STATUSES


def _borrowed_windows_drive_cwd_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _WINDOWS_DRIVE_CWD_RE

    return _WINDOWS_DRIVE_CWD_RE


def _borrowed_wsl_drive_cwd_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _WSL_DRIVE_CWD_RE

    return _WSL_DRIVE_CWD_RE


def _borrowed_listen_events():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _listen_events

    return _listen_events




async def _apply_status_event(db, agent_id: str, event: dict) -> dict:
    now = _now()
    row = await (await db.execute(
        "SELECT in_turn, awaiting_input, turn_run_id FROM agent_status_state WHERE agent_id = ?",
        (agent_id,))).fetchone()
    cur = {"in_turn": (row["in_turn"] if row else 0),
           "awaiting_input": (row["awaiting_input"] if row else 0),
           "turn_run_id": (row["turn_run_id"] if row else "")}
    new = apply_event(cur, event)
    await db.execute("""
        INSERT INTO agent_status_state (agent_id, in_turn, awaiting_input, turn_run_id,
                                        last_event, last_event_at, updated_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(agent_id) DO UPDATE SET
            in_turn=excluded.in_turn, awaiting_input=excluded.awaiting_input,
            turn_run_id=excluded.turn_run_id, last_event=excluded.last_event,
            last_event_at=excluded.last_event_at, updated_at=excluded.updated_at
    """, (agent_id, new["in_turn"], new["awaiting_input"], new["turn_run_id"],
          str(event.get("kind") or ""), now, now))
    await db.commit()
    return new


async def _broadcast_agent_status(ws, db, agent_id: str) -> None:
    """Recompute one agent's live status and push it to dashboards so an
    operator-driven state transition is reflected without waiting for the 60s
    reconcile sweep or a full client refetch. Best-effort: never raise into the
    caller. Mirrors the single-agent GET status compute (_compute_live_status_cache).
    """
    if ws is None:
        return
    try:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            return
        settings = await _load_settings(db)
        cache = await _compute_live_status_cache(db, row, settings=settings)
        status = cache.get("status") or ""
        # PUSH/POLL PARITY: the WS push serves the SAME proof-engine value the polled read does
        # (derive of the assembled inputs), so a push never overwrites a correct polled status.
        note = cache.get("reason") or ""
        if status not in _borrowed_manual_statuses():
            try:
                _derived = derive(cache["status_inputs"])
                # PUSH/POLL PARITY of the NOTE too (2026-07-10 review): the polled
                # read blanks the legacy-cascade reason when derive() disagrees
                # (the reason describes the superseded status). Mirror it here so the
                # WS-pushed statusNote never contradicts the pushed status.
                if _derived != status:
                    note = ""
                status = _derived
            except Exception:
                pass
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status,
            "statusNote": note,
        })
    except Exception:
        pass


async def _broadcast_engine_status(ws, db, agent_id: str, *, settings=None) -> None:
    """status v2 (Phase D1): push the EVENT-ENGINE status for one agent over WS
    so the dashboard reflects a turn start/end the instant the event lands — not
    on its next poll. Best-effort: never raise into the caller. Only meaningful
    under `status_engine=new`; callers gate on the flag so the legacy `old` path
    stays push-identical to before (it uses `_broadcast_agent_status`).
    """
    if ws is None:
        return
    try:
        agent_id = str(agent_id or "").strip()
        if not agent_id:
            return
        row = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        if not row:
            return
        settings = settings or await _load_settings(db)
        # Manual statuses (stop/disable) are operator overrides both paths honor
        # identically — surface the persisted status, not an engine derivation.
        manual = str(row["status"] or "").strip().lower()
        if manual in _borrowed_manual_statuses():
            status = manual
            note = _row_status_note(row)
        else:
            status = await engine_status(db, row, settings=settings)
            note = ""
        await ws.broadcast("agent_status", {
            "agentId": agent_id,
            "status": status or "",
            "statusNote": note or "",
        })
    except Exception:
        pass


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


async def _get_outbound_activity_map(db, agent_ids: list[str], *, include_runs: bool = True) -> dict[str, dict[str, Any]]:
    """When did each agent last PRODUCE something — send a message, finish a run?

    AUDIT FINDING 1 (2026-08-10). Every field on the agent-health surface answered about INBOUND
    or about registration liveness, and none about production:

        unread      inbound messages not yet read      — the wrong direction
        last read   the last message it CONSUMED       — the wrong direction
        last seen   registration/heartbeat liveness    — and a bare status PATCH advances it
        status      worker reachability                — not productivity

    During an outage every one of those stayed individually true while a reply sat undelivered, so
    a manager told the operator three times that a lane was dead. It was not.

    The reporter asked for a DEGRADED/STALE marker. The reviewer argued — correctly, and this is
    why the fix is shaped this way — that a STALE marker retires a DIFFERENT artifact ("the
    delivery path is verified") and STILL cannot say what an agent last produced. Even a perfect
    one leaves callers inferring productivity from inbound fields, which is exactly how the false
    claim was made. Outbound activity is the field that retires it; STALE is complementary.

    Deliberately reads `messages.from_agent` and finished runs — the two places production is
    recorded — and nothing about delivery. Answering one question well beats answering two vaguely,
    which is the failure being fixed.
    """
    if not agent_ids:
        return {}
    placeholders = ",".join("?" for _ in agent_ids)
    out: dict[str, dict[str, Any]] = {a: {} for a in agent_ids}

    # Last message SENT. `messages.timestamp` is epoch MILLISECONDS, not ISO — the schema's one
    # trap, and mixing it with the ISO columns below silently sorts wrong.
    cursor = await db.execute(
        f"""
        SELECT m.from_agent AS agent_id, MAX(m.timestamp) AS ts
        FROM messages m
        WHERE m.from_agent IN ({placeholders})
        GROUP BY m.from_agent
        """,
        tuple(agent_ids),
    )
    for row in await cursor.fetchall():
        ts = row["ts"]
        if ts:
            out.setdefault(row["agent_id"], {})["lastSentAt"] = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(int(ts) / 1000)
            )

    # Last run this agent COMPLETED as the worker. Distinct from "a run targeting it exists",
    # which the dispatch-state field already reports and which says nothing about output.
    #
    # OFF BY DEFAULT ON THE ROSTER, and that is a measured decision rather than caution. The cost
    # is not in the aggregate, it is in the FAN-OUT: with one agent SQLite searches
    # `idx_dispatch_runs_target_status(target_agent, status, requested_at)`; with the whole roster
    # in an `IN (...)` list it abandons that index and builds a temp B-tree over every completed
    # run. Measured on the live DB (18,005 runs), same statement, only the parameter count differs:
    #
    #     42 agents (roster)   37.0 ms median / 42.0 ms p95   TEMP B-TREE FOR GROUP BY
    #      1 agent  (detail)    0.01 ms median /  0.34 ms p95  SEARCH USING idx_dispatch_runs_target_status
    #
    # `GET /agents` is the dashboard's poll path and DECISIONS.md (2026-06-29) is explicit that
    # cost there is what produced the last `database is locked` era — so the roster does not run
    # this at all. `lastSentAt` alone answers "has this agent produced anything", the question the
    # false silent-lane claim turned on, at 2.55 ms on a covering index.
    #
    # The reviewer's alternative was a new index shaped `(status, target_agent, finished_at DESC)`,
    # declined for the surviving detail path — but "index-covered" would be too strong and the
    # reviewer was right to push back on it. `idx_dispatch_runs_target_status` does not include
    # `finished_at`, so it assists the target/status SEARCH and MAX() still reads that agent's
    # matching rows. The single-agent cost therefore scales with ONE AGENT'S history, not the
    # table's — measured live, same plan throughout:
    #
    #     agent with ~0 completed runs      0.004 ms
    #     sc-claude,   3,109 completed       3.84 ms median /  4.83 ms p95
    #     sc-manager,  7,383 completed      13.19 ms median / 16.20 ms p95
    #
    # 13 ms on a detail view someone opened deliberately is acceptable; 13 ms on a 2-second poll
    # across 42 agents is the lock class. That is the whole distinction. Reverse this decision if
    # either (a) run detail returns to a hot/poll path, or (b) a single heavy agent's detail view
    # gets a latency target this exceeds — then an index or a materialized outbound table earns
    # its write cost. Measure first; the 3,500× spread above is why assuming does not work here.
    if not include_runs:
        return out
    cursor = await db.execute(
        f"""
        SELECT target_agent AS agent_id, MAX(finished_at) AS ts
        FROM dispatch_runs
        WHERE target_agent IN ({placeholders})
          AND status = 'completed'
          AND COALESCE(finished_at, '') != ''
        GROUP BY target_agent
        """,
        tuple(agent_ids),
    )
    for row in await cursor.fetchall():
        if row["ts"]:
            out.setdefault(row["agent_id"], {})["lastCompletedRunAt"] = str(row["ts"])

    return out


def _merge_runtime_policy_for_wrapper_reregister(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep durable model/effort policy when a wrapper child refreshes live metadata."""
    previous = existing if isinstance(existing, dict) else {}
    current = incoming if isinstance(incoming, dict) else {}
    durable_previous = {key: value for key, value in previous.items() if key not in _borrowed_runtime_config_live_keys()}
    return {**durable_previous, **current}


async def _record_bridge_registration(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    machine_id: str,
    runtime: str,
    session_mode: str,
    session_handle: str,
    terminal_id: str = "",
    managed_wrapper_child: bool = False,
    now: str,
) -> None:
    """Single source of truth for register-time bridge_instances writes.

    Inserts/updates the bridge_instance row carrying the new bridge_id and
    its logical identity, then supersedes older rows according to the
    runtime ownership model. Generic managed bridges use latest-wins;
    resident bridges and managed wrapper-child bridges protect fresh
    same-logical-owner rows so duplicate registration does not kill work.
    """
    normalized_machine = _normalize_machine_id(machine_id)
    normalized_runtime_value = str(runtime or "")
    normalized_session_mode_value = str(session_mode or "")
    normalized_session_handle_value = str(session_handle or "").strip()
    normalized_terminal_id_value = str(terminal_id or "").strip()
    bridge_kind = "managed-wrapper-child" if managed_wrapper_child else ""
    await db.execute(
        """
        INSERT OR REPLACE INTO bridge_instances (
            id, agent_id, machine_id, runtime, session_mode, session_handle,
            terminal_id, bridge_kind, registered_at, last_seen, superseded_by, superseded_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            bridge_id,
            agent_id,
            normalized_machine,
            normalized_runtime_value,
            normalized_session_mode_value,
            normalized_session_handle_value,
            normalized_terminal_id_value,
            bridge_kind,
            now,
            now,
            "",
            None,
        ),
    )
    # Supersession carve-out applies to resident session_mode and to
    # managed wrapper children. Resident covers operator-side multi-window
    # CLI scenarios where two human-launched shells legitimately coexist
    # for the same identity. Managed wrapper children are bridge-spawned
    # PTYs whose in-process bridge claims channel/resident work; a fresh
    # same-logical-owner re-register is the same wrapper-owner class and
    # must not kill its active turn. Generic MANAGED bridges still use
    # latest-registration-wins to prevent leaked zombies.
    #
    # IMPORTANT: `_fail_active_runs_for_superseded_bridges` will fail
    # in-flight runs owned by the superseded bridges. For generic managed
    # mode that's correct — only one bridge should be driving an active
    # run, and if a new bridge registers, the old in-flight one is
    # presumed orphaned. Resident and wrapper-child carve-outs protect
    # parent/wrapper in-flight runs from duplicate same-owner registrations.
    # Heartbeat-aware carve-out (operator-reported 2026-05-23: 10+ leaked
    # bridge_instances for comms-tech-lead from May 21–22 claude-aify
    # restarts, never superseded). The resident-mode carve-out only
    # protects bridges whose heartbeat is FRESH — a same-identity bridge
    # whose last_seen is past the 5-min stale window is a dead process
    # whose row should be superseded so the table doesn't accumulate
    # zombie entries. Live multi-window resident scenarios still keep the
    # protection because their last_seen heartbeats stay fresh.
    # Latest-launch-wins for resident bridges (2026-05-29). The previous
    # blanket `session_mode == 'resident'` carve-out protected EVERY fresh
    # same-identity resident bridge from supersession, so each new wrapper
    # launch coexisted with the prior one instead of replacing it. In real use
    # that splits one logical agent into multiple live sessions (#1/#2…) and
    # lets stale rows accumulate, and the dashboard/delivery can land on the
    # wrong one. Operators need the tool to self-heal in a messy state, not to
    # require sterile single-launch discipline. So a new resident registration
    # now supersedes prior same-agent/same-machine bridges (the newest live
    # bridge is authoritative). The managed-wrapper-child protection is kept
    # intact: bridge-spawned PTY siblings sharing a terminal must not kill each
    # other. Same-process periodic re-register keeps the same bridge_id and is
    # excluded by `id != ?`, so only genuinely older launches are superseded.
    # Managed visible-TUI coexistence carve-out (2026-05-31). In the visible-TUI
    # managed model a single managed agent has TWO complementary live bridges:
    #   - a standalone `channel-sidecar` (the hermes-managed-host.js delivery
    #     loop) that CLAIMS channel runs and delivers via WS prompt.submit, and
    #   - a `managed-wrapper-child` (the visible TUI's in-session aify-comms MCP)
    #     that exists so the agent can self-reply via comms_send.
    # They play DIFFERENT roles for the same agent and must not supersede each
    # other. Before this carve-out the wrapper-child registration superseded the
    # sidecar (and vice versa on the sidecar's own bridge-registration path),
    # which blocked the superseded one from claiming → delivery silently stalled
    # (observed on gov-tui 2026-05-30: a queued run never claimed). Protect the
    # existing row whenever the registering bridge and the existing row form a
    # sidecar↔wrapper-child pair for the SAME managed agent+machine.
    new_kind = bridge_kind or "managed-resident"  # "" means resident/env bridge
    # KEPT (Task A' #154, 2026-06-01): the liveness beat does not prevent
    # register-time supersession (it only refreshes last_seen and cannot save a
    # row from a competing registration), so this is the only thing protecting a
    # sidecar↔wrapper-child complementary pair from killing each other. Removal
    # probe broke test_wrapper_child_registration_does_not_supersede_channel_sidecar
    # and test_wrapper_child_does_not_supersede_a_STALE_channel_sidecar.
    complementary_pair = (
        (new_kind == "managed-wrapper-child" and normalized_session_mode_value == "managed")
        or new_kind == "channel-sidecar"
    )
    # Complementary visible-TUI pair protection is ABSOLUTE (operator-reported
    # 2026-05-31, sc-claude). A channel-sidecar and a managed-wrapper-child for
    # the SAME managed agent play different roles and must NEVER supersede each
    # other — NOT EVEN when the sidecar's heartbeat is briefly stale during
    # managed-PTY churn. Previously this protection was an OR-branch inside the
    # `stale OR NOT(protected)` predicate, so the 5-min-stale clause overrode it:
    # a stale sidecar got superseded by the wrapper-child registration, and the
    # still-live sidecar's claims were then permanently blocked → delivery
    # silently stalled. Pulling it out as a leading `AND NOT (...)` makes it
    # absolute. The remaining stale/unprotected cleanup applies only to
    # NON-complementary rows (genuine zombies still age out; the live sidecar
    # reuses its stable id and self-refreshes).
    superseded_cursor = await db.execute(
        """
        SELECT id FROM bridge_instances
        WHERE agent_id = ? AND machine_id = ? AND id != ? AND superseded_by = ''
          AND NOT (
            ? = 'resident'
            AND COALESCE(bridge_kind, '') = 'channel-sidecar'
          )
          AND NOT (
            ? = 1
            AND session_mode = 'managed'
            AND COALESCE(bridge_kind, '') IN ('channel-sidecar', 'managed-wrapper-child')
            AND COALESCE(bridge_kind, '') != ?
          )
          AND (
            datetime(COALESCE(last_seen, '1970-01-01')) < datetime('now', '-5 minutes')
            OR NOT (
              runtime = ? AND session_mode = ?
              AND COALESCE(session_handle, '') = ?
              AND ? = 'managed-wrapper-child'
              AND COALESCE(bridge_kind, '') = 'managed-wrapper-child'
              AND COALESCE(terminal_id, '') = ?
            )
          )
        """,
        (
            agent_id,
            normalized_machine,
            bridge_id,
            normalized_session_mode_value,
            1 if complementary_pair else 0,
            new_kind,
            normalized_runtime_value,
            normalized_session_mode_value,
            normalized_session_handle_value,
            bridge_kind,
            normalized_terminal_id_value,
        ),
    )
    superseded_ids = [row["id"] for row in await superseded_cursor.fetchall()]
    if not superseded_ids:
        return
    placeholders = ",".join("?" for _ in superseded_ids)
    await db.execute(
        f"""
        UPDATE bridge_instances
        SET superseded_by = ?, superseded_at = ?
        WHERE id IN ({placeholders})
        """,
        (bridge_id, now, *superseded_ids),
    )
    await _fail_active_runs_for_superseded_bridges(
        db,
        agent_id=agent_id,
        machine_id=normalized_machine,
        superseding_bridge_id=bridge_id,
        finished_at=now,
        superseded_bridge_ids=superseded_ids,
    )
    await _stop_virtual_terminals_for_superseded_bridges(
        db,
        agent_id=agent_id,
        superseded_bridge_ids=superseded_ids,
        now=now,
    )


async def _record_claimer_lease(db, agent_id: str, *, action: str, bridge_id: str, now: str) -> str:
    """WS5 Task 5.1: upsert the agent's claimer lease. `action` is 'acquire'
    (→ state='acquired') or 'release' (→ state='released'). Idempotent; one row
    per agent. Returns the resulting state."""
    state = "acquired" if str(action or "").strip().lower() == "acquire" else "released"
    await db.execute(
        """
        INSERT INTO claimer_leases (agent_id, bridge_id, state, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(agent_id) DO UPDATE SET
            bridge_id = excluded.bridge_id,
            state = excluded.state,
            updated_at = excluded.updated_at
        """,
        (agent_id, str(bridge_id or "").strip(), state, now),
    )
    return state


async def _request_stop_agent_terminals(
    db, agent_id: str, *, requested_by: str, now: str, reap_triad: bool = False,
) -> int:
    """Stop an agent's live MANAGED terminals — an operator Stop must kill the
    running console/TUI, since aify-comms is the lifecycle driver for managed
    sessions (operator-reported 2026-05-31: Stop interrupted the run + marked the
    agent stopped but left the host TUI running). Appends a 'stop' terminal
    control (the bridge's terminal-control poll reaps the PTY) and marks the
    terminal 'stopping'. Skips synthetic (vterm_) and already terminal-state
    rows. Returns the number of terminals signaled.

    reap_triad (fix/hermes-leak P2): stamp the body sentinel so a MANAGED-HERMES
    stop also tears down the detached triad (gateway/loop/daemon) on the bridge,
    even when the agent row is already gone (REMOVE) and session_mode can't be
    resolved at claim time."""
    cursor = await db.execute(
        """
        SELECT id, environment_id, bridge_id, session_id FROM terminal_sessions
        WHERE agent_id = ?
          AND id NOT LIKE 'vterm_%'
          AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering', 'stopping')
        """,
        (agent_id,),
    )
    stop_body = "Agent stopped from dashboard."
    if reap_triad:
        stop_body = f"{_borrowed_reap_triad_body_sentinel()} {stop_body}"
    count = 0
    for t in await cursor.fetchall():
        await _append_terminal_control(
            db,
            terminal_id=t["id"],
            environment_id=t["environment_id"] or "",
            bridge_id=t["bridge_id"] or "",
            action="stop",
            requested_by=requested_by,
            body=stop_body,
        )
        await db.execute(
            "UPDATE terminal_sessions SET status = 'stopping', updated_at = ? WHERE id = ?",
            (now, t["id"]),
        )
        if t["session_id"]:
            await db.execute(
                "UPDATE agent_sessions SET terminal_status = 'stopping', last_seen = ? WHERE id = ?",
                (now, t["session_id"]),
            )
        count += 1
    return count


async def _resolve_live_console_terminal(db, agent_id: str):
    """Resolve an agent's LIVE console terminal row.

    Prefers the terminal_sessions row pointed at by runtime_state.consoleTerminal.
    terminalId (managed claude) or runtime_state.virtualTerminalId (pi/hermes
    virtual). If that pointer is unset or points at an ended terminal, FALL BACK to
    the agent's newest genuinely-live PTY terminal (2026-06-17): the consoleTerminal
    pointer is only written on a register-with-console path, so a managed console that
    LAZY-STARTS on a message leaves it empty — console_tail/console_input then wrongly
    reported "no live console" while the dashboard (which resolves via the live terminal
    row) showed it. The fallback makes the MCP tools agree with the dashboard. Returns
    None only when the agent truly has no live console. Agent-scoped on purpose: callers
    can only reach a terminal *through* the agent, never by arbitrary id; the fallback
    only ever returns a LIVE row that belongs to this agent (no stale/foreign extras).
    """
    agent_row = await (
        await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    ).fetchone()
    if not agent_row:
        return None
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    terminal_id = ""
    if isinstance(runtime_state, dict):
        console_terminal = runtime_state.get("consoleTerminal")
        if isinstance(console_terminal, dict):
            terminal_id = str(console_terminal.get("terminalId") or "").strip()
        if not terminal_id:
            terminal_id = str(runtime_state.get("virtualTerminalId") or "").strip()
    if terminal_id:
        terminal = await (
            await db.execute(
                "SELECT * FROM terminal_sessions WHERE id = ? AND agent_id = ?",
                (terminal_id, agent_id),
            )
        ).fetchone()
        if terminal and str(terminal["status"] or "").strip().lower() not in _borrowed_terminal_end_statuses():
            return terminal
    # Fallback: the agent's newest LIVE, non-virtual PTY terminal (the same live-terminal
    # source the dashboard renders), for lazy-started managed consoles whose pointer is unset.
    return await (
        await db.execute(
            "SELECT * FROM terminal_sessions WHERE agent_id = ? "
            "AND status IN ('starting','attached','running','active','idle','recovering') "
            "AND id NOT LIKE 'vterm_%' ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
    ).fetchone()


def _resume_command_for(runtime: Any, session_handle: Any, agent_id: Any = "") -> str:
    """Takeover/resume command for a session, sourced from the runtime adapter.

    Used by the mode-switch response (managed -> resident takeover) and the
    mutual-exclusion collision guard's actionable error. For hermes the resume
    target is the per-agent daemon session `aify-<agentId>` when no concrete
    handle is pinned; everything else resumes by the pinned handle. Best-effort:
    returns "" if the adapter has no resume command (never raises).
    """
    handle = str(session_handle or "").strip()
    normalized = _normalize_runtime(runtime)
    if not handle and normalized == "hermes" and agent_id:
        handle = f"aify-{agent_id}"
    if not handle:
        return ""
    try:
        from service.runtimes import adapter_for
        # Pass the agent id: the wrapper needs `--aify-agent` to export AIFY_AGENT_ID,
        # without which the resumed session's turn detector and turn hooks all silently
        # no-op and its status latches (the general-manager incident). A resume command
        # that omits it is a command that breaks the agent it resumes.
        return adapter_for(normalized).resume_command(handle, str(agent_id or "").strip()) or ""
    except Exception:
        return ""


def _runtime_handle_from_state(runtime: Any, runtime_state: Any) -> str:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    normalized = _normalize_runtime(runtime)
    if normalized == "codex":
        return str(state.get("threadId") or state.get("sessionId") or "").strip()
    if normalized == "pi":
        return str(state.get("sessionId") or state.get("threadId") or state.get("sessionFile") or "").strip()
    if normalized == "hermes":
        return str(state.get("sessionId") or state.get("threadId") or state.get("sessionKey") or "").strip()
    return str(state.get("sessionId") or state.get("threadId") or "").strip()


def _runtime_state_replacing_handle(runtime: Any, runtime_state: Any, session_handle: str) -> dict[str, Any]:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    result = dict(state or {})
    result.pop("sessionId", None)
    result.pop("threadId", None)
    return _runtime_state_with_handle(runtime, result, session_handle)


def _sanitize_session_handle(session_handle: Any) -> str:
    """Drop an unexpanded shell placeholder passed as a session handle.

    Callers sometimes register with sessionHandle="$HERMES_SESSION_ID" (or
    "$CODEX_THREAD_ID", "${VAR}") from a shell/MCP context where the variable was
    empty or never expanded, so the literal placeholder string gets stored. That
    can never resume a real runtime session and surfaces downstream as
    "session not found" plus a nonsensical `--resume ${HERMES_SESSION_ID}` resume
    command. Treat a handle that is *entirely* such a placeholder as no handle.
    Real handles (UUIDs, timestamp_hash ids) never match this shape.
    """
    handle = str(session_handle or "").strip()
    if handle and _borrowed_shell_placeholder_handle_re().match(handle):
        return ""
    return handle


def _session_capabilities_replacing_handle(capabilities: Any, session_handle: str) -> dict[str, Any]:
    existing = capabilities if isinstance(capabilities, dict) else _json_loads_or(capabilities, {})
    result = dict(existing or {}) if isinstance(existing, dict) else {}
    handle_present = bool(str(session_handle or "").strip())
    result.setdefault("persistent", True)
    result["bridgeResume"] = True
    result["nativeResume"] = handle_present
    return result


def _synth_terminal_should_be_created(runtime: str, settings: dict[str, Any]) -> bool:
    """Plan 4 (2026-05-25): synth-terminal (aify://virtual-rpc/<runtime>) is
    deprecated for wrapper-backed runtimes. The wrapper PTY IS the terminal.
    Synth stays for native managed runtimes such as pi/opencode and for
    native-controller fallback when wrapper backing is disabled.
    """
    if _managed_via_wrapper_for_runtime(settings, runtime):
        return False
    return True


async def _upsert_resident_agent_session(
    db,
    *,
    agent_id: str,
    runtime: str,
    workspace: str,
    machine_id: str,
    session_handle: str,
    runtime_config: dict[str, Any] | None,
    bridge_id: str,
    capabilities: list[str] | None,
    now: str,
) -> str:
    """Create the dashboard-visible session row for an operator-open CLI."""

    config = runtime_config if isinstance(runtime_config, dict) else {}
    machine = str(machine_id or "").strip()
    env_row = None
    if machine:
        env_row = await (await db.execute(
            """
            SELECT id
            FROM environments
            WHERE lower(machine_id) = lower(?)
              AND status != 'forgotten'
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (machine,),
        )).fetchone()
    if not env_row:
        return ""

    # FIX 1 (2026-06-03): the resident session id must be STABLE across relaunches
    # so a relaunch UPSERTs the SAME row instead of minting a new resident_* every
    # launch. session_handle / gatewayUrl / bridge_id all ROTATE per launch, so the
    # ON CONFLICT(id) DO UPDATE could never match and duplicate rows accumulated.
    # Key on (machine or env id or agent) — stable per (agent_id, runtime, machine).
    key_material = machine or str(env_row["id"]) or agent_id
    session_id = f"resident_{uuid.uuid5(uuid.NAMESPACE_URL, f'aify-comms:{agent_id}:{runtime}:{key_material}').hex[:16]}"
    app_server_url = str(config.get("appServerUrl") or "").strip()
    telemetry = {
        "resident": True,
        "nativeResume": bool(session_handle),
        "bridgeResume": bool(bridge_id),
        "cliAttach": True,
        "gateway": bool(str(config.get("gatewayUrl") or "").strip()),
    }
    await db.execute(
        """
        INSERT INTO agent_sessions (
            id, agent_id, environment_id, runtime, workspace, mode,
            owner_mode, owner_bridge_id, terminal_id, terminal_status, terminal_command, terminal_workspace,
            process_id, session_handle, app_server_url, spawn_spec_id, spawn_request_id,
            capabilities, telemetry, status, started_at, last_seen, ended_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            runtime = excluded.runtime,
            workspace = excluded.workspace,
            mode = excluded.mode,
            owner_mode = excluded.owner_mode,
            owner_bridge_id = excluded.owner_bridge_id,
            session_handle = excluded.session_handle,
            app_server_url = excluded.app_server_url,
            capabilities = excluded.capabilities,
            telemetry = excluded.telemetry,
            status = 'running',
            last_seen = excluded.last_seen,
            ended_at = NULL
        """,
        (
            session_id,
            agent_id,
            env_row["id"],
            runtime,
            workspace or "",
            "resident",
            "resident",
            bridge_id or "",
            "",
            "",
            "",
            "",
            "",
            session_handle or "",
            app_server_url,
            None,
            None,
            json.dumps({"resident": True, "cliAttach": True, "capabilities": capabilities or []}),
            json.dumps(telemetry),
            "running",
            now,
            now,
            None,
        ),
    )
    # RC3 (2026-06-03): collapse duplicate resident sessions. The resident session
    # id is a hash of the session_handle (line ~12879), so a relaunch with a new
    # native handle mints a NEW resident_* row while the prior one stays 'running'
    # — the dashboard then shows two live resident sessions for one agent. Retire
    # every OTHER resident session for this agent so exactly one stays live.
    await db.execute(
        """
        UPDATE agent_sessions
        SET status = 'stopped', ended_at = ?
        WHERE agent_id = ?
          AND mode = 'resident'
          AND id != ?
          AND status NOT IN ('stopped', 'failed', 'exited')
        """,
        (now, agent_id, session_id),
    )
    return session_id


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
    if family in {"linux", "darwin", "wsl"} and _borrowed_windows_drive_cwd_re().match(resolved_cwd):
        hint = '/mnt/<drive>/...' if family in {"linux", "wsl"} else "/Users/..."
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on {family}. '
                f'Use a native host path such as "{hint}", not a Windows drive-letter path.'
            ),
        )
    if family == "win32" and _borrowed_wsl_drive_cwd_re().match(resolved_cwd):
        raise HTTPException(
            400,
            (
                f'Invalid cwd "{resolved_cwd}" for codex live agent "{agent_id}" on Windows. '
                'Use forward-slash drive-letter form like "C:/repo", not a "/mnt/..." WSL path.'
            ),
        )