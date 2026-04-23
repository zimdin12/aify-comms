"""
aify-comms v2 API — SQLite backend.
Drop-in replacement for api.py with identical endpoint signatures.
"""
import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, Request
from fastapi.responses import HTMLResponse

# Per-agent wake-up events for comms_listen
_listen_events: dict[str, asyncio.Event] = {}

from service.db import get_db
from service.models import (
    AgentRegister, AgentStatusUpdate, AgentDescribeRequest, MessageSend, ClearRequest,
    ChannelCreate, ChannelMessage, ChannelJoin,
    SpawnAgentRequest,
    AgentRuntimeStateUpdate, DispatchRequest, DispatchClaimRequest, DispatchRunUpdate,
    DispatchControlRequest, DispatchControlClaimRequest, DispatchControlUpdate,
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

def _shared_dir(request: Request) -> Path:
    try:
        d = Path(request.app.state.config.data_dir) / "shared_files"
    except Exception:
        d = Path("/data/shared_files")
    d.mkdir(parents=True, exist_ok=True)
    return d

_MANUAL_STATUSES = {"blocked", "completed"}

DEFAULT_SETTINGS = {
    "retention_days": 90,
    "max_messages_per_agent": 1000,
    "max_shared_size_mb": 500,
    "stale_agent_hours": 24,
    "dashboard_refresh_seconds": 15,
    "rotation_enabled": True,
    "idle_minutes": 5,
    "offline_minutes": 30,
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

async def _get_ws(request: Request):
    try:
        return request.app.state.ws_manager
    except Exception:
        return None

async def _touch_agent(db, agent_id: str):
    await db.execute(
        "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed') THEN status ELSE 'active' END WHERE id = ?",
        (_now(), agent_id)
    )


def _json_loads_or(value: Any, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _normalize_session_mode(mode: Any) -> str:
    value = str(mode or "resident").strip().lower()
    return value if value in _SESSION_MODES else "resident"


def _normalize_runtime(runtime: Any) -> str:
    key = str(runtime or "generic").strip().lower()
    return _RUNTIME_ALIASES.get(key, key or "generic")


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


def _row_require_reply(row) -> bool:
    return bool(int((row["require_reply"] if row and "require_reply" in row.keys() else 0) or 0))


def _dispatch_reply_state(row) -> str:
    if not _row_require_reply(row):
        return "not_required"
    if str((row["result_message_id"] if row else "") or "").strip():
        return "sent"
    status = str((row["status"] if row else "") or "").strip().lower()
    if status in _DISPATCH_TERMINAL_STATUSES:
        return "pending"
    return "awaiting"


def _dispatch_reply_pending(row) -> bool:
    return _dispatch_reply_state(row) == "pending"


def _serialize_dispatch_run_row(row, *, blocked_by=None, include_body: bool = False, include_events=None, include_controls=None) -> dict[str, Any]:
    payload = {
        "id": row["id"],
        "messageId": row["message_id"],
        "from": row["from_agent"],
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
        return ["resident-run", "interrupt"]
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
    return _json_loads_or(row["capabilities"], [])


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
    if capabilities and "resident-run" not in capabilities:
        return None, 'agent capabilities do not include "resident-run"'
    if runtime == "codex" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident Codex session without a bound session handle. '
            "Re-register that live session or provide sessionHandle explicitly."
        )
    if runtime == "opencode" and not session_handle:
        return None, (
            f'agent "{row["id"]}" is a resident OpenCode session without a bound session handle. '
            "Re-register that live session with sessionHandle explicitly or use a managed worker."
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
            "or use comms_spawn_agent to create a managed worker."
        )
        hint["suggestedCommands"] = [
            f'comms_register(agentId="{recipient_id}", role="{role}", runtime="opencode", sessionHandle="<session-id>")',
            f'comms_spawn_agent(from="<your-agent>", agentId="{recipient_id}-worker", role="{role}", runtime="opencode")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime not in _LAUNCHABLE_RUNTIMES:
        hint["fix"] = "This target is message-only right now. Check comms_agent_info before suggesting any runtime-specific reinstall or restart steps."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    if session_mode == "managed" and (row["launch_mode"] or "detached") == "none":
        hint["fix"] = "Enable launch mode or recreate this agent as a managed worker."
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
        "capabilities": _json_loads_or(row["capabilities"], []),
        "runtimeConfig": _json_loads_or(row["runtime_config"], {}),
        "runtimeState": _json_loads_or(row["runtime_state"], {}),
        "dispatchState": dispatch_state or {"hasActiveRun": False, "activeRun": None, "queuedRuns": 0},
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
                    source_message_id=(source_message_ids or {}).get(recipient_id, message_id or ""),
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
                message_id=str(message_id or ""),
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
                run_id, message_id, from_agent, recipient_id, dispatch_mode, execution_mode, requested_runtime or "",
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
        WHERE target_agent = ? AND message_id = ? AND result_message_id = ''
        ORDER BY requested_at DESC
        LIMIT 1
        """,
        (from_agent, resolved_in_reply_to),
    )
    replied_run = await run_cursor.fetchone()
    if not replied_run:
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

# ─── Root ────────────────────────────────────────────────────────────────────

@router.get("/")
async def root():
    return {
        "service": "aify-comms",
        "version": "3.6.6",
        "storage": "sqlite",
        "endpoints": {
            "agents": "/api/v1/agents",
            "messages": "/api/v1/messages",
            "dispatch": "/api/v1/dispatch",
            "shared": "/api/v1/shared",
            "channels": "/api/v1/channels",
            "settings": "/api/v1/settings",
            "dashboard": "/api/v1/dashboard",
            "stats": "/api/v1/stats",
        },
    }

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
            INSERT OR REPLACE INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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


@router.post("/agents/spawn")
async def spawn_agent(req: SpawnAgentRequest, request: Request):
    validate_name(req.agentId, "agent ID")
    db = await get_db()
    try:
        await _touch_agent(db, req.from_agent)
        normalized_runtime = _normalize_runtime(req.runtime)
        if normalized_runtime not in _LAUNCHABLE_RUNTIMES:
            raise HTTPException(400, f'Runtime "{normalized_runtime}" does not support managed workers yet')

        owner_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.from_agent,))
        owner = await owner_cursor.fetchone()
        if not owner:
            raise HTTPException(404, f"Agent '{req.from_agent}' not found")

        existing_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (req.agentId,))
        existing = await existing_cursor.fetchone()
        if existing and _normalize_session_mode(existing["session_mode"] or "resident") != "managed":
            raise HTTPException(409, f'Agent "{req.agentId}" already exists as a resident session')

        machine_id = req.machineId or owner["machine_id"] or ""
        now = _now()
        capabilities = _default_capabilities_for(normalized_runtime, "managed", "")
        runtime_config = req.runtimeConfig or (existing and _json_loads_or(existing["runtime_config"], {})) or {}
        runtime_state = existing["runtime_state"] if existing and existing["runtime_state"] else "{}"
        if req.description is None:
            description_value = (existing["description"] if existing and "description" in existing.keys() else "") or ""
        else:
            description_value = req.description

        await db.execute(
            """
            INSERT OR REPLACE INTO agents (
                id, role, name, cwd, model, description, instructions, status, status_note, runtime, machine_id,
                launch_mode, session_mode, session_handle, managed_by, capabilities,
                runtime_config, runtime_state, registered_at, last_seen
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                req.agentId,
                req.role,
                req.name or req.agentId,
                req.cwd or owner["cwd"] or "",
                req.model or "",
                description_value,
                req.instructions or "",
                "idle",
                (existing["status_note"] if existing and "status_note" in existing.keys() else "") or "",
                normalized_runtime,
                machine_id,
                "managed",
                "managed",
                "",
                req.from_agent,
                json.dumps(capabilities),
                json.dumps(runtime_config),
                runtime_state,
                existing["registered_at"] if existing else now,
                now,
            ),
        )

        runs = []
        if req.body and str(req.body).strip():
            runs = await _create_dispatch_runs(
                db,
                [req.agentId],
                from_agent=req.from_agent,
                message_type="request",
                subject=req.subject or f"Spawn {req.agentId}",
                body=req.body,
                priority=req.priority,
                in_reply_to=None,
                dispatch_mode="require_start",
                execution_mode="managed",
                requested_runtime=normalized_runtime,
                message_id=None,
            )

        await db.commit()
        ws = await _get_ws(request)
        if ws:
            await ws.broadcast("agent_registered", {
                "agentId": req.agentId,
                "role": req.role,
                "runtime": normalized_runtime,
                "machineId": machine_id,
                "sessionMode": "managed",
            })
            for run in runs:
                await ws.broadcast("dispatch_queued", {"runId": run["runId"], "targetAgentId": run["targetAgentId"]})
        if runs:
            _wake_agent(req.agentId)

        return {
            "ok": True,
            "agentId": req.agentId,
            "sessionMode": "managed",
            "runtime": normalized_runtime,
            "machineId": machine_id,
            "runs": runs,
        }
    finally:
        await db.close()


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str, request: Request):
    db = await get_db()
    try:
        cursor = await db.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("agent_removed", {"agentId": agent_id})
        return {"ok": cursor.rowcount > 0}
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
        cursor = await db.execute(
            "UPDATE agents SET runtime_state = ?, last_seen = ? WHERE id = ?",
            (json.dumps(req.runtimeState or {}), _now(), agent_id)
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        return {"ok": True, "agentId": agent_id, "runtimeState": req.runtimeState or {}}
    finally:
        await db.close()


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

        linked_result_message_id = _primary_result_message_id(msg_id, recipients)

        for r in recipients:
            recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
            await db.execute(
                "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (recipient_message_id,
                 req.from_agent, r, "direct", req.type, req.subject, req.body, req.priority, 1 if req.trigger else 0, resolved_in_reply_to, ts)
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

        dispatch_runs = []
        not_started = []
        if req.trigger:
            require_reply = _dispatch_requires_reply(req.requireReply, default=req.type == "request")
            source_message_ids = {
                recipient_id: (f"{msg_id}-{recipient_id}" if len(recipients) > 1 else msg_id)
                for recipient_id in recipients
            }
            launchable_recipients = []
            for recipient_id in recipients:
                agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                row = await agent_cursor.fetchone()
                if not row:
                    not_started.append(_dispatch_fix_hint(recipient_id, None, "agent is not registered"))
                    continue
                execution_mode, reason = _agent_execution_mode(row)
                if reason or not execution_mode:
                    not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
                    continue
                launchable_recipients.append((recipient_id, execution_mode))
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
                steer=req.steer,
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
                "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed') THEN status ELSE ? END WHERE id = ?",
                (now, new_status, agent_id)
            )
            await db.commit()

        return {"total": total, "showing": len(messages), "messages": messages}
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
        await db.execute(
            "UPDATE agents SET last_seen = ?, status = CASE WHEN status IN ('blocked','completed','working') THEN status ELSE 'active' END WHERE id = ?",
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
            if req.mode == "message_only":
                continue
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

        message_id = None
        source_message_ids = {}
        if req.createMessage:
            message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
            ts = int(time.time() * 1000)
            for recipient_id in recipients:
                recipient_message_id = f"{message_id}-{recipient_id}" if len(recipients) > 1 else message_id
                source_message_ids[recipient_id] = recipient_message_id
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body, priority, dispatch_requested, in_reply_to, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        recipient_message_id,
                        req.from_agent, recipient_id, "direct", req.type, req.subject, req.body,
                        req.priority, 1 if req.mode != "message_only" else 0, resolved_in_reply_to, ts
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
        if req.mode != "message_only" and launchable_recipients:
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
            await db.rollback()
            raise HTTPException(404, f"Agent '{req.agentId}' not found")

        if req.machineId and agent["machine_id"] and agent["machine_id"] != req.machineId:
            await db.rollback()
            return {"ok": True, "run": None}

        # Reject claims from bridges that have been superseded by a newer
        # register from the same agent. Without this check, a stale codex-aify
        # (or any old bridge) process keeps polling, grabs queued runs, and
        # tries to resume pre-update thread bindings — which is how
        # "AbsolutePathBuf deserialized without a base path" errors keep
        # surfacing even after the code on disk has been patched. The fresh
        # bridge should be the only one claiming work once it has registered.
        if req.bridgeId and await _bridge_is_superseded(db, req.bridgeId, req.agentId):
            await db.commit()
            return {
                "ok": True,
                "run": None,
                "blockedBy": {
                    "reason": "bridge_superseded",
                    "bridgeId": req.bridgeId,
                    "agentId": req.agentId,
                    "hint": "This bridge has been replaced by a newer registration. Shut it down.",
                },
            }

        agent_runtime = _normalize_runtime(agent["runtime"] or "generic")

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
        if selected_run["message_id"]:
            await db.execute(
                "INSERT OR IGNORE INTO read_receipts (message_id, agent_id, read_at) VALUES (?,?,?)",
                (selected_run["message_id"], req.agentId, claimed_at)
            )
        await _append_dispatch_event(db, selected_run["id"], "claimed", f"machine={req.machineId or ''}")
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
            runs.append(_serialize_dispatch_run_row(row, blocked_by=blocked_by))
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
            SELECT id, from_agent, action, body, status, response_text, requested_at, claimed_at, handled_at
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

        if req.agentStatus:
            await db.execute(
                "UPDATE agents SET status = ?, last_seen = ? WHERE id = ?",
                (req.agentStatus, now, row["target_agent"])
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
        deleted = await _delete_messages_by_ids(db, [message_id])
        await db.commit()
        if deleted == 0:
            raise HTTPException(404, f"Message '{message_id}' not found")
        ws = await _get_ws(request)
        if ws: await ws.broadcast("message_deleted", {"id": message_id})
        return {"ok": True, "id": message_id}
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
        if file:
            shared_dir = _shared_dir(request)
            file_path = shared_dir / name
            data = await file.read()
            file_path.write_bytes(data)
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, file_path, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, str(file_path), len(data), 1, now)
            )
        else:
            text = content or ""
            await db.execute(
                "INSERT OR REPLACE INTO shared_artifacts (name, from_agent, description, content, size, is_binary, shared_at) VALUES (?,?,?,?,?,?,?)",
                (name, from_agent, description, text, len(text), 0, now)
            )
        await db.commit()
        ws = await _get_ws(request)
        if ws: await ws.broadcast("file_shared", {"name": name, "from": from_agent})
        return {"ok": True, "name": name}
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
async def list_channels(request: Request):
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
            channels.append({
                "name": ch["name"], "description": ch["description"],
                "createdBy": ch["created_by"], "createdAt": ch["created_at"],
                "members": [], "memberCount": member_count, "messageCount": msg_count,
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
async def get_channel(name: str, request: Request, limit: int = Query(50, ge=1, le=500), offset: int = 0):
    validate_name(name, "channel name")
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
            messages.append({
                "id": row["id"], "from": row["from_agent"], "type": row["type"],
                "body": row["body"], "timestamp": row["timestamp"],
                "dispatchRequested": bool(row["dispatch_requested"]) if "dispatch_requested" in row.keys() else False,
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

        # Channel message (canonical)
        await db.execute(
            "INSERT INTO messages (id, from_agent, channel, source, type, subject, body, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?)",
            (msg_id, req.from_agent, name, "channel", req.type, subject, req.body, 1 if (False if req.silent else req.trigger is not False) else 0, ts)
        )

        # Deliver to each member's inbox (except sender)
        mem_c = await db.execute("SELECT agent_id FROM channel_members WHERE channel_name = ?", (name,))
        members = [r["agent_id"] for r in await mem_c.fetchall()]
        recipients = []
        inbox_message_ids = {}
        for member in members:
            if member != req.from_agent:
                recipient_msg_id = f"{msg_id}-{member}"
                recipients.append(member)
                inbox_message_ids[member] = recipient_msg_id
                await db.execute(
                    "INSERT INTO messages (id, from_agent, to_agent, channel, source, type, subject, body, priority, dispatch_requested, timestamp) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        recipient_msg_id, req.from_agent, member, name, "channel", req.type, subject,
                        req.body, req.priority or "normal", 1 if (False if req.silent else req.trigger is not False) else 0, ts
                    )
                )

        should_trigger = False if req.silent else req.trigger is not False
        dispatch_runs = []
        not_started = []
        if should_trigger and recipients:
            launchable_recipients = []
            for recipient_id in recipients:
                agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                row = await agent_cursor.fetchone()
                if not row:
                    not_started.append(_dispatch_fix_hint(recipient_id, None, "agent is not registered"))
                    continue
                execution_mode, reason = _agent_execution_mode(row)
                if reason or not execution_mode:
                    not_started.append(_dispatch_fix_hint(recipient_id, row, reason or "active dispatch unavailable"))
                    continue
                launchable_recipients.append((recipient_id, execution_mode))
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
                steer=req.steer,
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
            """
        )
        reply_pending = (await reply_pending_c.fetchone())[0]

        return {
            "agents": agents,
            "total_messages": total,
            "unread_messages": unread,
            "channel_unread_messages": channel_unread,
            "orphan_unread_messages": orphan_unread,
            "messages_today": today,
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
            count_cursor = await db.execute("SELECT COUNT(*) FROM agents")
            deleted_agents = (await count_cursor.fetchone())[0]
            await db.execute("DELETE FROM agents")

        if req.target in ("channels", "all"):
            await db.execute("DELETE FROM channel_members")
            deleted_messages += await _delete_messages_where(db, "channel IS NOT NULL")
            await db.execute("DELETE FROM channels")

        if req.target == "all":
            await db.execute("DELETE FROM read_receipts")

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
