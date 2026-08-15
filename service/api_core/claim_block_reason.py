"""Why this bridge may not claim this run — and the wrapper-terminal facts that decide it.

RELOCATED from `service/api_core/claim_gating.py` in v0.5.4, byte-identical. Four functions that call
only each other: the block-reason computation and the three small helpers that answer "is there a
usable wrapper terminal right now". Nothing outside the module called the three, and only
`dispatch_claim.py` calls the entry point.

IT IS 208 LINES OF GUARD CHAIN AND THAT IS ITS SHAPE, not a defect. Every branch answers the same
question — is there a reason to refuse — and returns as soon as it finds one, which is why it cannot
be split by extract-method: a block lifted out of it carries an early exit that would return from the
helper instead of the caller. Measured, not assumed; the verifier refuses every candidate inside it.
Relocation is the move that fits, and it is why this file exists.

THE REASON IS THE PRODUCT, not a boolean. A claim that is refused with no explanation looks identical
to an agent that simply has no work, which is the failure this whole path exists to avoid — the string
it returns is read by the bridge and surfaced to the operator.
"""
from __future__ import annotations

from typing import Any, Optional

from service.api_core.capabilities import _managed_via_wrapper_for_runtime
from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.terminal_ownership import _active_terminal_for_agent
from service.api_core.terminal_text import _terminal_text_compact
# ALIASED ON IMPORT, exactly as the carrier did it. The public name is
# `environment_effective_status`; the moved body calls the underscored one, so importing it
# under the name it was WRITTEN against is what keeps the body byte-identical.
from service.env_status import environment_effective_status as _environment_effective_status


async def _active_wrapper_terminal_id(db, agent_id: str, *, settings: dict[str, Any]) -> str:
    terminal = await _active_terminal_for_agent(db, agent_id, settings=settings)
    if not terminal:
        return ""
    try:
        return str(terminal["terminal_id"] or terminal["id"] or "").strip()
    except Exception:
        return str((terminal.get("terminal_id") or terminal.get("id") or "") if isinstance(terminal, dict) else "").strip()


def _hermes_terminal_still_resuming(text: str) -> bool:
    compact = _terminal_text_compact(text)
    if not compact:
        return False
    resume_idx = compact.rfind("resuming")
    if resume_idx < 0:
        return False
    ready_idx = compact.rfind("ready")
    return ready_idx < resume_idx


async def _active_wrapper_terminal_not_ready_reason(db, terminal_id: str, runtime: str) -> str:
    if _normalize_runtime(runtime or "") != "hermes" or not terminal_id:
        return ""
    row = await (await db.execute(
        "SELECT output FROM terminal_sessions WHERE id = ?",
        (terminal_id,),
    )).fetchone()
    if not row:
        return ""
    if _hermes_terminal_still_resuming(str(row["output"] or "")):
        return "Hermes wrapper Console is still resuming a saved session; waiting for ready/heal before claiming channel work."
    return ""


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
    # channel-mode runs for managed-via-wrapper agents (see _CHANNEL_CLAIM_RUNTIMES
    # at line 290 and dispatch-execution.js supportedExecutionModes). Without
    # this carve-out, every wrapper-child claim hits "environment_bridge_not_current"
    # at line 1701 because the env bridge_id != the wrapper-child bridge_id —
    # and managed codex/hermes dispatches sit queued forever even when the
    # wrapper PTY is alive and its inner MCP server has registered. Detect a
    # wrapper-child claim by: (a) the request includes 'channel' in executionModes;
    # (b) the runtime is in _CHANNEL_CLAIM_RUNTIMES (managed-via-wrapper-eligible);
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
        and runtime in _CHANNEL_CLAIM_RUNTIMES
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
        and runtime in _CHANNEL_CLAIM_RUNTIMES
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
