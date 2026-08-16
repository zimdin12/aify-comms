"""Why a dispatch cannot reach this agent, and what the operator should do about it.

One function, 90 lines, and a single-function module is the right shape here for the same reason
`service/terminal_diagnostics.py` is one — the subject is "which fact explains the failure", and that is a
responsibility, not a utility. It follows that precedent deliberately rather than being filed next to
whatever imports it.

This is the SEND-side counterpart to `api_core/claim_gating.py`'s `_bridge_claim_block_reason`, and the two
are deliberately NOT one module. Claim gating answers "may this bridge take this run"; this answers "can
work reach this agent at all, and what is missing". They read overlapping inputs and produce answers for
different audiences — one for a bridge deciding whether to claim, one for a human deciding what to fix.
Merging them would produce a module whose subject is "reasons", which is a junk drawer with a plausible name.

The hint is operator-facing prose and that is the point: a preflight that refuses a send without saying
which capability, runtime or session mode is missing sends the operator to the logs. Every branch here
exists because someone could not tell a misconfigured agent from an unreachable one.

DB ACCESS: none. It is handed a row and settings.
"""

from __future__ import annotations

from typing import Any

from service.api_core.capabilities import _row_capabilities
from service.api_core.runtime import _normalize_launch_mode, _normalize_runtime, _normalize_session_mode
from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES as _LAUNCHABLE_RUNTIMES


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

    if session_mode == "resident" and "resident bridge" in reason:
        runtime_name = {
            "claude-code": "Claude",
            "codex": "Codex",
            "hermes": "Hermes",
            "opencode": "OpenCode",
            "pi": "Oh My Pi",
        }.get(runtime, runtime)
        hint["fix"] = (
            f"Restart the visible resident wrapper for this {runtime_name} session, then re-register from inside that same wrapper with comms_register. "
            "Raw /api/v1/agents metadata updates do not create the resident bridge heartbeat. "
            "Use Dashboard Switch to managed if the visible resident terminal should not own delivery."
        )
        hint["suggestedCommands"] = [
            f'comms_register(agentId="{recipient_id}", role="{role}", runtime="{runtime}")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
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

    if runtime == "opencode" and session_mode == "resident":
        hint["fix"] = (
            "Resident OpenCode sessions are presence-only. Spawn a persistent OpenCode agent from a connected dashboard environment."
        )
        hint["suggestedCommands"] = [
            f'comms_envs()',
            f'comms_spawn(from="<your-agent>", agentId="{recipient_id}-teammate", role="{role}", runtime="opencode")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime == "pi" and session_mode == "resident":
        hint["fix"] = (
            "Resident Oh My Pi sessions are presence-only. Spawn a persistent Pi agent from a connected dashboard environment."
        )
        hint["suggestedCommands"] = [
            f'comms_envs()',
            f'comms_spawn(from="<your-agent>", agentId="{recipient_id}-teammate", role="{role}", runtime="pi")',
            f'comms_agent_info(agentId="{recipient_id}")',
        ]
        return hint

    if runtime not in _LAUNCHABLE_RUNTIMES:
        hint["fix"] = "This target is message-only right now. Check comms_agent_info before suggesting any runtime-specific reinstall or restart steps."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    if session_mode == "managed" and _normalize_launch_mode(row["launch_mode"]) == "none":
        hint["fix"] = "Enable launch mode or recreate this agent as an environment-managed session."
        hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
        return hint

    hint["fix"] = "Inspect the target runtime/session with comms_agent_info, then retry with runtime-specific steps."
    hint["suggestedCommands"] = [f'comms_agent_info(agentId="{recipient_id}")']
    return hint
