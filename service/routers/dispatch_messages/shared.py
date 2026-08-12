"""Helpers owned by the dispatch+messages pair, and every borrow the pair still needs.

v0.5.2l. Two things live here, and the distinction is the whole point of the package:

OWNED (8 helpers). Used by dispatch handlers AND message handlers, and by nothing else.
Splitting dispatch and messages into separate modules would have made each of these a borrow in BOTH
— two shims, no owner, and a consolidation tag owed later. Moving the pair together lets them have a
real owner now. That is why the reviewer ruled for one combined tag.

BORROWED (33 names). Defined once here so `dispatch.py` and `messages.py` share one shim
rather than declaring their own. Each is still used by `agents`, by router-internal code, or by an
already-moved module borrowing it through the router — established by FOLLOWING THE SHIMS, not by raw
caller count. That distinction mattered: several names that looked local to this pair are borrowed by
channels, spawn_requests, sessions or the reconcilers, and moving them would have broken those.

Nearly all of it retires with `agents`, the last domain.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import HTTPException, Query, Request

from service import longpoll
from service.api_core.events import _append_dispatch_event, _append_terminal_event
from service.api_core.routing import domain_router
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import (
    _clip_text,
    _dedupe_preserve,
    _iso_from_ms,
    _json_loads_or,
    _quote_untrusted_subject,
    _row_require_reply,
    _timestamp_sort_key,
)
from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.settings import DEFAULT_SETTINGS, _load_settings
from service.api_core.validation import validate_name
from service.api_core.ws import _get_ws
from service.clock import iso_to_epoch as _iso_to_epoch
from service.clock import now as _now
from service.db import SQLITE_CLAIM_BUSY_TIMEOUT_MS, get_db
from service.ntfy import notify_operator
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state
from service.status_engine import apply_event

# Resolved to their REAL owners, asked of the repo rather than guessed:
from service.api_core.events import _append_terminal_control
from service.api_core.serialization import _machine_ids_same_host
from service.db import _NATIVE_MANAGED_RUNTIMES
from service.reconcilers.dispatch_lifecycle import _close_steered_contracts_for_parent_run, _mark_dispatch_run_answered
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
from service.status_engine import VALID_STATUSES
from service.env_status import environment_effective_status as _environment_effective_status
# Imported for the ANNOTATION as much as the call: under postponed evaluation an unresolved
# model name does not fail import, it fails a type-hint gate or a request at runtime.
from service.models import DispatchClaimRequest

logger = logging.getLogger("aify_comms.routers.dispatch_messages.shared")

async def _adopt_live_resident_driver(*a, **k):
    from service.control_plane import _adopt_live_resident_driver as _impl

    return await _impl(*a, **k)


async def _agent_tombstone(*a, **k):
    from service.control_plane import _agent_tombstone as _impl

    return await _impl(*a, **k)


def _auto_handoff_subject_for_run(*a, **k):
    from service.control_plane import _auto_handoff_subject_for_run as _impl

    return _impl(*a, **k)


def _auto_handoff_body_for_run(*a, **k):
    from service.control_plane import _auto_handoff_body_for_run as _impl

    return _impl(*a, **k)


async def _active_wrapper_terminal_id(*a, **k):
    from service.control_plane import _active_wrapper_terminal_id as _impl

    return await _impl(*a, **k)


async def _active_wrapper_terminal_not_ready_reason(*a, **k):
    from service.control_plane import _active_wrapper_terminal_not_ready_reason as _impl

    return await _impl(*a, **k)


async def _bridge_claim_block_reason(
    db,
    *,
    bridge_id: str,
    agent_id: str,
    agent_row,
    execution_modes: Optional[list[str]] = None,
    bridge_kind_hint: str = "",
) -> Optional[dict[str, Any]]:
    """Return a blockedBy payload when an old stdio bridge should not claim work.

    `bridge_kind_hint` is the claimant-declared bridge kind from the request
    (DispatchClaimRequest.bridgeKind). Standalone channel sidecars
    (claude-channel.js / hermes-channel.js) declare "channel-sidecar"; it lets
    the wrapper-backed gate below distinguish them from a wrapper-PTY child.
    """
    if not bridge_id:
        return None

    cursor = await db.execute(
        "SELECT superseded_by, bridge_kind, terminal_id FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if row and (row["superseded_by"] or "").strip():
        return {
            "reason": "bridge_superseded",
            "bridgeId": bridge_id,
            "agentId": agent_id,
            "hint": "This bridge has been replaced by a newer registration. Shut it down.",
        }

    runtime = _normalize_runtime((agent_row["runtime"] if agent_row else "") or "generic")
    if runtime not in {"codex", "opencode", "pi", "hermes"}:
        return None

    # Plan 6 follow-up (2026-05-26): wrapper-child bridges (the in-process
    # mcp/stdio/server.js that runs INSIDE a *-aify wrapper PTY) legitimately
    # have a different bridge_id from the environment bridge. They claim
    # channel-mode runs for managed-via-wrapper agents (see _borrowed_channel_claim_runtimes()
    # at line 290 and dispatch-execution.js supportedExecutionModes). Without
    # this carve-out, every wrapper-child claim hits "environment_bridge_not_current"
    # at line 1701 because the env bridge_id != the wrapper-child bridge_id —
    # and managed codex/hermes dispatches sit queued forever even when the
    # wrapper PTY is alive and its inner MCP server has registered. Detect a
    # wrapper-child claim by: (a) the request includes 'channel' in executionModes;
    # (b) the runtime is in _borrowed_channel_claim_runtimes() (managed-via-wrapper-eligible);
    # (c) the claimant bridge is registered for this agent (in bridge_instances).
    # Operator-observed 2026-05-26 with graph-tester-pi before Pi was moved
    # back to native RPC: inner MCP bridge
    # `2e8b7d91-...` registered fine, but its claims were silently rejected
    # against the env bridge `e1ef4cae-...`.
    supported_modes = {str(m or "").strip().lower() for m in (execution_modes or []) if str(m or "").strip()}
    bridge_kind = str((row["bridge_kind"] if row and "bridge_kind" in row.keys() else "") or "").strip()
    bridge_terminal_id = str((row["terminal_id"] if row and "terminal_id" in row.keys() else "") or "").strip()
    is_wrapper_child_claim = (
        "channel" in supported_modes
        and runtime in _borrowed_channel_claim_runtimes()
        and bridge_kind == "managed-wrapper-child"
    )
    # Standalone channel sidecar (Task 1.5/1.5b): the per-agent
    # claude-channel.js / hermes-channel.js process. It is NOT a wrapper-PTY
    # child and owns no visible Console terminal — it drives the agent's own
    # session (claude via MCP push; hermes via the pinned api_server daemon).
    # It declares bridgeKind="channel-sidecar" on the claim. Accept it on the
    # SAME basis claude's standalone sidecar is already accepted (claude
    # bypasses the wrapper-child gate purely by runtime — it is not in the
    # {codex, opencode, pi, hermes} set above). hermes IS in that set (it also
    # has a legacy wrapper-PTY path), so without this signal its standalone
    # sidecar would be wrongly rejected with managed_wrapper_child_required and
    # delivery would silently never happen.
    is_channel_sidecar_claim = (
        "channel" in supported_modes
        and runtime in _borrowed_channel_claim_runtimes()
        and str(bridge_kind_hint or "").strip().lower() == "channel-sidecar"
    )

    session_mode = _normalize_session_mode((agent_row["session_mode"] if agent_row else "") or "resident")
    runtime_state = _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {}
    current_bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
    runtime_state_environment_id = str(runtime_state.get("environmentId") or "").strip()
    managed_environment_id = runtime_state_environment_id
    if session_mode == "managed" and not managed_environment_id:
        session_cursor = await db.execute(
            """
            SELECT environment_id
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )
        session_row = await session_cursor.fetchone()
        managed_environment_id = str((session_row["environment_id"] if session_row else "") or "").strip()
    # RC1 (2026-06-03): a declared channel-sidecar (hermes-managed-host.js loop /
    # claude-channel.js) is a LEGITIMATELY distinct bridge id from the agent's
    # in-session MCP bridge (runtime_state.bridgeInstanceId). For RESIDENT hermes,
    # delivery is owned by that sidecar (the resident MAIN bridge no longer claims
    # resident hermes — see mcp/stdio/dispatch-execution.js). Without this carve-out
    # the one-current-bridge guard rejects the sidecar's claim with bridge_not_current
    # and the run sits queued forever with no valid claimer. The managed path already
    # exempts the sidecar (below, lines ~2336/2395); the resident path must too.
    if (session_mode != "managed" or not managed_environment_id) and current_bridge_id and current_bridge_id != bridge_id and not is_channel_sidecar_claim:
        return {
            "reason": "bridge_not_current",
            "bridgeId": bridge_id,
            "currentBridgeId": current_bridge_id,
            "agentId": agent_id,
            "hint": "This bridge is not the current stdio bridge for the agent. Restart or shut down stale runtime bridge/wrapper processes such as codex-aify, omp-aify, or pi-aify.",
        }

    if session_mode == "managed":
        settings = await _load_settings(db)
        # A standalone channel sidecar (claude-channel.js / hermes-channel.js)
        # is accepted directly: it owns no wrapper PTY, so the
        # managed-wrapper-child requirement and the PTY-terminal availability /
        # mismatch / readiness checks below do not apply to it. This is the
        # symmetric route — claude's standalone sidecar already bypasses these
        # by runtime (claude is not in the wrapper-backed set); hermes's
        # standalone sidecar bypasses them by declaring bridgeKind=channel-
        # sidecar (hermes ALSO has a legacy wrapper-PTY path, so it can't be
        # carved out by runtime alone). The environment online/bridge checks
        # still run below (the sidecar must not deliver into a dead env).
        wrapper_backed_channel_claim = (
            "channel" in supported_modes
            and runtime in {"codex", "hermes"}
            and _managed_via_wrapper_for_runtime(settings, runtime)
            and not is_channel_sidecar_claim
        )
        if (
            wrapper_backed_channel_claim
            and not is_wrapper_child_claim
        ):
            return {
                "reason": "managed_wrapper_child_required",
                "bridgeId": bridge_id,
                "agentId": agent_id,
                "runtime": runtime,
                "hint": (
                    f"Managed {runtime} is wrapper-backed. The environment bridge must start/reuse the "
                    "*-aify PTY and let that wrapper's child bridge claim channel dispatches."
                ),
            }
        if wrapper_backed_channel_claim and is_wrapper_child_claim:
            active_terminal_id = await _active_wrapper_terminal_id(db, agent_id, settings=settings)
            if not active_terminal_id:
                return {
                    "reason": "managed_wrapper_terminal_unavailable",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "hint": "Managed wrapper-backed dispatch has no active wrapper PTY. Recover or restart the managed session, then retry.",
                }
            if bridge_terminal_id != active_terminal_id:
                return {
                    "reason": "managed_wrapper_terminal_mismatch",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "bridgeTerminalId": bridge_terminal_id,
                    "currentTerminalId": active_terminal_id,
                    "hint": "This wrapper child belongs to an old terminal. Stop the stale wrapper and let the current managed PTY child claim the run.",
                }
            not_ready_reason = await _active_wrapper_terminal_not_ready_reason(db, active_terminal_id, runtime)
            if not_ready_reason:
                return {
                    "reason": "managed_wrapper_terminal_not_ready",
                    "bridgeId": bridge_id,
                    "agentId": agent_id,
                    "runtime": runtime,
                    "terminalId": active_terminal_id,
                    "hint": not_ready_reason,
                }
        environment_id = managed_environment_id
        if environment_id:
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
            env_row = await env_cursor.fetchone()
            current_environment_bridge = str((env_row["bridge_id"] if env_row else "") or "").strip()
            env_status = _environment_effective_status(
                env_row,
                offline_seconds=settings.get("environment_offline_seconds", 90),
            ) if env_row else "offline"
            if (
                current_environment_bridge
                and current_environment_bridge != bridge_id
                and not is_wrapper_child_claim
                and not is_channel_sidecar_claim
            ):
                return {
                    "reason": "environment_bridge_not_current",
                    "bridgeId": bridge_id,
                    "currentBridgeId": current_environment_bridge,
                    "environmentId": environment_id,
                    "agentId": agent_id,
                    "hint": "This managed agent belongs to an environment whose current bridge is different. Restart or kill the stale aify-comms bridge, then recover/restart the agent from Sessions.",
                }
            if env_status and env_status not in {"online", "degraded"}:
                return {
                    "reason": "environment_not_online",
                    "bridgeId": bridge_id,
                    "environmentId": environment_id,
                    "environmentStatus": env_status,
                    "agentId": agent_id,
                    "hint": "The managed agent's environment is not online. Start the environment bridge or assign the agent to another online environment.",
                }

    return None


async def _dispatch_conversation_context(db, row, *, limit: int = 8) -> list[dict[str, Any]]:
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    target_agent = str((row["target_agent"] if row else "") or "").strip()
    if not from_agent or not target_agent:
        return []
    current_message_ids = set(_dispatch_source_message_ids(row))
    cursor = await db.execute(
        """
        SELECT id, from_agent, to_agent, type, subject, body, priority, timestamp, in_reply_to
        FROM messages
        WHERE source = 'direct'
          AND (
            (from_agent = ? AND to_agent = ?)
            OR (from_agent = ? AND to_agent = ?)
          )
        ORDER BY timestamp DESC, rowid DESC
        LIMIT ?
        """,
        (from_agent, target_agent, target_agent, from_agent, max(1, int(limit or 8)) + len(current_message_ids)),
    )
    rows = await cursor.fetchall()
    context = []
    for message in reversed(rows):
        if message["id"] in current_message_ids:
            continue
        context.append({
            "id": message["id"],
            "from": message["from_agent"],
            "to": message["to_agent"],
            "type": message["type"],
            "subject": message["subject"],
            "body": message["body"] or "",
            "priority": message["priority"],
            "timestamp": message["timestamp"],
            "inReplyTo": message["in_reply_to"],
        })
        if len(context) >= limit:
            break
    return context


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


def _dispatch_reply_state(*a, **k):
    from service.control_plane import _dispatch_reply_state as _impl

    return _impl(*a, **k)


async def _has_claimable_steerable_run(
    db,
    *,
    agent_row,
    supported_modes: set[str],
    agent_runtime: str,
) -> bool:
    """True when the turn-busy claim gate should be BYPASSED because a queued
    channel/resident run can be steered (injected) into a mid-turn target.

    Used only by the /dispatch/claim turn-busy gate (send-deadlock fix,
    2026-06-02). The carve-out fires when BOTH hold:

      * the TARGET can accept a mid-turn inject — `steer` is in its computed
        capabilities (_row_capabilities). For claude that means a managed or
        channelEnabled-resident session; a plain resident claude without
        channelEnabled, or a resident codex/opencode/pi, has no `steer` and is
        NOT bypassed. This is the SAME predicate the send-time steer path uses
        (line ~6770: `active_run and "steer" in capabilities`), so the gate and
        the steer route agree on who is injectable.
      * there is at least one QUEUED run in channel/resident execution mode that
        this bridge's supported_modes can actually claim. A managed (headless)
        run is never injectable, so it stays queued behind the turn as before.

    Returning False preserves the original "wait for the turn to end" behavior.
    """
    capabilities = _row_capabilities(agent_row)
    if "steer" not in capabilities:
        return False
    target_agent = str((agent_row["id"] if agent_row else "") or "")
    if not target_agent:
        return False
    cursor = await db.execute(
        """
        SELECT execution_mode, requested_runtime, queue_if_busy, steer_if_busy
        FROM dispatch_runs
        WHERE target_agent = ? AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 25
        """,
        (target_agent,),
    )
    for run in await cursor.fetchall():
        if bool(run["queue_if_busy"]) or not bool(run["steer_if_busy"]):
            continue
        run_execution_mode = str((run["execution_mode"] or "managed")).strip().lower()
        if run_execution_mode not in {"channel", "resident"}:
            continue
        if supported_modes and run_execution_mode not in supported_modes:
            continue
        requested_runtime = str(run["requested_runtime"] or "").strip()
        if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
            continue
        return True
    return False


def _is_replaceable_auto_handoff_message(existing_message, replied_run) -> bool:
    if not existing_message or not replied_run:
        return True
    existing_body = str((existing_message["body"] if "body" in existing_message.keys() else "") or "")
    if existing_body.startswith("Auto-mirrored dispatch "):
        return True
    return (
        existing_body == _auto_handoff_body_for_run(replied_run)
        and str((existing_message["subject"] if "subject" in existing_message.keys() else "") or "").strip()
        == _auto_handoff_subject_for_run(replied_run)
        and str((existing_message["from_agent"] if "from_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["target_agent"] if "target_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["to_agent"] if "to_agent" in existing_message.keys() else "") or "").strip()
        == str((replied_run["from_agent"] if "from_agent" in replied_run.keys() else "") or "").strip()
        and str((existing_message["in_reply_to"] if "in_reply_to" in existing_message.keys() else "") or "").strip()
        == str((replied_run["message_id"] if "message_id" in replied_run.keys() else "") or "").strip()
    )


def _dispatch_source_message_ids(*a, **k):
    from service.control_plane import _dispatch_source_message_ids as _impl

    return _impl(*a, **k)


async def _mark_dispatch_source_messages_read(*a, **k):
    from service.control_plane import _mark_dispatch_source_messages_read as _impl

    return await _impl(*a, **k)


def _message_satisfies_reply_contract(*a, **k):
    from service.control_plane import _message_satisfies_reply_contract as _impl

    return _impl(*a, **k)


def _pending_dispatch_count(*a, **k):
    from service.control_plane import _pending_dispatch_count as _impl

    return _impl(*a, **k)


def _row_capabilities(*a, **k):
    from service.control_plane import _row_capabilities as _impl

    return _impl(*a, **k)


async def _record_channel_sidecar_heartbeat(*a, **k):
    from service.control_plane import _record_channel_sidecar_heartbeat as _impl

    return await _impl(*a, **k)


async def _release_stale_console_owner_for_claim(db, owner_session, req: DispatchClaimRequest) -> Optional[dict[str, Any]]:
    terminal_id = str(owner_session["terminal_id"] or "").strip()
    terminal_status = str(owner_session["terminal_status"] or "").strip().lower()
    terminal = None
    if terminal_id:
        terminal = await (await db.execute("SELECT * FROM terminal_sessions WHERE id = ?", (terminal_id,))).fetchone()
        if terminal:
            terminal_status = str(terminal["status"] or terminal_status or "").strip().lower()

    settings = await _load_settings(db)
    stale_after = max(30, int(settings.get("environment_offline_seconds", 90) or 90))
    terminal_bridge_id = str((terminal["bridge_id"] if terminal else "") or "").strip()
    env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (owner_session["environment_id"],))).fetchone()
    env_status = _environment_effective_status(env_row, offline_seconds=stale_after) if env_row else "offline"
    bridge_current = bool(
        env_row
        and env_status in {"online", "degraded"}
        and terminal_bridge_id
        and terminal_bridge_id == str(env_row["bridge_id"] or "").strip()
    )
    active_status = terminal_status in {"starting", "attached", "running", "active", "idle"}
    # Keep a live Console owner regardless of how long it has been QUIET. Liveness is
    # bridge_current (the owning env bridge is online AND still owns this terminal's
    # bridge_id) + active_status (the PTY has not posted a terminal/exit status) — NOT
    # output age. An alive-but-quiet managed worker (idle between turns, or mid-turn
    # not printing) legitimately emits nothing for minutes; releasing it on a ~90s
    # output-age then respawned a fresh PTY on the NEXT dispatch — the terminal-churn
    # / "terminal closes constantly" + accumulating terminal_sessions rows incident
    # (2026-06-06). Age is not liveness; the env-offline + bridge-mismatch checks below
    # (real liveness) still release a genuinely-dead owner.
    if terminal and active_status and bridge_current:
        return {
            "reason": "console_owner_active",
            "sessionId": owner_session["id"],
            "terminalId": terminal_id,
            "terminalStatus": terminal_status,
            "hint": "Console owns this runtime handle. Stop or return Console to managed before claiming managed Messenger work.",
        }

    now = _now()
    reason = "Released stale Console owner before managed dispatch claim."
    await db.execute(
        """
        UPDATE agent_sessions
        SET owner_mode = 'managed',
            terminal_status = 'failed',
            last_seen = ?
        WHERE id = ?
        """,
        (now, owner_session["id"]),
    )
    if terminal:
        await db.execute(
            """
            UPDATE terminal_sessions
            SET status = 'failed',
                updated_at = ?,
                stopped_at = COALESCE(stopped_at, ?),
                error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
            WHERE id = ?
            """,
            (now, now, reason, terminal_id),
        )
        await _append_terminal_event(
            db,
            terminal_id,
            "terminal_owner_released",
            json.dumps({
                "reason": "stale Console owner",
                "requestedByBridge": req.bridgeId or "",
                "previousBridge": terminal_bridge_id,
                "environmentBridge": str(env_row["bridge_id"] or "").strip() if env_row else "",
                "terminalStatus": terminal_status,
            }),
        )
    return None


def _borrowed_active_run_bridge_stale_seconds():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import ACTIVE_RUN_BRIDGE_STALE_SECONDS

    return ACTIVE_RUN_BRIDGE_STALE_SECONDS


def _borrowed_turn_busy_backstop_seconds():
    """BORROWED constant: one owner, never a copy (finding N7).

    This one must stay a borrow even though `_turn_busy_holds_delivery` moved here: the ceiling has
    to equal the status engine's `in_turn` clamp, four other api_v2 readers depend on it, and
    `test_turn_busy_delivery_ceiling` asserts the parity against `api_v2`. A copy here would let the
    delivery gate and the status clamp drift apart, which is the strand the ceiling exists to bound.
    """
    from service.control_plane import TURN_BUSY_BACKSTOP_SECONDS

    return TURN_BUSY_BACKSTOP_SECONDS


def _borrowed_coldstart_refused_prefix():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import COLDSTART_REFUSED_PREFIX

    return COLDSTART_REFUSED_PREFIX


def _borrowed_channel_claim_runtimes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _CHANNEL_CLAIM_RUNTIMES

    return _CHANNEL_CLAIM_RUNTIMES


def _borrowed_channel_managed_runtimes():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _CHANNEL_MANAGED_RUNTIMES

    return _CHANNEL_MANAGED_RUNTIMES


def _borrowed_dispatch_terminal_statuses():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _DISPATCH_TERMINAL_STATUSES

    return _DISPATCH_TERMINAL_STATUSES


def _borrowed_merged_dispatch_header():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _MERGED_DISPATCH_HEADER

    return _MERGED_DISPATCH_HEADER


def _borrowed_unthreaded_handoff_window_ms():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _UNTHREADED_HANDOFF_WINDOW_MS

    return _UNTHREADED_HANDOFF_WINDOW_MS



async def _active_terminal_for_agent(*a, **k):
    from service.control_plane import _active_terminal_for_agent as _impl

    return await _impl(*a, **k)


def _agent_execution_mode(*a, **k):
    from service.control_plane import _agent_execution_mode as _impl

    return _impl(*a, **k)


async def _append_dispatch_control(*a, **k):
    from service.control_plane import _append_dispatch_control as _impl

    return await _impl(*a, **k)


async def _apply_channel_routing_to_claude_runs(*a, **k):
    from service.control_plane import _apply_channel_routing_to_claude_runs as _impl

    return await _impl(*a, **k)


async def _auto_return_resident_to_managed_if_possible(*a, **k):
    from service.control_plane import _auto_return_resident_to_managed_if_possible as _impl

    return await _impl(*a, **k)


async def _clear_turn_busy_if_no_open_reply_owing_run(*a, **k):
    from service.control_plane import _clear_turn_busy_if_no_open_reply_owing_run as _impl

    return await _impl(*a, **k)


def _coldstart_refusal_message(*a, **k):
    from service.control_plane import _coldstart_refusal_message as _impl

    return _impl(*a, **k)


async def _coldstart_spawn_request_for_dispatch(*a, **k):
    from service.control_plane import _coldstart_spawn_request_for_dispatch as _impl

    return await _impl(*a, **k)


async def _create_dispatch_runs(*a, **k):
    from service.control_plane import _create_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _delete_messages_by_ids(*a, **k):
    from service.control_plane import _delete_messages_by_ids as _impl

    return await _impl(*a, **k)


async def _delete_messages_where(*a, **k):
    from service.control_plane import _delete_messages_where as _impl

    return await _impl(*a, **k)


def _dispatch_fix_hint(*a, **k):
    from service.control_plane import _dispatch_fix_hint as _impl

    return _impl(*a, **k)


async def _ensure_managed_pty_for_dispatch(*a, **k):
    from service.control_plane import _ensure_managed_pty_for_dispatch as _impl

    return await _impl(*a, **k)


async def _fail_pending_controls_for_run(*a, **k):
    from service.control_plane import _fail_pending_controls_for_run as _impl

    return await _impl(*a, **k)


async def _finalize_dispatch_runs(*a, **k):
    from service.control_plane import _finalize_dispatch_runs as _impl

    return await _impl(*a, **k)


async def _get_blocking_active_run(*a, **k):
    from service.control_plane import _get_blocking_active_run as _impl

    return await _impl(*a, **k)


async def _get_dispatch_state_for_agent(*a, **k):
    from service.control_plane import _get_dispatch_state_for_agent as _impl

    return await _impl(*a, **k)


async def _get_recipient_info(*a, **k):
    from service.control_plane import _get_recipient_info as _impl

    return await _impl(*a, **k)


async def _has_claimable_spawn_request(*a, **k):
    from service.control_plane import _has_claimable_spawn_request as _impl

    return await _impl(*a, **k)


async def _has_live_managed_wrapper_child(*a, **k):
    from service.control_plane import _has_live_managed_wrapper_child as _impl

    return await _impl(*a, **k)


def _insert_messages_via_console(*a, **k):
    from service.control_plane import _insert_messages_via_console as _impl

    return _impl(*a, **k)


def _is_delivery_only_claude_run(*a, **k):
    from service.control_plane import _is_delivery_only_claude_run as _impl

    return _impl(*a, **k)


async def _managed_environment_unavailable_reason(*a, **k):
    from service.control_plane import _managed_environment_unavailable_reason as _impl

    return await _impl(*a, **k)


def _managed_terminal_backing_enabled(*a, **k):
    from service.control_plane import _managed_terminal_backing_enabled as _impl

    return _impl(*a, **k)




async def _mirror_missing_dispatch_handoff(*a, **k):
    from service.control_plane import _mirror_missing_dispatch_handoff as _impl

    return await _impl(*a, **k)


async def _preflight_live_send_recipients(*a, **k):
    from service.control_plane import _preflight_live_send_recipients as _impl

    return await _impl(*a, **k)


def _reject_sender_truncated_body(*a, **k):
    from service.control_plane import _reject_sender_truncated_body as _impl

    return _impl(*a, **k)


async def _run_contract_reminders_once(*a, **k):
    from service.control_plane import _run_contract_reminders_once as _impl

    return await _impl(*a, **k)


async def _touch_agent(*a, **k):
    from service.control_plane import _touch_agent as _impl

    return await _impl(*a, **k)


async def _touch_current_agent_session(*a, **k):
    from service.control_plane import _touch_current_agent_session as _impl

    return await _impl(*a, **k)


async def _turn_busy_holds_delivery(db, agent_id: str) -> bool:
    """True when the RAW turn_busy flag may still hold delivery back.

    The delivery gates (send-time queue decision + /dispatch/claim) key on the raw
    harness signal on purpose: "explicit queue" means exactly "after this turn", and
    re-deriving that through status or a short window is what made queued sends land
    mid-turn (#236). So this helper does NOT reinterpret the signal — it only applies
    the SAME anti-strand ceiling the status engine already applies to `in_turn`
    (TURN_BUSY_BACKSTOP_SECONDS, see the constant's own note).

    Why a ceiling is required (regression found 2026-07-26): the gates are pure-raw,
    but nothing guarantees turn_busy is ever cleared.
      * The dead-bridge sweeper (_clear_turn_busy_for_dead_bridges) deliberately
        SKIPS turn_bridge_id IN ('', 'user-prompt-submit') — i.e. every hook-driven
        resident-claude turn — and skips any turn whose bridge is still alive.
      * A missed turn-END (killed harness, hook error, or a transcript classifier
        that keeps reading in-flight) therefore latches turn_busy=1 permanently.
    Past the ceiling, status ALREADY stops reporting `working` (derive() clamps
    in_turn in both the push and poll paths). Holding delivery past that point makes
    the two disagree permanently: the dashboard shows an idle agent whose queued work
    can never be claimed. For a target WITHOUT `steer` the claim gate returns early,
    so that agent goes permanently deaf to every dispatch.

    A genuinely long turn is unaffected: the bridge turn detectors KEEP-FRESH re-stamp
    turn-start, so turn_updated_at keeps advancing for as long as real work runs. Only
    an ABANDONED flag ages out — which is exactly the strand this bounds.
    """
    try:
        row = await (await db.execute(
            "SELECT turn_busy, turn_updated_at FROM agent_turn_state WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()
    except Exception:
        # Unreadable turn state must never block delivery — better to deliver.
        return False
    if not row or not int((row["turn_busy"] if "turn_busy" in row.keys() else 0) or 0):
        return False
    seen = _iso_to_epoch(str(row["turn_updated_at"] or ""))
    if not seen:
        # MISSING/UNPARSEABLE timestamp → do NOT hold (fixed 2026-07-26, review follow-up).
        # The first cut returned True here "to trust the raw flag", which quietly reproduced the
        # exact strand this helper exists to prevent: a latched turn_busy=1 whose turn_updated_at
        # is empty or malformed has NOTHING to age against, so it would hold delivery forever and
        # a non-steer target would stay permanently deaf — with no ceiling to rescue it.
        #
        # Releasing is the correct asymmetry. Every writer stamps turn_updated_at via _now()
        # (the /turn-start, /heartbeat and reconcile paths all do), so a blank or unparseable
        # value means a corrupt row, not a live turn. The worst case from releasing is ONE
        # message delivered mid-turn, which the harness queues or the reply reconciles; the worst
        # case from holding is an agent that never receives work again. Prefer the recoverable
        # failure.
        return False
    # A FUTURE timestamp must not hold either (review R4, 2026-07-26). `now - seen` goes NEGATIVE
    # for a clock-skewed or bad write, which trivially satisfies `<= CEILING` — so the flag would
    # hold delivery forever, the exact permanent strand this ceiling exists to bound. Requiring a
    # non-negative age closes it: only an age genuinely inside the window holds.
    age = datetime.now(timezone.utc).timestamp() - seen
    return 0 <= age <= _borrowed_turn_busy_backstop_seconds()


def _wake_agent(*a, **k):
    from service.control_plane import _wake_agent as _impl

    return _impl(*a, **k)


def _console_dispatch_input_body(req: DispatchRequest, *, recipient_id: str, message_id: str, bracketed_paste: bool = True) -> str:
    subject = str(req.subject or "").strip()
    body = str(req.body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    message = "\n".join(
        part for part in [
            "AIFY dashboard message",
            f"From: {req.from_agent}",
            f"To: {recipient_id}",
            f"Type: {req.type}",
            # Quoted like every other echo — see _quote_untrusted_subject. This one has
            # From/To framing around it, so it is the least dangerous site; one rule
            # beats four judgement calls about how much framing is enough.
            f"Subject: {_quote_untrusted_subject(subject, 240)}" if subject else "",
            f"MessageId: {message_id}",
            "",
            body,
            "",
            "Reply in the dashboard when appropriate, using the available aify-comms tools.",
        ] if part != ""
    )
    if bracketed_paste:
        return f"\x1b[200~{message}\x1b[201~\r"
    return f"{message}\r"


def _dispatch_requires_reply(explicit: Optional[bool], *, default: bool) -> bool:
    if explicit is None:
        return bool(default)
    return bool(explicit)


async def _link_reply_message_to_dispatch_run(
    db,
    *,
    from_agent: str,
    resolved_in_reply_to: str,
    reply_message_id: str,
    reply_type: str,
    reply_body: str,
) -> bool:
    # A linked request may answer the current contract while asking a follow-up. Keep
    # non-answer info messages open; their completion semantics remain content-aware.
    if str(reply_type or "").strip().lower() != "request" and not _message_satisfies_reply_contract(
        reply_type,
        body=reply_body,
    ):
        return False
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ? AND message_id = ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, resolved_in_reply_to),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        if not _is_replaceable_auto_handoff_message(existing_message, replied_run):
            return False

    current_status = str(replied_run["status"] or "").strip().lower()
    await _mark_dispatch_run_answered(
        db,
        replied_run["id"],
        reply_message_id,
        current_status,
        str(replied_run["execution_mode"] or ""),
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at)
        SELECT id, to_agent, ?
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND in_reply_to = ?
          AND dispatch_requested = 0
          AND body LIKE 'Auto-mirrored dispatch %'
        """,
        (_now(), from_agent, replied_run["from_agent"], replied_run["message_id"]),
    )
    handoff_note = (
        f"Result reply linked after run completion from {from_agent}"
        if current_status in _borrowed_dispatch_terminal_statuses()
        else f"Result reply recorded from {from_agent}"
    )
    await _append_dispatch_event(db, replied_run["id"], "handoff", handoff_note)
    return True


def _message_type_expects_reply(message_type: str) -> bool:
    return (message_type or "").strip().lower() in {"request", "review", "error"}


def _primary_result_message_id(message_id: str, recipients: list[str]) -> str:
    if len(recipients) == 1:
        return message_id
    if not recipients:
        return message_id
    return f"{message_id}-{recipients[0]}"


async def _record_terminal_delivery_contract(
    db,
    *,
    source_message_id: str,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    in_reply_to: Optional[str],
    require_reply: bool,
    terminal_id: str,
    control_id: str,
    runtime: str = "",
) -> str:
    run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    requested_at = _now()
    normalized_runtime = _normalize_runtime(runtime or "")
    existing_active_turn = None
    if normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}:
        active_cursor = await db.execute(
            """
            SELECT id
            FROM dispatch_runs
            WHERE target_agent = ?
              AND dispatch_mode = 'terminal'
              AND execution_mode = 'managed'
              AND runtime = ?
              AND status IN ('claimed', 'running')
            ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
            LIMIT 1
            """,
            (recipient_id, normalized_runtime),
        )
        existing_active_turn = await active_cursor.fetchone()
    if existing_active_turn:
        parent_run_id = str(existing_active_turn["id"] or "").strip()
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_delivered",
            f"Additional dashboard input delivered into terminal {terminal_id} with control {control_id}",
        )
        await _append_dispatch_event(
            db,
            parent_run_id,
            "terminal_coalesced",
            f"Coalesced message {source_message_id or 'unknown'} into active terminal-backed turn",
        )
        if source_message_id:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (source_message_id, recipient_id, requested_at),
            )
        await _invalidate_agent_live_state(db, recipient_id)
        return parent_run_id

    tracks_active_turn = normalized_runtime in {"claude-code", "codex", "hermes", "opencode", "pi"}
    status = "running" if tracks_active_turn else "delivered"
    await db.execute(
        """
        INSERT INTO dispatch_runs (
            id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime, runtime,
            message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at, started_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            run_id,
            source_message_id or None,
            from_agent,
            recipient_id,
            "terminal",
            "managed",
            "",
            normalized_runtime,
            message_type,
            subject,
            body,
            priority,
            in_reply_to,
            status,
            1 if require_reply else 0,
            requested_at,
            requested_at if tracks_active_turn else None,
        ),
    )
    await _append_dispatch_event(
        db,
        run_id,
        "terminal_delivered",
        f"Delivered into terminal {terminal_id} with control {control_id}",
    )
    if tracks_active_turn:
        await _append_dispatch_event(
            db,
            run_id,
            "running",
            "Awaiting explicit reply from terminal-backed turn",
        )
    if source_message_id:
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (source_message_id, recipient_id, requested_at),
        )
    await _invalidate_agent_live_state(db, recipient_id)
    return run_id


async def _resolve_recipient_ids(db, *, to: Optional[str], to_role: Optional[str], from_agent: str) -> list[str]:
    recipients: list[str] = []
    if to:
        recipients.append(to)
    if to_role:
        cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (to_role, from_agent))
        recipients.extend([row["id"] for row in await cursor.fetchall()])
    return _dedupe_preserve(recipients)


async def _resolve_reply_parent_message_id(db, reply_id: Optional[str]) -> tuple[Optional[str], bool]:
    candidate = str(reply_id or "").strip()
    if not candidate:
        return None, True

    cursor = await db.execute("SELECT id FROM messages WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    if row:
        return candidate, True

    cursor = await db.execute("SELECT message_id FROM dispatch_runs WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    resolved = str((row["message_id"] if row else "") or "").strip()
    if resolved:
        return resolved, True

    return None, False
