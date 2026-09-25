"""Helpers shared by the agents surfaces, defined once so the six surface modules do not each
declare their own."""

from __future__ import annotations

import json
import logging
import time
from typing import Any


from service.api_core.capabilities import (  # re-exported for this package's modules
    _managed_via_wrapper_for_runtime,
)
import sqlite3

logger = logging.getLogger("aify_comms.routers.agents.shared")


# _fail_active_runs_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.


# _machine_family moved to service/api_core/registration_gates.py in v0.5.4.


# _stop_virtual_terminals_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.


from service.api_core.tuning import (
    _RUNTIME_CONFIG_LIVE_KEYS,
    _SHELL_PLACEHOLDER_HANDLE_RE,
)


# _apply_status_event moved to service/api_core/status_events.py in v0.5.4 — it had seven
# router importers and depends only on the clock and the pure status engine, and a router
# declaring it blocked every api_core leaf that needed it.


# _broadcast_agent_status moved to service/api_core/status_broadcast.py in v0.5.4.


# _broadcast_engine_status moved to service/api_core/status_broadcast.py in v0.5.4.


# _enforce_env_reachable_gate moved to service/api_core/registration_gates.py in v0.5.4.


# _enforce_live_worker_gate moved to service/api_core/registration_gates.py in v0.5.4.


# _fresh_same_mode_bridge_conflict moved to service/api_core/registration_gates.py in v0.5.4, then on to
# service/api_core/same_mode_bridge_gate.py.


def _merge_runtime_policy_for_wrapper_reregister(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Keep durable model/effort policy when a wrapper child refreshes live metadata."""
    previous = existing if isinstance(existing, dict) else {}
    current = incoming if isinstance(incoming, dict) else {}
    durable_previous = {key: value for key, value in previous.items() if key not in _RUNTIME_CONFIG_LIVE_KEYS}
    return {**durable_previous, **current}


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


# _request_stop_agent_terminals moved to service/api_core/agent_terminal_ops.py in v0.5.4.


# _resolve_live_console_terminal moved to service/api_core/agent_terminal_ops.py in v0.5.4.


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
    if handle and _SHELL_PLACEHOLDER_HANDLE_RE.match(handle):
        return ""
    return handle


# _session_capabilities_replacing_handle moved to service/api_core/session_capabilities.py
# in v0.5.4 - six router importers, and a router declaring it blocked an api_core split.


def _synth_terminal_should_be_created(runtime: str, settings: dict[str, Any]) -> bool:
    """Plan 4 (2026-05-25): synth-terminal (aify://virtual-rpc/<runtime>) is
    deprecated for wrapper-backed runtimes. The wrapper PTY IS the terminal.
    Synth stays for native managed runtimes such as pi/opencode and for
    native-controller fallback when wrapper backing is disabled.
    """
    if _managed_via_wrapper_for_runtime(settings, runtime):
        return False
    return True


# _validate_registration_cwd moved to service/api_core/registration_gates.py in v0.5.4.
