"""Domain record -> API dict serializers. PURE — no database, no router.

Layer-0 slice of the v0.5.4 decomposition. Deliberately NOT folded into
`api_core/serialization.py`: that module holds primitives (json parsing, text clipping, timestamp
keys, machine-id normalisation) and has no domain dependencies. These three map a DB row onto the
shape the API returns, and `_environment_record_to_dict` needs `environment_effective_status` — a
domain rule. Putting them together would hand the primitives module a domain dependency it has no
reason to carry, and blur what either module is for.

Kept separate for the same reason `terminal_diagnostics.py` is separate: a module you can describe
in one sentence is one you can test in isolation.
"""

from __future__ import annotations

from typing import Any

from service.api_core.serialization import _json_loads_or
from service.env_status import environment_effective_status as _environment_effective_status
import re

from service.api_core.dispatch_text import (
    _MERGED_DISPATCH_HEADER,
    _pending_dispatch_count,
)
from service.api_core.reply_contract import (
    _dispatch_reply_pending,
    _dispatch_reply_state,
)
from service.api_core.serialization import _dedupe_preserve, _row_require_reply



def _environment_record_to_dict(row, *, offline_seconds: int = 90) -> dict[str, Any]:
    status = _environment_effective_status(row, offline_seconds=offline_seconds)
    runtimes = _json_loads_or(row["runtimes"], [])
    metadata = _json_loads_or(row["metadata"], {})
    normalized_runtimes = []
    for runtime in runtimes:
        if not isinstance(runtime, dict):
            continue
        normalized_runtimes.append({**runtime, "modes": ["managed-warm"]})
    terminal = bool(metadata.get("terminal"))
    pty = bool(metadata.get("pty"))
    terminal_runtimes = metadata.get("terminalRuntimes") if isinstance(metadata.get("terminalRuntimes"), list) else []
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
        "terminal": terminal,
        "pty": pty,
        "terminalRuntimes": terminal_runtimes,
        "status": status,
        "metadata": metadata,
        "registeredAt": row["registered_at"] or "",
        "lastSeen": row["last_seen"] or "",
    }


def _agent_session_to_dict(row) -> dict[str, Any]:
    keys = set(row.keys())
    raw_owner_mode = str(row["owner_mode"] if "owner_mode" in keys else "").strip()
    session_mode = str(row["mode"] or "").strip().lower()
    if raw_owner_mode in {"resident", "console"}:
        owner_mode = raw_owner_mode
    elif session_mode == "resident":
        owner_mode = "resident"
    else:
        owner_mode = raw_owner_mode or "managed"
    owner_bridge_id = str(row["owner_bridge_id"] if "owner_bridge_id" in keys else "").strip()
    terminal_id = str(row["terminal_id"] if "terminal_id" in keys else "").strip()
    terminal_status = str(row["terminal_status"] if "terminal_status" in keys else "").strip()
    terminal_command = str(row["terminal_command"] if "terminal_command" in keys else "").strip()
    terminal_workspace = str(row["terminal_workspace"] if "terminal_workspace" in keys else "").strip()
    return {
        "id": row["id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "mode": row["mode"] or "managed-warm",
        "ownerMode": owner_mode,
        "ownerBridgeId": owner_bridge_id,
        "terminalId": terminal_id,
        "terminalStatus": terminal_status,
        "terminalCommand": terminal_command,
        "terminalWorkspace": terminal_workspace,
        "terminal": {
            "id": terminal_id,
            "status": terminal_status,
            "command": terminal_command,
            "workspace": terminal_workspace,
            "ownerMode": owner_mode,
            "ownerBridgeId": owner_bridge_id,
        },
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


def _terminal_session_to_dict(row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "sessionId": row["session_id"],
        "agentId": row["agent_id"],
        "environmentId": row["environment_id"],
        "bridgeId": row["bridge_id"] or "",
        "runtime": row["runtime"],
        "workspace": row["workspace"] or "",
        "command": row["command"] or "",
        "output": (row["output"] if "output" in keys else "") or "",
        "outputSeq": int((row["output_seq"] if "output_seq" in keys else 0) or 0),
        "cols": int((row["cols"] if "cols" in keys else 0) or 0),
        "rows": int((row["rows"] if "rows" in keys else 0) or 0),
        "status": row["status"] or "",
        "requestedBy": row["requested_by"] or "",
        "createdAt": row["created_at"] or "",
        "updatedAt": row["updated_at"] or "",
        "stoppedAt": row["stopped_at"] or "",
        "error": row["error"] or "",
    }


# v0.5.4: `_serialize_dispatch_run_row` arrived from `routers/dispatch_messages/dispatch.py`. It is a
# row-to-payload function like every other one here, and it was in a 1,725-line router only because
# that is where its two callers live. It could not leave until the reply-state family got real owners
# one slice earlier — it reads `_dispatch_reply_state`, `_dispatch_reply_pending`,
# `_pending_dispatch_count` and `_MERGED_DISPATCH_HEADER`, all of which were reachable then only
# through a SIBLING ROUTER module, which would have made this leaf import upward.

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
        "queueIfBusy": bool(row["queue_if_busy"]),
        "steerIfBusy": bool(row["steer_if_busy"]),
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
