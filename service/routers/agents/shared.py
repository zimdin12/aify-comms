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
from service.api_core.records import (
    _agent_session_to_dict,
    _environment_record_to_dict,
    _terminal_session_to_dict,
)
from service.api_core.dispatch_text import (  # v0.5.4 owner; re-exported for this package
    _coldstart_refusal_message,
)
from service.api_core.virtual_rpc import VIRTUAL_RPC_COMMAND_SET
from service.api_core.serialization import _json_loads_or
from service.api_core.serialization import _normalize_machine_id
from service.api_core.serialization import _timestamp_sort_key
from service.api_core.capabilities import (  # re-exported for this package's modules
    _default_capabilities_for,
    _managed_via_wrapper_for_runtime,
)
from service.api_core.settings import DEFAULT_SETTINGS
from service.api_core.settings import _load_settings
from service.api_core.validation import validate_name
from service.api_core.vocabulary import SESSION_MODES as _SESSION_MODES
from service.api_core.ws import _get_ws
from service.api_core.liveness import _has_live_terminal_session
from service.api_core.agent_sessions import (
    _agent_tombstone,
    _session_handle_live_owner,
    _touch_current_agent_session,
)
from service.api_core.dispatch_state import _get_dispatch_state_for_agent, _get_dispatch_state_map
from service.api_core.turn_state import _clear_status_state_in_turn
from service.api_core.managed_env import (
    _has_pending_or_booting_spawn_request,
    _managed_owning_environment_row,
)
from service.api_core.recovery_writes import _record_channel_sidecar_heartbeat
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
from service.api_core.capabilities import _has_codex_live_app_server
from service.api_core.bridge_supersede import (
    _fail_active_runs_for_superseded_bridges,
    _stop_virtual_terminals_for_superseded_bridges,
)

logger = logging.getLogger("aify_comms.routers.agents.shared")





async def _agent_liveness(*a, **k):
    from service.control_plane import _agent_liveness as _impl

    return await _impl(*a, **k)


def _agent_record_to_dict(*a, **k):
    from service.control_plane import _agent_record_to_dict as _impl

    return _impl(*a, **k)






async def _append_dispatch_control(*a, **k):
    from service.control_plane import _append_dispatch_control as _impl

    return await _impl(*a, **k)


async def _auto_return_resident_to_managed_if_possible(*a, **k):
    from service.control_plane import _auto_return_resident_to_managed_if_possible as _impl

    return await _impl(*a, **k)








async def _compute_agent_status(*a, **k):
    from service.control_plane import _compute_agent_status as _impl

    return await _impl(*a, **k)


async def _compute_live_status_cache(*a, **k):
    from service.control_plane import _compute_live_status_cache as _impl

    return await _impl(*a, **k)








# _fail_active_runs_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.


async def _get_blocking_active_run(*a, **k):
    from service.control_plane import _get_blocking_active_run as _impl

    return await _impl(*a, **k)






async def _get_unread_count_map(*a, **k):
    from service.control_plane import _get_unread_count_map as _impl

    return await _impl(*a, **k)








def _is_lock_error(*a, **k):
    from service.control_plane import _is_lock_error as _impl

    return _impl(*a, **k)


# _machine_family moved to service/api_core/registration_gates.py in v0.5.4.








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




# _stop_virtual_terminals_for_superseded_bridges moved to service/api_core/bridge_supersede.py in v0.5.4.








async def engine_status(*a, **k):
    from service.control_plane import engine_status as _impl

    return await _impl(*a, **k)


def _borrowed_list_agents_refresh_limit():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import LIST_AGENTS_REFRESH_LIMIT

    return LIST_AGENTS_REFRESH_LIMIT














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




def _borrowed_runtime_config_live_keys():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _RUNTIME_CONFIG_LIVE_KEYS

    return _RUNTIME_CONFIG_LIVE_KEYS


def _borrowed_shell_placeholder_handle_re():
    """BORROWED constant: one owner, never a copy (finding N7)."""
    from service.control_plane import _SHELL_PLACEHOLDER_HANDLE_RE

    return _SHELL_PLACEHOLDER_HANDLE_RE








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


# _enforce_env_reachable_gate moved to service/api_core/registration_gates.py in v0.5.4.


# _enforce_live_worker_gate moved to service/api_core/registration_gates.py in v0.5.4.


# _fresh_same_mode_bridge_conflict moved to service/api_core/registration_gates.py in v0.5.4.


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


# _request_stop_agent_terminals moved to service/api_core/agent_terminal_ops.py in v0.5.4.


# _resolve_live_console_terminal moved to service/api_core/agent_terminal_ops.py in v0.5.4.


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


# _validate_registration_cwd moved to service/api_core/registration_gates.py in v0.5.4.