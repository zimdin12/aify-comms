"""
aify-comms v2 API — SQLite backend.
Drop-in replacement for api.py with identical endpoint signatures.
"""
import asyncio
import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse

# Per-agent wake-up events for comms_listen
_listen_events: dict[str, asyncio.Event] = {}

from service.db import get_db
from service.models import (
    AgentRegister, AgentStatusUpdate, AgentDescribeRequest, MessageSend, ClearRequest,
    ChannelCreate, ChannelMessage, ChannelJoin,
    AgentRuntimeStateUpdate, ConversationClearRequest, DispatchRequest, DispatchClaimRequest, DispatchRunUpdate,
    DispatchControlRequest, DispatchControlClaimRequest, DispatchControlUpdate,
    EnvironmentHeartbeat, EnvironmentControlRequest, EnvironmentControlClaim, EnvironmentControlUpdate, EnvironmentRootsUpdate,
    AgentEnvironmentAssignRequest, AgentRenameRequest, SpawnRequestCreate, SpawnRequestClaim, SpawnRequestUpdate, SessionControlRequest, AgentControlRequest,
)

SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')
_WINDOWS_DRIVE_CWD_RE = re.compile(r"^[a-zA-Z]:/")
_WSL_DRIVE_CWD_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")

def validate_name(name: str, label: str = "name") -> None:
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")

router = APIRouter(tags=["api"])

def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _iso_to_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0

def _iso_from_ms(timestamp_ms: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(max(0, int(timestamp_ms or 0)) / 1000))

def _shared_dir(request: Request) -> Path:
    try:
        d = Path(request.app.state.config.data_dir) / "shared_files"
    except Exception:
        d = Path("/data/shared_files")
    d.mkdir(parents=True, exist_ok=True)
    return d

_MANUAL_STATUSES = {"stopped"}

DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "stale_agent_hours": 24,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
    "idle_minutes": 5,
    "offline_minutes": 30,
    "environment_offline_seconds": 90,
}

_RUNTIME_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "claude_code": "claude-code",
    "codex": "codex",
    "opencode": "opencode",
    "generic": "generic",
}
_LAUNCHABLE_RUNTIMES = {"claude-code", "codex", "opencode"}
_SESSION_MODES = {"resident", "managed"}
_DISPATCH_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
_DISPATCH_ACTIVE_STATUSES = {"queued", "claimed", "running"}
_SPAWN_TERMINAL_STATUSES = {"running", "failed", "cancelled"}
_SPAWN_MODES = {"managed-warm"}
ACTIVE_RUN_BRIDGE_STALE_SECONDS = 120

async def _get_ws(request: Request):
    try:
        return request.app.state.ws_manager
    except Exception:
        return None

async def _touch_agent(db, agent_id: str):
    await db.execute(
        "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
        (_now(), agent_id)
    )


def _json_loads_or(value: Any, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _timestamp_sort_key(value: Any) -> str:
    try:
        raw = str(value or "").strip()
        if not raw:
            return ""
        from datetime import datetime, timezone
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
    except Exception:
        return str(value or "")


def _bridge_started_at(metadata: Any) -> str:
    if isinstance(metadata, dict):
        return _timestamp_sort_key(metadata.get("bridgeStartedAt"))
    return ""


def _normalize_session_mode(mode: Any) -> str:
    value = str(mode or "resident").strip().lower()
    return value if value in _SESSION_MODES else "resident"


def _normalize_runtime(runtime: Any) -> str:
    key = str(runtime or "generic").strip().lower()
    return _RUNTIME_ALIASES.get(key, key or "generic")


def _runtime_handle_from_state(runtime: Any, runtime_state: Any) -> str:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    normalized = _normalize_runtime(runtime)
    if normalized == "codex":
        return str(state.get("threadId") or state.get("sessionId") or "").strip()
    return str(state.get("sessionId") or state.get("threadId") or "").strip()


def _runtime_state_with_handle(runtime: Any, runtime_state: Any, session_handle: str) -> dict[str, Any]:
    state = runtime_state if isinstance(runtime_state, dict) else _json_loads_or(runtime_state, {})
    result = dict(state or {})
    handle = str(session_handle or "").strip()
    if not handle:
        return result
    if _normalize_runtime(runtime) == "codex":
        result["threadId"] = handle
    else:
        result["sessionId"] = handle
    return result


def _machine_family(machine_id: Any) -> str:
    return str(machine_id or "").strip().split(":", 1)[0].lower()


def _dedupe_preserve(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _dispatch_requires_reply(explicit: Optional[bool], *, default: bool) -> bool:
    if explicit is None:
        return bool(default)
    return bool(explicit)


def _message_type_expects_reply(message_type: str) -> bool:
    return (message_type or "").strip().lower() in {"request", "review", "error"}


def _row_require_reply(row) -> bool:
    return bool(int((row["require_reply"] if row and "require_reply" in row.keys() else 0) or 0))


def _is_delivery_only_claude_run(row) -> bool:
    if not row:
        return False
    return (
        str((row["runtime"] if "runtime" in row.keys() else "") or "").strip() == "claude-code"
        and str((row["status"] if "status" in row.keys() else "") or "").strip().lower() == "completed"
        and str((row["summary"] if "summary" in row.keys() else "") or "").strip() == "Delivered to Claude resident session"
    )


def _dispatch_reply_state(row) -> str:
    if not _row_require_reply(row):
        return "not_required"
    if str((row["result_message_id"] if row else "") or "").strip():
        return "sent"
    if _is_delivery_only_claude_run(row):
        return "awaiting"
    status = str((row["status"] if row else "") or "").strip().lower()
    if status in _DISPATCH_TERMINAL_STATUSES:
        return "pending"
    return "awaiting"


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


def _serialize_dispatch_run_row(row, *, blocked_by=None, include_body: bool = False, include_events=None, include_controls=None) -> dict[str, Any]:
    body_text = str((row["body"] if row and "body" in row.keys() else "") or "")
    merged_from_agents = []
    if body_text.startswith(_MERGED_DISPATCH_HEADER):
        merged_from_agents = _dedupe_preserve(
            match.group(1).strip()
            for match in re.finditer(r"^From:\s*(.+)$", body_text, flags=re.MULTILINE)
            if match.group(1).strip()
        )
    payload = {
        "id": row["id"],
        "messageId": row["message_id"],
        "from": row["from_agent"],
        "originalFrom": row["from_agent"],
        "targetAgentId": row["target_agent"],
        "status": row["status"],
        "mode": row["dispatch_mode"],
        "executionMode": row["execution_mode"] or "managed",
        "runtime": row["runtime"] or "",
        "claimBridgeId": row["claim_bridge_id"] or "",
        "requestedRuntime": row["requested_runtime"] or "",
        "subject": row["subject"],
        "summary": row["summary"] or "",
        "error": row["error_text"] or "",
        "resultMessageId": row["result_message_id"] or "",
        "requireReply": _row_require_reply(row),
        "replyState": _dispatch_reply_state(row),
        "replyPending": _dispatch_reply_pending(row),
        "requestedAt": row["requested_at"],
        "claimedAt": row["claimed_at"],
        "startedAt": row["started_at"],
        "finishedAt": row["finished_at"],
        "blockedByActiveRun": blocked_by,
    }
    if len(merged_from_agents) > 1:
        payload["from"] = "multiple"
        payload["mergedFromAgents"] = merged_from_agents
        payload["mergedDispatchCount"] = _pending_dispatch_count(body_text)
    if include_body:
        payload.update(
            {
                "type": row["message_type"],
                "body": row["body"],
                "priority": row["priority"],
                "inReplyTo": row["in_reply_to"],
                "externalThreadId": row["external_thread_id"] or "",
                "externalTurnId": row["external_turn_id"] or "",
            }
        )
    if include_events is not None:
        payload["events"] = include_events
    if include_controls is not None:
        payload["controls"] = include_controls
    return payload


def _has_codex_live_app_server(runtime_config: Optional[dict[str, Any]] = None) -> bool:
    if not isinstance(runtime_config, dict):
        return False
    return str(runtime_config.get("appServerUrl") or "").strip().lower().startswith(("ws://", "wss://"))


def _normalize_channel_history_where(channel_name: str) -> tuple[str, tuple[Any, ...]]:
    return "channel = ? AND to_agent IS NULL", (channel_name,)


def _channel_fanout_message_id(canonical_message_id: str, agent_id: str) -> str:
    return f"{canonical_message_id}-{agent_id}"


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
    if family in {"linux", "darwin"} and _WINDOWS_DRIVE_CWD_RE.match(resolved_cwd):
        hint = '/mnt/<drive>/...' if family == "linux" else "/Users/..."
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


async def _select_message_ids(db, where_clause: str, params: tuple[Any, ...] = ()) -> list[str]:
    cursor = await db.execute(f"SELECT id FROM messages WHERE {where_clause}", params)
    return [str(row["id"]) for row in await cursor.fetchall() if str(row["id"] or "").strip()]


async def _delete_messages_by_ids(db, message_ids: list[str], *, chunk_size: int = 250) -> int:
    pending = _dedupe_preserve([str(message_id or "").strip() for message_id in message_ids if str(message_id or "").strip()])
    if not pending:
        return 0

    deleted = 0
    for start in range(0, len(pending), chunk_size):
        chunk = pending[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        await db.execute(f"UPDATE messages SET in_reply_to = NULL WHERE in_reply_to IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_runs SET message_id = NULL WHERE message_id IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_runs SET in_reply_to = NULL WHERE in_reply_to IN ({placeholders})", chunk)
        await db.execute(f"UPDATE dispatch_controls SET source_message_id = '' WHERE source_message_id IN ({placeholders})", chunk)
        await db.execute(f"DELETE FROM read_receipts WHERE message_id IN ({placeholders})", chunk)
        cursor = await db.execute(f"DELETE FROM messages WHERE id IN ({placeholders})", chunk)
        deleted += cursor.rowcount or 0
    return deleted


async def _delete_messages_where(db, where_clause: str, params: tuple[Any, ...] = ()) -> int:
    message_ids = await _select_message_ids(db, where_clause, params)
    return await _delete_messages_by_ids(db, message_ids)


async def _agent_tombstone(db, agent_id: str):
    cursor = await db.execute("SELECT * FROM agent_tombstones WHERE agent_id = ?", (agent_id,))
    return await cursor.fetchone()


async def _tombstone_agent(
    db,
    agent_id: str,
    *,
    removed_by: str = "",
    bridge_id: str = "",
    reason: str = "",
    removed_at: Optional[str] = None,
):
    await db.execute(
        """
        INSERT OR REPLACE INTO agent_tombstones (
            agent_id, removed_at, removed_by, bridge_id, reason
        ) VALUES (?,?,?,?,?)
        """,
        (agent_id, removed_at or _now(), removed_by, bridge_id, reason),
    )


async def _remove_agent_record(
    db,
    agent_id: str,
    *,
    removed_by: str = "",
    reason: str = "",
) -> int:
    cursor = await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (agent_id,))
    row = await cursor.fetchone()
    runtime_state = _json_loads_or(row["runtime_state"], {}) if row else {}
    bridge_id = str(runtime_state.get("bridgeInstanceId") or "").strip()
    await _cancel_nonterminal_runs_for_agents(
        db,
        [agent_id],
        summary=f'Agent "{agent_id}" was removed before the run could finish.',
        event_type="agent_removed",
    )
    await _tombstone_agent(db, agent_id, removed_by=removed_by, bridge_id=bridge_id, reason=reason)
    await db.execute("DELETE FROM bridge_instances WHERE agent_id = ?", (agent_id,))
    cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
    return cursor.rowcount or 0


def _default_capabilities_for(
    runtime: str,
    session_mode: str,
    session_handle: str = "",
    runtime_config: Optional[dict[str, Any]] = None,
) -> list[str]:
    normalized_runtime = _normalize_runtime(runtime)
    normalized_session_mode = _normalize_session_mode(session_mode)
    session_handle = str(session_handle or "").strip()
    if normalized_session_mode == "managed":
        if normalized_runtime == "codex":
            return ["managed-run", "resume", "interrupt", "steer", "spawn"]
        if normalized_runtime == "opencode":
            return ["managed-run", "resume", "interrupt", "spawn"]
        if normalized_runtime == "claude-code":
            return ["managed-run", "resume", "interrupt", "spawn"]
        return []
    if normalized_runtime == "codex":
        if not session_handle:
            return []
        return ["resident-run", "resume", "interrupt", "steer"]
    if normalized_runtime == "opencode":
        if not session_handle:
            return []
        return ["resident-run", "resume", "interrupt"]
    if normalized_runtime == "claude-code":
        if isinstance(runtime_config, dict) and runtime_config.get("channelEnabled") is True:
            return ["resident-run", "interrupt", "steer"]
        return []
    return []


async def _resolve_recipient_ids(db, *, to: Optional[str], to_role: Optional[str], from_agent: str) -> list[str]:
    recipients: list[str] = []
    if to:
        recipients.append(to)
    if to_role:
        cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (to_role, from_agent))
        recipients.extend([row["id"] for row in await cursor.fetchall()])
    return _dedupe_preserve(recipients)


def _row_capabilities(row) -> list[str]:
    capabilities = _json_loads_or(row["capabilities"], [])
    if not row:
        return capabilities
    runtime = _normalize_runtime((row["runtime"] if "runtime" in row.keys() else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if "session_mode" in row.keys() else "") or "resident")
    runtime_config = _json_loads_or(row["runtime_config"], {}) if "runtime_config" in row.keys() else {}
    if runtime == "claude-code" and session_mode == "resident":
        channel_enabled = isinstance(runtime_config, dict) and runtime_config.get("channelEnabled") is True
        if not channel_enabled:
            return [cap for cap in capabilities if cap not in {"resident-run", "interrupt", "steer"}]
        for cap in ("resident-run", "interrupt", "steer"):
            if cap not in capabilities:
                capabilities = [*capabilities, cap]
    return capabilities


def _row_status_note(row) -> str:
    if not row or "status_note" not in row.keys():
        return ""
    return str(row["status_note"] or "").strip()


def _agent_wake_mode(row) -> str:
    runtime = _normalize_runtime((row["runtime"] if row else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if row else "") or "resident")
    session_handle = str((row["session_handle"] if row else "") or "").strip()
    capabilities = _row_capabilities(row) if row else []
    runtime_config = _json_loads_or(row["runtime_config"], {}) if row else {}

    if (row["launch_mode"] or "detached") == "none":
        return "disabled"
    if session_mode == "managed" and "managed-run" in capabilities:
        return "managed-worker"
    if session_mode == "resident" and runtime == "claude-code" and "resident-run" in capabilities:
        return "claude-live"
    if session_mode == "resident" and runtime == "codex" and "resident-run" in capabilities and session_handle and _has_codex_live_app_server(runtime_config):
        return "codex-live"
    if session_mode == "resident" and runtime == "codex" and "resident-run" in capabilities and session_handle:
        return "codex-thread-resume"
    if session_mode == "resident" and runtime == "opencode" and "resident-run" in capabilities and session_handle:
        return "opencode-session-resume"
    if session_mode == "resident" and runtime == "codex" and not session_handle:
        return "codex-missing-handle"
    if session_mode == "resident" and runtime == "opencode" and not session_handle:
        return "opencode-missing-handle"
    if session_mode == "resident" and runtime == "claude-code":
        return "claude-needs-channel"
    return "message-only"


def _agent_execution_mode(row, requested_runtime: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    runtime = _normalize_runtime(row["runtime"] or "generic")
    session_mode = _normalize_session_mode(row["session_mode"] or "resident")
    session_handle = str(row["session_handle"] or "").strip()
    if requested_runtime and _normalize_runtime(requested_runtime) != runtime:
        return None, f'requested runtime "{requested_runtime}" does not match registered runtime "{runtime}"'
    if runtime not in _LAUNCHABLE_RUNTIMES:
        return None, f'runtime "{runtime}" does not support active dispatch'
    capabilities = _row_capabilities(row)
    if session_mode == "managed":
        if (row["launch_mode"] or "detached") == "none":
            return None, "launch mode is disabled"
        if capabilities and "managed-run" not in capabilities:
            return None, 'agent capabilities do not include "managed-run"'
        return "managed", None
    if "resident-run" not in capabilities:
        return None, 'agent capabilities do not include "resident-run"'
    if runtime == "codex" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident Codex session without a bound session handle. '
            "Re-register that live session or provide sessionHandle explicitly."
        )
    if runtime == "opencode" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident OpenCode session without a bound session handle. '
            "Re-register that live session with sessionHandle explicitly or create an environment-managed session."
        )
    if (row["launch_mode"] or "detached") == "none":
        return None, "launch mode is disabled"
    return "resident", None


def _dispatch_fix_hint(recipient_id: str, row, reason: str) -> dict[str, Any]:
    runtime = _normalize_runtime((row["runtime"] if row else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if row else "") or "resident")
    role = (row["role"] if row else "") or "coder"
    capabilities = _row_capabilities(row) if row else []
    session_handle = str((row["session_handle"] if row else "") or "").strip()

    hint: dict[str, Any] = {
        "targetAgentId": recipient_id,
        "reason": reason,
        "runtime": runtime,
        "sessionMode": session_mode,
        "capabilities": capabilities,
    }

    if row is None:
        hint["fix"] = "Register the target agent first, then try triggering again."
        return hint

    if runtime == "codex" and session_mode == "resident" and not session_handle:
        hint["fix"] = "Restart Codex, then re-register from the exact live Codex session you want to wake."
        hint["suggestedCommands"] = [
            f'comms_register(agentId="{recipient_id}", role="{role}", runtime="codex")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime == "claude-code" and session_mode == "resident" and "resident-run" not in capabilities:
        hint["fix"] = "Start Claude with claude-aify, then re-register from that exact live Claude session."
        hint["suggestedCommands"] = [
            "claude-aify",
            f'comms_register(agentId="{recipient_id}", role="{role}", runtime="claude-code")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime == "opencode" and session_mode == "resident" and not session_handle:
        hint["fix"] = (
            "Re-register the live OpenCode session with runtime=\"opencode\" and a real sessionHandle, "
            "or spawn a persistent agent from a connected dashboard environment."
        )
        hint["suggestedCommands"] = [
            f'comms_register(agentId="{recipient_id}", role="{role}", runtime="opencode", sessionHandle="<session-id>")',
            f'comms_envs()',
            f'comms_spawn(from="<your-agent>", agentId="{recipient_id}-teammate", role="{role}", runtime="opencode")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime not in _LAUNCHABLE_RUNTIMES:
        hint["fix"] = "This target is message-only right now. Check comms_agent_info before suggesting any runtime-specific reinstall or restart steps."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    if session_mode == "managed" and (row["launch_mode"] or "detached") == "none":
        hint["fix"] = "Enable launch mode or recreate this agent as an environment-managed session."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    hint["fix"] = "Inspect the target runtime/session with comms_agent_info, then retry with runtime-specific steps."
    hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
    return hint


def _format_dispatch_state(active_row, queued_count: int) -> dict[str, Any]:
    active = None
    if active_row:
        active = {
            "runId": active_row["id"],
            "status": active_row["status"],
            "subject": active_row["subject"],
            "from": active_row["from_agent"],
            "executionMode": active_row["execution_mode"] or "managed",
            "runtime": active_row["runtime"] or "",
            "claimBridgeId": active_row["claim_bridge_id"] or "",
            "requestedAt": active_row["requested_at"] or "",
            "startedAt": active_row["started_at"] or active_row["claimed_at"] or "",
        }
    return {
        "hasActiveRun": bool(active),
        "activeRun": active,
        "queuedRuns": max(int(queued_count or 0), 0),
    }


async def _get_dispatch_state_for_agent(db, agent_id: str) -> dict[str, Any]:
    active_cursor = await db.execute(
        """
        SELECT id, from_agent, subject, status, execution_mode, runtime, requested_at, claimed_at, started_at
             , claim_bridge_id
        FROM dispatch_runs
        WHERE target_agent = ? AND status IN ('claimed', 'running')
        ORDER BY COALESCE(started_at, claimed_at, requested_at) ASC
        LIMIT 1
        """,
        (agent_id,)
    )
    active_row = await active_cursor.fetchone()
    queued_cursor = await db.execute(
        "SELECT COUNT(*) FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
        (agent_id,)
    )
    queued_count = (await queued_cursor.fetchone())[0]
    return _format_dispatch_state(active_row, queued_count)


async def _get_blocking_active_run(db, agent_id: str, exclude_run_id: str = "") -> Optional[dict[str, Any]]:
    state = await _get_dispatch_state_for_agent(db, agent_id)
    active = state.get("activeRun")
    if not active:
        return None
    if exclude_run_id and active.get("runId") == exclude_run_id:
        return None
    return active


async def _bridge_is_superseded(db, bridge_id: str, agent_id: str) -> bool:
    if not bridge_id:
        return False
    cursor = await db.execute(
        "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if not row:
        return False
    return bool((row["superseded_by"] or "").strip())


async def _bridge_claim_block_reason(db, *, bridge_id: str, agent_id: str, agent_row) -> Optional[dict[str, Any]]:
    """Return a blockedBy payload when an old stdio bridge should not claim work."""
    if not bridge_id:
        return None

    cursor = await db.execute(
        "SELECT superseded_by FROM bridge_instances WHERE id = ? AND agent_id = ?",
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
    if runtime not in {"codex", "opencode"}:
        return None

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
    if (session_mode != "managed" or not managed_environment_id) and current_bridge_id and current_bridge_id != bridge_id:
        return {
            "reason": "bridge_not_current",
            "bridgeId": bridge_id,
            "currentBridgeId": current_bridge_id,
            "agentId": agent_id,
            "hint": "This bridge is not the current stdio bridge for the agent. Restart or shut down stale codex-aify/opencode-aify processes.",
        }

    if session_mode == "managed":
        environment_id = managed_environment_id
        if environment_id:
            env_cursor = await db.execute("SELECT bridge_id, status FROM environments WHERE id = ?", (environment_id,))
            env_row = await env_cursor.fetchone()
            current_environment_bridge = str((env_row["bridge_id"] if env_row else "") or "").strip()
            env_status = str((env_row["status"] if env_row else "") or "").strip().lower()
            if current_environment_bridge and current_environment_bridge != bridge_id:
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


async def _bridge_registered_at(db, bridge_id: str, agent_id: str) -> str:
    if not bridge_id:
        return ""
    cursor = await db.execute(
        "SELECT registered_at FROM bridge_instances WHERE id = ? AND agent_id = ?",
        (bridge_id, agent_id)
    )
    row = await cursor.fetchone()
    if not row:
        return ""
    return row["registered_at"] or ""


async def _fail_active_runs_for_superseded_bridges(
    db,
    *,
    agent_id: str,
    machine_id: str,
    superseding_bridge_id: str,
    finished_at: str,
) -> list[str]:
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


async def _fail_pending_controls_for_run(
    db,
    run_id: str,
    *,
    handled_at: str,
    response_text: str,
):
    cursor = await db.execute(
        """
        SELECT id, action
        FROM dispatch_controls
        WHERE run_id = ? AND status IN ('pending', 'claimed')
        ORDER BY requested_at ASC, id ASC
        """,
        (run_id,),
    )
    controls = await cursor.fetchall()
    if not controls:
        return

    for control in controls:
        await db.execute(
            """
            UPDATE dispatch_controls
            SET status = 'failed', response_text = ?, handled_at = ?
            WHERE id = ?
            """,
            (response_text, handled_at, control["id"]),
        )
        await _append_dispatch_event(
            db,
            run_id,
            f"control:{control['action']}:failed",
            response_text,
        )


def _status_with_dispatch(status: str, dispatch_state: Optional[dict[str, Any]]) -> str:
    if not dispatch_state:
        return status
    if dispatch_state.get("hasActiveRun") and status not in _MANUAL_STATUSES and status != "stale":
        return "working"
    return status


def _agent_record_to_dict(row, status: str, unread: int, dispatch_state: Optional[dict[str, Any]] = None):
    runtime = _normalize_runtime(row["runtime"] or "generic")
    session_mode = _normalize_session_mode(row["session_mode"] or "resident")
    status_note = _row_status_note(row)
    effective_status = _status_with_dispatch(status, dispatch_state)
    display_status = effective_status
    if status_note and effective_status == status:
        display_status = f"{effective_status}: {status_note}"
    return {
        "role": row["role"],
        "name": row["name"],
        "cwd": row["cwd"],
        "model": row["model"],
        "description": (row["description"] if "description" in row.keys() else "") or "",
        "instructions": row["instructions"],
        "status": display_status,
        "statusRaw": effective_status,
        "statusNote": status_note,
        "registeredAt": row["registered_at"],
        "lastSeen": row["last_seen"],
        "unread": unread,
        "runtime": runtime,
        "machineId": row["machine_id"] or "",
        "launchMode": row["launch_mode"] or "detached",
        "sessionMode": session_mode,
        "wakeMode": _agent_wake_mode(row),
        "sessionHandle": row["session_handle"] or "",
        "managedBy": row["managed_by"] or "",
        "capabilities": _row_capabilities(row),
        "runtimeConfig": _json_loads_or(row["runtime_config"], {}),
        "runtimeState": _json_loads_or(row["runtime_state"], {}),
        "dispatchState": dispatch_state or {"hasActiveRun": False, "activeRun": None, "queuedRuns": 0},
    }


def _environment_effective_status(row, *, offline_seconds: int = 90) -> str:
    status = str(row["status"] or "online")
    if status == "online":
        try:
            from datetime import datetime, timezone, timedelta
            last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last > timedelta(seconds=max(15, int(offline_seconds or 90))):
                status = "offline"
        except Exception:
            pass
    return status


def _environment_record_to_dict(row, *, offline_seconds: int = 90) -> dict[str, Any]:
    status = _environment_effective_status(row, offline_seconds=offline_seconds)
    runtimes = _json_loads_or(row["runtimes"], [])
    normalized_runtimes = []
    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue
        normalized_runtimes.append({**runtime, "modes": ["managed-warm"]})
    return {
        "id": row["id"],
        "label": row["label"] or row["id"],
        "machineId": row["machine_id"] or "",
        "os": row["os"] or "",
        "kind": row["kind"] or "",
        "bridgeId": row["bridge_id"] or "",
        "bridgeVersion": (row["bridge_version"] if "bridge_version" in row.keys() else "") or "",
        "cwdRoots": _json_loads_or(row["cwd_roots"], []),
        "runtimes": normalized_runtimes,
        "status": status,
        "metadata": _json_loads_or(row["metadata"], {}),
        "registeredAt": row["registered_at"] or "",
        "lastSeen": row["last_seen"] or "",
    }


async def _repair_spawn_requests_from_initial_dispatch_failures(db) -> int:
    cursor = await db.execute(
        """
        SELECT *
        FROM spawn_requests
        WHERE status = 'running'
          AND COALESCE(initial_message, '') != ''
          AND COALESCE(error, '') = ''
        """
    )
    repaired = 0
    for spawn in await cursor.fetchall():
        started_at = spawn["started_at"] or spawn["updated_at"] or spawn["created_at"]
        run_cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE target_agent = ?
              AND requested_at >= ?
            ORDER BY requested_at ASC
            LIMIT 1
            """,
            (spawn["agent_id"], started_at),
        )
        run = await run_cursor.fetchone()
        if not run or str(run["status"] or "").lower() not in {"failed", "cancelled"}:
            continue
        error = (run["error_text"] or run["summary"] or f"Initial dispatch {run['status']}").strip()
        now = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'failed',
                error = ?,
                finished_at = COALESCE(finished_at, ?),
                updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (f"Initial brief failed: {error}", run["finished_at"] or now, now, spawn["id"]),
        )
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'failed',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE spawn_request_id = ?
              AND status IN ('starting', 'running')
            """,
            (run["finished_at"] or now, now, spawn["id"]),
        )
        repaired += 1
    if repaired:
        await db.commit()
    return repaired


async def _repair_superseded_recovering_sessions(db) -> int:
    now = _now()
    cursor = await db.execute(
        """
        SELECT old.id
        FROM agent_sessions old
        WHERE old.status IN ('starting', 'recovering', 'restarting')
          AND EXISTS (
            SELECT 1
            FROM agent_sessions current
            WHERE current.agent_id = old.agent_id
              AND current.id != old.id
              AND current.status = 'running'
              AND COALESCE(NULLIF(current.last_seen, ''), NULLIF(current.started_at, ''), '') >=
                  COALESCE(NULLIF(old.last_seen, ''), NULLIF(old.started_at, ''), '')
          )
        """
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    for row in rows:
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'ended',
                ended_at = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?),
                last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
            WHERE id = ?
              AND status IN ('starting', 'recovering', 'restarting')
            """,
            (now, now, row["id"]),
        )
    await db.commit()
    return len(rows)


async def _repair_current_session_freshness(db) -> int:
    cursor = await db.execute(
        """
        SELECT id, last_seen, runtime_state
        FROM agents
        WHERE session_mode = 'managed'
          AND runtime_state IS NOT NULL
          AND runtime_state != ''
          AND runtime_state != '{}'
        """
    )
    repaired = 0
    for row in await cursor.fetchall():
        runtime_state = _json_loads_or(row["runtime_state"], {})
        if not (runtime_state.get("spawnRequestId") or runtime_state.get("environmentId")):
            continue
        before = db.total_changes
        await _touch_current_agent_session(db, row["id"], runtime_state, row["last_seen"] or _now())
        if db.total_changes > before:
            repaired += 1
    if repaired:
        await db.commit()
    return repaired


def _runtime_capability_for_environment(environment: dict[str, Any], runtime: str) -> Optional[dict[str, Any]]:
    normalized = _normalize_runtime(runtime)
    for item in environment.get("runtimes") or []:
        if _normalize_runtime(item.get("runtime") or "") == normalized:
            return item
    return None


def _workspace_root_for(environment: dict[str, Any], workspace: str) -> str:
    workspace_value = str(workspace or "").strip()
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    if not workspace_value or not roots:
        return roots[0] if roots else ""
    normalized_workspace = workspace_value.replace("\\", "/").rstrip("/")
    for root in roots:
        normalized_root = root.replace("\\", "/").rstrip("/")
        if normalized_workspace == normalized_root or normalized_workspace.startswith(normalized_root + "/"):
            return root
    raise HTTPException(400, f'Workspace "{workspace_value}" is outside the roots advertised by environment "{environment.get("id")}"')


def _workspace_for_environment(environment: dict[str, Any], requested_workspace: Optional[str], fallback_workspace: Optional[str] = "") -> tuple[str, str]:
    roots = [str(root or "").strip() for root in (environment.get("cwdRoots") or []) if str(root or "").strip()]
    workspace = str(requested_workspace or fallback_workspace or "").strip()
    if not workspace:
        workspace = roots[0] if roots else ""
    try:
        workspace_root = _workspace_root_for(environment, workspace)
    except HTTPException:
        if requested_workspace:
            raise
        workspace = roots[0] if roots else ""
        workspace_root = _workspace_root_for(environment, workspace)
    if not workspace and workspace_root:
        workspace = workspace_root
    return workspace, workspace_root


def _normalize_roots(roots: Optional[list[str]]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for root in roots or []:
        value = str(root or "").strip()
        if not value:
            continue
        key = value.replace("\\", "/").rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _spawn_spec_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "model": row["model"] or "",
        "profile": row["profile"] or "",
        "mode": row["mode"] or "managed-warm",
        "systemPrompt": row["system_prompt"] or "",
        "instructions": row["standing_instructions"] or "",
        "envVars": _json_loads_or(row["env_vars"], {}),
        "channelIds": _json_loads_or(row["channel_ids"], []),
        "budgetPolicy": _json_loads_or(row["budget_policy"], {}),
        "contextPolicy": _json_loads_or(row["context_policy"], {}),
        "restartPolicy": _json_loads_or(row["restart_policy"], {}),
        "metadata": _json_loads_or(row["metadata"], {}),
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
    }


def _spawn_request_to_dict(row, spec: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "spawnSpecId": row["spawn_spec_id"],
        "createdBy": row["created_by"] or "",
        "environmentId": row["environment_id"],
        "agentId": row["agent_id"],
        "role": row["role"] or "coder",
        "name": row["name"] or "",
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "workspaceRoot": row["workspace_root"] or "",
        "initialMessage": row["initial_message"] or "",
        "priority": row["priority"] or "normal",
        "subject": row["subject"] or "",
        "mode": row["mode"] or "managed-warm",
        "resumePolicy": row["resume_policy"] or "native_first",
        "status": row["status"] or "queued",
        "claimedByBridgeId": row["claimed_by_bridge_id"] or "",
        "claimMachineId": row["claim_machine_id"] or "",
        "processId": row["process_id"] or "",
        "sessionHandle": row["session_handle"] or "",
        "sessionId": row["session_id"] or "",
        "error": row["error"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "claimedAt": row["claimed_at"] or "",
        "startedAt": row["started_at"] or "",
        "finishedAt": row["finished_at"] or "",
    }
    if spec is not None:
        payload["spawnSpec"] = spec
    return payload


def _agent_session_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "mode": row["mode"] or "managed-warm",
        "processId": row["process_id"] or "",
        "sessionHandle": row["session_handle"] or "",
        "appServerUrl": row["app_server_url"] or "",
        "spawnSpecId": row["spawn_spec_id"] or "",
        "spawnRequestId": row["spawn_request_id"] or "",
        "capabilities": _json_loads_or(row["capabilities"], {}),
        "telemetry": _json_loads_or(row["telemetry"], {}),
        "status": row["status"] or "",
        "startedAt": row["started_at"] or "",
        "lastSeen": row["last_seen"] or "",
        "endedAt": row["ended_at"] or "",
    }


async def _compute_agent_status(row, idle_minutes: int, offline_minutes: int):
    status = row["status"]
    if status not in _MANUAL_STATUSES and status != "stale":
        try:
            from datetime import datetime, timezone, timedelta
            last = datetime.fromisoformat(row["last_seen"].replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - last
            if age > timedelta(minutes=offline_minutes):
                status = "offline"
            elif age > timedelta(minutes=idle_minutes):
                status = "idle"
        except Exception:
            pass
    return status


async def _load_settings(db):
    settings = {**DEFAULT_SETTINGS}
    sc = await db.execute("SELECT key, value FROM settings")
    for row in await sc.fetchall():
        try:
            settings[row["key"]] = json.loads(row["value"])
        except Exception:
            pass
    return settings


async def _get_recipient_info(db, recipient_id: str):
    c = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
    row = await c.fetchone()
    if not row:
        return None
    settings = await _load_settings(db)
    status = await _compute_agent_status(row, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30))
    uc = await db.execute(
        "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts rr ON m.id = rr.message_id AND rr.agent_id = ? WHERE m.to_agent = ? AND rr.message_id IS NULL",
        (recipient_id, recipient_id)
    )
    unread = (await uc.fetchone())[0]
    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
    return _agent_record_to_dict(row, status, unread, dispatch_state)


async def _preflight_live_send_recipients(
    db,
    recipients: list[str],
    *,
    allow_steer: bool = False,
    allow_queue_busy: bool = False,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Return launchable recipients or per-recipient reasons without writing messages.

    Normal chat is live-wake-only: do not leave future inbox work behind when a
    recipient cannot start handling the message now.
    """
    settings = await _load_settings(db)
    launchable: list[tuple[str, str]] = []
    not_started: list[dict[str, Any]] = []
    unavailable_statuses = {"offline", "stale", "stopped"}

    for recipient_id in recipients:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
        row = await agent_cursor.fetchone()
        if not row:
            not_started.append(_dispatch_fix_hint(recipient_id, None, "agent is not registered"))
            continue

        dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
        base_status = await _compute_agent_status(
            row,
            settings.get("idle_minutes", 5),
            settings.get("offline_minutes", 30),
        )
        effective_status = _status_with_dispatch(base_status, dispatch_state)

        if effective_status in unavailable_statuses:
            hint = _dispatch_fix_hint(recipient_id, row, f'agent status is "{effective_status}"')
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        execution_mode, reason = _agent_execution_mode(row)
        if reason or not execution_mode:
            hint = _dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable")
            hint["recipientStatus"] = effective_status
            not_started.append(hint)
            continue

        if dispatch_state.get("hasActiveRun"):
            active = dispatch_state.get("activeRun") or {}
            capabilities = _row_capabilities(row)
            if allow_steer and "steer" in capabilities:
                launchable.append((recipient_id, execution_mode))
                continue
            if allow_queue_busy:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent is working")
            hint["recipientStatus"] = "working"
            hint["activeRun"] = active
            active_suffix = f" on {active.get('runId')}" if active.get("runId") else ""
            hint["fix"] = (
                f'Agent "{recipient_id}" is already working{active_suffix}. '
                "Wait, interrupt the active run, use comms_send(steer=true) for current-run guidance, or explicitly enable queueIfBusy."
            )
            not_started.append(hint)
            continue

        queued_runs = int(dispatch_state.get("queuedRuns") or 0)
        if queued_runs > 0:
            if allow_queue_busy:
                launchable.append((recipient_id, execution_mode))
                continue
            hint = _dispatch_fix_hint(recipient_id, row, "agent already has queued work")
            hint["recipientStatus"] = effective_status
            hint["queuedRuns"] = queued_runs
            hint["fix"] = (
                f'Agent "{recipient_id}" already has {queued_runs} queued run(s). '
                "Wait for the queue to drain, cancel stale runs, or explicitly enable queueIfBusy."
            )
            not_started.append(hint)
            continue

        launchable.append((recipient_id, execution_mode))

    return launchable, not_started


async def _append_dispatch_event(db, run_id: str, event_type: str, body: str = ""):
    await db.execute(
        "INSERT INTO dispatch_events (run_id, event_type, body, created_at) VALUES (?,?,?,?)",
        (run_id, event_type, body or "", _now())
    )


async def _append_dispatch_control(
    db,
    run_id: str,
    *,
    from_agent: str,
    action: str,
    body: str = "",
    source_message_id: str = "",
):
    control_id = f"ctl_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    await db.execute(
        """
        INSERT INTO dispatch_controls (
            id, run_id, from_agent, source_message_id, action, body, status, requested_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (control_id, run_id, from_agent or "", source_message_id or "", action, body or "", "pending", _now())
    )
    await _append_dispatch_event(db, run_id, f"control:{action}", f"requested by {from_agent or 'unknown'}")
    return control_id


_PRIORITY_ORDER = {"normal": 0, "high": 1, "urgent": 2}
_MERGED_DISPATCH_HEADER = "[AIFY PENDING DISPATCHES]"
_MERGED_DISPATCH_FOOTER = "[/AIFY PENDING DISPATCHES]"
_DISPATCH_BUFFER_CAP = 10
_CHANNEL_FANOUT_DEDUP_WINDOW_MS = 30_000


def _stronger_priority(left: str, right: str) -> str:
    left_key = str(left or "normal").strip().lower() or "normal"
    right_key = str(right or "normal").strip().lower() or "normal"
    return left_key if _PRIORITY_ORDER.get(left_key, 0) >= _PRIORITY_ORDER.get(right_key, 0) else right_key


def _clip_text(text: str, limit: int = 240) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)].rstrip() + "…"


def _render_pending_dispatch_item(
    index: int,
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    message_id: str = "",
    in_reply_to: str = "",
    requested_at: str = "",
) -> str:
    lines = [
        f"=== ITEM {index} ===",
        f"From: {from_agent or 'unknown'}",
        f"Type: {message_type or 'request'}",
        f"Subject: {subject or '(no subject)'}",
        f"Priority: {priority or 'normal'}",
    ]
    if requested_at:
        lines.append(f"At: {requested_at}")
    if message_id:
        lines.append(f"MessageId: {message_id}")
        lines.append("Full details are in the inbox. Read them there if you need the complete context.")
        preview = _clip_text(body or "", 240)
        if preview:
            lines.extend(["Body preview:", preview])
    else:
        if in_reply_to:
            lines.append(f"InReplyTo: {in_reply_to}")
        lines.extend(["Body:", str(body or "").strip()])
    return "\n".join(lines).strip()


def _pending_dispatch_count(body: str) -> int:
    text = str(body or "")
    if text.startswith(_MERGED_DISPATCH_HEADER):
        return len(re.findall(r"^=== ITEM \d+ ===$", text, flags=re.MULTILINE))
    return 1 if text.strip() else 0


def _build_pending_dispatch_subject(count: int, latest_subject: str) -> str:
    latest = _clip_text(latest_subject or "(no subject)", 80)
    if count <= 1:
        return latest
    return f"Pending updates ({count}); latest: {latest}"


def _append_pending_dispatch_body(
    existing_run,
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    requested_at: str,
    message_id: str = "",
    in_reply_to: str = "",
) -> Optional[tuple[str, int]]:
    """
    Returns (merged_body, item_count) on success, or None if the buffer cap
    is already at _DISPATCH_BUFFER_CAP and the new item cannot be appended.
    """
    existing_body = str(existing_run["body"] or "")
    if existing_body.startswith(_MERGED_DISPATCH_HEADER):
        current_count = _pending_dispatch_count(existing_body)
        if current_count >= _DISPATCH_BUFFER_CAP:
            return None
        count = current_count + 1
        new_item = _render_pending_dispatch_item(
            count,
            from_agent=from_agent,
            message_type=message_type,
            subject=subject,
            body=body,
            priority=priority,
            message_id=message_id,
            in_reply_to=in_reply_to,
            requested_at=requested_at,
        )
        merged_body = existing_body.replace(_MERGED_DISPATCH_FOOTER, f"\n\n{new_item}\n{_MERGED_DISPATCH_FOOTER}")
        return merged_body, count

    first_item = _render_pending_dispatch_item(
        1,
        from_agent=str(existing_run["from_agent"] or ""),
        message_type=str(existing_run["message_type"] or ""),
        subject=str(existing_run["subject"] or ""),
        body=str(existing_run["body"] or ""),
        priority=str(existing_run["priority"] or "normal"),
        message_id=str(existing_run["message_id"] or ""),
        in_reply_to=str(existing_run["in_reply_to"] or ""),
        requested_at=str(existing_run["requested_at"] or ""),
    )
    second_item = _render_pending_dispatch_item(
        2,
        from_agent=from_agent,
        message_type=message_type,
        subject=subject,
        body=body,
        priority=priority,
        message_id=message_id,
        in_reply_to=in_reply_to,
        requested_at=requested_at,
    )
    merged_body = "\n".join([
        _MERGED_DISPATCH_HEADER,
        f"Additional dispatches arrived while another run was active (cap: {_DISPATCH_BUFFER_CAP} items).",
        "Process the buffered items in order. For message-backed items, use comms_inbox(...) if you need the full original text.",
        "",
        first_item,
        "",
        second_item,
        _MERGED_DISPATCH_FOOTER,
    ]).strip()
    return merged_body, 2


def _dispatch_buffer_full_hint(
    recipient_id: str,
    row,
    *,
    from_agent: str,
    current_count: int,
    recipient_status: str,
    has_active_run: bool,
) -> dict[str, Any]:
    runtime = _normalize_runtime((row["runtime"] if row else "") or "generic")
    session_mode = _normalize_session_mode((row["session_mode"] if row else "") or "resident")
    return {
        "targetAgentId": recipient_id,
        "reason": "buffer_full",
        "runtime": runtime,
        "sessionMode": session_mode,
        "bufferCap": _DISPATCH_BUFFER_CAP,
        "bufferedCount": current_count,
        "recipientStatus": recipient_status,
        "hasActiveRun": has_active_run,
        "fromAgent": from_agent,
        "fix": (
            f"Target agent already has {current_count} buffered dispatches from {from_agent} "
            f"(cap: {_DISPATCH_BUFFER_CAP}). Wait for the current run to drain, "
            f"interrupt the active run with comms_run_interrupt, or call "
            f"comms_agent_info to inspect the queue before retrying."
        ),
    }


async def _find_mergeable_queued_run(
    db,
    *,
    recipient_id: str,
    from_agent: str,
):
    # Merge across ALL senders, not just the same sender. The merged body
    # includes sender attribution per item so the recipient knows who sent
    # what. Oldest message at the top, newest at the bottom.
    cursor = await db.execute(
        """
        SELECT *
        FROM dispatch_runs
        WHERE target_agent = ?
          AND status = 'queued'
        ORDER BY requested_at ASC
        LIMIT 1
        """,
        (recipient_id,),
    )
    return await cursor.fetchone()


async def _discard_superseded_active_run(db, recipient_id: str, active_run: dict[str, Any]) -> bool:
    owner_bridge_id = str(active_run.get("claimBridgeId") or "").strip()
    if not owner_bridge_id or not await _bridge_is_superseded(db, owner_bridge_id, recipient_id):
        return False

    finished_at = _now()
    await db.execute(
        "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
        (
            f'Auto-healed before steer: bridge "{owner_bridge_id}" was already superseded',
            finished_at,
            active_run["runId"],
        ),
    )
    await _append_dispatch_event(
        db,
        active_run["runId"],
        "auto_heal",
        f"Steer fallback cleaned stale run owned by superseded bridge {owner_bridge_id}",
    )
    await _fail_pending_controls_for_run(
        db,
        active_run["runId"],
        handled_at=finished_at,
        response_text=f'Stale run cleaned before steer by live server path. Superseded bridge: "{owner_bridge_id}".',
    )
    return True


async def _finalize_dispatch_runs(
    db,
    runs: list[dict[str, Any]],
    launchable_recipients: list[tuple[str, str]],
    not_started: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    finalized = []
    for run, (_, execution_mode) in zip(runs, launchable_recipients):
        if run.get("rejected"):
            not_started.append(run["rejectionHint"])
            continue

        if run.get("steered"):
            dispatch_state = await _get_dispatch_state_for_agent(db, run["targetAgentId"])
            run["queuedRunsForTarget"] = dispatch_state.get("queuedRuns", 0)
            finalized.append(run)
            continue

        await db.execute(
            "UPDATE dispatch_runs SET execution_mode = ? WHERE id = ?",
            (execution_mode, run["runId"])
        )
        active = await _get_blocking_active_run(db, run["targetAgentId"], exclude_run_id=run["runId"])
        if active:
            run["queuedBehindActiveRun"] = {
                "runId": active["runId"],
                "status": active["status"],
                "subject": active["subject"],
            }
        dispatch_state = await _get_dispatch_state_for_agent(db, run["targetAgentId"])
        run["queuedRunsForTarget"] = dispatch_state.get("queuedRuns", 0)
        finalized.append(run)
    return finalized


async def _create_dispatch_runs(
    db,
    recipients: list[str],
    *,
    from_agent: str,
    message_type: str,
    subject: str,
    body: str,
    priority: str,
    in_reply_to: Optional[str],
    dispatch_mode: str,
    execution_mode: str,
    requested_runtime: Optional[str],
    message_id: Optional[str] = None,
    source_message_ids: Optional[dict[str, str]] = None,
    steer: bool = False,
    require_reply: bool = False,
):
    runs = []
    requested_at = _now()
    for recipient_id in recipients:
        source_message_id = _dispatch_message_id_for_recipient(
            recipient_id,
            message_id=message_id,
            source_message_ids=source_message_ids,
        )
        # steer=true: if target has an active run, deliver as a steer
        # control on that run (injected between tool calls) instead of
        # queuing a new dispatch. Symmetric for Claude and Codex.
        if steer:
            row_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            recipient_row = await row_cursor.fetchone()
            capabilities = _row_capabilities(recipient_row) if recipient_row else []
            active_state = await _get_dispatch_state_for_agent(db, recipient_id)
            active_run = active_state.get("activeRun")
            if active_run and await _discard_superseded_active_run(db, recipient_id, active_run):
                active_state = await _get_dispatch_state_for_agent(db, recipient_id)
                active_run = active_state.get("activeRun")
            if active_run and "steer" in capabilities:
                steer_body = f"[Message from {from_agent}]\nSubject: {subject}\n\n{body}"
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=from_agent,
                    action="steer",
                    body=steer_body,
                    source_message_id=source_message_id,
                )
                runs.append({
                    "runId": active_run["runId"],
                    "targetAgentId": recipient_id,
                    "status": "steered",
                    "steered": True,
                    "requireReply": require_reply,
                    "controlId": control_id,
                    "steeredIntoActiveRun": {
                        "runId": active_run["runId"],
                        "status": active_run["status"],
                        "subject": active_run["subject"],
                    },
                })
                continue

        mergeable_run = await _find_mergeable_queued_run(
            db,
            recipient_id=recipient_id,
            from_agent=from_agent,
        )
        if mergeable_run:
            merge_result = _append_pending_dispatch_body(
                mergeable_run,
                from_agent=from_agent,
                message_type=message_type,
                subject=subject,
                body=body,
                priority=priority,
                requested_at=requested_at,
                message_id=source_message_id,
                in_reply_to=str(in_reply_to or ""),
            )
            if merge_result is None:
                # Buffer cap hit. Surface a rejection without dropping the existing
                # buffered run. Caller propagates this into notStarted.
                current_count = _pending_dispatch_count(str(mergeable_run["body"] or ""))
                row_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                recipient_row = await row_cursor.fetchone()
                recipient_status = "unknown"
                has_active = False
                if recipient_row:
                    settings = await _load_settings(db)
                    recipient_status = await _compute_agent_status(
                        recipient_row,
                        settings.get("idle_minutes", 5),
                        settings.get("offline_minutes", 30),
                    )
                    dispatch_state = await _get_dispatch_state_for_agent(db, recipient_id)
                    has_active = bool(dispatch_state.get("hasActiveRun"))
                    recipient_status = _status_with_dispatch(recipient_status, dispatch_state)
                rejection_hint = _dispatch_buffer_full_hint(
                    recipient_id,
                    recipient_row,
                    from_agent=from_agent,
                    current_count=current_count,
                    recipient_status=recipient_status,
                    has_active_run=has_active,
                )
                await _append_dispatch_event(
                    db,
                    mergeable_run["id"],
                    "buffer_full",
                    f"Rejected dispatch from {from_agent}: buffer cap {_DISPATCH_BUFFER_CAP} reached",
                )
                runs.append({
                    "runId": None,
                    "targetAgentId": recipient_id,
                    "status": "rejected",
                    "rejected": True,
                    "rejectionHint": rejection_hint,
                })
                continue

            merged_body, merged_count = merge_result
            # Keep message_id and in_reply_to pointing at the FIRST item that
            # opened this buffered run. Per-item ids are preserved in the body
            # text so the receiver can still pull each original from inbox.
            await db.execute(
                """
                UPDATE dispatch_runs
                SET subject = ?, body = ?, priority = ?, dispatch_mode = ?, message_type = ?, require_reply = ?
                WHERE id = ?
                """,
                (
                    _build_pending_dispatch_subject(merged_count, subject),
                    merged_body,
                    _stronger_priority(mergeable_run["priority"], priority),
                    "require_start" if mergeable_run["dispatch_mode"] == "require_start" or dispatch_mode == "require_start" else mergeable_run["dispatch_mode"],
                    message_type,
                    1 if (bool(mergeable_run["require_reply"]) or require_reply) else 0,
                    mergeable_run["id"],
                ),
            )
            await _append_dispatch_event(
                db,
                mergeable_run["id"],
                "merged",
                f"Buffered update from {from_agent}: {subject}",
            )
            runs.append({
                "runId": mergeable_run["id"],
                "targetAgentId": recipient_id,
                "status": "queued",
                "merged": True,
                "mergedCount": merged_count,
                "requireReply": bool(mergeable_run["require_reply"]) or require_reply,
            })
            continue

        run_id = f"run_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, message_id, from_agent, target_agent, dispatch_mode, execution_mode, requested_runtime,
                message_type, subject, body, priority, in_reply_to, status, require_reply, requested_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, source_message_id or None, from_agent, recipient_id, dispatch_mode, execution_mode, requested_runtime or "",
                message_type, subject, body, priority, in_reply_to, "queued", 1 if require_reply else 0, requested_at
            )
        )
        await _append_dispatch_event(db, run_id, "queued", f"{message_type}: {subject}")
        runs.append({"runId": run_id, "targetAgentId": recipient_id, "status": "queued", "requireReply": require_reply})
    return runs


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


def _primary_result_message_id(message_id: str, recipients: list[str]) -> str:
    if len(recipients) == 1:
        return message_id
    if not recipients:
        return message_id
    return f"{message_id}-{recipients[0]}"


def _dispatch_message_id_for_recipient(
    recipient_id: str,
    *,
    message_id: Optional[str],
    source_message_ids: Optional[dict[str, str]] = None,
) -> str:
    return str((source_message_ids or {}).get(recipient_id, message_id or "") or "").strip()


def _dispatch_source_message_ids(row) -> list[str]:
    ids = []
    primary = str((row["message_id"] if row and "message_id" in row.keys() else "") or "").strip()
    if primary:
        ids.append(primary)
    body = str((row["body"] if row and "body" in row.keys() else "") or "")
    ids.extend(match.group(1).strip() for match in re.finditer(r"\bMessage\s*Id:\s*([^\s]+)", body, re.IGNORECASE))
    return _dedupe_preserve([message_id for message_id in ids if message_id])


async def _mark_dispatch_source_messages_read(db, row, agent_id: str, read_at: str) -> int:
    message_ids = _dispatch_source_message_ids(row)
    if not message_ids:
        return 0
    for message_id in message_ids:
        await db.execute(
            "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
            (message_id, agent_id, read_at),
        )
    return len(message_ids)


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


def _serialize_inbox_message(row, *, include_body: bool) -> dict[str, Any]:
    msg = {
        "id": row["id"],
        "from": row["from_agent"],
        "type": row["type"],
        "source": row["source"],
        "channel": row["channel"],
        "subject": row["subject"],
        "preview": _clip_text(row["body"] or "", 240),
        "priority": row["priority"],
        "timestamp": row["timestamp"],
        "inReplyTo": row["in_reply_to"],
        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
        "read": row["read_at"] is not None,
        "readAt": row["read_at"],
    }
    if include_body:
        msg["body"] = row["body"]
    if row["in_reply_to"]:
        msg["parentContext"] = None
    return msg


async def _link_reply_message_to_dispatch_run(
    db,
    *,
    from_agent: str,
    resolved_in_reply_to: str,
    reply_message_id: str,
    reply_type: str,
    reply_body: str,
) -> bool:
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
        existing_cursor = await db.execute("SELECT body FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        existing_body = str((existing_message["body"] if existing_message else "") or "")
        if not existing_body.startswith("Auto-mirrored dispatch "):
            return False

    current_status = str(replied_run["status"] or "").strip().lower()
    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (reply_message_id, replied_run["id"]),
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
        if current_status in _DISPATCH_TERMINAL_STATUSES
        else f"Result reply recorded from {from_agent}"
    )
    await _append_dispatch_event(db, replied_run["id"], "handoff", handoff_note)
    return True


_UNTHREADED_HANDOFF_TYPES = {"response", "review", "error", "approval"}
_UNTHREADED_HANDOFF_WINDOW_MS = 24 * 60 * 60 * 1000


async def _link_unthreaded_reply_to_recent_dispatch_run(
    db,
    *,
    from_agent: str,
    to_agent: str,
    reply_message_id: str,
    reply_type: str,
    reply_timestamp_ms: int,
) -> bool:
    if str(reply_type or "").strip().lower() not in _UNTHREADED_HANDOFF_TYPES:
        return False
    if not from_agent or not to_agent or not reply_message_id:
        return False

    latest_requested_at = _iso_from_ms(reply_timestamp_ms)
    earliest_requested_at = _iso_from_ms(max(0, reply_timestamp_ms - _UNTHREADED_HANDOFF_WINDOW_MS))
    run_cursor = await db.execute(
        """
        SELECT * FROM dispatch_runs
        WHERE target_agent = ?
          AND from_agent = ?
          AND require_reply = 1
          AND status IN ('claimed', 'running', 'completed', 'failed', 'cancelled')
          AND requested_at >= ?
          AND requested_at <= ?
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, to_agent, earliest_requested_at, latest_requested_at),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
        return False
    existing_result_id = str(replied_run["result_message_id"] or "").strip()
    if existing_result_id:
        existing_cursor = await db.execute("SELECT body FROM messages WHERE id = ?", (existing_result_id,))
        existing_message = await existing_cursor.fetchone()
        existing_body = str((existing_message["body"] if existing_message else "") or "")
        if not existing_body.startswith("Auto-mirrored dispatch "):
            return False

    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (reply_message_id, replied_run["id"]),
    )
    await _append_dispatch_event(
        db,
        replied_run["id"],
        "handoff",
        f"Unthreaded result reply linked from {from_agent}",
    )
    return True


def _auto_handoff_subject_for_run(row) -> str:
    subject = str((row["subject"] if row else "") or (row["id"] if row else "") or "dispatch result").strip()
    status = str((row["status"] if row else "") or "").strip().lower()
    if status == "failed":
        return f"[FAILED] {subject}"
    if status == "cancelled":
        return f"[CANCELLED] {subject}"
    return f"Re: {subject}"


def _auto_handoff_body_for_run(row) -> str:
    status = str((row["status"] if row else "") or "").strip().lower()
    from_agent = str((row["from_agent"] if row else "") or "").strip()
    if status == "failed":
        detail = str((row["error_text"] if row else "") or (row["summary"] if row else "") or "Run failed.").strip()
        if from_agent == "dashboard":
            return f"The run failed before the agent sent a chat reply.\n\n{detail}"
        intro = "Auto-mirrored dispatch failure because no explicit reply message was recorded for the run."
    elif status == "cancelled":
        detail = str((row["summary"] if row else "") or "Run cancelled.").strip()
        if from_agent == "dashboard":
            return f"The run was cancelled before the agent sent a chat reply.\n\n{detail}"
        intro = "Auto-mirrored dispatch cancellation because no explicit reply message was recorded for the run."
    else:
        detail = str((row["summary"] if row else "") or "Run completed.").strip()
        if from_agent == "dashboard":
            return detail
        intro = "Auto-mirrored dispatch result because no explicit reply message was recorded for the run."
    return f"{intro}\n\n{detail}"


async def _mirror_missing_dispatch_handoff(db, row) -> Optional[str]:
    if not row or not _row_require_reply(row) or str(row["result_message_id"] or "").strip():
        return None
    if _is_delivery_only_claude_run(row):
        return None

    status = str(row["status"] or "").strip().lower()
    if status not in _DISPATCH_TERMINAL_STATUSES:
        return None

    ts = int(time.time() * 1000)
    message_id = f"{ts}-{uuid.uuid4().hex[:8]}"
    message_type = "error" if status == "failed" else "response"
    from_agent = str(row["target_agent"] or "").strip()
    to_agent = str(row["from_agent"] or "").strip()
    subject = _auto_handoff_subject_for_run(row)
    body = _auto_handoff_body_for_run(row)
    priority = row["priority"] or "normal"
    launchable_recipients: list[tuple[str, str]] = []
    not_started: list[dict[str, Any]] = []
    if to_agent and to_agent != "dashboard":
        launchable_recipients, not_started = await _preflight_live_send_recipients(
            db,
            [to_agent],
            allow_steer=True,
            allow_queue_busy=True,
        )

    await db.execute(
        """
        INSERT INTO messages (
            id, from_agent, to_agent, source, type, subject, body, priority,
            dispatch_requested, in_reply_to, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message_id,
            from_agent,
            to_agent,
            "direct",
            message_type,
            subject,
            body,
            priority,
            1 if launchable_recipients else 0,
            row["message_id"],
            ts,
        ),
    )
    await db.execute(
        "UPDATE dispatch_runs SET result_message_id = ? WHERE id = ?",
        (message_id, row["id"]),
    )
    await _append_dispatch_event(
        db,
        row["id"],
        "handoff",
        f"Auto-mirrored missing handoff to {to_agent}",
    )
    if launchable_recipients:
        delivery_runs = await _create_dispatch_runs(
            db,
            [recipient_id for recipient_id, _ in launchable_recipients],
            from_agent=from_agent,
            message_type=message_type,
            subject=subject,
            body=body,
            priority=priority,
            in_reply_to=row["message_id"],
            dispatch_mode="start_if_possible",
            execution_mode="managed",
            requested_runtime=None,
            message_id=message_id,
            steer=True,
            require_reply=False,
        )
        delivery_runs = await _finalize_dispatch_runs(
            db,
            delivery_runs,
            launchable_recipients,
            not_started,
        )
        run_ids = [str(run.get("runId") or "") for run in delivery_runs if run.get("runId")]
        if run_ids:
            await _append_dispatch_event(
                db,
                row["id"],
                "handoff",
                f"Queued mirrored handoff delivery to {to_agent}: {', '.join(run_ids)}",
            )
    elif not_started:
        reasons = "; ".join(str(item.get("reason") or "not startable") for item in not_started)
        await _append_dispatch_event(
            db,
            row["id"],
            "handoff",
            f"Mirrored handoff stored for {to_agent}; live delivery not queued: {reasons}",
        )
    return message_id


async def _cancel_nonterminal_runs_for_agents(
    db,
    agent_ids: list[str],
    *,
    summary: str,
    event_type: str,
) -> int:
    targets = _dedupe_preserve([str(agent_id or "").strip() for agent_id in agent_ids if str(agent_id or "").strip()])
    if not targets:
        return 0

    cancelled = 0
    finished_at = _now()
    chunk_size = 250
    for i in range(0, len(targets), chunk_size):
        chunk = targets[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        cursor = await db.execute(
            f"""
            SELECT id
            FROM dispatch_runs
            WHERE target_agent IN ({placeholders})
              AND status IN ('queued', 'claimed', 'running')
            """,
            chunk,
        )
        rows = await cursor.fetchall()
        if not rows:
            continue
        for row in rows:
            await db.execute(
                "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                (summary, finished_at, row["id"]),
            )
            await _append_dispatch_event(db, row["id"], event_type, summary)
            await _fail_pending_controls_for_run(
                db,
                row["id"],
                handled_at=finished_at,
                response_text=summary,
            )
            cancelled += 1
    return cancelled


async def _has_recent_direct_delivery_for_channel_fanout(
    db,
    *,
    from_agent: str,
    recipient_id: str,
    message_type: str,
    body: str,
    timestamp_ms: int,
) -> bool:
    lower_bound = int(timestamp_ms) - _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    upper_bound = int(timestamp_ms) + _CHANNEL_FANOUT_DEDUP_WINDOW_MS
    cursor = await db.execute(
        """
        SELECT 1
        FROM messages
        WHERE from_agent = ?
          AND to_agent = ?
          AND source = 'direct'
          AND type = ?
          AND body = ?
          AND timestamp BETWEEN ? AND ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (from_agent, recipient_id, message_type, body, lower_bound, upper_bound),
    )
    return await cursor.fetchone() is not None

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "aify-comms",
        "version": "3.6.6",
        "storage": "sqlite",
        "endpoints": {
            "agents": "/api/v1/agents",
            "environments": "/api/v1/environments",
            "spawnRequests": "/api/v1/spawn-requests",
            "sessions": "/api/v1/sessions",
            "messages": "/api/v1/messages",
            "dispatch": "/api/v1/dispatch",
            "shared": "/api/v1/shared",
            "channels": "/api/v1/channels",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
        },
    }


# ─── Environments ────────────────────────────────────────────────────────────

@router.get("/environments")
async def list_environments(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM environments WHERE status != 'forgotten'")
        environments = [
            _environment_record_to_dict(row, offline_seconds=settings.get("environment_offline_seconds", 90))
            for row in await cursor.fetchall()
        ]
        status_rank = {"online": 0, "degraded": 1, "unknown": 2, "offline": 3, "disabled": 4}
        environments.sort(key=lambda env: (status_rank.get(env.get("status") or "", 5), str(env.get("label") or "").lower(), str(env.get("id") or "").lower()))
        return {"ok": True, "environments": environments}
    finally:
        await db.close()


@router.post("/environments/heartbeat")
async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):
    env_id = str(req.id or "").strip()
    if not env_id:
        raise HTTPException(400, "Environment id is required")

    now = _now()
    cwd_roots = _normalize_roots(req.cwdRoots or [])
    runtimes = req.runtimes or []
    metadata = req.metadata or {}
    requested_status = str(req.status or "online").strip().lower()
    if requested_status not in {"online", "degraded", "offline"}:
        requested_status = "online"
    db = await get_db()
    try:
        existing_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        existing = await existing_cursor.fetchone()
        registered_at = existing["registered_at"] if existing else now
        existing_metadata = _json_loads_or(existing["metadata"], {}) if existing else {}
        manual_roots = bool(existing_metadata.get("manualRoots"))
        effective_roots = _json_loads_or(existing["cwd_roots"], []) if existing and manual_roots else cwd_roots
        next_metadata = {**metadata, "advertisedCwdRoots": cwd_roots}
        if manual_roots:
            next_metadata.update({
                "manualRoots": True,
                "manualRootsUpdatedAt": existing_metadata.get("manualRootsUpdatedAt", ""),
                "manualRootsUpdatedBy": existing_metadata.get("manualRootsUpdatedBy", ""),
            })
        superseded_bridge_id = ""
        if existing and str(existing["bridge_id"] or "").strip() and str(req.bridgeId or "").strip():
            existing_bridge_id = str(existing["bridge_id"] or "").strip()
            incoming_bridge_id = str(req.bridgeId or "").strip()
            if existing_bridge_id != incoming_bridge_id:
                existing_metadata = _json_loads_or(existing["metadata"], {})
                existing_started = _bridge_started_at(existing_metadata)
                incoming_started = _bridge_started_at(metadata)
                if existing_started and (not incoming_started or incoming_started < existing_started):
                    return {"ok": True, "environment": _environment_record_to_dict(existing)}
                if incoming_started and (not existing_started or incoming_started > existing_started):
                    superseded_bridge_id = existing_bridge_id
        if (
            existing
            and requested_status != "online"
            and str(existing["bridge_id"] or "").strip()
            and str(req.bridgeId or "").strip()
            and str(existing["bridge_id"] or "").strip() != str(req.bridgeId or "").strip()
        ):
            return {"ok": True, "environment": _environment_record_to_dict(existing)}
        if existing:
            await db.execute(
                """
                UPDATE environments
                SET label = ?, machine_id = ?, os = ?, kind = ?, bridge_id = ?,
                    bridge_version = ?, cwd_roots = ?, runtimes = ?, status = ?,
                    metadata = ?, last_seen = ?
                WHERE id = ?
                """,
                (
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    now,
                    env_id,
                ),
            )
        else:
            await db.execute(
                """
                INSERT INTO environments (
                    id, label, machine_id, os, kind, bridge_id, bridge_version,
                    cwd_roots, runtimes, status, metadata, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    env_id,
                    req.label or env_id,
                    req.machineId or "",
                    req.os or "",
                    req.kind or "",
                    req.bridgeId or "",
                    req.bridgeVersion or "",
                    json.dumps(effective_roots),
                    json.dumps(runtimes),
                    requested_status,
                    json.dumps(next_metadata),
                    registered_at,
                    now,
                ),
            )
        if superseded_bridge_id:
            pending_cursor = await db.execute(
                """
                SELECT id
                FROM environment_controls
                WHERE environment_id = ?
                  AND bridge_id = ?
                  AND action = 'stop'
                  AND status IN ('pending', 'claimed')
                LIMIT 1
                """,
                (env_id, superseded_bridge_id),
            )
            pending = await pending_cursor.fetchone()
            if not pending:
                await db.execute(
                    """
                    INSERT INTO environment_controls (
                        id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"envctl-{uuid.uuid4().hex}",
                        env_id,
                        superseded_bridge_id,
                        req.machineId or "",
                        "stop",
                        "pending",
                        "server:superseded-bridge",
                        now,
                    ),
                )
        await db.commit()
        row_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (env_id,))
        row = await row_cursor.fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_heartbeat", {"environmentId": env_id, "bridgeId": req.bridgeId or ""})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


@router.patch("/environments/{environment_id:path}/roots")
async def update_environment_roots(environment_id: str, req: EnvironmentRootsUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        metadata = _json_loads_or(env["metadata"], {})
        if req.resetToBridgeAdvertised:
            roots = _normalize_roots(metadata.get("advertisedCwdRoots") or _json_loads_or(env["cwd_roots"], []))
            next_metadata = {k: v for k, v in metadata.items() if k not in {"manualRoots", "manualRootsUpdatedAt", "manualRootsUpdatedBy"}}
            next_metadata["manualRoots"] = False
            next_metadata["manualRootsResetAt"] = now
            next_metadata["manualRootsResetBy"] = req.requestedBy or "dashboard"
        else:
            roots = _normalize_roots(req.roots or [])
            if not roots:
                raise HTTPException(400, "At least one root is required. Use resetToBridgeAdvertised to return to bridge-advertised roots.")
            next_metadata = {
                **metadata,
                "manualRoots": True,
                "manualRootsUpdatedAt": now,
                "manualRootsUpdatedBy": req.requestedBy or "dashboard",
                "previousCwdRoots": _json_loads_or(env["cwd_roots"], []),
            }
        await db.execute(
            """
            UPDATE environments
            SET cwd_roots = ?,
                metadata = ?,
                last_seen = ?
            WHERE id = ?
            """,
            (json.dumps(roots), json.dumps(next_metadata), now, environment_id),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        environment = _environment_record_to_dict(row)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("environment_roots_updated", {"environmentId": environment_id})
        return {"ok": True, "environment": environment}
    finally:
        await db.close()


# ─── Spawn Requests And Sessions ─────────────────────────────────────────────

@router.get("/spawn-requests")
async def list_spawn_requests(
    request: Request,
    status: Optional[str] = None,
    environmentId: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
):
    db = await get_db()
    try:
        await _repair_spawn_requests_from_initial_dispatch_failures(db)
        where = []
        params: list[Any] = []
        if status:
            where.append("sr.status = ?")
            params.append(status)
        if environmentId:
            where.append("sr.environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"""
            SELECT sr.*, ss.id AS spec_row_id
            FROM spawn_requests sr
            LEFT JOIN spawn_specs ss ON ss.id = sr.spawn_spec_id
            {where_sql}
            ORDER BY sr.created_at DESC
            LIMIT ?
            """,
            (*params, limit),
        )
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))
            spec_row = await spec_cursor.fetchone()
            result.append(_spawn_request_to_dict(row, _spawn_spec_to_dict(spec_row) if spec_row else None))
        return {"ok": True, "spawnRequests": result}
    finally:
        await db.close()


@router.post("/environments/{environment_id:path}/control")
async def control_environment(environment_id: str, req: EnvironmentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "forget"}:
        raise HTTPException(400, "Environment control action must be stop or forget")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env = await cursor.fetchone()
        if not env:
            raise HTTPException(404, "Environment not found")
        now = _now()
        if action == "forget":
            await db.execute("DELETE FROM environment_controls WHERE environment_id = ?", (environment_id,))
            await db.execute(
                """
                UPDATE environments
                SET status = 'forgotten',
                    bridge_id = '',
                    bridge_version = '',
                    runtimes = '[]',
                    metadata = ?,
                    last_seen = ?
                WHERE id = ?
                """,
                (json.dumps({**_json_loads_or(env["metadata"], {}), "forgottenAt": now, "forgottenBy": req.requestedBy or "dashboard"}), now, environment_id),
            )
            await db.commit()
            ws = await _get_ws(request)
            if ws: await ws.broadcast("environment_forgotten", {"environmentId": environment_id})
            return {"ok": True, "action": action, "environmentId": environment_id}

        control_id = f"envctl-{uuid.uuid4().hex}"
        await db.execute(
            """
            INSERT INTO environment_controls (
                id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                control_id,
                environment_id,
                env["bridge_id"] or "",
                env["machine_id"] or "",
                action,
                "pending",
                req.requestedBy or "dashboard",
                now,
            ),
        )
        await db.execute("UPDATE environments SET status = ? WHERE id = ?", ("disabled", environment_id))
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = 'lost',
                ended_at = COALESCE(ended_at, ?),
                last_seen = ?
            WHERE environment_id = ?
              AND status IN ('starting', 'running', 'recovering', 'restarting')
            """,
            (now, now, environment_id),
        )
        await db.execute(
            """
            UPDATE agents
            SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                launch_mode = 'none',
                runtime_state = '{}',
                last_seen = ?
            WHERE id IN (SELECT DISTINCT agent_id FROM agent_sessions WHERE environment_id = ?)
            """,
            (now, environment_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_requested", {"environmentId": environment_id, "action": action})
        return {"ok": True, "controlId": control_id, "action": action, "environmentId": environment_id}
    finally:
        await db.close()


@router.post("/environments/controls/claim")
async def claim_environment_control(req: EnvironmentControlClaim):
    db = await get_db()
    try:
        row = None
        while True:
            cursor = await db.execute(
                """
                SELECT *
                FROM environment_controls
                WHERE environment_id = ?
                  AND status = 'pending'
                  AND (bridge_id = '' OR bridge_id = ?)
                ORDER BY requested_at ASC
                LIMIT 1
                """,
                (req.environmentId, req.bridgeId),
            )
            candidate = await cursor.fetchone()
            if not candidate:
                return {"ok": True, "control": None}
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
            env = await env_cursor.fetchone()
            env_bridge_id = str((env["bridge_id"] if env else "") or "").strip()
            metadata = _json_loads_or(env["metadata"], {}) if env else {}
            bridge_started_at = metadata.get("bridgeStartedAt") or ""
            if (
                candidate["action"] == "stop"
                and env_bridge_id == req.bridgeId
                and _iso_to_epoch(candidate["requested_at"]) > 0
                and _iso_to_epoch(bridge_started_at) > 0
                and _iso_to_epoch(candidate["requested_at"]) < _iso_to_epoch(bridge_started_at)
            ):
                now = _now()
                await db.execute(
                    "UPDATE environment_controls SET status = 'failed', handled_at = ?, error = ? WHERE id = ? AND status = 'pending'",
                    (
                        now,
                        f'Stale stop control ignored because bridge "{req.bridgeId}" started after the control was requested.',
                        candidate["id"],
                    ),
                )
                await db.commit()
                continue
            row = candidate
            break
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = 'claimed', machine_id = ?, claimed_at = ? WHERE id = ? AND status = 'pending'",
            (req.machineId or "", now, row["id"]),
        )
        await db.commit()
        return {
            "ok": True,
            "control": {
                "id": row["id"],
                "environmentId": row["environment_id"],
                "bridgeId": row["bridge_id"] or "",
                "action": row["action"],
                "requestedBy": row["requested_by"] or "",
                "requestedAt": row["requested_at"] or "",
                "currentEnvironment": _environment_record_to_dict(env) if env else None,
            },
        }
    finally:
        await db.close()


@router.patch("/environments/controls/{control_id}")
async def update_environment_control(control_id: str, req: EnvironmentControlUpdate, request: Request):
    status = str(req.status or "").strip().lower()
    if status not in {"completed", "failed"}:
        raise HTTPException(400, "Environment control status must be completed or failed")
    db = await get_db()
    try:
        now = _now()
        await db.execute(
            "UPDATE environment_controls SET status = ?, handled_at = ?, error = ? WHERE id = ?",
            (status, now, req.error or "", control_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("environment_control_updated", {"controlId": control_id, "status": status})
        return {"ok": True, "controlId": control_id, "status": status}
    finally:
        await db.close()


@router.post("/spawn-requests")
async def create_spawn_request(req: SpawnRequestCreate, request: Request):
    validate_name(req.agentId, "agent ID")
    normalized_runtime = _normalize_runtime(req.runtime)
    mode = str(req.mode or "managed-warm").strip()
    if mode not in _SPAWN_MODES:
        raise HTTPException(400, f'Unsupported spawn mode "{mode}"')

    db = await get_db()
    try:
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{req.environmentId}" is {environment.get("status") or "unknown"}; restart its bridge before spawning.')
        runtime_capability = _runtime_capability_for_environment(environment, normalized_runtime)
        if not runtime_capability:
            raise HTTPException(400, f'Environment "{req.environmentId}" does not advertise runtime "{normalized_runtime}"')
        workspace = str(req.workspace or "").strip()
        workspace_root = _workspace_root_for(environment, workspace)
        if not workspace and workspace_root:
            workspace = workspace_root

        now = _now()
        spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO spawn_specs (
                id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                context_policy, restart_policy, metadata, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                spec_id,
                req.agentId,
                req.environmentId,
                normalized_runtime,
                workspace,
                req.model or "",
                req.profile or "",
                mode,
                req.systemPrompt or "",
                req.instructions or "",
                json.dumps(req.envVars or {}),
                json.dumps(req.channelIds or []),
                json.dumps(req.budgetPolicy or {}),
                json.dumps(req.contextPolicy or {}),
                json.dumps(req.restartPolicy or {}),
                json.dumps(req.metadata or {}),
                now,
                now,
            ),
        )
        await db.execute(
            """
            INSERT INTO spawn_requests (
                id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                workspace, workspace_root, initial_message, priority, subject, mode,
                resume_policy, status, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                request_id,
                spec_id,
                req.createdBy or "dashboard",
                req.environmentId,
                req.agentId,
                req.role or "coder",
                req.name or req.agentId,
                normalized_runtime,
                workspace,
                workspace_root,
                req.initialMessage or "",
                req.priority or "normal",
                req.subject or "",
                mode,
                req.resumePolicy or "native_first",
                "queued",
                now,
                now,
            ),
        )
        await db.commit()
        row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()
        spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_created", {"spawnRequestId": request_id, "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(row, _spawn_spec_to_dict(spec))}
    finally:
        await db.close()


@router.post("/spawn-requests/claim")
async def claim_spawn_request(req: SpawnRequestClaim, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (req.environmentId,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            await db.rollback()
            raise HTTPException(404, f'Environment "{req.environmentId}" not found')
        env_bridge_id = str(env_row["bridge_id"] or "").strip()
        if env_bridge_id and env_bridge_id != str(req.bridgeId or "").strip():
            await db.commit()
            return {
                "ok": True,
                "spawnRequest": None,
                "blockedBy": {
                    "reason": "bridge_not_current",
                    "environmentId": req.environmentId,
                    "bridgeId": req.bridgeId,
                    "currentBridgeId": env_bridge_id,
                },
            }

        row_cursor = await db.execute(
            """
            SELECT *
            FROM spawn_requests
            WHERE environment_id = ? AND status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """,
            (req.environmentId,),
        )
        row = await row_cursor.fetchone()
        if not row:
            await db.commit()
            return {"ok": True, "spawnRequest": None}

        claimed_at = _now()
        await db.execute(
            """
            UPDATE spawn_requests
            SET status = 'claimed', claimed_by_bridge_id = ?, claim_machine_id = ?,
                claimed_at = ?, updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (req.bridgeId, req.machineId or "", claimed_at, claimed_at, row["id"]),
        )
        await db.execute(
            "UPDATE environments SET last_seen = ? WHERE id = ?",
            (claimed_at, req.environmentId),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (row["id"],))).fetchone()
        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_claimed", {"spawnRequestId": row["id"], "environmentId": req.environmentId})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(spec_row) if spec_row else None)}
    finally:
        await db.close()


@router.patch("/spawn-requests/{spawn_request_id}")
async def update_spawn_request(spawn_request_id: str, req: SpawnRequestUpdate, request: Request):
    status_value = str(req.status or "").strip().lower()
    if status_value not in {"claimed", "starting", "running", "failed", "cancelled"}:
        raise HTTPException(400, f'Unsupported spawn request status "{req.status}"')
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f'Spawn request "{spawn_request_id}" not found')
        current_status = str(row["status"] or "").strip().lower()
        if current_status in {"failed", "cancelled"} and status_value != current_status:
            raise HTTPException(
                409,
                f'Spawn request "{spawn_request_id}" is already {current_status}; late bridge update "{status_value}" was ignored.',
            )
        if req.bridgeId and row["claimed_by_bridge_id"] and row["claimed_by_bridge_id"] != req.bridgeId:
            raise HTTPException(409, f'Spawn request "{spawn_request_id}" is claimed by another bridge')

        now = _now()
        session_id = row["session_id"] or ""
        finished_at = row["finished_at"]
        started_at = row["started_at"]
        if status_value == "starting" and not started_at:
            started_at = now
        if status_value in _SPAWN_TERMINAL_STATUSES:
            finished_at = now if status_value in {"failed", "cancelled"} else finished_at

        spec_row = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (row["spawn_spec_id"],))).fetchone()
        if not spec_row:
            raise HTTPException(500, f'Spawn spec "{row["spawn_spec_id"]}" missing')

        runtime_state = req.runtimeState or {}
        if req.bridgeId:
            runtime_state = {**runtime_state, "bridgeInstanceId": req.bridgeId}

        if status_value == "running":
            session_id = session_id or f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            effective_session_handle = req.sessionHandle or row["session_handle"] or ""
            if effective_session_handle:
                runtime_state = _runtime_state_with_handle(row["runtime"], runtime_state, effective_session_handle)
            agent_capabilities = _default_capabilities_for(row["runtime"], "managed", effective_session_handle)
            await db.execute(
                """
                INSERT INTO agents (
                    id, role, name, cwd, model, description, instructions, status, status_note,
                    runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                    capabilities, runtime_config, runtime_state, registered_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    role = excluded.role,
                    name = excluded.name,
                    cwd = excluded.cwd,
                    model = excluded.model,
                    instructions = excluded.instructions,
                    status = excluded.status,
                    runtime = excluded.runtime,
                    machine_id = excluded.machine_id,
                    launch_mode = excluded.launch_mode,
                    session_mode = excluded.session_mode,
                    session_handle = excluded.session_handle,
                    managed_by = excluded.managed_by,
                    capabilities = excluded.capabilities,
                    runtime_config = excluded.runtime_config,
                    runtime_state = excluded.runtime_state,
                    last_seen = excluded.last_seen
                """,
                (
                    row["agent_id"],
                    row["role"] or "coder",
                    row["name"] or row["agent_id"],
                    row["workspace"] or "",
                    spec_row["model"] or "",
                    "",
                    spec_row["standing_instructions"] or "",
                    "idle",
                    "",
                    row["runtime"],
                    row["claim_machine_id"] or "",
                    "managed",
                    "managed",
                    effective_session_handle,
                    row["created_by"] or "dashboard",
                    json.dumps(agent_capabilities),
                    "{}",
                    json.dumps(runtime_state),
                    now,
                    now,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    req.bridgeId or row["claimed_by_bridge_id"] or "",
                    row["agent_id"],
                    row["claim_machine_id"] or "",
                    row["runtime"],
                    "managed",
                    now,
                    now,
                    "",
                    None,
                ),
            )
            await db.execute(
                """
                INSERT OR REPLACE INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    row["agent_id"],
                    row["environment_id"],
                    row["runtime"],
                    row["workspace"] or "",
                    row["mode"] or "managed-warm",
                    req.processId or "",
                    effective_session_handle,
                    "",
                    row["spawn_spec_id"],
                    row["id"],
                    json.dumps(req.capabilities or {"persistent": True, "bridgeResume": True}),
                    json.dumps(req.telemetry or {}),
                    "running",
                    started_at or now,
                    now,
                    None,
                ),
            )
            await db.execute(
                """
                UPDATE agent_sessions
                SET status = 'ended',
                    ended_at = COALESCE(NULLIF(ended_at, ''), ?),
                    last_seen = COALESCE(NULLIF(ended_at, ''), NULLIF(last_seen, ''), ?)
                WHERE agent_id = ?
                  AND id != ?
                  AND status IN ('starting', 'running', 'recovering', 'restarting')
                """,
                (now, now, row["agent_id"], session_id),
            )
            if row["status"] != "running" and str(row["initial_message"] or "").strip():
                runs = await _create_dispatch_runs(
                    db,
                    [row["agent_id"]],
                    from_agent=row["created_by"] or "dashboard",
                    message_type="request",
                    subject=row["subject"] or f"Spawn {row['agent_id']}",
                    body=row["initial_message"],
                    priority=row["priority"] or "normal",
                    in_reply_to=None,
                    dispatch_mode="start_if_possible",
                    execution_mode="managed",
                    requested_runtime=row["runtime"],
                    message_id=None,
                    require_reply=True,
                )
                for run in runs:
                    _wake_agent(run["targetAgentId"])

        await db.execute(
            """
            UPDATE spawn_requests
            SET status = ?, process_id = ?, session_handle = ?, session_id = ?, error = ?,
                updated_at = ?, started_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                status_value,
                req.processId or row["process_id"] or "",
                req.sessionHandle or row["session_handle"] or "",
                session_id,
                req.error or "",
                now,
                started_at,
                finished_at,
                spawn_request_id,
            ),
        )
        await db.commit()
        updated = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (spawn_request_id,))).fetchone()
        updated_spec = await (await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (updated["spawn_spec_id"],))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("spawn_request_updated", {"spawnRequestId": spawn_request_id, "status": status_value})
            if status_value == "running":
                await ws.broadcast("agent_registered", {"agentId": row["agent_id"], "runtime": row["runtime"], "sessionMode": "managed"})
                if row["status"] != "running" and str(row["initial_message"] or "").strip():
                    await ws.broadcast("dispatch_queued", {"targetAgentId": row["agent_id"]})
        return {"ok": True, "spawnRequest": _spawn_request_to_dict(updated, _spawn_spec_to_dict(updated_spec) if updated_spec else None)}
    finally:
        await db.close()


@router.get("/sessions")
async def list_sessions(request: Request, agentId: Optional[str] = None, environmentId: Optional[str] = None, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        await _repair_superseded_recovering_sessions(db)
        await _repair_current_session_freshness(db)
        where = []
        params: list[Any] = []
        if agentId:
            where.append("agent_id = ?")
            params.append(agentId)
        if environmentId:
            where.append("environment_id = ?")
            params.append(environmentId)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        cursor = await db.execute(
            f"SELECT * FROM agent_sessions {where_sql} ORDER BY last_seen DESC LIMIT ?",
            (*params, limit),
        )
        return {"ok": True, "sessions": [_agent_session_to_dict(row) for row in await cursor.fetchall()]}
    finally:
        await db.close()


@router.post("/sessions/{session_id}/control")
async def control_session(session_id: str, req: SessionControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"stop", "restart", "recover", "resume", "cli_takeover"}:
        raise HTTPException(400, f'Unsupported session control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))
        session = await cursor.fetchone()
        if not session:
            raise HTTPException(404, f'Session "{session_id}" not found')

        now = _now()
        agent_id = session["agent_id"]
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if active_run:
            control_id = await _append_dispatch_control(
                db,
                active_run["runId"],
                from_agent=req.from_agent or "dashboard",
                action="interrupt",
                body=req.body or f"Session {action} requested from dashboard.",
            )

        spawn_request_row = None
        spawn_spec_row = None
        cancelled_spawns = 0
        if action in {"restart", "recover", "resume"}:
            pending_cursor = await db.execute(
                """
                SELECT *
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (agent_id,),
            )
            pending_spawn = await pending_cursor.fetchone()
            if pending_spawn:
                raise HTTPException(
                    409,
                    f'Agent "{agent_id}" already has pending spawn request "{pending_spawn["id"]}" ({pending_spawn["status"]}).',
                )

        if action in {"restart", "recover", "resume"}:
            spec_id = str(session["spawn_spec_id"] or "").strip()
            if not spec_id:
                raise HTTPException(409, f'Session "{session_id}" has no stored spawn spec to resume')
            spec_cursor = await db.execute("SELECT * FROM spawn_specs WHERE id = ?", (spec_id,))
            spawn_spec_row = await spec_cursor.fetchone()
            if not spawn_spec_row:
                raise HTTPException(409, f'Session "{session_id}" references missing spawn spec "{spec_id}"')
            env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (spawn_spec_row["environment_id"],))
            env_row = await env_cursor.fetchone()
            if not env_row:
                raise HTTPException(409, f'Environment "{spawn_spec_row["environment_id"]}" is not available')

            agent_cursor = await db.execute("SELECT role, name FROM agents WHERE id = ?", (agent_id,))
            agent_row = await agent_cursor.fetchone()
            environment = _environment_record_to_dict(env_row)
            if str(environment.get("status") or "").lower() != "online":
                raise HTTPException(409, f'Environment "{environment.get("id")}" is {environment.get("status") or "unknown"}; assign a live environment before {action}.')
            workspace = spawn_spec_row["workspace"] or session["workspace"] or ""
            workspace_root = _workspace_root_for(environment, workspace)
            request_id = f"spawn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO spawn_requests (
                    id, spawn_spec_id, created_by, environment_id, agent_id, role, name, runtime,
                    workspace, workspace_root, initial_message, priority, subject, mode,
                    resume_policy, status, session_handle, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,
                    spec_id,
                    req.from_agent or "dashboard",
                    spawn_spec_row["environment_id"],
                    agent_id,
                    (agent_row["role"] if agent_row else "") or "coder",
                    (agent_row["name"] if agent_row else "") or agent_id,
                    spawn_spec_row["runtime"],
                    workspace,
                    workspace_root,
                    req.body or "",
                    req.priority or "normal",
                    req.subject or f"{action.title()} {agent_id}",
                    spawn_spec_row["mode"] or session["mode"] or "managed-warm",
                    "native_first",
                    "queued",
                    session["session_handle"] or "",
                    now,
                    now,
                ),
            )
            spawn_request_row = await (await db.execute("SELECT * FROM spawn_requests WHERE id = ?", (request_id,))).fetchone()

        next_status = {
            "stop": "stopped",
            "restart": "restarting",
            "recover": "recovering",
            "resume": "recovering",
            "cli_takeover": "cli-takeover",
        }[action]
        await db.execute(
            """
            UPDATE agent_sessions
            SET status = ?, last_seen = ?, ended_at = CASE WHEN ? IN ('stopped','restarting','recovering') THEN ? ELSE ended_at END
            WHERE id = ?
            """,
            (next_status, now, next_status, now, session_id),
        )
        if action in {"stop", "cli_takeover"}:
            pending_spawn_cursor = await db.execute(
                """
                SELECT id
                FROM spawn_requests
                WHERE agent_id = ?
                  AND status IN ('queued', 'claimed', 'starting')
                """,
                (agent_id,),
            )
            for pending_spawn in await pending_spawn_cursor.fetchall():
                await db.execute(
                    """
                    UPDATE spawn_requests
                    SET status = 'cancelled',
                        error = ?,
                        finished_at = ?,
                        updated_at = ?
                    WHERE id = ?
                      AND status IN ('queued', 'claimed', 'starting')
                    """,
                    (
                        f'Session "{session_id}" was {"paused for CLI takeover" if action == "cli_takeover" else "stopped from the dashboard"} before spawn completed.',
                        now,
                        now,
                        pending_spawn["id"],
                    ),
                )
                cancelled_spawns += 1
            if action == "cli_takeover":
                await db.execute(
                    """
                    UPDATE agents
                    SET status = 'stopped',
                        status_note = ?,
                        launch_mode = 'none',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (
                        "Paused for direct CLI takeover. Close the CLI session and use Sessions -> Recover/Restart to return control to the dashboard.",
                        now,
                        agent_id,
                    ),
                )
            else:
                await db.execute(
                    "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END, last_seen = ? WHERE id = ?",
                    (now, agent_id),
                )
        else:
            await db.execute(
                "UPDATE agents SET status = CASE WHEN status = 'stopped' THEN status ELSE 'idle' END, last_seen = ? WHERE id = ?",
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agent_sessions WHERE id = ?", (session_id,))).fetchone()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("session_control_requested", {"sessionId": session_id, "agentId": agent_id, "action": action})
            if spawn_request_row:
                await ws.broadcast(
                    "spawn_request_created",
                    {"spawnRequestId": spawn_request_row["id"], "environmentId": spawn_request_row["environment_id"]},
                )
        return {
            "ok": True,
            "action": action,
            "session": _agent_session_to_dict(updated),
            "interruptControlId": control_id,
            "cancelledSpawns": cancelled_spawns,
            "spawnRequest": _spawn_request_to_dict(spawn_request_row, _spawn_spec_to_dict(spawn_spec_row) if spawn_spec_row else None) if spawn_request_row else None,
        }
    finally:
        await db.close()


# ─── Agents ──────────────────────────────────────────────────────────────────

@router.get("/agents")
async def list_agents(request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        idle_minutes = settings.get("idle_minutes", 5)
        offline_minutes = settings.get("offline_minutes", 30)

        cursor = await db.execute("SELECT * FROM agents")
        agents = await cursor.fetchall()
        result = {}
        for a in agents:
            aid = a["id"]
            c = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (aid, aid)
            )
            unread = (await c.fetchone())[0]
            status = await _compute_agent_status(a, idle_minutes, offline_minutes)
            dispatch_state = await _get_dispatch_state_for_agent(db, aid)
            result[aid] = _agent_record_to_dict(a, status, unread, dispatch_state)
        return {"agents": result}
    finally:
        await db.close()


@router.post("/agents")
async def register_agent(req: AgentRegister, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        normalized_runtime = _normalize_runtime(req.runtime or "generic")
        normalized_session_mode = _normalize_session_mode(req.sessionMode or "resident")
        resolved_cwd = req.cwd or ""
        runtime_config = req.runtimeConfig or {}
        _validate_registration_cwd(
            agent_id=req.agentId,
            runtime=normalized_runtime,
            session_mode=normalized_session_mode,
            machine_id=req.machineId or "",
            cwd=resolved_cwd,
            runtime_config=runtime_config,
        )
        now = _now()
        tombstone = await _agent_tombstone(db, req.agentId)
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
        if tombstone and req.restoreDeleted:
            await db.execute("DELETE FROM agent_tombstones WHERE agent_id = ?", (req.agentId,))
        existing = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        row = await existing.fetchone()
        # Re-register is a full state refresh: sessionHandle and runtime_state come
        # from the new request only. Preserving them across re-register let stale
        # Codex thread IDs survive a fresh codex-aify start, which then made
        # thread/resume fail with AbsolutePathBuf or "no rollout found".
        session_handle = req.sessionHandle or ""
        existing_state = "{}"
        # Description is team-facing metadata that survives re-register when the
        # caller does not pass a new value. Passing "" explicitly clears it.
        if req.description is None:
            description_value = (row["description"] if row and "description" in row.keys() else "") or ""
        else:
            description_value = req.description
        capabilities = req.capabilities
        if capabilities is None:
            capabilities = _default_capabilities_for(normalized_runtime, normalized_session_mode, session_handle, req.runtimeConfig or {})
        bridge_id = (req.bridgeId or "").strip()
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                role = excluded.role,
                name = excluded.name,
                cwd = excluded.cwd,
                model = excluded.model,
                description = excluded.description,
                instructions = excluded.instructions,
                status = excluded.status,
                status_note = excluded.status_note,
                runtime = excluded.runtime,
                machine_id = excluded.machine_id,
                launch_mode = excluded.launch_mode,
                session_mode = excluded.session_mode,
                session_handle = excluded.session_handle,
                managed_by = excluded.managed_by,
                capabilities = excluded.capabilities,
                runtime_config = excluded.runtime_config,
                runtime_state = excluded.runtime_state,
                last_seen = excluded.last_seen
            """,
            (
                req.agentId, req.role, req.name or req.agentId, resolved_cwd, req.model or "",
                description_value, req.instructions or "", req.status or "idle",
                (row["status_note"] if row and "status_note" in row.keys() else "") or "",
                normalized_runtime,
                req.machineId or "", req.launchMode or "detached",
                normalized_session_mode, session_handle, req.managedBy or "",
                json.dumps(capabilities or []), json.dumps(runtime_config),
                existing_state, row["registered_at"] if row and row["registered_at"] else now, now
            )
        )
        if session_handle:
            app_server_url = ""
            if isinstance(runtime_config, dict):
                app_server_url = str(runtime_config.get("appServerUrl") or "").strip()
            session_runtime_state = _runtime_state_with_handle(normalized_runtime, {}, session_handle)
            await db.execute(
                """
                UPDATE agent_sessions
                SET session_handle = ?,
                    app_server_url = CASE WHEN ? != '' THEN ? ELSE app_server_url END,
                    last_seen = ?,
                    capabilities = CASE
                        WHEN COALESCE(NULLIF(capabilities, ''), '{}') = '{}' THEN ?
                        ELSE capabilities
                    END,
                    telemetry = CASE
                        WHEN COALESCE(NULLIF(telemetry, ''), '{}') = '{}' THEN ?
                        ELSE telemetry
                    END
                WHERE id = (
                    SELECT id
                    FROM agent_sessions
                    WHERE agent_id = ?
                      AND runtime = ?
                    ORDER BY last_seen DESC
                    LIMIT 1
                )
                """,
                (
                    session_handle,
                    app_server_url,
                    app_server_url,
                    now,
                    json.dumps({"persistent": True, "nativeResume": True, "bridgeResume": True, "cliAttach": True}),
                    json.dumps({"registeredHandle": session_runtime_state}),
                    req.agentId,
                    normalized_runtime,
                ),
            )
        if bridge_id:
            await db.execute(
                """
                INSERT OR REPLACE INTO bridge_instances (
                    id, agent_id, machine_id, runtime, session_mode, registered_at, last_seen, superseded_by, superseded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    bridge_id,
                    req.agentId,
                    req.machineId or "",
                    normalized_runtime,
                    normalized_session_mode,
                    now,
                    now,
                    "",
                    None,
                )
            )
            await db.execute(
                """
                UPDATE bridge_instances
                SET superseded_by = ?, superseded_at = ?
                WHERE agent_id = ? AND machine_id = ? AND id != ? AND superseded_by = ''
                """,
                (bridge_id, now, req.agentId, req.machineId or "", bridge_id)
            )
            await _fail_active_runs_for_superseded_bridges(
                db,
                agent_id=req.agentId,
                machine_id=req.machineId or "",
                superseding_bridge_id=bridge_id,
                finished_at=now,
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_registered", {
                "agentId": req.agentId,
                "role": req.role,
                "runtime": normalized_runtime,
                "machineId": req.machineId or "",
                "sessionMode": normalized_session_mode,
            })
        return {
            "ok": True,
            "agentId": req.agentId,
            "role": req.role,
            "status": req.status or "idle",
            "runtime": normalized_runtime,
            "machineId": req.machineId or "",
            "bridgeId": bridge_id,
            "sessionMode": normalized_session_mode,
        }
    finally:
        await db.close()


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        settings = await _load_settings(db)
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            tombstone = await _agent_tombstone(db, agent_id)
            if tombstone:
                raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        uc = await db.execute(
            "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts rr ON m.id = rr.message_id AND rr.agent_id = ? WHERE m.to_agent = ? AND rr.message_id IS NULL",
            (agent_id, agent_id)
        )
        unread = (await uc.fetchone())[0]
        status = await _compute_agent_status(row, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30))
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        return {"ok": True, "agentId": agent_id, "agent": _agent_record_to_dict(row, status, unread, dispatch_state)}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/rename")
async def rename_agent(agent_id: str, req: AgentRenameRequest, request: Request):
    validate_name(agent_id, "agent ID")
    new_agent_id = str(req.newAgentId or "").strip()
    validate_name(new_agent_id, "new agent ID")
    if new_agent_id == agent_id:
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": False}

    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        existing = await (await db.execute("SELECT id FROM agents WHERE id = ?", (new_agent_id,))).fetchone()
        if existing:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" already exists')
        tombstone = await _agent_tombstone(db, new_agent_id)
        if tombstone:
            await db.rollback()
            raise HTTPException(409, f'Agent "{new_agent_id}" was intentionally removed before; clear that ID before reusing it')

        now = _now()
        await db.execute(
            """
            INSERT INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note,
                runtime, machine_id, launch_mode, session_mode, session_handle, managed_by,
                capabilities, runtime_config, runtime_state, registered_at, last_seen
            )
            SELECT ?, role, CASE WHEN name = id THEN ? ELSE name END, cwd, model, description,
                   instructions, status, status_note, runtime, machine_id, launch_mode,
                   session_mode, session_handle, managed_by, capabilities, runtime_config,
                   runtime_state, registered_at, ?
            FROM agents
            WHERE id = ?
            """,
            (new_agent_id, new_agent_id, now, agent_id),
        )
        for table, column in (
            ("agent_sessions", "agent_id"),
            ("spawn_specs", "agent_id"),
            ("spawn_requests", "agent_id"),
            ("bridge_instances", "agent_id"),
            ("read_receipts", "agent_id"),
            ("channel_members", "agent_id"),
        ):
            await db.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE messages SET to_agent = ? WHERE to_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE shared_artifacts SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_runs SET target_agent = ? WHERE target_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE dispatch_controls SET from_agent = ? WHERE from_agent = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE channels SET created_by = ? WHERE created_by = ?", (new_agent_id, agent_id))
        await db.execute("UPDATE agents SET managed_by = ? WHERE managed_by = ?", (new_agent_id, agent_id))
        await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.execute(
            """
            INSERT OR REPLACE INTO agent_tombstones (agent_id, removed_at, removed_by, bridge_id, reason)
            VALUES (?,?,?,?,?)
            """,
            (agent_id, now, req.requestedBy or "dashboard", "", f"renamed_to:{new_agent_id}"),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_renamed", {"oldAgentId": agent_id, "newAgentId": new_agent_id})
        return {"ok": True, "agentId": agent_id, "newAgentId": new_agent_id, "changed": True}
    except Exception:
        try:
            await db.rollback()
        except Exception:
            pass
        raise
    finally:
        await db.close()


@router.post("/agents/{agent_id}/environment")
async def assign_agent_environment(agent_id: str, req: AgentEnvironmentAssignRequest, request: Request):
    validate_name(agent_id, "agent ID")
    environment_id = str(req.environmentId or "").strip()
    if not environment_id:
        raise HTTPException(400, "environmentId is required")

    db = await get_db()
    try:
        agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await agent_cursor.fetchone()
        if not agent:
            raise HTTPException(404, f'Agent "{agent_id}" not found')
        env_cursor = await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))
        env_row = await env_cursor.fetchone()
        if not env_row:
            raise HTTPException(404, f'Environment "{environment_id}" not found')
        environment = _environment_record_to_dict(env_row)
        if str(environment.get("status") or "").lower() != "online":
            raise HTTPException(409, f'Environment "{environment_id}" is {environment.get("status") or "unknown"}, not online')

        runtime = _normalize_runtime(req.runtime or agent["runtime"] or "generic")
        if not _runtime_capability_for_environment(environment, runtime):
            raise HTTPException(400, f'Environment "{environment_id}" does not advertise runtime "{runtime}"')
        workspace, workspace_root = _workspace_for_environment(environment, req.workspace, agent["cwd"] or "")
        now = _now()
        previous_runtime = _normalize_runtime(agent["runtime"] or runtime)
        latest_session = await (await db.execute(
            """
            SELECT *
            FROM agent_sessions
            WHERE agent_id = ?
            ORDER BY
                CASE WHEN COALESCE(NULLIF(session_handle, ''), '') != '' THEN 0 ELSE 1 END,
                last_seen DESC
            LIMIT 1
            """,
            (agent_id,),
        )).fetchone()
        latest_session_handle = str((latest_session["session_handle"] if latest_session else "") or "").strip()
        agent_runtime_state = _json_loads_or(agent["runtime_state"], {})
        state_handle = _runtime_handle_from_state(previous_runtime, agent_runtime_state)
        preserve_handle = ""
        if previous_runtime == runtime:
            preserve_handle = str(agent["session_handle"] or latest_session_handle or state_handle or "").strip()
        preserved_runtime_state = _runtime_state_with_handle(runtime, {}, preserve_handle)

        spec_cursor = await db.execute(
            "SELECT * FROM spawn_specs WHERE agent_id = ? ORDER BY updated_at DESC LIMIT 1",
            (agent_id,),
        )
        spec = await spec_cursor.fetchone()
        if spec:
            spec_id = spec["id"]
            await db.execute(
                """
                UPDATE spawn_specs
                SET environment_id = ?, runtime = ?, workspace = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (environment_id, runtime, workspace, now, agent_id),
            )
        else:
            spec_id = f"spec_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO spawn_specs (
                    id, agent_id, environment_id, runtime, workspace, model, profile, mode,
                    system_prompt, standing_instructions, env_vars, channel_ids, budget_policy,
                    context_policy, restart_policy, metadata, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    spec_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    agent["model"] or "",
                    "",
                    "managed-warm",
                    "",
                    agent["instructions"] or "",
                    "{}",
                    "[]",
                    "{}",
                    "{}",
                    "{}",
                    json.dumps({"createdBy": req.requestedBy or "dashboard", "assignedFromDashboard": True}),
                    now,
                    now,
                ),
            )

        await db.execute(
            """
            UPDATE agent_sessions
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                session_handle = ?,
                spawn_spec_id = COALESCE(NULLIF(spawn_spec_id, ''), ?),
                status = CASE WHEN status IN ('starting','running','recovering','restarting') THEN 'lost' ELSE status END,
                ended_at = CASE WHEN status IN ('starting','running','recovering','restarting') THEN COALESCE(ended_at, ?) ELSE ended_at END,
                last_seen = ?
            WHERE agent_id = ?
            """,
            (environment_id, runtime, workspace, preserve_handle, spec_id, now, now, agent_id),
        )
        session_cursor = await db.execute(
            "SELECT id FROM agent_sessions WHERE agent_id = ? ORDER BY last_seen DESC LIMIT 1",
            (agent_id,),
        )
        existing_session = await session_cursor.fetchone()
        if not existing_session:
            session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
            await db.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, process_id, session_handle,
                    app_server_url, spawn_spec_id, spawn_request_id, capabilities, telemetry, status,
                    started_at, last_seen, ended_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session_id,
                    agent_id,
                    environment_id,
                    runtime,
                    workspace,
                    "managed-warm",
                    "",
                    preserve_handle,
                    "",
                    spec_id,
                    None,
                    json.dumps({"persistent": True, "nativeResume": bool(preserve_handle), "bridgeResume": True, "adopted": True}),
                    "{}",
                    "stopped",
                    now,
                    now,
                    now,
                ),
            )
        await db.execute(
            """
            UPDATE spawn_requests
            SET environment_id = ?,
                runtime = ?,
                workspace = ?,
                workspace_root = ?,
                updated_at = ?
            WHERE agent_id = ?
              AND status IN ('queued','claimed','starting')
            """,
            (environment_id, runtime, workspace, workspace_root, now, agent_id),
        )
        capabilities = _default_capabilities_for(runtime, "managed", preserve_handle)
        await db.execute(
            """
            UPDATE agents
            SET cwd = ?,
                runtime = ?,
                machine_id = ?,
                launch_mode = 'none',
                session_mode = 'managed',
                session_handle = ?,
                capabilities = ?,
                runtime_config = '{}',
                runtime_state = ?,
                status = CASE WHEN status = 'stopped' THEN status ELSE 'offline' END,
                last_seen = ?
            WHERE id = ?
            """,
            (
                workspace,
                runtime,
                environment.get("machineId") or "",
                preserve_handle,
                json.dumps(capabilities),
                json.dumps(preserved_runtime_state),
                now,
                agent_id,
            ),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_environment_assigned", {"agentId": agent_id, "environmentId": environment_id})
        return {
            "ok": True,
            "agentId": agent_id,
            "environmentId": environment_id,
            "runtime": runtime,
            "workspace": workspace,
            "spawnSpecId": spec_id,
        }
    finally:
        await db.close()


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        deleted = await _remove_agent_record(
            db,
            agent_id,
            removed_by="api",
            reason="delete_agent",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": deleted > 0, "agentId": agent_id}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/control")
async def control_agent(agent_id: str, req: AgentControlRequest, request: Request):
    action = str(req.action or "").strip().lower()
    if action not in {"interrupt", "stop", "resume"}:
        raise HTTPException(400, f'Unsupported agent control action "{req.action}"')

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))
        agent = await cursor.fetchone()
        if not agent:
            raise HTTPException(404, f"Agent '{agent_id}' not found")

        now = _now()
        active_run = await _get_blocking_active_run(db, agent_id)
        control_id = ""
        if action in {"interrupt", "stop"}:
            if active_run:
                control_id = await _append_dispatch_control(
                    db,
                    active_run["runId"],
                    from_agent=req.from_agent or "dashboard",
                    action="interrupt",
                    body=req.body or f"Agent {action} requested from dashboard.",
                )
            elif action == "interrupt":
                raise HTTPException(409, f'Agent "{agent_id}" has no active run to interrupt')

        cancelled_queued = 0
        if action == "stop":
            queued_cursor = await db.execute(
                "SELECT id FROM dispatch_runs WHERE target_agent = ? AND status = 'queued'",
                (agent_id,),
            )
            queued_rows = await queued_cursor.fetchall()
            for row in queued_rows:
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', summary = ?, finished_at = ? WHERE id = ?",
                    (f'Agent "{agent_id}" was stopped from the dashboard before the run could start.', now, row["id"]),
                )
                await _append_dispatch_event(db, row["id"], "agent_stopped", "Agent stopped from dashboard")
                cancelled_queued += 1
            await db.execute(
                """
                UPDATE agents
                SET status = 'stopped', status_note = ?, launch_mode = 'none', last_seen = ?
                WHERE id = ?
                """,
                ("Stopped from dashboard. Resume to allow wake/dispatch again.", now, agent_id),
            )
        elif action == "resume":
            await db.execute(
                """
                UPDATE agents
                SET status = 'idle', status_note = '', launch_mode = CASE WHEN launch_mode = 'none' THEN 'detached' ELSE launch_mode END,
                    last_seen = ?
                WHERE id = ?
                """,
                (now, agent_id),
            )

        await db.commit()
        updated = await (await db.execute("SELECT * FROM agents WHERE id = ?", (agent_id,))).fetchone()
        settings = await _load_settings(db)
        status = await _compute_agent_status(updated, settings.get("idle_minutes", 5), settings.get("offline_minutes", 30))
        dispatch_state = await _get_dispatch_state_for_agent(db, agent_id)
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast(
                "agent_control_requested",
                {"agentId": agent_id, "action": action, "controlId": control_id, "cancelledQueued": cancelled_queued},
            )
        return {
            "ok": True,
            "agentId": agent_id,
            "action": action,
            "controlId": control_id,
            "cancelledQueued": cancelled_queued,
            "agent": _agent_record_to_dict(updated, status, 0, dispatch_state),
        }
    finally:
        await db.close()


@router.patch("/agents/{agent_id}")
async def update_agent(agent_id: str, req: AgentStatusUpdate, request: Request):
    db = await get_db()
    try:
        note = getattr(req, 'note', None) or ''
        status_val = f"{req.status}: {note}" if note else req.status
        cursor = await db.execute(
            "UPDATE agents SET status = ?, status_note = ?, last_seen = ? WHERE id = ?",
            (req.status, note, _now(), agent_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_status", {"agentId": agent_id, "status": req.status})
        return {"ok": True, "agentId": agent_id, "status": status_val, "statusRaw": req.status, "statusNote": note}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/runtime-state")
async def update_agent_runtime_state(agent_id: str, req: AgentRuntimeStateUpdate, request: Request):
    db = await get_db()
    try:
        now = _now()
        cursor = await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(req.runtimeState or {}), now, agent_id)
        )
        await _touch_current_agent_session(db, agent_id, req.runtimeState or {}, now)
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        return {"ok": True, "agentId": agent_id, "runtimeState": req.runtimeState or {}}
    finally:
        await db.close()


async def _touch_current_agent_session(db, agent_id: str, runtime_state: dict[str, Any] | None, now: str) -> None:
    """Keep the dashboard backing record fresh when a managed runtime is used."""
    state = runtime_state or {}
    spawn_request_id = str(state.get("spawnRequestId") or "").strip()
    environment_id = str(state.get("environmentId") or "").strip()
    runtime_handle = str(state.get("sessionId") or state.get("threadId") or "").strip()
    if spawn_request_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE agent_id = ?
              AND spawn_request_id = ?
              AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
            """,
            (now, runtime_handle, runtime_handle, agent_id, spawn_request_id),
        )
        return
    if environment_id:
        await db.execute(
            """
            UPDATE agent_sessions
            SET last_seen = ?,
                session_handle = CASE WHEN ? != '' THEN ? ELSE session_handle END,
                status = CASE
                    WHEN status IN ('starting', 'recovering', 'restarting') THEN 'running'
                    ELSE status
                END
            WHERE id = (
                SELECT id
                FROM agent_sessions
                WHERE agent_id = ?
                  AND environment_id = ?
                  AND status NOT IN ('failed', 'lost', 'stopped', 'ended', 'completed', 'cancelled')
                ORDER BY last_seen DESC
                LIMIT 1
            )
            """,
            (now, runtime_handle, runtime_handle, agent_id, environment_id),
        )


# ─── Messages ────────────────────────────────────────────────────────────────

@router.post("/messages/send")
async def send_message(req: MessageSend, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        msg_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; message was sent unthreaded.'
            )

        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": []}

        launchable_recipients = []
        not_started = []
        dispatch_recipients = [r for r in recipients if r != "dashboard"]
        if req.trigger:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy) or str(req.type or "").strip().lower() == "response"
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                dispatch_recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            if not_started:
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                    elif r == "dashboard":
                        recipient_info[r] = {
                            "status": "active",
                            "unread": 0,
                            "runtime": "dashboard",
                            "machineId": "dashboard",
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "warnings": warnings,
                }

        linked_result_message_id = _primary_result_message_id(msg_id, recipients)

        for r in recipients:
            recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
            dispatch_requested = 1 if req.trigger and r != "dashboard" else 0
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (recipient_message_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, dispatch_requested, resolved_in_reply_to, ts)
            )

        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=linked_result_message_id,
                reply_type=req.type,
                reply_body=req.body,
            )
        else:
            for r in recipients:
                recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
                await _link_unthreaded_reply_to_recent_dispatch_run(
                    db,
                    from_agent=req.from_agent,
                    to_agent=r,
                    reply_message_id=recipient_message_id,
                    reply_type=req.type,
                    reply_timestamp_ms=ts,
                )

        dispatch_runs = []
        if req.trigger:
            require_reply = _dispatch_requires_reply(req.requireReply, default=req.type != "response")
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=msg_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=prefer_steer,
                require_reply=require_reply,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)

        # Gather recipient status info for sender context
        recipient_info = {}
        for r in recipients:
            info = await _get_recipient_info(db, r)
            if info:
                recipient_info[r] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }
            elif r == "dashboard":
                recipient_info[r] = {
                    "status": "active",
                    "unread": 0,
                    "runtime": "dashboard",
                    "machineId": "dashboard",
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_sent", {"id": msg_id, "from": req.from_agent, "to": recipients, "subject": req.subject})
            for r in recipients:
                await ws.notify_agent(r, "new_message", {"from": req.from_agent, "subject": req.subject})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        # Wake up any listening agents
        for r in recipients:
            _wake_agent(r)
        return {
            "ok": True,
            "messageId": msg_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.get("/messages/inbox/{agent_id}")
async def get_inbox(
    agent_id: str, request: Request,
    filter: str = Query("unread", pattern="^(unread|read|all)$"),
    fromAgent: Optional[str] = None, fromRole: Optional[str] = None,
    type: Optional[str] = None, limit: int = Query(200, ge=1, le=1000),
    mode: str = Query("full", pattern="^(full|headers)$"),
    messageId: Optional[str] = None,
    peek: Optional[str] = None,
):
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        include_body = mode != "headers"
        if messageId:
            base = """SELECT m.*, r.read_at FROM messages m
                      LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                      WHERE m.to_agent = ? AND m.id = ?"""
            params = [agent_id, agent_id, messageId]
        else:
            # Build query
            if filter == "unread":
                base = """SELECT m.*, NULL as read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ? AND r.message_id IS NULL"""
                params = [agent_id, agent_id]
            elif filter == "read":
                base = """SELECT m.*, r.read_at FROM messages m
                          JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]
            else:
                base = """SELECT m.*, r.read_at FROM messages m
                          LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ?
                          WHERE m.to_agent = ?"""
                params = [agent_id, agent_id]

        if fromAgent:
            base += " AND m.from_agent = ?"
            params.append(fromAgent)
        if fromRole:
            base += " AND m.from_agent IN (SELECT id FROM agents WHERE role = ?)"
            params.append(fromRole)
        if type:
            base += " AND m.type = ?"
            params.append(type)

        base += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(1 if messageId else limit)

        cursor = await db.execute(base, params)
        rows = await cursor.fetchall()

        # Count total (without limit)
        count_q = base.replace("SELECT m.*, NULL as read_at", "SELECT COUNT(*)").replace("SELECT m.*, r.read_at", "SELECT COUNT(*)")
        count_q = count_q[:count_q.rfind("LIMIT")]
        c = await db.execute(count_q, params[:-1])
        total = (await c.fetchone())[0]

        messages = []
        for row in rows:
            msg = _serialize_inbox_message(row, include_body=include_body)
            # Include parent message context for replies
            if row["in_reply_to"]:
                pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                parent = await pc.fetchone()
                if parent:
                    msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
            messages.append(msg)

        # Mark as read + update status (unless peek)
        if not peek:
            now = _now()
            unread_found = 0
            for msg in messages:
                if not msg["read"]:
                    unread_found += 1
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        (msg["id"], agent_id, now)
                    )
            # Complete stuck dispatch runs linked to messages we just read.
            # Only claimed/running (stuck from dead bridges) — NOT queued.
            # Queued dispatches should be left for the bridge to claim and
            # execute as a turn. Completing them here would prevent the wake.
            if unread_found > 0:
                read_msg_ids = [msg["id"] for msg in messages if not msg["read"]]
                for msg_id in read_msg_ids:
                    await db.execute(
                        """
                        UPDATE dispatch_runs
                        SET status = 'completed', summary = 'Message read via inbox', finished_at = ?
                        WHERE message_id = ? AND target_agent = ? AND status IN ('claimed', 'running')
                        """,
                        (now, msg_id, agent_id),
                    )

            # Smart status: got messages = working, no messages = idle
            new_status = "working" if unread_found > 0 else "idle"
            await db.execute(
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()

        return {"total": total, "showing": len(messages), "messages": messages}
    finally:
        await db.close()


@router.get("/messages/recent")
async def recent_messages(
    request: Request,
    limit: int = Query(80, ge=1, le=250),
):
    """Recent human-scale message activity without channel fanout duplicates."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM messages
            WHERE
              (source = 'direct' AND to_agent IS NOT NULL)
              OR (source = 'channel' AND to_agent IS NULL)
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        messages = []
        for row in await cursor.fetchall():
            messages.append({
                "id": row["id"],
                "from": row["from_agent"],
                "to": row["to_agent"],
                "channel": row["channel"],
                "source": row["source"],
                "type": row["type"],
                "subject": row["subject"],
                "preview": _clip_text(row["body"] or "", 240),
                "priority": row["priority"],
                "timestamp": row["timestamp"],
                "inReplyTo": row["in_reply_to"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
            })
        return {"ok": True, "messages": messages, "total": len(messages)}
    finally:
        await db.close()


@router.get("/messages/search")
async def search_messages(
    request: Request, query: str = "",
    agentId: Optional[str] = None,
    scope: str = Query("all", pattern="^(inbox|shared|all)$"),
    limit: int = Query(10, ge=1, le=100),
):
    db = await get_db()
    try:
        q = f"%{query.lower()}%"
        results = []

        if agentId and scope in ("inbox", "all"):
            cursor = await db.execute(
                "SELECT * FROM messages WHERE to_agent = ? AND (LOWER(subject) LIKE ? OR LOWER(body) LIKE ? OR LOWER(from_agent) LIKE ?) ORDER BY timestamp DESC LIMIT ?",
                (agentId, q, q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "message", "id": row["id"], "from": row["from_agent"],
                    "subject": row["subject"], "preview": (row["body"] or "")[:150],
                })

        if scope in ("shared", "all"):
            cursor = await db.execute(
                "SELECT * FROM shared_artifacts WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT ?",
                (q, q, limit)
            )
            for row in await cursor.fetchall():
                results.append({
                    "type": "shared", "name": row["name"], "from": row["from_agent"],
                    "description": row["description"], "size": row["size"],
                })

        return {"results": results[:limit], "total": len(results)}
    finally:
        await db.close()


# ─── Agent Info ──────────────────────────────────────────────────────────────

@router.get("/agents/{agent_id}/last-read")
async def agent_last_read(agent_id: str, request: Request):
    """Get the last message this agent read — useful for checking if they've seen your message."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT m.*, r.read_at FROM read_receipts r JOIN messages m ON m.id = r.message_id WHERE r.agent_id = ? ORDER BY r.read_at DESC LIMIT 1",
            (agent_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return {"agentId": agent_id, "lastRead": None}
        return {"agentId": agent_id, "lastRead": {
            "messageId": row["id"], "from": row["from_agent"], "subject": row["subject"],
            "type": row["type"], "readAt": row["read_at"], "timestamp": row["timestamp"],
        }}
    finally:
        await db.close()


@router.post("/agents/{agent_id}/heartbeat")
async def agent_heartbeat(agent_id: str, request: Request):
    """Lightweight heartbeat — bridge poll loop calls this to signal liveness."""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    bridge_id = str(body.get("bridgeId", "") or "").strip()
    now = _now()
    db = await get_db()
    try:
        tombstone = await _agent_tombstone(db, agent_id)
        if tombstone:
            raise HTTPException(410, f"Agent '{agent_id}' was intentionally removed")
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status = 'stopped' THEN status ELSE 'active' END WHERE id = ?",
            (now, agent_id),
        )
        if bridge_id:
            await db.execute(
                "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                (now, bridge_id, agent_id),
            )
        await db.commit()
        return {"ok": True}
    finally:
        await db.close()


@router.patch("/agents/{agent_id}/description")
async def update_agent_description(agent_id: str, req: AgentDescribeRequest, request: Request):
    """Update an agent's team-facing description without re-registering."""
    validate_name(agent_id, "agent ID")
    description = str(req.description or "")
    if len(description) > 2000:
        raise HTTPException(400, "description must be 2000 chars or fewer")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT id FROM agents WHERE id = ?", (agent_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        await db.execute(
            "UPDATE agents SET description = ?, last_seen = ? WHERE id = ?",
            (description, _now(), agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_description_updated", {"agentId": agent_id, "description": description})
        return {"ok": True, "agentId": agent_id, "description": description}
    finally:
        await db.close()


@router.get("/agents/{agent_id}/listen")
async def listen_for_messages(agent_id: str, request: Request, timeout: int = Query(300, ge=1, le=600)):
    """Long-poll: blocks until agent has unread messages or timeout. Returns the messages."""
    validate_name(agent_id, "agent ID")

    # Set status to idle (waiting for work)
    db = await get_db()
    try:
        await db.execute("UPDATE agents SET status = 'idle', last_seen = ? WHERE id = ?", (_now(), agent_id))
        await db.commit()
    finally:
        await db.close()

    # Create/get wake-up event for this agent
    if agent_id not in _listen_events:
        _listen_events[agent_id] = asyncio.Event()
    event = _listen_events[agent_id]
    event.clear()

    # Poll for unread messages, waiting on the event
    deadline = time.time() + timeout
    while time.time() < deadline:
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL",
                (agent_id, agent_id)
            )
            unread = (await cursor.fetchone())[0]
            if unread > 0:
                # Fetch and return the messages (mark as read)
                now = _now()
                mc = await db.execute(
                    "SELECT m.* FROM messages m LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = ? WHERE m.to_agent = ? AND r.message_id IS NULL ORDER BY m.timestamp DESC",
                    (agent_id, agent_id)
                )
                rows = await mc.fetchall()
                messages = []
                for row in rows:
                    msg = {
                        "id": row["id"], "from": row["from_agent"], "type": row["type"],
                        "source": row["source"], "channel": row["channel"],
                        "subject": row["subject"], "body": row["body"],
                        "priority": row["priority"], "timestamp": row["timestamp"],
                        "inReplyTo": row["in_reply_to"],
                        "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                    }
                    # Parent context for replies
                    if row["in_reply_to"]:
                        pc = await db.execute("SELECT from_agent, subject, body FROM messages WHERE id = ?", (row["in_reply_to"],))
                        parent = await pc.fetchone()
                        if parent:
                            msg["parentContext"] = {"from": parent["from_agent"], "subject": parent["subject"], "preview": (parent["body"] or "")[:100]}
                    messages.append(msg)
                    await db.execute("INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)", (row["id"], agent_id, now))

                # Set status to working
                await db.execute("UPDATE agents SET status = 'working', last_seen = ? WHERE id = ?", (now, agent_id))
                await db.commit()
                return {"total": len(messages), "messages": messages}
        finally:
            await db.close()

        # Wait for wake-up signal or check every 2 seconds
        try:
            await asyncio.wait_for(event.wait(), timeout=2.0)
            event.clear()
        except asyncio.TimeoutError:
            pass

    # Timeout — no messages arrived
    return {"total": 0, "messages": []}


def _wake_agent(agent_id: str):
    """Signal a listening agent that they have new messages."""
    ev = _listen_events.get(agent_id)
    if ev:
        ev.set()


# ─── Dispatch Runs ────────────────────────────────────────────────────────────

@router.post("/dispatch")
async def create_dispatch(req: DispatchRequest, request: Request):
    if not req.to and not req.toRole:
        raise HTTPException(400, "Need 'to' or 'toRole'")
    if req.mode == "message_only":
        raise HTTPException(400, "Dispatch no longer supports mode='message_only'. Use comms_send for normal live messaging or comms_dispatch without message_only for tracked work.")

    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        resolved_in_reply_to, reply_parent_found = await _resolve_reply_parent_message_id(db, req.inReplyTo)
        warnings = []
        if req.inReplyTo and not reply_parent_found:
            warnings.append(
                f'inReplyTo "{req.inReplyTo}" did not match an existing message; dispatch was sent unthreaded.'
            )
        recipients = await _resolve_recipient_ids(db, to=req.to, to_role=req.toRole, from_agent=req.from_agent)

        if not recipients:
            return {"ok": False, "error": "No recipients found", "recipients": [], "runs": []}

        not_started = []
        launchable_recipients = []
        recipient_rows = {}
        for recipient_id in recipients:
            cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
            row = await cursor.fetchone()
            if row:
                recipient_rows[recipient_id] = row
            execution_mode = None
            reason = None if row else "agent is not registered"
            if row:
                execution_mode, reason = _agent_execution_mode(row, req.requestedRuntime)
            if reason or not execution_mode:
                not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
            else:
                launchable_recipients.append((recipient_id, execution_mode))

        if req.mode == "require_start" and not_started:
            details = "; ".join(f"{item['targetAgentId']}: {item['reason']}" for item in not_started)
            return {
                "ok": False,
                "error": f"Active dispatch unavailable for: {details}",
                "recipients": recipients,
                "runs": [],
                "notStarted": not_started,
            }

        message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        source_message_ids = {}
        ts = int(time.time() * 1000)
        for recipient_id in recipients:
            recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
            source_message_ids[recipient_id] = recipient_message_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    recipient_message_id,
                    req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                    req.priority, 1, resolved_in_reply_to, ts
                )
            )
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=_primary_result_message_id(message_id, recipients),
                reply_type=req.type,
                reply_body=req.body,
            )

        runs = []
        if launchable_recipients:
            require_reply = _dispatch_requires_reply(req.requireReply, default=True)
            runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=req.subject,
                body=req.body,
                priority=req.priority,
                in_reply_to=resolved_in_reply_to,
                dispatch_mode=req.mode,
                execution_mode="managed",
                requested_runtime=req.requestedRuntime,
                message_id=message_id if len(recipients) == 1 else None,
                source_message_ids=source_message_ids,
                steer=req.steer,
                require_reply=require_reply,
            )
            runs = await _finalize_dispatch_runs(db, runs, launchable_recipients, not_started)

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "dispatch_request", {"from": req.from_agent, "subject": req.subject})
            for run in runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        for recipient_id in recipients:
            _wake_agent(recipient_id)

        return {
            "ok": True,
            "messageId": message_id,
            "recipients": recipients,
            "recipientStatus": recipient_info,
            "runs": runs,
            "notStarted": not_started,
            "warnings": warnings,
        }
    finally:
        await db.close()


@router.post("/dispatch/claim")
async def claim_dispatch(req: DispatchClaimRequest, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            tombstone = await _agent_tombstone(db, req.agentId)
            if tombstone:
                await db.rollback()
                raise HTTPException(410, f"Agent '{req.agentId}' was intentionally removed")
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        if req.machineId and agent["machine_id"] and agent["machine_id"] != req.machineId:
            await db.rollback()
            return {"ok": True, "run": None}

        agent_runtime = _normalize_runtime(agent["runtime"] or "generic")

        # Reject claims from stale stdio bridges. The bridge_instances row
        # catches normal supersession, while runtimeState.bridgeInstanceId
        # catches the more dangerous case where an old process keeps polling
        # after its bridge row has disappeared or been compacted away.
        blocked_by = await _bridge_claim_block_reason(
            db,
            bridge_id=req.bridgeId or "",
            agent_id=req.agentId,
            agent_row=agent,
        )
        if blocked_by:
            await db.commit()
            return {
                "ok": True,
                "run": None,
                "blockedBy": blocked_by,
            }

        # Update bridge liveness — the claim poll itself is the heartbeat.
        if req.bridgeId:
            await db.execute(
                "UPDATE bridge_instances SET last_seen = ? WHERE id = ? AND agent_id = ?",
                (_now(), req.bridgeId, req.agentId),
            )

        # Stale-run cleanup.
        #
        # The bridge-side gate (ACTIVE_RUNS in server.js) prevents a live
        # bridge from calling /dispatch/claim while it has work in flight.
        # Therefore: if this bridge IS calling claim, it has no local active
        # run. Any DB-level "active" row for this agent is either:
        #   (a) owned by THIS bridge (same bridgeId) — a bridge-side bug;
        #       return blockedBy as a safety net.
        #   (b) owned by a DIFFERENT bridge (or unowned) — stale by
        #       definition, because the owning bridge would not be polling
        #       if it were alive and busy. Clean it up and proceed.
        active_state = await _get_dispatch_state_for_agent(db, req.agentId)
        active_run = active_state.get("activeRun")
        if active_run:
            owner = (active_run.get("claimBridgeId") or "").strip()
            if owner and owner == req.bridgeId:
                await db.commit()
                return {"ok": True, "run": None, "blockedBy": active_run}
            active_since = _iso_to_epoch(active_run.get("startedAt") or active_run.get("requestedAt"))
            active_age = time.time() - active_since if active_since else ACTIVE_RUN_BRIDGE_STALE_SECONDS + 1
            if active_age < ACTIVE_RUN_BRIDGE_STALE_SECONDS:
                await db.commit()
                return {
                    "ok": True,
                    "run": None,
                    "blockedBy": {
                        **active_run,
                        "reason": "active_run_owned_by_previous_bridge",
                        "ownerBridgeId": owner or "",
                        "currentBridgeId": req.bridgeId or "",
                        "retryAfterSeconds": max(1, int(ACTIVE_RUN_BRIDGE_STALE_SECONDS - active_age)),
                        "hint": "A previous bridge claimed this run recently. Waiting avoids killing a run that may still complete.",
                    },
                }
            finished_at = _now()
            owner_label = owner or "unowned"
            await db.execute(
                "UPDATE dispatch_runs SET status = 'failed', summary = ?, finished_at = ? WHERE id = ?",
                (
                    f'Auto-healed: bridge "{owner_label}" replaced by "{req.bridgeId}"',
                    finished_at,
                    active_run["runId"],
                ),
            )
            await _append_dispatch_event(db, active_run["runId"], "auto_heal", f"Stale run cleanup: {owner_label} -> {req.bridgeId}")
            await _fail_pending_controls_for_run(db, active_run["runId"], handled_at=finished_at, response_text=f'Stale run cleaned by live bridge "{req.bridgeId}".')
        run_cursor = await db.execute(
            """
            SELECT * FROM dispatch_runs
            WHERE target_agent = ? AND status = 'queued'
            ORDER BY requested_at ASC
            LIMIT 25
            """,
            (req.agentId,)
        )
        runs = await run_cursor.fetchall()
        selected_run = None
        supported_modes = {str(mode or "").strip().lower() for mode in (req.executionModes or []) if str(mode or "").strip()}
        for run in runs:
            run_execution_mode = (run["execution_mode"] or "managed").strip().lower()
            if supported_modes and run_execution_mode not in supported_modes:
                continue
            if run["dispatch_mode"] == "message_only":
                await db.execute(
                    "UPDATE dispatch_runs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (_now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", "Dispatch mode is message_only")
                continue
            requested_runtime = run["requested_runtime"] or ""
            if requested_runtime and _normalize_runtime(requested_runtime) != agent_runtime:
                continue

            execution_mode, reason = _agent_execution_mode(agent, requested_runtime or None)
            if reason or not execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason or "active dispatch unavailable", _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason or "active dispatch unavailable")
                continue
            if (run["execution_mode"] or execution_mode) != execution_mode:
                final_status = "failed" if run["dispatch_mode"] == "require_start" else "cancelled"
                reason = (
                    f'Run execution mode "{run["execution_mode"] or "unknown"}" does not match the '
                    f'current capabilities of agent "{req.agentId}" ({execution_mode}).'
                )
                await db.execute(
                    "UPDATE dispatch_runs SET status = ?, error_text = ?, finished_at = ? WHERE id = ?",
                    (final_status, reason, _now(), run["id"])
                )
                await _append_dispatch_event(db, run["id"], "skipped", reason)
                continue

            selected_run = run
            break

        if not selected_run:
            await db.commit()
            return {"ok": True, "run": None}

        claimed_at = _now()
        await db.execute(
            "UPDATE dispatch_runs SET status = 'claimed', claimed_at = ?, claim_machine_id = ?, claim_bridge_id = ?, runtime = ? WHERE id = ?",
            (claimed_at, req.machineId or "", req.bridgeId or "", agent_runtime, selected_run["id"])
        )
        await _touch_current_agent_session(
            db,
            req.agentId,
            _json_loads_or(agent["runtime_state"], {}),
            claimed_at,
        )
        marked_read = await _mark_dispatch_source_messages_read(db, selected_run, req.agentId, claimed_at)
        await _append_dispatch_event(db, selected_run["id"], "claimed", f"machine={req.machineId or ''}")
        if marked_read > 1:
            await _append_dispatch_event(db, selected_run["id"], "read_receipts", f"Marked {marked_read} dispatched source messages read")
        await db.commit()

        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_claimed", {"runId": selected_run["id"], "targetAgentId": req.agentId})

        return {
            "ok": True,
            "run": {
                "id": selected_run["id"],
                "messageId": selected_run["message_id"],
                "from": selected_run["from_agent"],
                "targetAgentId": selected_run["target_agent"],
                "type": selected_run["message_type"],
                "subject": selected_run["subject"],
                "body": selected_run["body"],
                "priority": selected_run["priority"],
                "inReplyTo": selected_run["in_reply_to"],
                "status": "claimed",
                "mode": selected_run["dispatch_mode"],
                "executionMode": selected_run["execution_mode"] or "managed",
                "requireReply": _row_require_reply(selected_run),
                "conversationContext": await _dispatch_conversation_context(db, selected_run),
                "claimBridgeId": req.bridgeId or "",
                "requestedRuntime": selected_run["requested_runtime"] or None,
                "claimedAt": claimed_at,
            }
        }
    finally:
        await db.close()


@router.get("/dispatch/runs")
async def list_dispatch_runs(
    request: Request,
    agentId: Optional[str] = None,
    fromAgent: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
):
    db = await get_db()
    try:
        query = "SELECT * FROM dispatch_runs WHERE 1=1"
        params = []
        if agentId:
            query += " AND target_agent = ?"
            params.append(agentId)
        if fromAgent:
            query += " AND from_agent = ?"
            params.append(fromAgent)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY requested_at DESC LIMIT ?"
        params.append(limit)
        cursor = await db.execute(query, params)
        runs = []
        for row in await cursor.fetchall():
            blocked_by = None
            if row["status"] == "queued":
                blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
            payload = _serialize_dispatch_run_row(row, blocked_by=blocked_by)
            controls_cursor = await db.execute(
                """
                SELECT id, action, status, source_message_id, response_text
                FROM dispatch_controls
                WHERE run_id = ? AND source_message_id != ''
                ORDER BY requested_at ASC
                LIMIT 50
                """,
                (row["id"],),
            )
            source_controls = [
                {
                    "id": control["id"],
                    "action": control["action"],
                    "status": control["status"],
                    "sourceMessageId": control["source_message_id"],
                    "response": control["response_text"] or "",
                }
                for control in await controls_cursor.fetchall()
            ]
            if source_controls:
                payload["sourceControls"] = source_controls
            runs.append(payload)
        return {"runs": runs}
    finally:
        await db.close()


@router.get("/dispatch/runs/{run_id}")
async def get_dispatch_run(run_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")
        ec = await db.execute(
            "SELECT event_type, body, created_at FROM dispatch_events WHERE run_id = ? ORDER BY id ASC LIMIT 200",
            (run_id,)
        )
        events = [
            {"type": event["event_type"], "body": event["body"], "createdAt": event["created_at"]}
            for event in await ec.fetchall()
        ]
        cc = await db.execute(
            """
            SELECT id, from_agent, action, body, status, response_text, source_message_id, requested_at, claimed_at, handled_at
            FROM dispatch_controls WHERE run_id = ? ORDER BY requested_at ASC LIMIT 200
            """,
            (run_id,)
        )
        controls = [
            {
                "id": control["id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "status": control["status"],
                "response": control["response_text"],
                "sourceMessageId": control["source_message_id"],
                "requestedAt": control["requested_at"],
                "claimedAt": control["claimed_at"],
                "handledAt": control["handled_at"],
            }
            for control in await cc.fetchall()
        ]
        blocked_by = None
        if row["status"] == "queued":
            blocked_by = await _get_blocking_active_run(db, row["target_agent"], row["id"])
        return {
            "run": _serialize_dispatch_run_row(
                row,
                blocked_by=blocked_by,
                include_body=True,
                include_events=events,
                include_controls=controls,
            )
        }
    finally:
        await db.close()


@router.post("/dispatch/handoffs/repair")
async def repair_dispatch_handoffs(request: Request, limit: int = Query(100, ge=1, le=500)):
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT *
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
            ORDER BY requested_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        mirrored = []
        skipped_delivery_only = 0
        skipped = 0
        for row in rows:
            if _is_delivery_only_claude_run(row):
                skipped_delivery_only += 1
                continue
            message_id = await _mirror_missing_dispatch_handoff(db, row)
            if message_id:
                mirrored.append({"runId": row["id"], "messageId": message_id})
            else:
                skipped += 1

        await db.commit()
        ws = await _get_ws(request)
        if ws and mirrored:
            await ws.broadcast("dispatch_handoffs_repaired", {"mirrored": len(mirrored)})
        return {
            "ok": True,
            "mirrored": len(mirrored),
            "skippedDeliveryOnly": skipped_delivery_only,
            "skipped": skipped,
            "runs": mirrored,
        }
    finally:
        await db.close()


@router.patch("/dispatch/runs/{run_id}")
async def update_dispatch_run(run_id: str, req: DispatchRunUpdate, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Run '{run_id}' not found")

        updates = []
        params = []
        now = _now()

        if req.status:
            updates.append("status = ?")
            params.append(req.status)
            if req.status == "running" and not row["started_at"]:
                updates.append("started_at = ?")
                params.append(now)
            if req.status in _DISPATCH_TERMINAL_STATUSES:
                updates.append("finished_at = ?")
                params.append(now)
        if req.summary is not None:
            updates.append("summary = ?")
            params.append(req.summary)
        if req.error is not None:
            updates.append("error_text = ?")
            params.append(req.error)
        if req.resultMessageId is not None:
            normalized_result_message_id = str(req.resultMessageId or "").strip()
            if normalized_result_message_id or not str(row["result_message_id"] or "").strip():
                updates.append("result_message_id = ?")
                params.append(normalized_result_message_id)
        if req.externalThreadId is not None:
            updates.append("external_thread_id = ?")
            params.append(req.externalThreadId)
        if req.externalTurnId is not None:
            updates.append("external_turn_id = ?")
            params.append(req.externalTurnId)
        if req.runtime is not None:
            updates.append("runtime = ?")
            params.append(req.runtime)

        if updates:
            params.append(run_id)
            await db.execute(f"UPDATE dispatch_runs SET {', '.join(updates)} WHERE id = ?", params)
            if req.status in ("completed", "failed", "cancelled"):
                await _fail_pending_controls_for_run(
                    db,
                    run_id,
                    handled_at=now,
                    response_text=f'Run ended with status "{req.status}" before the control could be handled.',
                )
                refreshed_cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
                refreshed_row = await refreshed_cursor.fetchone()
                await _mirror_missing_dispatch_handoff(db, refreshed_row)

        if req.agentStatus:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
                (req.agentStatus, now, row["target_agent"])
            )
            agent_row = await (await db.execute("SELECT runtime_state FROM agents WHERE id = ?", (row["target_agent"],))).fetchone()
            await _touch_current_agent_session(
                db,
                row["target_agent"],
                _json_loads_or(agent_row["runtime_state"], {}) if agent_row else {},
                now,
            )

        if req.appendEvent:
            await _append_dispatch_event(db, run_id, req.eventType or "info", req.appendEvent)

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_updated", {"runId": run_id, "status": req.status or row["status"]})
        return {"ok": True, "runId": run_id}
    finally:
        await db.close()


@router.post("/dispatch/runs/{run_id}/control")
async def request_dispatch_control(run_id: str, req: DispatchControlRequest, request: Request):
    action = (req.action or "").strip().lower()
    if action not in {"interrupt", "steer"}:
        raise HTTPException(400, "Unsupported control action")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_runs WHERE id = ?", (run_id,))
        run = await cursor.fetchone()
        if not run:
            raise HTTPException(404, f"Run '{run_id}' not found")
        if run["status"] not in {"claimed", "running"}:
            raise HTTPException(409, f"Run '{run_id}' is not active")

        control_id = await _append_dispatch_control(
            db,
            run_id,
            from_agent=req.from_agent or "",
            action=action,
            body=req.body or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_requested", {"runId": run_id, "controlId": control_id, "action": action})
        return {"ok": True, "controlId": control_id, "runId": run_id, "action": action, "status": "pending"}
    finally:
        await db.close()


@router.post("/dispatch/controls/claim")
async def claim_dispatch_controls(req: DispatchControlClaimRequest, request: Request):
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        agent = await cursor.fetchone()
        if not agent:
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        machine_id = req.machineId or ""
        if machine_id and agent["machine_id"] and agent["machine_id"] != machine_id:
            await db.rollback()
            return {"ok": True, "controls": []}

        # Claim pending controls for this agent. No filter on run status —
        # Claude resident runs complete immediately on delivery, so their
        # controls would never be claimable under the old ('claimed','running')
        # filter. The channel bridge polls for controls independently and
        # delivers them as notifications regardless of run state.
        controls_cursor = await db.execute(
            """
            SELECT dc.*, dr.target_agent, dr.status as run_status
            FROM dispatch_controls dc
            JOIN dispatch_runs dr ON dr.id = dc.run_id
            WHERE dr.target_agent = ? AND dc.status = 'pending'
              AND (? = '' OR dc.run_id = ?)
            ORDER BY dc.requested_at ASC, dc.id ASC
            LIMIT 20
            """,
            (req.agentId, req.runId or "", req.runId or "")
        )
        controls = await controls_cursor.fetchall()
        if not controls:
            await db.commit()
            return {"ok": True, "controls": []}

        claimed_at = _now()
        results = []
        for control in controls:
            await db.execute(
                "UPDATE dispatch_controls SET status = 'claimed', claim_machine_id = ?, claimed_at = ? WHERE id = ?",
                (machine_id, claimed_at, control["id"])
            )
            results.append({
                "id": control["id"],
                "runId": control["run_id"],
                "from": control["from_agent"],
                "action": control["action"],
                "body": control["body"],
                "requestedAt": control["requested_at"],
                "claimedAt": claimed_at,
            })

        await db.commit()
        return {"ok": True, "controls": results}
    finally:
        await db.close()


@router.patch("/dispatch/controls/{control_id}")
async def update_dispatch_control(control_id: str, req: DispatchControlUpdate, request: Request):
    if req.status not in {"completed", "failed"}:
        raise HTTPException(400, "Unsupported control status")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM dispatch_controls WHERE id = ?", (control_id,))
        control = await cursor.fetchone()
        if not control:
            raise HTTPException(404, f"Control '{control_id}' not found")

        handled_at = _now()
        await db.execute(
            "UPDATE dispatch_controls SET status = ?, response_text = ?, handled_at = ? WHERE id = ?",
            (req.status, req.response or "", handled_at, control_id)
        )
        if req.status == "completed" and (control["source_message_id"] or "").strip():
            run_cursor = await db.execute(
                "SELECT target_agent FROM dispatch_runs WHERE id = ?",
                (control["run_id"],),
            )
            run = await run_cursor.fetchone()
            if run and (run["target_agent"] or "").strip():
                msg_cursor = await db.execute(
                    "SELECT 1 FROM messages WHERE id = ?",
                    ((control["source_message_id"] or "").strip(),),
                )
                if await msg_cursor.fetchone():
                    await db.execute(
                        "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                        ((control["source_message_id"] or "").strip(), run["target_agent"], handled_at),
                    )
        await _append_dispatch_event(
            db,
            control["run_id"],
            f"control:{control['action']}:{req.status}",
            req.response or "",
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("dispatch_control_updated", {"controlId": control_id, "status": req.status})
        return {"ok": True, "controlId": control_id, "status": req.status}
    finally:
        await db.close()


@router.delete("/messages/{message_id}")
async def unsend_message(message_id: str, request: Request):
    """Delete a message by ID. Also removes associated read receipts."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        message_ids = [message_id]
        if (row["source"] or "") == "channel" and not (row["to_agent"] or ""):
            fanout_cursor = await db.execute(
                "SELECT id FROM messages WHERE id LIKE ? AND channel = ? AND source = 'channel'",
                (f"{message_id}-%", row["channel"] or ""),
            )
            message_ids.extend([fanout["id"] for fanout in await fanout_cursor.fetchall()])
        deleted = await _delete_messages_by_ids(db, message_ids)
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_deleted", {"id": message_id, "deleted": deleted})
        return {"ok": True, "id": message_id, "deleted": deleted}
    finally:
        await db.close()


@router.post("/messages/{message_id}/read")
async def set_message_read_state(message_id: str, request: Request):
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    read = bool(body.get("read", True))
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")

    db = await get_db()
    try:
        cursor = await db.execute("SELECT id, to_agent FROM messages WHERE id = ?", (message_id,))
        row = await cursor.fetchone()
        if not row:
            raise HTTPException(404, f"Message '{message_id}' not found")
        if row["to_agent"] != agent_id:
            raise HTTPException(403, f'Message "{message_id}" is not addressed to "{agent_id}"')

        if read:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (message_id, agent_id, _now()),
            )
        else:
            await db.execute(
                "DELETE FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                (message_id, agent_id),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("message_read_state", {"id": message_id, "agentId": agent_id, "read": read})
        return {"ok": True, "id": message_id, "agentId": agent_id, "read": read}
    finally:
        await db.close()


@router.post("/messages/conversation/clear")
async def clear_direct_conversation(req: ConversationClearRequest, request: Request):
    agent_id = str(req.agentId or "").strip()
    peer_id = str(req.peerId or "").strip()
    if not agent_id or not peer_id:
        raise HTTPException(400, "Need agentId and peerId")
    validate_name(agent_id, "agent ID")
    validate_name(peer_id, "peer agent ID")

    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            source = 'direct'
            AND channel IS NULL
            AND (
                (from_agent = ? AND to_agent = ?)
                OR (from_agent = ? AND to_agent = ?)
            )
            """,
            (agent_id, peer_id, peer_id, agent_id),
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("conversation_cleared", {"agentId": agent_id, "peerId": peer_id, "deleted": deleted})
        return {"ok": True, "agentId": agent_id, "peerId": peer_id, "deleted": deleted}
    finally:
        await db.close()


@router.post("/messages/cleanup/orphan-unread")
async def cleanup_orphan_unread_messages(request: Request):
    """Delete unread inbox messages addressed to removed agents."""
    db = await get_db()
    try:
        deleted = await _delete_messages_where(
            db,
            """
            id IN (
                SELECT m.id
                FROM messages m
                LEFT JOIN agents a ON a.id = m.to_agent
                LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = m.to_agent
                WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            )
            """,
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws and deleted:
            await ws.broadcast("messages_cleaned", {"kind": "orphan_unread", "deleted": deleted})
        return {"ok": True, "deleted": deleted}
    finally:
        await db.close()


# ─── Shared Artifacts ────────────────────────────────────────────────────────

@router.get("/shared")
async def list_shared(request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM shared_artifacts ORDER BY shared_at DESC")
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
        if file:
            shared_dir = _shared_dir(request)
            file_path = shared_dir / name
            data = await file.read()
            size = len(data)
            is_binary = True
            file_path.write_bytes(data)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, file_path, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, str(file_path), size, 1, now)
            )
        else:
            text = content or ""
            size = len(text)
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


@router.delete("/shared/{name}")
async def delete_shared(name: str, request: Request):
    validate_name(name, "artifact name")
    db = await get_db()
    try:
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


# ─── Channels ────────────────────────────────────────────────────────────────

@router.get("/channels")
async def list_channels(request: Request, agentId: Optional[str] = None):
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels")
        channels = []
        for ch in await cursor.fetchall():
            mc = await db.execute("SELECT COUNT(*) FROM channel_members WHERE channel_name = ?", (ch["name"],))
            member_count = (await mc.fetchone())[0]
            history_where, history_params = _normalize_channel_history_where(ch["name"])
            msg_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
            msg_count = (await msg_c.fetchone())[0]
            unread_count = 0
            if viewer_id:
                unread_c = await db.execute(
                    """
                    SELECT COUNT(*)
                    FROM messages m
                    LEFT JOIN read_receipts r ON r.message_id = m.id AND r.agent_id = ?
                    WHERE m.channel = ? AND m.to_agent = ? AND m.source = 'channel' AND r.message_id IS NULL
                    """,
                    (viewer_id, ch["name"], viewer_id),
                )
                unread_count = (await unread_c.fetchone())[0]
            channels.append({
                "name": ch["name"], "description": ch["description"],
                "createdBy": ch["created_by"], "createdAt": ch["created_at"],
                "members": [], "memberCount": member_count, "messageCount": msg_count,
                "unreadCount": unread_count,
            })
            # Fetch member list
            mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (ch["name"],))
            channels[-1]["members"] = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"channels": channels}
    finally:
        await db.close()


@router.post("/channels")
async def create_channel(req: ChannelCreate, request: Request):
    validate_name(req.name, "channel name")
    db = await get_db()
    try:
        now = _now()
        try:
            await db.execute(
                "INSERT INTO channels (name, description, created_by, created_at) VALUES (?,?,?,?)",
                (req.name, req.description or "", req.createdBy, now)
            )
        except Exception:
            raise HTTPException(409, f"Channel '{req.name}' already exists")
        await db.execute(
            "INSERT INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (req.name, req.createdBy, now)
        )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("channel_created", {"name": req.name})
        return {"ok": True, "channel": req.name}
    finally:
        await db.close()


@router.get("/channels/{name}")
async def get_channel(
    name: str,
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = 0,
    agentId: Optional[str] = None,
):
    validate_name(name, "channel name")
    viewer_id = str(agentId or "").strip()
    if viewer_id:
        validate_name(viewer_id, "agent ID")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        ch = await cursor.fetchone()
        if not ch:
            raise HTTPException(404, f"Channel '{name}' not found")

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]

        history_where, history_params = _normalize_channel_history_where(name)
        total_c = await db.execute(f"SELECT COUNT(*) FROM messages WHERE {history_where}", history_params)
        total = (await total_c.fetchone())[0]

        # Paginate newest first
        msg_c = await db.execute(
            f"SELECT * FROM messages WHERE {history_where} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            history_params + (limit, offset)
        )
        messages = []
        for row in await msg_c.fetchall():
            read = True
            fanout_id = ""
            if viewer_id and row["from_agent"] != viewer_id and row["from_agent"] != "_system":
                fanout_id = _channel_fanout_message_id(row["id"], viewer_id)
                read_cursor = await db.execute(
                    "SELECT 1 FROM read_receipts WHERE message_id = ? AND agent_id = ?",
                    (fanout_id, viewer_id),
                )
                read = bool(await read_cursor.fetchone())
            messages.append({
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "body": row["body"], "priority": row["priority"], "timestamp": row["timestamp"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
                "read": read,
                "fanoutMessageId": fanout_id,
            })
        # Reverse so oldest is first in the returned slice (chat order)
        messages.reverse()

        return {
            "name": ch["name"], "description": ch["description"],
            "members": members, "totalMessages": total, "messages": messages,
        }
    finally:
        await db.close()


@router.delete("/channels/{name}")
async def delete_channel(name: str, request: Request):
    db = await get_db()
    try:
        await db.execute("DELETE FROM channel_members WHERE channel_name = ?", (name,))
        await _delete_messages_where(db, "channel = ?", (name,))
        cursor = await db.execute("DELETE FROM channels WHERE name = ?", (name,))
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Channel '{name}' not found")
        return {"ok": True}
    finally:
        await db.close()


@router.post("/channels/{name}/join")
async def join_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE name = ?", (name,))
        if not await cursor.fetchone():
            raise HTTPException(404, f"Channel '{name}' not found")
        now = _now()
        await db.execute(
            "INSERT OR IGNORE INTO channel_members (channel_name, agent_id, joined_at) VALUES (?,?,?)",
            (name, req.agentId, now)
        )
        # System message
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} joined the channel", int(time.time()*1000))
        )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"ok": True, "members": members}
    finally:
        await db.close()


@router.post("/channels/{name}/leave")
async def leave_channel(name: str, req: ChannelJoin, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        await db.execute("DELETE FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.agentId))
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}", "_system", name, "channel", "info", f"#{name}", f"{req.agentId} left the channel", int(time.time()*1000))
        )
        await db.commit()
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        return {"ok": True, "members": members}
    finally:
        await db.close()


@router.post("/channels/{name}/read")
async def mark_channel_read(name: str, request: Request):
    validate_name(name, "channel name")
    body = await request.json()
    agent_id = str(body.get("agentId") or "").strip()
    if not agent_id:
        raise HTTPException(400, "Need agentId")
    validate_name(agent_id, "agent ID")
    db = await get_db()
    try:
        member_cursor = await db.execute(
            "SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?",
            (name, agent_id),
        )
        if not await member_cursor.fetchone():
            raise HTTPException(403, f'Agent "{agent_id}" is not a member of #{name}')
        now = _now()
        cursor = await db.execute(
            """
            SELECT id
            FROM messages
            WHERE channel = ? AND to_agent = ? AND source = 'channel'
            """,
            (name, agent_id),
        )
        rows = await cursor.fetchall()
        for row in rows:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (row["id"], agent_id, now),
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_read", {"channel": name, "agentId": agent_id, "count": len(rows)})
        return {"ok": True, "channel": name, "agentId": agent_id, "read": len(rows)}
    finally:
        await db.close()


@router.post("/channels/{name}/send")
async def send_channel_message(name: str, req: ChannelMessage, request: Request):
    validate_name(name, "channel name")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)

        # Verify membership
        cursor = await db.execute("SELECT 1 FROM channel_members WHERE channel_name = ? AND agent_id = ?", (name, req.from_agent))
        if not await cursor.fetchone():
            raise HTTPException(403, f"Agent '{req.from_agent}' is not a member of #{name}. Join first.")

        msg_id = f"{int(time.time()*1000)}-{uuid.uuid4().hex[:8]}"
        ts = int(time.time() * 1000)
        subject = f"#{name}: {req.body[:80]}"
        should_trigger = False if req.silent else req.trigger is not False

        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        recipients = []
        inbox_message_ids = {}
        suppressed_duplicates = []
        for member in members:
            if member == req.from_agent:
                continue
            if await _has_recent_direct_delivery_for_channel_fanout(
                db,
                from_agent=req.from_agent,
                recipient_id=member,
                message_type=req.type,
                body=req.body,
                timestamp_ms=ts,
            ):
                suppressed_duplicates.append(member)
                continue
            recipient_msg_id = f"{msg_id}-{member}"
            recipients.append(member)
            inbox_message_ids[member] = recipient_msg_id

        launchable_recipients = []
        not_started = []
        if should_trigger and recipients:
            prefer_steer = (req.steer is not False) and not bool(req.queueIfBusy)
            allow_queue_busy = bool(req.queueIfBusy)
            launchable_recipients, not_started = await _preflight_live_send_recipients(
                db,
                recipients,
                allow_steer=prefer_steer,
                allow_queue_busy=allow_queue_busy,
            )
            if not_started:
                recipient_info = {}
                for recipient_id in recipients:
                    info = await _get_recipient_info(db, recipient_id)
                    if info:
                        recipient_info[recipient_id] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Channel message was not sent because one or more recipients cannot start live work now.",
                    "members": members,
                    "recipients": recipients,
                    "suppressedDuplicates": suppressed_duplicates,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                }

        # Channel message (canonical)
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (msg_id, req.from_agent, name, "channel", req.type, subject, req.body, req.priority or "normal", 1 if should_trigger else 0, ts)
        )

        # Deliver to each member's inbox (except sender)
        for member in members:
            if member != req.from_agent:
                recipient_msg_id = inbox_message_ids.get(member)
                if not recipient_msg_id:
                    continue
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        recipient_msg_id, req.from_agent, member, name, "channel", req.type, subject,
                        req.body, req.priority or "normal", 1 if should_trigger else 0, ts
                    )
                )

        dispatch_runs = []
        if should_trigger and recipients:
            dispatch_runs = await _create_dispatch_runs(
                db,
                [recipient_id for recipient_id, _ in launchable_recipients],
                from_agent=req.from_agent,
                message_type=req.type,
                subject=subject,
                body=req.body,
                priority=req.priority or "normal",
                in_reply_to=None,
                dispatch_mode="start_if_possible",
                execution_mode="managed",
                requested_runtime=None,
                message_id=inbox_message_ids.get(recipients[0]) if len(recipients) == 1 else None,
                source_message_ids=inbox_message_ids,
                steer=prefer_steer,
                require_reply=False,
            )
            dispatch_runs = await _finalize_dispatch_runs(db, dispatch_runs, launchable_recipients, not_started)

        recipient_info = {}
        for recipient_id in recipients:
            info = await _get_recipient_info(db, recipient_id)
            if info:
                recipient_info[recipient_id] = {
                    "status": info["status"],
                    "unread": info["unread"],
                    "runtime": info["runtime"],
                    "machineId": info["machineId"],
                }

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("channel_message", {"channel": name, "from": req.from_agent, "body": req.body[:200]})
            for recipient_id in recipients:
                await ws.notify_agent(recipient_id, "new_message", {"from": req.from_agent, "subject": subject, "channel": name})
            for run in dispatch_runs:
                if run.get("steered"):
                    continue
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        # Wake up any listening members
        for member in members:
            if member != req.from_agent:
                _wake_agent(member)
        return {
            "ok": True,
            "messageId": msg_id,
            "members": members,
            "recipients": recipients,
            "suppressedDuplicates": suppressed_duplicates,
            "recipientStatus": recipient_info,
            "dispatchRuns": dispatch_runs,
            "notStarted": not_started,
        }
    finally:
        await db.close()


# ─── Settings ────────────────────────────────────────────────────────────────

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
            if key in DEFAULT_SETTINGS:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
                    (key, json.dumps(value))
                )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("settings_updated")
        return await get_settings(request)
    finally:
        await db.close()


# ─── Stats ───────────────────────────────────────────────────────────────────

@router.get("/stats")
async def get_stats(request: Request):
    db = await get_db()
    try:
        agents_c = await db.execute("SELECT COUNT(*) FROM agents")
        agents = (await agents_c.fetchone())[0]

        environments_c = await db.execute("SELECT COUNT(*) FROM environments WHERE status != 'forgotten'")
        environments = (await environments_c.fetchone())[0]

        spawn_c = await db.execute("SELECT status, COUNT(*) as cnt FROM spawn_requests GROUP BY status")
        spawn_by_status = {row["status"]: row["cnt"] for row in await spawn_c.fetchall()}

        sessions_c = await db.execute("SELECT COUNT(*) FROM agent_sessions WHERE status IN ('starting','running')")
        active_sessions = (await sessions_c.fetchone())[0]

        total_c = await db.execute("SELECT COUNT(*) FROM messages WHERE source = 'direct'")
        total = (await total_c.fetchone())[0]

        # Unread direct inbox messages for currently registered agents only
        unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND m.source = 'direct' AND r.message_id IS NULL
            """
        )
        unread = (await unread_c.fetchone())[0]

        channel_unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND m.source = 'channel' AND r.message_id IS NULL
            """
        )
        channel_unread = (await channel_unread_c.fetchone())[0]

        orphan_unread_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM messages m
            LEFT JOIN agents a ON a.id = m.to_agent
            LEFT JOIN read_receipts r ON m.id = r.message_id AND r.agent_id = m.to_agent
            WHERE m.to_agent IS NOT NULL AND a.id IS NULL AND r.message_id IS NULL
            """
        )
        orphan_unread = (await orphan_unread_c.fetchone())[0]

        # Today
        today_start = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")) * 1000)
        today_c = await db.execute("SELECT COUNT(*) FROM messages WHERE timestamp >= ?", (today_start,))
        today = (await today_c.fetchone())[0]
        since_24h_ms = int((time.time() - 24 * 60 * 60) * 1000)
        since_24h_iso = _iso_from_ms(since_24h_ms)
        direct_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'direct' AND timestamp >= ?",
            (since_24h_ms,),
        )
        direct_24h = (await direct_24h_c.fetchone())[0]
        channel_24h_c = await db.execute(
            "SELECT COUNT(*) FROM messages WHERE source = 'channel' AND to_agent IS NULL AND timestamp >= ?",
            (since_24h_ms,),
        )
        channel_24h = (await channel_24h_c.fetchone())[0]
        active_pairs_c = await db.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT
                    CASE WHEN from_agent < to_agent THEN from_agent ELSE to_agent END AS a,
                    CASE WHEN from_agent < to_agent THEN to_agent ELSE from_agent END AS b
                FROM messages
                WHERE source = 'direct'
                  AND to_agent IS NOT NULL
                  AND timestamp >= ?
                GROUP BY a, b
            )
            """,
            (since_24h_ms,),
        )
        active_pairs_24h = (await active_pairs_c.fetchone())[0]
        run_failures_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status IN ('failed','cancelled') AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        run_failures_24h = (await run_failures_24h_c.fetchone())[0]
        failed_spawns_24h_c = await db.execute(
            "SELECT COUNT(*) FROM spawn_requests WHERE status = 'failed' AND updated_at >= ?",
            (since_24h_iso,),
        )
        failed_spawns_24h = (await failed_spawns_24h_c.fetchone())[0]
        completed_runs_24h_c = await db.execute(
            "SELECT COUNT(*) FROM dispatch_runs WHERE status = 'completed' AND COALESCE(finished_at, requested_at) >= ?",
            (since_24h_iso,),
        )
        completed_runs_24h = (await completed_runs_24h_c.fetchone())[0]

        # By type
        type_c = await db.execute("SELECT type, COUNT(*) as cnt FROM messages WHERE source = 'direct' GROUP BY type")
        by_type = {row["type"]: row["cnt"] for row in await type_c.fetchall()}

        # By agent
        agent_c = await db.execute("SELECT to_agent, COUNT(*) as cnt FROM messages WHERE to_agent IS NOT NULL GROUP BY to_agent")
        by_agent = {row["to_agent"]: row["cnt"] for row in await agent_c.fetchall()}

        # Shared
        shared_c = await db.execute("SELECT COUNT(*) as cnt, COALESCE(SUM(size),0) as total_size FROM shared_artifacts")
        shared_row = await shared_c.fetchone()

        dispatch_c = await db.execute(
            """
            SELECT status, COUNT(*) as cnt
            FROM dispatch_runs
            GROUP BY status
            """
        )
        dispatch_by_status = {row["status"]: row["cnt"] for row in await dispatch_c.fetchall()}
        reply_pending_c = await db.execute(
            """
            SELECT COUNT(*)
            FROM dispatch_runs
            WHERE require_reply = 1
              AND status IN ('completed', 'failed', 'cancelled')
              AND COALESCE(result_message_id, '') = ''
              AND NOT (
                  runtime = 'claude-code'
                  AND status = 'completed'
                  AND COALESCE(summary, '') = 'Delivered to Claude resident session'
              )
            """
        )
        reply_pending = (await reply_pending_c.fetchone())[0]

        return {
            "agents": agents,
            "environments": environments,
            "spawn_requests_total": sum(spawn_by_status.values()),
            "spawn_requests_by_status": spawn_by_status,
            "active_sessions": active_sessions,
            "total_messages": total,
            "unread_messages": unread,
            "channel_unread_messages": channel_unread,
            "orphan_unread_messages": orphan_unread,
            "messages_today": today,
            "direct_messages_24h": direct_24h,
            "channel_posts_24h": channel_24h,
            "active_dm_pairs_24h": active_pairs_24h,
            "run_failures_24h": run_failures_24h,
            "failed_spawns_24h": failed_spawns_24h,
            "completed_runs_24h": completed_runs_24h,
            "messages_by_type": by_type,
            "messages_by_agent": by_agent,
            "shared_files": shared_row["cnt"],
            "shared_size_bytes": shared_row["total_size"],
            "shared_size_mb": round(shared_row["total_size"] / 1048576, 2),
            "dispatch_runs_total": sum(dispatch_by_status.values()),
            "dispatch_runs_by_status": dispatch_by_status,
            "dispatch_reply_pending": reply_pending,
        }
    finally:
        await db.close()


@router.get("/analytics")
async def get_analytics(request: Request):
    db = await get_db()
    try:
        now_s = int(time.time())
        message_where = """
          (
            (source = 'direct' AND to_agent IS NOT NULL)
            OR (source = 'channel' AND to_agent IS NULL)
          )
        """

        async def count_messages_between(start_ms: int, end_ms: int) -> int:
            cursor = await db.execute(
                f"SELECT COUNT(*) FROM messages WHERE {message_where} AND timestamp >= ? AND timestamp < ?",
                (start_ms, end_ms),
            )
            return int((await cursor.fetchone())[0])

        hourly = []
        hour_start = (now_s // 3600) * 3600
        for i in range(23, -1, -1):
            start_s = hour_start - i * 3600
            hourly.append({
                "label": time.strftime("%H:00", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 3600) * 1000),
            })

        daily = []
        today_struct = time.localtime(now_s)
        today_start_s = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d", today_struct), "%Y-%m-%d")))
        for i in range(29, -1, -1):
            start_s = today_start_s - i * 86400
            daily.append({
                "label": time.strftime("%m-%d", time.localtime(start_s)),
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, (start_s + 86400) * 1000),
            })

        monthly = []
        year = today_struct.tm_year
        month = today_struct.tm_mon
        for i in range(11, -1, -1):
            m = month - i
            y = year
            while m <= 0:
                m += 12
                y -= 1
            next_m = m + 1
            next_y = y
            if next_m > 12:
                next_m = 1
                next_y += 1
            start_s = int(time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1)))
            end_s = int(time.mktime((next_y, next_m, 1, 0, 0, 0, 0, 0, -1)))
            monthly.append({
                "label": f"{y}-{m:02d}",
                "start": _iso_from_ms(start_s * 1000),
                "count": await count_messages_between(start_s * 1000, end_s * 1000),
            })

        status_c = await db.execute("SELECT status, COUNT(*) as cnt FROM dispatch_runs GROUP BY status")
        runs_by_status = {row["status"]: row["cnt"] for row in await status_c.fetchall()}

        agents_c = await db.execute("SELECT * FROM agents")
        agent_rows = await agents_c.fetchall()
        live_agents = 0
        online_agents = 0
        working_agents = 0
        for row in agent_rows:
            mode = _agent_wake_mode(row)
            if mode != "message-only" and mode != "disabled":
                live_agents += 1
            status = await _compute_agent_status(row, DEFAULT_SETTINGS["idle_minutes"], DEFAULT_SETTINGS["offline_minutes"])
            if not status.startswith("offline") and not status.startswith("stale"):
                online_agents += 1
            if status.startswith("working"):
                working_agents += 1

        env_c = await db.execute("SELECT COUNT(*) FROM environments WHERE status = 'online'")
        online_environments = int((await env_c.fetchone())[0])

        return {
            "ok": True,
            "messagesPerHour": hourly,
            "messagesPerDay": daily,
            "messagesPerMonth": monthly,
            "runsByStatus": runs_by_status,
            "liveAgents": live_agents,
            "onlineAgents": online_agents,
            "workingAgents": working_agents,
            "onlineEnvironments": online_environments,
        }
    finally:
        await db.close()


# ─── Clear ───────────────────────────────────────────────────────────────────

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


# ─── Rotate ──────────────────────────────────────────────────────────────────

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

        # Mark stale agents
        stale_hours = settings["stale_agent_hours"]
        cursor = await db.execute(
            "UPDATE agents SET status = 'stale' WHERE status != 'stale' AND datetime(last_seen) < datetime('now', ? || ' hours')",
            (f"-{stale_hours}",)
        )
        stats["stale_agents"] = cursor.rowcount

        # Clean orphaned read receipts
        await db.execute("DELETE FROM read_receipts WHERE message_id NOT IN (SELECT id FROM messages)")

        await db.commit()
        return {"ok": True, "stats": stats}
    finally:
        await db.close()


# ─── Dashboard ───────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent.parent / "dashboard.html"
    return HTMLResponse(
        html_path.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@router.get("/dashboard/dispatches", response_class=HTMLResponse)
async def dashboard_dispatches():
    return await dashboard()
