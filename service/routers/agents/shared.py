"""Helpers owned by the agents surfaces, plus every borrow they still need.

v0.5.2m. Defined once so the six surface modules share one shim rather than each
declaring its own. Borrows are established by FOLLOWING SHIMS, not raw caller count:
anything another module already borrows from the router stays borrowed here too.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any


from service.api_core.liveness import _LIVE_SESSION_STATUSES

from service.api_core.capabilities import (  # re-exported for this package's modules
    _managed_via_wrapper_for_runtime,
)
import sqlite3

logger = logging.getLogger("aify_comms.routers.agents.shared")






# Was a borrow shim; the owner is service/api_core/records.py, not the control plane.







# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/api_core/status_refresh.py in v0.5.4, so
# a plain import works.


# Was a borrow shim for the same reason `engine_status` above was: the legacy status path lived in
# the control plane, which a router cannot import at module level. Both status paths moved to
# service/api_core/status_inputs.py in v0.5.4, so this is a plain import.








# _fail_active_runs_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.




# _machine_family moved to service/api_core/registration_gates.py in v0.5.4.















# _stop_virtual_terminals_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.








# Was a borrow shim: the DB-reading wrapper lived in the control plane, and a router importing that
# at module level is a cycle. It moved to service/api_core/status_inputs.py in v0.5.4, so this is a
# plain import now. NOT `derive` — that is the pure state machine this wrapper feeds.
from service.api_core.tuning import (
    LIST_AGENTS_REFRESH_LIMIT,
    _CONSOLE_TAIL_MAX_BYTES,
    _CONSOLE_TAIL_MAX_LINES,
    _RUNTIME_CONFIG_LIVE_KEYS,
    _SHELL_PLACEHOLDER_HANDLE_RE,
)


def _borrowed_list_agents_refresh_limit():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return LIST_AGENTS_REFRESH_LIMIT














def _borrowed_console_tail_max_bytes():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _CONSOLE_TAIL_MAX_BYTES


def _borrowed_console_tail_max_lines():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _CONSOLE_TAIL_MAX_LINES


def _borrowed_live_session_statuses():
    """BORROWED constant: one owner, never a copy (finding N7).

    v0.5.4: the owner is now `api_core/liveness.py`, not the control plane. The accessor stays because its
    callers are unchanged and the borrow still reads exactly one owner — only the owner moved.
    """
    from service.api_core.liveness import _LIVE_SESSION_STATUSES

    return _LIVE_SESSION_STATUSES


# _borrowed_manual_statuses moved to service/api_core/status_broadcast.py in v0.5.4.




def _borrowed_runtime_config_live_keys():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _RUNTIME_CONFIG_LIVE_KEYS


def _borrowed_shell_placeholder_handle_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""

    return _SHELL_PLACEHOLDER_HANDLE_RE








def _borrowed_listen_events():
    """One owner, never a copy (finding N7) — and the owner is now a LEAF, not the control plane.

    v0.5.4 moved `_listen_events` to `service/longpoll.py`, which already owned the other waiter
    registry. The accessor stays because its six callers are unchanged and it still reads exactly one
    owner; only the owner moved. Returning the dict itself is the point — `routers/agents/config.py`
    INSERTS into it, so a copy would put the waiter in one dict and the wake in another and
    `comms_listen` would hang to its timeout with nothing logged.
    """
    from service.longpoll import _listen_events

    return _listen_events




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
    durable_previous = {key: value for key, value in previous.items() if key not in _borrowed_runtime_config_live_keys()}
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
    if handle and _borrowed_shell_placeholder_handle_re().match(handle):
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